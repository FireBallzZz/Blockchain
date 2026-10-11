"""
main.py
-------
Tkinter GUI for the P2P chat + file-sharing application.

Layout (top to bottom):
    Status bar          -> Offline / Listening on port X / N peers
    My Peer             -> name + port + Start / Stop buttons
    Connect to Peer     -> remote IP + port + Connect button
    Connected Peers     -> Listbox showing each peer "Name [peer_id]"
    Messages / Events   -> ScrolledText log
    Send                -> message entry + Send button + Choose File & Send

Thread-safety:
    All networking runs in background threads. Tkinter widgets must
    only be touched from the main thread, so the GUI callbacks
    installed in p2p_node.py schedule UI updates with root.after(0, ...).

    The only exception is `on_file_meta`: it runs on the network thread
    and decides where to save the incoming file. It only touches files
    on disk, never Tkinter widgets. It uses `schedule()` to append a
    log line.
"""

import os
import socket
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox

import p2p_node


# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
node = None                # The P2PNode instance once the user clicks Start
peer_lookup = {}           # peer_id (str) -> display string (str)
selected_peer_id = None    # peer_id currently selected in the Listbox
_connect_in_flight = False # guard against stacked Connect clicks


# ---------------------------------------------------------------------------
# GUI-thread-safe helpers
# ---------------------------------------------------------------------------
def log_event(msg):
    event_log.insert(tk.END, msg + "\n")
    event_log.see(tk.END)


def update_status():
    if node is None:
        status_var.set("Status: Offline")
        return
    n = len(node.peers)
    if n == 0:
        status_var.set(f"Status: Listening on port {node.port}  |  0 peers")
    elif n == 1:
        status_var.set(f"Status: Listening on port {node.port}  |  1 peer")
    else:
        status_var.set(f"Status: Listening on port {node.port}  |  "
                       f"{n} peers")


def add_peer(info):
    display = info.display()
    peer_lookup[info.peer_id] = display
    peer_listbox.insert(tk.END, display)
    update_status()


def remove_peer(peer_id):
    global selected_peer_id
    display = peer_lookup.pop(peer_id, None)
    if display is not None:
        try:
            idx = peer_listbox.get(0, tk.END).index(display)
            peer_listbox.delete(idx)
        except ValueError:
            pass
    if selected_peer_id == peer_id:
        selected_peer_id = None
    update_status()
    log_event(f"[INFO] Peer disconnected ({peer_id})")


def on_text(peer_id, sender_name, message):
    display = peer_lookup.get(peer_id, sender_name)
    log_event(f"{display}: {message}")


def on_file_meta(peer_id, sender_name, meta):
    """
    Incoming file metadata. Runs on the NETWORK thread.

    Returns the local destination path where the file should be saved.
    The network thread will read exactly `filesize` raw bytes from the
    socket and write them to that path before resuming the message loop.

    Doing the read in the network thread (not in a separate GUI-launched
    thread) is essential: if two threads read from the same socket
    simultaneously, the kernel splits the bytes between them in an
    unpredictable order, which corrupts the protocol stream.
    """
    filename = meta.get("filename", "file") or "file"
    filesize = meta.get("filesize", 0)

    downloads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    # If the same filename arrives twice, append a numeric suffix so we
    # never overwrite a previously received file.
    dest_path = os.path.join(downloads_dir, filename)
    base, ext = os.path.splitext(filename)
    i = 1
    while os.path.exists(dest_path):
        dest_path = os.path.join(downloads_dir, f"{base}_{i}{ext}")
        i += 1

    schedule(log_event,
             f"[FILE] Incoming '{filename}' ({filesize} bytes) "
             f"from {sender_name}")
    return dest_path


def on_file_done(peer_id, filename, dest_path, ok):
    if ok:
        schedule(log_event,
                 f"[FILE] Saved to downloads/{os.path.basename(dest_path)}")
    else:
        schedule(log_event, f"[ERROR] File transfer failed for {filename}")


def schedule(callback, *args):
    root.after(0, callback, *args)


# ---------------------------------------------------------------------------
# Button handlers
# ---------------------------------------------------------------------------
def start_peer():
    global node
    name = name_entry.get().strip()
    port_text = port_entry.get().strip()

    if not name:
        messagebox.showerror("Error", "Please enter a peer name.")
        return
    try:
        port = int(port_text)
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        messagebox.showerror("Error", "Port must be a number 1-65535.")
        return

    if node is not None:
        messagebox.showinfo("Info", "Peer is already running.")
        return

    p2p_node.callbacks["log_event"] = lambda m: schedule(log_event, m)
    p2p_node.callbacks["add_peer"] = lambda i: schedule(add_peer, i)
    p2p_node.callbacks["remove_peer"] = lambda pid: schedule(remove_peer, pid)
    p2p_node.callbacks["on_text"] = lambda pid, sn, msg: schedule(
        on_text, pid, sn, msg)
    p2p_node.callbacks["on_file_meta"] = on_file_meta
    p2p_node.callbacks["on_file_done"] = on_file_done

    node = p2p_node.P2PNode(name, port)
    try:
        node.start_server()
    except OSError as e:
        messagebox.showerror("Error", f"Could not start peer: {e}")
        node = None
        update_status()
        return

    log_event(f"[INFO] Peer '{name}' started, listening on port {port}")
    start_button.config(state=tk.DISABLED)
    stop_button.config(state=tk.NORMAL)
    name_entry.config(state=tk.DISABLED)
    port_entry.config(state=tk.DISABLED)
    connect_button.config(state=tk.NORMAL)
    send_button.config(state=tk.NORMAL)
    file_button.config(state=tk.NORMAL)
    update_status()


def stop_peer():
    global node, selected_peer_id
    if node is None:
        return
    node.stop()
    node = None
    peer_lookup.clear()
    peer_listbox.delete(0, tk.END)
    selected_peer_id = None
    log_event("[INFO] Peer stopped")
    start_button.config(state=tk.NORMAL)
    stop_button.config(state=tk.DISABLED)
    name_entry.config(state=tk.NORMAL)
    port_entry.config(state=tk.NORMAL)
    connect_button.config(state=tk.DISABLED)
    send_button.config(state=tk.DISABLED)
    file_button.config(state=tk.DISABLED)
    update_status()


def _validate_ip(ip):
    if not ip:
        return False
    try:
        socket.inet_aton(ip)
        return True
    except OSError:
        pass
    return all(c.isalnum() or c in ".-" for c in ip)


def connect_peer():
    global _connect_in_flight
    if node is None:
        messagebox.showerror("Error", "Start the peer first.")
        return
    if _connect_in_flight:
        return
    ip = ip_entry.get().strip()
    port_text = remote_port_entry.get().strip()
    if not ip or not port_text:
        messagebox.showerror("Error", "Enter remote IP and port.")
        return
    if not _validate_ip(ip):
        messagebox.showerror("Error", f"Invalid IP address: {ip}")
        return
    try:
        port = int(port_text)
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        messagebox.showerror("Error", "Remote port must be 1-65535.")
        return

    _connect_in_flight = True

    def _do_connect():
        global _connect_in_flight
        try:
            node.connect_to(ip, port)
            schedule(log_event, f"[INFO] Connecting to {ip}:{port} ...")
        except (ConnectionRefusedError, OSError) as e:
            schedule(log_event, f"[ERROR] Connection failed: {e}")
            schedule(messagebox.showerror, "Connection failed", str(e))
        finally:
            _connect_in_flight = False

    threading.Thread(target=_do_connect, daemon=True).start()


def on_peer_select(event):
    global selected_peer_id
    selection = peer_listbox.curselection()
    if not selection:
        return
    display = peer_listbox.get(selection[0])
    for pid, d in peer_lookup.items():
        if d == display:
            selected_peer_id = pid
            break


def send_text():
    if node is None:
        messagebox.showerror("Error", "Start the peer first.")
        return
    if not selected_peer_id:
        messagebox.showerror("Error", "Select a connected peer first.")
        return
    text = message_entry.get().strip()
    if not text:
        return
    if node.send_text(selected_peer_id, text):
        display = peer_lookup.get(selected_peer_id, "?")
        log_event(f"You -> {display}: {text}")
        message_entry.delete(0, tk.END)


def choose_and_send_file():
    if node is None:
        messagebox.showerror("Error", "Start the peer first.")
        return
    if not selected_peer_id:
        messagebox.showerror("Error", "Select a connected peer first.")
        return
    filepath = filedialog.askopenfilename(title="Choose a file to send")
    if not filepath:
        return
    if node.send_file(selected_peer_id, filepath):
        log_event(f"[FILE] Sending {os.path.basename(filepath)} ...")


# ---------------------------------------------------------------------------
# Build the Tkinter window
# ---------------------------------------------------------------------------
root = tk.Tk()
root.title("P2P Network — Chat & File Sharing")
root.geometry("760x680")
root.minsize(640, 560)

status_var = tk.StringVar(value="Status: Offline")
status_bar = tk.Label(root, textvariable=status_var, anchor="w",
                      relief="sunken", padx=6, pady=2,
                      font=("Segoe UI", 9))
status_bar.pack(fill="x", side="bottom")

my_peer_frame = tk.LabelFrame(root, text="My Peer", padx=8, pady=6)
my_peer_frame.pack(fill="x", padx=8, pady=(8, 4))

tk.Label(my_peer_frame, text="Name:").grid(row=0, column=0, sticky="w")
name_entry = tk.Entry(my_peer_frame, width=20)
name_entry.grid(row=0, column=1, padx=4)

tk.Label(my_peer_frame, text="Listening Port:").grid(row=0, column=2,
                                                    sticky="w")
port_entry = tk.Entry(my_peer_frame, width=10)
port_entry.grid(row=0, column=3, padx=4)
port_entry.insert(0, "5000")

start_button = tk.Button(my_peer_frame, text="Start Peer",
                         command=start_peer, width=12)
start_button.grid(row=0, column=4, padx=4)

stop_button = tk.Button(my_peer_frame, text="Stop",
                        command=stop_peer, width=8, state=tk.DISABLED)
stop_button.grid(row=0, column=5, padx=4)

connect_frame = tk.LabelFrame(root, text="Connect to Another Peer",
                              padx=8, pady=6)
connect_frame.pack(fill="x", padx=8, pady=4)

tk.Label(connect_frame, text="Remote IP:").grid(row=0, column=0, sticky="w")
ip_entry = tk.Entry(connect_frame, width=22)
ip_entry.grid(row=0, column=1, padx=4)
ip_entry.insert(0, "127.0.0.1")

tk.Label(connect_frame, text="Remote Port:").grid(row=0, column=2,
                                                  sticky="w")
remote_port_entry = tk.Entry(connect_frame, width=10)
remote_port_entry.grid(row=0, column=3, padx=4)
remote_port_entry.insert(0, "5001")

connect_button = tk.Button(connect_frame, text="Connect",
                           command=connect_peer, width=10,
                           state=tk.DISABLED)
connect_button.grid(row=0, column=4, padx=4)

middle_frame = tk.Frame(root)
middle_frame.pack(fill="both", expand=True, padx=8, pady=4)

peers_frame = tk.LabelFrame(middle_frame, text="Connected Peers",
                            padx=4, pady=4)
peers_frame.pack(side="left", fill="y")

peer_listbox = tk.Listbox(peers_frame, width=28, height=18,
                          font=("Consolas", 9),
                          selectbackground="#3a7afe",
                          selectforeground="white")
peer_listbox.pack(fill="y", expand=True)
peer_listbox.bind("<<ListboxSelect>>", on_peer_select)

log_frame = tk.LabelFrame(middle_frame, text="Messages / Events",
                         padx=4, pady=4)
log_frame.pack(side="left", fill="both", expand=True, padx=(8, 0))

event_log = scrolledtext.ScrolledText(log_frame, width=60, height=18,
                                      font=("Consolas", 9),
                                      state="normal", wrap="word")
event_log.pack(fill="both", expand=True)

send_frame = tk.LabelFrame(root, text="Send", padx=8, pady=6)
send_frame.pack(fill="x", padx=8, pady=4)

tk.Label(send_frame, text="Message:").grid(row=0, column=0, sticky="w")
message_entry = tk.Entry(send_frame, width=50)
message_entry.grid(row=0, column=1, padx=4, pady=2, sticky="we")
send_frame.columnconfigure(1, weight=1)

send_button = tk.Button(send_frame, text="Send", command=send_text,
                        width=10, state=tk.DISABLED)
send_button.grid(row=0, column=2, padx=4)

file_button = tk.Button(send_frame, text="Choose File & Send",
                        command=choose_and_send_file, width=22,
                        state=tk.DISABLED)
file_button.grid(row=1, column=1, sticky="w", pady=4)

# Pressing Enter in the message entry sends.
message_entry.bind("<Return>", lambda _e: send_text())

update_status()
log_event("[INFO] Welcome. Enter a name and port, then click Start Peer.")
log_event("[INFO] Tip: launch this app twice on the same PC to test "
         "two peers on different ports.")

root.mainloop()