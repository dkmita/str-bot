"""Google Calendar, read-only: the "Sedona Home" calendar (maintenance, owner stays).

Copied plumbing from ~/workout-coach/bot/integrations/gcal.py (one client per thread, retries)."""

import threading

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config import Config

SCOPES = ["https://www.googleapis.com/auth/calendar"]  # same refresh token as the coach bot
RETRIES = 3


class Calendar:
    def __init__(self, cfg: Config):
        self._creds = Credentials(token=None, refresh_token=cfg.google_refresh_token, client_id=cfg.google_client_id,
                                  client_secret=cfg.google_client_secret, token_uri="https://oauth2.googleapis.com/token",
                                  scopes=SCOPES)
        self._local = threading.local()
        self._tz = cfg.tz.key
        self.calendar_ids = [c for c in cfg.property_calendar_ids if c]

    @property
    def _svc(self):
        """One Google API client per thread: httplib2 isn't thread-safe."""
        svc = getattr(self._local, "svc", None)
        if svc is None:
            svc = self._local.svc = build("calendar", "v3", credentials=self._creds, cache_discovery=False)
        return svc

    def list_events(self, start: str, end: str) -> list[dict]:
        out = []
        for cid in self.calendar_ids:
            items = (self._svc.events().list(calendarId=cid, timeMin=start, timeMax=end, timeZone=self._tz,
                                             singleEvents=True, orderBy="startTime")
                     .execute(num_retries=RETRIES).get("items", []))
            out += [{"summary": e.get("summary", "").strip(), "start": e["start"].get("dateTime", e["start"].get("date")),
                     "end": e["end"].get("dateTime", e["end"].get("date")), "calendar": cid} for e in items]
        return sorted(out, key=lambda e: e["start"])
