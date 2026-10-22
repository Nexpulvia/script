
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, unquote

D = Path(__file__).parent / "Domains"

class H(SimpleHTTPRequestHandler):
    def do_GET(self):
        host = self.headers.get("Host", "").split(":")[0]
        name = host.removesuffix(".test")
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            self.send_error(400)
            return
        root = (D / name).resolve()
        if root.parent != D.resolve() or not root.is_dir():
            self.send_error(404, "Domain not found")
            return
        self.directory = str(root)
        super().do_GET()

    def translate_path(self, path):
        path = unquote(urlparse(path).path).lstrip("/")
        p = (Path(self.directory) / path).resolve()
        try:
            p.relative_to(Path(self.directory).resolve())
        except ValueError:
            return str(Path(self.directory) / "__not_found__")
        return str(p)

ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()
