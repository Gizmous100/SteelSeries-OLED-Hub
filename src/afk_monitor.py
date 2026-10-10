import ctypes
from ctypes import wintypes
import time
from threading import Thread
import logging

logger = logging.getLogger("OLED Customizer.AFK")

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT),
                ("dwTime", wintypes.DWORD)]

class XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", wintypes.DWORD),
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_byte),
        ("bRightTrigger", ctypes.c_byte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]

class AFKMonitor:
    def __init__(self, timeout_minutes=5):
        self.timeout_ms = timeout_minutes * 60 * 1000
        self.is_afk = False
        self._running = False
        self._last_packet_numbers = [0, 0, 0, 0]
        
        # Load XInput
        try:
            self.xinput = ctypes.windll.xinput1_4
        except OSError:
            try:
                self.xinput = ctypes.windll.xinput1_3
            except OSError:
                self.xinput = None

    def get_idle_time_ms(self):
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return 0

    def check_gamepad_activity(self):
        if not self.xinput:
            return False
            
        activity = False
        for i in range(4):
            state = XINPUT_STATE()
            res = self.xinput.XInputGetState(i, ctypes.byref(state))
            if res == 0:  # Success
                if state.dwPacketNumber != self._last_packet_numbers[i]:
                    self._last_packet_numbers[i] = state.dwPacketNumber
                    activity = True
        return activity

    def send_dummy_keypress(self):
        # Sends F15 keypress to reset Windows idle timer and keep GG awake
        VK_F15 = 0x7E
        ctypes.windll.user32.keybd_event(VK_F15, 0, 0, 0) # KEYDOWN
        ctypes.windll.user32.keybd_event(VK_F15, 0, 2, 0) # KEYUP

    def _loop(self):
        while self._running:
            # Check gamepad
            if self.check_gamepad_activity():
                # Gamepad is active, simulate keyboard input to keep PC/SteelSeries awake
                self.send_dummy_keypress()
            
            # Check idle time
            idle_ms = self.get_idle_time_ms()
            if idle_ms > self.timeout_ms:
                if not self.is_afk:
                    logger.info("AFK Monitor: True AFK detected, dimming OLED.")
                self.is_afk = True
            else:
                if self.is_afk:
                    logger.info("AFK Monitor: Activity detected, waking up.")
                self.is_afk = False
                
            time.sleep(1) # Poll every second

    def start(self):
        if not self._running:
            self._running = True
            Thread(target=self._loop, daemon=True, name="AFK-Monitor").start()

    def stop(self):
        self._running = False

