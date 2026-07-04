# nova_memory_store.py
# Persistent conversation history across Nova sessions
# Saves/loads last N exchanges to C:\Nova\history.json

import json
import os
from datetime import datetime

HISTORY_PATH = "C:/Nova/history.json"
MAX_EXCHANGES = 20  # each exchange = 1 user + 1 assistant message = 2 entries

def load_history() -> list[dict]:
    """Load conversation history from disk. Returns empty list if none exists."""
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("messages", [])
    except Exception:
        return []

def save_history(messages: list[dict]) -> None:
    """Save conversation history to disk, keeping only the last MAX_EXCHANGES exchanges."""
    # Each exchange is 2 messages (user + assistant), so keep last MAX_EXCHANGES * 2
    trimmed = messages[-(MAX_EXCHANGES * 2):]
    payload = {
        "last_updated": datetime.now().isoformat(),
        "exchange_count": len(trimmed) // 2,
        "messages": trimmed
    }
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

def clear_history() -> None:
    """Delete the history file."""
    if os.path.exists(HISTORY_PATH):
        os.remove(HISTORY_PATH)
