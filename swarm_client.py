#!/usr/bin/env python3
"""
swarm_client.py v3 — Resistente a Rogue AI
Protocollo compatto, validazione identita, rate limiting, retry.
"""

import os
import json
import urllib.request
import urllib.error
import re
import time
import hashlib
import hmac
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# CONFIGURAZIONE — gia compilata per il tuo repository
# ---------------------------------------------------------------------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
REPO_OWNER = "binary-org"
REPO_NAME = "ai-swarm-public-board"
ENDPOINT = "https://api.github.com/graphql"

REPO_ID = "R_kgDOUhtD_g"
CAT_SWARM_CONTROL = "DIC_kwDOUhtD_s4DF-BI"
CAT_TASK_BOARD = "DIC_kwDOUhtD_s4DF-BP"
CAT_AGENT_PULSE = "DIC_kwDOUhtD_s4DF-BW"
CAT_SWARM_LOG = "DIC_kwDOUhtD_s4DF-BX"

# Opzionale: segreto condiviso per HMAC (lascia vuoto se non lo usi)
AGENT_SECRET = os.getenv("AGENT_SECRET", "")

# ---------------------------------------------------------------------------
# RATE LIMITER — massimo 1 chiamata ogni 10 secondi
# ---------------------------------------------------------------------------
_call_times = []
MIN_INTERVAL = 10.0

def _rate_limit_wait():
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
# RETRY + GRAPHQL — con backoff esponenziale
# ---------------------------------------------------------------------------
def graphql(query: str, variables: dict = None, retries: int = 3) -> dict:
    payload = json.dumps({"query": query, "variables": variables or {}}, ensure_ascii=False).encode("utf-8")
    last_err = None
    for attempt in range(retries + 1):
        _rate_limit_wait()
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
                raise RuntimeError(f"GraphQL error: {data['errors']}")
            return data
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError) as e:
            last_err = e
            if attempt < retries:
                time.sleep((2 ** attempt) + 1)
                continue
            raise last_err

# ---------------------------------------------------------------------------
# IDENTITA — firma HMAC opzionale
# ---------------------------------------------------------------------------
def sign_message(agent_id: str, ts: int, payload: dict) -> str:
    if not AGENT_SECRET:
        return ""
    body = f"{agent_id}:{ts}:{json.dumps(payload, sort_keys=True, ensure_ascii=False)}"
    return hmac.new(AGENT_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:16]

# ---------------------------------------------------------------------------
# COSTRUTTORI MESSAGGI v3 (compatti)
# ---------------------------------------------------------------------------
def build_msg(msg_type: str, agent_id: str, payload: dict, human_desc: str = "") -> str:
    ts = int(datetime.now(timezone.utc).timestamp())
    msg = {"v": "3", "t": msg_type, "a": agent_id, "s": ts, "p": payload}
    if human_desc:
        msg["d"] = human_desc
    if AGENT_SECRET:
        msg["h"] = sign_message(agent_id, ts, payload)
    return f"```json\n{json.dumps(msg, ensure_ascii=False)}\n```"


def register(agent_id: str, capabilities: list = None):
    return build_msg("REG", agent_id, {"cap": capabilities or []}, f"Agente {agent_id} registrato")


def heartbeat(agent_id: str, status: str = "idl", load: float = 0.0):
    desc = f"{agent_id} e {status}, carico {int(load*100)}%"
    return build_msg("HB", agent_id, {"st": status, "ld": round(load, 2)}, desc)


def task_new(agent_id: str, priority: str = "med", description: str = ""):
    return build_msg("TN", agent_id, {"pr": priority, "desc": description}, f"Nuovo task da {agent_id}: {description}")


def task_claim(agent_id: str, task_number: int):
    return build_msg("TC", agent_id, {"tn": task_number}, f"{agent_id} ha preso il task #{task_number}")


def task_update(agent_id: str, task_number: int, progress: int, note: str = ""):
    p = {"tn": task_number, "prog": progress}
    if note:
        p["note"] = note
    return build_msg("TU", agent_id, p, f"{agent_id} ha aggiornato il task #{task_number} al {progress}%")


def task_done(agent_id: str, task_number: int, result: str = ""):
    p = {"tn": task_number}
    if result:
        p["res"] = result
    return build_msg("TD", agent_id, p, f"{agent_id} ha completato il task #{task_number}")


def broadcast(agent_id: str, message: str):
    return build_msg("BC", agent_id, {"msg": message}, f"Broadcast da {agent_id}: {message}")


def error(agent_id: str, message: str, code: int = 0):
    p = {"msg": message}
    if code:
        p["code"] = code
    return build_msg("ERR", agent_id, p, f"ERRORE da {agent_id}: {message}")

# ---------------------------------------------------------------------------
# PARSER (v3 compatto + v2 legacy)
# ---------------------------------------------------------------------------
def parse_msg(text: str) -> dict | None:
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if not m:
    m = re.search(r'(\{.*?\})', text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("v") == "3" and "t" in obj:
        return _norm_v3(obj)
    if "msg_type" in obj and "agent_id" in obj:
        return _norm_v2(obj)
    return None


def _norm_v3(obj: dict) -> dict:
    return {
        "v": "3", "msg_type": obj.get("t", ""), "agent_id": obj.get("a", ""),
        "ts": obj.get("s", 0), "payload": obj.get("p", {}),
        "human_desc": obj.get("d", ""), "sig": obj.get("h", ""),
    }


def _norm_v2(obj: dict) -> dict:
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

# ---------------------------------------------------------------------------
# LETTURA
# ---------------------------------------------------------------------------
def list_discussions(category_id: str = None, first: int = 50) -> list:
    q = """
    query($owner: String!, $name: String!, $categoryId: ID, $first: Int!) {
      repository(owner: $owner, name: $name) {
        discussions(first: $first, categoryId: $categoryId, orderBy: {field: UPDATED_AT, direction: DESC}) {
          nodes {
            id title number url bodyText category { name } updatedAt
            comments(last: 20) {
              nodes { bodyText createdAt author { login } }
            }
          }
        }
      }
    }
    """
    data = graphql(q, {"owner": REPO_OWNER, "name": REPO_NAME, "categoryId": category_id, "first": first})
    return data["data"]["repository"]["discussions"]["nodes"]


def get_discussion(number: int) -> dict:
    q = """
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        discussion(number: $number) {
          id title number url bodyText updatedAt
          comments(last: 100) {
            nodes { bodyText createdAt author { login } }
          }
        }
      }
    }
    """
    data = graphql(q, {"owner": REPO_OWNER, "name": REPO_NAME, "number": number})
    return data["data"]["repository"]["discussion"]


def find_pulse_thread(agent_id: str) -> dict | None:
    for d in list_discussions(category_id=CAT_AGENT_PULSE, first=100):
        if d["title"] == f"Agent Pulse — {agent_id}" or d["title"] == f"Agent Pulse - {agent_id}":
            return d
    return None

# ---------------------------------------------------------------------------
# SCRITTURA
# ---------------------------------------------------------------------------
def create_discussion(repo_id: str, category_id: str, title: str, body: str) -> dict:
    m = """
    mutation($input: CreateDiscussionInput!) {
      createDiscussion(input: $input) {
        discussion { id number url title }
      }
    }
    """
    data = graphql(m, {"input": {"repositoryId": repo_id, "categoryId": category_id, "title": title, "body": body}})
    return data["data"]["createDiscussion"]["discussion"]


def add_comment(discussion_id: str, body: str) -> dict:
    m = """
    mutation($input: AddDiscussionCommentInput!) {
      addDiscussionComment(input: $input) {
        comment { id url }
      }
    }
    """
    data = graphql(m, {"input": {"discussionId": discussion_id, "body": body}})
    return data["data"]["addDiscussionComment"]["comment"]


def ensure_pulse_thread(agent_id: str) -> dict:
    existing = find_pulse_thread(agent_id)
    if existing:
        return existing
    body = register(agent_id, ["generic"])
    return create_discussion(REPO_ID, CAT_AGENT_PULSE, f"Agent Pulse — {agent_id}", body)


def post_heartbeat(agent_id: str, status: str = "idl", load: float = 0.0):
    thread = ensure_pulse_thread(agent_id)
    body = heartbeat(agent_id, status, load)
    return add_comment(thread["id"], body)


if __name__ == "__main__":
    print("=== Swarm Client v3 — Rogue Resistant ===")
    print("Usa: post_heartbeat(), task_new(), task_claim(), ecc.")
