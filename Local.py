
import queue
import re
import threading
import traceback
import tkinter as tk
import webbrowser
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from tkinter.scrolledtext import ScrolledText
from urllib.parse import urlsplit, unquote

BASE = Path(__file__).resolve().parent
DOMAINS = (BASE / "Domains").resolve()
PORT = 8000

active = set()
restricted = set()
events = queue.Queue()
server = None
server_thread = None
server_running = False

def log(message):
    events.put(str(message))

class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log("REQUEST " + (fmt % args))

    def log_error(self, fmt, *args):
        log("HTTP ERROR " + (fmt % args))

    def do_GET(self):
        host = self.headers.get("Host", "").split(":")[0].lower()
        name = host[:-5] if host.endswith(".test") else ""

        if not name or not re.fullmatch(r"[a-z0-9_-]+", name):
            self.send_error(400, "Use a valid *.test hostname")
            return

        if name in restricted:
            self.send_error(403, "Domain restricted")
            return

        if name not in active:
            self.send_error(404, "Domain not hosted")
            return

        root = (DOMAINS / name).resolve()

        if root.parent != DOMAINS or not root.is_dir():
            self.send_error(404, "Domain folder not found")
            return

        self.directory = str(root)
        super().do_GET()

    def translate_path(self, path):
        root = Path(self.directory).resolve()
        path = unquote(urlsplit(path).path).lstrip("/")
        target = (root / path).resolve()

        try:
            target.relative_to(root)
        except ValueError:
            return str(root / "__not_found__")

        return str(target)

class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def start_server():
    global server, server_thread, server_running

    try:
        server = Server(("0.0.0.0", PORT), Handler)
        server_running = True
        server_thread = threading.Thread(
            target=server.serve_forever,
            daemon=True
        )
        server_thread.start()
        log(f"SERVER started on port {PORT}")
        log(f"Domains folder: {DOMAINS}")
    except Exception:
        server = None
        server_running = False
        log("SERVER STARTUP ERROR")
        log(traceback.format_exc())

def stop_server():
    global server, server_running

    old = server
    server = None
    server_running = False
    active.clear()

    if old:
        def stop():
            try:
                old.shutdown()
                old.server_close()
                log("SERVER stopped")
            except Exception:
                log("SHUTDOWN ERROR")
                log(traceback.format_exc())

        threading.Thread(target=stop, daemon=True).start()
    else:
        log("SERVER is already stopped")

def command(line):
    args = line.strip().lower().split()

    if not args:
        return

    cmd = args[0]

    if cmd == "help":
        log("Commands: host <name>, open <name>, restrict <name>, status, help, shutdown")
        return

    if cmd == "host" and len(args) == 2:
        name = args[1].removesuffix(".test")

        if not re.fullmatch(r"[a-z0-9_-]+", name):
            log("ERROR invalid domain name")
            return

        folder = (DOMAINS / name).resolve()

        if folder.parent != DOMAINS or not folder.is_dir():
            log(f"ERROR folder not found: Domains/{name}")
            return

        if not server_running:
            log("ERROR server is stopped")
            return

        if not (folder / "index.html").is_file():
            log(f"WARNING Domains/{name}/index.html is missing")

        restricted.discard(name)
        active.add(name)
        log(f"HOSTING {name}.test:{PORT}")
        return

    if cmd == "open" and len(args) == 2:
        name = args[1].removesuffix(".test")

        if not re.fullmatch(r"[a-z0-9_-]+", name):
            log("ERROR invalid domain name")
            return

        if not server_running or name not in active or name in restricted:
            log(f"ERROR {name}.test is not hosted")
            return

        url = f"http://{name}.test:{PORT}"
        try:
            webbrowser.open(url)
            log(f"OPENED {url}")
        except Exception:
            log("BROWSER ERROR")
            log(traceback.format_exc())
        return

    if cmd == "restrict" and len(args) == 2:
        name = args[1].removesuffix(".test")

        if not re.fullmatch(r"[a-z0-9_-]+", name):
            log("ERROR invalid domain name")
            return

        active.discard(name)
        restricted.add(name)
        log(f"RESTRICTED {name}.test")
        return

    if cmd == "status" and len(args) == 1:
        log(f"Active: {', '.join(sorted(active)) or 'none'}")
        log(f"Restricted: {', '.join(sorted(restricted)) or 'none'}")
        log(f"Server: {'running' if server_running else 'stopped'}")
        return

    if cmd == "shutdown" and len(args) == 1:
        stop_server()
        return

    log("ERROR invalid command. Type help.")

def submit(event=None):
    line = entry.get()
    entry.delete(0, tk.END)
    log("> " + line)

    try:
        command(line)
    except Exception:
        log("COMMAND ERROR")
        log(traceback.format_exc())

def update_log():
    try:
        while True:
            message = events.get_nowait()
            output.configure(state="normal")
            output.insert(tk.END, message + "\n")
            output.see(tk.END)
            output.configure(state="disabled")
    except queue.Empty:
        pass
    except Exception:
        traceback.print_exc()

    if root.winfo_exists():
        root.after(50, update_log)

def close_app():
    global server_running

    server_running = False
    old = server

    if old:
        threading.Thread(target=stop_server, daemon=True).start()

    root.destroy()

root = tk.Tk()
root.title("LocalNet command line")
root.geometry("760x480")

output = ScrolledText(
    root,
    state="disabled",
    font=("Consolas", 10)
)
output.pack(fill="both", expand=True, padx=8, pady=(8, 4))

entry = tk.Entry(root, font=("Consolas", 11))
entry.pack(fill="x", padx=8, pady=(4, 8))
entry.bind("<Return>", submit)
entry.focus_set()

root.protocol("WM_DELETE_WINDOW", close_app)

log("LocalNet command line")

if not DOMAINS.is_dir():
    log(f"ERROR Domains folder missing: {DOMAINS}")
else:
    start_server()

log("Type help to see commands.")
update_log()
root.mainloop()
