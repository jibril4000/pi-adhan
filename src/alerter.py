"""Send alert emails via the Resend HTTP API (stdlib only, no extra deps)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger("adhan.alerter")

RESEND_ENDPOINT = "https://api.resend.com/emails"
SEND_TIMEOUT = 15  # seconds


def send_email(
    api_key: str,
    sender: str,
    recipients: list[str],
    subject: str,
    body: str,
) -> tuple[bool, str]:
    """Send a plain-text email through Resend.

    Returns (ok, error_message). Never raises — a failed alert must not crash
    the caller (the health checker), so all errors are returned, not thrown.
    """
    if not api_key:
        return False, "no Resend API key configured"
    if not recipients:
        return False, "no recipients configured"

    payload = json.dumps({
        "from": sender,
        "to": recipients,
        "subject": subject,
        "text": body,
    }).encode()

    req = urllib.request.Request(
        RESEND_ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Resend's edge (Cloudflare) 403s the default Python-urllib UA as a
            # suspected bot; a normal UA gets through.
            "User-Agent": "adhan-healthcheck/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=SEND_TIMEOUT) as resp:
            if 200 <= resp.status < 300:
                return True, ""
            return False, f"Resend returned HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:300]
        except Exception:
            pass
        return False, f"Resend HTTP {e.code}: {detail}"
    except urllib.error.URLError as e:
        return False, f"network error reaching Resend: {e.reason}"
    except Exception as e:  # noqa: BLE001 — alerting must never raise
        return False, f"unexpected error: {e}"
