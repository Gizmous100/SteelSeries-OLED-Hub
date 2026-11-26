
import ctypes
from threading import Thread, Lock, current_thread
import time
import logging
import os
import sys

logger = logging.getLogger("OLED Customizer.FPSMonitor")


# --- PresentMon DLL Interface (fps_inspector_sdk build of PresentMon) ---
# C++ exports (extern "C"):
#   int StartEventRecording(int TargetPid, int arraySize)   // TargetPid 0 = all processes
#   int StopEventRecording()
#   int GetCurrentData(int numSamples, EventScores *out, double *timestamps, int *returnedSamples)
# EventScores = 6 packed doubles: fps, flip, deltaReady, deltaDisplayed, timeTaken, screenTime.
# GetCurrentData returns the newest min(numSamples, available) samples, oldest first,
# without consuming them; timestamps are in seconds. The buffer is a ring buffer.
class PresentMonExitCodes:
    STATUS_OK = 0
    GENERAL_ERROR = 1000
    PRIVILIGIES_ERROR = 1007


def _is_running_as_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


class FPSMonitor:
    """
    Live FPS of the foreground application, measured with PresentMon (ETW).

    Every DLL sample is the *instantaneous* FPS of one present (1000 / ms since the
    previous present on the same swap chain). Recording all processes (pid 0) mixes
    every app into one untagged buffer, and showing only the newest sample made the
    value jump wildly (e.g. 100 -> 2000). Instead this monitor:
      * records only the foreground application's PID and restarts the ETW session
        when the foreground application changes,
      * averages the frame times of the last second, like in-game FPS counters,
      * reports 0 ("Idle") when the application has stopped presenting frames.
    """

    _SAMPLE_FIELDS = 6        # doubles per EventScores sample (fps is the first one)
    _BUFFER_SAMPLES = 8192    # DLL ring-buffer capacity (oldest samples are overwritten)
    _READ_SAMPLES = 2048      # newest samples read per poll (covers 1 s up to ~2000 presents/s)
    _WINDOW_SEC = 1.0         # averaging window
    _STALE_SEC = 1.5          # no new presents for this long -> 0 ("Idle")
    _POLL_SEC = 0.5
    _SWITCH_POLLS = 2         # foreground app must be stable this many polls before switching
    _MIN_FPS = 1.0            # sanity bounds for a single sample (also rejects inf / NaN)
    _MAX_FPS = 5000.0
    _RENDER_ENTER_SEC = 2.0   # continuous frames needed before is_rendering() is True
    _RENDER_EXIT_SEC = 5.0    # ... and how long it stays True after frames stop

    _instance = None
    _lock = Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(FPSMonitor, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False): return
        self._fps_value = 0
        self._running = False
        self._lib = None
        self._thread = None
        # Generation token: a worker exits when its generation is stale, so a
        # fast stop()->start() cycle can't resurrect the old worker thread.
        self._worker_gen = 0
        # Serializes all DLL calls (worker vs start/stop from other threads)
        self._dll_lock = Lock()
        # Serializes start()/stop() calls from different threads
        self._state_lock = Lock()
        # PID the ETW session is currently recording (None = no session running)
        self._session_pid = None
        # Callable returning the PID of the application to measure (or None).
        # Set by HardwareMonitor (foreground application, excluding non-game apps).
        self.target_pid_provider = None
        # Why FPS is unavailable: None (ok / not started yet), "admin" (PresentMon
        # needs an elevated process), "missing" (DLL not found) or "error".
        self.unavailable_reason = None

        # Reusable buffers for GetCurrentData
        self._data_buf = (ctypes.c_double * (self._READ_SAMPLES * self._SAMPLE_FIELDS))()
        self._time_buf = (ctypes.c_double * self._READ_SAMPLES)()
        self._size_buf = (ctypes.c_int * 1)()
        self._last_newest_ts = None
        self._last_new_sample_at = 0.0
        # Sustained-rendering tracking for is_rendering() (monotonic seconds)
        self._presenting_since = None
        self._last_presenting_at = 0.0
        self._initialized = True

        # Determine DLL path
        if getattr(sys, 'frozen', False):
            base_dir = sys._MEIPASS
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        self._dll_path = os.path.join(base_dir, "src", "lib", "PresentMon.dll")

        # Lazy: an ETW capture is not free. The owner calls start()/stop() based on
        # the show_game_fps preference; a session only runs while an app is targeted.

    # ------------------------------------------------------------------ lifecycle

    def _load_lib(self):
        if self._lib is not None:
            return
        lib = ctypes.CDLL(self._dll_path)
        lib.StartEventRecording.restype = ctypes.c_int
        lib.StartEventRecording.argtypes = [ctypes.c_int, ctypes.c_int]
        lib.StopEventRecording.restype = ctypes.c_int
        lib.StopEventRecording.argtypes = []
        lib.GetCurrentData.restype = ctypes.c_int
        lib.GetCurrentData.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_int),
        ]
        self._lib = lib

    def _mark_no_admin(self, code=PresentMonExitCodes.PRIVILIGIES_ERROR):
        if self.unavailable_reason != "admin":
            logger.warning(
                "FPSMonitor: PresentMon requires administrator privileges (error %s). "
                "Live FPS is disabled until the hub runs elevated "
                "(e.g. via the 'Run at Startup' scheduled task).", code
            )
        self.unavailable_reason = "admin"

    def start(self):
        with self._state_lock:
            if self._running:
                return
            # A process cannot gain elevation at runtime, so retrying (and logging the
            # same error on every preference save) is pointless until the app restarts.
            if self.unavailable_reason == "admin":
                return

            if not os.path.exists(self._dll_path):
                if self.unavailable_reason != "missing":
                    logger.error(f"FPSMonitor: DLL not found at {self._dll_path}")
                self.unavailable_reason = "missing"
                return

            if not _is_running_as_admin():
                self._mark_no_admin()
                return

            try:
                self._load_lib()
            except Exception as e:
                self.unavailable_reason = "error"
                logger.error(f"FPSMonitor: Failed to load/init DLL: {e}")
                return

            self.unavailable_reason = None
            self._running = True
            self._worker_gen += 1
            self._thread = Thread(target=self._worker_loop, args=(self._worker_gen,),
                                  daemon=True, name="FPS-DLL-Worker")
            self._thread.start()
            logger.info("FPSMonitor: Native PresentMon logic started (foreground application)")

    def stop(self):
        with self._state_lock:
            self._running = False
            self._worker_gen += 1  # invalidate any live worker immediately
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=3.0)
        self._stop_session()
        with self._lock:
            self._fps_value = 0

    def get_fps(self) -> int:
        with self._lock:
            return self._fps_value

    def is_running(self) -> bool:
        return bool(self._running)

    def is_rendering(self) -> bool:
        """True while the measured app has presented frames continuously for
        _RENDER_ENTER_SEC (games do; documents and idle apps do not), with a
        _RENDER_EXIT_SEC grace period so short stalls do not flicker the view."""
        with self._lock:
            since = self._presenting_since
            last = self._last_presenting_at
        if since is None:
            return False
        now = time.monotonic()
        return (now - since) >= self._RENDER_ENTER_SEC and (now - last) <= self._RENDER_EXIT_SEC

    def _update_rendering(self, fps):
        now = time.monotonic()
        with self._lock:
            if fps > 0:
                if self._presenting_since is None:
                    self._presenting_since = now
                self._last_presenting_at = now
            elif self._presenting_since is not None and now - self._last_presenting_at > self._RENDER_EXIT_SEC:
                self._presenting_since = None

    # ------------------------------------------------------------------ worker

    def _get_target_pid(self):
        provider = self.target_pid_provider
        if not callable(provider):
            return None
        try:
            pid = provider()
            return int(pid) if pid else None
        except Exception:
            return None

    def _worker_loop(self, gen):
        pending_pid, pending_polls = None, 0
        while self._running and gen == self._worker_gen:
            try:
                target = self._get_target_pid()
                if target != self._session_pid:
                    # Debounce: follow the foreground app only once it is stable, so a
                    # quick Alt+Tab does not restart the ETW session back and forth.
                    pending_polls = pending_polls + 1 if target == pending_pid else 1
                    pending_pid = target
                    if pending_polls >= self._SWITCH_POLLS:
                        self._switch_session(target, gen)
                        pending_pid, pending_polls = None, 0
                else:
                    pending_pid, pending_polls = None, 0

                fps = self._read_fps() if self._session_pid is not None else 0
                with self._lock:
                    self._fps_value = fps
                self._update_rendering(fps)
            except Exception as e:
                logger.debug("FPS worker error: %s", e)
            time.sleep(self._POLL_SEC)

    def _switch_session(self, target_pid, gen):
        self._stop_session()
        if target_pid is None:
            return
        with self._dll_lock:
            if not self._running or gen != self._worker_gen:
                return
            res = self._lib.StartEventRecording(int(target_pid), self._BUFFER_SAMPLES)
            if res == PresentMonExitCodes.STATUS_OK:
                self._session_pid = target_pid
                self._last_newest_ts = None
                self._last_new_sample_at = time.monotonic()
                with self._lock:
                    self._presenting_since = None  # a new app must prove it renders
        if res == PresentMonExitCodes.STATUS_OK:
            logger.debug("FPSMonitor: measuring PID %s", target_pid)
        elif res == PresentMonExitCodes.PRIVILIGIES_ERROR:
            self._mark_no_admin(res)
            self._running = False
        else:
            # Retried automatically on the next foreground check
            logger.debug("FPSMonitor: StartEventRecording(pid=%s) failed (error %s)", target_pid, res)

    def _stop_session(self):
        with self._lock:
            self._presenting_since = None
        with self._dll_lock:
            if self._session_pid is None or self._lib is None:
                return
            try:
                self._lib.StopEventRecording()
            except Exception as e:
                logger.debug("FPSMonitor: StopEventRecording failed: %s", e)
            self._session_pid = None

    def _read_fps(self) -> int:
        """Average FPS of the target over the last second (0 when it stopped presenting)."""
        self._size_buf[0] = 0
        with self._dll_lock:
            if self._session_pid is None:
                return 0
            res = self._lib.GetCurrentData(self._READ_SAMPLES, self._data_buf, self._time_buf, self._size_buf)
        count = min(int(self._size_buf[0]), self._READ_SAMPLES)
        if res != PresentMonExitCodes.STATUS_OK or count <= 0:
            return 0

        # Samples are oldest-first; detect a stalled app by the newest timestamp.
        now = time.monotonic()
        newest_ts = self._time_buf[count - 1]
        if newest_ts != self._last_newest_ts:
            self._last_newest_ts = newest_ts
            self._last_new_sample_at = now
        elif now - self._last_new_sample_at > self._STALE_SEC:
            return 0

        data, times, step = self._data_buf, self._time_buf, self._SAMPLE_FIELDS
        total_ms, frames = 0.0, 0
        for i in range(count - 1, -1, -1):
            if newest_ts - times[i] > self._WINDOW_SEC:
                break
            fps = data[i * step]
            if self._MIN_FPS <= fps <= self._MAX_FPS:
                total_ms += 1000.0 / fps
                frames += 1
        if frames == 0 or total_ms <= 0:
            return 0
        return int(round(1000.0 * frames / total_ms))
