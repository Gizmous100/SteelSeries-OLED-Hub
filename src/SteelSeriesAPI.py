from json import loads, dumps
from time import sleep, time
import gc
from os import environ, path
import logging
import urllib3

GAME = "STEELSERIES_OLED_HUB"
GAME_DISPLAY_NAME = "SteelSeries OLED & RGB Hub"
AUTHOR = "gizmous (orig. 0z-zy)"
EVENT = "UPDATE"

logger = logging.getLogger("OLED Customizer.SteelSeriesAPI")


class SteelSeriesAPI:
    def __init__(self, initial_rgb=None, initial_active=True):
        programdata = environ.get("PROGRAMDATA") or r"C:\ProgramData"
        self.coreprops_paths = [
            path.join(programdata, "SteelSeries", "GG", "coreProps.json"),
            path.join(programdata, "SteelSeries", "SteelSeries Engine 3", "coreProps.json"),
            path.join(programdata, "SteelSeries", "SteelSeries GG", "coreProps.json"),
        ]
        self.address = ""
        
        # Use urllib3 PoolManager directly for maximum performance and minimal object churn.
        # requests.Session creates many heavy objects (Headers, CaseInsensitiveDict, etc.)
        # for every single request, which can lead to GC crashes at 10+ FPS.
        self._http = urllib3.PoolManager(
            num_pools=1,
            headers={"Content-Type": "application/json", "Connection": "keep-alive"},
            timeout=urllib3.Timeout(connect=1.0, read=3.0),
            retries=False
        )
        
        # Thread lock to prevent concurrent sends
        import threading
        self._send_lock = threading.Lock()
        
        # Error back-off (escalating: 2s → 5s → 10s)
        self._consecutive_errors = 0
        self._backoff_until = 0
        self._backoff_level = 0          # 0=2s, 1=5s, 2=10s
        self._BACKOFF_DURATIONS = [2.0, 5.0, 10.0]
        self._RESET_THRESHOLD = 10       # Force re-register after this many consecutive errors
        self._is_resetting = False      # Prevent overlapping resets
        if initial_rgb and len(initial_rgb) == 3:
            self._current_color = (int(initial_rgb[0]), int(initial_rgb[1]), int(initial_rgb[2]))
        else:
            self._current_color = (255, 255, 255)
        self._lighting_active = bool(initial_active)
        
        # Pre-allocate static headers to reduce object churn
        self._static_headers = {
            "Content-Type": "application/json",
            "Connection": "keep-alive"
        }
        
        # Frame counter for periodic manual GC collection
        self._frames_sent = 0
        
        # Track last successful API response (used by DisplayManager to detect hung GG)
        self._last_success_time = time()
        
        # Bounded: never loop forever in the constructor. If GG isn't up yet,
        # DisplayManager's run loop re-registers once it detects GG running.
        self.retrieve_address(retries=2)

    def retrieve_address(self, retries=None):
        """
        Locate coreProps.json and register with the GameSense API.
        If retries is None, loops forever.
        Otherwise attempts specified number of retries before giving up.
        """
        attempt = 0
        while True:
            try:
                coreprops = None
                for p in self.coreprops_paths:
                    if path.exists(p):
                        coreprops = p
                        break

                if not coreprops:
                    raise OSError("coreProps.json not found (Engine/GG not running?)")

                with open(coreprops, "r", encoding="utf-8") as f:
                    data = loads(f.readline())
                    self.address = "http://" + data["address"]

                # Clean start: register and bind
                self.register_game()
                self.bind_game_event()

                logger.info("Found local address API : %s", self.address)
                return self.address
            except Exception as e:
                attempt += 1
                if retries is not None and attempt >= retries:
                    logger.error("Failed to connect/register to SteelSeries GameSense API after %d attempts: %s", attempt, e)
                    return None
                    
                logger.error("Could not connect/register to SteelSeries GameSense API (%s). Retry in 5s...", e)
                sleep(5)

    def reset(self):
        """Invalidate current connection and force re-registration."""
        if self._is_resetting:
            return
        self._is_resetting = True
        try:
            logger.info("Resetting SteelSeries connection...")
            self.address = ""
            # Limit retries during resets to avoid blocking the main thread forever
            self.retrieve_address(retries=2)
        finally:
            self._is_resetting = False

    def bind_game_event(self, rgb_color=None):
        # Apex 7 Pro OLED = 128x40 (640 byte)
        dummy_128x40 = [0 for _ in range(640)]

        # 1. Screen handler for OLED
        update_handlers = [
            {
                "device-type": "screened-128x40",
                "mode": "screen",
                "datas": [
                    {"has-text": False, "image-data": dummy_128x40}
                ]
            }
        ]

        self.send_data("/bind_game_event", {
            "game": GAME,
            "event": EVENT,
            "value_optional": True,
            "handlers": update_handlers
        })

        # 2. Lighting handler for Keyboard and Mouse (Rival 5 / multi-zone mice)
        # Value 0 = AFK (blackout [0,0,0])
        # Value 1 = Active (user color [r,g,b])
        if rgb_color is not None:
            self._current_color = tuple(int(c) for c in rgb_color)
        r, g, b = self._current_color if self._current_color is not None else (255, 255, 255)

        color_ranges = [
            {"low": 0, "high": 0, "color": {"red": 0, "green": 0, "blue": 0}},
            {"low": 1, "high": 1, "color": {"red": r, "green": g, "blue": b}}
        ]

        lighting_handlers = [
            {
                "device-type": "rgb-per-key-zones",
                "zone": "all",
                "mode": "color",
                "color": color_ranges
            }
        ]

        # Multi-zone RGB devices (e.g. Rival 5 uses rgb-10-zone 'one'..'ten')
        for zone_name in ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"):
            lighting_handlers.append({
                "device-type": "rgb-zoned-device",
                "zone": zone_name,
                "mode": "color",
                "color": color_ranges
            })

        # Legacy 2-zone mouse specifiers
        for mouse_zone in ("wheel", "logo"):
            lighting_handlers.append({
                "device-type": "mouse",
                "zone": mouse_zone,
                "mode": "color",
                "color": color_ranges
            })

        self.send_data("/bind_game_event", {
            "game": GAME,
            "event": "LIGHTING",
            "min_value": 0,
            "max_value": 1,
            "value_optional": True,
            "handlers": lighting_handlers
        })

        # Immediately apply current lighting state so GameSense never leaves the mouse on default orange
        self.set_lighting(getattr(self, "_lighting_active", True))

        logger.info("Bound game events: UPDATE (OLED) and LIGHTING (Keyboard + Mouse RGB [%d, %d, %d])", r, g, b)

    def send_frame(self, image_128x40):
        if not isinstance(image_128x40, (list, bytes, bytearray)):
            raise ValueError("Image must be a list or bytes")

        # Convert to list for JSON - pad/trim to exactly 640 bytes
        img_list = list(image_128x40[:640])
        img40 = img_list + [0] * max(0, 640 - len(img_list))

        return self.send_data("/game_event", {
            "game": GAME,
            "event": EVENT,
            "data": {
                "value": 100,
                "frame": {"image-data-128x40": img40}
            }
        })

    def set_lighting(self, active: bool):
        """Send lighting event: value 1 (user color) or value 0 (AFK blackout)."""
        val = 1 if active else 0
        self._lighting_active = bool(active)
        return self.send_data("/game_event", {
            "game": GAME,
            "event": "LIGHTING",
            "data": {"value": val}
        })

    def update_rgb_color(self, r, g, b):
        """Update active color mapping if it changed."""
        new_color = (int(r), int(g), int(b))
        if getattr(self, "_current_color", None) != new_color:
            self._current_color = new_color
            if self.address:
                self.bind_game_event(rgb_color=new_color)

    def send_rgb(self, r, g, b):
        self.update_rgb_color(r, g, b)

    def clear_rgb(self):
        self.set_lighting(False)

    def set_mouse_afk(self, active: bool):
        self.set_lighting(not active)

    def remove_game(self):
        try:
            self.send_data("/remove_game", {"game": GAME}, ignore_errors=True)
        except Exception:
            pass

    def _stop_conflicting_gg_apps(self) -> bool:
        """
        Suspend built-in SteelSeries now-playing.exe if running to prevent OLED screen conflicts.
        We suspend rather than terminate because SteelSeriesEngine3.exe has a process watchdog
        that immediately respawns now-playing.exe if it exits.
        """
        newly_suspended = False
        try:
            import psutil
            if not hasattr(self, "_suspended_pids"):
                self._suspended_pids = set()
            for proc in psutil.process_iter(["pid", "name", "status"]):
                try:
                    pname = (proc.info.get("name") or "").lower()
                    pid = proc.info.get("pid")
                    if pname in ("now-playing.exe", "now-playing"):
                        if pid not in self._suspended_pids or proc.info.get("status") != psutil.STATUS_STOPPED:
                            logger.info("Suspending conflicting SteelSeries app %s (PID %d)", pname, pid)
                            proc.suspend()
                            self._suspended_pids.add(pid)
                            newly_suspended = True
                except Exception:
                    pass
        except Exception:
            pass
        return newly_suspended

    def register_game(self):
        self._stop_conflicting_gg_apps()

        # Clean up legacy and conflicting game entries if they exist in SteelSeries GG
        for legacy in ["OLED_CUSTOMIZER", "OLED_CUSTOMIZER_V1", "OLED_CUSTOMIZER_V2", "OLED_CUSTOMIZER_V3", "NOW_PLAYING"]:
            try:
                self.send_data("/remove_game", {"game": legacy}, ignore_errors=True)
            except Exception:
                pass

        self.send_data("/game_metadata", {
            "game": GAME,
            "game_display_name": GAME_DISPLAY_NAME,
            "developer": AUTHOR,
            "deinitialize_timer_length_ms": 60000 
        })

    def heartbeat(self):
        """Send a lightweight heartbeat to keep the game registered."""
        if self._stop_conflicting_gg_apps():
            try:
                self.send_data("/remove_game", {"game": "NOW_PLAYING"}, ignore_errors=True)
            except Exception:
                pass
        try:
            self.send_data("/game_heartbeat", {"game": GAME})
        except Exception:
            pass

    def send_data(self, endpoint, data, ignore_errors=False):
        if not self.address:
            return False

        # Error back-off check (do not drop critical registration/binding calls when ignore_errors is set)
        if not ignore_errors and self._consecutive_errors >= 5:
            now = time()
            if now < self._backoff_until:
                return False
            else:
                # Proceed but DON'T reset error counter yet — wait for success
                if now - self._backoff_until > 0.1: # Only log once per backoff
                   logger.debug("Back-off expired, testing SteelSeries API connection...")

        # Minimize object creation: encode JSON once outside the loop
        body = dumps(data).encode('utf-8')
        url = self.address + endpoint
        success = False

        if self._send_lock.acquire(timeout=0.5):
            try:
                headers = self._static_headers.copy()
                headers["Content-Length"] = str(len(body))
                
                response = self._http.request(
                    "POST",
                    url,
                    body=body,
                    headers=headers
                )
                
                # IMPORTANT: In urllib3, you must read the response data to 
                # release the connection back to the pool.
                resp_data = response.data
                
                if response.status == 200:
                    self._consecutive_errors = 0
                    self._backoff_level = 0
                    self._last_success_time = time()
                    success = True
                elif not ignore_errors:
                    logger.debug("SteelSeries API error %d on %s: %s", response.status, endpoint, resp_data.decode('utf-8', 'ignore'))
                    self._consecutive_errors += 1
            except Exception as e:
                if not ignore_errors:
                    self._consecutive_errors += 1
                    logger.debug("SteelSeries API transport error on %s: %s", endpoint, e)
            finally:
                self._send_lock.release()
        else:
            logger.warning("SteelSeries API send lock TIMEOUT (0.5s)")

        if ignore_errors:
            return success

        # Auto-recovery: if errors are persistent, force a full re-registration
        if self._consecutive_errors >= self._RESET_THRESHOLD:
            logger.warning("Persistent API errors (%d), forcing re-registration...", self._consecutive_errors)
            # Reset counter so we don't loop reset() if it fails internally
            self._consecutive_errors = 0
            self._backoff_level = 0
            try:
                self.reset()
            except Exception as e:
                logger.error("Auto-reset failed: %s", e)
        # Standard backoff with escalation
        elif self._consecutive_errors >= 5:
            # Clamp level
            idx = min(self._backoff_level, len(self._BACKOFF_DURATIONS) - 1)
            duration = self._BACKOFF_DURATIONS[idx]
            self._backoff_until = time() + duration
            # Increment level for next time (escalation)
            self._backoff_level += 1
            logger.warning("Too many API errors (%d), backing off for %.0fs", self._consecutive_errors, duration)

        return success
