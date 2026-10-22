
import ctypes
import os
import queue
import re
import socket
import struct
import subprocess
import sys
import threading
import tkinter as tk
import traceback
import webbrowser

from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from tkinter.scrolledtext import ScrolledText
from urllib.parse import unquote, urlsplit

if os.name == "nt" and not ctypes.windll.shell32.IsUserAnAdmin():
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        subprocess.list2cmdline([os.path.abspath(__file__)]),
        os.getcwd(),
        1
    )
    if result > 32:
        sys.exit()
    print("Administrator permission was not granted.")
    input("Press Enter to exit...")
    sys.exit()

BASE = Path(__file__).resolve().parent
DOMAINS = (BASE / "Domains").resolve()
PORT = 8000
SSID = "LocalNet"
PASSWORD = "LocalNet123"
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
started = False
lock = threading.RLock()


def log(message):
    events.put(str(message))


def run(args, timeout=10):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False
    )


def start_hotspot():
    global hotspot_started

    log("Checking Windows hotspot support...")

    try:
        result = run([
            "netsh", "wlan", "set", "hostednetwork",
            "mode=allow",
            f"ssid={SSID}",
            f"key={PASSWORD}"
        ])
        log((result.stdout + result.stderr).strip())

        if result.returncode != 0:
            log("ERROR: This Wi-Fi adapter may not support legacy hosted networks.")
            log("Try Windows Settings > Network & internet > Mobile hotspot.")
            return False

        result = run(["netsh", "wlan", "start", "hostednetwork"])
        log((result.stdout + result.stderr).strip())

        if result.returncode != 0:
            log("ERROR: Windows could not start the hotspot.")
            return False

        hotspot_started = True
        log(f"Wi-Fi network started: {SSID}")
        log(f"Wi-Fi password: {PASSWORD}")
        log("WARNING: Internet sharing and DHCP may require additional setup.")
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
        result = run(["netsh", "wlan", "stop", "hostednetwork"])
        log((result.stdout + result.stderr).strip())
    except Exception:
        log(traceback.format_exc())

    hotspot_started = False


def dns_response(data):
    if len(data) < 12:
        return None

    ident, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])

    if qd != 1:
        return None

    pos = 12
    labels = []

    try:
        while True:
            length = data[pos]
            pos += 1

            if length == 0:
                break

            if length & 0xC0 or pos + length > len(data):
                return None

            labels.append(data[pos:pos + length].decode("ascii").lower())
            pos += length

        if pos + 4 > len(data):
            return None

        qtype, qclass = struct.unpack("!HH", data[pos:pos + 4])
        end = pos + 4
        name = ".".join(labels)
        question = data[12:end]

        if name.endswith(".test") and qclass == 1:
            hostname = name[:-5]

            with lock:
                allowed = (
                    re.fullmatch(r"[a-z0-9_-]+", hostname)
                    and hostname in active
                    and hostname not in restricted
                )

            if allowed and qtype in (1, 255):
                answer = (
                    b"\xc0\x0c"
                    + struct.pack("!HHIH", 1, 1, 60, 4)
                    + socket.inet_aton(GATEWAY)
                )

                return (
                    struct.pack("!HHHHHH", ident, 0x8180, 1, 1, 0, 0)
                    + question + answer
                )

            return (
                struct.pack("!HHHHHH", ident, 0x8183, 1, 0, 0, 0)
                + question
            )

        return None

    except Exception:
        return None


def dns_loop():
    global dns_socket

    s = None

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", 53))
        s.settimeout(1)

        with lock:
            if closing:
                s.close()
                return
            dns_socket = s

        log("DNS listening on UDP port 53.")

        while True:
            with lock:
                if closing:
                    break

            try:
                data, address = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            response = dns_response(data)

            if response:
                try:
                    s.sendto(response, address)
                except OSError:
                    break
                continue

            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
                    upstream.settimeout(3)
                    upstream.sendto(data, UPSTREAM_DNS)
                    response, _ = upstream.recvfrom(4096)
                    s.sendto(response, address)
            except Exception as error:
                log(f"DNS forwarding error: {error}")

    except PermissionError:
        log("DNS ERROR: Administrator rights required for port 53.")
    except OSError as error:
        log(f"DNS ERROR: {error}")
        log("Port 53 may already be in use.")
    except Exception:
        log(traceback.format_exc())
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass

        with lock:
            if dns_socket is s:
                dns_socket = None


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log("HTTP " + (fmt % args))

    def do_GET(self):
        host = self.headers.get("Host", "").split(":")[0].lower()
        name = host[:-5] if host.endswith(".test") else ""

        if host in ("127.0.0.1", "localhost"):
            name = "test"

        with lock:
            allowed = name in active and name not in restricted

        if not re.fullmatch(r"[a-z0-9_-]+", name):
            self.send_error(400, "Use a valid *.test hostname")
            return

        if not allowed:
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
        target = (root / unquote(urlsplit(path).path).lstrip("/")).resolve()

        try:
            target.relative_to(root)
        except ValueError:
            return str(root / "__not_found__")

        return str(target)


def start_services():
    global server, dns_thread, started

    with lock:
        if closing:
            return False
        if started:
            return True

    try:
        new_server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        new_server.daemon_threads = True

        with lock:
            if closing:
                new_server.server_close()
                return False

            server = new_server
            started = True

        threading.Thread(target=new_server.serve_forever, daemon=True).start()
        log(f"HTTP server listening on port {PORT}.")

        dns_thread = threading.Thread(target=dns_loop, daemon=True)
        dns_thread.start()
        return True

    except Exception:
        log("SERVICE START ERROR")
        log(traceback.format_exc())
        return False


def stop_services():
    global server, started, dns_socket

    with lock:
        old_server = server
        old_dns = dns_socket
        server = None
        dns_socket = None
        started = False
        active.clear()

    if old_dns:
        try:
            old_dns.close()
        except Exception:
            pass

    if old_server:
        try:
            old_server.shutdown()
            old_server.server_close()
        except Exception:
            log(traceback.format_exc())

    log("Web and DNS services stopped.")


def valid_name(name):
    name = name.removesuffix(".test")

    if not re.fullmatch(r"[a-z0-9_-]+", name):
        log("ERROR: Invalid domain name.")
        return None

    return name


def command(line):
    args = line.strip().lower().split()

    if not args:
        return

    cmd = args[0]

    if cmd == "help":
        log("Commands: host <name>, open <name>, restrict <name>, status, shutdown")
        return

    if cmd == "host" and len(args) == 2:
        name = valid_name(args[1])
        if not name:
            return

        folder = (DOMAINS / name).resolve()

        if folder.parent != DOMAINS or not folder.is_dir():
            log(f"ERROR: Missing folder Domains/{name}")
            return

        if not started:
            start_hotspot()
            if not start_services():
                log("ERROR: Could not start LocalNet services.")
                return

        with lock:
            active.add(name)
            restricted.discard(name)

        log(f"HOSTING {name}.test:{PORT}")

        if not (folder / "index.html").is_file():
            log(f"WARNING: Domains/{name}/index.html is missing.")

        log(f"Local test URL: http://127.0.0.1:{PORT}")
        log("Remote DNS, DHCP and internet sharing may need configuration.")
        return

    if cmd == "open" and len(args) == 2:
        name = valid_name(args[1])
        if not name:
            return

        with lock:
            allowed = started and name in active and name not in restricted

        if not allowed:
            log("ERROR: Domain isn't hosted.")
            return

        url = f"http://{name}.test:{PORT}"
        webbrowser.open(url)
        log(f"Opening {url}")
        return

    if cmd == "restrict" and len(args) == 2:
        name = valid_name(args[1])
        if not name:
            return

        with lock:
            active.discard(name)
            restricted.add(name)

        log(f"RESTRICTED {name}.test")
        return

    if cmd == "status":
        with lock:
            log(f"Services: {'running' if started else 'stopped'}")
            log(f"Hosted: {', '.join(sorted(active)) or 'none'}")
            log(f"Restricted: {', '.join(sorted(restricted)) or 'none'}")

        log(f"Hotspot started: {hotspot_started}")
        return

    if cmd == "shutdown":
        close_app()
        return

    log("ERROR: Unknown command. Type help.")


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

    if root.winfo_exists():
        root.after(80, update_log)


def close_app():
    global closing

    with lock:
        if closing:
            return
        closing = True

    stop_services()
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
entry.focus_set()

root.protocol("WM_DELETE_WINDOW", close_app)

log("LocalNet command line ready.")
log(f"Domains folder: {DOMAINS}")
log(f"Wi-Fi target: {SSID}")
log("No server or hotspot started automatically.")
log("Type help for commands.")

if not DOMAINS.is_dir():
    log("WARNING: Create a Domains folder beside LocalNet.py.")

update_log()
root.mainloop()
