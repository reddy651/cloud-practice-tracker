from .. import db

class PracticeSession(db.Model):
    __tablename__ = "practice_sessions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    provider = db.Column(db.String(30), nullable=False, default="Azure")
    service = db.Column(db.String(120), nullable=True)
    topic = db.Column(db.String(200), nullable=True)
    description = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    problems = db.Column(db.Text, nullable=True)
    solutions = db.Column(db.Text, nullable=True)
    commands = db.Column(db.Text, nullable=True)
    interview_questions = db.Column(db.Text, nullable=True)
    interview_answers = db.Column(db.Text, nullable=True)
    interview_answers_available_at = db.Column(db.DateTime, nullable=True)
    ai_model = db.Column(db.String(120), nullable=True)
    ai_analyzed_at = db.Column(db.DateTime, nullable=True)
    document_blob_name = db.Column(db.String(1000), nullable=True)
    document_created_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(30), nullable=False, default="Completed")
    started_at = db.Column(db.DateTime, nullable=False)
    ended_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)

    user = db.relationship("User", back_populates="practices")
    activities = db.relationship("AzureActivity", back_populates="practice", cascade="all, delete-orphan")
    screenshots = db.relationship("Screenshot", back_populates="practice", cascade="all, delete-orphan", order_by="Screenshot.sequence_no")
    report = db.relationship("PracticeReport", back_populates="practice", uselist=False, cascade="all, delete-orphan")

class AzureActivity(db.Model):
    __tablename__ = "azure_activities"
    id = db.Column(db.Integer, primary_key=True)
    practice_id = db.Column(db.Integer, db.ForeignKey("practice_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    event_time = db.Column(db.DateTime, nullable=True)
    operation_name = db.Column(db.String(500), nullable=True)
    resource_id = db.Column(db.String(1000), nullable=True)
    resource_group = db.Column(db.String(255), nullable=True)
    resource_type = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(100), nullable=True)
    caller = db.Column(db.String(500), nullable=True)
    correlation_id = db.Column(db.String(255), nullable=True)
    raw_event_json = db.Column(db.JSON, nullable=True)
    practice = db.relationship("PracticeSession", back_populates="activities")

class Screenshot(db.Model):
    __tablename__ = "screenshots"
    id = db.Column(db.Integer, primary_key=True)
    practice_id = db.Column(db.Integer, db.ForeignKey("practice_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    blob_name = db.Column(db.String(1000), nullable=True)
    original_name = db.Column(db.String(255), nullable=True)
    sequence_no = db.Column(db.Integer, nullable=True, index=True)
    image_order = db.Column(db.Integer, nullable=True, default=1, index=True)
    step_explanation = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    practice = db.relationship("PracticeSession", back_populates="screenshots")

class PracticeReport(db.Model):
    __tablename__ = "practice_reports"
    id = db.Column(db.Integer, primary_key=True)
    practice_id = db.Column(db.Integer, db.ForeignKey("practice_sessions.id", ondelete="CASCADE"), nullable=False, unique=True)
    blob_name = db.Column(db.String(1000), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    practice = db.relationship("PracticeSession", back_populates="report")
