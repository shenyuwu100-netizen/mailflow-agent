"""
Application configuration module.
Loads settings from environment variables with sensible defaults.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration loaded from environment variables."""

    # Flask
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")
    DEBUG = os.getenv("FLASK_DEBUG", "True").lower() in ("true", "1", "yes")
    PORT = int(os.getenv("FLASK_PORT", 5005))

    # Microsoft Azure AD / Entra ID
    AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
    AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
    AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "common")
    AZURE_REDIRECT_URI = os.getenv(
        "AZURE_REDIRECT_URI", "http://localhost:5005/auth/callback"
    )
    AZURE_AUTHORITY = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}"
    GRAPH_API_ENDPOINT = "https://graph.microsoft.com/v1.0"

    # Microsoft Graph API scopes
    GRAPH_SCOPES = [
        "User.Read",
        "Mail.Read",
        "Mail.ReadWrite",
        "Mail.Send",
    ]

    # OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")

    # Database
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATABASE_PATH = os.getenv("MAILFLOW_LEGACY_DB", os.path.join(BASE_DIR, "var", "email_system.db"))
    AUTO_SEND_ENABLED = os.getenv("MAILFLOW_LEGACY_AUTO_SEND", "false").lower() == "true"

    # Company product catalog (JSON-based lightweight datastore)
    COMPANY_PRODUCTS_PATH = os.path.join(BASE_DIR, "data", "company_products.json")

    # Classification confidence threshold – emails below this go to manual review
    CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.75"))

    # Email send retry policy
    SEND_RETRY_MAX_ATTEMPTS = int(os.getenv("SEND_RETRY_MAX_ATTEMPTS", "3"))
    SEND_RETRY_DELAY_SECONDS = float(os.getenv("SEND_RETRY_DELAY_SECONDS", "1.0"))

    # Demo mode - prevents database modifications to preserve test data
    DEMO_MODE = os.getenv("DEMO_MODE", "True").lower() in ("true", "1", "yes")

    # Frontend URL for CORS configuration
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

