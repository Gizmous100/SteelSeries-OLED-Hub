from PIL import ImageFont, ImageDraw, Image
from time import localtime, strftime
import math

from src.image_utils import fetch_content_path
from src.utils import normalize_text


class Timer:
    class Style:
        STANDARD = "Standard"
        BIG = "Big Timer"
        DATE_FOCUSED = "Date Focused"
        ANALOG = "Analog"

    DAY_TRANSLATIONS = {
        "EN": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "PL": ["Pon", "Wt", "Sr", "Czw", "Pt", "Sob", "Nd"],
        "DE": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
        "ES": ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"],
        "FR": ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
        "IT": ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"],
        "PT": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"],
        "TR": ["Pzt", "Sal", "Car", "Per", "Cum", "Cmt", "Paz"],
        "NL": ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"],
    }

    def __init__(self, config, date_format, display_seconds, use_polish_days=False, style="Standard", language=None):
        self.config = config
        self.date_format = int(date_format)
        self.display_seconds = display_seconds
        self.use_polish_days = use_polish_days
        self.language = (language or ("PL" if use_polish_days else "EN")).upper()
        if self.language not in self.DAY_TRANSLATIONS:
            self.language = "EN"
        self.style = style
        
        # Instance-level fonts with Safety Fallbacks
        def safe_load_font(path, size):
            try:
                return ImageFont.truetype(font=path, size=size)
            except Exception:
                try:
                    return ImageFont.load_default()
                except Exception:
                    return None

        self._digi_path = fetch_content_path('fonts/DS-DIGIB.ttf')
        self._digi_fonts = {}
        for sz in range(10, 31):
            self._digi_fonts[sz] = safe_load_font(self._digi_path, sz)

        self.FONT_DIGI_BIG = self._digi_fonts[20]
        self.FONT_DIGI_MED = self._digi_fonts[16]
        self.FONT_DIGI_SMALL = self._digi_fonts[12]
        self.FONT_HUGE = self._digi_fonts[30]
        # Deliberately NOT the DS-DIGI face: it is a 7-segment LCD font whose
        # digits break into disconnected bars at small sizes ('5' especially).
        # VerdanaBold at 9px stays solid and renders "100" at 18px — almost
        # exactly the battery icon's width, so it centres cleanly underneath.
        self.FONT_BATT = safe_load_font(fetch_content_path('fonts/VerdanaBold.ttf'), 9)
        self.FONT_NARROW = safe_load_font(fetch_content_path('fonts/MunroSmall.ttf'), 10)
        self.FONT_LOCK_BADGE = safe_load_font(fetch_content_path('fonts/MunroSmall.ttf'), 8)

        # The clock changes at most once per second but get_image is called at
        # display FPS (10x/sec) — cache the rendered frame per displayed state.
        self._cache_key = None
        self._cached_image = None

        # Headset battery, weather, mute/deafen, and lock keys widgets injected by DisplayManager
        self.show_battery = False
        self.show_battery_percent = False   # icon only unless enabled
        self.show_clock_mute_icons = False
        self.show_lock_keys_clock = False
        self.battery_getter = None
        self.weather_getter = None
        self.mute_status_getter = None
        self.lock_keys_getter = None

        self.icon_mic_on = self._load_icon("assets/icons/mic_on.png")
        self.icon_mic_off = self._load_icon("assets/icons/mic_off.png")
        self.icon_speaker_on = self._load_icon("assets/icons/speaker_high.png")
        self.icon_speaker_mute = self._load_icon("assets/icons/speaker_mute.png")

    def _load_icon(self, rel_path):
        try:
            full_path = fetch_content_path(rel_path)
            with Image.open(full_path) as raw:
                return raw.resize((12, 12), Image.Resampling.NEAREST).convert("1")
        except Exception:
            return None

    def set_style(self, style):
        self.style = style

    def _get_digi_font(self, size):
        size = max(10, min(30, int(size)))
        return self._digi_fonts.get(size, self.FONT_DIGI_SMALL)

    @staticmethod
    def _measure_fixed_width(font, text):
        w0 = font.getlength("0")
        total_w = 0.0
        for c in text:
            total_w += w0 if c.isdigit() else font.getlength(c)
        return total_w

    def _fit_fixed_width_font(self, text, max_size, min_size, max_width):
        for sz in range(max_size, min_size - 1, -1):
            font = self._get_digi_font(sz)
            if self._measure_fixed_width(font, text) <= max_width:
                return font
        return self._get_digi_font(min_size)

    def _fit_text_font(self, text, max_size, min_size, max_width):
        for sz in range(max_size, min_size - 1, -1):
            font = self._get_digi_font(sz)
            if font.getlength(text) <= max_width:
                return font
        return self._get_digi_font(min_size)

    def _battery(self):
        if not self.show_battery or not callable(self.battery_getter):
            return None
        try:
            val = self.battery_getter()
        except Exception:
            return None
        return val if isinstance(val, int) and 0 <= val <= 100 else None

    def _weather(self):
        if not callable(self.weather_getter):
            return None
        try:
            data = self.weather_getter()
            if data and data.get("temp") is not None:
                temp = int(round(data["temp"]))
                code = data.get("code")
                aqi = int(round(data["aqi"])) if data.get("aqi") is not None else None
                return (temp, code, aqi)
        except Exception:
            pass
        return None

    def _mute_status(self):
        if not self.show_clock_mute_icons or not callable(self.mute_status_getter):
            return None
        try:
            res = self.mute_status_getter()
            if res is not None:
                is_mic_muted = bool(res[0])
                is_deafened = bool(res[1])
                eff_mic_muted = bool(is_mic_muted or is_deafened)
                if eff_mic_muted or is_deafened:
                    return (eff_mic_muted, is_deafened)
        except Exception:
            pass
        return None

    def _lock_keys(self):
        if not self.show_lock_keys_clock or not callable(self.lock_keys_getter):
            return None
        try:
            res = self.lock_keys_getter()
            if res and any(res):
                return (bool(res[0]), bool(res[1]), bool(res[2]))
        except Exception:
            pass
        return None

    def _draw_lock_badges(self, draw, lock_tuple, style, cx, cy, avail_w, x_left, x_right, day_cx=None):
        """
        Draw compact inverted badges ([C], [N], [S]) for active lock keys
        positioned cleanly with 0 pixel collision across all clock styles.
        """
        if not lock_tuple or not any(lock_tuple):
            return

        badges = [("C", lock_tuple[0]), ("N", lock_tuple[1]), ("S", lock_tuple[2])]
        active = [char for char, is_active in badges if is_active]
        if not active:
            return

        box_w, box_h, gap = 9, 6, 2
        k = len(active)
        total_w = k * box_w + (k - 1) * gap

        if style == self.Style.ANALOG:
            badge_cx = day_cx if day_cx is not None else cx
            by0 = 29
        elif style == self.Style.DATE_FOCUSED:
            badge_cx = cx
            by0 = 1
        else:
            # STANDARD and BIG
            badge_cx = cx
            by0 = 34

        by1 = by0 + box_h - 1
        on, off = self.config.primary, self.config.secondary
        start_x = int(round(badge_cx - total_w / 2.0))

        for i, char in enumerate(active):
            bx0 = start_x + i * (box_w + gap)
            bx1 = bx0 + box_w - 1
            draw.rectangle((bx0, by0, bx1, by1), fill=on)
            draw.text((bx0 + box_w // 2, by0 + 2), char, font=self.FONT_LOCK_BADGE, fill=off, anchor="mm")

    def _draw_mute_icons_top(self, image, mute_tuple):
        """Draw only mic_off / speaker_mute icons (without text) in the top weather-icon slot (y = 2..13)."""
        eff_mic_muted, is_deafened = mute_tuple
        if eff_mic_muted and is_deafened:
            if self.icon_mic_off:
                image.paste(self.icon_mic_off, (3, 2))
            if self.icon_speaker_mute:
                image.paste(self.icon_speaker_mute, (18, 2))
        elif eff_mic_muted:
            if self.icon_mic_off:
                image.paste(self.icon_mic_off, (10, 2))
        elif is_deafened:
            if self.icon_speaker_mute:
                image.paste(self.icon_speaker_mute, (10, 2))

    def _draw_battery(self, draw, pct):
        """Vertical headset battery, top-right, level printed under it."""
        w = self.config.width
        on, off = self.config.primary, self.config.secondary

        x1 = w - 4
        x0 = x1 - 10
        top = 3
        bot = 21

        # Clear only the battery column (x = 110..127)
        draw.rectangle((x0 - 4, 0, w - 1, 39 if self.show_battery_percent else 24), fill=off)

        # Body and Nub
        draw.rectangle((x0, top, x1, bot), outline=on)
        draw.rectangle((x0 + 3, top - 2, x1 - 3, top), fill=on)

        # 4 discrete segments
        SEGMENTS, SEG_H, GAP = 4, 3, 1
        lit = max(0, min(SEGMENTS, -(-max(0, min(100, pct)) // (100 // SEGMENTS))))
        for i in range(lit):
            seg_bot = (bot - 2) - i * (SEG_H + GAP)
            seg_top = seg_bot - SEG_H + 1
            draw.rectangle((x0 + 2, seg_top, x1 - 2, seg_bot), fill=on)

        # Level centred under the icon
        if self.show_battery_percent:
            draw.text(((x0 + x1) // 2, bot + 2), str(pct), font=self.FONT_BATT,
                      fill=on, anchor="mt")

    def _draw_weather(self, image, draw, weather_tuple, mute_tuple=None):
        """Compact weather icon (or mute/deafen icon in top slot), AQI, and temperature on the left column (x = 0..30)."""
        temp, code, aqi = weather_tuple
        on = self.config.primary
        ix, iy = 16, 9

        if mute_tuple is not None:
            self._draw_mute_icons_top(image, mute_tuple)
        else:
            if code == 0:
                draw.ellipse((ix - 3, iy - 3, ix + 3, iy + 3), outline=on)
            elif code in [1, 2, 3]:
                draw.ellipse((ix - 5, iy - 1, ix - 1, iy + 3), outline=on)
                draw.ellipse((ix - 2, iy - 4, ix + 4, iy + 2), outline=on)
            elif code in [61, 63, 65, 80, 81, 82]:
                draw.ellipse((ix - 5, iy - 5, ix + 5, iy - 1), outline=on)
                draw.line((ix - 2, iy + 1, ix - 3, iy + 4), fill=on)
                draw.line((ix + 1, iy + 1, ix, iy + 4), fill=on)
            elif code in [71, 73, 75, 85, 86]:
                draw.text((ix - 3, iy - 6), "*", font=self.FONT_BATT, fill=on)
            elif code in [95, 96, 99]:
                draw.polygon([(ix, iy - 5), (ix - 2, iy), (ix + 1, iy - 1), (ix - 1, iy + 4)], fill=on)
            else:
                draw.ellipse((ix - 2, iy - 2, ix + 2, iy + 2), outline=on)

        if aqi is not None:
            aqi_str = f"AQI:{aqi}" if aqi >= 100 else f"AQI: {aqi}"
            draw.text((ix, iy + 10), aqi_str, font=self.FONT_NARROW, fill=on, anchor="mm")

        draw.text((ix, iy + 20), f"{temp}°", font=self.FONT_BATT, fill=on, anchor="mm")

    def _draw_fixed_width_text(self, draw, cx, cy, text, font, fill, anchor="mm"):
        # Calculate fixed character widths for digits to prevent horizontal jitter
        w0 = font.getlength("0")
        total_w = self._measure_fixed_width(font, text)

        if anchor.startswith("m"):
            x = cx - total_w / 2
        else:
            x = cx

        bbox = font.getbbox("0")
        h = bbox[3] - bbox[1]

        if anchor.endswith("m"):
            y = cy - h / 2 - bbox[1]
        else:
            y = cy

        # Draw character by character
        for c in text:
            cw = font.getlength(c)
            cell_w = w0 if c.isdigit() else cw
            x_offset = (cell_w - cw) if c.isdigit() else 0
            draw.text((x + x_offset, y), c, font=font, fill=fill, anchor="lt")
            x += cell_w

    def get_image(self):
        battery = self._battery()
        mute_tuple = self._mute_status()
        weather_tuple = self._weather()
        lock_tuple = self._lock_keys()
        now = localtime()
        t_text, d_text = self.get_current_time()

        if self.style == self.Style.ANALOG:
            key = (self.style, now.tm_hour, now.tm_min,
                   now.tm_sec if self.display_seconds else -1,
                   self.language, battery, self.show_battery_percent, weather_tuple, mute_tuple, lock_tuple)
        else:
            key = (self.style, t_text, d_text, battery, self.show_battery_percent, weather_tuple, mute_tuple, lock_tuple)

        if key == self._cache_key and self._cached_image is not None:
            return self._cached_image

        image = Image.new(
            mode="1",
            size=(self.config.width, self.config.height),
            color=self.config.secondary
        )
        draw = ImageDraw.Draw(image)

        # Compute horizontal safe bounds based on which side widgets are active:
        # - Weather or Mute/Deafen icons occupy x = 0..31 on the left when active
        # - Battery occupies x = 110..127 on the right when active
        left_active = (mute_tuple is not None) or (weather_tuple is not None)
        x_left = 32 if left_active else 2
        x_right = 109 if battery is not None else (self.config.width - 2)
        avail_w = max(40, x_right - x_left)
        cx = (x_left + x_right) / 2.0
        cy = self.config.height / 2.0

        # === ANALOG STYLE ===
        if self.style == self.Style.ANALOG:
            current_time = localtime()
            radius = 17 if (left_active and battery is not None) else 18
            day_label = d_text.split(" ")[0]
            day_w = self.FONT_DIGI_SMALL.getlength(day_label)
            gap = 6
            group_w = (2 * radius) + gap + day_w
            group_x0 = cx - (group_w / 2.0)
            acx = group_x0 + radius
            day_cx = group_x0 + (2 * radius) + gap + (day_w / 2.0)

            # Draw Clock Face (Circle) and Center Dot
            draw.ellipse((acx - radius, cy - radius, acx + radius, cy + radius), outline=self.config.primary)
            draw.ellipse((acx - 1, cy - 1, acx + 1, cy + 1), fill=self.config.primary)

            hour_angle = math.radians((current_time.tm_hour % 12 + current_time.tm_min / 60) * 30 - 90)
            minute_angle = math.radians(current_time.tm_min * 6 - 90)
            second_angle = math.radians(current_time.tm_sec * 6 - 90)

            # Hour Hand (Shorter)
            hour_len = 10
            hx = acx + hour_len * math.cos(hour_angle)
            hy = cy + hour_len * math.sin(hour_angle)
            draw.line((acx, cy, hx, hy), fill=self.config.primary, width=2)

            # Minute Hand (Longer)
            min_len = radius - 3
            mx = acx + min_len * math.cos(minute_angle)
            my = cy + min_len * math.sin(minute_angle)
            draw.line((acx, cy, mx, my), fill=self.config.primary, width=1)

            # Second Hand (Thin, if enabled)
            if self.display_seconds:
                sec_len = radius - 2
                sx = acx + sec_len * math.cos(second_angle)
                sy = cy + sec_len * math.sin(second_angle)
                draw.line((acx, cy, sx, sy), fill=self.config.primary, width=1)

            # Day Name to the right of the analog dial
            draw.text((day_cx, cy), day_label, font=self.FONT_DIGI_SMALL, fill=self.config.primary, anchor="mm")
            if lock_tuple:
                self._draw_lock_badges(draw, lock_tuple, self.Style.ANALOG, cx, cy, avail_w, x_left, x_right, day_cx=day_cx)

        elif self.style == self.Style.BIG:
            # === BIG STYLE: HUGE TIME, NO DATE (auto-fitted to available width) ===
            big_font = self._fit_fixed_width_font(t_text, 30, 14, avail_w - 2)
            self._draw_fixed_width_text(
                draw, cx, cy,
                t_text,
                font=big_font,
                fill=self.config.primary,
                anchor="mm"
            )
            if lock_tuple:
                self._draw_lock_badges(draw, lock_tuple, self.Style.BIG, cx, cy, avail_w, x_left, x_right)

        elif self.style == self.Style.DATE_FOCUSED:
            # === DATE FOCUSED: BIG DATE, SMALL TIME (auto-fitted to available width) ===
            date_font = self._fit_text_font(d_text, 16, 11, avail_w - 2)
            time_font = self._fit_fixed_width_font(t_text, 12, 10, avail_w - 2)
            draw.text(
                (cx, cy - 8),
                d_text,
                font=date_font,
                fill=self.config.primary,
                anchor="mm"
            )
            self._draw_fixed_width_text(
                draw, cx, cy + 12,
                t_text,
                font=time_font,
                fill=self.config.primary,
                anchor="mm"
            )
            if lock_tuple:
                self._draw_lock_badges(draw, lock_tuple, self.Style.DATE_FOCUSED, cx, cy, avail_w, x_left, x_right)

        else:
            # === STANDARD STYLE (Default, auto-fitted to available width) ===
            time_font = self._fit_fixed_width_font(t_text, 20, 12, avail_w - 2)
            date_font = self._fit_text_font(d_text, 12, 10, avail_w - 2)
            self._draw_fixed_width_text(
                draw, cx, cy - 6,
                t_text,
                font=time_font,
                fill=self.config.primary,
                anchor="mm"
            )
            draw.text(
                (cx, cy + 9),
                d_text,
                font=date_font,
                fill=self.config.primary,
                anchor="mm"
            )
            if lock_tuple:
                self._draw_lock_badges(draw, lock_tuple, self.Style.STANDARD, cx, cy, avail_w, x_left, x_right)

        if weather_tuple is not None:
            self._draw_weather(image, draw, weather_tuple, mute_tuple)
        elif mute_tuple is not None:
            self._draw_mute_icons_top(image, mute_tuple)

        if battery is not None:
            self._draw_battery(draw, battery)

        self._cache_key = key
        self._cached_image = image
        return image

    def get_current_time(self):
        current_time = localtime()
        seconds = ":%S" if self.display_seconds else ""

        hour_24 = current_time.tm_hour
        am_pm = "AM" if hour_24 < 12 else "PM"

        if self.date_format == 12:
            time_text = strftime("%I:%M" + seconds, current_time) + f" {am_pm}"
        else:
            time_text = strftime("%H:%M" + seconds, current_time)

        wday = current_time.tm_wday  # 0 = Monday .. 6 = Sunday
        days_list = self.DAY_TRANSLATIONS.get(self.language, self.DAY_TRANSLATIONS["EN"])
        day_str = days_list[wday] if 0 <= wday < len(days_list) else strftime("%a", current_time)

        date_text = f"{day_str} {strftime('%d/%m/%y', current_time)}"

        return time_text, normalize_text(date_text)

    def set_display_seconds(self, display_seconds):
        self.display_seconds = display_seconds

    def set_date_format(self, date_format):
        self.date_format = date_format if date_format == 12 else 24

    def set_use_polish_days(self, use_polish_days):
        self.use_polish_days = use_polish_days
        if use_polish_days:
            self.language = "PL"
        elif self.language == "PL":
            self.language = "EN"

    def set_language(self, language):
        lang = (language or "EN").upper()
        self.language = lang if lang in self.DAY_TRANSLATIONS else "EN"
        self.use_polish_days = (self.language == "PL")
