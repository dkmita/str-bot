"""The system prompt and tool schemas change only on purpose: delete tests/snapshots/* to re-record."""

import json
from pathlib import Path

import pytest

import tools
from agent import Agent
from data.repo import Repo

SNAP = Path(__file__).parent / "snapshots"


def check(name: str, text: str) -> None:
    path = SNAP / name
    if not path.exists():
        path.write_text(text)
        pytest.skip(f"recorded {name}")
    assert text == path.read_text()


def test_prompt_addendum_unchanged(cfg):
    agent = Agent(cfg, calendar=None, repo=Repo(cfg.repo_dir, push=False))
    check("system_addendum.txt", agent.system.split("Test instructions.\n", 1)[1])


def test_tools_unchanged():
    check("tools.json", json.dumps(tools.TOOLS, indent=1, ensure_ascii=False))


def test_host_md_is_read_only_for_the_bot(repo_dir):
    repo = Repo(repo_dir, push=False)
    with pytest.raises(ValueError):
        repo.write("host.md", "x")
    assert repo.edit("property.md", "4pm", "3pm") == "Edited property.md"


def test_locks_text():
    from integrations.homeassistant import locks_text
    d = {"locks": [{"name": "Garage Lock", "state": "unlocked"}],
         "doors": [{"name": "Back Door Contact Sensor", "state": "off"}, {"name": "Hallway Sliding Door", "state": "off"}],
         "leaks": [{"name": "Kitchen Leak Detector", "state": "off"}],
         "batteries": [{"name": "Kitchen Leak Detector Battery", "level": 3.0}, {"name": "Front sensor", "level": 90.0}]}
    assert locks_text(d) == ("🏠 Sedona house\n🔓 Garage Lock: unlocked\n✔️ All 2 doors/windows closed\n"
                             "✔️ No leaks (1 sensors)\n🪫 Kitchen Leak Detector Battery: 3%")
    d["leaks"][0]["state"] = "on"
    assert "💧 LEAK: Kitchen Leak Detector" in locks_text(d)
