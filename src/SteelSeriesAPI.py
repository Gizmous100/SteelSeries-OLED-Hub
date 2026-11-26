from json import loads, dumps
from time import sleep, time, monotonic
import threading
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
        self._send_lock = threading.Lock()
        # Guards the lighting *state* only (short critical sections, never held
        # across HTTP calls). All lighting I/O runs on the Lighting-Worker thread.
        self._lighting_lock = threading.Lock()
        # Prevents overlapping re-registrations (reset() must not hold _send_lock,
        # otherwise every request it makes times out on that lock).
        self._reset_lock = threading.Lock()

        # Error back-off (escalating: 2s → 5s → 10s)
        self._consecutive_errors = 0
        self._backoff_until = 0
        self._backoff_level = 0          # 0=2s, 1=5s, 2=10s
        self._BACKOFF_DURATIONS = [2.0, 5.0, 10.0]
        self._RESET_THRESHOLD = 10       # Force re-register after this many consecutive errors
        self._is_resetting = False      # Prevent overlapping resets
        # Desired lighting state (written by callers, applied by the Lighting-Worker)
        self._lighting_active = bool(initial_active)
        self._rgb_color = self._normalize_rgb(initial_rgb)
        self._rgb_enabled = bool(initial_rgb is not None)
        self._rgb_black_off = False      # explicit black (per-profile colour) = backlight off
        self._release_redeploy = True    # False: defer the native-config redeploy (profile switch)
        self._lighting_seq = 0
        # Last lighting state GameSense confirmed: ("black",), ("color", rgb), ("released",)
        # or None when unknown (startup / after re-registration).
        self._applied_target = None
        self._redeploy_due = None        # monotonic time of a deferred redeploy (worker only)
        self._lighting_wakeup = threading.Event()
        self._lighting_stopped = False
        self._lighting_bound = False
        self._bound_color = (0, 0, 0)
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
        
        # Single thread for all lighting I/O: callers never block on HTTP, the newest
        # requested state always wins, and failed sends are retried with back-off.
        self._lighting_thread = threading.Thread(
            target=self._lighting_worker, daemon=True, name="Lighting-Worker"
        )
        self._lighting_thread.start()

        # Bounded: never loop forever in the constructor. If GG isn't up yet,
        # DisplayManager's run loop re-registers once it detects GG running.
        self.retrieve_address(retries=2)

    @staticmethod
    def _normalize_rgb(value):
        """Coerce a preference value into a valid (r, g, b) tuple."""
        try:
            if isinstance(value, (list, tuple)) and len(value) >= 3:
                return tuple(max(0, min(255, int(channel))) for channel in value[:3])
        except (TypeError, ValueError):
            pass
        return (0, 0, 0)

    def retrieve_address(self, retries=None):
        """
        Locate coreProps.json and register with the GameSense API.
        If retries is None, defaults to a bounded number of attempts
        (previously looped forever, which could hang startup when GG is absent).
        Otherwise attempts specified number of retries before giving up.
        """
        if retries is None:
            retries = 10  # bounded default: ~50s total, then give up
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
        """Invalidate current connection and force re-registration.

        Must not hold _send_lock while re-registering: every request made by
        retrieve_address() acquires it, so they all timed out and the hub was
        never re-registered after a GG restart.
        """
        if not self._reset_lock.acquire(blocking=False):
            return  # a reset is already in progress (e.g. nested via send_data)
        self._is_resetting = True
        try:
            logger.info("Resetting SteelSeries connection...")
            self.address = ""
            self.encrypted_address = ""
            self.gg_encrypted_address = ""
            self._lighting_bound = False
            self._bound_color = (0, 0, 0)
            self._last_lighting_release_ts = 0.0
            self._afk_saved_device_configs = {}
            self._afk_saved_mouse_configs = self._afk_saved_device_configs
            # Limit retries during resets to avoid blocking the main thread forever
            self.retrieve_address(retries=2)
        finally:
            self._is_resetting = False
            self._reset_lock.release()

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

    def _bind_lighting_event(self, color=(0, 0, 0), stop_session: bool = True) -> bool:
        """
        Register the dedicated AFK_GAME and bind its LIGHTING event for Keyboard,
        Mouse, and RGB zones using the official GameSense API.
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

        red, green, blue = self._normalize_rgb(color)
        static_color = {"red": red, "green": green, "blue": blue}

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
                "color": static_color
            },
            {
                "device-type": "mouse",
                "zone": "logo",
                "mode": "color",
                "color": static_color
            },
        ]

        # Multi-zone mouse specific zones (Rival 5 10 zones, Rival 600 8 zones, etc.)
        for z in ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"):
            lighting_handlers.append({
                "device-type": "rgb-10-zone",
                "zone": z,
                "mode": "color",
                "color": static_color
            })
            lighting_handlers.append({
                "device-type": "rgb-zoned-device",
                "zone": z,
                "mode": "color",
                "color": static_color
            })

        # Other zoned hardware (headsets, single/dual zone mice, mousepads)
        for general_type in ("rgb-1-zone", "rgb-2-zone", "rgb-3-zone", "rgb-5-zone", "rgb-8-zone", "rgb-12-zone"):
            lighting_handlers.append({
                "device-type": general_type,
                "zone": "one",
                "mode": "color",
                "color": static_color
            })

        ok = self.send_data("/bind_game_event", {
            "game": AFK_GAME,
            "event": "LIGHTING",
            "value_optional": True,
            "handlers": lighting_handlers
        })
        self._lighting_bound = bool(ok)
        if ok:
            self._bound_color = (red, green, blue)
            # At (re-)registration, stop the session immediately so the normal desktop
            # profile is active until a real transition asks for the AFK blackout or
            # the user color. Re-binds right before a LIGHTING event skip this, or the
            # native GG lighting would flash between the bind and the event.
            if stop_session:
                self.send_data("/stop_game", {"game": AFK_GAME}, ignore_errors=True)
            logger.info(
                "Bound LIGHTING event on %s (RGB color %s)", AFK_GAME, (red, green, blue)
            )
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

    def release_lighting_to_gg(self, force: bool = False, redeploy: bool = True) -> bool:
        """
        Cleanly release any AFK GameSense bindings and restore native SteelSeries
        GG Engine configurations.
        Uses /stop_game (not /remove_game) to preserve registration and avoid 429 errors.
        Returns False when the session could not be stopped, so the caller retries.
        redeploy=False skips the native-config redeploy (deferred by the caller).
        """
        if not self.address:
            return False
        now = time()
        if not getattr(self, "_lighting_bound", False) and not getattr(self, "_afk_saved_device_configs", None):
            if not force or (now - getattr(self, "_last_lighting_release_ts", 0.0) < 2.0):
                return True

        ok = True
        if getattr(self, "_lighting_bound", False):
            ok = bool(self.send_data("/stop_game", {
                "game": AFK_GAME
            }, ignore_errors=True))
            self._last_lighting_release_ts = time()

        if redeploy:
            self._redeploy_engine_active_configs()
        logger.info("Released AFK lighting (%s) to SteelSeries GG Engine native configs", AFK_GAME)
        return ok

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

        # 2. Bind LIGHTING on AFK_GAME once at startup. The device state is unknown
        #    after (re-)registration, so the Lighting-Worker re-applies the desired
        #    state: AFK blackout, user/profile colour, or GG native configs.
        self._bind_lighting_event()
        with self._lighting_lock:
            self._applied_target = None
            self._release_redeploy = True
            self._lighting_seq += 1
        self._lighting_wakeup.set()

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

    def _ensure_lighting_bound(self, color) -> bool:
        if getattr(self, "_lighting_bound", False) and getattr(self, "_bound_color", None) == tuple(color):
            return True
        return bool(self._bind_lighting_event(tuple(color), stop_session=False))

    def _send_lighting_event(self, value: int, color) -> bool:
        red, green, blue = self._normalize_rgb(color)
        bitmap = [[red, green, blue] for _ in range(132)]
        ok = self.send_data("/game_event", {
            "game": AFK_GAME,
            "event": "LIGHTING",
            "data": {
                "value": value,
                "frame": {"bitmap": bitmap},
            },
        })
        return bool(ok)

    # ------------------------------------------------------------------ lighting
    # Callers only record the desired state; the Lighting-Worker thread applies it.
    # LIGHTING events are therefore sent only on real transitions (or retries of a
    # failed transition), never from the render loop (AGENTS.md).

    _RETRY_DELAYS = (2.0, 5.0, 10.0)
    _RELEASE_REDEPLOY_DELAY = 2.0   # let GG finish a profile deploy before redeploying

    def _desired_target_locked(self):
        if not self._lighting_active:
            return ("black",)
        if self._rgb_enabled and (self._rgb_color != (0, 0, 0) or self._rgb_black_off):
            return ("color", self._rgb_color)
        return ("released",)

    def _owns_lighting(self) -> bool:
        target = self._applied_target
        return target is not None and target[0] in ("black", "color")

    def set_lighting(self, active: bool) -> bool:
        """
        Request the two-state lighting transition:
        - active=False (AFK mode): black [0, 0, 0] on keyboard, mouse and RGB zones.
        - active=True (Wake): user-selected RGB color when enabled, otherwise
          release the session and let native SteelSeries GG configs take over.

        Non-blocking: the Lighting-Worker applies it (and retries on failure).
        Called only on state transitions (never from the render loop) to avoid
        HTTP 429 responses and mouse profile lock-ups.
        """
        with self._lighting_lock:
            if self._lighting_active != bool(active):
                self._lighting_active = bool(active)
                self._lighting_seq += 1
            self._release_redeploy = True
            pending = self._desired_target_locked() != self._applied_target
        if pending:
            self._lighting_wakeup.set()
        return True

    def set_rgb_color(self, rgb, enabled: bool = True, black_off: bool = False,
                      redeploy: bool = True) -> bool:
        """Store the user / profile RGB colour; the Lighting-Worker applies it.

        Nothing is sent when the resulting lighting state equals the applied one
        (e.g. profiles sharing a colour, or colour edits while RGB is disabled).
        black_off=True makes an explicit black colour turn the backlight off
        (per-profile colours) instead of handing lighting back to GG.
        redeploy=False defers the native-config redeploy of a release, so a
        profile switch does not redeploy the outgoing profile's config.
        """
        color = self._normalize_rgb(rgb)
        with self._lighting_lock:
            new_state = (color, bool(enabled), bool(black_off))
            if new_state != (self._rgb_color, self._rgb_enabled, self._rgb_black_off):
                self._rgb_color, self._rgb_enabled, self._rgb_black_off = new_state
                self._lighting_seq += 1
            self._release_redeploy = bool(redeploy)
            pending = self._desired_target_locked() != self._applied_target
        if pending:  # also retries a previously failed transition
            self._lighting_wakeup.set()
        return True

    def stop_lighting_worker(self):
        self._lighting_stopped = True
        self._lighting_wakeup.set()

    def _apply_lighting_target(self, target, redeploy: bool) -> bool:
        kind = target[0]
        if kind == "black":
            self._ensure_lighting_bound((0, 0, 0))
            return self._send_lighting_event(0, (0, 0, 0))
        if kind == "color":
            color = target[1]
            self._ensure_lighting_bound(color)
            return self._send_lighting_event(1, color)
        return self.release_lighting_to_gg(force=True, redeploy=redeploy)

    def _apply_pending_lighting(self) -> bool:
        """Apply the newest desired state; re-check after each send (bounded)."""
        for _ in range(5):
            with self._lighting_lock:
                target = self._desired_target_locked()
                if target == self._applied_target:
                    return True
                redeploy_now = self._release_redeploy
            if not self._apply_lighting_target(target, redeploy_now):
                return False
            with self._lighting_lock:
                self._applied_target = target
            if target == ("released",):
                self._redeploy_due = None if redeploy_now else monotonic() + self._RELEASE_REDEPLOY_DELAY
            else:
                self._redeploy_due = None
        return True

    def _lighting_worker(self):
        failures = 0
        retry_delay = None
        while not self._lighting_stopped:
            timeout = retry_delay
            if self._redeploy_due is not None:
                remaining = max(0.0, self._redeploy_due - monotonic())
                timeout = remaining if timeout is None else min(timeout, remaining)
            self._lighting_wakeup.wait(timeout)
            self._lighting_wakeup.clear()
            if self._lighting_stopped:
                break
            try:
                # Deferred redeploy after a profile-switch release: by now GG has
                # deployed the new profile, so the *new* config is redeployed.
                if self._redeploy_due is not None and monotonic() >= self._redeploy_due:
                    self._redeploy_due = None
                    with self._lighting_lock:
                        still_released = (
                            self._applied_target == ("released",)
                            and self._desired_target_locked() == ("released",)
                        )
                    if still_released:
                        self._redeploy_engine_active_configs()

                if self._apply_pending_lighting():
                    failures, retry_delay = 0, None
                else:
                    failures += 1
                    retry_delay = self._RETRY_DELAYS[min(failures - 1, len(self._RETRY_DELAYS) - 1)]
                    logger.debug("Lighting transition failed, retrying in %.0fs", retry_delay)
            except Exception as e:
                failures += 1
                retry_delay = self._RETRY_DELAYS[min(failures - 1, len(self._RETRY_DELAYS) - 1)]
                logger.debug("Lighting worker error: %s", e)

    def clear_rgb(self):
        self.set_rgb_color((0, 0, 0), False)

    def set_mouse_afk(self, active: bool):
        self.set_lighting(not active)

    def remove_game(self):
        """Cleanly stop and remove GameSense games on exit and restore native GG Engine configs."""
        self.stop_lighting_worker()
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

    def resume_conflicting_apps(self):
        """Resume any SteelSeries app suspended by :meth:`_stop_conflicting_gg_apps`.

        Without this, the built-in now-playing.exe stayed frozen until the next
        SteelSeries GG restart.
        """
        suspended = getattr(self, "_suspended_pids", None)
        if not suspended:
            return
        try:
            import psutil
            for pid in list(suspended):
                try:
                    proc = psutil.Process(pid)
                    if proc.status() == psutil.STATUS_STOPPED:
                        proc.resume()
                        logger.info("Resumed conflicting SteelSeries app (PID %d)", pid)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            suspended.clear()

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
            # AFK_GAME has a 60 s deinitialize timer and lighting events are sent only on
            # transitions, so keep it alive while it drives the lighting (AFK blackout or
            # a user / profile colour); otherwise GG reverts the colour after ~60 s.
            if getattr(self, "_lighting_bound", False) and self._owns_lighting():
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
