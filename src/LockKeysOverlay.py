import logging
import os
from time import time

from PIL import Image, ImageDraw, ImageFont

from src.image_utils import fetch_content_path

logger = logging.getLogger("OLED Customizer.LockKeysOverlay")


class LockKeysOverlay:
    """
    Renders an elegant 128x40 OLED overlay whenever a keyboard lock key
    (Caps Lock, Num Lock, or Scroll Lock) is toggled.
    """

    def __init__(self, config, timeout: float = 1.8):
        self.config = config
        self.timeout = float(timeout) if timeout else 1.8
        self._last_change = 0.0
        self._toggled_key = "CAPS LOCK"
        self._is_active = False
        self._caps = False
        self._num = False
        self._scroll = False

        font_dir = fetch_content_path("fonts")
        try:
            self._font_badge = ImageFont.truetype(os.path.join(font_dir, "MunroSmall.ttf"), 10)
            self._font_tiny = ImageFont.truetype(os.path.join(font_dir, "MunroSmall.ttf"), 9)
            self._font_status = ImageFont.truetype(os.path.join(font_dir, "VerdanaBold.ttf"), 12)
        except Exception as e:
            logger.warning("Failed to load fonts for LockKeysOverlay: %s", e)
            self._font_badge = ImageFont.load_default()
            self._font_tiny = ImageFont.load_default()
            self._font_status = ImageFont.load_default()

    def set_timeout(self, timeout: float):
        try:
            self.timeout = float(timeout)
        except Exception:
            pass

    def trigger(
        self,
        toggled_key: str,
        is_active: bool,
        caps: bool = False,
        num: bool = False,
        scroll: bool = False,
    ):
        """Trigger displaying the lock key state overlay on OLED."""
        self._toggled_key = toggled_key.upper().strip()
        self._is_active = bool(is_active)
        self._caps = bool(caps)
        self._num = bool(num)
        self._scroll = bool(scroll)
        self._last_change = time()

        logger.info(
            "Lock key toggled: %s -> %s (caps=%s, num=%s, scroll=%s) for %.1fs",
            self._toggled_key,
            "ON" if self._is_active else "OFF",
            self._caps,
            self._num,
            self._scroll,
            self.timeout,
        )

    def should_display(self) -> bool:
        if self._last_change <= 0.0:
            return False
        return (time() - self._last_change) < self.timeout

    def get_image(self) -> Image.Image:
        """Draw high-contrast 128x40 HUD notification for keyboard lock toggles."""
        w, h = self.config.width, self.config.height
        on, off = self.config.primary, self.config.secondary

        image = Image.new("1", (w, h), color=off)
        draw = ImageDraw.Draw(image)

        # 1. Header inverted pill
        header_text = self._toggled_key
        try:
            bbox = self._font_badge.getbbox(header_text)
            bw = (bbox[2] - bbox[0]) + 10
        except Exception:
            bw = len(header_text) * 6 + 10

        bx0 = (w - bw) // 2
        bx1 = bx0 + bw
        draw.rectangle((bx0, 2, bx1, 12), fill=on)
        draw.text((w // 2, 7), header_text, font=self._font_badge, fill=off, anchor="mm")

        # 2. Divider line
        draw.line((12, 14, w - 12, 14), fill=on)

        # 3. Main status (ON / OFF)
        status_text = ">>>  ON  <<<" if self._is_active else "OFF"
        draw.text((w // 2, 23), status_text, font=self._font_status, fill=on, anchor="mm")

        # 4. Footer: status of remaining locks
        footer_parts = []
        if self._toggled_key != "CAPS LOCK":
            footer_parts.append(f"CAPS: {'ON' if self._caps else 'OFF'}")
        if self._toggled_key != "NUM LOCK":
            footer_parts.append(f"NUM: {'ON' if self._num else 'OFF'}")
        if self._toggled_key != "SCROLL LOCK":
            footer_parts.append(f"SCRL: {'ON' if self._scroll else 'OFF'}")

        footer_text = "   •   ".join(footer_parts)
        draw.text((w // 2, 34), footer_text, font=self._font_tiny, fill=on, anchor="mm")

        return image

