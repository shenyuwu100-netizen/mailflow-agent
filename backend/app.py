"""
Automated Customer Service Email Reply System
Main Flask application entry point.
"""

from flask import Flask, session
from flask_cors import CORS
from config import Config
from models.database import init_db
from routes.auth_routes import auth_bp
from routes.email_routes import email_bp
from routes.company_routes import company_bp



def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.secret_key = Config.SECRET_KEY

    is_production = Config.FRONTEND_URL.startswith("https://")
    app.config["SESSION_COOKIE_SAMESITE"] = "None" if is_production else "Lax"
    app.config["SESSION_COOKIE_SECURE"] = is_production
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    # Enable CORS for Vue.js frontend (dev on port 5173)
    CORS(app, supports_credentials=True, origins=[Config.FRONTEND_URL])

    # Initialize database
    init_db()

    # Register blueprints
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(email_bp, url_prefix="/api")
    app.register_blueprint(company_bp, url_prefix="/api")


    @app.route("/")
    def index():
        return {"message": "Automated Email Reply System API", "status": "running"}

    @app.route("/health")
    def health():
        return {"status": "ok"}

    return app


if __name__ == "__main__":
    app = create_app()
    print("=" * 60)
    print("  Automated Customer Service Email Reply System")
    print(f"  Server running at: http://0.0.0.0:{Config.PORT}")
    print("=" * 60)
    app.run(host='0.0.0.0', debug=Config.DEBUG, port=Config.PORT)
