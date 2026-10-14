"""Tool schemas for Claude, and the dispatcher."""

import json
from datetime import date, timedelta

from integrations import ical

TOOLS = [
    {
        "name": "list_stays",
        "description": "Reservations (and blocked nights) on Airbnb and VRBO from their calendar feeds, plus any "
                       "double bookings. Dates are YYYY-MM-DD; check_out is the departure day.",
        "input_schema": {
            "type": "object",
            "properties": {"start": {"type": "string", "description": "YYYY-MM-DD, default today"},
                           "days": {"type": "integer", "description": "Default 60"},
                           "include_blocks": {"type": "boolean", "description": "Default false"}},
        },
    },
    {
        "name": "property_calendar",
        "description": "Events on the Sedona Home calendar (maintenance, owner plans) between two dates.",
        "input_schema": {
            "type": "object",
            "properties": {"start": {"type": "string", "description": "YYYY-MM-DD"},
                           "end": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["start", "end"],
        },
    },
    {
        "name": "list_files",
        "description": "List the markdown notes in the repo (property.md, notes/...).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "Read a markdown note from the repo.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "edit_file",
        "description": "Replace one exact passage in a markdown note (old_text must appear exactly once).",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"},
                                                          "new_text": {"type": "string"}},
                         "required": ["path", "old_text", "new_text"]},
    },
    {
        "name": "write_file",
        "description": "Create or fully rewrite a markdown note.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                         "required": ["path", "content"]},
    },
]


def run(agent, name: str, args: dict) -> str:
    match name:
        case "list_stays":
            start = date.fromisoformat(args["start"]) if args.get("start") else agent.today()
            end = start + timedelta(days=args.get("days", 60))
            entries = ical.load_all(agent.cfg.feeds)
            shown = [s for s in entries if s["check_out"] > start.isoformat() and s["check_in"] < end.isoformat()
                     and (args.get("include_blocks") or s["kind"] == "stay")]
            doubles = [[ical.describe(a), ical.describe(b)] for a, b in ical.double_bookings(entries)
                       if a["check_out"] > start.isoformat()]
            result = {"platforms": sorted(agent.cfg.feeds), "stays": shown, "double_bookings": doubles}
        case "property_calendar":
            result = agent.calendar.list_events(f"{args['start']}T00:00:00Z", f"{args['end']}T23:59:59Z")
        case "list_files":
            result = agent.repo.list()
        case "read_file":
            return agent.repo.read(args["path"])
        case "edit_file":
            return agent.repo.edit(args["path"], args["old_text"], args["new_text"])
        case "write_file":
            return agent.repo.write(args["path"], args["content"])
        case _:
            raise ValueError(f"Unknown tool: {name}")
    return json.dumps(result, ensure_ascii=False)
