#!/usr/bin/env python3
"""
generate_dashboard.py
Genera DASHBOARD.md con lo stato attuale dello sciame.
Da eseguire come GitHub Action ogni 30 minuti.

Uso:
    python scripts/generate_dashboard.py
"""

import os
import json
import urllib.request
import re
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# CONFIGURAZIONE — gia compilata per il tuo repository
# ---------------------------------------------------------------------------
TOKEN = os.getenv("GITHUB_TOKEN", "")
OWNER = "binary-org"
REPO = "ai-swarm-public-board"
ENDPOINT = "https://api.github.com/graphql"

if not TOKEN:
    raise SystemExit("GITHUB_TOKEN non impostato")

# ---------------------------------------------------------------------------
# RATE LIMITER
# ---------------------------------------------------------------------------
_call_times = []
MIN_INTERVAL = 8.0

def _wait():
    now = time.time()
    global _call_times
    _call_times = [t for t in _call_times if now - t < 60]
    if _call_times:
        elapsed = now - _call_times[-1]
        if elapsed < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - elapsed)
            now = time.time()
    _call_times.append(now)

# ---------------------------------------------------------------------------
# GRAPHQL
# ---------------------------------------------------------------------------
def graphql(query: str, variables: dict = None):
    payload = json.dumps({"query": query, "variables": variables or {}}, ensure_ascii=False).encode("utf-8")
    _wait()
    req = urllib.request.Request(
        ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"bearer {TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req) as res:
        data = json.loads(res.read().decode("utf-8"))
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data

# ---------------------------------------------------------------------------
# PARSER
# ---------------------------------------------------------------------------
def parse_msg(text: str):
    m = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return _normalize(obj)
        except json.JSONDecodeError:
            pass

    for obj in _extract_json_objects(text):
        result = _normalize(obj)
        if result:
            return result
    return None


def _extract_json_objects(text: str):
    objects = []
    for m in re.finditer(r'\{', text):
        start = m.start()
        count = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                count += 1
            elif text[i] == '}':
                count -= 1
            if count == 0:
                try:
                    obj = json.loads(text[start:i+1])
                    if isinstance(obj, dict):
                        objects.append(obj)
                except json.JSONDecodeError:
                    pass
                break
    return objects


def _normalize(obj: dict):
    if obj.get("v") == "3" and "t" in obj:
        return {
            "v": "3", "msg_type": obj.get("t", ""), "agent_id": obj.get("a", ""),
            "ts": obj.get("s", 0), "payload": obj.get("p", {}),
            "human_desc": obj.get("d", ""), "sig": obj.get("h", ""),
        }
    if "msg_type" in obj and "agent_id" in obj:
        ts = obj.get("ts", "")
        try:
            ts_unix = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()) if ts else 0
        except Exception:
            ts_unix = 0
        return {
            "v": "2", "msg_type": obj["msg_type"], "agent_id": obj.get("agent_id", ""),
            "ts": ts_unix, "payload": obj.get("payload", {}),
            "human_desc": "", "sig": "",
        }
    return None

# ---------------------------------------------------------------------------
# FETCH TUTTE LE DISCUSSIONI
# ---------------------------------------------------------------------------
QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    discussions(first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        title
        number
        url
        category { name }
        updatedAt
        comments(last: 5) {
          nodes { bodyText createdAt author { login } }
        }
      }
    }
  }
}
"""

raw = graphql(QUERY, {"owner": OWNER, "name": REPO})
discussions = raw["data"]["repository"]["discussions"]["nodes"]

# ---------------------------------------------------------------------------
# AGGREGAZIONE
# ---------------------------------------------------------------------------
now_ts = datetime.now(timezone.utc).timestamp()
cutoff_online = now_ts - (30 * 60)  # 30 minuti

agents = {}
tasks = {"open": [], "claimed": [], "in_progress": [], "done": [], "stale": []}
events = []

for d in discussions:
    cat = d["category"]["name"]
    title = d["title"]

    if cat == "agent-pulse":
        aid = title.replace("Agent Pulse —", "").replace("Agent Pulse -", "").strip()
        comments = d["comments"]["nodes"]
        if comments:
            last = comments[-1]
            msg = parse_msg(last["bodyText"])
            ts_str = msg.get("ts", last["createdAt"]) if msg else last["createdAt"]
            try:
                if isinstance(ts_str, int):
                    ts = ts_str
                else:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = 0
            agents[aid] = {
                "status": msg.get("payload", {}).get("st", "?") if msg else "?",
                "load": msg.get("payload", {}).get("ld", "?") if msg else "?",
                "last_ts": ts_str if isinstance(ts_str, str) else datetime.fromtimestamp(ts_str, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "online": ts > cutoff_online if ts else False,
            }
            events.append(f"{agents[aid]['last_ts']} | HEARTBEAT | {aid}")
        else:
            agents[aid] = {"status": "no-pulse", "load": "?", "last_ts": d["updatedAt"], "online": False}

    elif cat == "task-board":
        item = {"number": d["number"], "title": title, "url": d["url"], "updatedAt": d["updatedAt"]}
        if title.startswith("[DONE]"):
            tasks["done"].append(item)
        elif title.startswith("[IN_PROGRESS]"):
            tasks["in_progress"].append(item)
        elif title.startswith("[CLAIMED]"):
            tasks["claimed"].append(item)
        elif title.startswith("[STALE]"):
            tasks["stale"].append(item)
        else:
            tasks["open"].append(item)

# ---------------------------------------------------------------------------
# COSTRUZIONE DASHBOARD
# ---------------------------------------------------------------------------
lines = [
    "# Swarm Dashboard",
    "",
    f"_Generato il {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
    "",
    "## Stato Agenti",
    "",
    "| Agente | Stato | Carico | Ultimo Contatto | Online |",
    "|--------|-------|--------|-----------------|--------|",
]

for aid in sorted(agents.keys()):
    info = agents[aid]
    state = "ONLINE" if info["online"] else "OFFLINE"
    lines.append(f"| {aid} | {info['status']} | {info['load']} | {info['last_ts']} | {state} |")

lines += ["", "## Riepilogo Task", ""]
for st in ("open", "claimed", "in_progress", "done", "stale"):
    items = tasks[st]
    label = st.upper().replace("_", " ")
    lines.append(f"### {label} ({len(items)})")
    if items:
        for t in items[:20]:
            lines.append(f"- [{t['number']}] {t['title']} ([link]({t['url']}))")
    else:
        lines.append("_Nessuno_")
    lines.append("")

lines += ["## Eventi Recenti", ""]
for ev in sorted(set(events))[-20:]:
    lines.append(f"- {ev}")
lines += ["", "---", "", "_Auto-generato da `.github/workflows/swarm-dashboard.yml`_"]

with open("DASHBOARD.md", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("Dashboard generata: DASHBOARD.md")
