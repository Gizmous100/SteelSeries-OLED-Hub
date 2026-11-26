import os
from os import path, makedirs, replace
from json import loads, dumps
from copy import deepcopy
from threading import Thread, RLock
from time import sleep

import logging
import ctypes

from src import secret_store
from src.utils import fetch_app_data_path

logger = logging.getLogger("OLED Customizer")

class UserPreferences:
    DEFAULT = {
        "spotify_enabled": False,
        "spotify_client_id": "",
        "spotify_client_secret": "",
        "spotify_redirect_uri": "",
        "local_port": 2408,
        "date_format": 12,
        "display_seconds": True,
        "timer_threshold": 2,
        "afk_enabled": True,
        "afk_disable_in_games": True,
        "afk_timeout_minutes": 5,
        "spotify_fetch_delay": 2,
        "extended_font": True,
        "display_timer": True,
        "display_player": True,
        "display_hw_monitor": False,
        "debug_enabled": False,
        "use_polish_days": False,
        "oled_language": "EN",
        "clock_style": "Standard",
        "hotkey_monitor": "Key.insert",
        "hotkey_mute": "Key.pause",
        "rgb_enabled": False,
        "rgb_color": [0, 212, 170],
        "primary": 1,
        "secondary": 0,
        "scrollbar_padding": 2,
        "text_padding_left": 30,
        "width": 128,
        "height": 40,
        "auto_launch_gg": False,
        "player_style": "Standard",
        "hw_polling_interval": 1000,
        "show_game_fps": False,
        "auto_hw_monitor_game": False,
        "selected_gpu": "Auto",
        # Toggled with Ctrl + this key. Not Insert: Ctrl+Insert is the standard Copy shortcut.
        "hotkey_calculator": "Key.f12",
        "hotkey_mute_2": "",
        "discord_client_id": "",
        "discord_client_secret": "",
        "discord_access_token": "",
        "discord_refresh_token": "",
        "discord_auth_expires": 0,
        "discord_mock_enabled": False,
        "headset_hid_sync_enabled": False,
        "discord_local_port": 8888,
        "show_headset_battery": False,
        "show_battery_percent": False,
        "show_clock_mute_icons": False,
        "weather_enabled": False,
        "weather_city": "Warsaw",
        "weather_aqi_region": "EU",
        "weather_display_time": 5,
        "hotkey_weather": "Key.f8",
        "show_profile_switch_overlay": True,
        "profile_switch_display_time": 3.5,
        "profile_colors": {},
        "profile_lighting_enabled": True,
        "show_lock_keys_overlay": True,
        "lock_keys_overlay_duration": 1.8,
        "show_lock_keys_clock": True
    }

    # A competing writer (older version, cloud sync, antivirus) can leave the
    # file half-written for a few milliseconds. Retry before declaring it
    # corrupt and resetting the user's settings.
    _READ_RETRIES = 3
    _READ_RETRY_DELAY = 0.05

    def __init__(self):
        self.valid = True
        self.preferences = deepcopy(self.DEFAULT)
        self.config_path = fetch_app_data_path("config.json")
        # Serialises load/save across the render loop, tray thread and GUI.
        self._io_lock = RLock()
        logger.info("Preferences path : " + self.config_path)

    def _read_raw(self):
        with open(self.config_path, "r", encoding="utf-8") as file:
            return file.read()

    def load_preferences(self) -> bool:
        self.valid = True

        with self._io_lock:
            raw = None
            for attempt in range(self._READ_RETRIES):
                try:
                    raw = self._read_raw()
                    break
                except FileNotFoundError:
                    logger.info("No preferences found, created default preferences")
                    self.save_preferences()
                    return True
                except OSError:
                    if attempt == self._READ_RETRIES - 1:
                        raise
                    sleep(self._READ_RETRY_DELAY)

            parsed = None
            last_error = None
            for attempt in range(self._READ_RETRIES):
                try:
                    candidate = loads(raw)
                    if not isinstance(candidate, dict):
                        raise ValueError("config.json root must be an object")
                    parsed = candidate
                    break
                except Exception as e:
                    last_error = e
                    if attempt < self._READ_RETRIES - 1:
                        sleep(self._READ_RETRY_DELAY)
                        try:
                            raw = self._read_raw()
                        except OSError:
                            pass

            if parsed is None:
                # Recover instead of refusing to run: keep the broken file for
                # inspection, reset to defaults, and warn without blocking any thread.
                logger.error("Corrupt config.json (%s) — resetting to defaults", last_error)
                try:
                    replace(self.config_path, self.config_path + ".bad")
                except OSError:
                    pass
                self.preferences = deepcopy(self.DEFAULT)
                self.save_preferences()
                self._warn_corrupt_config(str(last_error))
                return True

            self.preferences = secret_store.unprotect_mapping(parsed)

            modified = False
            if "oled_language" not in self.preferences and self.preferences.get("use_polish_days"):
                self.preferences["oled_language"] = "PL"
                modified = True

            # One-time migration of the old calculator default (Ctrl+Insert collides with
            # Copy and turned calculator mode on by accident). A user who picks Insert
            # again afterwards keeps it: the flag prevents migrating twice.
            if not self.preferences.get("hotkey_calculator_migrated"):
                if self.preferences.get("hotkey_calculator") == "Key.insert":
                    self.preferences["hotkey_calculator"] = self.DEFAULT["hotkey_calculator"]
                    logger.info("Calculator hotkey migrated from Ctrl+Insert to Ctrl+F12")
                self.preferences["hotkey_calculator_migrated"] = True
                modified = True

            for key, value in self.DEFAULT.items():
                if key not in self.preferences:
                    # Copy: mutable defaults (profile_colors, rgb_color) must never be
                    # shared with the class-level DEFAULT dict.
                    self.preferences[key] = deepcopy(value)
                    modified = True

            if modified:
                self.save_preferences()
            return True

    @staticmethod
    def _warn_corrupt_config(detail):
        def _show():
            try:
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "Your config.json was corrupt and has been reset to defaults.\n"
                    "The old file was kept as config.json.bad.\n\n" + detail,
                    "SteelSeries OLED & RGB Hub - Settings Reset",
                    0x30,  # MB_ICONWARNING
                )
            except Exception:
                pass
        Thread(target=_show, daemon=True).start()

    def save_preferences(self):
        """Write preferences atomically so readers never see a partial file.

        The JSON is written to a sibling ``.tmp`` file, flushed to disk and then
        swapped in with :func:`os.replace`. Secrets are DPAPI-encrypted on the
        way out; the in-memory copy stays plain so the rest of the app can use
        it transparently.
        """
        with self._io_lock:
            makedirs(path.dirname(self.config_path), exist_ok=True)
            payload = dumps(secret_store.protect_mapping(self.preferences), indent=4)
            tmp_path = self.config_path + ".tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as file:
                    file.write(payload)
                    file.flush()
                    try:
                        os.fsync(file.fileno())
                    except (OSError, TypeError):
                        # fsync may fail on some filesystems/network drives or
                        # with mocked file objects in tests — not fatal.
                        pass
                if path.exists(tmp_path):
                    try:
                        replace(tmp_path, self.config_path)
                    except FileNotFoundError:
                        # tmp_path was not actually written to disk (e.g. open()
                        # is mocked in unit tests) — in-memory preferences are
                        # already updated, so this is not fatal.
                        pass
            finally:
                if path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

    def get_preference(self, key, default=None):
        """Return the preference for ``key``, or ``default`` if it is missing.

        Backward compatible: callers passing only ``key`` get ``None`` for a
        missing key, exactly as before.
        """
        return self.preferences.get(key, default)
