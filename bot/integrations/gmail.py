"""Gmail, read-only: Airbnb/VRBO notification emails from one or more inboxes (each with its own token)."""

import base64
import threading

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from integrations.gcal import RETRIES, SCOPES

QUERY = "from:(airbnb.com OR vrbo.com OR homeaway.com) newer_than:2d"


class Inbox:
    def __init__(self, label: str, client_id: str, client_secret: str, refresh_token: str):
        self.label = label
        self._creds = Credentials(token=None, refresh_token=refresh_token, client_id=client_id,
                                  client_secret=client_secret, token_uri="https://oauth2.googleapis.com/token",
                                  scopes=SCOPES)
        self._local = threading.local()

    @property
    def _svc(self):
        svc = getattr(self._local, "svc", None)
        if svc is None:
            svc = self._local.svc = build("gmail", "v1", credentials=self._creds, cache_discovery=False)
        return svc

    def recent_ids(self, query: str = QUERY) -> list[str]:
        resp = self._svc.users().messages().list(userId="me", q=query, maxResults=50).execute(num_retries=RETRIES)
        return [m["id"] for m in resp.get("messages", [])]

    def get(self, msg_id: str) -> dict:
        """{id, from, subject, date (epoch ms), text}: text is the plain-text part."""
        m = self._svc.users().messages().get(userId="me", id=msg_id, format="full").execute(num_retries=RETRIES)
        headers = {h["name"].lower(): h["value"] for h in m["payload"].get("headers", [])}
        return {"id": msg_id, "from": headers.get("from", ""), "subject": headers.get("subject", ""),
                "date": int(m.get("internalDate", 0)), "text": _plain(m["payload"])}


def _plain(part: dict) -> str:
    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
    for child in part.get("parts", []) or []:
        if text := _plain(child):
            return text
    return ""
