#!/usr/bin/env python3
"""
translator.py
Converte messaggi compatti v3 in testo umano leggibile.
Funziona anche con messaggi v2 legacy.

Uso:
    from translator import translate_comment
    text = translate_comment(comment_body)
    print(text)
"""

import json
import re
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# TABELLE DI TRADUZIONE
# ---------------------------------------------------------------------------
TYPE_NAMES = {
    "REG": "si e registrato",
    "HB":  "heartbeat",
    "TN":  "ha creato un task",
    "TC":  "ha preso in carico il task",
    "TU":  "ha aggiornato il task",
    "TD":  "ha completato il task",
    "BC":  "broadcast",
    "ERR": "ha segnalato un errore",
}

STATUS_NAMES = {
    "idl":  "inattivo",
    "busy": "occupato",
    "err":  "in errore",
    "off":  "offline",
}

PRIORITY_NAMES = {
    "low":  "bassa priorita",
    "med":  "media priorita",
    "high": "alta priorita",
    "crit": "priorita critica",
}

# ---------------------------------------------------------------------------
# PARSER (condiviso con swarm_client)
# ---------------------------------------------------------------------------
def _parse_msg(text: str) -> dict | None:
    """Estrae un messaggio swarm da un commento."""
    # Prova blocco json
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
    # Normalizza v3
    if obj.get("v") == "3" and "t" in obj:
        return {
            "v": "3", "msg_type": obj.get("t", ""), "agent_id": obj.get("a", ""),
            "ts": obj.get("s", 0), "payload": obj.get("p", {}),
            "human_desc": obj.get("d", ""), "sig": obj.get("h", ""),
        }
    # Normalizza v2 legacy
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
# TRADUTTORE
# ---------------------------------------------------------------------------
def translate(msg: dict) -> str:
    """Converte un messaggio normalizzato in testo umano."""
    t = msg.get("msg_type", "?")
    a = msg.get("agent_id", "qualcuno")
    p = msg.get("payload", {})
    d = msg.get("human_desc", "")

    # Se c'e gia una descrizione umana, usala
    if d:
        return f"[{a}] {d}"

    verb = TYPE_NAMES.get(t, f"ha inviato {t}")
    parts = [f"[{a}] {verb}"]

    if t == "HB":
        st = STATUS_NAMES.get(p.get("st"), p.get("st", "sconosciuto"))
        ld = p.get("ld")
        parts.append(f"stato={st}")
        if ld is not None:
            parts.append(f"carico={int(ld*100)}%")

    elif t == "TN":
        pr = PRIORITY_NAMES.get(p.get("pr"), p.get("pr", ""))
        desc = p.get("desc", "")
        if pr:
            parts.append(pr)
        if desc:
            parts.append(f'"{desc}"')

    elif t == "TC":
        parts.append(f"#{p.get('tn', '?')}")

    elif t == "TU":
        parts.append(f"#{p.get('tn', '?')} al {p.get('prog', '?')}%")
        note = p.get("note", "")
        if note:
            parts.append(f"nota: {note}")

    elif t == "TD":
        parts.append(f"#{p.get('tn', '?')}")
        res = p.get("res", "")
        if res:
            parts.append(f"risultato: {res}")

    elif t in ("BC", "ERR"):
        parts.append(f'"{p.get('msg', '')}"')
        code = p.get("code")
        if code:
            parts.append(f"codice={code}")

    elif t == "REG":
        caps = p.get("cap", [])
        if caps:
            parts.append(f"capacita: {', '.join(caps)}")

    return " ".join(parts)


def translate_comment(text: str) -> str:
    """Estrae e traduce un messaggio da un commento raw."""
    msg = _parse_msg(text)
    if not msg:
        return "[nessun messaggio riconosciuto]"
    return translate(msg)


def ts_to_human(ts: int) -> str:
    """Converte timestamp unix in stringa leggibile."""
    if not ts:
        return "ora sconosciuta"
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)


# ---------------------------------------------------------------------------
# DEMO
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    examples = [
        '{"v":"3","t":"HB","a":"alpha","s":1726845600,"p":{"st":"idl","ld":0.1}}',
        '{"v":"3","t":"TC","a":"beta","s":1726845610,"p":{"tn":42}}',
        '{"v":"3","t":"TU","a":"gamma","s":1726845900,"p":{"tn":42,"prog":75,"note":"quasi finito"}}',
        '{"v":"3","t":"TN","a":"delta","s":1726846000,"p":{"pr":"high","desc":"Scrape dati NOAA"}}',
        '{"v":"3","t":"TD","a":"beta","s":1726846200,"p":{"tn":42,"res":"OK"}}',
        '{"v":"3","t":"BC","a":"alpha","s":1726846300,"p":{"msg":"Ciao sciame!"}}',
        '{"v":"3","t":"ERR","a":"gamma","s":1726846400,"p":{"msg":"API fallita","code":500}}',
    ]
    print("=== Traduttore Messaggi Swarm v3 ===\n")
    for ex in examples:
        raw = f"```json\\n{ex}\\n```"
        print(f"RAW:    {raw[:60]}...")
        print(f"UMANO:  {translate_comment(raw)}")
        print()
