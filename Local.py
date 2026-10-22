
import ctypes
import queue
import re
import socket
import struct
import subprocess
import threading
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from tkinter.scrolledtext import ScrolledText

BASE = Path(__file__).resolve().parent
DOMAINS = (BASE / "Domains").resolve()
PORT = 8000
SSID = "LocalNet"
PASSWORD = "LocalNet2026!"
GATEWAY = "192.168.137.1"
UPSTREAM_DNS = ("1.1.1.1", 53)

active = set()
restricted = set()
events = queue.Queue()
server = None
dns_socket = None
dns_thread = None
hotspot_started = False
closing = False


def log(s):
    events.put(str(s))


def run(args, timeout=10):
    return subprocess.run(
        args, capture_output=True, text=True,
        timeout=timeout, shell=False
    )


def admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def start_hotspot():
    global hotspot_started

    log("Checking legacy Windows hosted-network support...")

    try:
        r = run([
            "netsh", "wlan", "set", "hostednetwork",
            "mode=allow", f"ssid={SSID}", f"key={PASSWORD}"
        ])
        log((r.stdout + r.stderr).strip())

        if r.returncode != 0:
            log("Hotspot setup failed. This adapter may not support hostednetwork.")
            log("Open Windows Settings > Network & internet > Mobile hotspot.")
            return False

        r = run(["netsh", "wlan", "start", "hostednetwork"])
        log((r.stdout + r.stderr).strip())

        if r.returncode != 0 or "started" not in r.stdout.lower():
            log("Windows could not start the legacy hotspot.")
            log("Use Windows Mobile Hotspot manually on this PC.")
            return False

        hotspot_started = True
        log("Legacy hotspot started.")
        log("Note: internet sharing and client DHCP may still need Windows ICS.")
        return True

    except Exception:
        log("HOTSPOT ERROR")
        log(traceback.format_exc())
        return False


def stop_hotspot():
    global hotspot_started

    if not hotspot_started:
        return

    try:
        r = run(["netsh", "wlan", "stop", "hostednetwork"])
        log((r.stdout + r.stderr).strip())
    except Exception:
        log(traceback.format_exc())

    hotspot_started = False


def dns_response(data):
    if len(data) < 12:
        return None

    ident, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])

    if qd < 1:
        return None

    pos = 12
    labels = []

    try:
        while True:
            length = data[pos]
            pos += 1
            if length == 0:
                break
            if length & 0xC0:
                return None
            labels.append(data[pos:pos + length].decode("ascii").lower())
            pos += length

        if pos + 4 > len(data):
            return None

        qtype, qclass = struct.unpack("!HH", data[pos:pos + 4])
        question_end = pos + 4
        name = ".".join(labels)

        if name.endswith(".test") and qtype in (1, 255) and qclass == 1:
            host = name[:-5]

            if (re.fullmatch(r"[a-z0-9_-]+", host)
                    and host in active and host not in restricted):
                answer = (
                    b"\xc0\x0c"
                    + struct.pack("!HHIH", 1, 1, 60, 4)
                    + socket.inet_aton(GATEWAY)
                )
                return (
                    struct.pack("!HHHHHH", ident, 0x8180, 1, 1, 0, 0)
                    + data[12:question_end] + answer
                )

            return (
                struct.pack("!HHHHHH", ident, 0x8183, 1, 0, 0, 0)
                + data[12:question_end]
            )

        return None

    except Exception:
        return None


def dns_loop():
    global dns_socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", 53))
        s.settimeout(1)
        dns_socket = s
        log("DNS listening on UDP port 53.")

        while not closing:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            response = dns_response(data)

            if response:
                s.sendto(response, addr)
                continue

            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
                    upstream.settimeout(3)
                    upstream.sendto(data, UPSTREAM_DNS)
                    response, _ = upstream.recvfrom(4096)
                    s.sendto(response, addr)
            except Exception as e:
                log(f"DNS forwarding error: {e}")

    except PermissionError:
        log("DNS ERROR: administrator rights are required for port 53.")
    except OSError as e:
        log(f"DNS ERROR: {e}. Another DNS service may already use port 53.")
    except Exception:
        log(traceback.format_exc())
    finally:
        if dns_socket:
            try:
                dns_socket.close()
            except Exception:
                pass
            dns_socket = None


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log("HTTP " + (fmt % args))

    def do_GET(self):
        host = self.headers.get("Host", "").split(":")[0].lower()
        name = host[:-5] if host.endswith(".test") else ""

        if host in ("127.0.0.1", "localhost"):
            name = "test"

        if not re.fullmatch(r"[a-z0-9_-]+", name):
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
        from urllib.parse import unquote, urlsplit

        root = Path(self.directory).resolve()
        target = (root / unquote(urlsplit(path).path).lstrip("/")).resolve()

        try:
            target.relative_to(root)
        except ValueError:
            return str(root / "__not_found__")

        return str(target)


def start_web():
    global server

    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log(f"HTTP server listening on port {PORT}.")
    except Exception:
        log("HTTP SERVER ERROR")
        log(traceback.format_exc())


def command(line):
    args = line.strip().lower().split()
    if not args:
        return

    cmd = args[0]

    if cmd == "help":
        log("host <name> | open <name> | restrict <name> | status | hotspot | shutdown")
    elif cmd == "host" and len(args) == 2:
        name = args[1].removesuffix(".test")
        if not re.fullmatch(r"[a-z0-9_-]+", name):
            log("Invalid domain name.")
            return
        folder = (DOMAINS / name).resolve()
        if folder.parent != DOMAINS or not folder.is_dir():
            log(f"Missing folder: Domains/{name}")
            return
        active.add(name)
        restricted.discard(name)
        log(f"Hosting {name}.test")
        if not (folder / "index.html").exists():
            log("WARNING: index.html is missing.")
    elif cmd == "open" and len(args) == 2:
        name = args[1].removesuffix(".test")
        if name in active and name not in restricted:
            webbrowser.open(f"http://{name}.test:{PORT}")
            log(f"Opened {name}.test")
            log("If the name doesn't resolve on this PC, configure its hosts file.")
        else:
            log("Domain isn't hosted.")
    elif cmd == "restrict" and len(args) == 2:
        name = args[1].removesuffix(".test")
        active.discard(name)
        restricted.add(name)
        log(f"Restricted {name}.test")
    elif cmd == "status":
        log(f"Hosted: {', '.join(sorted(active)) or 'none'}")
        log(f"Restricted: {', '.join(sorted(restricted)) or 'none'}")
        log(f"Hotspot started by app: {hotspot_started}")
        log(f"DNS thread started: {dns_thread.is_alive() if dns_thread else False}")
    elif cmd == "hotspot":
        log("Hotspot is managed at startup. See startup diagnostics above.")
    elif cmd == "shutdown":
        close_app()
    else:
        log("Unknown command. Type help.")


def submit(event=None):
    line = entry.get()
    entry.delete(0, tk.END)
    log("> " + line)
    try:
        command(line)
    except Exception:
        log(traceback.format_exc())


def update_log():
    try:
        while True:
            msg = events.get_nowait()
            output.configure(state="normal")
            output.insert(tk.END, msg + "\n")
            output.see(tk.END)
            output.configure(state="disabled")
    except queue.Empty:
        pass

    if root.winfo_exists():
        root.after(80, update_log)


def close_app():
    global closing, dns_socket

    if closing:
        return

    closing = True
    active.clear()

    if dns_socket:
        try:
            dns_socket.close()
        except Exception:
            pass

    if server:
        threading.Thread(
            target=lambda: (server.shutdown(), server.server_close()),
            daemon=True
        ).start()

    stop_hotspot()
    root.destroy()


root = tk.Tk()
root.title("LocalNet command line")
root.geometry("780x500")

output = ScrolledText(root, state="disabled", font=("Consolas", 10))
output.pack(fill="both", expand=True, padx=8, pady=8)

entry = tk.Entry(root, font=("Consolas", 11))
entry.pack(fill="x", padx=8, pady=(0, 8))
entry.bind("<Return>", submit)

root.protocol("WM_DELETE_WINDOW", close_app)

log("LocalNet starting...")
log(f"Domains folder: {DOMAINS}")

if not DOMAINS.is_dir():
    log("ERROR: Create a Domains folder beside LocalNet.py.")

if not admin():
    log("WARNING: Not running as Administrator.")
    log("DNS port 53 and hotspot startup may fail.")

start_web()

dns_thread = threading.Thread(target=dns_loop, daemon=True)
dns_thread.start()

if admin():
    start_hotspot()
else:
    log("Open LocalNet as Administrator to attempt hotspot startup.")

log("Type help for commands.")
update_log()
root.mainloop()
