# Author: Nakhoul Nehra
# Description: Logging middleware for the proxy server.
#              Uses Python's built-in logging module to write structured
#              per-request records to proxy.log and to the console.

import logging
import os
from datetime import datetime

# ── Log file lives next to the running script ────────────────────────────────
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy.log")

# ── Module-level logger ───────────────────────────────────────────────────────
logger = logging.getLogger("ProxyLogger")
logger.setLevel(logging.DEBUG)          # capture everything (DEBUG and above)

# Prevent duplicate handlers if this module is imported more than once
if not logger.handlers:

    # ── File handler — writes every record to proxy.log 
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)

    # ── Console handler — shows INFO+ in the terminal
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # ── Shared formatter: timestamp | level | message
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


# Public helpers called from handler.py

def log_request(client_ip: str, client_port: int,
                method: str, url: str,
                target_host: str, target_port: int) -> None:
    """
    Log an incoming client request before it is forwarded.

    Args:
        client_ip    : IP address of the connecting client.
        client_port  : Source port of the connecting client.
        method       : HTTP method (GET, POST, CONNECT, …).
        url          : Full URL or CONNECT authority (host:port).
        target_host  : Resolved target hostname.
        target_port  : Resolved target port number.
    """
    logger.info(
        f"REQUEST  | client={client_ip}:{client_port} | "
        f"{method} {url} | target={target_host}:{target_port}"
    )


def log_response(client_ip: str, client_port: int,
                 method: str, url: str,
                 status_code: int | None) -> None:
    """
    Log the outcome of a forwarded request once the response is received.

    Args:
        client_ip   : IP address of the connecting client.
        client_port : Source port of the connecting client.
        method      : HTTP method used.
        url         : Full URL that was requested.
        status_code : HTTP status code returned by the target server,
                      or None for raw HTTPS tunnels (CONNECT).
    """
    code_str = str(status_code) if status_code is not None else "TUNNEL"
    logger.info(
        f"RESPONSE | client={client_ip}:{client_port} | "
        f"{method} {url} | status={code_str}"
    )


def log_error(client_ip: str, client_port: int,
              context: str, error: Exception | str) -> None:
    """
    Log an error that occurred while processing a request.

    Args:
        client_ip   : IP address of the connecting client.
        client_port : Source port of the connecting client.
        context     : Short label describing where the error occurred
                      (e.g. "forward_http", "parse_request").
        error       : The exception object or error string.
    """
    logger.error(
        f"ERROR    | client={client_ip}:{client_port} | "
        f"context={context} | {error}"
    )


def log_cache_hit(url: str) -> None:
    """Log when a response is served from cache (used by caching layer)."""
    logger.debug(f"CACHE HIT  | {url}")


def log_cache_miss(url: str) -> None:
    """Log when a cache miss forces a fresh fetch (used by caching layer)."""
    logger.debug(f"CACHE MISS | {url}")


def log_blocked(client_ip: str, client_port: int, url: str) -> None:
    """Log when a request is blocked by the blacklist (used by filter layer)."""
    logger.warning(
        f"BLOCKED  | client={client_ip}:{client_port} | {url}"
    )


#testing 


"""

# 1. Normal HTTP GET request → tests REQUEST + RESPONSE (status=200)
curl -x http://127.0.0.1:8888 http://example.com -v

# 2. Another HTTP GET with different site → tests repeated request logging
curl -x http://127.0.0.1:8888 http://httpbin.org/get -v

# 3. HTTP POST request → tests POST parsing + REQUEST + RESPONSE
curl -x http://127.0.0.1:8888 -X POST http://httpbin.org/post -d "name=test" -v

# 4. HTTPS request using CONNECT tunnel → tests CONNECT + TUNNEL response
curl -x http://127.0.0.1:8888 https://example.com -k -v

# 5. HTTPS request to another site → tests more CONNECT tunnel logging
curl -x http://127.0.0.1:8888 https://httpbin.org/get -k -v

# 6. Force timeout/error using invalid port → tests ERROR log (forward_http)
curl -x http://127.0.0.1:8888 http://example.com:9999 -v

# 7. Force DNS failure → tests ERROR log (forward_http / handle_client)
curl -x http://127.0.0.1:8888 http://this-domain-should-not-exist-123456.com -v

# 8. Malformed request simulation → tests parse failure / handle_client error
printf "BADREQUEST\r\n\r\n" | nc 127.0.0.1 8888


"""