"""
p2p_node.py
-----------
Core peer-to-peer networking logic.

Each running peer:

  1. Acts as a TCP server.
       - Opens a listening socket on a chosen port.
       - Accepts incoming connections from other peers in a loop.
       - For every accepted connection, it spawns a NEW THREAD so that
         multiple peers can talk to us at the same time.

  2. Acts as a TCP client.
       - When the user clicks "Connect", we open an outgoing socket to
         the remote peer and start a thread for that connection too.

  3. Does the HELLO handshake.
       - As soon as a TCP connection is open, BOTH sides send a HELLO
         JSON message and reply with a HELLO_ACK JSON message. After
         that both peers know each other's name, ID, and port.

  4. Handles text and file messages.
       - Each per-connection thread runs `handle_peer`, which loops
         receiving messages and dispatches them to callbacks provided
         by the GUI.

No GUI code lives here. The GUI (main.py) gives us callbacks for:
  - log_event(text)            -> show a line in the log
  - add_peer(info)             -> add a peer to the connected list
  - remove_peer(peer_id)       -> remove a peer when it disconnects
  - on_text(peer_id, name, m)  -> incoming text message
  - on_file_meta(peer_id, ...) -> incoming file metadata; GUI returns
                                  the destination path (or None to drop)
  - on_file_done(peer_id, ...) -> called after the file bytes have
                                  been written (or the transfer failed)

This keeps the networking layer separate from Tkinter, which makes
both pieces easier to read.
"""

import os
import socket
import threading
import uuid

import protocol


# Callback placeholders. main.py will overwrite these with real
# Tkinter-safe callbacks (using root.after) so that networking threads
# never touch Tkinter widgets directly.
#
# on_file_meta(peer_id, sender_name, meta)
#   Called when an incoming FILE metadata message arrives. The GUI
#   decides where to save the file and RETURNS that destination path.
#   Returning None means "drop the file but keep the stream aligned".
#
# on_file_done(peer_id, filename, dest_path, ok)
#   Called after the bytes have actually been received (or failed).
#   The GUI uses this to show a "Saved" / "Failed" message.
callbacks = {
    "log_event": lambda msg: None,
    "add_peer": lambda info: None,
    "remove_peer": lambda peer_id: None,
    "on_text": lambda peer_id, sender_name, message: None,
    "on_file_meta": lambda peer_id, sender_name, meta: None,
    "on_file_done": lambda peer_id, filename, dest_path, ok: None,
}


class PeerInfo:
    """
    A tiny value object describing one remote peer we are connected to.

    We also keep a lock so the GUI thread and the network thread can
    safely read the same object without weird races.
    """

    def __init__(self, peer_id, peer_name, port, sock, address,
                 initiator=False):
        self.peer_id = peer_id
        self.peer_name = peer_name
        self.port = port
        self.sock = sock
        self.address = address        # (ip, port) tuple from accept/connect
        self.initiator = initiator    # True if WE connected to them
        self.lock = threading.Lock()

    def display(self):
        return f"{self.peer_name} [{self.peer_id}]"


class P2PNode:
    """
    A peer that can act as both TCP server and TCP client.

    Lifecycle:
        node = P2PNode("Alice", 5000)
        node.start_server()    # begin accepting incoming connections
        node.connect_to(ip, p) # open an outgoing connection
        node.send_text(peer_id, "hi")
        node.send_file(peer_id, "/path/to/file")
        node.stop()            # stop the server and close all sockets
    """

    def __init__(self, peer_name, port):
        # Generate a short unique ID for this peer. Used in HELLO messages
        # and to tell connections apart in the GUI.
        self.peer_id = uuid.uuid4().hex[:8]
        self.peer_name = peer_name
        self.port = port

        self.server_socket = None       # the listening socket
        self.server_thread = None       # thread running accept_loop
        self.running = False            # True while the server is up

        # peer_id -> PeerInfo. Guards every access with self.peers_lock.
        self.peers = {}
        self.peers_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Small helpers used by both networking and GUI threads
    # ------------------------------------------------------------------ #

    def log(self, msg):
        callbacks["log_event"](msg)

    def _add_peer(self, info):
        with self.peers_lock:
            self.peers[info.peer_id] = info
        callbacks["add_peer"](info)

    def _remove_peer(self, peer_id):
        with self.peers_lock:
            info = self.peers.pop(peer_id, None)
        if info is not None:
            try:
                info.sock.close()
            except OSError:
                pass
            callbacks["remove_peer"](peer_id)

    # ------------------------------------------------------------------ #
    # TCP server: bind / listen / accept / spawn thread per connection
    # ------------------------------------------------------------------ #

    def start_server(self):
        """Create the listening socket and start the accept loop."""
        # Create a TCP socket (IPv4, stream = TCP).
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Allow reusing the port quickly after we close, useful in tests.
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Bind to all interfaces so other computers on the LAN can reach us.
        self.server_socket.bind(("0.0.0.0", self.port))
        # Start listening. The kernel will queue incoming connections.
        self.server_socket.listen()

        self.running = True
        # Run accept_loop in the background so the GUI stays responsive.
        self.server_thread = threading.Thread(
            target=self._accept_loop, daemon=True
        )
        self.server_thread.start()

        self.log(f"[SERVER] Listening on 0.0.0.0:{self.port} "
                 f"(peer_id={self.peer_id})")

    def stop_server(self):
        """Stop accepting new connections and close the listening socket."""
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
            self.server_socket = None

        # Close every active peer socket too. Their threads will exit.
        with self.peers_lock:
            peer_ids = list(self.peers.keys())
        for pid in peer_ids:
            self._remove_peer(pid)

        self.log("[SERVER] Stopped")

    def _accept_loop(self):
        """
        Sit in accept() forever while the server is running.

        Each new connection is given its own thread, so the GUI and other
        peer connections keep working.
        """
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
            except OSError:
                # Socket was closed -> we are shutting down.
                break

            self.log(f"[SERVER] Incoming connection from {addr[0]}:{addr[1]}")

            # Spawn a per-connection thread. The peer is NOT registered
            # in self.peers yet — that happens after the HELLO handshake.
            t = threading.Thread(
                target=self._serve_peer, args=(conn, addr, False),
                daemon=True,
            )
            t.start()

    # ------------------------------------------------------------------ #
    # TCP client: connect to a remote peer
    # ------------------------------------------------------------------ #

    def connect_to(self, ip, port):
        """
        Open an outgoing TCP connection to another peer.

        This runs synchronously: if the remote peer is offline we will
        get ConnectionRefusedError or a timeout. Callers should wrap
        this in try/except to surface a friendly GUI error.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((ip, port))
        addr = (ip, port)
        self.log(f"[CLIENT] Connected to {ip}:{port}")

        # Run the same handler used for incoming connections.
        t = threading.Thread(
            target=self._serve_peer, args=(sock, addr, True), daemon=True
        )
        t.start()

    # ------------------------------------------------------------------ #
    # Per-connection handler: handshake + message loop
    # ------------------------------------------------------------------ #

    def _serve_peer(self, sock, address, initiator):
        """
        Runs in its own thread for each peer connection.

        1. Send our HELLO and read their HELLO.
        2. Reply with HELLO_ACK and read their HELLO_ACK.
        3. Register the peer in self.peers and notify the GUI.
        4. Loop receiving text / file messages until the peer disconnects.
        """
        try:
            # ----- HELLO HANDSHAKE ------------------------------------
            # Step 1: we send our HELLO first.
            protocol.send_message(sock, {
                "type": "hello",
                "peer_id": self.peer_id,
                "peer_name": self.peer_name,
                "port": self.port,
            })

            # Step 2: we read their HELLO.
            hello = protocol.recv_message(sock)
            if not hello or hello.get("type") != "hello":
                self.log(f"[ERROR] Handshake failed with {address}")
                sock.close()
                return

            # Step 3: we send our HELLO_ACK.
            protocol.send_message(sock, {
                "type": "hello_ack",
                "peer_id": self.peer_id,
                "peer_name": self.peer_name,
                "port": self.port,
            })

            # Step 4: we read their HELLO_ACK.
            hello_ack = protocol.recv_message(sock)
            if not hello_ack or hello_ack.get("type") != "hello_ack":
                self.log(f"[ERROR] Handshake ack failed with {address}")
                sock.close()
                return

            # We now know who this peer is.
            peer_id = hello["peer_id"]
            peer_name = hello["peer_name"]
            peer_port = hello["port"]

            info = PeerInfo(peer_id, peer_name, peer_port, sock, address,
                            initiator=initiator)
            self._add_peer(info)
            self.log(f"[OK] Handshake complete with {peer_name} [{peer_id}]")

            # ----- MESSAGE LOOP ---------------------------------------
            # The peer can now send us text or file messages. Text is a
            # framed JSON message; file is metadata JSON followed by raw
            # bytes on the same socket.
            while True:
                msg = protocol.recv_message(sock)
                if msg is None:
                    # Peer disconnected cleanly.
                    break

                mtype = msg.get("type")
                if mtype == "text":
                    callbacks["on_text"](
                        peer_id,
                        msg.get("sender_name", peer_name),
                        msg.get("message", ""),
                    )
                elif mtype == "file":
                    # After this JSON message, the next `filesize` raw
                    # bytes on the socket are the actual file.
                    #
                    # IMPORTANT — only THIS thread reads from `sock`.
                    # If we handed the file off to another thread, the
                    # other thread and this thread would both call
                    # recv() on the same socket and the bytes would be
                    # split between them in an unpredictable order. That
                    # is a classic TCP race condition.
                    #
                    # So instead, we:
                    #   1. Ask the GUI where to save the file. The GUI
                    #      returns a destination path (or None to cancel).
                    #   2. We read exactly `filesize` bytes here.
                    #   3. We write them to disk.
                    #   4. We notify the GUI when finished.
                    #
                    # This blocks this peer connection's message loop
                    # for the duration of the transfer, but that is
                    # fine: each peer has its own thread, so other peers
                    # keep working normally.
                    dest_path = callbacks["on_file_meta"](
                        peer_id, peer_name, msg)
                    filesize = msg.get("filesize", 0)
                    if dest_path:
                        ok = protocol.recv_file(sock, filesize, dest_path)
                        callbacks["on_file_done"](
                            peer_id, msg.get("filename", "file"),
                            dest_path, ok)
                    else:
                        # GUI refused — drain the bytes so the stream
                        # stays aligned, then drop them.
                        _ = protocol.recv_file(sock, filesize,
                                               os.devnull)
                else:
                    self.log(f"[WARN] Unknown message type: {mtype}")

        except (ConnectionResetError, ConnectionAbortedError,
                BrokenPipeError, OSError) as e:
            self.log(f"[ERROR] Connection lost with {address}: {e}")
        finally:
            # Clean up: if we registered this peer, remove it from the list.
            # We use peer_id captured above if available.
            try:
                self._remove_peer(peer_id)
            except UnboundLocalError:
                # Handshake never finished — nothing to remove.
                pass
            try:
                sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # Outgoing messages
    # ------------------------------------------------------------------ #

    def send_text(self, peer_id, message):
        """Send a text message to a connected peer."""
        info = self._get_peer(peer_id)
        if info is None:
            self.log(f"[ERROR] Peer {peer_id} is not connected")
            return False

        with info.lock:
            protocol.send_message(info.sock, {
                "type": "text",
                "sender_id": self.peer_id,
                "sender_name": self.peer_name,
                "message": message,
            })
        return True

    def send_file(self, peer_id, filepath):
        """
        Send a file to a connected peer.

        Step 1: send framed FILE metadata JSON (filename, filesize).
        Step 2: stream the raw file bytes in 64 KB chunks.

        The order matters! The receiver will read filesize bytes
        immediately after parsing the metadata.
        """
        info = self._get_peer(peer_id)
        if info is None:
            self.log(f"[ERROR] Peer {peer_id} is not connected")
            return False

        if not os.path.isfile(filepath):
            self.log(f"[ERROR] File not found: {filepath}")
            return False

        filesize = os.path.getsize(filepath)
        filename = os.path.basename(filepath)

        with info.lock:
            # Step 1: metadata (framed JSON).
            protocol.send_message(info.sock, {
                "type": "file",
                "sender_id": self.peer_id,
                "sender_name": self.peer_name,
                "filename": filename,
                "filesize": filesize,
            })
            # Step 2: raw file bytes (no framing, just chunks).
            protocol.send_file(info.sock, filepath)

        self.log(f"[FILE] Sent {filename} ({filesize} bytes) to "
                 f"{info.peer_name}")
        return True

    def _get_peer(self, peer_id):
        with self.peers_lock:
            return self.peers.get(peer_id)

    # ------------------------------------------------------------------ #
    # Shutdown
    # ------------------------------------------------------------------ #

    def stop(self):
        """Stop the server and close all connections."""
        self.stop_server()
