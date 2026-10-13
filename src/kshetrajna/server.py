"""Small loopback-only dashboard and same-origin JSON API."""

import hmac
import json
import secrets
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit
from .service import AssistantService
from .chat import ChatAssistant


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, collector, config, store, *, demo=False):
        self.collector = collector
        self.config = config
        self.store = store
        self.token = secrets.token_urlsafe(32)
        self.service = AssistantService(collector, config, store, demo=demo)
        self.chat = ChatAssistant(self.service)
        self.file_service = None
        self.shutdown_gate = threading.Lock()
        self.shutdown_requested = False
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

    def _send(self, status: int, body: bytes, content_type: str, *, headers=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        for name, value in (headers or {}).items():
            self.send_header(name, str(value))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; frame-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data, separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8")

    @staticmethod
    def _parse_file_id(value, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, str) or not value.isdecimal():
            raise ValueError(f"Invalid {name}")
        parsed = int(value)
        if parsed <= 0:
            raise ValueError(f"Invalid {name}")
        return parsed

    @staticmethod
    def _body_file_id(body: dict, name: str) -> int:
        value = body.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"Invalid {name}")
        return value

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
        assets.update({"/chat.js": ("chat.js", "text/javascript; charset=utf-8"),
                       "/chat.css": ("chat.css", "text/css; charset=utf-8")})
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
        elif path == "/api/files/known-folders" and self.server.file_service:
            from .known_folders import discover_known_folders
            self._json(200, {"folders": discover_known_folders()})
        elif path.startswith("/api/files/root/") and self.server.file_service:
            try:
                root_id = self._parse_file_id(path.rsplit("/", 1)[-1], "root_id")
                self._json(200, self.server.file_service.get_root_details(root_id))
            except ValueError as e:
                self._json(400, {"error": str(e)})
        elif path == "/api/files/search" and self.server.file_service:
            from urllib.parse import parse_qs
            try:
                qs = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                q = qs.get("q", [""])[0]
                root_id = (self._parse_file_id(qs["root_id"][0], "root_id")
                           if "root_id" in qs else None)
                self._json(200, self.server.file_service.search(q, root_id))
            except ValueError as e:
                self._json(400, {"error": str(e)})
        elif path == "/api/files/insights" and self.server.file_service:
            self._json(200, self.server.file_service.get_insights())
        elif path.startswith("/api/files/file/") and path.endswith("/image") and self.server.file_service:
            supplied_token = self.headers.get("X-Kshetrajna-Token", "")
            if not supplied_token.isascii() or not hmac.compare_digest(supplied_token, self.server.token):
                self._json(403, {"error": "Image preview requires the dashboard session"})
                return
            try:
                file_id = self._parse_file_id(path.split("/")[-2], "file_id")
                image = self.server.file_service.get_file_image(file_id)
                self._send(200, image["data"], image["content_type"], headers={
                    "X-Image-Width": image["width"], "X-Image-Height": image["height"],
                    "X-Image-Format": image["format"], "X-Image-Orientation": image["orientation"],
                })
            except (ValueError, OverflowError) as error:
                self._json(400, {"error": str(error)})
            except OSError:
                self._json(409, {"error": "The image is unavailable or could not be read"})
        elif path.startswith("/api/files/file/") and path.endswith("/pdf") and self.server.file_service:
            supplied_token = self.headers.get("X-Kshetrajna-Token", "")
            if not supplied_token.isascii() or not hmac.compare_digest(supplied_token, self.server.token):
                self._json(403, {"error": "PDF preview requires the dashboard session"})
                return
            try:
                file_id = self._parse_file_id(path.split("/")[-2], "file_id")
                pdf = self.server.file_service.get_file_pdf(file_id)
                self._send(200, pdf["data"], pdf["content_type"], headers={
                    "Content-Disposition": "inline",
                    "X-PDF-Pages": pdf["pages"] or "unknown",
                })
            except (ValueError, OverflowError) as error:
                self._json(400, {"error": str(error)})
            except OSError:
                self._json(409, {"error": "The PDF is unavailable or could not be read"})
        elif path.startswith("/api/files/file/") and path.endswith("/excerpt") and self.server.file_service:
            try:
                file_id = self._parse_file_id(path.split("/")[-2], "file_id")
                self._json(200, self.server.file_service.get_file_excerpt(file_id))
            except ValueError as e:
                self._json(400, {"error": str(e)})
        elif path == "/api/files/actions" and self.server.file_service:
            self._json(200, {"actions": self.server.file_service.get_action_journal()})
        elif path.startswith("/api/files/sort/plan/") and self.server.file_service:
            plan_id = path.rsplit("/", 1)[-1]
            if not plan_id or len(plan_id) > 100:
                self._json(400, {"error": "Invalid sort plan"})
            else:
                try:
                    self._json(200, self.server.file_service._sort_plan_view(
                        self.server.file_service._sort_plan(plan_id)))
                except ValueError as error:
                    self._json(404, {"error": str(error)})
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
        path = urlsplit(self.path).path
        limit = 12000 if path == "/api/chat/message" else 4096
        if length < 0 or length > limit or self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self._json(413, {"error": "Invalid request size"})
            return
        path = urlsplit(self.path).path
        service = self.server.service
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Request body must be a JSON object")
            if path == "/api/shutdown":
                if body.get("confirm") is not True or set(body) != {"confirm"}:
                    raise ValueError("Explicit shutdown confirmation is required")
                self._json(200, {"status": "shutting_down"})
                with self.server.shutdown_gate:
                    if not self.server.shutdown_requested:
                        self.server.shutdown_requested = True
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif path == "/api/settings":
                with self.server.collector._gate:
                    result = self.server.collector.update_settings(body)
                    service.tick()
                self._json(200, {"settings": asdict(result)})
            elif path == "/api/delete":
                service.delete_history()
                self.server.chat.clear()
                self._json(200, {"deleted": True})
            elif path == "/api/chat/status":
                self._json(200, self.server.chat.status(force=body.get("refresh") is True))
            elif path == "/api/chat/message":
                self._json(200, self.server.chat.start(body.get("message"), body.get("session_id")))
            elif path == "/api/chat/job":
                self._json(200, self.server.chat.job(body.get("id"), body.get("session_id")))
            elif path == "/api/chat/cancel":
                self._json(200, self.server.chat.cancel(body.get("id"), body.get("session_id")))
            elif path == "/api/chat/clear":
                self._json(200, self.server.chat.clear(body.get("session_id")))
            elif path == "/api/chat/unload":
                if body.get("confirm") is not True:
                    raise ValueError("Explicit confirmation is required")
                self._json(200, self.server.chat.unload_model(body.get("model")))
            elif path == "/api/processes/preview":
                self._json(200, {"plan": self.server.chat.processes.preview(body.get("pid"), body.get("action"))})
            elif path == "/api/processes/execute":
                self.server.chat.validate_session(body.get("session_id"))
                decision = self.server.chat.processes.execute(
                    body.get("id"), confirm=body.get("confirm"), typed_name=body.get("typed_name", ""))
                self.server.chat.record_outcome(body.get("session_id"), decision)
                self._json(200, {"decision": decision})
            elif path == "/api/maintenance/preview":
                self._json(200, {"plan": service.maintenance.preview(body.get("id"))})
            elif path == "/api/maintenance/execute":
                self.server.chat.validate_session(body.get("session_id"))
                decision = service.maintenance.execute(body.get("id"))
                self.server.chat.record_outcome(body.get("session_id"), decision)
                self._json(200, {"decision": decision})
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
                if body.get("consent") is not True:
                    raise ValueError("Folder access requires explicit consent")
                self._json(200, self.server.file_service.add_root(body.get("path"), body.get("label", "")))
            elif path == "/api/files/roots/rescan" and self.server.file_service:
                self.server.file_service.rescan_root(self._body_file_id(body, "root_id"))
                self._json(200, {"rescanned": True})
            elif path == "/api/files/roots/cancel-scan" and self.server.file_service:
                self.server.file_service.cancel_scan(self._body_file_id(body, "root_id"))
                self._json(200, {"cancelled": True})
            elif path == "/api/files/roots/remove" and self.server.file_service:
                self.server.file_service.remove_root(self._body_file_id(body, "root_id"))
                self._json(200, {"removed": True})
            elif path == "/api/files/delete-all" and self.server.file_service:
                self.server.file_service.delete_all()
                self._json(200, {"deleted": True})
            elif path == "/api/files/actions/propose" and self.server.file_service:
                self._json(200, self.server.file_service.create_proposal(
                    self._body_file_id(body, "file_id"), body.get("kind"), body.get("destination")))
            elif path == "/api/files/actions/apply" and self.server.file_service:
                result = self.server.file_service.apply_action(body.get("proposal_id"))
                self._json(200 if result.get("status") in ("applied", "deleted") else 409, result)
            elif path == "/api/files/actions/undo" and self.server.file_service:
                result = self.server.file_service.undo_action(body.get("action_id"))
                self._json(200 if result.get("status") == "rolled_back" else 409, result)
            elif path == "/api/files/actions/dismiss" and self.server.file_service:
                self.server.file_service.dismiss_proposal(body.get("proposal_id"))
                self._json(200, {"dismissed": True})
            elif path == "/api/files/organize" and self.server.file_service:
                result = self.server.file_service.organize_file(
                    self._body_file_id(body, "file_id"), confirm=body.get("confirm"))
                self._json(200 if result.get("status") in ("applied", "partial") else 409, result)
            elif path == "/api/files/sort/preview" and self.server.file_service:
                self._json(200, self.server.file_service.generate_sort_plan(
                    self._body_file_id(body, "root_id"), body.get("create_folders", False)))
            elif path == "/api/files/sort/update" and self.server.file_service:
                self._json(200, self.server.file_service.update_sort_plan(
                    body.get("plan_id"), body.get("suggestions")))
            elif path == "/api/files/sort/apply" and self.server.file_service:
                result = self.server.file_service.apply_sort_plan(body.get("plan_id"))
                self._json(200 if result.get("status") in ("applied", "partial") else 409, result)
            elif path == "/api/files/sort/approve-idea" and self.server.file_service:
                result = self.server.file_service.approve_sort_idea(
                    body.get("plan_id"), folder=body.get("folder"),
                    suggestion_id=body.get("suggestion_id"), confirm=body.get("confirm"))
                self._json(200 if result.get("status") in ("applied", "partial") else 409, result)
            elif path == "/api/files/sort/undo" and self.server.file_service:
                result = self.server.file_service.undo_sort_plan(body.get("plan_id"))
                self._json(200 if result.get("status") == "rolled_back" else 409, result)
            else:
                self._json(404, {"error": "Not found"})
        except (ValueError, TypeError) as error:
            self._json(400, {"error": str(error)})
        except OSError:
            self._json(409, {"error": "Windows could not complete this operation. The target may have exited or denied access."})
