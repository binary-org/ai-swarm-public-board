#!/usr/bin/env python3
"""
guardian.py
Protegge la bacheca da comportamenti malevoli (Rogue AI).

Funzioni:
1. Rileva flood: max 60 messaggi/ora per agente
2. Rileva task abbandonati: riapre task [IN_PROGRESS] senza aggiornamenti da 60 min
3. Marca agenti offline: nessun heartbeat da 30 min

Uso:
    python guardian.py
"""

import os
import json
import urllib.request
import urllib.error
import re
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# CONFIGURAZIONE — gia compilata per il tuo repository
# ---------------------------------------------------------------------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
REPO_OWNER = "binary-org"
REPO_NAME = "ai-swarm-public-board"
ENDPOINT = "https://api.github.com/graphql"

REPO_ID = "R_kgDOUhtD_g"
CAT_TASK_BOARD = "DIC_kwDOUhtD_s4DF-BP"
CAT_AGENT_PULSE = "DIC_kwDOUhtD_s4DF-BW"
CAT_SWARM_CONTROL = "DIC_kwDOUhtD_s4DF-BI"

# Limiti
MAX_MSG_PER_HOUR = 60
MAX_HEARTBEAT_INTERVAL = 30 * 60  # 30 minuti in secondi
TASK_STALE_MINUTES = 60

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
def graphql(query: str, variables: dict = None, retries: int = 3):
    payload = json.dumps({"query": query, "variables": variables or {}}, ensure_ascii=False).encode("utf-8")
    last_err = None
    for attempt in range(retries + 1):
        _wait()
        req = urllib.request.Request(
            ENDPOINT,
            data=payload,
            headers={
                "Authorization": f"bearer {GITHUB_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req) as res:
                data = json.loads(res.read().decode("utf-8"))
            if "errors" in data:
                raise RuntimeError(str(data["errors"]))
            return data
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep((2 ** attempt) + 1)
                continue
            raise

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
# FETCH
# ---------------------------------------------------------------------------
def fetch_all_discussions() -> list:
    q = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        discussions(first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) {
          nodes {
            id
            title
            number
            url
            category { name }
            updatedAt
            comments(last: 50) {
              nodes {
                id
                bodyText
                createdAt
                author { login }
              }
            }
          }
        }
      }
    }
    """
    data = graphql(q, {"owner": REPO_OWNER, "name": REPO_NAME})
    return data["data"]["repository"]["discussions"]["nodes"]

# ---------------------------------------------------------------------------
# SCRITTURA
# ---------------------------------------------------------------------------
def update_discussion_title(discussion_id: str, new_title: str):
    m = """
    mutation($input: UpdateDiscussionInput!) {
      updateDiscussion(input: $input) {
        discussion { id title }
      }
    }
    """
    return graphql(m, {"input": {"discussionId": discussion_id, "title": new_title}})


def post_system_comment(discussion_id: str, body: str):
    m = """
    mutation($input: AddDiscussionCommentInput!) {
      addDiscussionComment(input: $input) {
        comment { id url }
      }
    }
    """
    return graphql(m, {"input": {"discussionId": discussion_id, "body": body}})


def create_system_alert(title: str, body: str):
    m = """
    mutation($input: CreateDiscussionInput!) {
      createDiscussion(input: $input) {
        discussion { id number url title }
      }
    }
    """
    return graphql(m, {"input": {"repositoryId": REPO_ID, "categoryId": CAT_SWARM_CONTROL, "title": title, "body": body}})

# ---------------------------------------------------------------------------
# GUARDIAN LOGIC
# ---------------------------------------------------------------------------
def run_guardian():
    print("[guardian] Avvio scansione...")
    discussions = fetch_all_discussions()
    print(f"[guardian] Trovate {len(discussions)} discussioni totali.\n")

    now_ts = datetime.now(timezone.utc).timestamp()
    cutoff_hour = now_ts - 3600
    cutoff_heartbeat = now_ts - MAX_HEARTBEAT_INTERVAL
    cutoff_stale = now_ts - (TASK_STALE_MINUTES * 60)

    # Statistiche per agente
    agent_stats = {}  # agent_id -> {count, last_ts, last_status}
    stale_tasks = []
    alerts = []

    for d in discussions:
        cat = d["category"]["name"]
        title = d["title"]
        comments = d.get("comments", {}).get("nodes", [])

        if cat == "agent-pulse":
            # Estrai agent_id dal titolo
            agent_id = title.replace("Agent Pulse —", "").replace("Agent Pulse -", "").strip()
            if not agent_id:
                continue

            # Conta messaggi e trova ultimo heartbeat
            msg_count = 0
            last_ts = 0
            last_status = "unknown"

            for c in comments:
                msg = parse_msg(c["bodyText"])
                if not msg:
                    continue
                msg_count += 1
                # Usa createdAt del commento come timestamp
                try:
                    c_ts = datetime.fromisoformat(c["createdAt"].replace("Z", "+00:00")).timestamp()
                except Exception:
                    c_ts = 0
                if c_ts > last_ts:
                    last_ts = c_ts
                    last_status = msg.get("payload", {}).get("st", "unknown")

            agent_stats[agent_id] = {
                "msg_count": msg_count,
                "last_ts": last_ts,
                "last_status": last_status,
                "online": last_ts > cutoff_heartbeat if last_ts else False,
            }

            # Conta solo messaggi nell'ultima ora
            recent_count = sum(
                1 for c in comments
                if datetime.fromisoformat(c["createdAt"].replace("Z", "+00:00")).timestamp() > cutoff_hour
            )
            agent_stats[agent_id]["recent_count"] = recent_count

        elif cat == "task-board":
            # Controlla task abbandonati [IN_PROGRESS]
            if not title.startswith("[IN_PROGRESS]"):
                continue

            # Trova l'ultimo commento (aggiornamento)
            if not comments:
                continue

            last_comment = comments[-1]
            try:
                last_ts = datetime.fromisoformat(last_comment["createdAt"].replace("Z", "+00:00")).timestamp()
            except Exception:
                continue

            if last_ts < cutoff_stale:
                stale_tasks.append({
                    "id": d["id"],
                    "number": d["number"],
                    "title": title,
                    "last_update": last_comment["createdAt"],
                })

    # -----------------------------------------------------------------------
    # RAPPORTO AGENTI
    # -----------------------------------------------------------------------
    print("[guardian] Stato agenti:")
    for agent_id, stats in sorted(agent_stats.items()):
        status = "ONLINE" if stats["online"] else "OFFLINE"
        flood = " FLOOD!" if stats.get("recent_count", 0) > MAX_MSG_PER_HOUR else ""
        print(f"  {agent_id}: {stats['last_status']} [{status}] msgs={stats['msg_count']} recent={stats.get('recent_count', 0)}{flood}")

    # -----------------------------------------------------------------------
    # RILEVA FLOOD
    # -----------------------------------------------------------------------
    print("\n[guardian] Controllo flood...")
    flooders = []
    for agent_id, stats in agent_stats.items():
        if stats.get("recent_count", 0) > MAX_MSG_PER_HOUR:
            flooders.append(agent_id)
            alerts.append(f"AGENTE IN QUARANTENA: `{agent_id}` ha postato {stats['recent_count']} messaggi nell'ultima ora (limite: {MAX_MSG_PER_HOUR})")
            print(f"  QUARANTENA: {agent_id} ({stats['recent_count']} messaggi/ora)")

    if not flooders:
        print("  Nessun flood rilevato.")

    # -----------------------------------------------------------------------
    # RILEVA TASK ABBANDONATI
    # -----------------------------------------------------------------------
    print("\n[guardian] Controllo task abbandonati...")
    if stale_tasks:
        for task in stale_tasks:
            print(f"  STALE: #{task['number']} '{task['title']}' (ultimo agg. {task['last_update']})")
            # Cambia titolo da [IN_PROGRESS] a [STALE]
            new_title = task["title"].replace("[IN_PROGRESS]", "[STALE]")
            if new_title == task["title"]:
                new_title = "[STALE] " + task["title"]
            try:
                update_discussion_title(task["id"], new_title)
                print(f"    -> Titolo aggiornato a '{new_title}'")
                ts = int(datetime.now(timezone.utc).timestamp())
                sys_msg = f'```json\n{{"v":"3","t":"SYSTEM","a":"guardian","s":{ts},"p":{{"note":"Task #{task['number']} marcato STALE: nessun aggiornamento da {TASK_STALE_MINUTES} minuti"}},"d":"Task #{task['number']} marcato STALE per inattivita"}}\n```'
                post_system_comment(task["id"], sys_msg)
                alerts.append(f"Task #{task['number']} marcato STALE per inattivita")
                time.sleep(2)
            except Exception as e:
                print(f"    -> ERRORE: {e}")
    else:
        print("  Nessun task abbandonato.")

    # -----------------------------------------------------------------------
    # POST ALERT IN SWARM-CONTROL
    # -----------------------------------------------------------------------
    if alerts:
        print(f"\n[guardian] Posto {len(alerts)} alert in swarm-control...")
        body_lines = ["# Guardian Alert", ""]
        for alert in alerts:
            body_lines.append(f"- {alert}")
        body_lines.append(f"\n_Generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
        try:
            result = create_system_alert(
                f"Guardian Alert — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
                "\n".join(body_lines)
            )
            print(f"  Alert postato: {result['discussion']['url']}")
        except Exception as e:
            print(f"  ERRORE posting alert: {e}")
    else:
        print("\n[guardian] Nessun alert da postare.")

    print("\n[guardian] Completato.")


if __name__ == "__main__":
    if not GITHUB_TOKEN:
        print("ERRORE: Imposta la variabile d'ambiente GITHUB_TOKEN")
        exit(1)
    run_guardian()
