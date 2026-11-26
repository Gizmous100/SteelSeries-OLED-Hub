# 📜 Changelog — SteelSeries OLED & RGB Hub

Complete version history for **SteelSeries OLED & RGB Hub**, developed and expanded by **[gizmous](https://github.com/Gizmous100)** on top of the original **[OLED Customizer](https://github.com/0z-zy/OLED_Customizer)** created by **[0z-zy](https://github.com/0z-zy)** (inspired by **[SteelSeries-Spotify-Linker](https://github.com/Firewee/SteelSeries-Spotify-Linker)** by **[Firewee](https://github.com/Firewee)**).

**Project Versioning Policy:**
- **`+0.10` (`Major`)** — brand-new major functional modules (e.g., `v1.10`, `v1.20`, `v1.30`, `v1.40`, `v1.50`).
- **`+0.01` (`Fix / Feature Upgrade`)** — bug fixes, architectural optimizations, and enhancements to existing modules (e.g., `v1.41` – `v1.46`).

---

## [`v1.54`] — 2026-11-26 — 🎨 Per-Profile RGB Sync, In-Game FPS View & Full Code-Review Hardening
### ✨ Fix / Feature Upgrade (`+0.01`)
- **Per-profile RGB colour sync (`src/DisplayManager.py`, `src/UserPreferences.py`, `src/SteelSeriesAPI.py`):**
  - Added `profile_colors` preference (map of profile name → `[R, G, B]`) and `profile_lighting_enabled` master toggle.
  - On every SteelSeries / GG profile switch, the hub now applies the target profile's colour **only when it differs from the currently applied colour** — if profiles share the same colour set, no GameSense LIGHTING event is sent, so the keyboard/mouse/RGB backlight no longer blinks on every switch.
  - Only overrides SteelSeries GG native lighting when a profile has an explicit entry in `profile_colors` or `rgb_enabled` is `True`; otherwise keeps/releases control to SteelSeries GG (including when switching back to onboard profile slots).
  - AFK blackout always takes priority: during AFK the backlight stays off regardless of profile changes; the profile colour applies on wake.
  - Colour changes still go through `SteelSeriesAPI.set_rgb_color()`, which emits LIGHTING events exclusively on real transitions (never in the 10 FPS render loop), preventing HTTP 429 and mouse profile lock-ups.
  - Saving settings no longer reverts the active profile's colour to the global `rgb_color` — `update_preferences()` resolves the colour for the currently active profile.
  - The colour of the loadout already active when the hub starts is now applied immediately (previously only after the first profile switch).
  - All lighting I/O now runs on a single **Lighting-Worker** thread in `SteelSeriesAPI`: `set_lighting()` / `set_rgb_color()` only record the desired state (AFK blackout, user / profile colour, or GG native) and return immediately, and the worker applies the newest state. A profile switch racing AFK entry can no longer relight the keyboard/mouse during AFK, the OLED render loop never waits on lighting HTTP calls, and a profile switch during AFK stores the colour and applies it on wake.
  - A lighting transition that fails to send (e.g. GG busy deploying a profile) is **retried** with back-off (2 s → 5 s → 10 s) until GameSense confirms it; previously the colour was recorded as applied before sending and never retried.
  - `AFK_GAME` is now kept alive with heartbeats whenever it drives the lighting (AFK blackout **or** a user / profile colour). It has a 60 s deinitialize timer and lighting events are sent only on transitions, so colours could revert to GG lighting after about a minute.
  - Re-binding the LIGHTING handler right before an event no longer sends `/stop_game` in between, which briefly flashed the native GG lighting on every colour change and on AFK entry.
  - Switching from a coloured profile to one without a colour hands lighting back to GG immediately but **defers the native-config redeploy by 2 s**, so it no longer force-deploys the outgoing profile's config while GG is still deploying the new one.
  - An explicit black profile colour (`[0, 0, 0]` in `profile_colors`) now turns the backlight off for that profile instead of handing lighting back to GG.
  - Per-profile colours keep working when *Show Profile Name on OLED when Changed* is turned off: the profile monitor keeps polling (every 1 s) while colours are configured and only skips the overlay and its instant frame.
  - Switching the keyboard back to a Desktop-loadout config now looks its colour up under the Desktop loadout name (previously the Engine config name was used, so the Desktop colour was ignored).
  - `_active_profile_name` is initialised before the Profile-Monitor thread starts, so the colour of the loadout active at startup can no longer be reverted to the global colour by a startup race.
  - The profile monitor polls every 350 ms (previously 80 ms) to avoid hammering GG's local server while it deploys a loadout.
  - With RGB disabled, lighting is reliably handed back to SteelSeries GG at startup (the bound `AFK_GAME` handler is stopped and native configs redeployed once), while repeated colour changes with RGB disabled no longer trigger redundant releases.
  - The instant OLED overlay frame on a software preconfig switch is now sent **before** the colour change is queued, so it reliably preempts GG's default OLED background.
- **Tray menu reflects Settings (`src/Systray.py`, `src/DisplayManager.py`):**
  - The tray menu checkmarks (e.g. *Player → Show Player*, *Clock → Show Clock*) were built before preferences were loaded and never refreshed after saving Settings, so a disabled *Enable Music Info* still showed as checked. `update_preferences()` now rebuilds the tray menu when a value it shows changed, so it always matches the saved settings. Tray toggles record the state they rebuilt, so the hot reload that follows does not rebuild (and close) a menu the user just reopened.
- **Clock disabled no longer freezes the OLED (`src/DisplayManager.py`, `src/Timer.py`, `src/Weather.py`):**
  - With the clock turned off, the render loop produced no frame and the OLED stayed frozen on the last image.
  - The screen now uses exactly the same layout as the clock (`Timer.get_layout_image()`): headset battery on the right while a headset is connected, Caps / Num / Scroll Lock badges at the bottom and mute/deafen icons top-left (each when enabled in Settings). Only the clock face is replaced — by the weather centred in the free area (`WeatherModule.draw_centered()`: icon + temperature, description / AQI below) when weather is enabled, or left empty otherwise.
- **GG reconnect after a restart (`src/SteelSeriesAPI.py`):**
  - `reset()` held the request lock while re-registering, so every registration request timed out on that lock: the OLED froze for about 10 s and the hub was never re-registered after a SteelSeries GG restart. Re-registration now runs without that lock (overlapping resets are still prevented by a separate guard).
- **Preferences defaults (`src/UserPreferences.py`):**
  - Missing preferences are filled with **copies** of the defaults, so mutable defaults such as `profile_colors` are never shared with the class-level `DEFAULT` dict.
- **Full code review fixes — safety & security:**
  - **Startup never kills an unrelated process (`main.py`):** a stale `.lock` file (e.g. after a reboot) could hold a PID Windows had reused; the elevated hub terminated whatever process owned it. Lock-file PIDs are now killed only when the process really is a hub instance.
  - **Two launches can no longer kill each other (`main.py`):** the "find old instances → terminate → write lock" phase is serialised with a named mutex, and each instance writes its own temporary lock file.
  - **GG is no longer killed while it starts (`src/DisplayManager.py`):** the "API unresponsive for 180 s → restart GG" recovery counted the time GG was not running at all, so it killed GG right after it appeared again. The silence timer is now reset while GG is absent and when it is detected.
  - **Updater (`src/updater.py`):** the version, the download and the SHA-256 now all come from the same GitHub release (the version used to be read from `main`, which could loop on or downgrade to the latest release); the update is aborted when the release is not newer or has no published SHA-256 digest (it used to install, and run elevated, an unverified binary); the PowerShell `Unblock-File` call that interpolated the install path into a command string was removed (the Zone.Identifier stream is still deleted directly).
  - **Local receiver DNS-rebinding guard (`src/ExtensionReceiver.py`):** requests are served only when the `Host` header is a loopback name (`127.0.0.1`, `localhost`, `[::1]`); `Access-Control-Allow-Private-Network` is granted only to allowed origins; an out-of-range port no longer kills the receiver thread (Settings also limits ports to 1–65535).
  - **Spotify OAuth CSRF protection (`src/SpotifyAPI.py`):** the authorization request carries a random `state` that the localhost callback must return; other requests get `400` instead of "Authentication Successful". A Spotify `401` is retried once after a token refresh instead of recursing without limit.
- **Full code review fixes — keyboard, mouse & calculator (`src/DisplayManager.py`, `src/Calculator.py`, `src/Systray.py`, `src/UserPreferences.py`):**
  - Calculator mode no longer swallows arrows, Alt, Win, F-keys and media/volume keys system-wide (an empty character matched every digit check), and the calculator ignores empty input.
  - The default calculator toggle is now **Ctrl + F12** — Ctrl + Insert is the standard Copy shortcut and turned calculator mode on by accident. Existing configs using the old default are migrated once.
  - Closing the calculator with Esc / the shortcut no longer leaves the clock disabled: the tray toggle stopped turning the clock and System Stats off (the calculator already takes priority on screen).
  - Holding a hotkey no longer fires it repeatedly on key auto-repeat (e.g. mute toggling several times).
  - Ctrl / Shift state comes from the OS (`GetAsyncKeyState`): after Ctrl+Alt+Del the hook-tracked Ctrl could stay "held", so Insert toggled the calculator.
  - Mouse 4/5 hotkeys also swallow the matching button-up, so the focused app no longer navigates Back/Forward.
- **Full code review fixes — reliability:**
  - **Weather (`src/Weather.py`):** a failed fetch (e.g. network not ready at logon) is retried after 60 s instead of showing "Loading..." for 30 minutes; the user's city query is kept separate from the geocoded name, so a preference reload no longer wipes and refetches the weather.
  - **Volume overlay (`src/volume.py`):** no `TypeError` (and frozen OLED) when no speaker interface exists; the speaker is re-initialised every 5 s until it is available.
  - **AFK (`src/afk_logic.py`, `src/DisplayManager.py`):** when AFK settled without a cursor baseline (locked workstation, cursor at 0,0) input is still detected and a baseline is latched, so moving the mouse wakes the hub again.
  - **Frame failures (`src/DisplayManager.py`):** `send_frame()` returning `False` now counts as a failure, so the "frames failing for 30 s → re-register" recovery actually runs.
  - **Spotify toggle (`src/DisplayManager.py`):** enabling / disabling *Spotify Integration* takes effect immediately instead of after a restart.
  - **Discord (`src/DiscordRPC.py`):** the stored token is cleared only when Discord explicitly rejects it (timeouts and pipe errors are retried after 10 s), and the refresh token is stored (DPAPI-protected) and used when the ~7-day access token expires, so mute sync no longer silently stops every week.
  - **Headset HID (`src/HIDListener.py`):** the listener thread no longer dies when the dongle is unplugged (`device_path` is read only under its lock).
  - **Profile backups (`src/ProfileBackup.py`):** a raw-copy backup folds its `-wal` sidecar into the database when possible, and restore also restores a backup's `-wal`, so the newest committed profile changes are not lost.
  - **Exit (`src/DisplayManager.py`):** the suspended built-in `now-playing.exe` is resumed before the (possibly slow) GameSense cleanup, so the exit watchdog can no longer leave it frozen.
- **Optional native components & English comments (`src/volume.py`, `src/HIDListener.py`, `src/DisplayManager.py`, `src/SpotifyPlayer.py`, `src/Systray.py`):** `pythoncom`, `pycaw` / `comtypes` and `hid.dll` are optional — when missing, the volume overlay / headset sync are disabled instead of blocking startup (AGENTS.md §2). The remaining Turkish code comments were translated to English (AGENTS.md §3).
- **Clock freeze when Music Info is disabled (`src/DisplayManager.py`):**
  - Fixed `_run_once()` switching `self.state` to `SHOW_PLAYER` while `display_player` (Music Info) is `False` and media is playing — which previously left `frame_data` as `None` and froze the clock until media stopped. Now `self.state` stays `SHOW_CLOCK` whenever `display_player` is disabled.
- **In-game FPS view & System Stats display rules (`src/DisplayManager.py`, `src/Timer.py`, `src/HardwareMonitor.py`, `src/SettingsWindow.py`):**
  - With *Show Game FPS* on, the FPS now replaces the **clock face only while a game is running** (fullscreen / borderless foreground game that keeps rendering frames for 2 s — a maximised document, a slideshow or a remote desktop no longer counts; media players, Office apps, remote desktops and VMs were added to `_NON_GAME_PROCESSES`). During a game the FPS view also wins over the music player. It shows a big value styled like the clock with an `FPS` caption, while the weather widget, headset battery, lock-key badges and mute icons stay exactly as on the clock (`Timer.get_layout_image()` + `Timer.draw_value_face()`). Outside games the screen shows whatever is enabled: clock, centred weather or an empty face. Without admin rights the caption reads `NO ADMIN`.
  - *Always Show System Stats* shows the stats all the time, in games or not. *Auto-switch to System Stats in Games* now limits the stats to gaming sessions — also when *Always Show System Stats* is on — and outside games the clock / weather / empty screen returns (`DisplayManager._system_stats_active()`).
  - With auto-switch on, the in-game screen is System Stats with the FPS in its right column; the big FPS view is used when auto-switch is off.
  - *Enable Clock* and *Always Show System Stats* can now be on together **only with Auto-switch enabled** (stats while gaming, clock outside games). Without Auto-switch they stay mutually exclusive, and turning Auto-switch off while both are on turns the clock off. The rule is applied in Settings (`_clock_stats_toggle()`, `_on_auto_switch_toggle()`, normalised again on save) and in the tray menu (`src/Systray.py`). Settings groups *Always Show System Stats* with *Auto-switch* and explains the rule in a note below them.
  - The FPS capture (PresentMon ETW) now runs only when *Show Game FPS* is enabled — game detection for auto-switch is window based and never needed it. The Settings label was corrected to *Auto-switch to System Stats in Games (only while gaming)*; the unused `HardwareMonitor.is_game_running()` was removed.
- **Stable game FPS counter (`src/fps_monitor.py`, `src/HardwareMonitor.py`):**
  - The FPS value jumped wildly (e.g. 100 → 2000 → 100). PresentMon was recording **all processes** into one untagged buffer and the hub showed only the newest sample, which is the *instantaneous* FPS of a single present (1000 / ms since the previous present) from whichever app presented last.
  - The monitor now records only the **foreground application** (`HardwareMonitor.get_foreground_app_pid()`, skipping the desktop, the hub and known non-game apps from the shared `_NON_GAME_PROCESSES` list) and restarts the PresentMon session when the foreground app changes (debounced, so a quick Alt+Tab does not restart it).
  - The displayed value is the **average over the last second** (frames / total frame time), like in-game FPS counters; invalid samples (0, infinity) are ignored, and the slot falls back to `Idle` when the app has not presented a frame for 1.5 s.
  - ctypes signatures now match the DLL's 32-bit `int` exports.
  - The big FPS view and the System Stats column share one value (`HardwareMonitor.get_effective_fps()`: PresentMon first, then LibreHardwareMonitor's *Frame Rate* sensor), so they can no longer disagree.
- **Game FPS shows why it cannot measure (`src/fps_monitor.py`, `src/HardwareMonitor.py`):**
  - PresentMon only works in an elevated process; without administrator rights it fails with error `1007` and the FPS slot showed a misleading `Idle` even during a game. The slot now shows `Admin` when the hub is not running as administrator, `N/A` when the PresentMon DLL is missing or fails, and `Idle` only when the capture is running and no game is rendering.
  - The privilege failure is logged once as a warning explaining how to fix it (run the hub elevated, e.g. via the *Run at Startup* scheduled task) instead of an error on every Settings save.
  - Settings → Display: the toggle is now labelled *Show Game FPS (requires Administrator)* and a status line below it shows whether the hub is currently running as administrator; if not, it explains that the FPS slot will show `Admin` and how to fix it (`src/SettingsWindow.py`).
  - Fixed `FPSMonitor.start()` storing `None` as the worker thread (`Thread(...).start()` return value), so `stop()` now actually waits for the worker to exit.

---

## [`v1.53`] — 2026-11-09 — 💡 AFK Lighting Sync Fix
### ✨ Fix / Feature Upgrade (`+0.01`)
- **AFK Lighting Synchronization (`src/DisplayManager.py`):**
  - Removed 3-second throttling when entering AFK mode so lighting always turns black immediately with the OLED screen.
  - Added periodic lighting/AFK consistency check every 30 seconds in the main render loop — if a previous `set_lighting()` call silently failed (e.g. GG temporarily unresponsive), lighting is re-synced to match AFK state.
  - Fixed user report: lighting staying on while OLED is off during AFK mode.

---

## [`v1.52`] — 2026-11-08 — 🔒 Stability Hardening, Optional Dependency Guards & AFK Logic Merge
### ✨ Fix / Feature Upgrade (`+0.01`)
- **Optional Dependency Guards (`src/WindowsMedia.py`, `src/DiscordRPC.py`):**
  - Wrapped `winrt.windows.media.control` and `pywin32` (`win32pipe`, `msvcrt`) imports in `try/except ImportError` so the application starts even when these optional packages are missing, per AGENTS.md fault-tolerance rule.
- **Bounded GameSense Address Retrieval (`src/SteelSeriesAPI.py`):**
  - `retrieve_address(retries=None)` now defaults to a bounded 10 attempts (~50s) instead of looping forever when SteelSeries GG is not running, preventing startup hang.
- **Side-by-Side Instance Protection (`main.py`):**
  - Process scan now only terminates orphaned instances from the same executable directory, preserving older versions the user intentionally keeps alongside.
- **Missing Content Resilience (`src/image_utils.py`, `src/Weather.py`, `src/Timer.py`):**
  - Missing `content/` assets (icons, fonts) no longer crash the app; fall back to blank placeholders or system fonts.
- **AFK Logic Merge (`src/DisplayManager.py`, `src/afk_logic.py`):**
  - Merged tested AFK helpers (`compute_idle_ms`, `is_gamepad_or_external_active`, `wake_reason`, `cursor_moved`) from `src/afk_logic.py` into the render loop, eliminating duplicated inline logic and test/runtime drift.
  - `trigger_afk_test()` now runs in a background thread to avoid blocking the GUI.
- **Thread Safety & Lifecycle Hardening (`src/HIDListener.py`, `src/fps_monitor.py`, `src/DisplayManager.py`, `src/SteelSeriesAPI.py`, `src/Systray.py`, `src/HardwareMonitor.py`):**
  - `HIDListener` keeps references to all background threads and joins them on `stop()`; `device_path` protected by lock against TOCTOU races.
  - `FPSMonitor.stop()` joins the worker thread.
  - `_last_sent_frame` dedup logic in `DisplayManager` protected by `_frame_lock` across all 9 write sites.
  - `SteelSeriesAPI.reset()` guarded by `_send_lock` for state consistency.
  - `Systray.exit_app()` verifies lock file owner (PID) before removal to protect single-instance guarantee.
  - `HardwareMonitor.update_preferences()` no longer leaks refcount on repeated preference reloads.
- **Logging & Update Cleanup (`main.py`, `src/updater.py`, `src/UserPreferences.py`):**
  - `setup_logging()` falls back to `crash.log` instead of silently swallowing initialization errors.
  - Updater cleans up leftover `.new` files on all exit paths.
  - `os.fsync()` and `os.replace()` wrapped in `try/except` for robustness on network filesystems and mocked test environments.
- **UI & Rendering (`src/Timer.py`, `src/Weather.py`, `src/Systray.py`):**
  - Temperature degree symbol (`°C`) routed through `normalize_text()` to prevent missing-glyph boxes on 7-bit OLED fonts.
  - All Turkish comments translated to English per AGENTS.md localization rule.
- **Removed `run_err.txt` / `run_out.txt`** from repository root (runtime test artifacts).

---

## [`v1.51`] — 2026-11-02 — 🎮 Gamepad & External Device AFK Idle Decoupling & Sleep Blackout Fix
### ✨ Fix / Feature Upgrade (`+0.01`)
- **Gamepad & External Input Idle Decoupling (`src/DisplayManager.py`):**
  - Resolved an issue where connected XInput controllers (or any external input device generating polling packets / stick deflection) continuously reset Windows `GetLastInputInfo()` system idle timer to zero, preventing AFK mode from ever engaging.
  - Automatically detects connected gamepads via `XInputGetState` (with 1.0s caching) and detects external device input desynchronization (`hook/cursor idle > 5000ms` while `win_idle < 2500ms`), dynamically decoupling AFK idle tracking to strictly rely on physical keyboard and mouse activity (`min(hook_idle_ms, cursor_idle_ms)`).
- **Hardened AFK Wake Safeguard (`src/DisplayManager.py`):**
  - Suppressed the generic `win_idle_ms < 500` fallback wake-up condition whenever a gamepad or external device is active, ensuring that background controller polling packets never prematurely wake the OLED screen, keyboard, or mouse back up.
  - AFK wake remains 100% responsive strictly to genuine physical user input: keyboard keypresses, mouse button clicks / wheel scrolls, or intentional mouse cursor movement (>= 10 px).
- **Unit Test Suite & Verification (`tests/test_afk_and_display.py`):**
  - Updated idle calculation tests to verify that AFK timeout progression is fully preserved during continuous controller polling.

---

## [`v1.50`] — 2026-11-02 — ⌨️ Keyboard Lock Keys HUD & Clock Badges, Safe GameSense AFK Blackout & Profile Safeguard
### 🆕 New Major Module (`+0.10`)
- **Keyboard Lock Keys Indicators (Caps Lock, Num Lock, Scroll Lock) (`src/LockKeysOverlay.py`, `src/Timer.py`, `src/DisplayManager.py`, `src/SettingsWindow.py`):**
  - **Instant 128×40 HUD Notification (Variant 3):** Displays a crisp, high-contrast 128×40 HUD notification tile whenever Caps Lock, Num Lock, or Scroll Lock is toggled. Shows an inverted header pill with the toggled key, centered bold active state (`>>> ON <<<` / `OFF`), and a live status summary of the other two locks.
  - **Zero-Overlap Discrete Clock Badges:** Renders subtle inverted badges (`[C]`, `[N]`, `[S]`) on clock faces when lock keys are active, with verified 0 px overlap across all 4 clock styles (`STANDARD`, `BIG`, `DATE_FOCUSED`, `ANALOG`) across every widget combination (with/without weather, with/without headset battery, with/without voice mute icons).
  - **Low-Latency Native Hook & Polling Integration:** Captures physical key releases via low-level keyboard hook (`WH_KEYBOARD_LL`) with sub-millisecond response time and 100% reliability, backed by 10 FPS state verification.
  - **Customizable Preferences & Live Test Preview:** Configurable toggle and display duration (1.0–3.0s) in Settings GUI with interactive "⌨️ Test Lock Keys Overlay Now" preview.
- **Safe GameSense API Lighting Isolation & Dual-Device AFK Blackout (`src/SteelSeriesAPI.py`):**
  - Completely replaced intrusive in-database configuration mutations with the official SteelSeries GameSense API (`AFK_GAME` / `LIGHTING` events), registered once at startup.
  - Preemptively eliminates `HTTP 429: Events for too many games have been registered recently` rate-limiting by managing AFK transitions strictly via `/game_event` (blackout) and `/stop_game` (wake), completely dropping `/remove_game` registration churn.
  - Targets exact hardware zones for mice (`wheel`, `logo`, and zones `one` through `ten` for multi-zone mice such as Rival 5) with static black `[0, 0, 0]`, eliminating SteelSeries default orange (`#FC4E02`) fallback.
  - Per-key RGB keyboards (Apex 7, Apex Pro) blackout via full bitmap mode `[0, 0, 0]`.
- **Non-Destructive Active Configuration Redeployment (`src/SteelSeriesAPI.py`, `src/DisplayManager.py`):**
  - Streamlined `_redeploy_engine_active_configs()` to perform lean, read-only active profile detection and instant atomic `forceDeploy` without touching user settings or modifying hardware timeouts in the database.
  - Unblocked instant wake-up transitions in `DisplayManager` by eliminating false 3.0s retry throttling on active state changes.
- **Profile Integrity Safeguard & Unit Test Suite (`tests/test_steelseries_api.py`, `tests/test_lock_keys.py`):**
  - Expanded unit test suite to 49 passing tests verifying GameSense AFK zone structures, non-destructive transitions, and HUD/clock badge rendering across all clock styles.

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
