# Logging middleware for the proxy

import logging
import os

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy.log")

logger = logging.getLogger("ProxyLogger")
logger.setLevel(logging.DEBUG)          # capture everything (DEBUG and above)

# Prevent duplicate handlers if this module is imported more than once
if not logger.handlers:

    # File handler — writes every record to proxy.log
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)

    # Console handler — shows INFO+ in the terminal
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # Shared formatter: timestamp | level | message
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
    """Logs an incoming request."""
    logger.info(
        f"REQUEST  | client={client_ip}:{client_port} | "
        f"{method} {url} | target={target_host}:{target_port}"
    )


def log_response(client_ip: str, client_port: int,
                 method: str, url: str,
                 target_host: str, target_port: int,
                 status_code: int | None) -> None:
    """Logs the final response status."""
    code_str = str(status_code) if status_code is not None else "TUNNEL"
    logger.info(
        f"RESPONSE | client={client_ip}:{client_port} | "
        f"{method} {url} | target={target_host}:{target_port} | status={code_str}"
    )


def log_error(client_ip: str, client_port: int,
              context: str, error: Exception | str) -> None:
    """Logs errors to proxy.log."""
    logger.error(
        f"ERROR    | client={client_ip}:{client_port} | "
        f"context={context} | {error}"
    )


def log_cache_hit(url: str) -> None:
    """Log when a response is served from cache (used by caching layer)."""
    logger.info(f"CACHE HIT  | {url}")


def log_cache_miss(url: str) -> None:
    """Log when a cache miss forces a fresh fetch (used by caching layer)."""
    logger.info(f"CACHE MISS | {url}")


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
