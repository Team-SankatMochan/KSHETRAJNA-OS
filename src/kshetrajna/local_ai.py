"""Bounded Ollama client. Fixed loopback address, no cloud models or downloads."""

import http.client
import json
import socket
import threading
import time


class LocalAIError(ValueError):
    pass


class OllamaClient:
    host, port = "127.0.0.1", 11434
    tool_planning = True

    def __init__(self):
        self._lock = threading.Lock()
        self._active = {}

    def cancel(self, event):
        event.set()
        with self._lock:
            connection = self._active.get(event)
        if connection and connection.sock:
            try:
                connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _json(self, method, path, body=None, *, timeout=5):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            connection.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read(1048577)
            if response.status != 200 or len(data) > 1048576:
                raise LocalAIError("Local model server rejected the request")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise LocalAIError("Malformed local model response")
            return result
        except (OSError, http.client.HTTPException, json.JSONDecodeError) as error:
            raise LocalAIError("Ollama is unavailable. Start Ollama locally, then refresh models.") from error
        finally:
            connection.close()

    def models(self):
        data = self._json("GET", "/api/tags")
        models = data.get("models")
        if not isinstance(models, list):
            raise LocalAIError("Invalid local model list")
        result = []
        for model in models[:100]:
            name = model.get("name") if isinstance(model, dict) else None
            if (isinstance(name, str) and 0 < len(name) <= 150 and name.isascii() and
                    "cloud" not in name.casefold() and not model.get("remote_host")):
                result.append({"name": name, "size_bytes": model.get("size", 0)})
        return sorted(result, key=lambda m: m["name"])

    def validate_model(self, name):
        if name not in {m["name"] for m in self.models()}:
            raise LocalAIError("Choose an installed local model; cloud models are unavailable")
        details = self._json("POST", "/api/show", {"model": name})
        if details.get("remote_host") or details.get("remote_model"):
            raise LocalAIError("Remote model providers are not permitted")
        return details

    def stream(self, model, messages, cancel, *, tool_callback=None):
        if cancel.is_set():
            return
        details = self.validate_model(model)
        if cancel.is_set():
            return
        connection = http.client.HTTPConnection(self.host, self.port, timeout=90)
        with self._lock:
            self._active[cancel] = connection
        body = {"model": model, "messages": messages, "stream": True, "keep_alive": "1m",
                "options": {"num_ctx": 4096, "num_predict": 384, "temperature": 0.3}}
        if tool_callback and "tools" in details.get("capabilities", []):
            body["tools"] = [{"type": "function", "function": {
                "name": "review_pc", "description": "Inspect this PC or prepare review cards for a user's system request. This tool never executes changes. Do not call it for general educational questions.",
                "parameters": {"type": "object", "properties": {"intent": {"type": "string", "enum": [
                    "memory_review", "idle_review", "system_review", "temp_cleanup", "disk_cleanup", "store_cache", "sort_files"]}},
                    "required": ["intent"], "additionalProperties": False}}}]
        try:
            payload = json.dumps(body).encode()
            if cancel.is_set():
                return
            connection.request("POST", "/api/chat", body=payload,
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise LocalAIError("The local model could not start this conversation")
            started, received, done = time.monotonic(), 0, False
            while not cancel.is_set() and time.monotonic() - started < 180:
                line = response.readline(65537)
                if not line:
                    break
                received += len(line)
                if len(line) > 65536 or received > 1048576:
                    raise LocalAIError("Local model response exceeded the limit")
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LocalAIError("Malformed response from local model") from error
                if not isinstance(item, dict) or not isinstance(item.get("message", {}), dict):
                    raise LocalAIError("Malformed local model response")
                if item.get("error"):
                    raise LocalAIError("Local model reported an inference error")
                calls = item.get("message", {}).get("tool_calls", [])
                if calls and tool_callback:
                    if not isinstance(calls, list) or len(calls) > 4:
                        raise LocalAIError("Invalid local tool request")
                    for call in calls:
                        tool_callback(call)
                fragment = item.get("message", {}).get("content", "")
                if not isinstance(fragment, str):
                    raise LocalAIError("Malformed local model message")
                if fragment:
                    yield fragment
                if item.get("done"):
                    done = True
                    break
            if not done and not cancel.is_set():
                raise LocalAIError("Local model response timed out or ended early")
        except (OSError, http.client.HTTPException) as error:
            raise LocalAIError("Local model connection was interrupted. Try a smaller installed model.") from error
        finally:
            with self._lock:
                self._active.pop(cancel, None)
            connection.close()

    def unload(self, model):
        self.validate_model(model)
        self._json("POST", "/api/generate", {"model": model, "keep_alive": 0, "stream": False}, timeout=20)
        return {"status": "completed", "message": "Asked Ollama to unload this model. Other models or applications may still use memory."}
