"""
Hardware Monitor for OLED display.
Uses LibreHardwareMonitor via pythonnet to get CPU/GPU temps.
Includes WMI fallback.

Thread-safety: LHM runs in dedicated worker thread to avoid COM threading issues.
"""
import logging
from time import time, sleep
from threading import Thread, Lock
import os

import psutil
from PIL import Image, ImageDraw, ImageFont

from src.image_utils import fetch_content_path
from src.fps_monitor import FPSMonitor

logger = logging.getLogger("OLED Customizer.HardwareMonitor")

# Try to load LibreHardwareMonitor
_lhm_available = False

# WMI Fallback
try:
    import wmi
    _wmi_available = True
except ImportError:
    _wmi = None
    _wmi_available = False

try:
    # First check if admin
    import ctypes
    if ctypes.windll.shell32.IsUserAnAdmin() == 0:
        logger.warning("Not running as Admin - LHM sensors might fail")

    import HardwareMonitor.Hardware as HW
    _lhm_available = True
    logger.info("LibreHardwareMonitor loaded successfully")
except Exception as e:
    logger.warning(f"LibreHardwareMonitor not available: {e}")


class _LHMWorker:
    """
    Dedicated worker thread that owns all LibreHardwareMonitor COM objects.
    Main thread reads from cached values, never touching LHM directly.
    This fixes COM threading crashes (RPC_E_WRONG_THREAD).
    """
    
    def __init__(self):
        self._cache = {}
        self._cache_lock = Lock()
        self._running = False
        self._computer = None
        self._polling_interval = 1000 # Default 1s
        
    def start(self, interval=1000):
        self._polling_interval = interval
        if self._running:
            return
        self._running = True
        Thread(target=self._worker_loop, daemon=True, name="LHM-Worker").start()
        
    def _worker_loop(self):
        """Runs in dedicated thread - owns all COM objects and performs all system polling."""
        try:
            # Initialize COM for this thread
            import ctypes
            ctypes.windll.ole32.CoInitialize(0)
        except Exception:
            pass
            
        try:
            if _lhm_available:
                self._computer = HW.Computer()
                self._computer.IsCpuEnabled = True
                self._computer.IsGpuEnabled = True
                self._computer.IsMemoryEnabled = True
                self._computer.Open()
                logger.info("LHM Worker: Hardware monitoring initialized")
        except Exception as e:
            logger.warning(f"LHM Worker: LHM sensors not available (not Admin?): {e}")
            
        import psutil
        import wmi
        _wmi_handle = None
        try:
            _wmi_handle = wmi.WMI(namespace="root/WMI")
        except Exception:
            pass

        last_wmi_poll = 0
        wmi_temp_cache = None

        # Main update loop
        while self._running:
            try:
                new_cache = {}
                now = time()

                # 1. Update LHM Sensors (if available)
                if self._computer:
                    try:
                        for hw in self._computer.Hardware:
                            hw_type = str(hw.HardwareType).lower()
                            hw_name = str(hw.Name)
                            hw.Update()
                            for sensor in hw.Sensors:
                                if sensor.Value is not None:
                                    key = (hw_type, hw_name, str(sensor.SensorType).lower(), str(sensor.Name).lower())
                                    new_cache[key] = float(sensor.Value)
                            for sub in hw.SubHardware:
                                sub.Update()
                                for sensor in sub.Sensors:
                                    if sensor.Value is not None:
                                        key = (hw_type, hw_name, str(sensor.SensorType).lower(), str(sensor.Name).lower())
                                        new_cache[key] = float(sensor.Value)
                    except Exception as e:
                        logger.debug(f"LHM poll error: {e}")

                # 2. Update psutil Sensors (CPU Usage, RAM)
                new_cache[('cpu', 'auto', 'load', 'total')] = psutil.cpu_percent(interval=None)
                mem = psutil.virtual_memory()
                new_cache[('ram', 'auto', 'load', 'percent')] = mem.percent
                new_cache[('ram', 'auto', 'data', 'used_gb')] = mem.used / (1024**3)
                new_cache[('ram', 'auto', 'data', 'total_gb')] = mem.total / (1024**3)

                # 3. Update WMI Sensors (Fallback CPU Temp - Cache for 2 seconds to avoid leak/lag)
                if _wmi_handle and now - last_wmi_poll > 2.0:
                    try:
                        last_wmi_poll = now
                        temps = _wmi_handle.MSAcpi_ThermalZoneTemperature()
                        for t in temps:
                            c = (t.CurrentTemperature / 10.0) - 273.15
                            if c > 0:
                                wmi_temp_cache = c
                                break
                    except Exception:
                        pass
                
                if wmi_temp_cache is not None:
                    new_cache[('cpu', 'auto', 'temperature', 'fallback')] = wmi_temp_cache

                with self._cache_lock:
                    self._cache = new_cache
                    
            except Exception as e:
                logger.debug(f"LHM Worker loop error: {e}")
                
            sleep(max(0.2, self._polling_interval / 1000.0))
            
    def get_sensor(self, hw_type, sensor_type, name_contains=None, hw_name=None):
        """Thread-safe read from cache."""
        with self._cache_lock:
            # Priority 1: Exact matches
            for (hw, hn, st, name), value in self._cache.items():
                if hw_type.lower() not in hw: continue
                if hw_name and hw_name.lower() != "auto" and hw_name.lower() not in hn.lower(): continue
                if sensor_type.lower() not in st: continue
                if name_contains and name_contains.lower() not in name: continue
                return value
            
            # Priority 2: Fallbacks (e.g. WMI temp if LHM temp missing)
            if hw_type.lower() == "cpu" and sensor_type.lower() == "temperature":
                return self._cache.get(('cpu', 'auto', 'temperature', 'fallback'))
            if hw_type.lower() == "cpu" and sensor_type.lower() == "load":
                return self._cache.get(('cpu', 'auto', 'load', 'total'))
            if hw_type.lower() == "ram":
                if sensor_type.lower() == "load": return self._cache.get(('ram', 'auto', 'load', 'percent'))
                if name_contains == "used": return self._cache.get(('ram', 'auto', 'data', 'used_gb'))
                if name_contains == "total": return self._cache.get(('ram', 'auto', 'data', 'total_gb'))
                
        return None

    def get_available_gpus(self):
        """Return list of detected GPU names."""
        gpus = set()
        with self._cache_lock:
            for (hw, hn, st, name) in self._cache.keys():
                if "gpu" in hw:
                    gpus.add(hn)
        return sorted(list(gpus))
        
    def stop(self):
        """Signals worker thread to stop and releases COM locks."""
        self._running = False
        try:
            if self._computer:
                self._computer.Close()
        except Exception:
            pass


# Global worker instance
_lhm_worker = _LHMWorker()


class HardwareMonitor:
    """
    Hardware monitor overlay for OLED display.
    Only gather data once in the background. get_image just reads the cache.
    """
    
    def __init__(self, config, preferences, timeout=3.0):
        self.config = config
        self.preferences = preferences
        self.timeout = timeout
        self._last_trigger = 0.0
        
        self.FONT = ImageFont.truetype(
            font=fetch_content_path("fonts/VerdanaBold.ttf"),
            size=11,
        )
        
        self.cpu_icon = self._load_icon("cpu_icon.png")
        self.gpu_icon = self._load_icon("gpu_icon.png")
        self.ram_icon = self._load_icon("ram_icon.png")
        
        interval = preferences.get_preference("hw_polling_interval") or 1000
        _lhm_worker.start(interval)
        
        self.show_fps = bool(preferences.get_preference("show_game_fps"))
        self.auto_switch_game = bool(preferences.get_preference("auto_hw_monitor_game"))
        self._game_detect_ts = 0.0
        self._game_detect_cached = False
        self.fps_monitor = FPSMonitor()
        if self.show_fps or self.auto_switch_game:
            self.fps_monitor.start()

    def update_preferences(self, preferences):
        self.show_fps = bool(preferences.get_preference("show_game_fps"))
        self.auto_switch_game = bool(preferences.get_preference("auto_hw_monitor_game"))
        if self.show_fps or self.auto_switch_game:
            self.fps_monitor.start()
        else:
            self.fps_monitor.stop()
        interval = preferences.get_preference("hw_polling_interval") or 1000
        _lhm_worker._polling_interval = interval
        if not _lhm_worker._running:
            _lhm_worker.start(interval)

    def _load_icon(self, filename):
        try:
            path = fetch_content_path(f"assets/icons/{filename}")
            if os.path.exists(path):
                return Image.open(path).convert("1")
        except Exception as e:
            logger.warning(f"Failed to load icon {filename}: {e}")
        return None

    def trigger(self):
        self._last_trigger = time()

    def should_display(self) -> bool:
        return (time() - self._last_trigger) < self.timeout

    def is_fullscreen_game_active(self) -> bool:
        """
        Detect whether a fullscreen or borderless-windowed game is currently active
        in the foreground. Cached for 1.0s to avoid per-frame Win32/psutil overhead.
        """
        now = time()
        if now - self._game_detect_ts < 1.0:
            return self._game_detect_cached

        self._game_detect_ts = now
        result = False
        try:
            import ctypes
            import ctypes.wintypes
            import psutil

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                self._game_detect_cached = False
                return False

            desktop = user32.GetDesktopWindow()
            shell = user32.GetShellWindow()
            if hwnd == desktop or hwnd == shell:
                self._game_detect_cached = False
                return False

            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

            try:
                proc = psutil.Process(pid.value)
                name = proc.name().lower()

                ignored = {
                    "explorer.exe", "chrome.exe", "firefox.exe", "msedge.exe",
                    "opera.exe", "brave.exe", "vivaldi.exe", "discord.exe", "spotify.exe",
                    "devenv.exe", "code.exe", "antigravity.exe", "pycharm64.exe", "idea64.exe",
                    "obs64.exe", "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe",
                    "ds4windows.exe", "steelseriesgg.exe", "steelseriesggclient.exe",
                    "steelseriesengine.exe", "steelseriesengine3.exe", "steelseries-oled-hub.exe",
                    "oled-customizer.exe", "searchapp.exe", "searchhost.exe",
                    "applicationframehost.exe", "systemsettings.exe", "lockapp.exe",
                    "taskmgr.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
                    "windowsterminal.exe", "notepad.exe", "vlc.exe", "mpc-hc64.exe",
                    "mpv.exe", "potplayer.exe", "potplayermini64.exe"
                }
                if name in ignored:
                    self._game_detect_cached = False
                    return False

                # 1. Check Direct3D Exclusive Fullscreen state via Shell32
                try:
                    quns_state = ctypes.c_int(0)
                    if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(quns_state)) == 0:
                        # QUNS_RUNNING_D3D_FULL_SCREEN = 3
                        if quns_state.value == 3:
                            self._game_detect_cached = True
                            return True
                except Exception:
                    pass

                # 2. Check Fullscreen / Borderless Windowed monitor coverage
                rect = ctypes.wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                monitor = user32.MonitorFromWindow(hwnd, 2)

                class MONITORINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", ctypes.wintypes.DWORD),
                        ("rcMonitor", ctypes.wintypes.RECT),
                        ("rcWork", ctypes.wintypes.RECT),
                        ("dwFlags", ctypes.wintypes.DWORD)
                    ]
                mi = MONITORINFO()
                mi.cbSize = ctypes.sizeof(MONITORINFO)
                user32.GetMonitorInfoW(monitor, ctypes.byref(mi))

                is_fullscreen = (rect.left <= mi.rcMonitor.left and
                                 rect.top <= mi.rcMonitor.top and
                                 rect.right >= mi.rcMonitor.right and
                                 rect.bottom >= mi.rcMonitor.bottom)

                if is_fullscreen:
                    result = True

            except Exception:
                pass

        except Exception:
            pass

        self._game_detect_cached = result
        return result

    def is_game_running(self) -> bool:
        if not self.auto_switch_game:
            return False
        return self.is_fullscreen_game_active()

    def _get_lhm_sensor(self, hw_type, sensor_type, name_contains=None):
        selected_gpu = self.preferences.get_preference("selected_gpu") or "Auto"
        return _lhm_worker.get_sensor(hw_type, sensor_type, name_contains, hw_name=selected_gpu if hw_type.lower() == "gpu" else None)

    def get_available_gpus(self):
        return _lhm_worker.get_available_gpus()

    def stop(self):
        try:
            self.fps_monitor.stop()
            _lhm_worker.stop()
        except Exception:
            pass

    def get_image(self):
        w, h = self.config.width, self.config.height
        image = Image.new("1", (w, h), color=self.config.secondary)
        draw = ImageDraw.Draw(image)

        # --- Data Gathering (Cache-Only (V7)) ---
        cpu_temp = self._get_lhm_sensor("Cpu", "Temperature", "Tctl")
        if not cpu_temp: cpu_temp = self._get_lhm_sensor("Cpu", "Temperature", "Package")
        
        cpu_usage = self._get_lhm_sensor("Cpu", "Load", "total") or 0
            
        gpu_temp = self._get_lhm_sensor("Gpu", "Temperature", "Core")
        if not gpu_temp: gpu_temp = self._get_lhm_sensor("Gpu", "Temperature", "GPU")
        
        gpu_load = self._get_lhm_sensor("Gpu", "Load", "Core")
        if not gpu_load: gpu_load = self._get_lhm_sensor("Gpu", "Load", "GPU")
            
        ram_used = self._get_lhm_sensor("Ram", "data", "used") or 0
        ram_total = self._get_lhm_sensor("Ram", "data", "total") or 0
        
        fps = 0
        if self.show_fps:
            fps = self.fps_monitor.get_fps()
            if fps <= 0:
                lhm_fps = self._get_lhm_sensor("Gpu", "Factor", "Frame Rate")
                if lhm_fps: fps = int(lhm_fps)

        # --- Layout ---
        col_width = w // 3
        c1_x, c2_x, c3_x = 0, col_width, col_width * 2
        y_icon, y_text1, y_text2 = 0, 13, 26

        def draw_centered(text, cx, cy, font=None):
            bbox = draw.textbbox((0, 0), text, font=font or self.FONT)
            tw = bbox[2] - bbox[0]
            draw.text((cx + (col_width - tw) / 2, cy), text, font=font or self.FONT, fill=self.config.primary)

        def paste_centered(icon, cx, cy):
            if icon: image.paste(icon, (int(cx + (col_width - 12) // 2), cy))

        # CPU
        paste_centered(self.cpu_icon, c1_x, y_icon)
        draw_centered(f"{int(cpu_temp)}°" if cpu_temp else "--", c1_x, y_text1)
        draw_centered(f"{int(cpu_usage)}%", c1_x, y_text2)

        # GPU
        paste_centered(self.gpu_icon, c2_x, y_icon)
        draw_centered(f"{int(gpu_temp)}°" if gpu_temp else "--", c2_x, y_text1)
        draw_centered(f"{int(gpu_load) if gpu_load else 0}%", c2_x, y_text2)

        # RAM / FPS (headset battery lives on the clock — no free slot here)
        paste_centered(self.ram_icon, c3_x, y_icon)
        draw_centered(f"{ram_used:.1f}G", c3_x, y_text1)
        if self.show_fps:
            if fps > 0:
                val_text = f"{int(fps)}" + ("" if fps >= 100 else " FPS")
                draw_centered(val_text, c3_x, y_text2)
            else:
                draw_centered("Idle", c3_x, y_text2)
        else:
            draw_centered(f"{int(ram_total)}GB", c3_x, y_text2)

        return image
