"""Sandboxed file access to the str-bot repo, plus git commit/push."""

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Files the agent may never read or write.
BLOCKED = {".env", ".git", ".state", ".venv", "bot", "Dockerfile", "fly.toml", ".github"}


class Repo:
    def __init__(self, root: Path, push: bool):
        self.root = root.resolve()
        self.push = push

    def _resolve(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError(f"Path outside the repo: {rel}")
        top = path.relative_to(self.root).parts[0]
        if top in BLOCKED or top.startswith(".env"):
            raise ValueError(f"Access to {top} is not allowed")
        return path

    def read(self, rel: str) -> str:
        return self._resolve(rel).read_text()

    def write(self, rel: str, content: str) -> str:
        if not rel.endswith(".md"):
            raise ValueError("Only .md files can be written")
        path = self._resolve(rel)
        if path in (self.root / "CLAUDE.md", self.root / "host.md"):
            raise ValueError(f"{path.name} is read-only for the bot; propose changes to me instead")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return f"Wrote {rel} ({len(content)} chars)"

    def edit(self, rel: str, old: str, new: str) -> str:
        """Replace one exact passage. Fails unless `old` appears exactly once."""
        path = self._resolve(rel)
        if not rel.endswith(".md") or path in (self.root / "CLAUDE.md", self.root / "host.md"):
            raise ValueError("Only .md files other than CLAUDE.md and host.md can be edited")
        text = path.read_text()
        count = text.count(old)
        if count != 1:
            raise ValueError(f"The text to replace appears {count} times in {rel}; include more context so it's unique")
        path.write_text(text.replace(old, new))
        return f"Edited {rel}"

    def list(self, rel: str = ".") -> list[str]:
        base = self.root if rel in ("", ".") else self._resolve(rel)
        return sorted(
            str(p.relative_to(self.root))
            for p in base.rglob("*.md")
            if not p.relative_to(self.root).parts[0] in BLOCKED
        )

    def commit(self, message: str) -> bool:
        """Commit any changed .md files. Returns True if a commit was made."""
        git = ["git", "-C", str(self.root)]
        subprocess.run([*git, "add", "--", "*.md"], check=True)
        if subprocess.run([*git, "diff", "--cached", "--quiet"]).returncode == 0:
            return False
        files = subprocess.run([*git, "diff", "--cached", "--name-only"],
                               capture_output=True, text=True).stdout.split()
        subprocess.run([*git, "commit", "-q", "-m", message], check=True)
        log.info("Committed %s", ", ".join(files))
        if self.push:
            subprocess.run([*git, "pull", "-q", "--rebase"], check=True)
            subprocess.run([*git, "push", "-q"], check=True)
            log.info("Pushed to GitHub")
        return True
