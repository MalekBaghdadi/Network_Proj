# Main request handler logic
# Handles HTTP and HTTPS tunnels

import socket
import threading
from constants import BUFFER_SIZE
from filter import is_blocked, blocked_response
from logger import log_request, log_response, log_error, logger
from cache import get as cache_get, store as cache_store, MAX_ENTRY_BYTES


def parse_request(raw_request):
    """
    Parses raw bytes from client into method, url, host, etc.
    """
    try:
        # separate headers from body
        header_section = raw_request.split(b'\r\n\r\n')[0]
        lines = header_section.decode('utf-8', errors='replace').split('\r\n')

        # First line: e.g. "GET http://example.com/page HTTP/1.1"
        first_line = lines[0]
        parts = first_line.split(' ')
        method = parts[0]   # GET, POST, CONNECT, etc.
        url    = parts[1]   # full URL or path
        # parts[2] is the HTTP version — we don't need it here

        # Parse remaining lines into a headers dict
        headers = {}
        for line in lines[1:]:
            if ':' in line:
                key, _, value = line.partition(':')
                headers[key.strip().lower()] = value.strip()

        # Extract host and port
        host = headers.get('host', '')
        port = 80  # default HTTP port

        # For CONNECT (HTTPS): url looks like "example.com:443"
        if method == 'CONNECT':
            host, port = url.split(':')
            port = int(port)
        # For regular HTTP with absolute URL: "http://example.com/path"
        elif '://' in url:
            without_scheme = url.split('://')[1]
            host_part = without_scheme.split('/')[0]
            if ':' in host_part:
                host, port = host_part.split(':')
                port = int(port)
            else:
                host = host_part
        else:
            # Fallback: relative URL — host and port come from the Host header
            if ':' in host:
                host, port_str = host.split(':', 1)
                port = int(port_str)

        return method, url, host, port, headers

    except Exception as e:
        logger.error(f"parse_request failed: {e}")
        return None, None, None, None, None


def modify_headers(raw_request):
    """
    Cleans up the headers (removes proxy-specific ones) before forwarding.
    Also fixes the URL from absolute to path-only.
    """
    # Headers that must NOT be forwarded to the target server
    hop_by_hop = [
        'proxy-connection',
        'proxy-authenticate',
        'proxy-authorization',
        'connection',
        'keep-alive',
        'te',
        'trailers',
        'transfer-encoding',
        'upgrade',
    ]

    try:
        header_part, _, body = raw_request.partition(b'\r\n\r\n')
        lines = header_part.decode('utf-8', errors='replace').split('\r\n')

        # Rewrite absolute URL to path-only in the request line
        # e.g. "GET http://example.com/path?q=1 HTTP/1.1" → "GET /path?q=1 HTTP/1.1"
        first_line_parts = lines[0].split(' ')
        if len(first_line_parts) == 3:
            req_method, req_url, req_version = first_line_parts
            if '://' in req_url:
                without_scheme = req_url.split('://')[1]
                slash_idx = without_scheme.find('/')
                path_only = without_scheme[slash_idx:] if slash_idx != -1 else '/'
                lines[0] = f"{req_method} {path_only} {req_version}"

        clean_lines = []
        for line in lines:
            # Keep the first line (e.g. GET /path HTTP/1.1) always
            if not line or ':' not in line:
                clean_lines.append(line)
                continue
            header_name = line.split(':')[0].strip().lower()
            if header_name not in hop_by_hop:
                clean_lines.append(line)

        # Add Connection: close so server closes after response
        clean_lines.append('Connection: close')

        cleaned = '\r\n'.join(clean_lines).encode('utf-8')
        return cleaned + b'\r\n\r\n' + body

    except Exception as e:
        logger.error(f"modify_headers failed: {e}")
        return raw_request


def _parse_status_code(response_bytes: bytes) -> int | None:
    """Gets the status code from the first line of the response."""
    try:
        first_line = response_bytes.split(b'\r\n', 1)[0].decode('utf-8', errors='replace')
        # e.g. "HTTP/1.1 200 OK"
        return int(first_line.split(' ')[1])
    except Exception:
        return None


def forward_http(client_socket, host, port, request, client_ip, client_port, method, url):
    """
    Main loop for HTTP: connects to server, sends request, relays back to client.
    Also handles caching if it's a GET.
    """
    server_socket = None          # ensure it is defined for the finally block
    status_code   = None
    first_chunk   = True

    # Cache stuff
    accumulate      = (method == 'GET')
    response_buffer = bytearray() if accumulate else None
    skip_cache      = False

    try:
        # Open a socket to the target server
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.settimeout(10)  # 10 second timeout
        server_socket.connect((host, port))

        # Send the cleaned request
        server_socket.sendall(request)

        # Read and relay the response in chunks
        while True:
            data = server_socket.recv(BUFFER_SIZE)
            if not data:
                break

            # Parse the status code from the very first response chunk
            if first_chunk:
                status_code = _parse_status_code(data)
                first_chunk = False

            client_socket.sendall(data)

            # Cache accumulation
            if accumulate and not skip_cache:
                response_buffer.extend(data)
                if len(response_buffer) > MAX_ENTRY_BYTES:
                    # Response is too big to cache — free the memory and give up.
                    skip_cache      = True
                    response_buffer = None

        # Log the completed response
        log_response(client_ip, client_port, method, url, host, port, status_code)

        # Store in cache if we have a complete GET + 2xx response
        # cache_store() silently enforces its own policy (status code, headers,
        # size, Cache-Control), so we can call it unconditionally here.
        if accumulate and not skip_cache and response_buffer is not None:
            cache_store(host, url, method, status_code, bytes(response_buffer))

    except socket.timeout:
        msg = f"Connection to {host}:{port} timed out"
        print(f"[!] {msg}")
        log_error(client_ip, client_port, "forward_http", msg)

    except Exception as e:
        print(f"[!] HTTP forwarding error: {e}")
        log_error(client_ip, client_port, "forward_http", e)

    finally:
        if server_socket:
            server_socket.close()


def tunnel_https(client_socket, host, port, client_ip, client_port, url):
    """
    MITM for HTTPS. Creates fake certs and reads the plaintext before re-encrypting.
    """
    server_socket = None
    secure_client_socket = None
    secure_server_socket = None

    try:
        from mitm import get_server_context, get_client_context
        # Tell the client the tunnel is open
        client_socket.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')

        # Log the successful tunnel establishment (no HTTP status code)
        log_response(client_ip, client_port, "CONNECT", url, host, port, status_code=None)

        # Upgrade client socket to SSL using fake certificate
        try:
            server_context = get_server_context(host)
            secure_client_socket = server_context.wrap_socket(client_socket, server_side=True)
        except Exception as ssl_err:
            print(f"[!] SSL wrap failed for client {host}: {ssl_err}")
            return

        # Connect to the real target server
        import socket
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.settimeout(10)
        server_socket.connect((host, port))

        # Upgrade server socket to SSL
        client_context = get_client_context()
        secure_server_socket = client_context.wrap_socket(server_socket, server_hostname=host)

        # Now pipe data in both directions simultaneously using two threads
        def pipe(src, dst, label):
            try:
                while True:
                    data = src.recv(BUFFER_SIZE)
                    if not data:
                        break
                    
                    # Read/log plaintext content!
                    if label == "Client->Server" and len(data) > 0:
                        print(f"\n[MITM] Plaintext Request to {host}:")
                        print(data.decode('utf-8', errors='replace').split('\r\n')[0])
                    
                    dst.sendall(data)
            except Exception:
                pass

        t1 = threading.Thread(target=pipe, args=(secure_client_socket, secure_server_socket, "Client->Server"))
        t2 = threading.Thread(target=pipe, args=(secure_server_socket, secure_client_socket, "Server->Client"))
        t1.daemon = True
        t2.daemon = True
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    except Exception as e:
        print(f"[!] HTTPS MITM error: {e}")
        log_error(client_ip, client_port, "tunnel_https", e)

    finally:
        if secure_server_socket:
            secure_server_socket.close()
        elif server_socket:
            server_socket.close()


def handle_client(client_socket, client_address):
    """Decision point: is it HTTP or HTTPS? Blocked or Cached?"""
    client_ip, client_port = client_address   # unpack once for reuse

    try:
        # Read the client's request
        raw_request = client_socket.recv(BUFFER_SIZE)
        if not raw_request:
            return

        # Parse the request
        method, url, host, port, headers = parse_request(raw_request)
        if not method:
            return

        print(f"[>] {method} {url} → {host}:{port}")

        # Log every incoming request immediately after parsing
        log_request(client_ip, client_port, method, url, host, port)

        # Check the request against the blacklist / whitelist before forwarding
        if is_blocked(host, client_ip, client_port, url):
            client_socket.sendall(blocked_response())
            return

        # Cache lookup
        # Check the cache BEFORE opening a socket to the target server.
        # On a fresh hit, serve the stored bytes directly and skip forwarding.
        # cache_get() is method-aware — non-GET requests return None immediately.
        if method == 'GET':
            cached_bytes = cache_get(host, url, method)
            if cached_bytes is not None:
                client_socket.sendall(cached_bytes)
                # Extract the stored status code so the log line still shows e.g. 200.
                cached_status = _parse_status_code(cached_bytes)
                log_response(client_ip, client_port, method, url,
                             host, port, cached_status)
                return

        # Route based on method
        if method == 'CONNECT':
            # HTTPS tunnel — don't modify the request, just tunnel
            tunnel_https(client_socket, host, port, client_ip, client_port, url)
        else:
            # HTTP — clean headers and forward
            clean_request = modify_headers(raw_request)
            forward_http(client_socket, host, port, clean_request,
                         client_ip, client_port, method, url)

    except Exception as e:
        print(f"[!] handle_client error: {e}")
        log_error(client_ip, client_port, "handle_client", e)

    finally:
        client_socket.close()
