import os
import sys
import socket
import struct
import threading
import subprocess
import ctypes
import queue
import webbrowser
import ipaddress
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tkinter import Tk, Text, Entry, END, BOTH, LEFT, RIGHT, Y, Frame, Scrollbar

if os.name != "nt":
    raise SystemExit("LocalNet requires Windows.")

if not ctypes.windll.shell32.IsUserAnAdmin():
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable,
        subprocess.list2cmdline([os.path.abspath(__file__)]),
        None, 1
    )
    raise SystemExit

BASE = Path(__file__).resolve().parent
DOMAINS = BASE / "Domains"
SSID = "LocalNet"
PASSWORD = "LocalNet123"
PORT = 8000
GATEWAY = "192.168.137.1"
DNS_UPSTREAM = ("1.1.1.1", 53)

DOMAINS.mkdir(exist_ok=True)

active = set()
restricted = set()
hotspot_started = False
services_started = False
http_server = None
dns_socket = None
dns_thread = None
http_thread = None
log_queue = queue.Queue()
running = True


def log(message):
    log_queue.put(str(message))


def domain_name(name):
    name = name.strip().lower()
    if name.endswith(".test"):
        name = name[:-5]
    return name.strip(".")


def valid_domain(name):
    if not name or len(name) > 63:
        return False
    return all(
        part and len(part) <= 63 and
        part[0].isalnum() and part[-1].isalnum() and
        all(c.isalnum() or c == "-" for c in part)
        for part in name.split(".")
    )


def powershell(script, timeout=40):
    return subprocess.run(
        [
            "powershell.exe", "-NoProfile",
            "-ExecutionPolicy", "Bypass", "-Command", script
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False
    )


def start_hotspot():
    global hotspot_started

    script = r"""
$ErrorActionPreference = 'Stop'
try {
    $null = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType=WindowsRuntime]
    $null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime]
    $null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime]

    $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    if ($null -eq $profile) {
        throw 'No active internet connection profile was found. Connect to the internet first.'
    }

    $manager = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)

    $config = New-Object Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration
    $config.Ssid = 'LocalNet'
    $config.Passphrase = 'LocalNet123'

    $configure = $manager.ConfigureAccessPointAsync($config)
    while ($configure.Status -eq [Windows.Foundation.AsyncStatus]::Started) {
        Start-Sleep -Milliseconds 100
    }
    if ($configure.Status -ne [Windows.Foundation.AsyncStatus]::Completed) {
        throw 'Windows could not configure the hotspot.'
    }
    $configure.GetResults()

    $operation = $manager.StartTetheringAsync()
    while ($operation.Status -eq [Windows.Foundation.AsyncStatus]::Started) {
        Start-Sleep -Milliseconds 100
    }
    if ($operation.Status -ne [Windows.Foundation.AsyncStatus]::Completed) {
        throw 'Windows could not start tethering.'
    }

    $result = $operation.GetResults()
    if ($result.Status -ne [Windows.Networking.NetworkOperators.TetheringOperationStatus]::Success -and
        $result.Status -ne [Windows.Networking.NetworkOperators.TetheringOperationStatus]::AlreadyOn) {
        throw "Hotspot failed: $($result.Status) $($result.AdditionalErrorMessage)"
    }

    'HOTSPOT_OK'
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""

    log("Starting Windows Mobile Hotspot...")

    try:
        result = powershell(script)

        if result.stdout.strip():
            log(result.stdout.strip())
        if result.stderr.strip():
            log(result.stderr.strip())

        hotspot_started = (
            result.returncode == 0 and
            "HOTSPOT_OK" in result.stdout
        )

        if hotspot_started:
            log("Mobile Hotspot started.")
        else:
            log("ERROR: Hotspot startup failed.")
            log("Check Settings > Network & internet > Mobile hotspot.")

        return hotspot_started

    except Exception as error:
        log(f"Hotspot error: {error}")
        hotspot_started = False
        return False


def stop_hotspot():
    global hotspot_started

    if not hotspot_started:
        return

    script = r"""
$ErrorActionPreference = 'Stop'
try {
    $null = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType=WindowsRuntime]
    $null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime]

    $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    if ($null -ne $profile) {
        $manager = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
        $operation = $manager.StopTetheringAsync()

        while ($operation.Status -eq [Windows.Foundation.AsyncStatus]::Started) {
            Start-Sleep -Milliseconds 100
        }

        if ($operation.Status -eq [Windows.Foundation.AsyncStatus]::Completed) {
            $operation.GetResults() | Out-Null
        }
    }
    'HOTSPOT_STOP_REQUESTED'
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""

    try:
        result = powershell(script)
        if result.stdout.strip():
            log(result.stdout.strip())
        if result.stderr.strip():
            log(result.stderr.strip())
    except Exception as error:
        log(f"Hotspot shutdown error: {error}")
    finally:
        hotspot_started = False


def add_firewall_rules():
    rules = [
        ("LocalNet DNS UDP", "UDP", "53"),
        ("LocalNet Web TCP", "TCP", str(PORT))
    ]

    for name, protocol, port in rules:
        try:
            result = subprocess.run(
                [
                    "netsh", "advfirewall", "firewall", "add", "rule",
                    f"name={name}", "dir=in", "action=allow",
                    f"protocol={protocol}", f"localport={port}"
                ],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                log(f"Firewall rule checked/added: {name}")
            else:
                log(f"Firewall rule warning ({name}): {result.stderr.strip() or result.stdout.strip()}")
        except Exception as error:
            log(f"Firewall rule error: {error}")


class LocalNetHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class WebHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        host = self.headers.get("Host", "").split(":")[0].lower().rstrip(".")
        name = domain_name(host)

        if not name or name not in active:
            self.send_error(404, "LocalNet domain not hosted")
            return

        if name in restricted:
            self.send_error(403, "This LocalNet domain is restricted")
            return

        root = (DOMAINS / name).resolve()

        try:
            root.relative_to(DOMAINS.resolve())
        except ValueError:
            self.send_error(403)
            return

        requested = self.path.split("?", 1)[0].split("#", 1)[0]
        requested = requested.lstrip("/")

        if not requested:
            requested = "index.html"

        target = (root / requested).resolve()

        try:
            target.relative_to(root)
        except ValueError:
            self.send_error(403)
            return

        if target.is_dir():
            target = target / "index.html"

        if not target.is_file():
            self.send_error(404, "File not found")
            return

        try:
            data = target.read_bytes()
            content_type = "application/octet-stream"

            suffix = target.suffix.lower()
            types = {
                ".html": "text/html; charset=utf-8",
                ".htm": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".txt": "text/plain; charset=utf-8",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".svg": "image/svg+xml",
                ".ico": "image/x-icon",
                ".webp": "image/webp"
            }
            content_type = types.get(suffix, content_type)

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        except (OSError, ConnectionError):
            pass


def read_dns_name(packet, offset):
    labels = []
    jumped = False
    next_offset = offset
    jumps = 0

    while offset < len(packet):
        length = packet[offset]

        if length == 0:
            if not jumped:
                next_offset = offset + 1
            break

        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("Bad DNS pointer")
            pointer = ((length & 0x3F) << 8) | packet[offset + 1]
            if not jumped:
                next_offset = offset + 2
            offset = pointer
            jumped = True
            jumps += 1
            if jumps > 20:
                raise ValueError("DNS pointer loop")
            continue

        offset += 1
        if offset + length > len(packet):
            raise ValueError("Bad DNS label")

        labels.append(packet[offset:offset + length].decode("ascii"))
        offset += length

        if not jumped:
            next_offset = offset

    return ".".join(labels).lower(), next_offset


def build_local_dns_reply(packet, q_end, qname, qtype, qclass):
    transaction = packet[:2]
    flags = struct.unpack("!H", packet[2:4])[0]
    question = packet[12:q_end]

    # A query gets the LocalNet gateway address.
    if qtype == 1 and qclass == 1:
        answer = (
            b"\xc0\x0c" +
            struct.pack("!HHIH", 1, 1, 60, 4) +
            socket.inet_aton(GATEWAY)
        )
        return (
            transaction +
            struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0) +
            question + answer
        )

    # For unsupported record types, return a valid empty response.
    return (
        transaction +
        struct.pack("!HHHHH", 0x8180, 1, 0, 0, 0) +
        question
    )


def handle_dns_packet(packet, client):
    try:
        if len(packet) < 17:
            return

        qname, offset = read_dns_name(packet, 12)
        if offset + 4 > len(packet):
            return

        qtype, qclass = struct.unpack("!HH", packet[offset:offset + 4])
        q_end = offset + 4

        if qname.endswith(".test") or qname == "test":
            reply = build_local_dns_reply(
                packet, q_end, qname, qtype, qclass
            )
            dns_socket.sendto(reply, client)
            return

        # Forward ordinary DNS requests to Cloudflare.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
                upstream.settimeout(3)
                upstream.sendto(packet, DNS_UPSTREAM)
                reply, _ = upstream.recvfrom(4096)
                dns_socket.sendto(reply, client)
        except OSError:
            # Return SERVFAIL if upstream DNS is unavailable.
            response = (
                packet[:2] +
                struct.pack("!HHHHH", 0x8182, 1, 0, 0, 0) +
                packet[12:q_end]
            )
            dns_socket.sendto(response, client)

    except (ValueError, OSError, struct.error):
        pass


def dns_loop():
    global dns_socket

    while dns_socket is not None:
        try:
            packet, client = dns_socket.recvfrom(4096)
            threading.Thread(
                target=handle_dns_packet,
                args=(packet, client),
                daemon=True
            ).start()
        except OSError:
            break


def start_services():
    global http_server, http_thread, dns_socket, dns_thread, services_started

    if services_started:
        return True

    try:
        http_server = LocalNetHTTPServer(("0.0.0.0", PORT), WebHandler)
        http_thread = threading.Thread(
            target=http_server.serve_forever,
            daemon=True
        )
        http_thread.start()
        log(f"HTTP server listening on port {PORT}.")

    except OSError as error:
        log(f"HTTP server failed: {error}")
        http_server = None
        return False

    try:
        dns_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dns_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        dns_socket.bind(("0.0.0.0", 53))

        dns_thread = threading.Thread(target=dns_loop, daemon=True)
        dns_thread.start()
        log("DNS server listening on UDP port 53.")

    except OSError as error:
        log(f"DNS server failed: {error}")
        dns_socket = None

        if http_server:
            http_server.shutdown()
            http_server.server_close()
            http_server = None

        return False

    services_started = True
    return True


def stop_services():
    global http_server, dns_socket, services_started

    if dns_socket:
        try:
            dns_socket.close()
        except OSError:
            pass
        dns_socket = None

    if http_server:
        try:
            http_server.shutdown()
            http_server.server_close()
        except OSError:
            pass
        http_server = None

    services_started = False
    log("LocalNet web and DNS services stopped.")


def ensure_domain(name):
    folder = DOMAINS / name
    folder.mkdir(parents=True, exist_ok=True)

    index = folder / "index.html"
    if not index.exists():
        index.write_text(
            "<!doctype html>\n"
            "<html><head><meta charset='utf-8'>"
            f"<title>{name}.test</title></head>"
            "<body style='font-family:Arial,sans-serif'>"
            f"<h1>{name}.test</h1>"
            "<p>Your LocalNet domain is working.</p>"
            f"<p>Edit <code>Domains/{name}/index.html</code> "
            "to change this page.</p>"
            "</body></html>",
            encoding="utf-8"
        )


def command(line):
    global hotspot_started

    args = line.strip().split()
    if not args:
        return

    cmd = args[0].lower()

    if cmd == "host":
        if len(args) != 2:
            log("Usage: host <name>")
            return

        name = domain_name(args[1])
        if not valid_domain(name):
            log("Invalid domain name.")
            return

        if name in active:
            log(f"{name}.test is already hosted.")
            return

        if not services_started:
            if not start_hotspot():
                log("Hosting cancelled because the hotspot failed.")
                return

            if not start_services():
                log("ERROR: Could not start LocalNet services.")
                stop_hotspot()
                return

            add_firewall_rules()

        ensure_domain(name)
        active.add(name)
        restricted.discard(name)

        log(f"Hosting {name}.test")
        log(f"Local URL: http://{name}.test:{PORT}")
        log(f"Local folder: {DOMAINS / name}")

    elif cmd == "open":
        if len(args) != 2:
            log("Usage: open <name>")
            return

        name = domain_name(args[1])
        if name not in active:
            log(f"{name}.test is not hosted. Use: host {name}")
            return

        url = f"http://{name}.test:{PORT}"
        log(f"Opening {url}")
        webbrowser.open(url)

    elif cmd == "restrict":
        if len(args) != 2:
            log("Usage: restrict <name>")
            return

        name = domain_name(args[1])
        if name not in active:
            log(f"{name}.test is not hosted.")
            return

        restricted.add(name)
        log(f"{name}.test is now restricted.")

    elif cmd == "status":
        log(f"Hotspot: {'running' if hotspot_started else 'stopped'}")
        log(f"Services: {'running' if services_started else 'stopped'}")
        log(f"Gateway configured in script: {GATEWAY}")
        log(f"HTTP port: {PORT}")
        log("Hosted domains: " + (", ".join(sorted(
            f"{name}.test" for name in active
        )) if active else "none"))
        log("Restricted domains: " + (", ".join(sorted(
            f"{name}.test" for name in restricted
        )) if restricted else "none"))

    elif cmd == "shutdown":
        log("Shutting down LocalNet...")
        stop_services()
        stop_hotspot()
        active.clear()
        restricted.clear()
        log("LocalNet stopped.")

    elif cmd in ("help", "?"):
        log("Commands: host <name>, open <name>, restrict <name>, status, shutdown")

    else:
        log(f"Unknown command: {cmd}. Type help.")


root = Tk()
root.title("LocalNet command line")
root.geometry("760x480")
root.minsize(520, 320)

output = Text(
    root, bg="#101010", fg="#eeeeee",
    insertbackground="white", wrap="word",
    font=("Consolas", 10), state="disabled"
)
scroll = Scrollbar(root, command=output.yview)
output.configure(yscrollcommand=scroll.set)

scroll.pack(side=RIGHT, fill=Y)
output.pack(fill=BOTH, expand=True)

bottom = Frame(root)
bottom.pack(fill="x")

entry = Entry(
    bottom, bg="#202020", fg="white",
    insertbackground="white", font=("Consolas", 10)
)
entry.pack(side=LEFT, fill="x", expand=True, padx=6, pady=6)


def submit(event=None):
    line = entry.get().strip()
    if not line:
        return

    entry.delete(0, END)
    log("> " + line)
    threading.Thread(target=command, args=(line,), daemon=True).start()


entry.bind("<Return>", submit)


def pump_logs():
    try:
        while True:
            message = log_queue.get_nowait()
            output.configure(state="normal")
            output.insert(END, message + "\n")
            output.see(END)
            output.configure(state="disabled")
    except queue.Empty:
        pass

    if running:
        root.after(100, pump_logs)


def on_close():
    global running
    running = False

    try:
        stop_services()
        stop_hotspot()
    except Exception:
        pass

    root.destroy()


root.protocol("WM_DELETE_WINDOW", on_close)

log("LocalNet ready.")
log("Nothing starts automatically.")
log("Type help for commands.")
log(f"Domain folders: {DOMAINS}")
log("Example: host test")
log("Then edit Domains/test/index.html")
pump_logs()
root.mainloop()
