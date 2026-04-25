# SecureWatch Proxy Project

Group project for the Computer Networks course at LAU.

## What this project is

This project is a local HTTP/HTTPS proxy with:
- request logging
- blacklist/whitelist filtering
- response caching
- HTTPS interception support (MITM for testing/education)
- a desktop control panel for monitoring and testing

## Project files (simple explanation)

- `proxy.py`  
  Main server entry point. Opens the proxy socket on `0.0.0.0:8888`, accepts clients, and starts one thread per connection.

- `handler.py`  
  Core request handler. Parses client requests, checks filters, serves cache hits, forwards HTTP traffic, and handles HTTPS `CONNECT` flows.

- `filter.py`  
  Rule engine for allow/block logic. Reads `rules.json` (auto-reloads when modified) and decides if a host should be blocked.

- `cache.py`  
  In-memory cache for HTTP `GET` responses. Supports TTL expiration, hit/miss stats, and LRU-style eviction under memory limits.

- `logger.py`  
  Central logging module. Writes structured logs to `proxy.log` and prints key events to the terminal.

- `mitm.py`  
  Certificate helper for HTTPS interception. Generates a local root CA and per-domain certificates for TLS wrapping during `CONNECT`.

- `control_panel.py`  
  PyQt GUI to start/stop proxy, manage rules, inspect logs/cache, and send test requests through the proxy.

## Requirements

- Python 3.10+ (recommended)
- Packages:
  - `PyQt5`
  - `cryptography`

Install dependencies:

```bash
pip install PyQt5 cryptography
```

## How to run the proxy

From the project folder:

```bash
python proxy.py
```

Expected output:
- `[*] SecureWatch Proxy listening on 0.0.0.0:8888`

The proxy listens on:
- Host: `127.0.0.1` (local client side)
- Port: `8888`

## How to run the control panel

From the project folder:

```bash
python control_panel.py
```

Inside the panel:
- Use **Start Proxy** / **Stop Proxy** from the Dashboard.
- Use **Request Lab** to send GET/POST through the proxy.
- Use **Rules** to switch between blacklist and whitelist mode.
- Use **Logs** and **Cache** pages to inspect behavior live.

## Tester curl commands

Use these while proxy is running on `127.0.0.1:8888`.

### Basic HTTP

```bash
curl -x http://127.0.0.1:8888 http://example.com -v
curl -x http://127.0.0.1:8888 http://httpbin.org/get -v
```

### POST test

```bash
curl -x http://127.0.0.1:8888 -X POST http://httpbin.org/post -d "name=test" -v
```

### HTTPS / CONNECT test

```bash
curl -x http://127.0.0.1:8888 https://example.com -k -v
curl -x http://127.0.0.1:8888 https://httpbin.org/get -k -v
```

### Cache behavior test (repeat same GET)

```bash
curl -x http://127.0.0.1:8888 http://httpbin.org/cache/60 -v
curl -x http://127.0.0.1:8888 http://httpbin.org/cache/60 -v
```

Check `proxy.log` for `CACHE MISS` then `CACHE HIT`.

### Filter/block test

1. Add `example.com` to blocked domains in `rules.json` (or from Control Panel Rules page).
2. Run:

```bash
curl -x http://127.0.0.1:8888 http://example.com -v
```

Expected result:
- HTTP `403 Forbidden`
- A `BLOCKED` entry in `proxy.log`.

## Logging, caching, MITM, proxy, handler, filter (quick overview)

- **Logging**  
  Every request/response/error/cache event is recorded in `proxy.log` using structured lines (`REQUEST`, `RESPONSE`, `ERROR`, `CACHE HIT/MISS`, `BLOCKED`).

- **Caching**  
  Only cacheable HTTP `GET` responses are stored. Cache is in-memory with TTL expiry and size limits to keep proxy performance stable.

- **MITM**  
  For HTTPS `CONNECT`, the proxy can terminate TLS on both sides using generated certificates, enabling inspection/relay during testing.

- **Proxy**  
  The proxy server accepts browser/tool traffic and routes each client connection to `handler.py` logic in separate threads.

- **Handler**  
  This is the traffic workflow controller: parse request -> check rules -> check cache -> forward HTTP or tunnel HTTPS -> log result.

- **Filter**  
  Applies `blacklist`/`whitelist` policy from `rules.json` before forwarding, and returns a custom 403 response for blocked targets.
