import logging
from time import time
import json
import urllib.request
import urllib.parse
from threading import Thread
from PIL import Image, ImageDraw, ImageFont
from src.image_utils import fetch_content_path

logger = logging.getLogger("OLED Customizer.Weather")

class WeatherModule:
    def __init__(self, config, preferences):
        self.config = config
        self.preferences = preferences
        
        self.city = self.preferences.get_preference("weather_city") or "Warszawa"
        self.enabled = bool(self.preferences.get_preference("weather_enabled"))
        
        self.lat = None
        self.lon = None
        self.temperature = None
        self.weathercode = None
        self.aqi = None
        self.last_update = 0
        self.update_interval = 1800  # 30 mins
        
        self.font = ImageFont.truetype(fetch_content_path("fonts/VerdanaBold.ttf"), 14)
        self.small_font = ImageFont.truetype(fetch_content_path("fonts/MunroSmall.ttf"), 10)
        
        self._thread = None
        self.update()

    def update_preferences(self, preferences):
        self.preferences = preferences
        new_city = self.preferences.get_preference("weather_city") or "Warszawa"
        self.enabled = bool(self.preferences.get_preference("weather_enabled"))
        
        if new_city != self.city:
            self.city = new_city
            self.lat = None
            self.lon = None
            self.last_update = 0
            self.temperature = None
            self.update()

    def update(self):
        if not self.enabled:
            return
        if time() - self.last_update > self.update_interval:
            self.last_update = time()
            self._thread = Thread(target=self._fetch_data, daemon=True)
            self._thread.start()

    def _fetch_data(self):
        try:
            if self.lat is None or self.lon is None:
                # Geocode
                url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(self.city)}&count=1&language=pl&format=json"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 SteelSeries-OLED-Hub'})
                with urllib.request.urlopen(req, timeout=5.0) as response:
                    data = json.loads(response.read().decode())
                    if data.get("results"):
                        self.lat = data["results"][0]["latitude"]
                        self.lon = data["results"][0]["longitude"]
                        self.city = data["results"][0]["name"]
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
                
            # Fetch AQI
            try:
                aqi_url = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={self.lat}&longitude={self.lon}&current=european_aqi"
                aqi_req = urllib.request.Request(aqi_url, headers={'User-Agent': 'Mozilla/5.0 SteelSeries-OLED-Hub'})
                with urllib.request.urlopen(aqi_req, timeout=5.0) as aqi_response:
                    aqi_data = json.loads(aqi_response.read().decode())
                    self.aqi = aqi_data.get("current", {}).get("european_aqi")
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
            return {"temp": self.temperature, "code": self.weathercode, "aqi": self.aqi}
        return None

    def get_weather_desc(self, code):
        if code is None: return "Brak"
        if code == 0: return "Bezchmurnie"
        if code in [1, 2, 3]: return "Zachmurzenie"
        if code in [45, 48]: return "Mgla"
        if code in [51, 53, 55, 56, 57]: return "Mzawka"
        if code in [61, 63, 65, 66, 67, 80, 81, 82]: return "Deszcz"
        if code in [71, 73, 75, 77, 85, 86]: return "Snieg"
        if code in [95, 96, 99]: return "Burza"
        return "Nieznana"

    def get_image(self):
        img = Image.new("1", (128, 40), 0)
        draw = ImageDraw.Draw(img)

        # Try to update if interval passed
        self.update()

        if self.temperature is not None:
            # Left side: City and description
            draw.text((2, 2), self.city[:15].upper(), font=self.small_font, fill=1)
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
                aqi_text = f"AQI: {int(round(self.aqi))}"
                bbox_aqi = draw.textbbox((0, 0), aqi_text, font=self.small_font)
                w_aqi = bbox_aqi[2] - bbox_aqi[0]
                draw.text((126 - w_aqi, 28), aqi_text, font=self.small_font, fill=1)
        else:
            draw.text((2, 12), "Pobieranie...", font=self.font, fill=1)

        return img

