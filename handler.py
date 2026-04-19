# Author: Malek Baghdadi
# Description: Per-connection logic. Parses incoming HTTP requests, strips
#              proxy-specific headers, and forwards traffic between the client
#              and the target server. Handles both HTTP (GET/POST forwarding)
#              and HTTPS (raw TCP tunnel via CONNECT method).
# Author: Nakhoul Nehra | Logging integrated via logger.py

import socket
import threading
from constants import BUFFER_SIZE
from filter import is_blocked, blocked_response
from logger import log_request, log_response, log_error, logger


def parse_request(raw_request):
    # Author: Malek Baghdadi
    """
    Parse a raw HTTP request into its components.
    Returns: (method, url, host, port, headers_dict)
    """
    try:
        # Split header section from body
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
    # Author: Malek Baghdadi
    """
    Remove proxy-specific headers from the request before forwarding.
    Rewrites the request line to use a path-only URL (strips scheme and host).
    Returns the cleaned request as bytes.
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


# Author: Nakhoul Nehra
def _parse_status_code(response_bytes: bytes) -> int | None:
    """
    Called inside forward_http() to extract the HTTP status code
    from the first response chunk.
    e.g. b'HTTP/1.1 200 OK\\r\\n...' → 200
    Returns None if parsing fails (e.g. for binary/partial data).
    """
    try:
        first_line = response_bytes.split(b'\r\n', 1)[0].decode('utf-8', errors='replace')
        # "HTTP/1.1 200 OK"  →  ["HTTP/1.1", "200", "OK"]
        return int(first_line.split(' ')[1])
    except Exception:
        return None


def forward_http(client_socket, host, port, request, client_ip, client_port, method, url):
    # Author: Malek Baghdadi | Logging integrated by: Nakhoul Nehra
    """
    Forward an HTTP request to the target server and relay the response
    back to the client.
    """
    server_socket = None          # ensure it is defined for the finally block
    status_code   = None
    first_chunk   = True

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

        # Log the completed response
        log_response(client_ip, client_port, method, url, host, port, status_code)

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
    # Author: Malek Baghdadi | Logging integrated by: Nakhoul Nehra
    """
    Create a raw TCP tunnel for HTTPS traffic (no decryption).
    Tells the client the tunnel is ready, then pipes data both ways.
    """
    server_socket = None   # ensure defined for finally block

    try:
        # Connect to the real target server
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.settimeout(10)
        server_socket.connect((host, port))

        # Tell the client the tunnel is open
        client_socket.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')

        # Log the successful tunnel establishment (no HTTP status code)
        log_response(client_ip, client_port, "CONNECT", url, host, port, status_code=None)

        # Now pipe data in both directions simultaneously using two threads
        def pipe(src, dst):
            try:
                while True:
                    data = src.recv(BUFFER_SIZE)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass

        t1 = threading.Thread(target=pipe, args=(client_socket, server_socket))
        t2 = threading.Thread(target=pipe, args=(server_socket, client_socket))
        t1.daemon = True
        t2.daemon = True
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    except Exception as e:
        print(f"[!] HTTPS tunnel error: {e}")
        log_error(client_ip, client_port, "tunnel_https", e)

    finally:
        if server_socket:
            server_socket.close()


def handle_client(client_socket, client_address):
    # Author: Malek Baghdadi | Logging integrated by: Nakhoul Nehra
    """
    Entry point for each client thread.
    Reads the request, decides if it's HTTP or HTTPS, and routes accordingly.
    """
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
