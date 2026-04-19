# Author: Nakhoul Nehra
# Description: Blacklist / whitelist filtering for the proxy server.
#              Loads domain and IP rules from rules.json and checks each
#              request before it is forwarded to the target server.
#              Blocked requests are rejected with a custom 403 Forbidden
#              HTML response. The rules file is reloaded automatically
#              whenever it changes on disk — no proxy restart needed.

import json
import os
import threading

from logger import log_blocked

# ── Path to the rules file (same directory as this script) ───────────────────
RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.json")

# ── In-memory copy of the loaded rules and a lock for thread safety ──────────
_rules      = {"mode": "blacklist", "blocked": [], "allowed": []}
_rules_lock = threading.Lock()

# ── Track the last modification time so we only reload when the file changes ─
_last_mtime = 0.0


def _load_rules() -> None:
    """
    Read rules.json from disk and update the in-memory _rules dict.
    Called internally whenever the file modification time has changed.
    """
    global _rules

    with open(RULES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    with _rules_lock:
        _rules = data


def _reload_if_changed() -> None:
    """
    Compare the current modification time of rules.json with the last
    known value. If the file has been updated, reload it.
    This gives live reloading without restarting the proxy.
    """
    global _last_mtime

    try:
        mtime = os.path.getmtime(RULES_FILE)
        if mtime != _last_mtime:
            _load_rules()
            _last_mtime = mtime
    except Exception as e:
        # If the file is missing or unreadable, keep the existing rules
        print(f"[!] Could not reload rules.json: {e}")


def _matches(host: str, entry: str) -> bool:
    """
    Check whether a host matches a single rules entry.
    Supports exact matches (e.g. 'ads.example.com') and
    domain-suffix matches (e.g. 'example.com' also blocks 'sub.example.com').
    """
    host  = host.lower()
    entry = entry.lower()
    return host == entry or host.endswith("." + entry)


def is_blocked(host: str, client_ip: str, client_port: int, url: str) -> bool:
    """
    Return True if the request should be blocked, False if it should pass.

    Behaviour depends on the active mode in rules.json:
      - "blacklist" (default): block any host that appears in 'blocked'.
      - "whitelist"           : block any host that does NOT appear in 'allowed'.

    Also calls log_blocked() so the logger records the event.

    Args:
        host        : Target hostname or IP (e.g. 'ads.example.com').
        client_ip   : Client IP address (for logging).
        client_port : Client source port (for logging).
        url         : Full URL of the request (for logging).
    """
    # Check for an updated rules file before every decision
    _reload_if_changed()

    with _rules_lock:
        mode    = _rules.get("mode", "blacklist")
        blocked = _rules.get("blocked", [])
        allowed = _rules.get("allowed", [])

    if mode == "whitelist":
        # Allow only hosts that appear in the allowed list
        permitted = any(_matches(host, entry) for entry in allowed)
        if not permitted:
            log_blocked(client_ip, client_port, url)
            return True

    else:
        # Blacklist mode: block hosts that appear in the blocked list
        if any(_matches(host, entry) for entry in blocked):
            log_blocked(client_ip, client_port, url)
            return True

    return False


def blocked_response() -> bytes:
    """
    Build and return a complete HTTP 403 Forbidden response with a simple
    HTML body. This is sent directly to the client when a request is blocked.
    """
    body = (
        "<html><body>"
        "<h1>403 Forbidden</h1>"
        "<p>This resource has been blocked by the proxy.</p>"
        "</body></html>"
    )
    body_bytes = body.encode("utf-8")

    response = (
        f"HTTP/1.1 403 Forbidden\r\n"
        f"Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("utf-8") + body_bytes

    return response