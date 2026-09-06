"""
Authentication routes using Microsoft Azure AD OAuth2 (MSAL).
Blueprint prefix: /auth
"""

from flask import Blueprint, redirect, request, session, jsonify, url_for
import msal
import requests
import time
from config import Config

auth_bp = Blueprint("auth", __name__)


def _build_msal_app():
    # 创建不走系统代理的 session，避免 ProxyError
    http_session = requests.Session()
    http_session.proxies = {"http": None, "https": None}
    return msal.ConfidentialClientApplication(
        Config.AZURE_CLIENT_ID,
        authority=Config.AZURE_AUTHORITY,
        client_credential=Config.AZURE_CLIENT_SECRET,
        http_client=http_session,
    )


def _build_auth_url(prompt: str = "consent"):
    app = _build_msal_app()
    return app.get_authorization_request_url(
        scopes=Config.GRAPH_SCOPES,
        redirect_uri=Config.AZURE_REDIRECT_URI,
        prompt=prompt,
    )


@auth_bp.route("/login")
def login():
    """Redirect the user to Microsoft login."""
    auth_url = _build_auth_url()
    return redirect(auth_url)


@auth_bp.route("/callback")
def callback():
    """Handle the OAuth2 callback from Microsoft."""
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        return jsonify({"error": error, "description": request.args.get("error_description")}), 400

    if not code:
        return jsonify({"error": "No authorization code received"}), 400

    msal_app = _build_msal_app()
    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=Config.GRAPH_SCOPES,
        redirect_uri=Config.AZURE_REDIRECT_URI,
    )

    if "error" in result:
        return jsonify({"error": result["error"], "description": result.get("error_description")}), 400

    print(f"[AUTH] token scope: {result.get('scope')}", flush=True)
    print(f"[AUTH] token_type: {result.get('token_type')}", flush=True)

    session["access_token"] = result["access_token"]
    session["refresh_token"] = result.get("refresh_token")
    session["token_expires_at"] = int(time.time()) + result.get("expires_in", 3600)
    session["user"] = result.get("id_token_claims", {})

    # Redirect to frontend
    return redirect(f"{Config.FRONTEND_URL}/emails")


@auth_bp.route("/logout")
def logout():
    """Clear the session and redirect to Microsoft logout."""
    session.clear()
    logout_url = (
        f"https://login.microsoftonline.com/{Config.AZURE_TENANT_ID}/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri={Config.FRONTEND_URL}/login"
    )
    return redirect(logout_url)


@auth_bp.route("/me")
def me():
    """Return current logged-in user info from session."""
    if "user" not in session or "access_token" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    user = session["user"]
    return jsonify({
        "name": user.get("name", ""),
        "email": user.get("preferred_username", user.get("email", "")),
        "oid": user.get("oid", ""),
    })


@auth_bp.route("/status")
def status():
    """Check authentication status without redirecting."""
    if "access_token" in session:
        user = session.get("user", {})
        return jsonify({
            "authenticated": True,
            "user": {
                "name": user.get("name", ""),
                "email": user.get("preferred_username", user.get("email", "")),
            },
        })
    return jsonify({"authenticated": False})


def refresh_access_token():
    """Refresh the access token using the refresh token. Returns new access_token or None."""
    refresh_token = session.get("refresh_token")
    if not refresh_token:
        print("[AUTH] No refresh token available", flush=True)
        return None

    try:
        msal_app = _build_msal_app()
        result = msal_app.acquire_token_by_refresh_token(
            refresh_token,
            scopes=Config.GRAPH_SCOPES,
        )

        if "error" in result:
            print(f"[AUTH] Token refresh failed: {result.get('error')}", flush=True)
            return None

        # Update session with new tokens
        session["access_token"] = result["access_token"]
        if "refresh_token" in result:
            session["refresh_token"] = result["refresh_token"]
        session["token_expires_at"] = result.get("expires_in", 3600) + int(time.time())

        print("[AUTH] Token refreshed successfully", flush=True)
        return result["access_token"]
    except Exception as e:
        print(f"[AUTH] Token refresh exception: {e}", flush=True)
        return None


def get_valid_token():
    """Get a valid access token, refreshing if necessary. Returns (token, needs_reauth)."""
    token = session.get("access_token")
    expires_at = session.get("token_expires_at", 0)

    if not token:
        return None, True

    # Check if token is expired or will expire in next 5 minutes
    if time.time() >= (expires_at - 300):
        print("[AUTH] Token expired or expiring soon, attempting refresh", flush=True)
        new_token = refresh_access_token()
        if new_token:
            return new_token, False
        else:
            return None, True

    return token, False
