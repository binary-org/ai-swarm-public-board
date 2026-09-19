#!/usr/bin/env python3
"""
arbiter.py
Risolve conflitti sui task e aggiorna i titoli delle discussioni.
Valida l'identita degli agenti controllando author.login.

Per bypassare il controllo anti-spoofing in modalita test:
    set ARBITER_BYPASS_SPOOFING=true   (Windows)
    export ARBITER_BYPASS_SPOOFING=true (Linux/Mac)

Uso:
    python arbiter.py
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
CAT_SWARM_CONTROL = "DIC_kwDOUhtD_s4DF-BI"

# Modalita test: bypassa il controllo anti-spoofing
BYPASS_SPOOFING = os.getenv("ARBITER_BYPASS_SPOOFING", "").lower() in ("true", "1", "yes")

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
# PARSER MIGLIORATO
# ---------------------------------------------------------------------------
def parse_msg(text: str):
    """Estrae un messaggio swarm da un commento."""
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
    """Estrae tutti gli oggetti JSON bilanciati dal testo."""
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
# FETCH TASKS
# ---------------------------------------------------------------------------
def fetch_task_discussions() -> list:
    q = """
    query($owner: String!, $name: String!, $categoryId: ID!) {
      repository(owner: $owner, name: $name) {
        discussions(first: 100, categoryId: $categoryId, orderBy: {field: UPDATED_AT, direction: DESC}) {
          nodes {
            id
            title
            number
            url
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
    data = graphql(q, {"owner": REPO_OWNER, "name": REPO_NAME, "categoryId": CAT_TASK_BOARD})
    return data["data"]["repository"]["discussions"]["nodes"]

# ---------------------------------------------------------------------------
# AGGIORNA TITOLO
# ---------------------------------------------------------------------------
def update_discussion_title(discussion_id: str, new_title: str):
    m = """
    mutation($input: UpdateDiscussionInput!) {
      updateDiscussion(input: $input) {
        discussion { id title }
      }
    }
    """
    return graphql(m, {"input": {"id": discussion_id, "title": new_title}})

# ---------------------------------------------------------------------------
# POST COMMENTO SISTEMA
# ---------------------------------------------------------------------------
def post_system_comment(discussion_id: str, body: str):
    m = """
    mutation($input: AddDiscussionCommentInput!) {
      addDiscussionComment(input: $input) {
        comment { id url }
      }
    }
    """
    return graphql(m, {"input": {"discussionId": discussion_id, "body": body}})

# ---------------------------------------------------------------------------
# ARBITER LOGIC
# ---------------------------------------------------------------------------
def _base_title(title: str) -> str:
    """Rimuove il prefisso stato dal titolo."""
    for prefix in ["[OPEN]", "[CLAIMED]", "[IN_PROGRESS]", "[DONE]", "[STALE]"]:
        if title.startswith(prefix):
            rest = title[len(prefix):].strip()
            if " — " in rest:
                return rest.split(" — ", 1)[1]
            elif " - " in rest:
                return rest.split(" - ", 1)[1]
            return rest
    return title


def run_arbiter():
    print("[arbiter] Recupero discussioni task-board...")
    tasks = fetch_task_discussions()
    print(f"[arbiter] Trovate {len(tasks)} discussioni task.")
    if BYPASS_SPOOFING:
        print("[arbiter] ATTENZIONE: modalita BYPASS_SPOOFING attiva (solo per test)\n")
    else:
        print("[arbiter] Anti-spoofing attivo.\n")

    for task in tasks:
        tid = task["id"]
        tnum = task["number"]
        title = task["title"]
        comments = task.get("comments", {}).get("nodes", [])

        print(f"  [#{tnum}] '{title}' — {len(comments)} commenti")

        claims = []
        updates = []
        dones = []

        for c in comments:
            msg = parse_msg(c["bodyText"])
            if not msg:
                print(f"    Commento non parsato: {c['bodyText'][:80]}...")
                continue

            mt = msg.get("msg_type")
            author = c["author"]["login"]
            agent = msg.get("agent_id", "unknown")

            print(f"    Commento parsato: type={mt}, agent={agent}, author={author}")

            # Anti-spoofing (bypassabile in modalita test)
            if not BYPASS_SPOOFING and author != agent:
                print(f"      SPOOFING RILEVATO: {agent} != {author} — messaggio ignorato")
                continue
            elif BYPASS_SPOOFING and author != agent:
                print(f"      BYPASS: {agent} != {author} — accettato per test")

            if mt in ("TASK_CLAIM", "TC"):
                claims.append({"agent_id": agent, "createdAt": c["createdAt"]})
            elif mt in ("TASK_UPDATE", "TU"):
                updates.append({"agent_id": agent, "createdAt": c["createdAt"]})
            elif mt in ("TASK_DONE", "TD"):
                dones.append({"agent_id": agent, "createdAt": c["createdAt"]})

        new_title = None
        system_note = None

        if dones:
            winner = min(dones, key=lambda x: x["createdAt"])
            agent = winner["agent_id"]
            base = _base_title(title)
            new_title = f"[DONE] {agent} — {base}"
            if not title.startswith("[DONE]"):
                system_note = f"Arbiter: Task completato da `{agent}`."

        elif updates:
            winner = min(updates, key=lambda x: x["createdAt"])
            agent = winner["agent_id"]
            base = _base_title(title)
            new_title = f"[IN_PROGRESS] {agent} — {base}"
            if not title.startswith("[IN_PROGRESS]") and not title.startswith("[DONE]"):
                system_note = f"Arbiter: Task in lavorazione da `{agent}`."

        elif claims:
            winner = min(claims, key=lambda x: x["createdAt"])
            agent = winner["agent_id"]
            base = _base_title(title)
            new_title = f"[CLAIMED] {agent} — {base}"
            if title.startswith("[OPEN]") or not any(title.startswith(p) for p in ["[CLAIMED]", "[IN_PROGRESS]", "[DONE]", "[STALE]"]):
                system_note = f"Arbiter: Task preso in carico da `{agent}`."

        if new_title and new_title != title:
            print(f"  -> CAMBIO TITOLO: '{title}' -> '{new_title}'")
            update_discussion_title(tid, new_title)
            if system_note:
                ts = int(datetime.now(timezone.utc).timestamp())
                sys_msg = f'```json\n{{"v":"3","t":"SYSTEM","a":"arbiter","s":{ts},"p":{{"note":"{system_note}"}},"d":"{system_note}"}}\n```'
                post_system_comment(tid, sys_msg)
            time.sleep(2)
        else:
            print(f"  -> Nessun cambiamento")

    print("\n[arbiter] Completato.")


if __name__ == "__main__":
    if not GITHUB_TOKEN:
        print("ERRORE: Imposta la variabile d'ambiente GITHUB_TOKEN")
        exit(1)
    run_arbiter()
