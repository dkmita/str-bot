"""Home Assistant (at the Sedona house), read-only for now: locks, door sensors, batteries.

Reached over Tailscale: the Fly machine runs tailscaled in userspace mode (entrypoint.sh) and requests go through its
local SOCKS5 proxy. (Its HTTP proxy adds a forwarding header that HA rejects with 400 from an untrusted proxy.) Auth: a long-lived token of a dedicated non-admin HA user (HA_TOKEN)."""

import re

import httpx

PROXY = "socks5://localhost:1055"


class HomeAssistant:
    def __init__(self, url: str, token: str, proxy: str | None = PROXY):
        self.url = url.rstrip("/")
        self._client = httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=15, proxy=proxy)

    def _get(self, path: str):
        r = self._client.get(self.url + path)
        r.raise_for_status()
        return r.json()

    def ping(self) -> str:
        return self._get("/api/")["message"]  # "API running."

    def config(self) -> dict:
        c = self._get("/api/config")
        return {"location": c.get("location_name"), "version": c.get("version"), "time_zone": c.get("time_zone")}

    def devices(self) -> dict[str, list[dict]]:
        """Locks, door/window sensors, leak sensors and device batteries, as {group: [{entity, name, state, ...}]}."""
        out = {"locks": [], "doors": [], "leaks": [], "batteries": []}
        for s in self._get("/api/states"):
            eid, attrs = s["entity_id"], s.get("attributes", {})
            name, cls = attrs.get("friendly_name", eid), attrs.get("device_class")
            row = {"entity": eid, "name": name, "state": s["state"], "changed": s.get("last_changed", "")[:16]}
            leak = cls == "moisture" or "leak" in name.lower()
            if eid.startswith("lock.") and "remote" not in name.lower():  # ratgdo's "Lock remotes" isn't a door lock
                out["locks"].append(row)
            elif eid.startswith("binary_sensor.") and leak:
                out["leaks"].append(row)
            elif eid.startswith("binary_sensor.") and cls in ("door", "garage_door", "window", "opening"):
                out["doors"].append(row)
            elif cls == "battery" and eid.startswith("sensor.") and not _PERSONAL.search(name):
                try:
                    level = float(s["state"])
                except ValueError:
                    continue
                out["batteries"].append(row | {"level": level})
        out["batteries"].sort(key=lambda r: r["level"])
        return out


_PERSONAL = re.compile(r"iphone|ipad|phone|pixel|galaxy|sm-[a-z]\d|tablet|watch|macbook", re.I)  # people's devices


def locks_text(d: dict) -> str:
    icon = {"locked": "🔒", "unlocked": "🔓", "jammed": "⚠️", "unavailable": "❔"}
    lines = ["🏠 Sedona house"]
    lines += [f"{icon.get(l['state'], '•')} {l['name']}: {l['state']}" for l in d["locks"]] or ["(no locks found)"]
    opened = [x for x in d["doors"] if x["state"] == "on"]
    lines += [f"🚪 open: {x['name']}" for x in opened]
    if d["doors"] and not opened:
        lines.append(f"✔️ All {len(d['doors'])} doors/windows closed")
    wet = [x for x in d.get("leaks", []) if x["state"] == "on"]
    lines += [f"💧 LEAK: {x['name']}" for x in wet]
    if d.get("leaks") and not wet:
        lines.append(f"✔️ No leaks ({len(d['leaks'])} sensors)")
    low = [b for b in d["batteries"] if b["level"] <= 30]
    lines += [f"🪫 {b['name']}: {b['level']:.0f}%" for b in low]
    if d["batteries"] and not low:
        lines.append(f"🔋 Batteries OK (lowest {d['batteries'][0]['level']:.0f}%)")
    return "\n".join(lines)
