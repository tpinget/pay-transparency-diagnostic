"""
EU Pay Transparency Diagnostic — Local development server
Reads ANTHROPIC_API_KEY from .env, proxies Claude API calls.
Supports both standard JSON responses and SSE streaming.
The API key never leaves this server — the browser never sees it.

Usage:
    pip install anthropic
    python serve.py
    → http://localhost:8080

Routes:
    GET  /              → index.html
    GET  /api/config    → { has_key: bool, model: str }
    POST /api/diagnostic  → proxy to Anthropic (JSON response)
    POST /api/scope       → proxy to Anthropic (JSON response, supports tools)
    POST /api/stream      → proxy to Anthropic (SSE streaming)
    GET  /locales/*.json  → i18n locale files
    GET  /*             → static files from BASE_DIR
"""

import os
import json
import mimetypes
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen, Request as URLRequest
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse

# ─── Load .env if present ───────────────────────────────────────
ENV_FILE = Path(__file__).parent / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

API_KEY           = os.environ.get("ANTHROPIC_API_KEY", "")
HOST              = os.environ.get("HOST", "localhost")
PORT              = int(os.environ.get("PORT", "8080"))
BASE_DIR          = Path(__file__).parent
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL     = "claude-sonnet-4-6"


# ─── Request handler ─────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        """Compact log — method, path, status only."""
        path = args[0].split()[1] if args else "?"
        print(f"  {self.command} {path} → {args[1]}")

    # ── Route dispatcher ─────────────────────────────────────────
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/config":
            self._config()
        elif path in ("/", ""):
            self._serve_file("index.html")
        else:
            target = BASE_DIR / path.lstrip("/")
            if target.exists() and target.is_file():
                self._serve_file(path.lstrip("/"))
            else:
                self._not_found()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/diagnostic":
            self._diagnostic()
        elif path == "/api/scope":
            self._diagnostic()
        elif path == "/api/stream":
            self._stream()
        else:
            self._not_found()

    def do_OPTIONS(self):
        """CORS preflight."""
        self._cors_headers(200)
        self.end_headers()

    # ── /api/config ──────────────────────────────────────────────
    def _config(self):
        """
        Reports whether a server-side API key is configured.
        Never exposes the key itself.
        """
        payload = json.dumps({
            "has_key": bool(API_KEY),
            "model":   DEFAULT_MODEL,
        }).encode()
        self._cors_headers(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # ── /api/diagnostic — standard JSON proxy ────────────────────
    def _diagnostic(self):
        """
        Receives diagnostic payload, injects API key, forwards to Anthropic.
        Returns a standard JSON response (full report generation).

        Also handles /api/scope: the browser sends the same shape of request
        (model, max_tokens, system, messages) plus a `tools` array enabling
        web_search, used to resolve the EU Directive 2023/970 transposition
        status and deadline for a country/headcount pair. Both routes are
        generic JSON request/response proxies, so they share this handler.
        """
        if not API_KEY:
            self._error(503, "ANTHROPIC_API_KEY not configured on the server.")
            return

        body = self._read_body()
        if body is None:
            return

        if "messages" not in body or "system" not in body:
            self._error(400, "Missing required fields: messages, system.")
            return

        payload = {
            "model":      body.get("model", DEFAULT_MODEL),
            "max_tokens": body.get("max_tokens", 1500),
            "system":     body["system"],
            "messages":   body["messages"],
        }
        # Forwarded only when present (e.g. the web_search tool used by
        # /api/scope) — /api/diagnostic's report generation doesn't need them.
        if "tools" in body:
            payload["tools"] = body["tools"]
        if "tool_choice" in body:
            payload["tool_choice"] = body["tool_choice"]

        anthropic_payload = json.dumps(payload).encode()

        req = URLRequest(
            ANTHROPIC_API_URL,
            data=anthropic_payload,
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         API_KEY,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            method="POST",
        )

        try:
            # 90s (vs. the original 60s) to leave room for /api/scope calls,
            # which may run several web searches before answering.
            with urlopen(req, timeout=90) as resp:
                response_body = resp.read()
            self._cors_headers(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        except HTTPError as e:
            error_body = e.read()
            self._cors_headers(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error_body)))
            self.end_headers()
            self.wfile.write(error_body)
        except URLError as e:
            self._error(502, f"Could not reach Anthropic API: {e.reason}")
        except TimeoutError:
            self._error(504, "Anthropic API call timed out after 60 seconds.")

    # ── /api/stream — SSE streaming proxy ────────────────────────
    def _stream(self):
        """
        Streaming proxy using Server-Sent Events (SSE).
        Used for dimension-level adaptive analysis — streams Claude's
        response token by token to the browser.

        SSE format:
            data: {"type": "text_delta", "text": "..."}\n\n
            data: {"type": "done"}\n\n
            data: {"type": "error", "message": "..."}\n\n
        """
        if not API_KEY:
            self._sse_error("ANTHROPIC_API_KEY not configured on the server.")
            return

        body = self._read_body()
        if body is None:
            return

        if "messages" not in body or "system" not in body:
            self._sse_error("Missing required fields: messages, system.")
            return

        # Build streaming request to Anthropic
        anthropic_payload = json.dumps({
            "model":      body.get("model", DEFAULT_MODEL),
            "max_tokens": body.get("max_tokens", 800),
            "system":     body["system"],
            "messages":   body["messages"],
            "stream":     True,   # enable SSE streaming
        }).encode()

        req = URLRequest(
            ANTHROPIC_API_URL,
            data=anthropic_payload,
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         API_KEY,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            method="POST",
        )

        # Open SSE response headers before connecting to Anthropic
        self._cors_headers(200)
        self.send_header("Content-Type",  "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")  # disable nginx buffering if present
        self.end_headers()

        try:
            with urlopen(req, timeout=90) as resp:
                # Forward Anthropic SSE stream line by line to the browser
                for raw_line in resp:
                    line = raw_line.decode("utf-8").rstrip("\n\r")

                    if not line.startswith("data:"):
                        continue

                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")

                    # Forward text deltas — the actual streamed tokens
                    if event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            sse = json.dumps({"type": "text_delta", "text": text})
                            self._sse_write(sse)

                    # Signal stream completion
                    elif event_type == "message_stop":
                        self._sse_write(json.dumps({"type": "done"}))
                        break

                    # Forward usage stats if present
                    elif event_type == "message_delta":
                        usage = event.get("usage", {})
                        if usage:
                            self._sse_write(json.dumps({"type": "usage", "usage": usage}))

        except HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            self._sse_write(json.dumps({"type": "error", "message": f"API error {e.code}: {err[:200]}"}))
        except URLError as e:
            self._sse_write(json.dumps({"type": "error", "message": f"Connection error: {e.reason}"}))
        except (BrokenPipeError, ConnectionResetError):
            # Browser closed the connection — normal for user navigation
            pass
        except TimeoutError:
            self._sse_write(json.dumps({"type": "error", "message": "Stream timed out after 90 seconds."}))

    # ── Static file serving ───────────────────────────────────────
    def _serve_file(self, filename):
        target = BASE_DIR / filename
        if not target.exists():
            self._not_found()
            return
        mime, _ = mimetypes.guess_type(str(target))
        data = target.read_bytes()
        self._cors_headers(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── Helpers ───────────────────────────────────────────────────
    def _read_body(self):
        """Read and parse JSON request body. Returns None and sends 400 on error."""
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            self._error(400, "Invalid JSON payload.")
            return None

    def _cors_headers(self, code):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _sse_write(self, data_str):
        """Write a single SSE event to the response stream."""
        try:
            msg = f"data: {data_str}\n\n".encode("utf-8")
            self.wfile.write(msg)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _sse_error(self, message):
        """Send an SSE error event with proper headers."""
        self._cors_headers(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self._sse_write(json.dumps({"type": "error", "message": message}))

    def _error(self, code, message):
        payload = json.dumps({"error": message}).encode()
        self._cors_headers(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _not_found(self):
        self._error(404, f"Not found: {self.path}")


# ─── Entry point ─────────────────────────────────────────────────
if __name__ == "__main__":
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("application/json", ".json")

    key_status = (
        "✓ API key loaded from environment"
        if API_KEY else
        "⚠  No API key — set ANTHROPIC_API_KEY in .env"
    )

    print()
    print("  EU Pay Transparency Diagnostic")
    print("  ─────────────────────────────────────────")
    print(f"  {key_status}")
    print(f"  Server  : http://{HOST}:{PORT}")
    print(f"  Routes  : /api/config  /api/diagnostic  /api/scope  /api/stream")
    print(f"  Press Ctrl+C to stop")
    print()

    # ThreadingHTTPServer: HTTPServer is single-threaded, so while it's
    # blocked inside urlopen() on a slow /api/scope web-search call, any
    # concurrent /api/stream request queues behind it on the same socket —
    # this was the cause of the first dimension's stream taking ~26s longer
    # than the rest. Threading lets /api/stream proceed independently.
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
        server.server_close()
