import os
from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()

def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-only-change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL",
        "mysql+mysqlconnector://practice_user:strong_password@localhost:3306/practice_tracker"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
    app.config["AZURE_STORAGE_CONTAINER"] = os.getenv("AZURE_STORAGE_CONTAINER", "practice-files")

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    from .models.user import User
    from .models.practice import PracticeSession, AzureActivity, Screenshot, PracticeReport

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from .routes.auth import auth_bp
    from .routes.practice import practice_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(practice_bp)

    with app.app_context():
        db.create_all()
        from .services.schema_upgrade import ensure_schema_upgrades
        ensure_schema_upgrades()

    return app
