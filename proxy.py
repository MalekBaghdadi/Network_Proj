# Author: Malek Baghdadi
# Role: Main entry point. Sets up the TCP socket server and dispatches one thread per client connection.

import socket
import threading
import sys
from handler import handle_client
from constants import BUFFER_SIZE
from mitm import generate_ca

HOST = '0.0.0.0'    # listen on all network interfaces
PORT = 8888         # clients will point their browser to this port


active_connections = 0
connections_lock = threading.Lock()

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
    generate_ca()  # Generate CA cert on startup if not already present

    # Create a TCP socket (IPv4)
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Allow port reuse immediately after restart (avoids "Address in use" error)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Bind to host and port
    server_socket.bind((HOST, PORT))

    # Start listening — queue up to 50 pending connections
    server_socket.listen(50)

    print(f"[*] SecureWatch Proxy listening on {HOST}:{PORT}")

    try:
        while True:
            # Block here until a client connects
            try:
                client_socket, client_address = server_socket.accept()
            except socket.timeout:
                continue
            except KeyboardInterrupt:
                break

            print(f"[+] New connection from {client_address[0]}:{client_address[1]}")

            # Spin up a thread for this client — don't block the main loop
            thread = threading.Thread(
                target=tracked_handle,
                args=(client_socket, client_address)
            )
            thread.daemon = True   # thread dies when main program exits
            thread.start()
    finally:
        server_socket.close()



if __name__ == '__main__':
    try:
        start_server()
    except KeyboardInterrupt:
        print("\n[*] Proxy shutting down.")
        sys.exit(0)