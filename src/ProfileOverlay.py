import logging
import os
from time import time
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from src.image_utils import fetch_content_path
from src.utils import normalize_text

logger = logging.getLogger("OLED Customizer.ProfileOverlay")


class ProfileOverlay:
    """
    Renders an elegant 128x40 OLED overlay whenever a SteelSeries device profile
    changes (e.g. Apex 7 onboard switch SteelSeries+F9, Rival 5 profile change,
    or SteelSeries GG / game profile automatic switch).
    """

    FRIENDLY_DEVICE_NAMES = {
        "profile": "Profile",
        "gg_profile": "Profile",
        "apex_7": "Apex 7",
        "apex_7_tkl": "Apex 7 TKL",
        "apex_pro": "Apex Pro",
        "apex_pro_tkl": "Apex Pro TKL",
        "apex_9": "Apex 9",
        "rival_5": "Rival 5",
        "aerox_3": "Aerox 3",
        "aerox_5": "Aerox 5",
        "aerox_9": "Aerox 9",
        "prime": "Prime",
        "arctis_nova_7_gen2_tx": "Nova 7",
        "arctis_nova_pro": "Nova Pro",
        "arctis_nova_5": "Nova 5",
    }

    def __init__(self, config, timeout: float = 3.5):
        self.config = config
        self.timeout = float(timeout) if timeout else 3.5
        self._last_change = 0.0
        self._current_device = ""
        self._current_profile = ""
        self._current_slot: Optional[int] = None

        font_dir = fetch_content_path("fonts")
        try:
            self._font_badge = ImageFont.truetype(os.path.join(font_dir, "MunroSmall.ttf"), 10)
            self._font_p_large = ImageFont.truetype(os.path.join(font_dir, "VerdanaBold.ttf"), 14)
            self._font_p_mid = ImageFont.truetype(os.path.join(font_dir, "VerdanaBold.ttf"), 12)
            self._font_p_small = ImageFont.truetype(os.path.join(font_dir, "VerdanaBold.ttf"), 10)
        except Exception as e:
            logger.warning("Failed to load fonts for ProfileOverlay: %s", e)
            self._font_badge = ImageFont.load_default()
            self._font_p_large = ImageFont.load_default()
            self._font_p_mid = ImageFont.load_default()
            self._font_p_small = ImageFont.load_default()

    def set_timeout(self, timeout: float):
        try:
            self.timeout = float(timeout)
        except Exception:
            pass

    def trigger(self, raw_device_name: str, raw_profile_name: str, slot: Optional[int] = None):
        """Trigger displaying the profile switch overlay on OLED."""
        dev_clean = self.FRIENDLY_DEVICE_NAMES.get(
            raw_device_name.lower(),
            raw_device_name.replace("_", " ").title()
        )
        prof_clean = raw_profile_name or ""
        for sym in ("™", "®", "©", "\ufffd"):
            prof_clean = prof_clean.replace(sym, "")
        prof_clean = normalize_text(prof_clean).strip()
        prof_clean = " ".join(prof_clean.split())

        # Pretty formatting for common single-token / lowercase game names
        lower_token = prof_clean.lower().replace(" ", "").replace("_", "").replace("-", "")
        game_map = {
            "blackdesert": "Black Desert",
            "battlefield6": "Battlefield 6",
            "battlefield2042": "Battlefield 2042",
            "battlefieldv": "Battlefield V",
            "battlefield1": "Battlefield 1",
            "leagueoflegends": "League of Legends",
            "counterstrike2": "CS2",
            "cs2": "CS2",
            "csgo": "CS:GO",
            "valorant": "Valorant",
            "dota2": "Dota 2",
            "apexlegends": "Apex Legends",
            "gtav": "GTA V",
            "gta5": "GTA V",
            "cyberpunk2077": "Cyberpunk 2077",
            "witcher3": "The Witcher 3",
            "fortnite": "Fortnite",
            "overwatch": "Overwatch",
            "overwatch2": "Overwatch 2",
            "worldofwarcraft": "World of Warcraft",
            "wow": "World of Warcraft",
            "diablo4": "Diablo IV",
            "diabloiv": "Diablo IV",
            "desktop": "Desktop",
            "default": "Default",
        }
        if lower_token in game_map:
            prof_clean = game_map[lower_token]
        elif prof_clean.islower():
            prof_clean = prof_clean.title()

        if not prof_clean:
            prof_clean = "Profile"

        self._current_device = dev_clean
        self._current_profile = prof_clean
        self._current_slot = slot
        self._last_change = time()

        slot_str = f" [Slot {slot + 1}]" if slot is not None and slot >= 0 else ""
        logger.info(
            "Profile change detected: %s -> '%s'%s (displaying OLED overlay for %.1fs)",
            self._current_device,
            self._current_profile,
            slot_str,
            self.timeout,
        )

    def should_display(self) -> bool:
        if self._last_change <= 0.0:
            return False
        return (time() - self._last_change) < self.timeout

    def get_image(self) -> Image.Image:
        """
        Draw a crisp 128x40 monochrome bitmap:
        - Outer border [0, 0, 127, 39]
        - Inverted top pill with Device Name and Slot indicator (e.g. APEX 7 [1/5])
        - Subtle horizontal divider line at y=14
        - Large bold centered profile name
        """
        w, h = self.config.width, self.config.height
        image = Image.new("1", (w, h), color=self.config.secondary)
        draw = ImageDraw.Draw(image)

        # Outer border
        draw.rectangle([0, 0, w - 1, h - 1], outline=self.config.primary)

        # Header Badge
        badge_text = f" {self._current_device.upper()} "
        if self._current_slot is not None and self._current_slot >= 0:
            badge_text += f"[{self._current_slot + 1}/5] "

        try:
            bbox = self._font_badge.getbbox(badge_text)
            bw = bbox[2] - bbox[0]
            bh = bbox[3] - bbox[1]
        except Exception:
            bw, bh = len(badge_text) * 6, 8

        bx = max(2, (w - bw) // 2)

        # Inverted pill for device name
        draw.rectangle([bx - 2, 2, min(w - 3, bx + bw + 2), 12], fill=self.config.primary)
        draw.text((bx, 2), badge_text, font=self._font_badge, fill=self.config.secondary)

        # Horizontal accent line
        draw.line([(3, 14), (w - 4, 14)], fill=self.config.primary)

        # Profile Name centered in bottom area
        prof = self._current_profile
        # Select best font size to avoid truncation
        if len(prof) <= 10:
            fnt = self._font_p_large
            y_offset = 18
        elif len(prof) <= 14:
            fnt = self._font_p_mid
            y_offset = 20
        else:
            fnt = self._font_p_small
            y_offset = 22

        try:
            p_bbox = fnt.getbbox(prof)
            pw = p_bbox[2] - p_bbox[0]
        except Exception:
            pw = len(prof) * 7

        if pw > (w - 8):
            fnt = self._font_badge
            y_offset = 24
            try:
                p_bbox = fnt.getbbox(prof)
                pw = p_bbox[2] - p_bbox[0]
            except Exception:
                pw = len(prof) * 5

        px = max(4, (w - pw) // 2)
        draw.text((px, y_offset), prof, font=fnt, fill=self.config.primary)

        return image

