import logging
from time import time
import json
import urllib.request
import urllib.parse
from threading import Thread
from PIL import Image, ImageDraw, ImageFont
from src.image_utils import fetch_content_path
from src.utils import normalize_text

logger = logging.getLogger("OLED Customizer.Weather")

class WeatherModule:
    SUPPORTED_AQI_REGIONS = ("Auto", "EU", "US", "PL", "UK", "CN", "IN", "CA", "AU")
    SUPPORTED_LANGUAGES = ("EN", "PL", "DE", "ES", "FR", "IT", "PT", "TR", "NL")

    WEATHER_TRANSLATIONS = {
        "EN": {
            "na": "N/A", "clear": "Clear Sky", "cloudy": "Cloudy", "fog": "Fog",
            "drizzle": "Drizzle", "rain": "Rain", "snow": "Snow", "storm": "Storm",
            "unknown": "Unknown", "loading": "Loading...",
        },
        "PL": {
            "na": "Brak", "clear": "Bezchmurnie", "cloudy": "Zachmurzenie", "fog": "Mgla",
            "drizzle": "Mzawka", "rain": "Deszcz", "snow": "Snieg", "storm": "Burza",
            "unknown": "Nieznana", "loading": "Pobieranie...",
        },
        "DE": {
            "na": "k.A.", "clear": "Klar", "cloudy": "Bewolkt", "fog": "Nebel",
            "drizzle": "Niesel", "rain": "Regen", "snow": "Schnee", "storm": "Gewitter",
            "unknown": "Unbekannt", "loading": "Laden...",
        },
        "ES": {
            "na": "N/D", "clear": "Despejado", "cloudy": "Nublado", "fog": "Niebla",
            "drizzle": "Llovizna", "rain": "Lluvia", "snow": "Nieve", "storm": "Tormenta",
            "unknown": "Desconocido", "loading": "Cargando...",
        },
        "FR": {
            "na": "N/D", "clear": "Degage", "cloudy": "Nuageux", "fog": "Brouillard",
            "drizzle": "Bruine", "rain": "Pluie", "snow": "Neige", "storm": "Orage",
            "unknown": "Inconnu", "loading": "Chargement...",
        },
        "IT": {
            "na": "N/D", "clear": "Sereno", "cloudy": "Nuvoloso", "fog": "Nebbia",
            "drizzle": "Pioviggine", "rain": "Pioggia", "snow": "Neve", "storm": "Temporale",
            "unknown": "Sconosciuto", "loading": "Caricamento...",
        },
        "PT": {
            "na": "N/D", "clear": "Limpo", "cloudy": "Nublado", "fog": "Nevoeiro",
            "drizzle": "Chuvisco", "rain": "Chuva", "snow": "Neve", "storm": "Tempestade",
            "unknown": "Desconhecido", "loading": "Carregando...",
        },
        "TR": {
            "na": "Yok", "clear": "Acik", "cloudy": "Bulutlu", "fog": "Sisli",
            "drizzle": "Cisenti", "rain": "Yagmur", "snow": "Kar", "storm": "Firtina",
            "unknown": "Bilinmiyor", "loading": "Yukleniyor...",
        },
        "NL": {
            "na": "n.v.t.", "clear": "Helder", "cloudy": "Bewolkt", "fog": "Mist",
            "drizzle": "Motregen", "rain": "Regen", "snow": "Sneeuw", "storm": "Onweer",
            "unknown": "Onbekend", "loading": "Laden...",
        },
    }

    def __init__(self, config, preferences):
        self.config = config
        self.preferences = preferences
        
        self.city = self.preferences.get_preference("weather_city") or "Warsaw"
        self.aqi_region = self.preferences.get_preference("weather_aqi_region") or "EU"
        if self.aqi_region not in self.SUPPORTED_AQI_REGIONS:
            self.aqi_region = "EU"
        lang_pref = self.preferences.get_preference("oled_language") or (
            "PL" if self.preferences.get_preference("use_polish_days") else "EN"
        )
        self.language = str(lang_pref).upper()
        if self.language not in self.SUPPORTED_LANGUAGES:
            self.language = "EN"
        self.enabled = bool(self.preferences.get_preference("weather_enabled"))
        
        self.lat = None
        self.lon = None
        self.country_code = ""
        self.timezone = ""
        self.temperature = None
        self.weathercode = None
        self.aqi = None
        self.effective_aqi_region = "EU" if self.aqi_region == "Auto" else self.aqi_region
        self._last_aqi_raw = None
        self.last_update = 0
        self.update_interval = 1800  # 30 mins
        
        self.font = ImageFont.truetype(fetch_content_path("fonts/VerdanaBold.ttf"), 14)
        self.small_font = ImageFont.truetype(fetch_content_path("fonts/MunroSmall.ttf"), 10)
        
        self._thread = None
        self.update()

    def update_preferences(self, preferences):
        self.preferences = preferences
        new_city = self.preferences.get_preference("weather_city") or "Warsaw"
        new_aqi_region = self.preferences.get_preference("weather_aqi_region") or "EU"
        if new_aqi_region not in self.SUPPORTED_AQI_REGIONS:
            new_aqi_region = "EU"
        new_lang = str(
            self.preferences.get_preference("oled_language")
            or ("PL" if self.preferences.get_preference("use_polish_days") else "EN")
        ).upper()
        if new_lang not in self.SUPPORTED_LANGUAGES:
            new_lang = "EN"
        self.language = new_lang
        was_enabled = self.enabled
        self.enabled = bool(self.preferences.get_preference("weather_enabled"))
        
        city_changed = (new_city != self.city)
        region_changed = (new_aqi_region != self.aqi_region)
        self.aqi_region = new_aqi_region

        if city_changed:
            self.city = new_city
            self.lat = None
            self.lon = None
            self.country_code = ""
            self.timezone = ""
            self.last_update = 0
            self.temperature = None
            self.aqi = None
            self._last_aqi_raw = None
            self.update()
        elif region_changed:
            if self._last_aqi_raw:
                self._compute_regional_aqi(self._last_aqi_raw)
            else:
                self.last_update = 0
                self.update()
        elif self.enabled and not was_enabled:
            self.last_update = 0
            self.update()

    def update(self):
        if not self.enabled:
            return
        if time() - self.last_update > self.update_interval:
            self.last_update = time()
            self._thread = Thread(target=self._fetch_data, daemon=True)
            self._thread.start()

    def _resolve_region(self):
        if self.aqi_region != "Auto":
            return self.aqi_region
        cc = (self.country_code or "").upper()
        tz = self.timezone or ""
        if cc == "GB":
            return "UK"
        if cc == "CN":
            return "CN"
        if cc == "IN":
            return "IN"
        if cc == "CA":
            return "CA"
        if cc == "AU":
            return "AU"
        if cc in ("US", "MX", "BR", "AR", "CL", "CO", "PE", "KR", "TW", "PH", "TH", "VN", "SG", "MY", "ID") or tz.startswith("America/"):
            return "US"
        return "EU"

    @staticmethod
    def _piecewise_linear(val, breakpoints):
        if val is None or val < 0:
            return None
        for c_low, c_high, i_low, i_high in breakpoints:
            if val <= c_high:
                if c_high == c_low:
                    return float(i_high)
                return i_low + (val - c_low) * (i_high - i_low) / (c_high - c_low)
        last = breakpoints[-1]
        c_low, c_high, i_low, i_high = last
        if c_high > c_low:
            return i_high + (val - c_high) * (i_high - i_low) / (c_high - c_low)
        return float(i_high)

    def _compute_regional_aqi(self, current_aqi_data):
        if not current_aqi_data:
            self.aqi = None
            return

        region = self._resolve_region()
        self.effective_aqi_region = region

        eu_val = current_aqi_data.get("european_aqi")
        us_val = current_aqi_data.get("us_aqi")
        pm25 = current_aqi_data.get("pm2_5")
        pm10 = current_aqi_data.get("pm10")
        no2 = current_aqi_data.get("nitrogen_dioxide")
        o3 = current_aqi_data.get("ozone")
        so2 = current_aqi_data.get("sulphur_dioxide")

        if region == "EU":
            self.aqi = eu_val if eu_val is not None else us_val
            return
        if region == "US":
            self.aqi = us_val if us_val is not None else eu_val
            return

        if region == "PL":
            # Polish GIOS Air Quality Index mapped to 0-100+ scale
            pl_Subs = []
            if pm25 is not None:
                pl_Subs.append(self._piecewise_linear(pm25, [(0, 13, 0, 20), (13, 35, 20, 40), (35, 55, 40, 60), (55, 75, 60, 80), (75, 110, 80, 100), (110, 150, 100, 125)]))
            if pm10 is not None:
                pl_Subs.append(self._piecewise_linear(pm10, [(0, 20, 0, 20), (20, 50, 20, 40), (50, 80, 40, 60), (80, 110, 60, 80), (110, 150, 80, 100), (150, 200, 100, 125)]))
            if no2 is not None:
                pl_Subs.append(self._piecewise_linear(no2, [(0, 40, 0, 20), (40, 100, 20, 40), (100, 150, 40, 60), (150, 230, 60, 80), (230, 400, 80, 100), (400, 600, 100, 125)]))
            if o3 is not None:
                pl_Subs.append(self._piecewise_linear(o3, [(0, 70, 0, 20), (70, 120, 20, 40), (120, 150, 40, 60), (150, 180, 60, 80), (180, 240, 80, 100), (240, 300, 100, 125)]))
            if so2 is not None:
                pl_Subs.append(self._piecewise_linear(so2, [(0, 50, 0, 20), (50, 100, 20, 40), (100, 200, 40, 60), (200, 350, 60, 80), (350, 500, 80, 100), (500, 700, 100, 125)]))
            valid = [v for v in pl_Subs if v is not None]
            self.aqi = max(valid) if valid else eu_val
            return

        if region == "UK":
            # UK DEFRA Daily Air Quality Index (DAQI 1-10)
            uk_bands = []
            if pm25 is not None:
                thresholds = [11, 23, 35, 41, 47, 53, 58, 64, 70]
                uk_bands.append(next((i + 1 for i, t in enumerate(thresholds) if pm25 <= t), 10))
            if pm10 is not None:
                thresholds = [16, 33, 50, 58, 66, 75, 83, 91, 100]
                uk_bands.append(next((i + 1 for i, t in enumerate(thresholds) if pm10 <= t), 10))
            if no2 is not None:
                thresholds = [67, 134, 200, 267, 334, 400, 467, 534, 600]
                uk_bands.append(next((i + 1 for i, t in enumerate(thresholds) if no2 <= t), 10))
            if o3 is not None:
                thresholds = [33, 66, 100, 120, 140, 160, 187, 213, 240]
                uk_bands.append(next((i + 1 for i, t in enumerate(thresholds) if o3 <= t), 10))
            self.aqi = max(uk_bands) if uk_bands else eu_val
            return

        if region == "CN":
            # China MEP AQI (HJ 633-2012, 0-500)
            cn_subs = []
            if pm25 is not None:
                cn_subs.append(self._piecewise_linear(pm25, [(0, 35, 0, 50), (35, 75, 50, 100), (75, 115, 100, 150), (115, 150, 150, 200), (150, 250, 200, 300), (250, 350, 300, 400), (350, 500, 400, 500)]))
            if pm10 is not None:
                cn_subs.append(self._piecewise_linear(pm10, [(0, 50, 0, 50), (50, 150, 50, 100), (150, 250, 100, 150), (250, 350, 150, 200), (350, 420, 200, 300), (420, 500, 300, 400), (500, 600, 400, 500)]))
            if no2 is not None:
                cn_subs.append(self._piecewise_linear(no2, [(0, 100, 0, 50), (100, 200, 50, 100), (200, 700, 100, 150), (700, 1200, 150, 200), (1200, 2340, 200, 300), (2340, 3090, 300, 400), (3090, 3840, 400, 500)]))
            if o3 is not None:
                cn_subs.append(self._piecewise_linear(o3, [(0, 160, 0, 50), (160, 200, 50, 100), (200, 300, 100, 150), (300, 400, 150, 200), (400, 800, 200, 300), (800, 1000, 300, 400), (1000, 1200, 400, 500)]))
            valid = [v for v in cn_subs if v is not None]
            self.aqi = max(valid) if valid else us_val
            return

        if region == "IN":
            # India CPCB National AQI (NAQI, 0-500)
            in_subs = []
            if pm25 is not None:
                in_subs.append(self._piecewise_linear(pm25, [(0, 30, 0, 50), (30, 60, 50, 100), (60, 90, 100, 200), (90, 120, 200, 300), (120, 250, 300, 400), (250, 380, 400, 500)]))
            if pm10 is not None:
                in_subs.append(self._piecewise_linear(pm10, [(0, 50, 0, 50), (50, 100, 50, 100), (100, 250, 100, 200), (250, 350, 200, 300), (350, 430, 300, 400), (430, 510, 400, 500)]))
            if no2 is not None:
                in_subs.append(self._piecewise_linear(no2, [(0, 40, 0, 50), (40, 80, 50, 100), (80, 180, 100, 200), (180, 280, 200, 300), (280, 400, 300, 400), (400, 520, 400, 500)]))
            if o3 is not None:
                in_subs.append(self._piecewise_linear(o3, [(0, 50, 0, 50), (50, 100, 50, 100), (100, 168, 100, 200), (168, 208, 200, 300), (208, 748, 300, 400), (748, 900, 400, 500)]))
            valid = [v for v in in_subs if v is not None]
            self.aqi = max(valid) if valid else us_val
            return

        if region == "CA":
            # Canadian Air Quality Health Index (AQHI, 1-10+ scale)
            import math
            o3_ppb = (o3 or 0.0) / 1.96
            no2_ppb = (no2 or 0.0) / 1.88
            pm25_ug = pm25 or 0.0
            aqhi = (1000.0 / 10.4) * (
                (math.exp(0.000537 * o3_ppb) - 1.0)
                + (math.exp(0.000871 * no2_ppb) - 1.0)
                + (math.exp(0.000487 * pm25_ug) - 1.0)
            )
            self.aqi = max(1, int(round(aqhi)))
            return

        if region == "AU":
            # Australian NEPM Air Quality Index (% of national standard)
            au_subs = []
            if pm25 is not None:
                au_subs.append((pm25 / 25.0) * 100.0)
            if pm10 is not None:
                au_subs.append((pm10 / 50.0) * 100.0)
            if no2 is not None:
                au_subs.append((no2 / 164.0) * 100.0)
            if o3 is not None:
                au_subs.append((o3 / 130.0) * 100.0)
            if so2 is not None:
                au_subs.append((so2 / 210.0) * 100.0)
            self.aqi = max(au_subs) if au_subs else eu_val
            return

        self.aqi = eu_val

    def _fetch_data(self):
        try:
            if self.lat is None or self.lon is None:
                # Geocode
                geo_lang = (self.language or "EN").lower()
                url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(self.city)}&count=1&language={geo_lang}&format=json"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 SteelSeries-OLED-Hub'})
                with urllib.request.urlopen(req, timeout=5.0) as response:
                    data = json.loads(response.read().decode())
                    if data.get("results"):
                        res = data["results"][0]
                        self.lat = res["latitude"]
                        self.lon = res["longitude"]
                        self.city = res["name"]
                        self.country_code = (res.get("country_code") or "").upper()
                        self.timezone = res.get("timezone") or ""
                    else:
                        logger.error(f"Weather: City {self.city} not found.")
                        return

            # Fetch weather
            url = f"https://api.open-meteo.com/v1/forecast?latitude={self.lat}&longitude={self.lon}&current_weather=true"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 SteelSeries-OLED-Hub'})
            with urllib.request.urlopen(req, timeout=5.0) as response:
                data = json.loads(response.read().decode())
                current = data.get("current_weather", {})
                self.temperature = current.get("temperature")
                self.weathercode = current.get("weathercode")
                
            # Fetch AQI & Pollutants for Multi-Region calculation
            try:
                aqi_url = (
                    f"https://air-quality-api.open-meteo.com/v1/air-quality"
                    f"?latitude={self.lat}&longitude={self.lon}"
                    f"&current=european_aqi,us_aqi,pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone"
                )
                aqi_req = urllib.request.Request(aqi_url, headers={'User-Agent': 'Mozilla/5.0 SteelSeries-OLED-Hub'})
                with urllib.request.urlopen(aqi_req, timeout=5.0) as aqi_response:
                    aqi_data = json.loads(aqi_response.read().decode())
                    self._last_aqi_raw = aqi_data.get("current", {})
                    self._compute_regional_aqi(self._last_aqi_raw)
            except Exception as e:
                logger.error(f"AQI fetch failed: {e}")
                self.aqi = None
                
        except Exception as e:
            logger.error(f"Weather fetch failed: {e}")

    def get_weather_data(self):
        if not self.enabled:
            return None
        self.update()
        if self.temperature is not None:
            return {
                "temp": self.temperature,
                "code": self.weathercode,
                "aqi": self.aqi,
                "aqi_region": self.effective_aqi_region,
            }
        return None

    def get_weather_desc(self, code):
        tr = self.WEATHER_TRANSLATIONS.get(self.language, self.WEATHER_TRANSLATIONS["EN"])
        if code is None: return tr["na"]
        if code == 0: return tr["clear"]
        if code in [1, 2, 3]: return tr["cloudy"]
        if code in [45, 48]: return tr["fog"]
        if code in [51, 53, 55, 56, 57]: return tr["drizzle"]
        if code in [61, 63, 65, 66, 67, 80, 81, 82]: return tr["rain"]
        if code in [71, 73, 75, 77, 85, 86]: return tr["snow"]
        if code in [95, 96, 99]: return tr["storm"]
        return tr["unknown"]

    def get_image(self):
        img = Image.new("1", (128, 40), 0)
        draw = ImageDraw.Draw(img)

        # Try to update if interval passed
        self.update()

        if self.temperature is not None:
            # Left side: City (normalized for ASCII pixel font) and description
            city_display = normalize_text(self.city)[:15].upper()
            draw.text((2, 2), city_display, font=self.small_font, fill=1)
            desc = self.get_weather_desc(self.weathercode)
            draw.text((2, 25), desc, font=self.small_font, fill=1)
            
            # Right side: Temperature
            temp_text = f"{int(round(self.temperature))}°C"
            bbox = draw.textbbox((0, 0), temp_text, font=self.font)
            w = bbox[2] - bbox[0]
            draw.text((126 - w, 12), temp_text, font=self.font, fill=1)
            
            # Middle: Icon
            icon_x, icon_y = 64, 18
            if self.weathercode == 0:
                draw.ellipse((icon_x-5, icon_y-5, icon_x+5, icon_y+5), outline=1)
                draw.line((icon_x-8, icon_y, icon_x+8, icon_y), fill=1)
                draw.line((icon_x, icon_y-8, icon_x, icon_y+8), fill=1)
            elif self.weathercode in [1, 2, 3]:
                draw.ellipse((icon_x-8, icon_y-2, icon_x, icon_y+6), outline=1)
                draw.ellipse((icon_x-2, icon_y-6, icon_x+8, icon_y+4), outline=1)
                draw.ellipse((icon_x+2, icon_y-2, icon_x+10, icon_y+6), outline=1)
            elif self.weathercode in [61, 63, 65, 80, 81, 82]:
                draw.ellipse((icon_x-8, icon_y-8, icon_x+8, icon_y), outline=1)
                draw.line((icon_x-4, icon_y+2, icon_x-6, icon_y+8), fill=1)
                draw.line((icon_x, icon_y+2, icon_x-2, icon_y+8), fill=1)
                draw.line((icon_x+4, icon_y+2, icon_x+2, icon_y+8), fill=1)
            elif self.weathercode in [71, 73, 75, 85, 86]:
                draw.text((icon_x-4, icon_y-8), "*", font=self.font, fill=1)
            elif self.weathercode in [95, 96, 99]:
                draw.polygon([(icon_x, icon_y-8), (icon_x-4, icon_y+2), (icon_x+2, icon_y), (icon_x-2, icon_y+8)], fill=1)
            else:
                draw.ellipse((icon_x-5, icon_y-5, icon_x+5, icon_y+5), outline=1)
                
            if self.aqi is not None:
                aqi_text = f"{self.effective_aqi_region} AQI: {int(round(self.aqi))}"
                bbox_aqi = draw.textbbox((0, 0), aqi_text, font=self.small_font)
                w_aqi = bbox_aqi[2] - bbox_aqi[0]
                draw.text((126 - w_aqi, 28), aqi_text, font=self.small_font, fill=1)
        else:
            tr = self.WEATHER_TRANSLATIONS.get(self.language, self.WEATHER_TRANSLATIONS["EN"])
            draw.text((2, 12), tr["loading"], font=self.font, fill=1)

        return img


