"""Small loopback-only dashboard and same-origin JSON API."""

import hmac
import json
import secrets
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit
from .service import AssistantService


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, collector, config, store, *, demo=False):
        self.collector = collector
        self.config = config
        self.store = store
        self.token = secrets.token_urlsafe(32)
        self.service = AssistantService(collector, config, store, demo=demo)
        self.file_service = None
        collector.service = self.service
        super().__init__(("127.0.0.1", port), DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format: str, *args) -> None:
        # Requests can contain user-controlled strings; avoid logging paths or names.
        pass

    def _allowed_host(self) -> bool:
        port = self.server.server_port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data, separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if not self._allowed_host():
            self._json(403, {"error": "Host rejected"})
            return
        path = urlsplit(self.path).path
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.css": ("app.css", "text/css; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/intelligence.js": ("intelligence.js", "text/javascript; charset=utf-8"),
                  "/maintenance.js": ("maintenance.js", "text/javascript; charset=utf-8"),
                  "/files.js": ("files.js", "text/javascript; charset=utf-8"),
                  "/intelligence.css": ("intelligence.css", "text/css; charset=utf-8"),
                  "/files.css": ("files.css", "text/css; charset=utf-8")}
        if path in assets:
            name, content_type = assets[path]
            body = (files("kshetrajna") / "web" / name).read_bytes()
            if name == "index.html":
                body = body.replace(b"__CSRF_TOKEN__", self.server.token.encode("ascii"))
            self._send(200, body, content_type)
        elif path == "/api/state":
            self._json(200, self.server.service.state())
        elif path == "/api/report":
            self._json(200, self.server.service.report())
        elif path == "/api/files/state" and self.server.file_service:
            self._json(200, self.server.file_service.state())
        elif path.startswith("/api/files/root/") and self.server.file_service:
            try:
                root_id = int(path.split("/")[-1])
                self._json(200, self.server.file_service.get_root_details(root_id))
            except ValueError as e:
                self._json(400, {"error": str(e)})
        elif path == "/api/files/search" and self.server.file_service:
            from urllib.parse import parse_qs
            qs = parse_qs(urlsplit(self.path).query)
            q = qs.get("q", [""])[0]
            root_id = int(qs.get("root_id", [0])[0]) if "root_id" in qs else None
            self._json(200, self.server.file_service.search(q, root_id))
        elif path == "/api/files/insights" and self.server.file_service:
            self._json(200, self.server.file_service.get_insights())
        elif path.startswith("/api/files/file/") and path.endswith("/excerpt") and self.server.file_service:
            try:
                file_id = int(path.split("/")[-2])
                self._json(200, self.server.file_service.get_file_excerpt(file_id))
            except ValueError as e:
                self._json(400, {"error": str(e)})
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self._allowed_host():
            self.close_connection = True
            self._json(403, {"error": "Host rejected"})
            return
        expected_origins = {f"http://127.0.0.1:{self.server.server_port}",
                            f"http://localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        supplied_token = self.headers.get("X-Kshetrajna-Token", "")
        if origin not in expected_origins or not supplied_token.isascii() or not hmac.compare_digest(
            supplied_token, self.server.token
        ):
            self.close_connection = True
            self._json(403, {"error": "Request rejected"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > 4096 or self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self._json(413, {"error": "Invalid request size"})
            return
        path = urlsplit(self.path).path
        service = self.server.service
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Request body must be a JSON object")
            if path == "/api/settings":
                with self.server.collector._gate:
                    result = self.server.collector.update_settings(body)
                    service.tick()
                self._json(200, {"settings": asdict(result)})
            elif path == "/api/delete":
                service.delete_history()
                self._json(200, {"deleted": True})
            elif path == "/api/maintenance/preview":
                self._json(200, {"plan": service.maintenance.preview(body.get("id"))})
            elif path == "/api/maintenance/execute":
                self._json(200, {"decision": service.maintenance.execute(body.get("id"))})
            elif path == "/api/actions/approve":
                self._json(200, {"decision": service.approve(body.get("id"))})
            elif path == "/api/actions/undo":
                self._json(200, {"decision": service.undo(body.get("id"))})
            elif path == "/api/actions/dismiss":
                service.dismiss(body.get("id"))
                self._json(200, {"dismissed": True})
            elif path == "/api/actions/feedback":
                service.feedback(body.get("id"), body.get("value"))
                self._json(200, {"saved": True})
            elif path == "/api/workspaces/save":
                service.save_workspace(body.get("name"), body.get("apps"))
                self._json(200, {"saved": True})
            elif path == "/api/workspaces/activate":
                service.activate_workspace(body.get("name"))
                self._json(200, {"activated": True})
            elif path == "/api/demo/scenario":
                service.change_scenario(body.get("scenario"))
                self._json(200, {"changed": True})
            elif path == "/api/files/roots/add" and self.server.file_service:
                self._json(200, self.server.file_service.add_root(body.get("path"), body.get("label", "")))
            elif path == "/api/files/roots/rescan" and self.server.file_service:
                self.server.file_service.rescan_root(body.get("root_id"))
                self._json(200, {"rescanned": True})
            elif path == "/api/files/roots/cancel-scan" and self.server.file_service:
                self.server.file_service.cancel_scan(body.get("root_id"))
                self._json(200, {"cancelled": True})
            elif path == "/api/files/roots/remove" and self.server.file_service:
                self.server.file_service.remove_root(body.get("root_id"))
                self._json(200, {"removed": True})
            elif path == "/api/files/delete-all" and self.server.file_service:
                self.server.file_service.delete_all()
                self._json(200, {"deleted": True})
            else:
                self._json(404, {"error": "Not found"})
        except (ValueError, TypeError) as error:
            self._json(400, {"error": str(error)})
        except OSError:
            self._json(409, {"error": "Windows could not complete this operation. The target may have exited or denied access."})
