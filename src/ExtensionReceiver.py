from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as HTTPServer
import json
import threading
import logging
import time

logger = logging.getLogger("OLED Customizer.ExtensionReceiver")

# A playing tab may go quiet for a while (ads, buffering); an idle tab is
# dropped much sooner so stale titles do not stick to the OLED.
STALE_PLAYING_SECONDS = 120
STALE_IDLE_SECONDS = 10

# Only browser extensions and loopback pages may talk to this server.
_ALLOWED_ORIGIN_PREFIXES = (
    "chrome-extension://",
    "moz-extension://",
    "safari-web-extension://",
    "edge-extension://",
)


class ExtensionData:
    def __init__(self):
        self.tabs = {}  # {tabId: {"data": data, "last_update": time}}
        self.discord_code = None
        self.oauth_state = None
        self.last_winner_id = None
        self._lock = threading.Lock()

    @staticmethod
    def _sanitize(new_data):
        """Coerce an incoming payload into a well-typed dict.
        Anything on localhost can POST here, so never trust field types.
        Returns None if the payload is not usable at all."""
        if not isinstance(new_data, dict):
            return None

        def _num(val):
            try:
                v = float(val)
                # NaN/inf would poison int() conversions downstream
                if v != v or v in (float("inf"), float("-inf")):
                    return 0.0
                return max(0.0, v)
            except (TypeError, ValueError):
                return 0.0

        return {
            "title": str(new_data.get("title") or ""),
            "artist": str(new_data.get("artist") or ""),
            "progress": _num(new_data.get("progress")),
            "duration": _num(new_data.get("duration")),
            "playing": new_data.get("playing") is True,
            "isFocused": new_data.get("isFocused") is True,
            "tabId": str(new_data.get("tabId") or "default"),
        }

    def update(self, new_data):
        data = self._sanitize(new_data)
        if data is None:
            return
        with self._lock:
            self.tabs[data["tabId"]] = {
                "data": data,
                "last_update": time.time()
            }

    def get_data(self):
        now = time.time()
        with self._lock:
            # 1. Cleanup stale tabs
            stale_keys = []
            for k, v in self.tabs.items():
                is_playing = v["data"].get("playing") is True
                limit = STALE_PLAYING_SECONDS if is_playing else STALE_IDLE_SECONDS
                if now - v["last_update"] > limit:
                    stale_keys.append(k)
            for k in stale_keys:
                if k == self.last_winner_id: self.last_winner_id = None
                del self.tabs[k]
            
            if not self.tabs:
                return None
            
            # 2. PRIORITY SELECTION
            # Prio 0: Visible + Playing
            # Prio 1: Playing (Background)
            # Prio 2: Visible (Paused)
            # Prio 3: Other
            
            tab_ids = list(self.tabs.keys())
            def get_prio(k):
                v = self.tabs[k]
                data = v["data"]
                is_playing = data.get("playing") is True
                is_visible = data.get("isFocused") is True # isFocused is document.visibilityState
                
                if is_playing and is_visible: return 0
                if is_playing: return 1
                if is_visible: return 2
                return 3

            tab_ids.sort(key=lambda k: (get_prio(k), -self.tabs[k]["last_update"]))
            best_id = tab_ids[0]
            
            # Stickiness: Stay with current winner if they are still playing and best is not 'visible'
            if self.last_winner_id in self.tabs:
                winner_v = self.tabs[self.last_winner_id]
                if winner_v["data"].get("playing") is True and get_prio(best_id) >= 1:
                    best_id = self.last_winner_id

            self.last_winner_id = best_id
            return self.tabs[best_id]["data"]

    def set_discord_code(self, code):
        with self._lock:
            self.discord_code = code
            # The OAuth state is single-use.
            self.oauth_state = None
            logger.info("Captured Discord Auth Code from localhost callback")

    def consume_discord_code(self):
        with self._lock:
            code = self.discord_code
            self.discord_code = None
            return code

    def set_oauth_state(self, state):
        with self._lock:
            self.oauth_state = state

    def verify_oauth_state(self, state) -> bool:
        """Check (and consume) the state generated for the authorize URL."""
        with self._lock:
            expected = self.oauth_state
            if expected is None:
                # No authorization flow is pending; reject stray callbacks.
                return False
            if state == expected:
                self.oauth_state = None
                return True
            return False

# Global storage instance
extension_storage = ExtensionData()

class ExtensionHandler(BaseHTTPRequestHandler):
    MAX_BODY_SIZE = 1_048_576  # 1 MB limit

    @staticmethod
    def _is_allowed_origin(origin, host):
        """Allow browser extensions and loopback pages only.

        Requests without an Origin header (local tools, curl) are not browser
        cross-site requests, so they are accepted. The important part is that a
        random website can no longer POST here through a victim's browser.
        """
        if not origin:
            return True
        if origin.startswith(_ALLOWED_ORIGIN_PREFIXES):
            return True
        return origin in (f"http://{host}", f"https://{host}")

    def _origin_allowed(self) -> bool:
        return self._is_allowed_origin(
            self.headers.get('Origin'), self.headers.get('Host', '')
        )

    def _send_cors_headers(self):
        origin = self.headers.get('Origin')
        if origin and self._is_allowed_origin(origin, self.headers.get('Host', '')):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Private-Network', 'true')

    def do_POST(self):
        if not self._origin_allowed():
            logger.warning("Rejected extension POST from origin %s", self.headers.get('Origin'))
            self.send_response(403)
            self.end_headers()
            return

        if self.path == '/extension_data':
            raw = self.headers.get('Content-Length', '0')
            try:
                content_length = int(raw)
            except (ValueError, TypeError):
                content_length = -1

            if content_length < 0:
                self.send_response(400)
                self._send_cors_headers()
                self.end_headers()
                return

            if content_length > self.MAX_BODY_SIZE:
                self.send_response(413)
                self._send_cors_headers()
                self.end_headers()
                return

            post_data = self.rfile.read(content_length)

            try:
                data = json.loads(post_data.decode('utf-8'))
                logger.debug(
                    "Extension payload -> title=%r playing=%s focused=%s",
                    data.get('title'), data.get('playing'), data.get('isFocused'),
                )
                extension_storage.update(data)
                self.send_response(200)
            except Exception as e:
                logger.error(f"Failed to parse extension data: {e}")
                self.send_response(400)

            self._send_cors_headers()
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        # Handle Discord OAuth Redirect: http://localhost:PORT/?code=XYZ&state=...
        from urllib.parse import urlparse, parse_qs
        query = urlparse(self.path).query
        params = parse_qs(query)

        if 'code' in params:
            state = params.get('state', [None])[0]
            if not extension_storage.verify_oauth_state(state):
                logger.warning("Rejected Discord callback with missing/invalid OAuth state")
                self.send_response(400)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(
                    b"<html><body style='background:#1f1f1f;color:white;font-family:sans-serif;"
                    b"text-align:center;padding-top:50px;'><h1>Invalid OAuth state</h1>"
                    b"<p>Close this window and start the connection again from the app.</p>"
                    b"</body></html>"
                )
                self.wfile.flush()
                return

            code = params['code'][0]
            extension_storage.set_discord_code(code)

            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(b"<html><body style='background:#1f1f1f;color:white;font-family:sans-serif;text-align:center;padding-top:50px;'>")
            self.wfile.write(b"<h1>Discord Connected!</h1><p>You can close this window and go back to App.</p></body></html>")
            self.wfile.flush()
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        if not self._origin_allowed():
            self.send_response(403)
            self.end_headers()
            return
        # Handle CORS preflight
        self.send_response(204)
        self._send_cors_headers()
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        # Suppress server logs
        return

class ExtensionReceiver:
    def __init__(self, port=2408, host='127.0.0.1'):
        self.port = port
        self.host = host
        self.server = None
        self.thread = None
        # Set once serve_forever() is actually running. Calling shutdown()
        # before that point blocks forever, so stop() checks this flag.
        self._serving = threading.Event()

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return

        def run_server():
            try:
                server = HTTPServer((self.host, self.port), ExtensionHandler)
            except OSError as e:
                logger.error(
                    "Extension Receiver could not bind %s:%s (%s). "
                    "Discord OAuth and browser media sync will be unavailable.",
                    self.host, self.port, e,
                )
                return

            self.server = server
            self._serving.set()
            logger.info(f"Extension Receiver listening on {self.host}:{self.port}")
            try:
                server.serve_forever(poll_interval=0.25)
            finally:
                self._serving.clear()
                try:
                    server.server_close()
                except Exception:
                    pass
                if self.server is server:
                    self.server = None
                logger.info("Extension Receiver stopped")

        self.thread = threading.Thread(target=run_server, daemon=True, name="Extension-Receiver")
        self.thread.start()

    def stop(self):
        server = self.server
        if server is None:
            return
        logger.info("Extension Receiver: Stopping...")
        self.server = None
        # shutdown() must run from a different thread than serve_forever().
        # It can block briefly, so bound the wait and always close the socket.
        if self._serving.is_set():
            stopper = threading.Thread(target=server.shutdown, daemon=True, name="Extension-Stop")
            stopper.start()
            stopper.join(timeout=2.0)
        try:
            server.server_close()
        except Exception:
            pass

    def get_latest_data(self):
        return extension_storage.get_data()

    def get_discord_code(self):
        return extension_storage.consume_discord_code()
