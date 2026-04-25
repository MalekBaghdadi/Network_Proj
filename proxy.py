# Author: Malek Baghdadi
# Role: Main entry point. Sets up the TCP socket server and dispatches one thread per client connection.
# Author: Nakhoul Nehra | Admin control support added for PyQt control panel.

import socket
import threading
import sys
from handler import handle_client
from mitm import generate_ca

HOST = '0.0.0.0'
PORT = 8888

active_connections = 0
connections_lock = threading.Lock()

server_socket = None
server_running = False
server_lock = threading.Lock()


def tracked_handle(client_socket, client_address):
    global active_connections

    with connections_lock:
        active_connections += 1
        print(f"[*] Active connections: {active_connections}")

    try:
        handle_client(client_socket, client_address)
    finally:
        with connections_lock:
            active_connections -= 1
            print(f"[*] Connection closed. Active connections: {active_connections}")


def start_server():
    global server_socket, server_running

    with server_lock:
        if server_running:
            print("[*] Proxy is already running.")
            return
        server_running = True

    generate_ca()

    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((HOST, PORT))
        server_socket.listen(50)
        server_socket.settimeout(1)

        print(f"[*] SecureWatch Proxy listening on {HOST}:{PORT}")

        while server_running:
            try:
                client_socket, client_address = server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            print(f"[+] New connection from {client_address[0]}:{client_address[1]}")

            thread = threading.Thread(
                target=tracked_handle,
                args=(client_socket, client_address)
            )
            thread.daemon = True
            thread.start()

    except Exception as e:
        print(f"[!] Proxy server error: {e}")

    finally:
        with server_lock:
            server_running = False

        if server_socket:
            try:
                server_socket.close()
            except Exception:
                pass

        print("[*] Proxy stopped.")


def stop_server():
    global server_socket, server_running

    with server_lock:
        server_running = False

    if server_socket:
        try:
            server_socket.close()
        except Exception:
            pass


def is_running():
    return server_running


if __name__ == '__main__':
    try:
        start_server()
    except KeyboardInterrupt:
        stop_server()
        print("\n[*] Proxy shutting down.")
        sys.exit(0)