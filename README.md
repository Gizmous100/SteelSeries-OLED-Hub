# 🎹 SteelSeries OLED & RGB Hub

**The ultimate music, hardware, weather, headset battery, and RGB ambiance dashboard for your SteelSeries Keyboard & Peripherals.**

![Version](https://img.shields.io/badge/version-1.46-blue?style=for-the-badge)
![Author](https://img.shields.io/badge/author-gizmous-orange?style=for-the-badge)
![Inspired By](https://img.shields.io/badge/inspired_by-OLED_Customizer_(0z--zy)-lightgrey?style=for-the-badge)
![License](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey?style=for-the-badge)

**SteelSeries OLED & RGB Hub** transforms your keyboard's 128×40 OLED screen and SteelSeries RGB peripherals into a unified, high-contrast information and lighting hub. Whether you're listening to Spotify or YouTube, watching Prime Video, tracking CPU/GPU temperatures and live FPS in-game, checking your wireless headset battery, or viewing local weather and European Air Quality (`AQI`), SteelSeries OLED & RGB Hub delivers it with pixel-perfect clarity and zero flicker.

> [!NOTE]
> ### 💡 Inspiration & Project Heritage
> **SteelSeries OLED & RGB Hub (`v1.46`)** is **heavily inspired by the original [OLED Customizer](https://github.com/0z-zy/OLED_Customizer) created by [0z-zy](https://github.com/0z-zy)** (and the foundational [SteelSeries-Spotify-Linker](https://github.com/Firewee/SteelSeries-Spotify-Linker) by **[Firewee](https://github.com/Firewee)**).  
> It **retains and includes all features of the original application** (Spotify & Windows SMTC media dashboard, CPU/GPU/RAM/FPS Ghost-Monitor, adaptive volume control, Discord RPC & global mic mute, OLED calculator, 4 clock themes, and SteelSeries profile backups), while being significantly expanded by **[gizmous](https://github.com/gizmous)** with brand-new wireless headset battery telemetry, live weather & European AQI, smart AFK power-saving with gamepad detection, 10-zone mouse RGB & profile sync, and anti-jitter adaptive rendering.

---

## 📋 Table of Contents

- [🆕 Major New Features & Expansions (by gizmous)](#-major-new-features--expansions-by-gizmous)
  - [1. 🎧 Wireless Headset Battery & Native USB HID Telemetry](#1--wireless-headset-battery--native-usb-hid-telemetry)
  - [2. 🌦️ Live Weather & European Air Quality Index (AQI)](#2-️-live-weather--european-air-quality-index-aqi)
  - [3. 💤 Smart Idle / AFK Mode with XInput Gamepad Detection](#3--smart-idle--afk-mode-with-xinput-gamepad-detection)
  - [4. 🎮 Automatic Hardware Monitor Switching in Games](#4--automatic-hardware-monitor-switching-in-games)
  - [5. 🌈 10-Zone Mouse RGB & SteelSeries GG Profile Sync](#5--10-zone-mouse-rgb--steelseries-gg-profile-sync)
  - [6. 📐 Adaptive Clock Layouts & Anti-Jitter Digits](#6--adaptive-clock-layouts--anti-jitter-digits)
  - [7. 🎬 Streaming & Video Playback Hysteresis (Prime Video / Netflix)](#7--streaming--video-playback-hysteresis-prime-video--netflix)
  - [8. 🛡️ Fault-Tolerant Architecture & Stability Hardening](#8-️-fault-tolerant-architecture--stability-hardening)
- [📈 Version History (`v1.00` → `v1.46`)](#-version-history-v100--v146)
- [🚀 Complete Feature Overview](#-complete-feature-overview)
  - [🎵 Dynamic Media Dashboard](#-dynamic-media-dashboard)
  - [🌡️ Ghost-Monitor (Hardware Stats & FPS)](#️-ghost-monitor-hardware-stats--fps)
  - [🔊 Reimagined Volume Control & Discord Sync](#-reimagined-volume-control--discord-sync)
  - [🖩 Built-In OLED Calculator](#-built-in-oled-calculator)
  - [💾 SteelSeries Profile Backup & Database Compaction](#-steelseries-profile-backup--database-compaction)
- [⌨️ Global Hotkeys](#️-global-hotkeys)
- [🕰️ Clock Gallery](#-clock-gallery)
- [🛠️ Quick Setup & Building from Source](#️-quick-setup--building-from-source)
- [🎵 Spotify API Setup](#-spotify-api-setup)
- [📦 Repository Structure](#-repository-structure)
- [📚 Acknowledgments & Credits](#-acknowledgments--credits)

---

## 🆕 Major New Features & Expansions (by gizmous)

Compared to the original OLED Customizer, **SteelSeries OLED & RGB Hub (`v1.46`)** introduces **4 major new functional modules (`+0.40`)** and **6 core architectural & UX upgrades (`+0.06`)**:

### 1. 🎧 Wireless Headset Battery & Native USB HID Telemetry
- **Direct USB HID Battery Readout**: Communicates directly with SteelSeries USB wireless transceivers via native Win32 HID reports (`[0x06, 0xb0]`) to read real-time battery levels for **SteelSeries Arctis Nova 7** (as well as compatible Cloud III / HyperX wireless headsets) without relying on heavy background services.
- **Vertical 4-Segment Battery Indicator**: Renders a crisp vertical 4-bar battery icon in the top-right corner of the clock display, with an optional numeric percentage (`0%`–`100%`) centered directly underneath.
- **Smart Power-Off & Range Detection**: Automatically detects when the wireless headset is powered off or out of range and hides the battery widget immediately so stale battery values are never displayed.

### 2. 🌦️ Live Weather & European Air Quality Index (`AQI`)
- **Zero-API-Key Weather Engine**: Powered by Open-Meteo Geocoding, Weather Forecast, and Air Quality APIs — simply type your city name in **Settings → Display → Weather Widget** and save.
- **Clock Screen Weather Mini-Widget**: Displays a custom pixel-art weather condition icon (clear sky, clouds, drizzle/rain, snow, thunderstorm), live temperature (`°C`), and **European Air Quality Index (`AQI`)** on the left side of the clock screen.
- **Full-Screen Weather Overlay (`F8`)**: Press the Weather hotkey (default **`F8`**) at any time—even inside fullscreen games—to summon a dedicated full-screen weather card showing your city name, weather description, temperature, and live `AQI` for a configurable duration (default: 5 seconds).

### 3. 💤 Smart Idle / AFK Mode with XInput Gamepad Detection
- **OLED Burn-In Protection & RGB Blackout**: Automatically blanks the OLED display and turns off both keyboard and mouse RGB lighting after a configurable period of user inactivity (default: 5 minutes).
- **XInput Controller / Gamepad Awareness**: Continuously polls up to 4 connected XInput controllers (`XInputGetState`). When you are playing a game with a controller and not touching the keyboard or mouse, the app automatically resets the Windows idle timer (`VK_F15`) so your OLED screen and RGB lighting never turn off mid-game.
- **Instant Wake**: Restores the OLED display and your custom RGB color the instant keyboard, mouse, or controller input resumes.

### 4. 🎮 Automatic Hardware Monitor Switching in Games
- **Smart 3D Game Detection**: Enable *"Auto-switch to System Stats in Games"* in Settings to automatically switch the OLED screen from the clock to the live **CPU / GPU / RAM / FPS Hardware Monitor** whenever a 3D game is launched (detected via PresentMon telemetry).
- **Seamless Return**: Automatically switches back to your clock layout as soon as you exit the game.

### 5. 🌈 10-Zone Mouse RGB & SteelSeries GG Profile Sync
- **Full 10-Zone Mouse RGB Support (`rgb-zoned-device`)**: Extends GameSense lighting control beyond keyboards (`rgb-per-key-zones`) and basic 2-zone mice (`wheel`, `logo`) to support all **10 independent RGB zones (`one` through `ten`)** on modern multi-zone SteelSeries mice such as the **Rival 5**. Eliminates the issue where multi-zone mice remained stuck on factory SteelSeries Orange.
- **Live SteelSeries GG Config Watcher**: Monitors the SteelSeries Engine database (`database.db`) in the background so switching hardware configs/profiles in SteelSeries GG immediately re-applies your chosen RGB color.
- **Transition-Only Lighting Dispatch**: Sends GameSense `LIGHTING` events strictly on state transitions (startup, AFK entry/exit, color change, or GG config switch) rather than inside the 10 FPS render loop—preventing HTTP 429 rate-limiting and mouse profile lockups.

### 6. 📐 Adaptive Clock Layouts & Anti-Jitter Digits
- **Dynamic Horizontal Layout Fitting**: All 4 clock themes (*Standard*, *Big Timer*, *Date Focused*, and *Analog*) dynamically compute available horizontal space `[x_left .. x_right]` and automatically adjust font sizes and centering when the Weather widget (`38px` left) and/or Headset Battery widget (`19px`/`12px` right) are active. Tested across all 128 widget/format combinations with zero pixel overlap.
- **Fixed-Width Anti-Jitter LCD Digits (`_draw_fixed_width_text`)**: Custom per-character cell rendering keeps the clock completely stationary when narrow digits (`1`, `4`) appear in the seconds counter.
- **Compact Polish Day Abbreviations & Short Date**: Optional compact Polish day names (`P`, `W`, `Sr`, `C`, `Pt`, `S`, `N`) and `dd/mm/yy` date formatting designed to fit alongside side widgets.

### 7. 🎬 Streaming & Video Playback Hysteresis (Prime Video / Netflix)
- **2.5-Second Playback Grace Window**: Absorbs brief `Paused`/`Stopped` status blips from Windows UWP and browser video players (such as Amazon Prime Video, Netflix, and YouTube) so the OLED display never flickers between the media player and the clock.
- **Automatic Conflict Guard (`now-playing.exe`)**: Automatically suspends SteelSeries GG's conflicting built-in `now-playing.exe` applet while running (and resumes it on exit) to prevent competing OLED frame updates.

### 8. 🛡️ Fault-Tolerant Architecture & Stability Hardening
- **Bundled `sqlite3.dll` with Fallback Loader**: Ensures SteelSeries GG profile backups and database compaction (`VACUUM`) work reliably on any Windows installation.
- **Single-Instance & Memory Hardening**: Automatically cleans up orphaned background processes on startup, eliminates GDI/PIL image handle leaks in the 10 FPS loop, and fixes reopening the Settings GUI after saving.

---

## 📈 Version History (`v1.00` → `v1.46`)

| Version | Category | Summary of Changes |
| :--- | :---: | :--- |
| **`v1.00`** | Base | Original **OLED Customizer** foundation by **0z-zy** (inspired by **Firewee**). |
| **`v1.10`** | 🆕 Major (`+0.10`) | **Auto-Switch to Hardware Monitor in Games** via PresentMon FPS detection. |
| **`v1.20`** | 🆕 Major (`+0.10`) | **Wireless Headset Battery via USB HID** (`[0x06, 0xb0]`) + vertical 4-bar icon & power-off detection. |
| **`v1.30`** | 🆕 Major (`+0.10`) | **Live Weather & European AQI Module** (clock mini-widget + full-screen `F8` overlay). |
| **`v1.40`** | 🆕 Major (`+0.10`) | **Smart Idle / AFK Mode** (OLED & RGB blackout + XInput controller activity detection). |
| **`v1.41`** | ✨ Fix (`+0.01`) | **Compact Polish Day Names & Short Year** (`dd/mm/yy`) for side-widget compatibility. |
| **`v1.42`** | ✨ Fix (`+0.01`) | **Bundled `sqlite3.dll` Fault Tolerance** & thread-safe Settings window re-entry fix. |
| **`v1.43`** | ✨ Fix (`+0.01`) | **Fixed-Width Anti-Jitter Digits & Adaptive Clock Themes** across all 4 clock styles. |
| **`v1.44`** | ✨ Fix (`+0.01`) | **Memory, Threading & Single-Instance Hardening** (orphan process cleanup & PIL handle fixes). |
| **`v1.45`** | ✨ Fix (`+0.01`) | **SMTC / Prime Video Playback Hysteresis** (2.5s grace period & `now-playing.exe` suspension). |
| **`v1.46`** | ✨ Fix (`+0.01`) | **10-Zone Mouse RGB (`rgb-zoned-device`) & SteelSeries GG Config Switch Sync**. |

---

## 🚀 Complete Feature Overview

### 🎵 Dynamic Media Dashboard
- **Spotify, YouTube & Streaming Support**: Real-time integration with Spotify (Web API), Windows media (SMTC), and browser playback.
- **5 Player Styles**: Choose between *Standard*, *Compact*, *Centered*, *Ticker*, and *Minimal* layouts.
- **Pixel-Perfect Icons**: Custom YouTube, Spotify, and Media icons crafted for the 128×40 OLED display.

| Spotify | YouTube | Generic Media |
| :---: | :---: | :---: |
| ![Spotify](content/demos/demo_player.gif) | ![YouTube](content/demos/demo_player_youtube.gif) | ![Generic](content/demos/demo_player_generic.gif) |

### 🌡️ Ghost-Monitor (Hardware Stats & FPS)
- **Instant Peek (`Insert`)**: Tap **Insert** to summon CPU/GPU/RAM/FPS stats over any screen, toggle **Always Show System Stats**, or enable **Auto-Switch in Games**.
- **Thermal Guard & Memory Tracking**: High-accuracy CPU & GPU usage/temperature monitoring via LibreHardwareMonitor (with WMI fallback) plus live RAM usage.
- **🎮 Live Game FPS**: Real-time FPS counter powered by PresentMon (requires Administrator privileges).

![HW Monitor Demo](content/demos/demo_hw_monitor.gif)

### 🔊 Reimagined Volume Control & Discord Sync
- **Adaptive Volume Overlay**: Visual volume bar and dynamic speaker icons (mute, low, mid, high).
- **Discord RPC Sync & Global Mute**: Connects to Discord via local IPC to display microphone status on the clock and toggle mute globally via keyboard keys or mouse side buttons (`Mouse 4` / `Mouse 5`), plus physical headset mute button sync.

![Volume Demo](content/demos/demo_volume.gif)

### 🖩 Built-In OLED Calculator
- **Quick Access (`Ctrl + Insert`)**: Open an interactive calculator directly on your keyboard's OLED screen. Captures numpad input so games don't receive stray keystrokes.

### 💾 SteelSeries Profile Backup & Database Compaction
- **One-Click Backups & Safety Restore**: Backup and restore your SteelSeries GG `engine`, `prism`, and `sonar` SQLite databases from the System Tray or **Settings → Backups**.
- **Compact Databases (`VACUUM`)**: Reclaim disk space from bloated SteelSeries databases.

---

## ⌨️ Global Hotkeys

| Action | Default Key | Notes |
| :--- | :--- | :--- |
| **Show System Stats** | `Insert` | Quick peek at CPU / GPU / RAM / FPS |
| **Show Weather Overlay** *(🆕 NEW)* | `F8` | Summons full-screen city weather & European AQI |
| **Mute Microphone (Primary)** | `Pause` | Supports keyboard keys & mouse buttons (`M4` / `M5`) |
| **Mute Microphone (Optional)** | *Not set* | Bind a second mute key in Settings |
| **Toggle Calculator** | `Ctrl + Insert` | Numpad input, `Esc` to close |

---

## 🕰️ Clock Gallery

| Style | Preview |
| :--- | :---: |
| **Standard** | ![Standard](content/demos/demo_clock.gif) |
| **Analog** | ![Analog](content/demos/demo_clock_analog.gif) |
| **Big Timer** | ![Big](content/demos/demo_clock_big.gif) |
| **Date Focused** | ![Date](content/demos/demo_clock_date.gif) |

---

## 🛠️ Quick Setup & Building from Source

> [!IMPORTANT]
> **Administrator Privileges**: Run the application as Administrator to enable hardware temperature sensors (LibreHardwareMonitor) and live game FPS (PresentMon). Enabling **"Run on Start"** automatically creates an elevated Windows Task Scheduler task.

### 1. Standalone Executable
1. Download **`SteelSeries-OLED-Hub.exe`** from [Releases](https://github.com/gizmous/SteelSeries_OLED_Hub/releases).
2. Launch `SteelSeries-OLED-Hub.exe` — the app will appear in your System Tray.
3. Right-click the tray icon and select **⚙️ Settings...** to customize your display, weather city, headset battery, hotkeys, and RGB color.

### 2. Running or Building from Source
```powershell
git clone https://github.com/gizmous/SteelSeries_OLED_Hub.git
cd SteelSeries_OLED_Hub
pip install -r requirements.txt

# Run from source:
python main.py

# Build standalone SteelSeries-OLED-Hub.exe:
python -m PyInstaller SteelSeries-OLED-Hub.spec --noconfirm
```

---

## 🎵 Spotify API Setup

Windows SMTC media detection works out of the box with zero configuration. If you also want direct Spotify Web API polling:
1. Visit the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and click **"Create app"**.
2. Set **Redirect URI** to `http://127.0.0.1:2408/callback` and select **"Web API"**.
3. Copy your **Client ID** and **Client Secret** into **Settings → Spotify**, enable **Spotify Integration**, and click **SAVE**.

---

## 📦 Repository Structure

```text
SteelSeries_OLED_Hub/
├── .gitignore                   # Git ignore rules for build/dist/cache files
├── LICENSE                      # MIT License (gizmous & original copyright 0z-zy / Firewee)
├── README.md                    # Project documentation
├── main.py                      # Application entry point & single-instance guard
├── version.py                   # Version (1.46) and author metadata
├── version_info.py              # Windows executable version metadata
├── SteelSeries-OLED-Hub.spec    # PyInstaller build specification
├── requirements.txt             # Python dependencies
├── sqlite3.dll                  # Bundled SQLite library for SteelSeries GG profile backups
├── content/                     # Icons, TrueType OLED fonts, documentation images & demo GIFs
└── src/                         # Application modules (DisplayManager, SteelSeriesAPI, Weather,
                                 # afk_monitor, HIDListener, Timer, WindowsMedia, HardwareMonitor,
                                 # fps_monitor, volume, Calculator, DiscordRPC, ProfileBackup, SettingsWindow, etc.)
```

---

## 📚 Acknowledgments & Credits

**SteelSeries OLED & RGB Hub (`v1.46`)** stands on the shoulders of great open-source projects and **includes all features of the original application** alongside major new expansions:

- **Author & Maintainer (`v1.46`)**: **[gizmous](https://github.com/gizmous)** — author of the `v1.10`–`v1.46` expansions (Wireless Headset Battery HID, Live Weather & European AQI, Smart AFK + XInput Gamepad detection, Auto-Game Hardware Monitor, 10-Zone Mouse RGB & GG Config Sync, SMTC/Prime Video Hysteresis, and Adaptive Anti-Jitter Clock Layouts).
- **Original Application & Inspiration**: **[0z-zy](https://github.com/0z-zy)** — creator of the original [OLED Customizer](https://github.com/0z-zy/OLED_Customizer), which served as the direct inspiration and base for this project. All original features created by **0z-zy** are fully preserved in this edition.
- **Foundational Inspiration**: **[Firewee](https://github.com/Firewee)** — creator of [SteelSeries-Spotify-Linker](https://github.com/Firewee/SteelSeries-Spotify-Linker), whose core OLED & Spotify integration inspired the original project.

---

**Developed and Expanded by [gizmous](https://github.com/gizmous) (`v1.46`) • Includes all original features & heavily inspired by [OLED Customizer](https://github.com/0z-zy/OLED_Customizer) by [0z-zy](https://github.com/0z-zy) & [Firewee](https://github.com/Firewee).**
