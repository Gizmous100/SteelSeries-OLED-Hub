# 📜 Changelog — SteelSeries OLED & RGB Hub

Complete version history for **SteelSeries OLED & RGB Hub**, developed and expanded by **[gizmous](https://github.com/Gizmous100)** on top of the original **[OLED Customizer](https://github.com/0z-zy/OLED_Customizer)** created by **[0z-zy](https://github.com/0z-zy)** (inspired by **[SteelSeries-Spotify-Linker](https://github.com/Firewee/SteelSeries-Spotify-Linker)** by **[Firewee](https://github.com/Firewee)**).

**Project Versioning Policy:**
- **`+0.10` (`Major`)** — brand-new major functional modules (e.g., `v1.10`, `v1.20`, `v1.30`, `v1.40`).
- **`+0.01` (`Fix / Feature Upgrade`)** — bug fixes, architectural optimizations, and enhancements to existing modules (e.g., `v1.41` – `v1.45`).

---

## [`v1.45`] — 2026-11-01 — Dual-Device AFK Blackout, Headset Battery HID Discovery & Gamepad Play Compatibility
### ✨ Fix / Feature Upgrade (`+0.01`)
- **Flicker-Free AFK Wake-Up & Atomic Single-Pass Profile Restore (`src/SteelSeriesAPI.py`):**
  - Resolved keyboard LED flickering upon AFK wake-up by making encrypted REST hardware configuration blackout primary, eliminating GameSense Lisp VM `device reset:` churn.
  - Single atomic `forceDeploy` pass restores user profiles smoothly with zero flashing.
- **AFK Mode Compatibility During Gamepad Play & Controller Decoupling (`src/DisplayManager.py`):**
  - Resolved issue where gamepad USB polling prevented AFK blackout even with in-game suppression disabled.
  - Decoupled gamepad telemetry so keyboard, mouse, and OLED enter power-saving blackout during controller gameplay, waking strictly on physical keyboard/mouse input.
- **Wireless Headset HID Discovery Hardening (`src/HIDListener.py`):**
  - Eliminated false latching onto keyboards/mice by implementing multi-stage candidate scoring (Product String matching `Arctis`, `Nova`, `Headset`; PID exclusion for known keyboards/mice; usage page validation).
  - Resilient periodic re-discovery reconnects to headsets after waking from sleep or dongle reconnection.
- **AFK Mouse Blackout & SSL Module Import Fix (`src/SteelSeriesAPI.py`):**
  - Moved `ssl` and `urllib3` imports to module scope to eliminate runtime `ImportError: cannot import name 'ssl'` inside low-memory worker threads, ensuring reliable native blackout for all devices.
  - Universal wake-up guard ensures instantaneous lighting restoration even under high CPU load.

---

## [`v1.44`] — 2026-10-27 — AFK Sensor Deadband, Screen Sleep Guard & Precise Input Tracking
### ✨ Fix / Feature Upgrade (`+0.01`)
- **Optical Sensor Jitter Deadband (`src/DisplayManager.py`):**
  - Implemented 15-pixel Euclidean displacement deadband (`math.hypot(dx, dy) >= 15.0`) to filter out microscopic optical sensor jitter on high-DPI mouse sensors (such as SteelSeries TrueMove Air).
- **Display Sleep / Blanking Guard (`src/DisplayManager.py`):**
  - Prevents spurious input wakeups triggered when Windows turns off displays or enters monitor standby.
- **Click-Only Low-Level Mouse Hook (`src/DisplayManager.py`):**
  - Restricted `WH_MOUSE_LL` hook to mouse clicks and wheel scrolling, leaving coordinate displacement to the deadband filter.
- **Integrated Controller Support for AFK Detection (`src/DisplayManager.py`):**
  - Directly integrated XInput state checks into `DisplayManager` to streamline controller responsiveness.

---

## [`v1.43`] — 2026-10-23 — Profile Switch Detection, GG Preconfig OLED Overlay & Silent Auto-Launch
### ✨ Fix / Feature Upgrade (`+0.01`)
- **Active Profile Priority in Engine Redeploy (`src/SteelSeriesAPI.py`):**
  - Ensured `active: 1` candidate profile selection strictly precedes `last_deployed: 1` during engine redeploy, preventing accidental fallback to desktop profiles during games like *Black Desert*.
- **Real-Time Profile Switch Detection & Instant OLED Overlay (`src/DisplayManager.py`, `src/SteelSeriesAPI.py`):**
  - Sub-100ms detection of game profile changes via continuous SQLite WAL monitoring.
  - Native-styled 128×40 OLED overlay display for GG preconfigs and game assignments that lack physical onboard animations.
  - Dedicated configuration section in Settings under Display with test preview button.
- **Silent Background SteelSeries GG Auto-Launch (`src/SteelSeriesAPI.py`):**
  - Automatically launches SteelSeries GG minimized via `SteelSeriesGGEZ.exe` or `SteelSeriesGG.exe` with `-auto=true` when GG is not detected running on startup.

---

## [`v1.42`] — 2026-10-12 — AFK Engine Lighting Coexistence, GUI Hardening & Input Safeguards
### ✨ Fix / Feature Upgrade (`+0.01`)
- **100% Native SteelSeries GG Engine Config Coexistence (`src/SteelSeriesAPI.py`, `src/DisplayManager.py`):**
  - Removed legacy static single-color override ("Match Keyboard Color") that hijacked GG illumination. Active usage now grants native GG engine control.
- **Isolated AFK RGB GameSense Architecture & Rival 5 Orange Fix (`src/SteelSeriesAPI.py`):**
  - Separated OLED updates (`STEELSERIES_OLED_HUB`) and AFK blackout (`STEELSERIES_AFK_RGB`) into independent games, eliminating engine Lisp VM composite zone caching issues that caused Rival 5 to revert to factory orange.
- **Apex 7 Hardware `idle_timeout` Override & Triple-Redundant Activity Tracking (`src/SteelSeriesAPI.py`, `src/DisplayManager.py`, `src/utils.py`):**
  - Automatically overrides keyboard hardware `idle_timeout` to 0 in Engine configs so keyboard LEDs don't cut out during mouse-only desktop activity.
  - Activity tracking uses `min(win_idle_ms, hook_idle_ms, cursor_idle_ms)` with `GetCursorPos` and low-level mouse/keyboard hooks.
- **In-Game AFK Suppression & Clock Voice Indicators (`src/HardwareMonitor.py`, `src/Timer.py`, `src/SettingsWindow.py`):**
  - Added option to disable AFK mode while fullscreen 3D games are active.
  - Visual mute and deafen status icons displayed on the clock screen (replacing the weather cloud when Discord is muted/deafened).
  - Mock Discord mode for testing voice status indicators without live Discord IPC.
- **Settings GUI Stability Hardening (`src/SettingsWindow.py`):**
  - Resolved Tcl/Tk crash (`tcl86t.dll`) by ensuring safe main-thread GUI dispatch and proper lock release.

---

## [`v1.41`] — 2026-10-10 — Personalization, AQI Standards, 9-Language OLED & Discord Integration
### ✨ Fix / Feature Upgrade (`+0.01`)
- **Multi-Region / Global Air Quality Index (`AQI`) Standards (`src/Weather.py`, `src/SettingsWindow.py`):**
  - Added support for 9 regional/global AQI standards: `Auto (By City)`, `Europe (EU - CAQI)`, `USA / Americas (US - EPA)`, `Poland (PL - GIOS)`, `United Kingdom (UK - DAQI)`, `China (CN - MEP)`, `India (IN - NAQI)`, `Canada (CA - AQHI)`, and `Australia (AU - NEPM)`.
  - Instant recalculation upon saving Settings; regional prefix badge rendered on full-screen weather overlay (`F8`).
- **Unified 9-Language OLED Localization (`src/Timer.py`, `src/Weather.py`, `src/utils.py`):**
  - Added 9-language selector (`English`, `Polish`, `German`, `Spanish`, `French`, `Italian`, `Portuguese`, `Turkish`, `Dutch`) for Clock days and Weather overlay descriptions.
  - Diacritic transliteration via `normalize_text()` ensures clean 7-bit ASCII rendering without missing glyphs.
- **Full 10-Zone Mouse RGB (`rgb-zoned-device`) & Profile Sync (`src/SteelSeriesAPI.py`, `src/DisplayManager.py`):**
  - Extended lighting events to include `rgb-zoned-device` with 10 independent zones plus wheel/logo, fixing Rival 5 sticking on factory orange.
  - Background monitoring of SteelSeries GG database for automatic profile switch sync.
  - Transition-only lighting events prevent HTTP 429 errors.
- **Discord RPC OAuth Validation, Setup Helpers & Initial Sync (`src/DiscordRPC.py`, `src/SettingsWindow.py`):**
  - Validation of Discord Snowflake App ID and Client Secret with interactive setup helpers.
  - Immediate persistence and live IPC reload upon connecting.
- **Streaming & Video Playback Hysteresis (`src/DisplayManager.py`, `src/WindowsMedia.py`):**
  - 2.5-second pause grace window absorbs buffering/seeking blips on Prime Video, Netflix, YouTube.
  - Automatic suspension of conflicting GG `now-playing.exe` applet during execution.
- **Stability, Anti-Jitter & Personalization Hardening:**
  - Fixed-width anti-jitter clock digits and dynamic layout scaling based on active side widgets.
  - Bundled `sqlite3.dll` with fault-tolerant imports for GG profile backup/compaction.
  - Compact Polish day abbreviations and orphaned process cleanup.

---

## [`v1.40`] — 2026-10-09 — 🆕 Smart Idle / AFK Mode with XInput Gamepad Detection
### 🆕 New Major Module (`+0.10`)
- **Automatic OLED & RGB Blackout on Idle (`afk_enabled`, `afk_timeout_minutes`):**
  - After a configurable user inactivity timeout (default: 5 minutes), the application blanks the OLED screen (burn-in protection) and turns off keyboard and mouse RGB lighting.
- **XInput Controller / Gamepad Activity Detection (`src/afk_monitor.py`):**
  - Added [`src/afk_monitor.py`](src/afk_monitor.py) to poll up to 4 connected XInput controllers (`XInputGetState` via `xinput1_4.dll` / `xinput1_3.dll`).
  - Moving analog sticks, pressing triggers (LT/RT), or pressing buttons on a gamepad automatically resets the Windows idle timer (`VK_F15`), preventing the OLED screen and RGB lighting from turning off while gaming with a controller.
- **Phantom Input Guard:**
  - Protected the AFK state machine against false input impulses generated by SteelSeries drivers within 3 seconds of switching lighting states.

---

## [`v1.30`] — 2026-10-08 — 🆕 Live Weather & Air Quality Index Module (`src/Weather.py`)
### 🆕 New Major Module (`+0.10`)
- **Zero-API-Key Open-Meteo Integration (`src/Weather.py`):**
  - Added [`src/Weather.py`](src/Weather.py) to fetch city coordinates (Open-Meteo Geocoding API), live temperature and WMO weather condition codes (Open-Meteo Forecast API), and Air Quality Index data (`AQI`, Open-Meteo Air Quality API).
- **Clock Screen Weather Mini-Widget (`src/Timer.py`):**
  - Displays a pixel-art weather condition icon (sun, clouds, rain, snow, storm), `AQI` indicator, and current temperature in `°C` on the left side of the clock display (`x = 0..31`).
- **Full-Screen Weather Overlay via Global Hotkey (`F8`):**
  - Added a configurable global hotkey (default: `F8` in the *Hotkeys* tab) that displays a full-screen weather card showing the city name, weather condition description, icon, temperature, and `AQI` for a configurable duration (default: 5 seconds).

---

## [`v1.20`] — 2026-10-07 — 🆕 Wireless Headset Battery via Native USB HID (`src/HIDListener.py`)
### 🆕 New Major Module (`+0.10`)
- **Direct USB HID Headset Battery Readout:**
  - Extended [`src/HIDListener.py`](src/HIDListener.py) to actively poll the SteelSeries USB wireless transceiver via HID reports (`[0x06, 0xb0]`) to read real-time battery percentage on wireless headsets (including **SteelSeries Arctis Nova 7** and compatible HyperX / Cloud III models).
- **Vertical 4-Segment Battery Icon on Clock (`src/Timer.py`):**
  - Renders a vertical 4-bar battery indicator in the top-right corner of the clock display (`x = 110..127`) with an optional numeric percentage (`0%`–`100%`) centered below the icon.
- **Headset Power-Off / Out-of-Range Detection:**
  - Detects when the wireless headset is turned off or disconnected via HID status bytes and immediately hides the battery indicator so stale battery levels are never shown.

---

## [`v1.10`] — 2026-10-07 — 🆕 Automatic Hardware Monitor Switching in 3D Games
### 🆕 New Major Module (`+0.10`)
- **Automatic "System Stats in Games" Mode (`auto_hw_monitor_game`):**
  - Added automatic switching from the clock to the **CPU / GPU / RAM / FPS** Hardware Monitor in [`src/HardwareMonitor.py`](src/HardwareMonitor.py), [`src/DisplayManager.py`](src/DisplayManager.py), and [`src/SettingsWindow.py`](src/SettingsWindow.py) whenever PresentMon detects an active 3D game rendering frames.
  - Automatically returns to the clock or media player once the game closes.

---

## [`v1.00`] — Project Foundation — Original OLED Customizer (`0z-zy` / `Firewee`)
### 📦 Core Features Preserved from the Original Application
- **Dynamic Media Dashboard (Spotify Web API + Windows SMTC):**
  - Displays track title, artist, progress bar, duration, and source icons (Spotify, YouTube, generic Media) across 5 player styles (*Standard*, *Compact*, *Centered*, *Ticker*, *Minimal*).
- **Ghost-Monitor (`src/HardwareMonitor.py`, `src/fps_monitor.py`):**
  - Live CPU/GPU load and temperature monitoring (LibreHardwareMonitor + WMI fallback), RAM usage, and live game FPS counter (PresentMon) via the `Insert` hotkey or persistent mode.
- **Volume Control & Discord Integration (`src/volume.py`, `src/DiscordRPC.py`):**
  - Visual volume bar overlay on the OLED screen, Discord microphone mute synchronization over local IPC, and global microphone mute hotkeys (keyboard keys, `Mouse 4` / `Mouse 5` side buttons, and physical headset mute button).
- **Built-In OLED Calculator (`src/Calculator.py`):**
  - Interactive calculator launched with `Ctrl + Insert`, capturing Numpad keys via a low-level `WH_KEYBOARD_LL` hook so keystrokes don't leak into active games.
- **4 Clock Styles (`src/Timer.py`):**
  - *Standard*, *Big Timer*, *Date Focused*, and *Analog* clock themes with 12h/24h format and seconds toggle.
- **SteelSeries GG Profile Backups (`src/ProfileBackup.py`):**
  - One-click backup, restore, and `VACUUM` compaction for SteelSeries `engine`, `prism`, and `sonar` SQLite databases.
