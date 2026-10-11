import json
import struct


# 4 bytes, unsigned int, big-endian ("!I" in struct terms).
# This format is used to send the size of the next JSON message.
LENGTH_FORMAT = "!I"
LENGTH_SIZE = 4  # bytes

# Chunk size used while streaming file bytes over the socket.
# 64 KB is small enough to keep memory low and large enough to be efficient.
FILE_CHUNK_SIZE = 64 * 1024


def send_message(sock, message):
    """
    Send a single JSON message over `sock` using length-prefix framing.

    Steps:
      1. Convert the Python dict -> JSON string -> bytes (utf-8).
      2. Pack the byte length into 4 bytes.
      3. Send the 4 length bytes, then the JSON bytes.
    """
    payload = json.dumps(message).encode("utf-8")
    length_bytes = struct.pack(LENGTH_FORMAT, len(payload))

    # sendall() guarantees that ALL bytes are written to the socket.
    sock.sendall(length_bytes)
    sock.sendall(payload)


def recv_exact(sock, n):
    """
    Read EXACTLY `n` bytes from `sock`.

    TCP may return fewer bytes than we asked for, so we keep calling
    recv() until we have all `n` bytes (or the connection closes).

    Returns bytes, or b"" if the peer closed the connection.
    """
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            # Peer closed the socket.
            return b""
        data += chunk
    return data


def recv_message(sock):
    """
    Receive one framed JSON message and return it as a Python dict.

    Steps:
      1. Read 4 bytes -> the length of the JSON payload.
      2. Read exactly that many bytes -> the JSON payload.
      3. Parse JSON and return the dict.

    Returns None if the peer disconnected before a full message arrived.
    """
    length_bytes = recv_exact(sock, LENGTH_SIZE)
    if not length_bytes:
        # Nothing came in -> the peer closed the socket.
        return None

    (length,) = struct.unpack(LENGTH_FORMAT, length_bytes)

    payload = recv_exact(sock, length)
    if not payload:
        return None

    return json.loads(payload.decode("utf-8"))


def send_file(sock, filepath):
    """
    Stream a file from disk to `sock` in 64 KB chunks.

    We do NOT load the whole file into memory — we read and send one
    chunk at a time. This keeps memory usage low for large files.
    """
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(FILE_CHUNK_SIZE)
            if not chunk:
                break
            sock.sendall(chunk)


def recv_file(sock, filesize, dest_path):
    """
    Receive exactly `filesize` bytes from `sock` and write them to
    `dest_path`.

    We keep reading until the full file size has arrived, because a
    single recv() may return only part of the file.
    """
    received = 0
    with open(dest_path, "wb") as f:
        while received < filesize:
            to_read = min(FILE_CHUNK_SIZE, filesize - received)
            chunk = sock.recv(to_read)
            if not chunk:
                # Connection died in the middle of the file.
                return False
            f.write(chunk)
            received += len(chunk)
    return True
