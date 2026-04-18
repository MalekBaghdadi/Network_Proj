# Author: Malek Baghdadi
# Description: Per-connection logic. Parses incoming HTTP requests, strips
#              proxy-specific headers, and forwards traffic between the client
#              and the target server. Handles both HTTP (GET/POST forwarding)
#              and HTTPS (raw TCP tunnel via CONNECT method).
#
# Author Nakhoul Nehra: Integrated logging calls via logger.py 
#   - log_request()  called once the request is parsed successfully.
#   - log_response() called after the full response is relayed to the client.
#   - log_error()    called inside every except block in place of bare print().

import socket
import threading
from constants import BUFFER_SIZE
from logger import log_request, log_response, log_error  

# BUFFER_SIZE imported from constants.py


def parse_request(raw_request):
    """
    Parse a raw HTTP request into its components.
    Returns: (method, url, host, port, headers_dict, raw_request)
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
        # parts[2] is the HTTP version — we don't need it for now

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
        # For regular HTTP: url looks like "http://example.com/path"
        elif '://' in url:
            without_scheme = url.split('://')[1]
            host_part = without_scheme.split('/')[0]
            if ':' in host_part:
                host, port = host_part.split(':')
                port = int(port)
            else:
                host = host_part

        return method, url, host, port, headers

    except Exception as e:
        print(f"[!] Failed to parse request: {e}")
        return None, None, None, None, None


def modify_headers(raw_request):
    """
    Remove proxy-specific headers from the request before forwarding.
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

        clean_lines = []
        for line in lines:
            # Keep the first line (GET /path HTTP/1.1) always
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
        print(f"[!] Header modification failed: {e}")
        return raw_request


def _parse_status_code(response_bytes: bytes) -> int | None:
    """
    Extract the HTTP status code from the first line of a response.
    e.g. b'HTTP/1.1 200 OK\\r\\n...' → 200
    Returns None if parsing fails (e.g. for binary/partial data).
    
    Author [Member 2]: Helper added for logging response status codesS.
    """
    try:
        first_line = response_bytes.split(b'\r\n', 1)[0].decode('utf-8', errors='replace')
        # "HTTP/1.1 200 OK"  →  ["HTTP/1.1", "200", "OK"]
        return int(first_line.split(' ')[1])
    except Exception:
        return None


def forward_http(client_socket, host, port, request, client_ip, client_port, method, url):
    """
    Forward an HTTP request to the target server and relay the response
    back to the client.

    Author [Member 2]: Added client_ip, client_port, method, url parameters
                       so we can call log_response() / log_error().
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
        log_response(client_ip, client_port, method, url, status_code)

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
    Create a raw TCP tunnel for HTTPS traffic (no decryption).
    Tells the client the tunnel is ready, then pipes data both ways.

    Author [Member 2]: Added client_ip, client_port, url parameters
                       for logging.
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
        log_response(client_ip, client_port, "CONNECT", url, status_code=None)

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