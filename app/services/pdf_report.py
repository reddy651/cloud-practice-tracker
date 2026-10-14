from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.utils import ImageReader
from PIL import Image as PILImage


def _escape(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def _draw_page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(A4[0] / 2, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()

def _groups(practice):
    groups = {}
    rows = sorted(practice.screenshots, key=lambda x: ((x.sequence_no if x.sequence_no is not None else 999999), (x.image_order if x.image_order is not None else 999999), x.id))
    for row in rows:
        groups.setdefault(row.sequence_no or 0, []).append(row)
    return [(seq, rows) for seq, rows in sorted(groups.items())]


def build_report(practice, screenshot_items=None):
    """Create a complete PDF containing ALL saved screenshots in sequence."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=practice.title or "Cloud Practice Tracker",
        author="Cloud Practice Tracker",
        allowSplitting=1,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="StepExplanation", parent=styles["BodyText"], leading=14, spaceAfter=8))
    styles.add(ParagraphStyle(name="CenterSmall", parent=styles["BodyText"], alignment=TA_CENTER, fontSize=8))

    items = screenshot_items or []
    item_by_id = {x.get("id"): x for x in items if x.get("id") is not None}
    groups = _groups(practice)

    story = [
        Paragraph("Cloud Practice Tracker", styles["Title"]),
        Spacer(1, 2 * mm),
        Paragraph(_escape(practice.title), styles["Heading1"]),
        Paragraph(f"Provider: {_escape(practice.provider)}", styles["BodyText"]),
        Paragraph(f"Service: {_escape(practice.service or '-')}", styles["BodyText"]),
        Paragraph(f"Topic: {_escape(practice.topic or '-')}", styles["BodyText"]),
        Paragraph(f"Total steps: {len(groups)}", styles["BodyText"]),
        Spacer(1, 6 * mm),
        Paragraph("Practice Steps", styles["Heading2"]),
        Spacer(1, 2 * mm),
    ]

    for idx, (seq, rows) in enumerate(groups):
        if idx > 0:
            story.append(PageBreak())
        story.extend([Paragraph(f"Step #{seq:03d}", styles["Heading2"]), Spacer(1, 2 * mm)])
        explanation = next((r.step_explanation for r in rows if (r.step_explanation or '').strip()), "Not provided yet.")
        story.extend([Paragraph("<b>Step explanation:</b>", styles["BodyText"]), Paragraph(_escape(explanation), styles["StepExplanation"])])
        for row in rows:
            item = item_by_id.get(row.id)
            if item and item.get("data"):
                try:
                    raw = item["data"]
                    with PILImage.open(BytesIO(raw)) as pil:
                        pil.load()
                        if pil.mode not in ("RGB", "RGBA"):
                            pil = pil.convert("RGBA")
                        normalized = BytesIO(); pil.save(normalized, format="PNG"); normalized.seek(0)
                        iw, ih = pil.size
                    max_w = 182 * mm; max_h = 220 * mm
                    scale = min(max_w / float(iw), max_h / float(ih), 1.0)
                    story.append(Spacer(1, 3 * mm))
                    story.append(Image(normalized, width=iw * scale, height=ih * scale))
                except Exception as exc:
                    story.append(Paragraph(f"[Screenshot could not be embedded: {_escape(str(exc))}]", styles["CenterSmall"]))
            else:
                story.append(Paragraph("[Screenshot replacement required]", styles["CenterSmall"]))
            story.append(Spacer(1, 3 * mm))

    if practice.description:
        story.append(PageBreak())
        story.extend([
            Paragraph("Additional Notes", styles["Heading2"]),
            Paragraph(_escape(practice.description), styles["BodyText"]),
        ])

    doc.build(story, onFirstPage=_draw_page_number, onLaterPages=_draw_page_number)
    return buf.getvalue()
