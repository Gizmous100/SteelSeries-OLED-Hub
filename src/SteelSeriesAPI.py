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
        Redeploy the active SteelSeries GG Engine configuration on connected RGB
        devices via encryptedAddress (/configuration/<id>/forceDeploy).
        Also disables any conflicting onboard hardware idle_timeout (e.g. Apex 7's
        default 300000ms keyboard-only firmware timer that dims the keyboard even
        while the user is actively moving the mouse) so AFK blackout is managed
        uniformly across keyboard, mouse, and OLED by SteelSeries OLED & RGB Hub.
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
                for cfg_meta in cfgs:
                    cid = cfg_meta.get("id")
                    if not cid:
                        continue
                    try:
                        get_req = urllib.request.Request(f"{self.encrypted_address}/configuration/{cid}")
                        with urllib.request.urlopen(get_req, context=ctx, timeout=1.5) as c_resp:
                            cfg_wrapper = loads(c_resp.read().decode("utf-8"))
                        cfg_obj = cfg_wrapper.get("configuration") or {}
                        raw_settings = cfg_obj.get("settings")
                        if isinstance(raw_settings, str) and "idle_timeout" in raw_settings:
                            settings_dict = loads(raw_settings)
                            idle_to = settings_dict.get("idle_timeout")
                            if isinstance(idle_to, dict) and int(idle_to.get("timeout") or 0) > 0:
                                prev_to = idle_to.get("timeout")
                                settings_dict["idle_timeout"] = {"timeout": 0}
                                cfg_obj["settings"] = dumps(settings_dict)
                                cfg_obj.pop("undeployedsettings", None)
                                put_req = urllib.request.Request(
                                    f"{self.encrypted_address}/configuration/{cid}",
                                    data=dumps(cfg_obj).encode("utf-8"),
                                    headers={"Content-Type": "application/json"},
                                    method="PUT",
                                )
                                with urllib.request.urlopen(put_req, context=ctx, timeout=2.0) as _:
                                    pass
                                logger.info(
                                    "Disabled onboard hardware idle_timeout (was %sms) in Engine config '%s' on %s",
                                    prev_to,
                                    cfg_meta.get("name"),
                                    dev.get("name"),
                                )
                    except Exception as ie:
                        logger.debug("Onboard idle_timeout check skipped for %s: %s", cid, ie)

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
        Register the dedicated AFK_GAME and bind its LIGHTING event for Keyboard
        RGB zones during AFK blackout:
        - For per-key RGB keyboards (Apex 7, Apex Pro, etc.), GameSense requires
          mode: "bitmap" when targeting zone: "all".
        - Generic keyboard catch-all uses mode: "color".
        Mice and keyboards are also natively blacked out via _blackout_device_configs().
        """
        self.send_data("/game_metadata", {
            "game": AFK_GAME,
            "game_display_name": AFK_GAME_DISPLAY_NAME,
            "developer": AUTHOR,
            "deinitialize_timer_length_ms": 60000,
            "options": {"enabled": True}
        }, ignore_errors=True)

        color_ranges = [
            {"low": 0, "high": 1, "color": {"red": 0, "green": 0, "blue": 0}}
        ]

        lighting_handlers = [
            # For per-key RGB keyboards (Apex 7, Apex Pro, etc.), GameSense requires
            # mode: "bitmap" when targeting zone: "all".
            {
                "device-type": "rgb-per-key-zones",
                "zone": "all",
                "mode": "bitmap"
            },
            # Generic keyboard catch-all
            {
                "device-type": "keyboard",
                "zone": "all",
                "mode": "color",
                "color": color_ranges
            }
        ]

        ok = self.send_data("/bind_game_event", {
            "game": AFK_GAME,
            "event": "LIGHTING",
            "min_value": 0,
            "max_value": 1,
            "value_optional": True,
            "handlers": lighting_handlers
        })
        self._lighting_bound = bool(ok)
        if ok:
            logger.info("Bound LIGHTING event on %s for Keyboard AFK blackout [0, 0, 0]", AFK_GAME)
        return bool(ok)

    def _blackout_device_configs(self) -> bool:
        """
        Natively black out all connected SteelSeries keyboards and mice during AFK mode.
        Because SteelSeries GG Engine has no GameSense dispatcher support for
        multi-zone composite mice (e.g. Rival 5 with 10 zones), and GameSense event
        routing for per-key keyboards can be overridden by active GG profiles,
        applying native Engine config updates via /configuration/<cid>/forceDeploy ensures
        100% true blackout [0, 0, 0] across all keyboard and mouse keys/zones.
        """
        if not self.encrypted_address:
            return False

        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(f"{self.encrypted_address}/devices")
            with urllib.request.urlopen(req, context=ctx, timeout=2.0) as resp:
                devices = loads(resp.read().decode("utf-8")).get("devices", [])

            for dev in devices:
                if dev.get("connected") != 1:
                    continue

                dev_id = dev.get("id")
                dev_type = dev.get("type")
                dev_name = str(dev.get("name", "")).lower()
                pid = dev.get("product_id")

                is_mouse = (
                    dev_type == 1
                    or pid == 272111676
                    or any(k in dev_name for k in ("mouse", "rival", "aerox", "sensei", "prime"))
                )
                is_keyboard = (
                    dev_type == 0
                    or any(k in dev_name for k in ("keyboard", "apex"))
                )

                if not (is_mouse or is_keyboard) or not dev_id:
                    continue

                req_cfgs = urllib.request.Request(f"{self.encrypted_address}/device/{dev_id}/configurations")
                with urllib.request.urlopen(req_cfgs, context=ctx, timeout=2.0) as resp_cfgs:
                    cfgs = loads(resp_cfgs.read().decode("utf-8")).get("configurations", [])

                active_cfg = (
                    next((c for c in cfgs if c.get("active") == 1 and c.get("last_deployed") == 1), None)
                    or next((c for c in cfgs if c.get("active") == 1), None)
                    or next((c for c in cfgs if c.get("last_deployed") == 1), None)
                    or (cfgs[0] if cfgs else None)
                )
                if not active_cfg or not active_cfg.get("id"):
                    continue

                cid = active_cfg["id"]
                raw_settings = active_cfg.get("settings", "{}")

                # Save original settings if not already preserved
                if cid not in self._afk_saved_device_configs:
                    self._afk_saved_device_configs[cid] = (active_cfg, raw_settings, dev.get("name", "device"))

                # Modify to black settings
                settings_dict = loads(raw_settings)
                settings_dict["global_brightness"] = {"value": 0}
                settings_dict["default_lighting_enabled"] = {"default_lighting_enabled": 0}

                # Clear all color sequences and RGB elements
                for k in list(settings_dict.keys()):
                    if "color_sequence" in k and isinstance(settings_dict[k], dict) and "data" in settings_dict[k]:
                        old_data = settings_dict[k]["data"]
                        n_len = len(old_data[0]) if old_data and isinstance(old_data[0], list) else 30
                        settings_dict[k]["data"] = [[0] * n_len]
                    elif "lighting" in k and isinstance(settings_dict[k], dict) and k != "lighting_config":
                        sub = settings_dict[k]
                        if "initial_color" in sub:
                            sub["initial_color"] = {"r": 0, "g": 0, "b": 0}
                        if "react_color" in sub:
                            sub["react_color"] = {"r": 0, "g": 0, "b": 0, "time": 0}

                # Keyboard-specific illumination elements (e.g. Apex 7, Apex Pro)
                if "idle_mode_dimmer" in settings_dict and isinstance(settings_dict["idle_mode_dimmer"], dict):
                    settings_dict["idle_mode_dimmer"]["value"] = 0

                if "lock_key_color" in settings_dict and isinstance(settings_dict["lock_key_color"], dict):
                    settings_dict["lock_key_color"] = {"r": 0, "g": 0, "b": 0}

                if "lighting_config" in settings_dict and isinstance(settings_dict["lighting_config"], dict):
                    lc = settings_dict["lighting_config"]
                    for g_key in ("foundation_graphics", "idle_graphics", "reactive_graphics"):
                        if g_key in lc and isinstance(lc[g_key], dict):
                            lc[g_key]["graphics"] = []
                            lc[g_key]["num_graphics"] = 0
                    if "zones" in lc and isinstance(lc["zones"], dict) and "zones" in lc["zones"]:
                        for z in lc["zones"]["zones"]:
                            if isinstance(z, dict):
                                if "foundation_default_color" in z:
                                    z["foundation_default_color"] = {"r": 0, "g": 0, "b": 0}
                                if "idle_default_color" in z:
                                    z["idle_default_color"] = {"r": 0, "g": 0, "b": 0}

                active_cfg["settings"] = dumps(settings_dict)
                active_cfg.pop("undeployedSettings", None)
                active_cfg.pop("undeployedsettings", None)

                put_req = urllib.request.Request(
                    f"{self.encrypted_address}/configuration/{cid}",
                    data=dumps(active_cfg).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                with urllib.request.urlopen(put_req, context=ctx, timeout=2.0) as _:
                    pass

                deploy_req = urllib.request.Request(
                    f"{self.encrypted_address}/configuration/{cid}/forceDeploy",
                    data=b"",
                    method="POST",
                )
                with urllib.request.urlopen(deploy_req, context=ctx, timeout=2.0) as _:
                    pass

                logger.info(
                    "Applied native AFK blackout [0, 0, 0] to %s '%s' (%s)",
                    "keyboard" if is_keyboard else "mouse",
                    dev.get("name"),
                    cid,
                )
            return True
        except Exception as e:
            logger.debug("Device AFK blackout skipped/failed: %s", e)
            return False

    def _blackout_mouse_configs(self) -> bool:
        """Backward-compatible alias for _blackout_device_configs."""
        return self._blackout_device_configs()

    def _restore_device_configs(self) -> bool:
        """
        Restore original SteelSeries GG Engine configurations and colors on keyboards
        and mice when exiting AFK mode.
        """
        if not self.encrypted_address or not self._afk_saved_device_configs:
            return True

        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            for cid, (cfg, orig_settings, dev_name) in list(self._afk_saved_device_configs.items()):
                cfg["settings"] = orig_settings
                cfg.pop("undeployedSettings", None)
                cfg.pop("undeployedsettings", None)

                put_req = urllib.request.Request(
                    f"{self.encrypted_address}/configuration/{cid}",
                    data=dumps(cfg).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                with urllib.request.urlopen(put_req, context=ctx, timeout=2.0) as _:
                    pass

                deploy_req = urllib.request.Request(
                    f"{self.encrypted_address}/configuration/{cid}/forceDeploy",
                    data=b"",
                    method="POST",
                )
                with urllib.request.urlopen(deploy_req, context=ctx, timeout=2.0) as _:
                    pass

                logger.info("Restored native Engine lighting for '%s' (%s)", dev_name, cid)

            self._afk_saved_device_configs.clear()
            return True
        except Exception as e:
            logger.debug("Device lighting restore skipped/failed: %s", e)
            return False

    def _restore_mouse_configs(self) -> bool:
        """Backward-compatible alias for _restore_device_configs."""
        return self._restore_device_configs()

    def release_lighting_to_gg(self, force: bool = False) -> bool:
        """
        Stop and remove the dedicated AFK_GAME (STEELSERIES_AFK_RGB) so its Lisp VM
        table is completely destroyed, restore native keyboard and mouse lighting, wait for deinitialize
        (0x22 internal-lighting-reset) to complete, and force-deploy active SteelSeries
        GG Engine configurations so keyboard and mouse illumination are 100% native.
        """
        if not self.address:
            return False
        now = time()
        if not getattr(self, "_lighting_bound", False) and not getattr(self, "_afk_saved_device_configs", None):
            if not force or (now - getattr(self, "_last_lighting_release_ts", 0.0) < 2.0):
                return True

        self._restore_device_configs()

        self.send_data("/remove_game_event", {
            "game": AFK_GAME,
            "event": "LIGHTING"
        }, ignore_errors=True)
        self.send_data("/stop_game", {
            "game": AFK_GAME
        }, ignore_errors=True)
        self.send_data("/remove_game", {
            "game": AFK_GAME
        }, ignore_errors=True)
        self._lighting_bound = False
        self._last_lighting_release_ts = time()

        # Wait ~120ms for generic-initializer.lsp deinitialize: (internal-lighting-reset 0x22)
        # to finish, then redeploy active Engine configs so Rival 5 1-frame steady
        # profiles do not remain on factory orange (#FC4E02).
        sleep(0.12)
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

        # 2. Only bind LIGHTING on AFK_GAME when in AFK blackout; otherwise ensure
        # AFK_GAME is removed and native GG Engine configs are deployed.
        if not getattr(self, "_lighting_active", True):
            self._bind_lighting_event()
            self.set_lighting(False)
        else:
            sleep(0.1)
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
        Apply AFK lighting transition:
        - active=False (AFK mode): register/bind AFK_GAME LIGHTING for keyboard blackout,
          and natively black out connected keyboards and mice via Engine forceDeploy.
        - active=True (Wake / Normal): restore native device lighting, stop and remove AFK_GAME,
          and redeploy active SteelSeries GG Engine configs.
        """
        self._lighting_active = bool(active)

        if not active:
            kb_ok = True
            if not getattr(self, "_lighting_bound", False):
                kb_ok = self._bind_lighting_event()
            if kb_ok:
                black_bitmap = [[0, 0, 0] for _ in range(132)]
                self.send_data("/game_event", {
                    "game": AFK_GAME,
                    "event": "LIGHTING",
                    "data": {
                        "value": 0,
                        "frame": {
                            "bitmap": black_bitmap
                        }
                    }
                })
            dev_ok = self._blackout_device_configs()
            return kb_ok or dev_ok

        self._restore_device_configs()
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
