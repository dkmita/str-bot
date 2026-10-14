"""Claude API cost log: one JSON line per call, plus a report (Telegram /usage or the command line).

CLI: python bot/data/usage.py path/to/usage.jsonl [days]
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# $ per million tokens: input, cache write (5-min, 1.25× input), cache read, output
PRICES = {
    "claude-opus-5-5": (4.00, 5.00, 0.20, 20.00),
    "claude-sonnet-5-5": (2.00, 2.50, 0.20, 10.00),
    "claude-haiku-4-5": (1.00, 1.25, 0.10, 5.00),
}
DEFAULT = PRICES["claude-opus-5-5"]


def cost(model: str, input: int, cache_write: int, cache_read: int, output: int, cache_write_1h: int = 0) -> float:
    """`cache_write` is 5-minute cache writes (1.25× input price); `cache_write_1h` is 1-hour writes (2×)."""
    p_in, p_write, p_read, p_out = PRICES.get(model, DEFAULT)
    return round((input * p_in + cache_write * p_write + cache_write_1h * 2 * p_in + cache_read * p_read
                  + output * p_out) / 1e6, 5)


def record(path: Path, *, ts: datetime, source: str, turn_id: str, call: int, model: str, effort: str | None,
           usage, stop_reason: str | None = None, tools: list[str] | None = None, preview: str | None = None) -> float:
    """Append one call to the log; returns its cost."""
    tokens = {"input": usage.input_tokens or 0,
              "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
              "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
              "output": usage.output_tokens or 0}
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
    split = getattr(usage, "cache_creation", None)  # per-TTL breakdown of cache writes, when the API reports it
    write_1h = (getattr(split, "ephemeral_1h_input_tokens", 0) or 0) if split else 0
    tokens["cache_write_1h"] = write_1h
    usd = cost(model, tokens["input"], tokens["cache_write"] - write_1h, tokens["cache_read"], tokens["output"],
               write_1h) + 0.01 * searches
    row = {"ts": ts.isoformat(timespec="seconds"), "source": source, "turn_id": turn_id, "call": call,
           "model": model, "effort": effort, **tokens, "cost_usd": usd, "stop_reason": stop_reason,
           "tools": tools or [], **({"web_searches": searches} if searches else {})}
    if preview is not None:
        row["preview"] = preview[:60]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return usd


def report(path: Path, now: datetime, days: int = 7) -> str:
    if not path.exists():
        return "No usage logged yet."
    since = (now - timedelta(days=days - 1)).date().isoformat()
    rows = [r for r in map(json.loads, path.read_text().splitlines()) if r["ts"][:10] >= since]
    if not rows:
        return f"No Claude calls in the last {days} days."

    turns: dict[str, dict] = {}
    for r in rows:
        t = turns.setdefault(r["turn_id"], {"ts": r["ts"], "source": r["source"], "cost": 0.0, "calls": 0,
                                            "preview": ""})
        t["cost"] += r["cost_usd"]
        t["calls"] += 1
        t["preview"] = t["preview"] or r.get("preview", "")

    by_day, by_source = defaultdict(float), defaultdict(lambda: [0.0, 0])
    for t in turns.values():
        by_day[t["ts"][:10]] += t["cost"]
        by_source[t["source"]][0] += t["cost"]
        by_source[t["source"]][1] += 1
    total = sum(by_day.values())
    lines = [f"💸 Claude spend, last {days} days: ${total:.2f}",
             f"{len(turns)} turns · {len(rows)} calls · avg ${total / len(turns):.3f}/turn", "", "By day"]
    for day in sorted(by_day):
        lines.append(f"{datetime.fromisoformat(day):%a %b %-d}: ${by_day[day]:.2f}")
    lines += ["", "By source"]
    for src, (usd, n) in sorted(by_source.items(), key=lambda kv: -kv[1][0]):
        lines.append(f"{src}: ${usd:.2f} ({n} turns)")
    lines += ["", "Priciest turns"]
    for t in sorted(turns.values(), key=lambda t: -t["cost"])[:5]:
        preview = (t["preview"][:40] + "…") if len(t["preview"]) > 40 else t["preview"]
        lines.append(f"{datetime.fromisoformat(t['ts']):%a %-I:%M%p} {t['source']} ${t['cost']:.3f} ({t['calls']} calls)"
                     + (f"\n  {preview}" if preview else ""))
    return "\n".join(lines)


if __name__ == "__main__":
    from zoneinfo import ZoneInfo
    print(report(Path(sys.argv[1]), datetime.now(ZoneInfo("America/Phoenix")),
                 int(sys.argv[2]) if len(sys.argv) > 2 else 7))
