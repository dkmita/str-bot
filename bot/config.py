"""Settings loaded from environment variables (see .env.example)."""

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_chat_id: int | None
    model: str
    repo_dir: Path
    tz: ZoneInfo  # the property's timezone (Sedona: America/Phoenix, no DST)
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    property_calendar_ids: list[str]
    feeds: dict[str, str]  # platform → iCal export URL (secret)
    gmail_tokens: dict[str, str]  # inbox label → refresh token (read-only; same OAuth client)
    git_push: bool
    log_content: bool
    hc_ping_url: str | None
    hc_deps_ping_url: str | None  # healthchecks.io check for the 30-min connection checks

    @property
    def state_dir(self) -> Path:
        return self.repo_dir / ".state"


def load() -> Config:
    chat_id = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID")
    feeds = {name: url for name, url in (("Airbnb", os.environ.get("AIRBNB_ICAL_URL")),
                                         ("VRBO", os.environ.get("VRBO_ICAL_URL"))) if url}
    return Config(
        telegram_token=_required("TELEGRAM_BOT_TOKEN"),
        allowed_chat_id=int(chat_id) if chat_id else None,
        model=os.environ.get("STR_MODEL", "claude-opus-5-5"),
        repo_dir=Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parent.parent)),
        tz=ZoneInfo(os.environ.get("PROPERTY_TIMEZONE", "America/Phoenix")),
        google_client_id=_required("GOOGLE_CLIENT_ID"),
        google_client_secret=_required("GOOGLE_CLIENT_SECRET"),
        google_refresh_token=_required("GOOGLE_REFRESH_TOKEN"),
        # "Sedona Home" (hot tub maintenance etc.); read-only
        property_calendar_ids=os.environ.get("PROPERTY_CALENDAR_IDS", "leomproperty@gmail.com").split(","),
        feeds=feeds,
        # Airbnb mail goes to d.kmita@gmail.com (the main token); VRBO's to leomproperty@gmail.com (its own sign-in)
        gmail_tokens={label: tok for label, tok in (("d.kmita", os.environ.get("GOOGLE_REFRESH_TOKEN")),
                                                     ("leomproperty", os.environ.get("LEOM_GOOGLE_REFRESH_TOKEN")))
                      if tok},
        git_push=os.environ.get("GIT_PUSH", "false").lower() == "true",
        log_content=os.environ.get("LOG_CONTENT", "true").lower() == "true",
        hc_ping_url=os.environ.get("HC_PING_URL") or None,
        hc_deps_ping_url=os.environ.get("HC_DEPS_PING_URL") or None,
    )
