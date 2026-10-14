from sqlalchemy import inspect, text
from .. import db


def ensure_schema_upgrades():
    """Small MySQL-friendly upgrade helper for this project.

    It adds new optional columns without requiring the user to drop their existing database.
    """
    inspector = inspect(db.engine)
    table = "practice_sessions"
    existing = {c["name"] for c in inspector.get_columns(table)}
    additions = {
        "interview_questions": "TEXT NULL",
        "interview_answers": "LONGTEXT NULL",
        "interview_answers_available_at": "DATETIME NULL",
        "ai_model": "VARCHAR(120) NULL",
        "ai_analyzed_at": "DATETIME NULL",
        "document_blob_name": "VARCHAR(1000) NULL",
        "document_created_at": "DATETIME NULL",
    }
    for name, ddl in additions.items():
        if name not in existing:
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
    shot_existing = {c["name"] for c in inspector.get_columns("screenshots")}
    if "sequence_no" not in shot_existing:
        db.session.execute(text("ALTER TABLE screenshots ADD COLUMN sequence_no INT NULL"))
    if "step_explanation" not in shot_existing:
        db.session.execute(text("ALTER TABLE screenshots ADD COLUMN step_explanation TEXT NULL"))
    if "image_order" not in shot_existing:
        db.session.execute(text("ALTER TABLE screenshots ADD COLUMN image_order INT NULL"))
        # Existing screenshots predate multi-image steps. Give each existing
        # screenshot a stable order so they continue to work after the upgrade.
        db.session.execute(text("""
            UPDATE screenshots s
            JOIN (
                SELECT id, ROW_NUMBER() OVER (PARTITION BY practice_id, sequence_no ORDER BY id) AS rn
                FROM screenshots
            ) x ON x.id = s.id
            SET s.image_order = x.rn
        """))
    # Screenshot replacement workflow: removing a screenshot keeps the step and
    # its explanation, so the image columns must allow NULL until a replacement is pasted.
    db.session.execute(text("ALTER TABLE screenshots MODIFY COLUMN blob_name VARCHAR(1000) NULL"))
    db.session.execute(text("ALTER TABLE screenshots MODIFY COLUMN original_name VARCHAR(255) NULL"))
    db.session.commit()
