# 📜 Changelog — SteelSeries OLED & RGB Hub

Pełna historia zmian projektu **SteelSeries OLED & RGB Hub** rozwijanego przez **[gizmous](https://github.com/Gizmous100)** na bazie oryginalnego projektu **[OLED Customizer](https://github.com/0z-zy/OLED_Customizer)** autorstwa **[0z-zy](https://github.com/0z-zy)** (inspirowanego **[SteelSeries-Spotify-Linker](https://github.com/Firewee/SteelSeries-Spotify-Linker)** autorstwa **[Firewee](https://github.com/Firewee)**).

**Zasady wersjonowania projektu:**
- **`+0.10` (`Major`)** — nowe główne moduły funkcjonalne (np. `v1.10`, `v1.20`, `v1.30`, `v1.40`).
- **`+0.01` (`Fix / Feature Upgrade`)** — poprawki błędów, optymalizacje architektury oraz rozbudowa istniejących modułów (np. `v1.41` – `v1.47`).

---

## [`v1.47`] — 2026-10-10 — Rozbudowa modułu pogody o globalne / regionalne skale jakości powietrza (`AQI`)
### ✨ Nowości i ulepszenia (`+0.01`)
- **Wybór obszaru / standardu AQI w ustawieniach (`Settings → Display → 🌦️ Weather Widget`):**
  - Dodano listę wyboru **Obszar / Skala AQI (AQI Region)** zapisywaną w preferencjach (`weather_aqi_region`).
  - Dodano obsługę **9 trybów i regionalnych standardów jakości powietrza** w [`src/Weather.py`](src/Weather.py):
    1. **`Auto (Wg miasta / By City)`** — automatyczny wybór skali AQI na podstawie kodu kraju (`country_code`) oraz strefy czasowej (`timezone`) wyszukanego miasta z API geokodowania Open-Meteo.
    2. **`Europa (EU - CAQI)`** — Europejski Indeks Jakości Powietrza EEA (`0–100+`).
    3. **`USA / Ameryki (US - EPA)`** — Amerykański standard US EPA AQI (`0–500`).
    4. **`Polska (PL - GIOŚ)`** — Polski Indeks Jakości Powietrza Głównego Inspektoratu Ochrony Środowiska (`0–100+`) obliczany na żywo ze stężeń `PM2.5`, `PM10`, `NO₂`, `O₃` oraz `SO₂` według oficjalnych progów GIOŚ.
    5. **`Wielka Brytania (UK - DAQI)`** — Brytyjski Daily Air Quality Index DEFRA (`skala 1–10`).
    6. **`Chiny (CN - MEP)`** — Chiński standard Ministerstwa Ekologii i Środowiska `HJ 633-2012` (`0–500`).
    7. **`Indie (IN - NAQI)`** — Indyjski National Air Quality Index CPCB (`0–500`).
    8. **`Kanada (CA - AQHI)`** — Kanadyjski Air Quality Health Index (`skala 1–10+`) wyliczany eksponencjalnie z `O₃`, `NO₂` i `PM2.5`.
    9. **`Australia (AU - NEPM)`** — Australijski standard NEPM AQI (`0–100+%` normy krajowej).
- **Prezentacja wybranego regionu na pełnoekranowej karcie pogody (`F8`):**
  - Pełnoekranowy ekran pogody wyświetla w prawym dolnym rogu skrót aktywnej skali wraz z wartością (np. `EU AQI: 30`, `US AQI: 46`, `PL AQI: 15`, `UK AQI: 2`).
- **Natychmiastowe przeliczanie po zmianie w GUI:**
  - Zmiana regionu AQI w ustawieniach przelicza indeks natychmiast z ostatnio pobranych danych zanieczyszczeń bez konieczności czekania na kolejny cykl odświeżania.

---

## [`v1.46`] — 2026-10-10 — Pełne wsparcie 10-strefowego RGB myszek (`rgb-zoned-device`) i synchronizacja profili GG
### ✨ Poprawki i ulepszenia (`+0.01`)
- **Obsługa wielostrefowych myszek SteelSeries (`rgb-zoned-device`):**
  - Rozszerzono rejestrację zdarzenia `LIGHTING` w [`src/SteelSeriesAPI.py`](src/SteelSeriesAPI.py) o urządzenie `rgb-zoned-device` oraz wszystkie 10 niezależnych stref (`one`, `two`, `three`, `four`, `five`, `six`, `seven`, `eight`, `nine`, `ten`) oprócz tradycyjnych stref `wheel` i `logo`.
  - Rozwiązano problem, w którym myszki wielostrefowe (np. **SteelSeries Rival 5**) pozostawały na fabrycznym pomarańczowym kolorze SteelSeries mimo włączenia synchronizacji RGB lub wejścia w tryb AFK.
- **Automatyczne wykrywanie przełączenia profilu (Config) w SteelSeries GG:**
  - Dodano w [`src/DisplayManager.py`](src/DisplayManager.py) monitorowanie zmian w bazie danych `C:\ProgramData\SteelSeries\GG\apps\engine\db\database.db` (`-wal`). Po zmianie konfiguracji w SteelSeries GG aplikacja automatycznie ponawia synchronizację koloru RGB.
- **Architektura oświetlenia oparta wyłącznie na przejściach stanów:**
  - Wyeliminowano wysyłanie zdarzeń RGB w pętli 10 FPS (zapobieganie błędom `HTTP 429 Too Many Requests` i zawieszaniu profilu myszki).

---

## [`v1.45`] — 2026-10-10 — Histereza odtwarzania wideo (Prime Video / Netflix / YouTube) i blokada konfliktów GG
### ✨ Poprawki i ulepszenia (`+0.01`)
- **Histereza pauzy / buforowania SMTC (2.5 sekundy):**
  - Dodano okno podtrzymania stanu odtwarzania (`pause_grace_ms`) w [`src/DisplayManager.py`](src/DisplayManager.py), które pochłania chwilowe zmiany statusu `Paused`/`Stopped` zgłaszane przez aplikacje wideo (Amazon Prime Video, Netflix, przeglądarki) podczas buforowania lub przewijania. Wyeliminowano migotanie ekranu OLED między odtwarzaczem a zegarem.
- **Rozpoznawanie źródeł wideo w SMTC:**
  - Dodano rozpoznawanie aplikacji wideo (`primevideo`, `amazonvideo`, `netflix`, `disney`, `vlc`, `mpv`, `mpc`, `potplayer`, `kodi`) w [`src/DisplayManager.py`](src/DisplayManager.py) oraz [`src/WindowsMedia.py`](src/WindowsMedia.py).
- **Automatyczne wstrzymywanie `now-playing.exe`:**
  - Dodano w [`src/SteelSeriesAPI.py`](src/SteelSeriesAPI.py) automatyczne wstrzymywanie wbudowanego apletu `now-playing.exe` ze SteelSeries GG na czas działania programu (oraz wznawianie go przy zamknięciu), aby zapobiec walce dwóch procesów o wyświetlacz OLED.

---

## [`v1.44`] — 2026-10-09 — Optymalizacja pamięci, wątków i blokady pojedynczej instancji
### ✨ Poprawki i ulepszenia (`+0.01`)
- **Zamykanie osieroconych procesów przy starcie (`_kill_orphan_instances`):**
  - W [`main.py`](main.py) dodano automatyczne wykrywanie i zamykanie zawieszonych procesów z poprzednich sesji przed utworzeniem pliku blokady `.lock`.
- **Eliminacja wycieków pamięci i uchwytów PIL/GDI:**
  - Zoptymalizowano pętlę renderowania 10 FPS oraz buforowanie klatek (`_last_sent_frame`), ograniczając zbędne alokacje obiektów graficznych.
- **Usunięcie powtarzających się klatek w trybie AFK:**
  - W trybie AFK pusta klatka (`[0] * 640`) jest wysyłana wyłącznie raz przy przejściu w stan bezczynności zamiast co sekundę, co zapobiega wybudzaniu i mruganiu ekranu OLED.

---

## [`v1.43`] — 2026-10-09 — Stała szerokość cyfr (`Anti-Jitter`) i adaptacyjne układy zegara
### ✨ Poprawki i ulepszenia (`+0.01`)
- **Renderowanie cyfr o stałej szerokości (`_draw_fixed_width_text`):**
  - W [`src/Timer.py`](src/Timer.py) zaimplementowano renderowanie każdej cyfry zegara w komórce o stałej szerokości równej cyfrze `"0"`. Zegar nie przesuwa się już na boki, gdy w sekundniku pojawiają się węższe cyfry (`1`, `4`).
- **Dynamiczne dopasowanie wszystkich 4 stylów zegara do aktywnych widżetów bocznych:**
  - Wszystkie 4 motywy (*Standard*, *Big Timer*, *Date Focused*, *Analog*) dynamicznie wyliczają dostępną przestrzeń poziomą `[x_left .. x_right]` w zależności od tego, czy aktywny jest widżet pogody po lewej (`32px`) oraz widżet baterii słuchawek po prawej (`19px`), automatycznie dobierając rozmiar czcionki i środek ciężkości.

---

## [`v1.42`] — 2026-10-09 — Odporność na brak bibliotek SQLite (`sqlite3.dll`) i poprawka okna Ustawień
### ✨ Poprawki i ulepszenia (`+0.01`)
- **Wbudowana biblioteka `sqlite3.dll` i bezpieczny import (`Fault Tolerance`):**
  - Dodano plik `sqlite3.dll` do projektu i specyfikacji PyInstaller oraz zabezpieczono [`src/ProfileBackup.py`](src/ProfileBackup.py) blokami `try...except`, dzięki czemu brak systemowych bibliotek DLL nie blokuje uruchomienia programu, a kopie zapasowe i kompaktowanie baz (`VACUUM`) działają na każdym systemie Windows.
- **Poprawka ponownego otwierania okna Ustawień (`SettingsWindow.py`):**
  - Naprawiono zwalnianie blokady `_open_gui` i niszczenie okna `Tk()` po kliknięciu `SAVE`, co umożliwiało wielokrotne otwieranie okna ustawień z zasobnika systemowego bez błędu wątku Tkintera.

---

## [`v1.41`] — 2026-10-09 — Kompaktowe polskie nazwy dni tygodnia i skrócony format daty
### ✨ Poprawki i ulepszenia (`+0.01`)
- **Polskie skróty dni tygodnia (`use_polish_days`):**
  - Zastąpiono dawną opcję językową opcją **„Użyj języka polskiego”** w [`src/Timer.py`](src/Timer.py), [`src/SettingsWindow.py`](src/SettingsWindow.py) i [`src/UserPreferences.py`](src/UserPreferences.py).
  - Wprowadzono ultrakompaktowe polskie oznaczenia dni tygodnia (`P`, `W`, `Sr`, `C`, `Pt`, `S`, `N`) oraz skrócony format roku (`dd/mm/yy`), dzięki czemu data mieści się czytelnie na ekranie 128×40 nawet przy jednocześnie włączonej pogodzie i baterii słuchawek.

---

## [`v1.40`] — 2026-10-09 — 🆕 Inteligentny tryb bezczynności (Smart Idle / AFK Mode) z obsługą padów XInput
### 🆕 Nowy główny moduł (`+0.10`)
- **Automatyczne wygaszanie ekranu OLED i podświetlenia RGB (`afk_enabled`, `afk_timeout_minutes`):**
  - Po określonym czasie bezczynności użytkownika (domyślnie 5 minut) aplikacja wygasza wyświetlacz OLED (ochrona przed wypaleniem matrycy) oraz wyłącza podświetlenie RGB klawiatury i myszki.
- **Wykrywanie aktywności kontrolerów / padów XInput (`src/afk_monitor.py`):**
  - Dodano moduł [`src/afk_monitor.py`](src/afk_monitor.py), który w tle odpytuje do 4 podłączonych kontrolerów XInput (`XInputGetState` z `xinput1_4.dll` / `xinput1_3.dll` / `xinput9_1_0.dll`).
  - Poruszanie drążkami analogowymi, wciskanie spustów (LT/RT) lub przycisków na padzie podczas gry automatycznie resetuje licznik bezczynności Windows (`SendInput` z `VK_F15`), zapobiegając wygaszeniu ekranu OLED i podświetlenia w trakcie grania na kontrolerze.
- **Filtrowanie pozornych wejść (Phantom Input Guard):**
  - Zabezpieczono pętlę AFK przed fałszywymi impulsami wejścia generowanymi przez sterownik SteelSeries w ciągu 3 sekund od przełączenia stanu podświetlenia.

---

## [`v1.30`] — 2026-10-08 — 🆕 Moduł Pogody na żywo i Indeksu Jakości Powietrza (`src/Weather.py`)
### 🆕 Nowy główny moduł (`+0.10`)
- **Integracja z Open-Meteo bez kluczy API (`src/Weather.py`):**
  - Dodano moduł [`src/Weather.py`](src/Weather.py) pobierający współrzędne geograficzne wybranego miasta (Open-Meteo Geocoding API), aktualną temperaturę i kod pogody WMO (Open-Meteo Forecast API) oraz jakość powietrza (`AQI`, Open-Meteo Air Quality API).
- **Mini-widżet pogody na ekranie zegara (`src/Timer.py`):**
  - Po lewej stronie ekranu zegara (`x = 0..31`) wyświetlana jest pikseloartowa ikona warunków pogodowych (słońce, chmury, deszcz, śnieg, burza), wskaźnik `AQI` oraz aktualna temperatura w `°C`.
- **Pełnoekranowa karta pogody pod skrótem klawiszowym (`F8`):**
  - Dodano globalny skrót klawiszowy (domyślnie `F8`, konfigurowalny w zakładce *Hotkeys*), który w dowolnym momencie (również w grach pełnoekranowych) wyświetla przez zadany czas (domyślnie 5 s) pełnoekranową kartę pogody z nazwą miasta, opisem zjawiska po polsku, ikoną, temperaturą i `AQI`.

---

## [`v1.20`] — 2026-10-07 — 🆕 Poziom baterii bezprzewodowych słuchawek przez natywny USB HID (`src/HIDListener.py`)
### 🆕 Nowy główny moduł (`+0.10`)
- **Bezpośredni odczyt baterii słuchawek przez USB HID:**
  - Rozbudowano [`src/HIDListener.py`](src/HIDListener.py) o aktywne odpytywanie odbiornika USB SteelSeries raportem HID (`[0x06, 0xb0]`) w celu odczytu poziomu naładowania baterii słuchawek bezprzewodowych (m.in. **SteelSeries Arctis Nova 7** oraz kompatybilnych modeli HyperX / Cloud III).
- **Pionowa 4-segmentowa ikona baterii na ekranie zegara (`src/Timer.py`):**
  - W prawym górnym rogu ekranu zegara (`x = 110..127`) rysowana jest czytelna pionowa ikona baterii z 4 segmentami oraz opcjonalnym wynikiem procentowym (`0%`–`100%`) wyśrodkowanym pod ikoną.
- **Wykrywanie wyłączenia słuchawek / utraty zasięgu:**
  - Aplikacja rozpoznaje stan wyłączenia słuchawek na podstawie bajtów statusu raportu HID i natychmiast ukrywa widżet baterii, aby nie wyświetlać nieaktualnego poziomu naładowania.

---

## [`v1.10`] — 2026-10-07 — 🆕 Automatyczne przełączanie na Monitor Sprzętu w grach 3D
### 🆕 Nowy główny moduł (`+0.10`)
- **Automatyczny tryb „System Stats in Games” (`auto_hw_monitor_game`):**
  - Dodano w [`src/HardwareMonitor.py`](src/HardwareMonitor.py), [`src/DisplayManager.py`](src/DisplayManager.py) oraz [`src/SettingsWindow.py`](src/SettingsWindow.py) funkcję automatycznego przełączania wyświetlacza OLED z zegara na ekran monitora zasobów (**CPU / GPU / RAM / FPS**), gdy tylko PresentMon wykryje aktywny proces gry 3D renderujący klatki.
  - Po zamknięciu gry wyświetlacz automatycznie powraca do zegara lub odtwarzacza multimediów.

---

## [`v1.00`] — Fundament projektu — Oryginalny OLED Customizer (`0z-zy` / `Firewee`)
### 📦 Funkcje bazowe zachowane z oryginalnej aplikacji
- **Dynamiczny panel multimediów (Spotify Web API + Windows SMTC):**
  - Wyświetlanie tytułu utworu, wykonawcy, paska postępu, czasu trwania oraz ikon źródła (Spotify, YouTube, Media) w 5 stylach (*Standard*, *Compact*, *Centered*, *Ticker*, *Minimal*).
- **Ghost-Monitor (`src/HardwareMonitor.py`, `src/fps_monitor.py`):**
  - Podgląd obciążenia i temperatury CPU/GPU (LibreHardwareMonitor + fallback WMI), użycia pamięci RAM oraz licznika FPS na żywo (PresentMon) po naciśnięciu klawisza `Insert` lub w trybie stałym.
- **Sterowanie głośnością i integracja z Discordem (`src/volume.py`, `src/DiscordRPC.py`):**
  - Wizualny pasek głośności na ekranie OLED, synchronizacja statusu mikrofonu z Discordem przez lokalne IPC oraz globalny skrót wyciszania mikrofonu (obsługa klawiatury, przycisków bocznych myszy `Mouse 4` / `Mouse 5` oraz fizycznego przycisku mute na słuchawkach).
- **Wbudowany kalkulator OLED (`src/Calculator.py`):**
  - Interaktywny kalkulator uruchamiany skrótem `Ctrl + Insert` z przechwytywaniem klawiatury numerycznej (Numpad) w niskopoziomowym hooku `WH_KEYBOARD_LL`.
- **4 style zegara (`src/Timer.py`):**
  - Motywy *Standard*, *Big Timer*, *Date Focused* oraz *Analog* z wyborem formatu 12h/24h i sekundnika.
- **Kopie zapasowe profili SteelSeries GG (`src/ProfileBackup.py`):**
  - Tworzenie i przywracanie kopii zapasowych baz danych `engine`, `prism` oraz `sonar` wraz z funkcją kompaktowania (`VACUUM`).

