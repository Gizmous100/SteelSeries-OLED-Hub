from json import loads, dumps
from time import sleep, time
import gc
from os import environ, path
import logging
import ssl
import urllib.request
import urllib3

GAME = "STEELSERIES_OLED_HUB"
GAME_DISPLAY_NAME = "SteelSeries OLED & RGB Hub"
AFK_GAME = "STEELSERIES_AFK_RGB"
AFK_GAME_DISPLAY_NAME = "SteelSeries OLED Hub (AFK Blackout)"
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
        self.encrypted_address = ""
        self.gg_encrypted_address = ""
        
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
        self._lighting_active = True
        self._lighting_bound = False
        self._last_lighting_release_ts = 0.0
        self._cleaned_main_game = False
        self._afk_saved_device_configs = {}
        self._afk_saved_mouse_configs = self._afk_saved_device_configs
        
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
                    enc_addr = data.get("encryptedAddress")
                    self.encrypted_address = ("https://" + enc_addr) if enc_addr else ""
                    gg_enc_addr = data.get("ggEncryptedAddress")
                    self.gg_encrypted_address = ("https://" + gg_enc_addr) if gg_enc_addr else ""

                # Clean start: register and bind
                self._lighting_bound = False
                self._last_lighting_release_ts = 0.0
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
            self.encrypted_address = ""
            self.gg_encrypted_address = ""
            self._lighting_bound = False
            self._last_lighting_release_ts = 0.0
            self._afk_saved_device_configs = {}
            self._afk_saved_mouse_configs = self._afk_saved_device_configs
            # Limit retries during resets to avoid blocking the main thread forever
            self.retrieve_address(retries=2)
        finally:
            self._is_resetting = False

    def _redeploy_engine_active_configs(self):
        """
        Force-redeploy currently active configurations on all connected SteelSeries
        devices via encryptedAddress (/configuration/<id>/forceDeploy).
        Non-destructive: does not modify or overwrite device settings in the database.
        """
        if not self.encrypted_address:
            return
        try:
            import ssl
            import urllib.request
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(f"{self.encrypted_address}/v1/connectedDevices")
            with urllib.request.urlopen(req, context=ctx, timeout=1.5) as resp:
                payload = loads(resp.read().decode("utf-8"))
            for dev in payload.get("devices", []):
                if not dev.get("supports_prism_sync") and dev.get("type") not in (0, 1):
                    continue
                cfgs = dev.get("configuration_metadata") or []
                active_cfg = (
                    next((c for c in cfgs if c.get("active") == 1 and c.get("last_deployed") == 1), None)
                    or next((c for c in cfgs if c.get("active") == 1), None)
                    or next((c for c in cfgs if c.get("last_deployed") == 1), None)
                )
                if active_cfg and active_cfg.get("id"):
                    active_cid = active_cfg["id"]
                    deploy_req = urllib.request.Request(
                        f"{self.encrypted_address}/configuration/{active_cid}/forceDeploy",
                        data=b"",
                        method="POST",
                    )
                    with urllib.request.urlopen(deploy_req, context=ctx, timeout=1.5) as _:
                        pass
                    logger.debug(
                        "Redeployed active Engine config '%s' (%s) on %s",
                        active_cfg.get("name"),
                        active_cid,
                        dev.get("name"),
                    )
        except Exception as e:
            logger.debug("Engine active config redeploy skipped/failed: %s", e)

    def _bind_lighting_event(self) -> bool:
        """
        Register the dedicated AFK_GAME and bind its LIGHTING event for Keyboard,
        Mouse, and RGB zones during AFK blackout using official GameSense API:
        - For per-key RGB keyboards (Apex 7, Apex Pro, etc.), GameSense requires
          mode: "bitmap" when targeting zone: "all".
        - For mice (Rival 5, etc.) and RGB zones, targets exact hardware zones
          with static color [0, 0, 0] to avoid default orange fallbacks.
        - Bound once at startup; toggled strictly via /game_event and /stop_game
          without repetitive register/remove churn (prevents HTTP 429).
        """
        self.send_data("/game_metadata", {
            "game": AFK_GAME,
            "game_display_name": AFK_GAME_DISPLAY_NAME,
            "developer": AUTHOR,
            "deinitialize_timer_length_ms": 60000,
            "options": {"enabled": True}
        }, ignore_errors=True)

        black = {"red": 0, "green": 0, "blue": 0}

        lighting_handlers = [
            # For per-key RGB keyboards (Apex 7, Apex Pro, etc.)
            {
                "device-type": "rgb-per-key-zones",
                "zone": "all",
                "mode": "bitmap"
            },
            # Mouse standard zones (wheel, logo)
            {
                "device-type": "mouse",
                "zone": "wheel",
                "mode": "color",
                "color": black
            },
            {
                "device-type": "mouse",
                "zone": "logo",
                "mode": "color",
                "color": black
            },
        ]

        # Multi-zone mouse specific zones (Rival 5 10 zones, Rival 600 8 zones, etc.)
        for z in ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"):
            lighting_handlers.append({
                "device-type": "rgb-10-zone",
                "zone": z,
                "mode": "color",
                "color": black
            })
            lighting_handlers.append({
                "device-type": "rgb-zoned-device",
                "zone": z,
                "mode": "color",
                "color": black
            })

        # Other zoned hardware (headsets, single/dual zone mice, mousepads)
        for general_type in ("rgb-1-zone", "rgb-2-zone", "rgb-3-zone", "rgb-5-zone", "rgb-8-zone", "rgb-12-zone"):
            lighting_handlers.append({
                "device-type": general_type,
                "zone": "one",
                "mode": "color",
                "color": black
            })

        ok = self.send_data("/bind_game_event", {
            "game": AFK_GAME,
            "event": "LIGHTING",
            "value_optional": True,
            "handlers": lighting_handlers
        })
        self._lighting_bound = bool(ok)
        if ok:
            # Stop session immediately so normal desktop profile is active until AFK
            self.send_data("/stop_game", {"game": AFK_GAME}, ignore_errors=True)
            logger.info("Bound LIGHTING event on %s for dual-device AFK blackout [0, 0, 0]", AFK_GAME)
        return bool(ok)

    def _blackout_device_configs(self) -> bool:
        """Deprecated: in-database configuration mutation removed in favor of GameSense API."""
        return True

    def _blackout_mouse_configs(self) -> bool:
        """Deprecated backward-compatible alias."""
        return True

    def _restore_device_configs(self) -> bool:
        """Deprecated: in-database configuration mutation removed in favor of GameSense API."""
        return True

    def _restore_mouse_configs(self) -> bool:
        """Deprecated backward-compatible alias."""
        return True

    def release_lighting_to_gg(self, force: bool = False) -> bool:
        """
        Cleanly release any AFK GameSense bindings and restore native SteelSeries
        GG Engine configurations.
        Uses /stop_game (not /remove_game) to preserve registration and avoid 429 errors.
        """
        if not self.address:
            return False
        now = time()
        if not getattr(self, "_lighting_bound", False) and not getattr(self, "_afk_saved_device_configs", None):
            if not force or (now - getattr(self, "_last_lighting_release_ts", 0.0) < 2.0):
                return True

        if getattr(self, "_lighting_bound", False):
            self.send_data("/stop_game", {
                "game": AFK_GAME
            }, ignore_errors=True)
            self._last_lighting_release_ts = time()

        self._redeploy_engine_active_configs()
        logger.info("Released AFK lighting (%s) and restored SteelSeries GG Engine native configs", AFK_GAME)
        return True

    def bind_game_event(self):
        # Apex 7 Pro OLED = 128x40 (640 byte)
        dummy_128x40 = [0 for _ in range(640)]

        # 1. Screen handler for OLED (strictly on GAME = STEELSERIES_OLED_HUB)
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

        # Trigger initial UPDATE event so GAME's Generic-Initializer runs initialize: now
        # rather than after _redeploy_engine_active_configs().
        self.send_data("/game_event", {
            "game": GAME,
            "event": EVENT,
            "data": {
                "value": 100,
                "frame": {"image-data-128x40": dummy_128x40}
            }
        }, ignore_errors=True)

        # 2. Bind LIGHTING on AFK_GAME once at startup
        self._bind_lighting_event()
        if not getattr(self, "_lighting_active", True):
            self.set_lighting(False)
        else:
            sleep(0.05)
            self._redeploy_engine_active_configs()

        logger.info("Bound game event: UPDATE (OLED 128x40)")

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
        """
        Apply AFK lighting transition using official GameSense API:
        - active=False (AFK mode): bind AFK_GAME and send black LIGHTING event
          ([0, 0, 0] across keyboard, mouse, and RGB zones) without touching the database.
        - active=True (Wake / Normal): cleanly release GameSense AFK session and
          redeploy native SteelSeries GG Engine active configurations.
        """
        self._lighting_active = bool(active)

        if not active:
            if not getattr(self, "_lighting_bound", False):
                self._bind_lighting_event()
            black_bitmap = [[0, 0, 0] for _ in range(132)]
            ok = self.send_data("/game_event", {
                "game": AFK_GAME,
                "event": "LIGHTING",
                "data": {
                    "value": 0,
                    "frame": {
                        "bitmap": black_bitmap
                    }
                }
            })
            return bool(ok)

        # Waking from AFK (active=True):
        return self.release_lighting_to_gg(force=True)

    def clear_rgb(self):
        self.set_lighting(False)

    def set_mouse_afk(self, active: bool):
        self.set_lighting(not active)

    def remove_game(self):
        """Cleanly stop and remove GameSense games on exit and restore native GG Engine configs."""
        try:
            for g in (AFK_GAME, GAME):
                self.send_data("/stop_game", {"game": g}, ignore_errors=True)
                self.send_data("/remove_game", {"game": g}, ignore_errors=True)
            sleep(0.12)
            self._redeploy_engine_active_configs()
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

        # Clean up legacy and conflicting game entries (plus AFK_GAME) in SteelSeries GG
        cleanup_games = [
            "OLED_CUSTOMIZER", "OLED_CUSTOMIZER_V1", "OLED_CUSTOMIZER_V2",
            "OLED_CUSTOMIZER_V3", "STEELSERIES_OLED_CUSTOMIZER", "NOW_PLAYING", AFK_GAME
        ]
        # On first startup, also remove GAME once to purge any legacy LIGHTING
        # composite-zones-in-use persisted in GG's SQLite database from older versions.
        if not getattr(self, "_cleaned_main_game", False):
            cleanup_games.append(GAME)
            self._cleaned_main_game = True

        for legacy in cleanup_games:
            try:
                self.send_data("/stop_game", {"game": legacy}, ignore_errors=True)
                self.send_data("/remove_game", {"game": legacy}, ignore_errors=True)
            except Exception:
                pass

        sleep(0.08)
        self.send_data("/game_metadata", {
            "game": GAME,
            "game_display_name": GAME_DISPLAY_NAME,
            "developer": AUTHOR,
            "deinitialize_timer_length_ms": 60000 
        })

    def heartbeat(self):
        """Send a lightweight heartbeat to keep the game(s) registered."""
        if self._stop_conflicting_gg_apps():
            try:
                self.send_data("/remove_game", {"game": "NOW_PLAYING"}, ignore_errors=True)
            except Exception:
                pass
        try:
            self.send_data("/game_heartbeat", {"game": GAME})
            if getattr(self, "_lighting_bound", False) and not getattr(self, "_lighting_active", True):
                self.send_data("/game_heartbeat", {"game": AFK_GAME}, ignore_errors=True)
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
