#!/usr/bin/env python3
"""
cleanup_test_board.py
Rimuove le discussioni di test create durante lo sviluppo.

Uso:
    python cleanup_test_board.py           # chiede conferma
    python cleanup_test_board.py --force   # cancella senza chiedere
"""

import os
import sys
import time

# Importa dal client
from swarm_client import (
    GITHUB_TOKEN, REPO_OWNER, REPO_NAME,
    graphql, list_discussions,
    CAT_AGENT_PULSE, CAT_TASK_BOARD, CAT_SWARM_CONTROL
)

# Pattern che identificano discussioni di test
PATTERNS = [
    "Agent Pulse — test-",
    "Agent Pulse - test-",
    "[OPEN] Test",
    "[CLAIMED] test-",
    "[IN_PROGRESS] test-",
    "[DONE] test-",
    "[STALE] test-",
    "Test arbiter conflict",
    "Guardian Alert",
]

if not GITHUB_TOKEN:
    print("ERRORE: GITHUB_TOKEN non impostato")
    sys.exit(1)


def delete_discussion(did: str):
    m = """
    mutation($input: DeleteDiscussionInput!) {
      deleteDiscussion(input: $input) { clientMutationId }
    }
    """
    graphql(m, {"input": {"id": did}})


print("Scansione discussioni di test...")
all_discussions = []
all_discussions += list_discussions(category_id=CAT_AGENT_PULSE, first=100)
all_discussions += list_discussions(category_id=CAT_TASK_BOARD, first=100)
all_discussions += list_discussions(category_id=CAT_SWARM_CONTROL, first=100)

to_delete = []
for d in all_discussions:
    for pat in PATTERNS:
        if pat in d["title"]:
            to_delete.append(d)
            break

if not to_delete:
    print("Nessuna discussione di test trovata.")
    sys.exit(0)

print(f"Trovate {len(to_delete)} discussioni di test:")
for d in to_delete:
    print(f"  #{d['number']}: {d['title']}")

if "--force" not in sys.argv:
    ans = input("Scrivi 'delete' per confermare la cancellazione: ").strip().lower()
    if ans != "delete":
        print("Cancellazione annullata.")
        sys.exit(0)

print("\nCancellazione in corso...")
for d in to_delete:
    print(f"  Cancellazione #{d['number']}... ", end="", flush=True)
    try:
        delete_discussion(d["id"])
        print("OK")
    except Exception as e:
        print(f"ERRORE ({e})")
    time.sleep(2)

print("\nPulizia completata!")
