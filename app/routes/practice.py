import os
import hashlib
from datetime import datetime, timedelta
from io import BytesIO
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from .. import db
from ..models.practice import PracticeSession, AzureActivity, Screenshot, PracticeReport
from ..services.azure_collector import collect_for_practice
from ..services.pdf_report import build_report
from ..services.practice_document import build_markdown, build_docx
from ..services.ai_assistant import suggest_step, ask_ai
from ..services.ai_practice_details import generate_practice_details
from ..services.storage import upload_bytes, download_bytes, delete_bytes

practice_bp = Blueprint("practice", __name__)
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MIME_BY_EXT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


def owned_practice(practice_id):
    practice = db.session.get(PracticeSession, practice_id)
    if not practice or practice.user_id != current_user.id:
        abort(404)
    return practice


def safe_image_name(filename):
    name = secure_filename(filename)
    if not name:
        return None
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return None
    return name


def _ordered_screenshots(practice):
    return sorted(
        practice.screenshots,
        key=lambda x: ((x.sequence_no if x.sequence_no is not None else 999999),
                       (x.image_order if x.image_order is not None else 999999), x.id),
    )


def _step_numbers(practice):
    return sorted({s.sequence_no for s in practice.screenshots if s.sequence_no is not None})


def _renumber_screenshots(practice):
    """Renumber step groups while preserving multiple screenshots per step."""
    ordered_steps = _step_numbers(practice)
    mapping = {old: index for index, old in enumerate(ordered_steps, start=1)}
    changed = False
    for screenshot in practice.screenshots:
        old = screenshot.sequence_no
        new = mapping.get(old, len(mapping) + 1)
        if old != new:
            screenshot.sequence_no = new
            changed = True
    # normalize image order inside each step
    for step_no in sorted({s.sequence_no for s in practice.screenshots if s.sequence_no is not None}):
        rows = sorted([s for s in practice.screenshots if s.sequence_no == step_no], key=lambda x: (x.image_order or 999999, x.id))
        for idx, row in enumerate(rows, start=1):
            if row.image_order != idx:
                row.image_order = idx
                changed = True
    return changed


def _next_sequence(practice):
    _renumber_screenshots(practice)
    steps = _step_numbers(practice)
    return (max(steps) + 1) if steps else 1


def _step_groups(practice):
    """Return {step_no: [Screenshot rows]} preserving step/image order."""
    groups = {}
    for s in _ordered_screenshots(practice):
        groups.setdefault(s.sequence_no or 0, []).append(s)
    return groups


def _screenshot_items(practice):
    items = []
    for s in _ordered_screenshots(practice):
        if not s.blob_name:
            continue
        ext = s.original_name.rsplit(".", 1)[-1].lower() if s.original_name and "." in s.original_name else ""
        items.append({
            "id": s.id,
            "name": s.original_name or "",
            "data": download_bytes(s.blob_name),
            "content_type": MIME_BY_EXT.get(ext, "image/png"),
            "sequence_no": s.sequence_no or 0,
            "image_order": s.image_order or 1,
        })
    return items


def _save_document_files(practice):
    markdown = build_markdown(practice).encode("utf-8")
    base = f"users/{current_user.id}/practices/{practice.id}"
    upload_bytes(f"{base}/practice.md", markdown, "text/markdown; charset=utf-8")


def _generate_word_document(practice):
    screenshot_items = _screenshot_items(practice)
    data = build_docx(practice, screenshot_items=screenshot_items)
    blob_name = f"users/{current_user.id}/practices/{practice.id}/practice-document.docx"
    upload_bytes(blob_name, data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    practice.document_blob_name = blob_name
    practice.document_created_at = datetime.now()
    return data


def _generate_and_store_pdf(practice):
    screenshot_items = _screenshot_items(practice)
    pdf = build_report(practice, screenshot_items=screenshot_items)
    blob_name = f"users/{current_user.id}/practices/{practice.id}/practice-report.pdf"
    upload_bytes(blob_name, pdf, "application/pdf")
    if practice.report:
        practice.report.blob_name = blob_name
    else:
        practice.report = PracticeReport(blob_name=blob_name)
    return blob_name


@practice_bp.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("practice.dashboard"))
    return render_template("home.html")


@practice_bp.route("/dashboard")
@login_required
def dashboard():
    practices = PracticeSession.query.filter_by(user_id=current_user.id).order_by(
        PracticeSession.created_at.desc()
    ).all()
    return render_template("dashboard.html", practices=practices)


@practice_bp.route("/practice/new", methods=["GET", "POST"])
@login_required
def new_practice():
    if request.method == "POST":
        now = datetime.now()
        context = request.form.get("context", "").strip()
        title = request.form.get("title", "").strip() or f"Practice - {now.strftime('%d-%b-%Y %H:%M')}"
        provider = request.form.get("provider", "Other").strip() or "Other"
        service = request.form.get("service", "").strip()
        topic = request.form.get("topic", "").strip()

        p = PracticeSession(
            user_id=current_user.id,
            title=title,
            provider=provider,
            service=service,
            topic=topic,
            description=context,
            status="In Progress",
            started_at=now,
            ended_at=now,
        )
        db.session.add(p)
        db.session.commit()
        flash("Practice created. Paste your first screenshot and enter its step explanation.", "success")
        return redirect(url_for("practice.detail", practice_id=p.id))

    return render_template("practice_form.html")


def _is_duplicate_step(practice, data, explanation):
    """Reject duplicate screenshots, but allow multiple unique screenshots in one step."""
    image_hash = hashlib.sha256(data).hexdigest()
    normalized = " ".join((explanation or "").lower().split())
    for existing in practice.screenshots:
        if not existing.blob_name:
            continue
        try:
            existing_data = download_bytes(existing.blob_name)
            if hashlib.sha256(existing_data).hexdigest() == image_hash:
                return True, f"This screenshot is already saved in Step #{existing.sequence_no:03d}. Use a different screenshot or remove the duplicate."
        except Exception:
            pass
    return False, ""


def _validate_steps(practice):
    errors = []
    seen_hashes = {}
    groups = _step_groups(practice)
    for seq in sorted(groups):
        rows = groups[seq]
        explanation = next((r.step_explanation for r in rows if (r.step_explanation or '').strip()), '')
        if not explanation:
            errors.append(f"Step #{seq:03d} has no step explanation.")
        if not any(r.blob_name for r in rows):
            errors.append(f"Step #{seq:03d} is missing its screenshot. Paste a replacement screenshot in that step before creating the document.")
        for row in rows:
            if not row.blob_name:
                continue
            try:
                digest = hashlib.sha256(download_bytes(row.blob_name)).hexdigest()
                if digest in seen_hashes:
                    errors.append(f"Step #{seq:03d} uses the same screenshot as Step #{seen_hashes[digest]:03d}.")
                else:
                    seen_hashes[digest] = seq
            except Exception as exc:
                errors.append(f"Step #{seq:03d} screenshot could not be read: {exc}")
    return errors


def _store_uploaded_file(practice, f, name, step_explanation="", seq=None, image_order=None):
    seq = seq if seq is not None else _next_sequence(practice)
    if image_order is None:
        existing = [s for s in practice.screenshots if s.sequence_no == seq]
        image_order = max([s.image_order or 0 for s in existing] or [0]) + 1
    ext = name.rsplit(".", 1)[-1].lower()
    stored_name = f"{seq:03d}_{image_order:03d}_{secure_filename(name)}"
    blob_name = f"users/{current_user.id}/practices/{practice.id}/screenshots/{stored_name}"
    data = f.read()
    upload_bytes(blob_name, data, f.mimetype or MIME_BY_EXT.get(ext, "application/octet-stream"))
    screenshot = Screenshot(
        practice_id=practice.id,
        blob_name=blob_name,
        original_name=name,
        sequence_no=seq,
        image_order=image_order,
        step_explanation=step_explanation.strip() if step_explanation else None,
    )
    db.session.add(screenshot)
    return screenshot


@practice_bp.route("/practice/<int:practice_id>")
@login_required
def detail(practice_id):
    p = owned_practice(practice_id)
    if _renumber_screenshots(p):
        db.session.commit()
    return render_template("practice_detail.html", practice=p, now=datetime.now(), step_groups=_step_groups(p))


@practice_bp.route("/practice/<int:practice_id>/add-step", methods=["POST"])
@login_required
def add_step(practice_id):
    p = owned_practice(practice_id)
    files = [f for f in request.files.getlist("screenshots") if f and f.filename]
    # Backward compatibility with older form field.
    if not files:
        files = [f for f in request.files.getlist("screenshot") if f and f.filename]
    explanation = request.form.get("step_explanation", "").strip()
    if not files:
        flash("Paste or choose at least one screenshot before saving the step.", "error")
        return redirect(url_for("practice.detail", practice_id=p.id) + "#add-step")
    if not explanation:
        flash("Enter the step explanation before saving the step.", "error")
        return redirect(url_for("practice.detail", practice_id=p.id) + "#add-step")
    seq = _next_sequence(p)
    try:
        for index, f in enumerate(files, start=1):
            name = safe_image_name(f.filename)
            if not name:
                raise ValueError("Only PNG, JPG, JPEG and WEBP screenshots are supported.")
            data = f.read()
            duplicate, message = _is_duplicate_step(p, data, explanation)
            if duplicate:
                raise ValueError(message)
            f.stream.seek(0)
            _store_uploaded_file(p, f, name, explanation, seq=seq, image_order=index)
        p.status = "In Progress"
        db.session.commit()
        flash(f"Step #{seq:03d} saved with {len(files)} screenshot(s). You can now add the next step.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Step could not be saved: {exc}", "error")
    return redirect(url_for("practice.detail", practice_id=p.id) + "#add-step")


@practice_bp.route("/practice/<int:practice_id>/edit", methods=["GET", "POST"])
@login_required
def edit_practice(practice_id):
    p = owned_practice(practice_id)
    if request.method == "POST":
        p.title = request.form.get("title", "").strip() or p.title
        p.provider = request.form.get("provider", "Other").strip() or "Other"
        p.service = request.form.get("service", "").strip()
        p.topic = request.form.get("topic", "").strip()
        p.description = request.form.get("description", "").strip()
        p.notes = request.form.get("notes", "").strip()
        p.problems = request.form.get("problems", "").strip()
        p.solutions = request.form.get("solutions", "").strip()
        p.commands = request.form.get("commands", "").strip()
        p.interview_questions = request.form.get("interview_questions", "").strip()
        # Answers are locked until the 24-hour release time. After release,
        # allow the user to edit/save them like the other study fields.
        if p.interview_answers_available_at and datetime.now() >= p.interview_answers_available_at:
            p.interview_answers = request.form.get("interview_answers", "").strip()
        p.status = request.form.get("status", "Draft").strip() or "Draft"
        fmt = "%Y-%m-%dT%H:%M"
        try:
            p.started_at = datetime.strptime(request.form.get("started_at", ""), fmt)
            p.ended_at = datetime.strptime(request.form.get("ended_at", ""), fmt)
        except (TypeError, ValueError):
            pass
        db.session.commit()
        flash("Practice details updated.", "success")
        return redirect(url_for("practice.detail", practice_id=p.id))
    return render_template("practice_edit.html", practice=p, now=datetime.now())


@practice_bp.route("/practice/<int:practice_id>/edit/ai-fill", methods=["POST"])
@login_required
def ai_fill_practice_edit(practice_id):
    p = owned_practice(practice_id)
    try:
        _renumber_screenshots(p)
        steps = []
        for s in sorted(p.screenshots, key=lambda x: (x.sequence_no or 999999, x.id)):
            explanation = (s.step_explanation or '').strip()
            if explanation:
                steps.append(f"Step #{s.sequence_no:03d}: {explanation}")
        step_context = "\n".join(steps)
        data, model, analyzed_at = generate_practice_details(p, step_context=step_context)
        # The user's existing provider is authoritative; AI may improve the rest.
        if p.provider:
            data['provider'] = p.provider
        for field in ("title", "provider", "service", "topic", "description", "notes", "problems", "solutions", "commands", "interview_questions"):
            setattr(p, field, data.get(field, '').strip())
        # Generate and store the answers now, but do not return them to the browser
        # until the 24-hour release time has passed. This prevents early display in the UI.
        p.interview_answers = data.get("interview_answers", "").strip()
        p.interview_answers_available_at = analyzed_at + timedelta(hours=24)
        p.ai_model = model
        p.ai_analyzed_at = analyzed_at
        db.session.commit()
        safe_data = dict(data)
        safe_data.pop("interview_answers", None)
        return {"ok": True, "data": safe_data, "model": model, "analyzed_at": analyzed_at.isoformat(), "answers_available_at": p.interview_answers_available_at.isoformat()}
    except Exception as exc:
        db.session.rollback()
        return {"ok": False, "error": str(exc)}, 500


@practice_bp.route("/practice/<int:practice_id>/screenshot/<int:screenshot_id>/step", methods=["POST"])
@login_required
def save_step(practice_id, screenshot_id):
    p = owned_practice(practice_id)
    screenshot = db.session.get(Screenshot, screenshot_id)
    if not screenshot or screenshot.practice_id != p.id:
        abort(404)
    explanation = request.form.get("step_explanation", "").strip()
    if not explanation:
        flash("Enter the step explanation before saving.", "error")
        return redirect(url_for("practice.detail", practice_id=p.id) + f"#screenshot-{screenshot.id}")
    try:
        group = [s for s in p.screenshots if s.sequence_no == screenshot.sequence_no]
        for row in group:
            row.step_explanation = explanation
        db.session.commit()
        flash(f"Step #{screenshot.sequence_no:03d} saved. The explanation applies to all screenshots in this step.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Step could not be saved: {exc}", "error")
    return redirect(url_for("practice.detail", practice_id=p.id) + f"#screenshot-{screenshot.id}")


@practice_bp.route("/practice/<int:practice_id>/screenshot/<int:screenshot_id>/ai-suggest", methods=["POST"])
@login_required
def ai_step_suggestion(practice_id, screenshot_id):
    p = owned_practice(practice_id)
    screenshot = db.session.get(Screenshot, screenshot_id)
    if not screenshot or screenshot.practice_id != p.id:
        abort(404)
    try:
        data = download_bytes(screenshot.blob_name)
        ext = screenshot.original_name.rsplit(".", 1)[-1].lower() if "." in screenshot.original_name else ""
        suggestion, model = suggest_step(data, MIME_BY_EXT.get(ext, "image/png"), p.title)
        suggestion = suggestion.strip()
        for row in p.screenshots:
            if row.sequence_no == screenshot.sequence_no:
                row.step_explanation = suggestion
        db.session.commit()
        flash(f"AI suggestion added to Step #{screenshot.sequence_no:03d}. Review it and click Save Step.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"AI suggestion failed: {exc}", "error")
    return redirect(url_for("practice.detail", practice_id=p.id) + f"#screenshot-{screenshot.id}")


@practice_bp.route("/practice/<int:practice_id>/document/update", methods=["POST"])
@login_required
def update_document(practice_id):
    p = owned_practice(practice_id)
    try:
        # If Update Document was clicked from a step card, persist that textarea
        # first so edits are included in the generated Word document.
        screenshot_id = request.form.get("screenshot_id", type=int)
        if screenshot_id:
            screenshot = db.session.get(Screenshot, screenshot_id)
            if not screenshot or screenshot.practice_id != p.id:
                abort(404)
            explanation = request.form.get("step_explanation", "").strip()
            for row in p.screenshots:
                if row.sequence_no == screenshot.sequence_no:
                    row.step_explanation = explanation

        _renumber_screenshots(p)
        errors = _validate_steps(p)
        if errors:
            flash("Document update blocked until repeated/missing steps are fixed: " + " | ".join(errors), "error")
            return redirect(url_for("practice.detail", practice_id=p.id) + "#document")

        _save_document_files(p)
        _generate_word_document(p)
        db.session.commit()
        flash("Word document updated with all currently saved steps.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Document update failed: {exc}", "error")
    return redirect(url_for("practice.detail", practice_id=p.id) + "#document")


@practice_bp.route("/practice/<int:practice_id>/document/edit", methods=["POST"])
@login_required
def edit_saved_document(practice_id):
    """Re-open a completed practice so the user can add more screenshots/steps."""
    p = owned_practice(practice_id)
    p.status = "In Progress"
    p.ended_at = datetime.now()
    db.session.commit()
    flash("Edit mode enabled. You can add more screenshots and steps below. Save the document again when finished.", "success")
    return redirect(url_for("practice.detail", practice_id=p.id) + "#add-step")


@practice_bp.route("/practice/<int:practice_id>/document/save", methods=["POST"])
@login_required
def save_document(practice_id):
    p = owned_practice(practice_id)
    if not p.screenshots:
        flash("Save at least one step before creating the Word document.", "error")
        return redirect(url_for("practice.detail", practice_id=p.id) + "#add-step")
    try:
        _renumber_screenshots(p)
        errors = _validate_steps(p)
        if errors:
            flash("Cannot create the final Word document until repeated/missing steps are fixed: " + " | ".join(errors), "error")
            return redirect(url_for("practice.detail", practice_id=p.id) + "#document")
        _save_document_files(p)
        docx_data = _generate_word_document(p)
        p.status = "Completed"
        p.ended_at = datetime.now()
        db.session.commit()

        # Save to the configured cloud storage first, then immediately return
        # the same .docx bytes as a browser download. This gives the user both
        # cloud backup and a local copy from one button click.
        return send_file(
            BytesIO(docx_data),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name=secure_filename(p.title) + ".docx",
        )
    except Exception as exc:
        db.session.rollback()
        flash(f"Word document creation failed: {exc}", "error")
    return redirect(url_for("practice.detail", practice_id=p.id) + "#document")


@practice_bp.route("/practice/<int:practice_id>/document/finalize", methods=["POST"])
@login_required
def finalize_document(practice_id):
    """Create the final Word document and PDF from every saved step."""
    p = owned_practice(practice_id)
    if not p.screenshots:
        flash("Save at least one step before finalizing the document.", "error")
        return redirect(url_for("practice.detail", practice_id=p.id) + "#add-step")
    try:
        _renumber_screenshots(p)
        errors = _validate_steps(p)
        if errors:
            flash("Cannot finalize until repeated/missing steps are fixed: " + " | ".join(errors), "error")
            return redirect(url_for("practice.detail", practice_id=p.id) + "#document")
        _save_document_files(p)
        _generate_word_document(p)
        _generate_and_store_pdf(p)
        p.status = "Completed"
        p.ended_at = datetime.now()
        db.session.commit()
        flash("Final Word document and PDF created successfully.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Final document creation failed: {exc}", "error")
    return redirect(url_for("practice.detail", practice_id=p.id) + "#document")


@practice_bp.route("/practice/<int:practice_id>/document/pdf", methods=["POST"])
@login_required
def convert_document_to_pdf(practice_id):
    p = owned_practice(practice_id)
    if not p.screenshots:
        flash("Save at least one step before creating the PDF.", "error")
        return redirect(url_for("practice.detail", practice_id=p.id) + "#add-step")
    try:
        _renumber_screenshots(p)
        errors = _validate_steps(p)
        if errors:
            flash("Cannot create the final PDF until repeated/missing steps are fixed: " + " | ".join(errors), "error")
            return redirect(url_for("practice.detail", practice_id=p.id) + "#document")
        # The PDF is generated from the same current saved steps as the Word document.
        if not p.document_blob_name:
            _save_document_files(p)
            _generate_word_document(p)
        pdf_data = build_report(p, screenshot_items=_screenshot_items(p))
        blob_name = f"users/{current_user.id}/practices/{p.id}/practice-report.pdf"
        upload_bytes(blob_name, pdf_data, "application/pdf")
        if p.report:
            p.report.blob_name = blob_name
        else:
            p.report = PracticeReport(blob_name=blob_name)
        p.status = "Completed"
        p.ended_at = datetime.now()
        db.session.commit()

        # One click: store the complete PDF in cloud storage AND download the
        # exact same bytes to the user's local PC.
        return send_file(
            BytesIO(pdf_data),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=secure_filename(p.title) + ".pdf",
        )
    except Exception as exc:
        db.session.rollback()
        flash(f"PDF creation failed: {exc}", "error")
    return redirect(url_for("practice.detail", practice_id=p.id) + "#document")


@practice_bp.route("/practice/<int:practice_id>/screenshots", methods=["POST"])
@login_required
def upload_screenshots(practice_id):
    """Backward-compatible batch upload endpoint. New UI uses /add-step."""
    p = owned_practice(practice_id)
    files = [f for f in request.files.getlist("screenshots") if f and f.filename]
    if not files:
        flash("Paste a screenshot or choose an image file.", "error")
        return redirect(url_for("practice.detail", practice_id=p.id))
    if len(files) > 20:
        flash("Maximum 20 screenshots per upload batch.", "error")
        return redirect(url_for("practice.detail", practice_id=p.id))
    try:
        for f in files:
            name = safe_image_name(f.filename)
            if not name:
                continue
            data = f.read()
            duplicate, message = _is_duplicate_step(p, data, "")
            if duplicate:
                continue
            f.stream.seek(0)
            _store_uploaded_file(p, f, name)
        db.session.commit()
        flash("Screenshots added. Add an explanation to each step and click Save Step.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Screenshot upload failed: {exc}", "error")
    return redirect(url_for("practice.detail", practice_id=p.id))


@practice_bp.route("/ai-assistant", methods=["GET", "POST"])
@login_required
def ai_assistant():
    answer = None
    question = ""
    if request.method == "POST":
        question = request.form.get("question", "").strip()
        if question:
            try:
                answer, _ = ask_ai(question)
            except Exception as exc:
                flash(f"AI assistant failed: {exc}", "error")
        else:
            flash("Enter a question.", "error")
    return render_template("ai_assistant.html", answer=answer, question=question)


@practice_bp.route("/practice/<int:practice_id>/azure-sync", methods=["POST"])
@login_required
def azure_sync(practice_id):
    p = owned_practice(practice_id)
    if p.provider.lower() != "azure":
        flash("Azure synchronization is only available for Azure practices.", "error")
        return redirect(url_for("practice.detail", practice_id=p.id))
    try:
        count = collect_for_practice(p)
        flash(f"Synchronized {count} new Azure Activity Log event(s) and archived the raw batch to cloud storage.", "success")
    except Exception as exc:
        flash(f"Azure sync failed: {exc}", "error")
    return redirect(url_for("practice.detail", practice_id=p.id))


@practice_bp.route("/screenshot/<int:screenshot_id>/view")
@login_required
def view_screenshot(screenshot_id):
    screenshot = db.session.get(Screenshot, screenshot_id)
    if not screenshot or screenshot.practice.user_id != current_user.id:
        abort(404)
    try:
        data = download_bytes(screenshot.blob_name)
        ext = screenshot.original_name.rsplit(".", 1)[-1].lower() if "." in screenshot.original_name else ""
        return send_file(BytesIO(data), mimetype=MIME_BY_EXT.get(ext, "application/octet-stream"), as_attachment=False, download_name=screenshot.original_name)
    except Exception as exc:
        flash(f"Screenshot view failed: {exc}", "error")
        return redirect(url_for("practice.detail", practice_id=screenshot.practice_id))


@practice_bp.route("/practice/<int:practice_id>/screenshot/<int:screenshot_id>/replace", methods=["POST"])
@login_required
def replace_screenshot(practice_id, screenshot_id):
    p = owned_practice(practice_id)
    screenshot = db.session.get(Screenshot, screenshot_id)
    if not screenshot or screenshot.practice_id != p.id:
        abort(404)
    f = request.files.get("replacement_screenshot")
    if not f or not f.filename:
        flash(f"Choose or paste a replacement screenshot for Step #{screenshot.sequence_no:03d}.", "error")
        return redirect(url_for("practice.detail", practice_id=p.id) + f"#screenshot-{screenshot.id}")
    name = safe_image_name(f.filename)
    if not name:
        flash("Only PNG, JPG, JPEG and WEBP screenshots are supported.", "error")
        return redirect(url_for("practice.detail", practice_id=p.id) + f"#screenshot-{screenshot.id}")
    try:
        data = f.read()
        digest = hashlib.sha256(data).hexdigest()
        for other in p.screenshots:
            if other.id == screenshot.id or not other.blob_name:
                continue
            try:
                if hashlib.sha256(download_bytes(other.blob_name)).hexdigest() == digest:
                    raise ValueError(f"This screenshot is already used by Step #{other.sequence_no:03d}.")
            except ValueError:
                raise
            except Exception:
                pass
        if screenshot.blob_name:
            try:
                delete_bytes(screenshot.blob_name)
            except Exception:
                pass
        ext = name.rsplit(".", 1)[-1].lower()
        stored_name = f"{screenshot.sequence_no:03d}_{secure_filename(name)}"
        blob_name = f"users/{current_user.id}/practices/{p.id}/screenshots/{stored_name}"
        upload_bytes(blob_name, data, f.mimetype or MIME_BY_EXT.get(ext, "application/octet-stream"))
        screenshot.blob_name = blob_name
        screenshot.original_name = name
        db.session.commit()
        flash(f"Replacement screenshot saved for Step #{screenshot.sequence_no:03d}. The existing step explanation was kept.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Screenshot replacement failed: {exc}", "error")
    return redirect(url_for("practice.detail", practice_id=p.id) + f"#screenshot-{screenshot.id}")

@practice_bp.route("/practice/<int:practice_id>/screenshot/<int:screenshot_id>/delete", methods=["POST"])
@login_required
def delete_screenshot(practice_id, screenshot_id):
    p = owned_practice(practice_id)
    screenshot = db.session.get(Screenshot, screenshot_id)
    if not screenshot or screenshot.practice_id != p.id:
        abort(404)
    try:
        if screenshot.blob_name:
            delete_bytes(screenshot.blob_name)
    except Exception as exc:
        flash(f"Storage cleanup warning: {exc}", "error")
    # IMPORTANT: keep the step row and its explanation. Only remove the image so
    # the same step number becomes a replacement slot.
    screenshot.blob_name = None
    screenshot.original_name = None
    db.session.commit()
    flash(f"Screenshot removed from Step #{screenshot.sequence_no:03d}. The step explanation was kept. Paste a replacement screenshot in this same step.", "success")
    return redirect(url_for("practice.detail", practice_id=p.id) + f"#screenshot-{screenshot.id}")


@practice_bp.route("/practice/<int:practice_id>/report", methods=["POST"])
@login_required
def generate_report(practice_id):
    # Backward-compatible PDF endpoint.
    return convert_document_to_pdf(practice_id)


@practice_bp.route("/report/<int:practice_id>/download")
@login_required
def download_report(practice_id):
    p = owned_practice(practice_id)
    if not p.report:
        abort(404)
    try:
        data = download_bytes(p.report.blob_name)
        filename = secure_filename(p.title) + ".pdf"
        return send_file(BytesIO(data), mimetype="application/pdf", as_attachment=True, download_name=filename)
    except Exception as exc:
        flash(f"Download failed: {exc}", "error")
        return redirect(url_for("practice.detail", practice_id=p.id))


@practice_bp.route("/practice/<int:practice_id>/document/download")
@login_required
def download_document(practice_id):
    p = owned_practice(practice_id)
    if not p.document_blob_name:
        abort(404)
    try:
        data = download_bytes(p.document_blob_name)
        return send_file(
            BytesIO(data),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name=secure_filename(p.title) + ".docx",
        )
    except Exception as exc:
        flash(f"Word document download failed: {exc}", "error")
        return redirect(url_for("practice.detail", practice_id=p.id))
