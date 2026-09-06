"""
Microsoft Graph API service.
Handles reading and sending emails via Graph API using the access token stored in session.
"""

import time
import requests
from config import Config


NO_PROXY = {"http": None, "https": None}


class EmailSendError(Exception):
    """Raised when sending an email reply fails after retries."""

    def __init__(self, message: str, attempts: int, last_error: str):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class GraphService:
    """Wrapper around Microsoft Graph API v1.0 for email operations."""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = Config.GRAPH_API_ENDPOINT
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict = None):
        resp = requests.get(
            f"{self.base_url}{path}", headers=self.headers, params=params,
            timeout=30, proxies=NO_PROXY
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict):
        resp = requests.post(
            f"{self.base_url}{path}", headers=self.headers, json=payload,
            timeout=30, proxies=NO_PROXY
        )
        resp.raise_for_status()
        return resp

    def _patch(self, path: str, payload: dict):
        resp = requests.patch(
            f"{self.base_url}{path}", headers=self.headers, json=payload,
            timeout=30, proxies=NO_PROXY
        )
        resp.raise_for_status()

    def get_emails(self, top: int = 20) -> list[dict]:
        """Fetch emails from inbox, most recent first."""
        # 先尝试 inbox，再 fallback 到 /me/messages
        for path in ("/me/mailFolders/inbox/messages", "/me/messages"):
            try:
                data = self._get(
                    path,
                    params={
                        "$top": top,
                        "$orderby": "receivedDateTime desc",
                        "$select": "id,subject,from,receivedDateTime,bodyPreview,body,isRead",
                    },
                )
                values = data.get("value", [])
                print(f"[GRAPH] {path} 返回 {len(values)} 封邮件", flush=True)
                if values:
                    return values
            except Exception as e:
                print(f"[GRAPH] {path} 失败: {e}", flush=True)
        return []

    def get_email_detail(self, message_id: str) -> dict:
        """Fetch a single email by Graph message id."""
        return self._get(f"/me/messages/{message_id}")

    def mark_as_read(self, message_id: str):
        """Mark an email as read."""
        self._patch(f"/me/messages/{message_id}", {"isRead": True})

    def send_reply(self, message_id: str, reply_text: str, max_attempts: int | None = None) -> dict:
        """Send a reply to an email using retry policy. Returns {'attempts': int} on success."""
        attempts = max(1, max_attempts or Config.SEND_RETRY_MAX_ATTEMPTS)
        delay_seconds = max(0.0, Config.SEND_RETRY_DELAY_SECONDS)
        last_error = ""

        for attempt in range(1, attempts + 1):
            try:
                self._post(
                    f"/me/messages/{message_id}/reply",
                    {
                        "message": {},
                        "comment": reply_text,
                    },
                )
                return {"attempts": attempt}
            except Exception as e:
                last_error = str(e)
                print(f"[GRAPH] 发送失败，第 {attempt}/{attempts} 次: {last_error}", flush=True)
                if attempt < attempts and delay_seconds > 0:
                    time.sleep(delay_seconds)

        raise EmailSendError(
            f"发送回复失败，已重试 {attempts} 次",
            attempts=attempts,
            last_error=last_error or "unknown error",
        )

    def get_me(self) -> dict:
        """Get the current authenticated user's profile."""
        return self._get("/me")

