from threading import Thread
from queue import Queue, Empty
from time import sleep, time
import logging

from src.SpotifyAPI import SpotifyAPI
from src.SpotifyPlayer import SpotifyPlayer
from src.SteelSeriesAPI import SteelSeriesAPI
from src.Timer import Timer
from src.Calculator import Calculator
from src.Weather import WeatherModule
from src.volume import VolumeOverlay
from src.ProfileOverlay import ProfileOverlay
from src.image_utils import convert_to_bitmap
from src.UserPreferences import UserPreferences
from src.Systray import run_systray_async
from src.WindowsMedia import WindowsMedia
from src.HardwareMonitor import HardwareMonitor
from src.ExtensionReceiver import ExtensionReceiver
from src.DiscordRPC import DiscordIPC
from src.HIDListener import HIDListener
from src.utils import is_process_running, find_steelseries_gg_path, launch_process, get_idle_time_ms, get_last_input_info, get_cursor_pos
import asyncio

logger = logging.getLogger("OLED Customizer")


class State:
    SHOW_CLOCK = 0
    SHOW_PLAYER = 1
    SHOW_CALCULATOR = 2


class DisplayManager:
    def __init__(self, config, fps):
        self.config = config
        self.fps = fps
        self.state = State.SHOW_CLOCK
        self._running = True

        self.enabled = True
        self.display_clock = True
        self.display_player = True
        self.display_hw_monitor = False

        self.user_preferences = UserPreferences()
        self.user_preferences.load_preferences()  # Load saved preferences FIRST
        self.timer = Timer(
            config,
            self.user_preferences.get_preference("date_format"),
            self.user_preferences.get_preference("display_seconds"),
            self.user_preferences.get_preference("use_polish_days"),
            self.user_preferences.get_preference("clock_style"),
            self.user_preferences.get_preference("oled_language"),
        )

        self.calculator = Calculator(config)
        self._calculator_active = False  # True when OLED is showing the calculator
        self.weather = WeatherModule(config, self.user_preferences)
        self._weather_active_time = 0

        run_systray_async(self)

        self.player = SpotifyPlayer(config, self.user_preferences, fps)
        
        # Only initialize Spotify API if enabled in preferences
        self.spotify_enabled = self.user_preferences.get_preference("spotify_enabled")
        if self.spotify_enabled:
            self.spotify_api = SpotifyAPI(self.user_preferences)
        else:
            self.spotify_api = None
        
        self.steelseries_api = SteelSeriesAPI()
        
        # Lock for thread-safety across shared state like self.player
        self._lock = __import__('threading').Lock()

        self.volume_overlay = VolumeOverlay(config)
        profile_timeout = float(self.user_preferences.get_preference("profile_switch_display_time") or 3.5)
        self.profile_overlay = ProfileOverlay(config, timeout=profile_timeout)
        self._last_active_loadout_name = ""
        self._test_profile_idx = 0
        self.hardware_monitor = HardwareMonitor(config, self.user_preferences)
        Thread(target=self._profile_monitor_loop, daemon=True, name="Profile-Monitor").start()
        discord_port = self.user_preferences.get_preference("discord_local_port") or 8888
        self.extension_receiver = ExtensionReceiver(port=discord_port)
        self.extension_receiver.start()

        # Setup global keyboard hook (raw Windows WH_KEYBOARD_LL)
        # This works even when fullscreen games (GTA V, etc.) have focus,
        # unlike pynput's Listener which can silently stop receiving events.
        self._ctrl_held = False
        self._shift_held = False  # Track Shift for parenthesis detection
        # VK codes for hotkeys (populated by _reload_hotkey_vks via load_preferences)
        self._vk_monitor = None
        self._vk_mute = None
        self._vk_mute_2 = None
        self._vk_calculator = None
        # Action queue: hook callback enqueues lightweight strings,
        # worker thread handles them. This keeps the hook callback <1ms
        # so Windows never kills it (LL hooks die after ~300ms timeout).
        self._hotkey_action_queue = Queue(maxsize=32)
        Thread(target=self._hotkey_action_worker, daemon=True, name="Hotkey-Actions").start()
        
        # Track Thread ID of the hook loop so we can post a WM_QUIT to it
        self._hook_thread_id = None
        
        # Start the raw Windows keyboard hook thread
        Thread(target=self._keyboard_hook_loop, daemon=True, name="Keyboard-Hook").start()

        # Windows Media (SMTC) - runs in background thread
        self.windows_media = WindowsMedia()
        self._smtc_data = None
        self._smtc_lock = __import__('threading').Lock()
        Thread(target=self._poll_smtc_loop, daemon=True).start()

        # Discord RPC — polls Discord's local IPC for mic mute/deaf state
        discord_cid = self.user_preferences.get_preference("discord_client_id") or None
        self._discord_ipc = DiscordIPC(
            client_id=discord_cid, 
            preferences=self.user_preferences,
            extension_receiver=self.extension_receiver
        )

        # Bi-directional sync tracking
        self._hid_listener = None
        self._last_discord_state = None
        self._last_hw_state = None
        self._last_hw_event_ts = 0.0
        self._sync_lockout_until = 0.0
        self._discord_disconnect_ts = 0.0
        self._last_hotkey_req = None
        self._last_hotkey_req_ts = 0.0

        # Route the mute hotkey to Discord whenever its IPC pipe is connected
        # (otherwise the hotkey was a silent no-op while Discord was running).
        # Assigned after the sync state above exists — the hotkey worker thread
        # is already running and could fire mid-init.
        self.volume_overlay._discord_toggle_cb = self._hotkey_set_discord_mute
        self._set_headset_hid_sync_enabled(
            bool(self.user_preferences.get_preference("headset_hid_sync_enabled"))
        )
        # Headset battery, shown top-right on the clock (opt-in; needs HID sync)
        self.timer.battery_getter = lambda: (
            self._hid_listener.current_battery() if self._hid_listener else None
        )
        self.timer.weather_getter = lambda: (
            self.weather.get_weather_data() if hasattr(self, "weather") else None
        )
        self.timer.mute_status_getter = self._get_clock_mute_status
        self.timer.show_clock_mute_icons = bool(
            self.user_preferences.get_preference("show_clock_mute_icons")
        )
        Thread(target=self._discord_rpc_loop, daemon=True, name="Discord-RPC").start()

        now_ms = int(time() * 1000)

        # polling timers
        self._last_spotify_poll_ms = now_ms

        # SOURCE STATE
        self._spotify_last_seen_ms = 0
        self._spotify_last_playing_ms = 0
        self._spotify_paused = True

        self._yt_last_seen_ms = 0
        self._yt_last_playing_ms = 0
        self._yt_paused = True

        # toleranslar (flicker kesmek için)
        # SIFIRLANDI: Paused olduğu an saate dönmesi için bekleme süreleri kaldırıldı.
        # Geri eklendi: Ancak polling gecikmesi yüzünden olan flicker'ı engellemek için.
        # Bu değer PAUSE basıldığında devreye girmez (explicit check var), 
        # sadece VERİ GELMEDİĞİNDE "hala çalıyor varsay" süresidir.
        self._spotify_hold_playing_ms = 3000   
        self._yt_hold_playing_ms = 3000        

        # gg flicker azalt
        self._last_sent_frame = None
        # If startup registration failed (GG not up yet), start False so the
        # run loop triggers a re-registration as soon as GG is detected
        self._gg_was_running = bool(self.steelseries_api.address)
        self._gg_check_ts = 0.0
        self._gg_running_cached = False
        self._last_lighting_state = None

        # STICKY SOURCE logic
        self._extension_last_data_ms = 0
        self._extension_lock_seconds = 5
        
        self.auto_launch_gg = False
        self._last_launch_attempt = 0
        self._last_frame_sent_time = 0
        self._last_heartbeat_time = 0  # Heartbeat to keep game registered
        self._frame_fail_start = 0     # Tracks when frame sends started failing
        self._last_gg_restart_attempt = 0  # Cooldown for auto-restarting hung GG
        self._gg_restart_in_progress = False
        
        self._is_afk = False
        self._afk_timeout_ms = 5 * 60 * 1000  # 5 minutes by default
        self._afk_toggled_time = 0
        self._afk_settled = False
        self._afk_settled_time = 0
        self._afk_baseline_input_tick = 0
        self._afk_baseline_cursor_pos = get_cursor_pos()
        self._last_hook_input_ms = now_ms
        self._last_cursor_pos = self._afk_baseline_cursor_pos
        self._last_cursor_move_ms = now_ms

        # Spotify worker thread (single persistent thread instead of spawning new ones)
        self._spotify_queue = Queue(maxsize=1)
        Thread(target=self._spotify_worker_loop, daemon=True, name="Spotify-Worker").start()
        
        # Config hot-reloading tracker
        self._config_file_path = self.user_preferences.config_path
        self._last_config_mtime = 0
        try:
            import os
            if os.path.exists(self._config_file_path):
                self._last_config_mtime = os.path.getmtime(self._config_file_path)
        except Exception:
            pass
        self._last_config_check_ts = time()

        self.load_preferences()
        
        # Diagnostic: Log Admin status
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            logger.info(f"Process Privilege Status: {'ADMIN/UAC' if is_admin else 'USER'}")
            if not is_admin:
                logger.warning("App detected NOT running as Admin. Hotkeys may fail over elevated games (GTA V, etc.).")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Raw low-level Windows keyboard hook (WH_KEYBOARD_LL)
    # Handles ALL hotkeys + numpad suppression for calculator.
    # Works even when fullscreen games have exclusive focus.
    # ------------------------------------------------------------------

    # Map from pynput-style key strings ("Key.insert") to Windows VK codes
    _KEY_STR_TO_VK_MAP = {
        "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
        "page_up": 0x21, "page_down": 0x22,
        "pause": 0x13, "scroll_lock": 0x91, "print_screen": 0x2C,
        "caps_lock": 0x14, "num_lock": 0x90,
        "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
        "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
        "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
        "space": 0x20, "enter": 0x0D, "backspace": 0x08,
        "tab": 0x09, "esc": 0x1B,
        "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        "media_play_pause": 0xB3, "media_next": 0xB0, "media_previous": 0xB1,
        "media_volume_mute": 0xAD, "media_volume_up": 0xAF, "media_volume_down": 0xAE,
        "mouse_4": 0x05, "mouse_5": 0x06,
    }

    @staticmethod
    def _key_str_to_vk(key_str):
        """Convert a preference key string like 'Key.insert' or 'a' to a Windows VK code."""
        if not key_str:
            return None
        try:
            # "Key.insert" → look up in our map
            if key_str.startswith("Key."):
                name = key_str.split("Key.", 1)[1].lower()
                return DisplayManager._KEY_STR_TO_VK_MAP.get(name)

            # "<65>" → direct VK code
            if key_str.startswith("<") and key_str.endswith(">"):
                return int(key_str[1:-1])

            # Single character "a", "1" → use VkKeyScanW to get VK code
            if len(key_str) == 1:
                import ctypes
                result = ctypes.windll.user32.VkKeyScanW(ord(key_str))
                vk = result & 0xFF
                if vk != 0xFF:
                    return vk

            return None
        except Exception:
            return None

    def _reload_hotkey_vks(self):
        """Re-read hotkey preferences and convert to VK codes for the raw hook."""
        self._vk_monitor = self._key_str_to_vk(self.user_preferences.get_preference("hotkey_monitor"))
        self._vk_weather = self._key_str_to_vk(self.user_preferences.get_preference("hotkey_weather"))
        self._vk_mute = self._key_str_to_vk(self.user_preferences.get_preference("hotkey_mute"))
        self._vk_mute_2 = self._key_str_to_vk(self.user_preferences.get_preference("hotkey_mute_2"))
        self._vk_calculator = self._key_str_to_vk(self.user_preferences.get_preference("hotkey_calculator"))
        logger.info(f"Hotkey VKs: Monitor=0x{self._vk_monitor or 0:02X}, Weather=0x{self._vk_weather or 0:02X}, "
                    f"Mute=0x{self._vk_mute or 0:02X}, Mute2=0x{self._vk_mute_2 or 0:02X}, "
                    f"Calculator=0x{self._vk_calculator or 0:02X}")

    def _hotkey_action_worker(self):
        """
        Worker thread that processes hotkey actions dispatched from the
        keyboard hook callback. This keeps the hook callback <1ms so
        Windows never kills it (LL hooks die after ~300ms timeout).

        Actions like toggle_mic_mute use COM calls that can take 100ms+,
        which would cause the hook to be silently removed by Windows.
        """
        # All mic/COM work from hotkeys happens on this thread, and
        # _sync_all_capture_mics expects COM to be live on its caller.
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception as e:
            logger.debug("Hotkey worker CoInitialize failed: %s", e)

        while self._running:
            try:
                action = self._hotkey_action_queue.get(timeout=2.0)
                if action == "trigger_monitor":
                    self.hardware_monitor.trigger()
                elif action == "trigger_weather":
                    self._weather_active_time = time()
                elif action == "toggle_mute":
                    logger.info("Mute key pressed - Toggling Mute")
                    self.volume_overlay.toggle_mic_mute()
                elif action == "calc_on":
                    self.calculator.clear()
                    logger.info("Calculator activated")
                elif action == "calc_off":
                    logger.info("Calculator deactivated")
                elif action == "calc_exit":
                    logger.info("Calculator exited via Escape")
                elif action and action.startswith("calc_input:"):
                    key = action.split(":", 1)[1]
                    self.calculator.key_input(key)
            except Empty:
                continue
            except Exception:
                pass

    def _keyboard_hook_loop(self):
        """
        Installs a low-level Windows keyboard hook (WH_KEYBOARD_LL) via ctypes
        to handle ALL hotkeys and numpad suppression for calculator.

        This works even when fullscreen games (GTA V, etc.) have exclusive focus,
        unlike pynput's Listener which can silently lose events.

        CRITICAL: Uses proper 64-bit types. On x64 Windows, LRESULT/LPARAM/WPARAM
        are all 8 bytes. Using 4-byte c_long would corrupt the stack.
        """
        try:
            import ctypes
            from ctypes import wintypes, POINTER, Structure, byref

            # Register the thread ID.
            self._hook_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

            # Store hook handles explicitly for manual cleanup on shutdown
            self._k_hook = None
            self._m_hook = None

            # Use WinDLL with use_last_error for proper error reporting
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            # ---- Constants ----
            WH_KEYBOARD_LL = 13
            WH_MOUSE_LL = 14
            HC_ACTION = 0
            WM_KEYDOWN = 0x0100
            WM_SYSKEYDOWN = 0x0104
            WM_KEYUP = 0x0101
            WM_SYSKEYUP = 0x0105
            
            WM_XBUTTONDOWN = 0x020B
            WM_XBUTTONUP = 0x020C

            VK_LCONTROL = 0xA2
            VK_RCONTROL = 0xA3
            VK_LSHIFT   = 0xA0
            VK_RSHIFT   = 0xA1
            VK_ESCAPE = 0x1B
            VK_RETURN = 0x0D
            VK_BACK = 0x08     # Backspace
            VK_DELETE = 0x2E

            # Numpad VK codes (96..111) + VK_OEM_COMMA (188) + VK_OEM_PERIOD (190)
            NUMPAD_VKS = set(range(96, 112))
            NUMPAD_VKS.add(188)
            NUMPAD_VKS.add(190)

            NUMPAD_OP_MAP = {
                107: "+", 109: "-", 106: "*", 111: "/",
                110: ".", 108: ",",  # VK_SEPARATOR for European locales
                188: ",",  # VK_OEM_COMMA
                190: ".",  # VK_OEM_PERIOD
            }

            # ---- Struct for the hook data ----
            class KBDLLHOOKSTRUCT(Structure):
                _fields_ = [
                    ("vkCode",      wintypes.DWORD),
                    ("scanCode",    wintypes.DWORD),
                    ("flags",       wintypes.DWORD),
                    ("time",        wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p),
                ]

            # ---- 64-bit safe callback type ----
            # On x64: LRESULT = c_longlong, WPARAM = c_ulonglong, LPARAM = c_longlong
            LRESULT = wintypes.LPARAM   # c_longlong on 64-bit
            HOOKPROC = ctypes.CFUNCTYPE(
                LRESULT,                # return type (8 bytes on x64)
                ctypes.c_int,           # nCode
                wintypes.WPARAM,        # wParam
                wintypes.LPARAM,        # lParam
            )

            # ---- Set proper function signatures ----
            kernel32.GetModuleHandleW.restype = ctypes.c_void_p
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

            user32.SetWindowsHookExW.restype = ctypes.c_void_p
            user32.SetWindowsHookExW.argtypes = [
                ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD
            ]

            user32.CallNextHookEx.restype = LRESULT
            user32.CallNextHookEx.argtypes = [
                ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
            ]

            user32.UnhookWindowsHookEx.restype = wintypes.BOOL
            user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]

            user32.GetMessageW.argtypes = [
                POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
            ]
            user32.TranslateMessage.argtypes = [POINTER(wintypes.MSG)]
            user32.DispatchMessageW.argtypes = [POINTER(wintypes.MSG)]

            # ToUnicode: translates VK + scan code + keyboard state → character
            # Used to detect ( and ) regardless of keyboard layout (TR, EN, etc.)
            user32.ToUnicode.restype  = ctypes.c_int
            user32.ToUnicode.argtypes = [
                wintypes.UINT,           # wVirtKey
                wintypes.UINT,           # wScanCode
                ctypes.POINTER(ctypes.c_ubyte),  # lpKeyState (256 bytes)
                ctypes.c_void_p,         # pwszBuff (writable wide-char buffer)
                ctypes.c_int,            # cchBuff
                wintypes.UINT,           # wFlags
            ]

            def _vk_to_char(vk, scan):
                """Translate a VK code to its character using current keyboard state.
                Returns the character string, or '' on failure."""
                try:
                    key_state = (ctypes.c_ubyte * 256)()
                    # Inject Shift state so ToUnicode resolves the shifted character.
                    # VK_SHIFT (0x10) is the generic one ToUnicode actually checks.
                    # Also set VK_LSHIFT (0xA0) / VK_RSHIFT (0xA1) for completeness.
                    if self._shift_held:
                        key_state[0x10] = 0x80   # VK_SHIFT  (generic — what ToUnicode uses)
                        key_state[0xA0] = 0x80   # VK_LSHIFT
                        key_state[0xA1] = 0x80   # VK_RSHIFT
                    buf = ctypes.create_unicode_buffer(4)
                    n = user32.ToUnicode(vk, scan, key_state, buf, 4, 0)
                    if n > 0:
                        return buf.value[:n]
                except Exception:
                    pass
                return ""

            # ---- Enqueue helper (fire-and-forget, never blocks) ----
            def _enqueue(action):
                try:
                    self._hotkey_action_queue.put_nowait(action)
                except Exception:
                    pass  # Queue full — drop (better than blocking the hook)

            # ---- The unified hook callback ----
            # CRITICAL: This must return in <1ms. Windows silently kills
            # LL hooks whose callbacks exceed ~300ms. ALL real work is
            # dispatched to _hotkey_action_worker via the queue.
            @HOOKPROC
            def _hook_callback(nCode, wParam, lParam):
                try:
                    if nCode == HC_ACTION:
                        # Read VK code and flags from memory at lParam address
                        addr = lParam & 0xFFFFFFFFFFFFFFFF  # ensure unsigned
                        vk = ctypes.c_uint32.from_address(addr).value
                        flags = ctypes.c_uint32.from_address(addr + 8).value

                        # Track non-injected physical keyboard events (LLKHF_INJECTED = 0x10)
                        if (flags & 0x10) == 0:
                            self._last_hook_input_ms = int(time() * 1000)

                        is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                        is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)

                        # ---- Track Ctrl modifier ----
                        if vk in (VK_LCONTROL, VK_RCONTROL):
                            if is_down:
                                self._ctrl_held = True
                            elif is_up:
                                self._ctrl_held = False
                            # Don't suppress Ctrl itself
                            return user32.CallNextHookEx(None, nCode, wParam, lParam)

                        # ---- Track Shift modifier (for parenthesis detection) ----
                        if vk in (VK_LSHIFT, VK_RSHIFT):
                            if is_down:
                                self._shift_held = True
                            elif is_up:
                                self._shift_held = False
                            # Don't suppress Shift itself
                            return user32.CallNextHookEx(None, nCode, wParam, lParam)

                        # Only process key-DOWN events for actions below
                        if is_down:
                            # ---- Calculator toggle: Ctrl + calculator key ----
                            if self._ctrl_held and self._vk_calculator and vk == self._vk_calculator:
                                self._ctrl_held = False  # Consume modifier
                                self._calculator_active = not self._calculator_active
                                if self._calculator_active:
                                    _enqueue("calc_on")
                                else:
                                    _enqueue("calc_off")
                                # Don't suppress — let other apps see the key too
                                return user32.CallNextHookEx(None, nCode, wParam, lParam)

                            # ---- Normal hotkeys (always active) ----
                            if self._vk_monitor and vk == self._vk_monitor:
                                _enqueue("trigger_monitor")
                                return user32.CallNextHookEx(None, nCode, wParam, lParam)

                            if self._vk_weather and vk == self._vk_weather:
                                _enqueue("trigger_weather")
                                return user32.CallNextHookEx(None, nCode, wParam, lParam)

                            if (self._vk_mute and vk == self._vk_mute) or (self._vk_mute_2 and vk == self._vk_mute_2):
                                logger.debug(f"RAW HOOK: Captured Mute Hotkey (VK:0x{vk:02X})")
                                _enqueue("toggle_mute")
                                return user32.CallNextHookEx(None, nCode, wParam, lParam)

                        # ---- Calculator Input and Suppression (Active Mode) ----
                        # Processed outside `if is_down` so we suppress BOTH down and up events
                        # to prevent ghost "key up" inputs in games/OS.
                        if self._calculator_active:
                            is_suppressed = False

                            if vk == VK_ESCAPE:
                                if is_down:
                                    self._calculator_active = False
                                    _enqueue("calc_exit")
                                is_suppressed = True
                            elif vk == VK_RETURN:
                                if is_down:
                                    _enqueue("calc_input:enter")
                                is_suppressed = True
                            elif vk == VK_BACK:
                                if is_down:
                                    _enqueue("calc_input:backspace")
                                is_suppressed = True
                            elif vk == VK_DELETE:
                                if is_down:
                                    _enqueue("calc_input:delete")
                                is_suppressed = True
                            else:
                                # ---- Number row and Symbols (layout-agnostic) ----
                                # Works on TR keyboard, EN keyboard, TKL boards, etc.
                                scan = ctypes.c_uint32.from_address(addr + 4).value
                                ch = _vk_to_char(vk, scan)
                                if ch in "0123456789+-*/.,()":
                                    if is_down:
                                        _enqueue(f"calc_input:{ch}")
                                    is_suppressed = True
                                else:
                                    # ---- Legacy Numpad suppression (fallback) ----
                                    is_numpad = vk in NUMPAD_VKS
                                    if is_numpad:
                                        if is_down:
                                            if 96 <= vk <= 105:
                                                _enqueue(f"calc_input:{vk - 96}")
                                            elif vk in NUMPAD_OP_MAP:
                                                _enqueue(f"calc_input:{NUMPAD_OP_MAP[vk]}")
                                        is_suppressed = True

                            if is_suppressed:
                                return LRESULT(1).value  # SUPPRESS key

                except Exception:
                    pass  # Never let an exception escape the hook callback

                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            @HOOKPROC
            def _mouse_hook_callback(nCode, wParam, lParam):
                try:
                    if nCode == HC_ACTION:
                        addr = lParam & 0xFFFFFFFFFFFFFFFF
                        m_flags = ctypes.c_uint32.from_address(addr + 12).value
                        # Track non-injected physical mouse events (LLMHF_INJECTED = 0x01)
                        if (m_flags & 0x01) == 0:
                            self._last_hook_input_ms = int(time() * 1000)

                        if wParam == WM_XBUTTONDOWN:
                            # Read XBUTTON data from memory at lParam + 8
                            mouse_data = ctypes.c_uint32.from_address(addr + 8).value
                            xbutton = (mouse_data >> 16) & 0xFFFF
                            
                            vk = None
                            if xbutton == 1: vk = 0x05 # Mouse 4
                            elif xbutton == 2: vk = 0x06 # Mouse 5
                            
                            if vk:
                                if self._vk_monitor and vk == self._vk_monitor:
                                    _enqueue("trigger_monitor")
                                    return LRESULT(1).value # Suppress so game doesn't see it (common for side buttons)
                                if self._vk_weather and vk == self._vk_weather:
                                    _enqueue("trigger_weather")
                                    return LRESULT(1).value
                                if (self._vk_mute and vk == self._vk_mute) or (self._vk_mute_2 and vk == self._vk_mute_2):
                                    _enqueue("toggle_mute")
                                    return LRESULT(1).value

                except Exception:
                    pass
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            # ---- Install ----
            # Must keep callback references alive (prevent GC)
            self._keyboard_hook_proc = _hook_callback
            self._mouse_hook_proc = _mouse_hook_callback

            hmod = kernel32.GetModuleHandleW(None)
            self._k_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _hook_callback, hmod, 0)
            self._m_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, _mouse_hook_callback, hmod, 0)

            if not self._k_hook or not self._m_hook:
                err = ctypes.get_last_error()
                logger.error(f"SetWindowsHookExW failed (error {err})")
                return

            logger.info("Global kb/mouse hooks installed OK")

            # ---- Message pump (required to keep LL hook alive) ----
            msg = wintypes.MSG()
            try:
                while user32.GetMessageW(byref(msg), None, 0, 0) != 0:
                    user32.TranslateMessage(byref(msg))
                    user32.DispatchMessageW(byref(msg))
            finally:
                if self._k_hook:
                    user32.UnhookWindowsHookEx(self._k_hook)
                if self._m_hook:
                    user32.UnhookWindowsHookEx(self._m_hook)
                logger.info("Hooks removed locally from thread exit")

        except Exception as e:
            # This thread is non-critical — never crash the app
            logger.error(f"Keyboard hook thread failed: {e}", exc_info=True)

    # ------------------------------------------------------------------

    def _set_headset_hid_sync_enabled(self, enabled):
        enabled = bool(enabled)
        currently_enabled = self._hid_listener is not None
        if enabled == currently_enabled:
            return

        # Re-align state machine whenever listener availability changes.
        self._last_discord_state = None
        self._last_hw_state = None
        self._last_hw_event_ts = 0.0
        self._sync_lockout_until = 0

        if enabled:
            try:
                self._hid_listener = HIDListener(self.volume_overlay)
                self._hid_listener.start()
                logger.info("Headset HID sync enabled by preference")
            except Exception as e:
                self._hid_listener = None
                logger.error("Failed to enable headset HID sync: %s", e)
        else:
            try:
                self._hid_listener.stop()
            except Exception as e:
                logger.debug("Failed to stop HID listener cleanly: %s", e)
            self._hid_listener = None
            logger.info("Headset HID sync disabled by preference")

    def _restart_steelseries_gg(self):
        """Kill and restart SteelSeries GG when its API becomes unresponsive.
        This handles the case where GG.exe is still 'running' but its HTTP API
        on localhost is frozen/hung and doesn't respond to requests.

        Runs on a background thread; guarded so only one restart runs at a time."""
        if self._gg_restart_in_progress:
            return
        self._gg_restart_in_progress = True
        import subprocess
        try:
            # Force-kill all GG processes
            for proc_name in ["SteelSeriesGG.exe", "SteelSeriesGGClient.exe"]:
                subprocess.run(
                    ["taskkill", "/F", "/IM", proc_name],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            logger.info("Auto-Recovery: Killed SteelSeries GG processes")
            sleep(5)  # Wait for cleanup

            # Relaunch GG
            gg_path = find_steelseries_gg_path()
            if gg_path:
                args = r'-dataPath="C:\ProgramData\SteelSeries\GG" -dbEnv=production'
                launch_process(gg_path, args)
                logger.info("Auto-Recovery: Relaunched SteelSeries GG")
                sleep(10)  # Wait for API to come up
            else:
                logger.warning("Auto-Recovery: Could not find SteelSeries GG path for relaunch")

            # Mark GG as 'was not running' so the main loop triggers re-registration
            self._gg_was_running = False
            # Reset the success timer so we don't immediately re-trigger
            self.steelseries_api._last_success_time = time()
        except Exception as e:
            logger.error("Auto-Recovery: Failed to restart SteelSeries GG: %s", e)
        finally:
            self._gg_restart_in_progress = False

    def load_preferences(self):
        self.user_preferences.load_preferences()
        self.update_preferences()

    def update_preferences(self):
        self.fetch_delay = max(
            int(self.user_preferences.get_preference("spotify_fetch_delay")),
            1 / self.fps,
        )
        self.timer_threshold = (
            max(int(self.user_preferences.get_preference("timer_threshold")), 0) * 1000
        )
        try:
            afk_mins = float(self.user_preferences.get_preference("afk_timeout_minutes") or 5)
        except Exception:
            afk_mins = 5.0
        self._afk_timeout_ms = max(int(afk_mins * 60 * 1000), 5000)

        self.display_clock = self.user_preferences.get_preference("display_timer")
        self.display_player = self.user_preferences.get_preference("display_player")
        self.display_hw_monitor = self.user_preferences.get_preference("display_hw_monitor")

        # Sync Layout constants
        self.config.scrollbar_padding = int(self.user_preferences.get_preference("scrollbar_padding") or 2)
        self.config.text_padding_left = int(self.user_preferences.get_preference("text_padding_left") or 30)

        if hasattr(self.timer, "set_display_seconds"):
            self.timer.set_display_seconds(self.user_preferences.get_preference("display_seconds"))
        if hasattr(self.timer, "set_date_format"):
            self.timer.set_date_format(self.user_preferences.get_preference("date_format"))
        if hasattr(self.timer, "set_language"):
            self.timer.set_language(
                self.user_preferences.get_preference("oled_language")
                or ("PL" if self.user_preferences.get_preference("use_polish_days") else "EN")
            )
        elif hasattr(self.timer, "set_use_polish_days"):
            self.timer.set_use_polish_days(self.user_preferences.get_preference("use_polish_days"))
        if hasattr(self.timer, "set_style"):
            self.timer.set_style(self.user_preferences.get_preference("clock_style"))
            
        if hasattr(self.player, "set_style"):
            self.player.set_style(self.user_preferences.get_preference("player_style"))

        if hasattr(self, "profile_overlay"):
            self.profile_overlay.set_timeout(
                float(self.user_preferences.get_preference("profile_switch_display_time") or 3.5)
            )
        self.timer.show_battery = bool(
            self.user_preferences.get_preference("show_headset_battery"))
        self.timer.show_battery_percent = bool(
            self.user_preferences.get_preference("show_battery_percent"))
        self.timer.show_clock_mute_icons = bool(
            self.user_preferences.get_preference("show_clock_mute_icons"))

        if hasattr(self, "hardware_monitor"):
            self.hardware_monitor.update_preferences(self.user_preferences)
            
        if hasattr(self, "weather"):
            self.weather.update_preferences(self.user_preferences)

        try:
            import os
            if os.path.exists(self._config_file_path):
                self._last_config_mtime = os.path.getmtime(self._config_file_path)
        except Exception:
            pass

        if not self.user_preferences.get_preference("afk_enabled") and self._is_afk:
            self._is_afk = False
            self._last_lighting_state = None
            self._last_sent_frame = None

        # Hotkeys — convert preference strings to VK codes for the raw hook
        self._reload_hotkey_vks()
        
        self.auto_launch_gg = self.user_preferences.get_preference("auto_launch_gg")

        # Discord
        discord_cid = self.user_preferences.get_preference("discord_client_id")
        if hasattr(self, "_discord_ipc"):
            self._discord_ipc.set_client_id(discord_cid)
        self._set_headset_hid_sync_enabled(
            self.user_preferences.get_preference("headset_hid_sync_enabled")
        )

        self._spotify_poll_ms = max(250, int(self.fetch_delay * 1000))
        
        # Reload Spotify credentials if they changed (only if Spotify is enabled)
        if hasattr(self, "spotify_api") and self.spotify_api:
            changed = self.spotify_api.reload_config()
            # If credentials changed, force a re-fetch with prompt
            if changed:
                logger.info("Credentials changed, triggering re-auth...")
                Thread(target=self.spotify_api.fetch_token, args=(True,), daemon=True).start()
            
            # If not ready (missing tokens) but config didn't change, we do NOTHING directly here.
            # The user must fix the config in the settings window to trigger the 'changed' path.
            # OR we could silently try to fetch if prompt_user=False? No, let's keep it clean.

    def trigger_afk_test(self):
        """Enter AFK mode after a short 1.5s delay so the user can test OLED + RGB blackout."""
        sleep(1.5)
        now_ms = int(time() * 1000)
        last_input_tick, _ = get_last_input_info()
        cur_pos = get_cursor_pos()
        self._is_afk = True
        self._afk_toggled_time = now_ms
        self._afk_settled = False
        self._afk_settled_time = now_ms + 1500
        self._afk_baseline_input_tick = last_input_tick
        self._afk_baseline_cursor_pos = cur_pos
        self._last_cursor_pos = cur_pos
        self._last_sent_frame = None
        logger.info("AFK State changed to: True (triggered via Settings Test AFK button)")
        self._sync_lighting_state()
        try:
            if self.steelseries_api.send_frame([0] * 640):
                self._last_sent_frame = [0] * 640
                self._last_frame_sent_time = time()
        except Exception as e:
            logger.debug("Initial AFK test blank frame failed: %s", e)

    def _sync_lighting_state(self):
        """
        0 = AFK blackout ([0, 0, 0] on keyboard and mouse)
        1 = Active (LIGHTING event unbound so SteelSeries GG Engine controls colors natively)
        """
        target_lighting = 0 if self._is_afk else 1
        if self._last_lighting_state != target_lighting:
            try:
                if self.steelseries_api.set_lighting(not self._is_afk):
                    self._last_lighting_state = target_lighting
                else:
                    self._last_lighting_state = None
            except Exception as e:
                self._last_lighting_state = None
                logger.debug(f"Failed to set lighting state: {e}")

    # _parse_key removed — replaced by _key_str_to_vk and _reload_hotkey_vks above

    def init(self):
        # Startup: Only attempt Spotify auth if enabled
        if not self.spotify_enabled or not self.spotify_api:
            return
            
        # Run in thread to not block startup logic
        def startup_auth():
             self.spotify_api.fetch_token(prompt_user=True)
             
        Thread(target=startup_auth, daemon=True).start()

    def run(self):
        while self._running:
            try:
                self._run_once()
            except Exception:
                # One bad frame/payload must never kill the whole app
                logger.exception("Main loop iteration failed; continuing")
                sleep(1.0)

    def _run_once(self):
            if not self.enabled:
                sleep(1 / self.fps)
                if self.state != -1: # Reset state visualization if disabled
                   pass
                return

            # Check if SteelSeries GG is running. Cached: psutil's full process
            # scan is far too expensive to run 10x per second.
            now_check = time()
            if now_check - self._gg_check_ts > 3.0:
                self._gg_running_cached = is_process_running(["SteelSeriesGG.exe", "SteelSeriesGGClient.exe", "SteelSeriesEngine3.exe"])
                self._gg_check_ts = now_check
            gg_running = self._gg_running_cached
            
            if not gg_running:
                 self._gg_was_running = False
                 logger.debug(f"GG not running. auto_launch_gg={self.auto_launch_gg}")
                 
                 # Auto-launch logic
                 if self.auto_launch_gg:
                     now_sec = time()
                     time_since_last = now_sec - self._last_launch_attempt
                     logger.debug(f"Time since last attempt: {time_since_last:.1f}s")
                     if time_since_last > 60: # Limit attempts to once per minute
                         self._last_launch_attempt = now_sec
                         gg_path = find_steelseries_gg_path()
                         logger.info(f"Found GG path: {gg_path}")
                         if gg_path:
                             logger.info("SteelSeries GG not found. Auto-launching...")
                             # Launch with same args as official shortcut
                             args = r'-dataPath="C:\ProgramData\SteelSeries\GG" -dbEnv=production'
                             result = launch_process(gg_path, args, minimized=True)
                             logger.info(f"Launch result: {result}")
                             # No blocking wait: the loop re-checks every 2s and
                             # the 60s cooldown above prevents relaunch spam.
                         else:
                             logger.warning("Auto-launch enabled but SteelSeries GG path not found.")

                 sleep(2)
                 return
            
            # If it was NOT running and NOW it is, trigger a reset
            if not self._gg_was_running:
                logger.info("SteelSeries GG detected! Reconnecting...")
                self.steelseries_api.reset()
                self._gg_was_running = True
                self._last_lighting_state = None

            now_sec = time()
            # Automatic configuration hot-reloading (e.g. when saved from Settings GUI)
            if now_sec - self._last_config_check_ts > 1.5:
                self._last_config_check_ts = now_sec
                try:
                    import os
                    if os.path.exists(self._config_file_path):
                        mtime = os.path.getmtime(self._config_file_path)
                        if self._last_config_mtime and mtime > self._last_config_mtime:
                            self._last_config_mtime = mtime
                            logger.info("External configuration change detected, reloading preferences...")
                            self.load_preferences()
                        elif not self._last_config_mtime:
                            self._last_config_mtime = mtime
                except Exception as e:
                    logger.debug("Failed checking config mtime: %s", e)

            now_ms = int(now_sec * 1000)

            # 0) AFK lighting state sync
            self._sync_lighting_state()

            # 1) YT poll (hızlı)
            self.volume_overlay.update()

            # Media poll: fetch both sources (extension has priority over SMTC)
            ext_data = self.extension_receiver.get_latest_data()
            
            with self._smtc_lock:
                smtc_fb = self._smtc_data.copy() if self._smtc_data else {}
            
            smtc_active = bool(smtc_fb and (smtc_fb.get("title") or smtc_fb.get("artist")))
            ext_active = ext_data is not None
            
            payload = None

            ext_playing = bool(ext_data.get("playing")) if ext_active else False
            smtc_playing = (not bool(smtc_fb.get("paused", True))) if smtc_active else False

            if smtc_active and ext_active and (ext_playing or not smtc_playing):
                # MERGE: Extension has best Title/Artist when extension is playing (or both paused)
                self._extension_last_data_ms = now_ms
                is_playing = ext_playing
                
                prog = int((ext_data.get("progress") or 0) * 1000)
                dur = int((ext_data.get("duration") or 0) * 1000)
                
                payload = {
                    "title": ext_data.get("title") or smtc_fb.get("title"),
                    "artist": ext_data.get("artist") or smtc_fb.get("artist"),
                    "progress": prog,
                    "duration": dur,
                    "paused": not is_playing,
                    "source": "youtube"
                }

            elif ext_active and not smtc_playing:
                # ONLY EXTENSION
                self._extension_last_data_ms = now_ms
                is_playing = ext_playing
                payload = {
                    "title": ext_data.get("title"),
                    "artist": ext_data.get("artist"),
                    "progress": int((ext_data.get("progress") or 0) * 1000),
                    "duration": int((ext_data.get("duration") or 0) * 1000),
                    "paused": not is_playing,
                    "source": "youtube"
                }

            elif smtc_active:
                # Stickiness for extension: if closed recently, don't glitch to basic SMTC names
                # unless SMTC is actively playing a different media app
                in_ext_lock = (not smtc_playing) and ((now_ms - self._extension_last_data_ms) < (self._extension_lock_seconds * 1000))
                if not in_ext_lock:
                    # ONLY SMTC
                    src_app = (smtc_fb.get("source") or "").lower()
                    video_keywords = (
                        "chrome", "edge", "firefox", "opera", "brave", "vivaldi",
                        "primevideo", "amazonvideo", "video", "netflix", "disney",
                        "vlc", "mpv", "mpc", "potplayer", "kodi", "wmplayer",
                    )
                    if "spotify" in src_app:
                        source = "spotify"
                    elif any(x in src_app for x in video_keywords):
                        source = "youtube"
                    else:
                        source = "generic"
                    
                    payload = smtc_fb.copy()
                    payload["source"] = source

            if payload:
                self._yt_last_seen_ms = now_ms
                self._yt_paused = bool(payload.get("paused", False))
                if not self._yt_paused:
                    self._yt_last_playing_ms = now_ms
                
                # Spotify priority check (Spotify wins if officially playing)
                spotify_active = (not self._spotify_paused) and \
                                 ((now_ms - self._spotify_last_playing_ms) <= self._spotify_hold_playing_ms)
                
                if not spotify_active:
                    # V5: Standardize lock with timeout
                    if self._lock.acquire(timeout=0.1):
                        try:
                            self._apply_to_player(self.player, payload, now_ms, source=payload.get("source", "youtube"))
                        finally:
                            self._lock.release()

            # 2) Spotify poll (normal) - only if Spotify is enabled
            if self.spotify_api and now_ms - self._last_spotify_poll_ms >= self._spotify_poll_ms:
                self._last_spotify_poll_ms = now_ms
                # Submit to persistent worker thread instead of creating new threads
                try:
                    self._spotify_queue.put_nowait(self.spotify_api)
                except Exception:
                    pass  # Queue full = previous poll still running, skip

            # 3) State selection with pause hysteresis (timer_threshold) to prevent
            # momentary SMTC status transitions (buffering/seeking in video apps) from flashing the clock.
            pause_grace_ms = max(int(getattr(self, "timer_threshold", 2000) or 2000), 1500)

            spotify_since_play = (now_ms - self._spotify_last_playing_ms) if self._spotify_last_playing_ms > 0 else 999999
            spotify_playing_active = (
                (not self._spotify_paused and spotify_since_play <= self._spotify_hold_playing_ms)
                or (self._spotify_paused and spotify_since_play <= pause_grace_ms)
            )

            yt_since_play = (now_ms - self._yt_last_playing_ms) if self._yt_last_playing_ms > 0 else 999999
            yt_playing_active = (
                (not self._yt_paused and yt_since_play <= self._yt_hold_playing_ms)
                or (self._yt_paused and yt_since_play <= pause_grace_ms)
            )

            if spotify_playing_active:
                self.state = State.SHOW_PLAYER
            elif yt_playing_active:
                self.state = State.SHOW_PLAYER
            else:
                self.state = State.SHOW_CLOCK

            # --- AFK Logic ---
            cur_pos = get_cursor_pos()
            if cur_pos != self._last_cursor_pos and cur_pos != (0, 0):
                self._last_cursor_pos = cur_pos
                self._last_cursor_move_ms = now_ms

            if self.user_preferences.get_preference("afk_enabled"):
                game_blocks_afk = (
                    bool(self.user_preferences.get_preference("afk_disable_in_games"))
                    and self.hardware_monitor.is_fullscreen_game_active()
                )
                if game_blocks_afk:
                    self._last_cursor_move_ms = now_ms
                    self._last_hook_input_ms = now_ms
                    if self._is_afk:
                        self._is_afk = False
                        self._afk_toggled_time = now_ms
                        self._afk_settled = False
                        self._last_sent_frame = None
                        logger.info("AFK State changed to: False (suppressed by active game)")
                        self._sync_lighting_state()
                else:
                    last_input_tick, win_idle_ms = get_last_input_info()
                    hook_idle_ms = max(0, now_ms - self._last_hook_input_ms) if self._last_hook_input_ms > 0 else win_idle_ms
                    cursor_idle_ms = max(0, now_ms - self._last_cursor_move_ms) if self._last_cursor_move_ms > 0 else win_idle_ms
                    idle_ms = min(win_idle_ms, hook_idle_ms, cursor_idle_ms)

                    if not self._is_afk:
                        # User is currently active: enter AFK when idle time exceeds timeout
                        if idle_ms >= self._afk_timeout_ms:
                            self._is_afk = True
                            self._afk_toggled_time = now_ms
                            self._afk_settled = False
                            self._afk_settled_time = now_ms + 1500
                            self._afk_baseline_input_tick = last_input_tick
                            self._afk_baseline_cursor_pos = cur_pos
                            self._last_sent_frame = None
                            logger.info(f"AFK State changed to: True (idle: {idle_ms}ms >= {self._afk_timeout_ms}ms)")
                            self._sync_lighting_state()
                            try:
                                if self.steelseries_api.send_frame([0] * 640):
                                    self._last_sent_frame = [0] * 640
                                    self._last_frame_sent_time = time()
                            except Exception:
                                pass
                    else:
                        # User is currently in AFK:
                        if not self._afk_settled:
                            # Allow 1.5s for hardware/driver transitions upon entering AFK to settle
                            if (now_ms - self._afk_toggled_time) >= 1500:
                                self._afk_settled = True
                                self._afk_settled_time = now_ms
                                self._afk_baseline_input_tick = last_input_tick
                                self._afk_baseline_cursor_pos = cur_pos
                        else:
                            # Once settled, ANY fresh mouse movement, click, or keypress wakes from AFK
                            if (
                                last_input_tick != self._afk_baseline_input_tick
                                or idle_ms < 1000
                                or self._last_hook_input_ms > self._afk_settled_time
                                or self._last_cursor_move_ms > self._afk_settled_time
                                or (cur_pos != self._afk_baseline_cursor_pos and cur_pos != (0, 0))
                            ):
                                self._is_afk = False
                                self._afk_toggled_time = now_ms
                                self._afk_settled = False
                                self._last_sent_frame = None
                                logger.info("AFK State changed to: False (woken by user input)")
                                self._sync_lighting_state()
            else:
                if self._is_afk:
                    self._is_afk = False
                    self._afk_toggled_time = now_ms
                    self._afk_settled = False
                    self._last_sent_frame = None
                    logger.info("AFK State changed to: False (afk_enabled disabled)")
                    self._sync_lighting_state()

            frame_data = None
            is_profile_active = False

            if self._is_afk:
                # AFK: Blank screen (all 640 bytes = 0x00)
                frame_data = [0] * 640
            # Calculator overlay takes priority over everything when active
            elif self._calculator_active:
                img = self.calculator.get_image()
                frame_data = convert_to_bitmap(img)
            # Volume & mic mute overlay > hardware monitor > everything else
            elif self.volume_overlay.should_display():
                img = self.volume_overlay.get_image()
                frame_data = convert_to_bitmap(img)
            # Profile Switch Overlay
            elif (
                hasattr(self, "profile_overlay")
                and self.profile_overlay.should_display()
                and self.user_preferences.get_preference("show_profile_switch_overlay")
            ):
                img = self.profile_overlay.get_image()
                frame_data = convert_to_bitmap(img)
                is_profile_active = True
            # Hardware monitor overlay
            elif self.display_hw_monitor or self.hardware_monitor.should_display() or self.hardware_monitor.is_game_running():
                img = self.hardware_monitor.get_image()
                frame_data = convert_to_bitmap(img)
            # Weather hotkey overlay
            elif hasattr(self, "weather") and self._weather_active_time > 0 and (time() - self._weather_active_time) < int(self.user_preferences.get_preference("weather_display_time")):
                img = self.weather.get_image()
                frame_data = convert_to_bitmap(img)
            else:
                if self.state == State.SHOW_CLOCK and self.display_clock:
                    img = self.timer.get_image()
                    frame_data = convert_to_bitmap(img)
                elif self.state == State.SHOW_PLAYER and self.display_player:
                    # Use lock with timeout to prevent deadlock hangs
                    if self._lock.acquire(timeout=0.1):
                        try:
                            img = self.player.next_step()
                        finally:
                            self._lock.release()
                    else:
                        img = None
                        
                    if img:
                        frame_data = convert_to_bitmap(img)

                    # paused threshold (Yedek kontrol, yukarıdaki mantık bunu zaten çözüyor ama kalsın)
                    if self.player.pause_started and (int(time() * 1000) - self.player.pause_started) > self.timer_threshold:
                        self.state = State.SHOW_CLOCK

            # Send if image changed OR periodically (every 1s when active, every 2s when AFK so the
            # blank [0]*640 frame stays active and prevents GameSense's 60s deinitializer-proc from
            # resetting the OLED back to the default background image).
            now_sec = time()
            if frame_data is not None:
                resend_interval = 2.0 if self._is_afk else 1.0
                force_resend = (now_sec - self._last_frame_sent_time > resend_interval) or is_profile_active
                if (frame_data != self._last_sent_frame) or force_resend:
                    try:
                        if self.steelseries_api.send_frame(frame_data):
                            self._last_sent_frame = frame_data
                            self._last_frame_sent_time = now_sec
                            self._frame_fail_start = 0  # Reset failure timer on success
                    except Exception as e:
                        logger.debug("Frame send failed: %s", e)
                        # Track prolonged failure — if frames fail for 30s+, force re-register
                        if self._frame_fail_start == 0:
                            self._frame_fail_start = now_sec
                        elif (now_sec - self._frame_fail_start) > 30.0:
                            logger.warning("Frames failing for 30s+, forcing SteelSeries re-registration...")
                            self._frame_fail_start = 0
                            try:
                                self.steelseries_api.reset()
                            except Exception as re:
                                logger.error("Re-registration failed: %s", re)

            # Heartbeat: keep the game(s) registered (every 25s)
            if now_sec - self._last_heartbeat_time > 25.0:
                self._last_heartbeat_time = now_sec
                try:
                    self.steelseries_api.heartbeat()
                except Exception:
                    pass

            # Auto-restart GG if its API has been unresponsive for 2+ minutes
            # This catches the case where GG.exe is running but its HTTP API is frozen
            api_silent_secs = now_sec - self.steelseries_api._last_success_time
            if api_silent_secs > 120 and (now_sec - self._last_gg_restart_attempt) > 300:
                logger.warning(
                    "Auto-Recovery: SteelSeries API unresponsive for %.0fs, restarting GG...",
                    api_silent_secs
                )
                self._last_gg_restart_attempt = now_sec
                # Run in background: the restart sleeps ~15s and must not
                # freeze rendering/overlays for that long
                Thread(target=self._restart_steelseries_gg, daemon=True, name="GG-Restart").start()

            sleep(1 / self.fps)

    def _spotify_worker_loop(self):
        """Persistent worker thread for Spotify polling (prevents thread leak)."""
        while self._running:
            try:
                spotify_api = self._spotify_queue.get(timeout=2.0)
                # V6: Move network call OUTSIDE the lock to prevent app-wide freezes
                song_data = spotify_api.fetch_song()
                if self._lock.acquire(timeout=0.1):
                    try:
                        self._poll_spotify(song_data)
                    finally:
                        self._lock.release()
            except Empty:
                continue  # No work, loop back
            except Exception:
                pass

    def _poll_spotify(self, song_data):
        try:
            if not song_data:
                return

            now_ms = int(time() * 1000)

            self._spotify_last_seen_ms = now_ms
            self._spotify_paused = bool(song_data.get("paused", False))
            if not self._spotify_paused:
                self._spotify_last_playing_ms = now_ms

            # Spotify paused ise ve YT aktif çalıyorsa overwrite etme
            yt_active = (not self._yt_paused) and \
                        ((now_ms - self._yt_last_playing_ms) <= self._yt_hold_playing_ms)
            
            if yt_active and self._spotify_paused:
                return

            payload = {
                "title": song_data.get("title", ""),
                "artist": song_data.get("artist", ""),
                "progress": int(song_data.get("progress") or 0),
                "duration": max(int(song_data.get("duration") or 1), 1),
                "paused": self._spotify_paused,
            }
            self._apply_to_player(self.player, payload, now_ms, source="spotify")
        except Exception:
            pass

    @staticmethod
    def _apply_to_player(player, data, now_ms: int, source="spotify"):
        """
        Scroll resetlenmesin diye:
        - title/artist değiştiyse update_song
        - aynıysa sadece seek_song (progress güncelle)
        """
        try:
            title = (data.get("title") or "").strip()
            artist = (data.get("artist") or "").strip()
            raw_progress = data.get("progress")
            raw_duration = data.get("duration")
            paused = bool(data.get("paused", False))

            # Check if song changed FIRST (before resolving duration)
            changed = True
            try:
                cur_title = player.title.content
                cur_artist = player.artist.content
                changed = (cur_title != title) or (cur_artist != artist)
            except Exception:
                changed = True

            # If SMTC returned -1 (unknown timeline) AND same song: keep cached values
            # If song changed with no timeline: update title/artist only, don't reset time
            if raw_duration is not None and int(raw_duration) == -1:
                if not changed:
                    # Same song — keep the player's existing cached data
                    duration = max(player.song_duration, 1)
                    progress = player.song_position if (raw_progress is None or int(raw_progress) == -1) else max(int(raw_progress), 0)
                else:
                    # New song but no timeline data from SMTC
                    # Update title/artist/source immediately, but DON'T reset duration
                    # to avoid 00:00/00:00 flash. Duration will come from Spotify API shortly.
                    player.title.set_text(title)
                    player.artist.set_text(artist)
                    player.source = source
                    player.song_position = 0
                    player.changed = True
                    player.step = 0
                    player.title.set_step(0)
                    player.artist.set_step(0)
                    # Skip the rest — don't call update_song with bad duration
                    if not player.paused and paused:
                        player.pause_started = now_ms
                    elif player.paused and not paused:
                        player.pause_started = 0
                    player.set_paused(paused)
                    return
            else:
                progress = int(raw_progress or 0)
                duration = int(raw_duration or 1)

            if duration <= 0:
                duration = 1
                progress = 0
            if progress < 0:
                progress = 0

            if changed:
                player.update_song(title, artist, progress, duration, paused, source)
            else:
                if player.source != source:
                    player.source = source
                    player.changed = True

                # Same song: update duration if a better value arrived
                # (e.g. SMTC set it to 1 initially, then Spotify API sent the real value)
                if duration > 1 and duration != player.song_duration:
                    player.song_duration = duration
                    player.changed = True

                # Update progress position (jitter-free sync)
                if not paused:
                    # --- JITTER-FREE SYNC BAŞLANGIÇ ---
                    current_pos = player.song_position
                    diff = progress - current_pos  # Gecikme farkı
                    
                    # Eğer kullanıcı videoyu ileri/geri sarmışsa (2sn üstü fark) zorla güncelle
                    if abs(diff) > 2000:
                         player.seek_song(progress)
                    
                    # Eğer duraklatılmışsa (PAUSE), tam yerini göster (flicker burada önemli değil)
                    elif paused and abs(diff) > 200:
                         player.seek_song(progress)
                    
                    # Eğer çalıyorsa ve fark çok büyük değilse:
                    # Geriye doğru gidişi engelle (Jitter'ın ana sebebi odur).
                    # Sadece ciddi bir gerileme/fark varsa müdahale et.
                    elif not paused:
                        # Eğer gelen veri mevcut konumumuzdan ÖNDEYSE ve fark 500ms+ ise snap yap
                        if diff > 500:
                            player.seek_song(progress)
                        # Eğer gelen veri ARKADAYSA ama fark küçükse güncelleme YAPMA (flicker önle)
                    # --- JITTER-FREE SYNC BİTİŞ ---

            # pause bookkeeping
            if not player.paused and paused:
                player.pause_started = now_ms
            elif player.paused and not paused:
                player.pause_started = 0

            player.set_paused(paused)
        except Exception:
            pass

    def update_config(self):
        if not self.user_preferences.load_preferences():
            logger.error("Failed to update configuration")
            return

        self.update_preferences()
        logger.info("Configuration updated successfully")

    def fast_unhook(self):
        """Emergency unhook of low-level hooks to prevent OS freeze on exit."""
        import ctypes
        logger.info("Performing fast unhook of LL hooks...")
        try:
            if hasattr(self, "_k_hook") and self._k_hook:
                ctypes.windll.user32.UnhookWindowsHookEx(self._k_hook)
                self._k_hook = None
            if hasattr(self, "_m_hook") and self._m_hook:
                ctypes.windll.user32.UnhookWindowsHookEx(self._m_hook)
                self._m_hook = None
            logger.info("LL hooks removed successfully.")
        except Exception as e:
            logger.warning(f"Fast unhook failed: {e}")

    def shutdown(self):
        """Gracefully stop all background threads and clean up resources."""
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        logger.info("Shutting down DisplayManager...")
        self._running = False
        
        # Fast unhook immediately to prevent mouse freeze
        self.fast_unhook()

        # Stop and remove GameSense games and redeploy native SteelSeries GG configs
        # so the 60s deinitialize timer never resets Rival 5 to orange after exit.
        if hasattr(self, "steelseries_api") and self.steelseries_api:
            try:
                self.steelseries_api.remove_game()
            except Exception:
                pass

        # Post a WM_QUIT anyway just to be polite and try to free the thread.
        # It's okay if this deadlocks internally because we already dropped the hooks.
        if getattr(self, "_hook_thread_id", None):
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._hook_thread_id, 0x0012, 0, 0)
            except Exception:
                pass

        # Drain queues so threads waiting on .get() can exit
        for q in (self._hotkey_action_queue, self._spotify_queue):
            try:
                while not q.empty():
                    q.get_nowait()
            except Exception:
                pass

        # Stop the extension receiver HTTP server
        if hasattr(self, "extension_receiver"):
            try:
                self.extension_receiver.stop()
            except Exception:
                pass

        # Close Discord IPC connection
        if hasattr(self, "_discord_ipc"):
            try:
                self._discord_ipc.close()
            except Exception:
                pass

        # Stop hardware monitoring (LHM worker + PresentMon ETW session)
        if hasattr(self, "hardware_monitor"):
            try:
                self.hardware_monitor.stop()
            except Exception:
                pass

        # Stop headset HID sync listener
        if getattr(self, "_hid_listener", None):
            try:
                self._hid_listener.stop()
            except Exception:
                pass
            self._hid_listener = None

        logger.info("DisplayManager shutdown complete")

    def _poll_smtc_loop(self):
        """Background thread that polls SMTC every 200ms using a persistent event loop."""
        import ctypes
        try:
            # COINIT_MULTITHREADED = 0x0 (required for WinRT async operations without a message pump)
            ctypes.windll.ole32.CoInitializeEx(0, 0)
        except Exception as e:
            logger.warning(f"CoInitializeEx failed: {e}")

        logger.info("SMTC poll loop started")
        last_valid_ts = 0.0

        async def runner():
            nonlocal last_valid_ts
            while self._running:
                try:
                    data = await self.windows_media.get_media_info()
                    now_ts = time()
                    # V5: Lock timeout for SMTC background thread
                    if self._smtc_lock.acquire(timeout=0.1):
                        try:
                            if data and (data.get("title") or data.get("artist")):
                                self._smtc_data = data
                                last_valid_ts = now_ts
                            elif (now_ts - last_valid_ts) > 1.5:
                                self._smtc_data = {}
                        finally:
                            self._smtc_lock.release()
                    
                except (OSError, RuntimeError) as e:
                    # COM threading error - reset manager
                    logger.debug(f"SMTC COM error, resetting: {e}")
                    self.windows_media.manager = None
                except Exception as e:
                    logger.debug(f"SMTC poll error: {e}")
                
                await asyncio.sleep(0.2)

        # Keep restarting the loop if it crashes (SMTC/WinRT can be unstable)
        while self._running:
            try:
                # Use persistent event loop instead of asyncio.run() to avoid thread issues
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(runner())
                finally:
                    loop.close()
            except (OSError, RuntimeError) as e:
                # COM/threading error - common with WinRT
                logger.debug(f"SMTC loop COM error, restarting: {e}")
            except Exception as e:
                logger.error(f"SMTC loop crashed, restarting in 2s: {e}")
            
            import time as _time
            _time.sleep(2)
            # Reset manager to force re-init
            self.windows_media.manager = None
            # Re-initialize COM for this thread
            try:
                ctypes.windll.ole32.CoUninitialize()
                ctypes.windll.ole32.CoInitializeEx(0, 0)
            except Exception:
                pass

    def _get_clock_mute_status(self):
        """Return (is_mic_muted, is_deafened) for the Clock left-side widget."""
        if not hasattr(self, "volume_overlay") or self.volume_overlay is None:
            return (False, False)
        vo = self.volume_overlay
        if vo._discord_connected:
            deaf = bool(vo._discord_deafened)
            if vo._mic_override is not None:
                mute = bool(vo._mic_override)
            elif vo._discord_muted is not None:
                mute = bool(vo._discord_muted) or deaf
            else:
                mute = bool(vo._last_mic_mute) or deaf
        else:
            deaf = bool(vo._last_mute)
            mute = bool(vo._last_mic_mute)
        return (mute, deaf)

    def trigger_mock_discord_state(self, toggle_mute=False, toggle_deaf=False):
        """Toggle or preview mocked Discord voice state (mute / deafen) on the OLED."""
        cur_mute = (
            bool(self.volume_overlay._discord_muted)
            if self.volume_overlay._discord_muted is not None
            else bool(self.volume_overlay._last_mic_mute)
        )
        cur_deaf = bool(self.volume_overlay._discord_deafened)
        if toggle_mute:
            cur_mute = not cur_mute
        if toggle_deaf:
            cur_deaf = not cur_deaf
        effective_mute = bool(cur_mute or cur_deaf)
        now = time()
        self._last_discord_state = effective_mute
        self._last_hw_state = effective_mute
        self._sync_lockout_until = now + 1.2
        if self._is_afk:
            self._is_afk = False
            self._afk_toggled_time = int(now * 1000)
            self._afk_settled = False
            self._sync_lighting_state()
        self.volume_overlay.trigger_mock_discord(muted=cur_mute, deafened=cur_deaf)
        if self._hid_listener:
            try:
                self._hid_listener.set_hardware_mute(effective_mute)
            except Exception as e:
                logger.debug("Mock Discord headset sync failed: %s", e)
        return cur_mute, cur_deaf

    def _hotkey_set_discord_mute(self, muted):
        """Apply a mute-hotkey press as ONE transaction against Discord.

        The critical part is priming _last_discord_state/_last_hw_state and
        arming the lockout BEFORE Discord echoes the change back. Without it,
        the RPC loop treats our own command's VOICE_SETTINGS_UPDATE echo as a
        fresh external Discord action, and the 10Hz steady-state branch wipes
        the optimistic UI state in the meantime — which is what made presses
        appear to do nothing and then flip a beat later.

        Returns True if the command was sent (caller falls back to the system
        mic path when False).
        """
        sent_ipc = self._discord_ipc.set_mute(muted)
        mock_enabled = bool(self.user_preferences.get_preference("discord_mock_enabled"))
        if not sent_ipc and not mock_enabled:
            return False

        now = time()
        # Mark this change as OURS so the echo is absorbed, not reprocessed
        self._last_discord_state = muted
        self._last_hw_state = muted
        self._sync_lockout_until = now + 1.2
        self._last_hotkey_req = muted
        self._last_hotkey_req_ts = now
        # A fresh press supersedes any previous override; it is re-armed below
        # only if Discord refuses this new request.
        self.volume_overlay._mic_override = None
        if not sent_ipc and mock_enabled:
            self.volume_overlay._discord_connected = True
            self.volume_overlay._discord_muted = muted

        # The RPC loop's sync path is suppressed for this change, so perform the
        # work it would have done: gate the real Windows capture devices...
        try:
            self.volume_overlay._sync_all_capture_mics(muted, source="Hotkey")
        except Exception as e:
            logger.debug("Hotkey system-mic sync failed: %s", e)
        # ...and mirror it onto the headset LED.
        if self._hid_listener:
            try:
                self._hid_listener.set_hardware_mute(muted)
            except Exception as e:
                logger.debug("Hotkey headset sync failed: %s", e)
        return True

    def _discord_rpc_loop(self):
        """Background thread that polls Discord's local IPC for mic mute/deaf state.
        
        Connects to Discord's named pipe (discord-ipc-0), sends GET_VOICE_SETTINGS
        every ~2 seconds, and updates the volume overlay's Discord mute state.
        Auto-reconnects if Discord restarts or pipe disconnects.
        """
        logger.info("Discord RPC poll thread started")
        
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass

        while self._running:
            try:
                mock_enabled = bool(self.user_preferences.get_preference("discord_mock_enabled"))
                has_client_id = bool(self._discord_ipc.client_id)

                # When Discord Application ID is not configured, run in Mock Discord Mode
                # (if enabled) so hardware headset mute, mute hotkeys, and OLED icons work
                # locally without spamming IPC connection attempts.
                if not has_client_id:
                    if mock_enabled:
                        self.volume_overlay._discord_connected = True
                        if self.volume_overlay._discord_muted is None:
                            self.volume_overlay._discord_muted = bool(self.volume_overlay._last_mic_mute)
                        if self.volume_overlay._discord_deafened is None:
                            self.volume_overlay._discord_deafened = False

                        new_hw_mute = self._hid_listener._last_state if self._hid_listener else None
                        new_hw_event_ts = self._hid_listener._last_hw_ts if self._hid_listener else 0.0
                        now = time()
                        if (
                            new_hw_mute is not None
                            and new_hw_event_ts > self._last_hw_event_ts
                            and new_hw_mute != self._last_hw_state
                            and now > self._sync_lockout_until
                        ):
                            logger.info(f"[MOCK-DISCORD] Hardware button pressed: {self._last_hw_state} -> {new_hw_mute}")
                            self._last_hotkey_req = None
                            self.volume_overlay._mic_override = None
                            self._last_hw_state = new_hw_mute
                            self._last_discord_state = new_hw_mute
                            self._last_hw_event_ts = new_hw_event_ts
                            self._sync_lockout_until = now + 1.0
                            self.volume_overlay.set_discord_mute(
                                new_hw_mute,
                                bool(self.volume_overlay._discord_deafened),
                                True,
                            )
                        sleep(0.25)
                    else:
                        self.volume_overlay.set_discord_mute(None, None, False)
                        sleep(5.0)
                    continue

                # Try to connect if not already connected
                if not self._discord_ipc.is_connected:
                    if self._discord_ipc.connect():
                        now = time()
                        logger.info("Discord IPC connected — now tracking mute state")
                        
                        # JITTER BRIDGE: If we were disconnected for less than 10 seconds, 
                        # don't reset _last_discord_state. This prevents "sync ripples" 
                        # if the pipe just flickered during a movie/game.
                        if self._discord_disconnect_ts > 0 and (now - self._discord_disconnect_ts) < 10.0:
                            logger.info("Discord IPC: Jitter bridge active (reconnected within 10s)")
                        else:
                            # Full reset if it's been a long time or a fresh start
                            self._last_discord_state = None
                            self._last_hw_state = None
                            
                        self._discord_disconnect_ts = 0.0
                        
                        # Initial poll right after connecting (only if already authenticated)
                        voice = self._discord_ipc.get_voice_settings()
                        if voice and self._discord_ipc._authenticated:
                            # Establish initial baseline so the first loop iteration doesn't think it's a 'change'
                            initial_mute = bool(voice["mute"] or voice["deaf"])
                            self._last_discord_state = initial_mute
                            
                            # Also sync to UI immediately
                            self.volume_overlay.set_discord_mute(
                                voice["mute"], voice["deaf"], True
                            )
                            # LOCKOUT: Give the connection 2 seconds to settle before allowed button syncs.
                            self._sync_lockout_until = now + 2.0
                            logger.info(f"Discord IPC: Initial state established (Muted={initial_mute}) - Sync Lockout for 2s")
                    else:
                        # Discord not running — hold state for 10s before clearing (unless mock mode is enabled)
                        if mock_enabled:
                            self.volume_overlay._discord_connected = True
                        else:
                            if self._discord_disconnect_ts == 0.0:
                                self._discord_disconnect_ts = time()
                            if (time() - self._discord_disconnect_ts) > 10.0:
                                self.volume_overlay.set_discord_mute(None, None, False)
                        
                        sleep(10)
                        continue
                
                # Listen/Poll for voice settings
                voice = self._discord_ipc.get_voice_settings()

                # Pipe open but OAuth incomplete: get_voice_settings only
                # returns a {mute: False} placeholder, which would clobber real
                # state and make the OLED lie (icon unmuted while the system
                # mic is muted via the hotkey fallback). Treat as disconnected
                # (unless mock mode is enabled):
                if voice is not None and not self._discord_ipc._authenticated:
                    if mock_enabled:
                        self.volume_overlay._discord_connected = True
                    else:
                        self.volume_overlay.set_discord_mute(None, None, False)
                    sleep(1.0)
                    continue

                if voice:
                    # --- DIRECT EVENT-BASED SYNC LOGIC ---
                    # The lockout window suppresses echo events while one side
                    # is still propagating a change to the other (prevents the
                    # headset<->Discord mute ping-pong).
                    new_discord_mute = bool(voice["mute"] or voice["deaf"])
                    new_hw_mute = self._hid_listener._last_state if self._hid_listener else None
                    new_hw_event_ts = self._hid_listener._last_hw_ts if self._hid_listener else 0.0
                    now = time()

                    # 1. Did Discord change?
                    if new_discord_mute != self._last_discord_state:
                        if now > self._sync_lockout_until:
                            # Diagnostic: Discord contradicting a very recent
                            # hotkey request means Discord itself refused the
                            # SET (e.g. self-mute outside a voice channel),
                            # not that we are fighting ourselves.
                            # Only a genuine refusal if OUR request is recent AND
                            # no hardware button press happened since it — a
                            # headset press also changes Discord, and
                            # misreading that as a refusal armed a bogus
                            # override and skipped the real sync (which is what
                            # left the headset and Discord pointing different
                            # ways).
                            if (self._last_hotkey_req is not None
                                    and (now - self._last_hotkey_req_ts) < 3.0
                                    and new_hw_event_ts <= self._last_hotkey_req_ts
                                    and new_discord_mute != self._last_hotkey_req):
                                requested = self._last_hotkey_req
                                logger.warning(
                                    "[DISCORD-SYNC] Discord refused our hotkey request "
                                    "(asked mute=%s, Discord reports mute=%s) — holding the "
                                    "Windows mic at mute=%s so the keypress still applies",
                                    requested, new_discord_mute, requested,
                                )
                                # Honor the user's intent: pin the real capture
                                # devices where they asked and show that on the
                                # OLED. Deliberately skip set_discord_mute(),
                                # which would re-sync the mic to Discord's
                                # (refused) state and un-mute them again.
                                self._last_discord_state = new_discord_mute
                                self._last_hw_state = new_discord_mute
                                self._last_hw_event_ts = new_hw_event_ts
                                self._last_hotkey_req = None
                                self.volume_overlay._mic_override = requested
                                try:
                                    self.volume_overlay._sync_all_capture_mics(
                                        requested, source="Hotkey-Override")
                                except Exception as e:
                                    logger.debug("Override mic sync failed: %s", e)
                                sleep(0.1)
                                continue
                            logger.info(f"[DISCORD-SYNC] Discord state changed: {self._last_discord_state} -> {new_discord_mute}")
                            # A genuine Discord-side change (UI click) is fresh
                            # user intent and supersedes any refusal override.
                            self.volume_overlay._mic_override = None
                            self._last_discord_state = new_discord_mute
                            self._last_hw_state = new_discord_mute
                            self._last_hw_event_ts = new_hw_event_ts

                            # Prevent stale hardware echoes from overriding this change
                            self._sync_lockout_until = now + 1.2

                            self.volume_overlay.set_discord_mute(voice["mute"], voice["deaf"], True)
                            if self._hid_listener:
                                self._hid_listener.set_hardware_mute(new_discord_mute)

                    # 2. Did Hardware change?
                    elif (
                        new_hw_mute is not None
                        and new_hw_event_ts > self._last_hw_event_ts
                        and new_hw_mute != self._last_hw_state
                    ):
                        if now > self._sync_lockout_until:
                            logger.info(f"[HARDWARE-SYNC] Hardware button pressed: {self._last_hw_state} -> {new_hw_mute}")
                            # A physical press is fresh intent and supersedes
                            # any pending hotkey request/override.
                            self._last_hotkey_req = None
                            self.volume_overlay._mic_override = None
                            self._last_hw_state = new_hw_mute
                            self._last_discord_state = new_hw_mute
                            self._last_hw_event_ts = new_hw_event_ts

                            # Discord takes ~1s to reflect the change
                            self._sync_lockout_until = now + 1.5

                            self.volume_overlay.set_discord_mute(new_hw_mute, False, True)
                            self._discord_ipc.set_mute(new_hw_mute)
                        else:
                            # A real button press landed inside the lockout.
                            # Deliberately do NOT consume it: leaving the
                            # trackers untouched means it is re-evaluated and
                            # applied the moment the lockout expires, instead
                            # of being silently dropped (16 presses produced
                            # only 3 syncs before this). Echoes of our own
                            # writes are already filtered in HIDListener by the
                            # 400ms _last_write_ts debounce, so nothing bounces.
                            logger.debug(
                                "[HARDWARE-SYNC] press during lockout, deferring: %s", new_hw_mute)

                    else:
                        # 3. Steady State
                        if now > self._sync_lockout_until:
                            self._last_discord_state = new_discord_mute
                            if new_hw_mute is not None:
                                self._last_hw_state = new_hw_mute
                            if new_hw_event_ts > self._last_hw_event_ts:
                                self._last_hw_event_ts = new_hw_event_ts

                            self.volume_overlay._discord_muted = voice["mute"]
                            self.volume_overlay._discord_deafened = voice["deaf"]
                            self.volume_overlay._discord_connected = True

                else:
                    # Discord poll failed or pipe closed
                    if self._discord_disconnect_ts == 0.0:
                        self._discord_disconnect_ts = time()
                        logger.warning("Discord RPC pipe disconnected - initiating jitter bridge (10s)")

                    
                    if (time() - self._discord_disconnect_ts) > 10.0:
                        self.volume_overlay.set_discord_mute(None, None, False)
                        self._last_discord_state = None
                        
                    self._discord_ipc.close()
                    sleep(2)

                sleep(0.1) # Faster poll loop! Changed from 0.5 to 0.1
                
            except Exception as e:
                logger.debug(f"Discord RPC loop error: {e}")
                self.volume_overlay.set_discord_mute(None, None, False)
                self._discord_ipc.close()
                sleep(10)  # Wait before retry on error
    def trigger_profile_overlay_test(self):
        """Test triggering the profile overlay, cycling through sample devices/profiles."""
        if not hasattr(self, "profile_overlay"):
            return
        test_samples = [
            ("profile", "Battlefield 6", None),
            ("profile", "Black Desert", None),
            ("apex_7", "Desktop", 0),
            ("apex_7", "Black Desert", 1),
            ("rival_5", "Black Desert", None),
            ("profile", "Desktop", None),
        ]
        dev, prof, slot = test_samples[self._test_profile_idx % len(test_samples)]
        self._test_profile_idx += 1
        self.profile_overlay.trigger(dev, prof, slot)

    def _profile_monitor_loop(self):
        """
        Background loop polling SteelSeries GG and Engine APIs for active profile switches.
        - Polling /v1/loadouts/active detects game/desktop loadout switching (e.g. Black Desert, Desktop).
        - Polling /v1/connectedDevices detects individual device profile/slot switches (e.g. SteelSeries + F9).
        - Purely in-memory mapping to prevent any modifications or corruption to Engine's SQLite database.
        """
        logger.info("[PROFILE-MONITOR] Profile monitor loop started")
        import urllib3
        from json import loads
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        monitor_http = urllib3.PoolManager(
            cert_reqs='CERT_NONE',
            timeout=urllib3.Timeout(connect=1.0, read=2.0),
            retries=False
        )

        last_loadout_id = None
        last_loadout_name = None
        last_dev_configs = {}      # dev_name -> active_cfg_id
        cfg_to_loadout = {}        # cfg_id -> loadout_name
        last_loadouts_cache_ts = 0.0
        last_loadout_trigger_ts = 0.0
        initial_sync = True

        while self._running:
            try:
                if not self.user_preferences.get_preference("show_profile_switch_overlay"):
                    sleep(1.5)
                    continue

                gg_url = getattr(self.steelseries_api, "gg_encrypted_address", None)
                eng_url = getattr(self.steelseries_api, "encrypted_address", None)

                if not gg_url or not eng_url:
                    sleep(2.0)
                    continue

                now = time()

                # 1. Periodically cache loadout <-> config mappings (every 15s)
                if (now - last_loadouts_cache_ts) > 15.0:
                    try:
                        r_loadouts = monitor_http.request("GET", f"{gg_url}/v1/loadouts")
                        if r_loadouts.status == 200:
                            data = loads(r_loadouts.data.decode("utf-8", errors="replace"))
                            new_map = {}
                            loadouts_list = data.get("loadouts", data) if isinstance(data, dict) else data
                            if isinstance(loadouts_list, list):
                                desktop_cfg_ids = set()
                                for l in loadouts_list:
                                    if isinstance(l, dict) and (l.get("name") or "").lower() == "desktop":
                                        for c in l.get("configs", []):
                                            if c.get("config_id"):
                                                desktop_cfg_ids.add(c.get("config_id"))
                                for l in loadouts_list:
                                    if isinstance(l, dict) and (l.get("name") or "").lower() != "desktop":
                                        lname = l.get("name")
                                        if lname:
                                            for c in l.get("configs", []):
                                                cid = c.get("config_id")
                                                if cid and cid not in desktop_cfg_ids:
                                                    new_map[cid] = lname
                            cfg_to_loadout = new_map
                            last_loadouts_cache_ts = now
                    except Exception as e:
                        logger.debug(f"[PROFILE-MONITOR] Loadouts cache error: {e}")

                # 2. Check GG Active Loadout (Desktop, Black Desert, Battlefield 6, etc.)
                try:
                    r_act = monitor_http.request("GET", f"{gg_url}/v1/loadouts/active")
                    if r_act.status == 200:
                        act_data = loads(r_act.data.decode("utf-8", errors="replace"))
                        loadout = act_data.get("activeLoadout", {})
                        lid = loadout.get("id")
                        lname = loadout.get("name")
                        if lname:
                            if initial_sync:
                                last_loadout_id = lid
                                last_loadout_name = lname
                            elif lname != last_loadout_name or (lid and lid != last_loadout_id):
                                logger.info(f"[PROFILE-MONITOR] Active loadout switched: '{last_loadout_name}' -> '{lname}'")
                                last_loadout_id = lid
                                last_loadout_name = lname
                                last_loadout_trigger_ts = now
                                self.profile_overlay.trigger(raw_device_name="profile", raw_profile_name=lname)
                except Exception as e:
                    logger.debug(f"[PROFILE-MONITOR] Active loadout poll error: {e}")

                # 3. Check Connected Devices (e.g. Apex 7 onboard switch SteelSeries+F9)
                try:
                    r_dev = monitor_http.request("GET", f"{eng_url}/v1/connectedDevices")
                    if r_dev.status == 200:
                        dev_data = loads(r_dev.data.decode("utf-8", errors="replace"))
                        devices = dev_data.get("devices", []) if isinstance(dev_data, dict) else []
                        for d in devices:
                            dname = d.get("name")
                            cfgs = d.get("configuration_metadata", [])
                            # CRITICAL: Prioritize active == 1 over last_deployed == 1
                            act_cfg = next((c for c in cfgs if c.get("active") == 1), None) or next((c for c in cfgs if c.get("last_deployed") == 1), None)
                            if not act_cfg:
                                continue
                            cfg_id = act_cfg.get("id")
                            cfg_name = act_cfg.get("name")

                            if initial_sync:
                                last_dev_configs[dname] = cfg_id
                                continue

                            if last_dev_configs.get(dname) != cfg_id:
                                last_dev_configs[dname] = cfg_id

                                # Suppress duplicate device trigger if overall loadout switch just fired
                                if (now - last_loadout_trigger_ts) < 6.0:
                                    logger.debug(f"[PROFILE-MONITOR] Device {dname} switch to '{cfg_name}' suppressed (part of loadout switch)")
                                    continue

                                slot = None
                                if cfg_name and "config" in cfg_name.lower():
                                    parts = cfg_name.split()
                                    if len(parts) >= 2 and parts[-1].isdigit():
                                        try:
                                            slot = int(parts[-1]) - 1
                                        except Exception:
                                            pass

                                display_name = cfg_to_loadout.get(cfg_id) or cfg_name
                                logger.info(f"[PROFILE-MONITOR] Device {dname} configuration changed to '{display_name}' (slot={slot})")
                                self.profile_overlay.trigger(raw_device_name=dname, raw_profile_name=display_name, slot=slot)
                except Exception as e:
                    logger.debug(f"[PROFILE-MONITOR] Connected devices poll error: {e}")

                if initial_sync:
                    initial_sync = False

                sleep(0.5)

            except Exception as e:
                logger.debug(f"[PROFILE-MONITOR] Loop exception: {e}")
                sleep(2.0)
