# nova_notify.py
# Thin wrapper around ntfy.sh's public relay — Layer 3 of 86baykvb7's
# deferred "real push" notification layer, built for real (86bb3ceyp).
#
# ntfy.sh's public topics are NOT access-controlled: anyone who knows the
# topic string can publish to it or subscribe to it, and message content
# transits ntfy.sh's own servers in plaintext. NTFY_TOPIC must therefore be
# treated as a secret — long, random, unguessable — stored only in .env,
# never logged, never committed. Generate one with:
#   python -c "import secrets; print('nova-' + secrets.token_urlsafe(24))"
# then subscribe to the exact printed string in the ntfy phone app (no
# server URL to set, ntfy.sh is the default).

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from nova_config import is_push_notifications_enabled

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

NTFY_BASE_URL = "https://ntfy.sh"
NTFY_TIMEOUT_SECONDS = 10


def send_notification(title: str, message: str, tags: str | None = None, priority: str | None = None) -> bool:
    """
    Push a real phone notification via ntfy.sh. Best-effort, never raises —
    callers never need their own try/except, same as add_comment()'s
    callers today. Returns False (silent no-op) if push notifications are
    disabled in nova_config.json or NTFY_TOPIC isn't set in .env.
    """
    if not is_push_notifications_enabled():
        return False

    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return False

    headers = {"Title": title}
    if tags:
        headers["Tags"] = tags
    if priority:
        headers["Priority"] = priority

    try:
        response = httpx.post(
            f"{NTFY_BASE_URL}/{topic}",
            content=message.encode("utf-8"),
            headers=headers,
            timeout=NTFY_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"ntfy push failed: {e}")
        return False


if __name__ == "__main__":
    ok = send_notification(
        title="Nova test",
        message="nova_notify.py quick test — if you see this, ntfy wiring works.",
        tags="white_check_mark",
    )
    print("sent" if ok else "failed / not configured (check push_notifications.enabled and NTFY_TOPIC)")
