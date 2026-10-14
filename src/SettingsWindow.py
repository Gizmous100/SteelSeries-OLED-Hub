"""
SteelSeries OLED & RGB Hub - Settings GUI
Frameless window, sidebar navigation, custom widgets, clean aesthetics.
"""

import tkinter as tk
from tkinter import ttk, colorchooser, messagebox
import os
import gc
import queue
import logging
import ctypes
import threading

logger = logging.getLogger("OLED Customizer.Settings")
from version import __version__
from src.UserPreferences import UserPreferences
from src.utils import set_startup, is_startup_enabled, fetch_app_data_path as f_app_data
from src.updater import is_update_available

# --- THEME CONSTANTS ---
class Colors:
    BG_ROOT = "#2b2b2b"       # Dark Gray background
    SIDEBAR = "#1f1f1f"       # Darker sidebar
    CONTENT = "#2b2b2b"       # Main content bg
    
    ACCENT_PRIMARY = "#ffffff" # White for enabled state
    ACCENT_HOVER   = "#e0e0e0"
    ACCENT_DIM     = "#888888"
    
    TEXT_MAIN  = "#ffffff"
    TEXT_DIM   = "#aaaaaa"
    
    CARD_BG    = "#383838"    # Lighter gray for cards
    CARD_HOVER = "#424242"
    BORDER     = "#444444"
    
    INPUT_BG   = "#1a1a1a"
    INPUT_FG   = "#eeeeee"
    
    DANGER     = "#e74c3c"

FONT_HEADER = ("Segoe UI", 14, "bold")
FONT_SUBHEADER = ("Segoe UI", 10, "bold")
FONT_BODY = ("Segoe UI", 9)
FONT_SMALL = ("Segoe UI", 8)


class CustomTitleBar(tk.Frame):
    def __init__(self, parent, title="SteelSeries OLED & RGB Hub"):
        super().__init__(parent, bg=Colors.SIDEBAR, height=32)
        self.parent = parent
        self.pack_propagate(False)
        self.pack(fill="x", side="top")
        
        # Dragging logic
        self.bind("<Button-1>", self._start_move)
        self.bind("<B1-Motion>", self._move_window)
        
        # Title
        tk.Label(self, text=title, font=("Segoe UI", 10, "bold"), 
                 bg=Colors.SIDEBAR, fg=Colors.TEXT_MAIN).pack(side="left", padx=15)
        
        # Window controls
        close_btn = tk.Label(self, text="×", font=("Arial", 14), 
                             bg=Colors.SIDEBAR, fg=Colors.TEXT_DIM, width=4)
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: parent.destroy())
        close_btn.bind("<Enter>", lambda e: close_btn.configure(bg=Colors.DANGER, fg="white"))
        close_btn.bind("<Leave>", lambda e: close_btn.configure(bg=Colors.SIDEBAR, fg=Colors.TEXT_DIM))
        
        min_btn = tk.Label(self, text="−", font=("Arial", 14), 
                           bg=Colors.SIDEBAR, fg=Colors.TEXT_DIM, width=4)
        min_btn.pack(side="right")
        min_btn.bind("<Button-1>", lambda e: parent.iconify())
        min_btn.bind("<Enter>", lambda e: min_btn.configure(bg=Colors.CARD_HOVER, fg="white"))
        min_btn.bind("<Leave>", lambda e: min_btn.configure(bg=Colors.SIDEBAR, fg=Colors.TEXT_DIM))

    def _start_move(self, event):
        self.x = event.x
        self.y = event.y

    def _move_window(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.parent.winfo_x() + deltax
        y = self.parent.winfo_y() + deltay
        self.parent.geometry(f"+{x}+{y}")


class SidebarButton(tk.Frame):
    def __init__(self, parent, text, icon, command, is_selected=False):
        super().__init__(parent, bg=Colors.SIDEBAR, height=45, cursor="hand2")
        self.pack_propagate(False)
        self.command = command
        self.is_selected = is_selected
        
        self.indicator = tk.Frame(self, bg=Colors.ACCENT_PRIMARY if is_selected else Colors.SIDEBAR, width=4)
        self.indicator.pack(side="left", fill="y")
        
        self.lbl = tk.Label(self, text=f"{icon}  {text}", font=FONT_BODY,
                            bg=Colors.SIDEBAR, fg=Colors.TEXT_MAIN if is_selected else Colors.TEXT_DIM)
        self.lbl.pack(side="left", padx=15)
        
        self.bind("<Button-1>", lambda e: command())
        self.lbl.bind("<Button-1>", lambda e: command())
        
        self.bind("<Enter>", self._on_hover)
        self.bind("<Leave>", self._on_leave)
        
    def _on_hover(self, e):
        if not self.is_selected:
            self.configure(bg=Colors.CARD_HOVER)
            self.lbl.configure(bg=Colors.CARD_HOVER, fg=Colors.TEXT_MAIN)
            
    def _on_leave(self, e):
        if not self.is_selected:
            self.configure(bg=Colors.SIDEBAR)
            self.lbl.configure(bg=Colors.SIDEBAR, fg=Colors.TEXT_DIM)

    def set_selected(self, selected):
        self.is_selected = selected
        self.indicator.configure(bg=Colors.ACCENT_PRIMARY if selected else Colors.SIDEBAR)
        self.lbl.configure(fg=Colors.TEXT_MAIN if selected else Colors.TEXT_DIM)
        self.configure(bg=Colors.SIDEBAR) # Reset bg
        self.lbl.configure(bg=Colors.SIDEBAR)


class ToggleSwitch(tk.Canvas):
    def __init__(self, parent, variable, command=None, width=44, height=22):
        super().__init__(parent, width=width, height=height, bg=Colors.CARD_BG, highlightthickness=0, cursor="hand2")
        self.variable = variable
        self.command = command
        self.width = width
        self.height = height
        self.bind("<Button-1>", self._toggle)
        try:
            self.variable.trace_add("write", self._on_var_change)
        except Exception:
            pass
        self._draw()

    def _on_var_change(self, *_args):
        try:
            if self.winfo_exists():
                self._draw()
        except Exception:
            pass

    def _toggle(self, event):
        self.variable.set(not self.variable.get())
        self._draw()
        if self.command: self.command()
        
    def _draw(self):
        self.delete("all")
        bg_color = Colors.ACCENT_PRIMARY if self.variable.get() else "#555555"
        
        # Draw pill
        self.create_oval(0, 0, self.height, self.height, fill=bg_color, outline="")
        self.create_oval(self.width-self.height, 0, self.width, self.height, fill=bg_color, outline="")
        self.create_rectangle(self.height/2, 0, self.width-self.height/2, self.height, fill=bg_color, outline="")
        
        # Draw knob
        knob_color = "black" if self.variable.get() else "white"
        knob_x = self.width - self.height + 2 if self.variable.get() else 2
        self.create_oval(knob_x, 2, knob_x + self.height - 4, self.height - 2, fill=knob_color, outline="")


class SettingsGUI:
    # Dropdowns that show friendly labels but must save raw values.
    # _init_variables maps value -> label, _save_all maps label -> value.
    DROPDOWN_MAPS = {
        "spotify_fetch_delay": {"1s (Fast)": "1", "2s (Default)": "2", "3s": "3", "5s": "5", "10s": "10"},
        "hw_polling_interval": {"500ms": "500", "1s (Default)": "1000", "2s": "2000", "5s": "5000"},
        "oled_language": {
            "English (EN)": "EN",
            "Polish (PL)": "PL",
            "German (DE)": "DE",
            "Spanish (ES)": "ES",
            "French (FR)": "FR",
            "Italian (IT)": "IT",
            "Portuguese (PT)": "PT",
            "Turkish (TR)": "TR",
            "Dutch (NL)": "NL",
        },
        "weather_aqi_region": {
            "Auto (By City)": "Auto",
            "Europe (EU - CAQI)": "EU",
            "USA / Americas (US - EPA)": "US",
            "Poland (PL - GIOS)": "PL",
            "United Kingdom (UK - DAQI)": "UK",
            "China (CN - MEP)": "CN",
            "India (IN - NAQI)": "IN",
            "Canada (CA - AQHI)": "CA",
            "Australia (AU - NEPM)": "AU",
        },
    }

    # tkinter keysym -> the "Key.x" strings DisplayManager._key_str_to_vk expects
    KEYSYM_TO_PREF = {
        "Pause": "Key.pause", "Insert": "Key.insert", "Delete": "Key.delete",
        "Home": "Key.home", "End": "Key.end",
        "Prior": "Key.page_up", "Next": "Key.page_down",
        "Scroll_Lock": "Key.scroll_lock", "Print": "Key.print_screen",
        "Caps_Lock": "Key.caps_lock", "Num_Lock": "Key.num_lock",
        "space": "Key.space", "Return": "Key.enter", "BackSpace": "Key.backspace",
        "Tab": "Key.tab",
        "Up": "Key.up", "Down": "Key.down", "Left": "Key.left", "Right": "Key.right",
        **{f"F{i}": f"Key.f{i}" for i in range(1, 13)},
    }
    _CAPTURE_MODIFIERS = {
        "Shift_L", "Shift_R", "Control_L", "Control_R",
        "Alt_L", "Alt_R", "Win_L", "Win_R", "Super_L", "Super_R",
    }

    def __init__(self, prefs, on_save=None, manager=None):
        self.prefs = prefs
        self.on_save = on_save
        self.manager = manager
        self.root = None
        self.hwnd = None
        self._capture_target = None
        self._capture_prev = ""
        self._capture_bind_id = None
        self.vars = {}
        self.mock_status_var = None
        self.current_page = None
        self.pages = {}
        self.nav_buttons = {}

    def show(self):
        self.root = tk.Tk()
        self.root.title("SteelSeries OLED & RGB Hub")
        self.current_page = None
        self.root.overrideredirect(True)
        self.root.geometry("680x620")
        self.root.configure(bg=Colors.BG_ROOT)
        
        # Center
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - 680) // 2
        y = (self.root.winfo_screenheight() - 620) // 2
        self.root.geometry(f"+{x}+{y}")
        
        def set_appwindow(root):
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            self.hwnd = hwnd or root.winfo_id()
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = style & ~WS_EX_TOOLWINDOW
            style = style | WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            root.wm_withdraw()
            root.after(10, lambda: root.wm_deiconify())

        self.root.after(10, lambda: set_appwindow(self.root))

        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TCombobox", fieldbackground=Colors.INPUT_BG, background=Colors.INPUT_BG, foreground=Colors.INPUT_FG, arrowcolor=Colors.TEXT_DIM, borderwidth=0)
        style.map("TCombobox", fieldbackground=[("readonly", Colors.INPUT_BG)], selectbackground=[("readonly", Colors.INPUT_BG)], selectforeground=[("readonly", Colors.INPUT_FG)])

        self._build_layout()
        try:
            self.root.mainloop()
        finally:
            try:
                self.vars.clear()
                self.pages.clear()
                self.nav_buttons.clear()
                self.mock_status_var = None
                self.update_btn_text = None
                self.update_btn = None
                self.discord_btn = None
                self.log_text = None
                self.backups_scrollable_frame = None
                self.content_area = None
                self.canvas = None
                self.scrollbar = None
                self.content_container = None
            except Exception:
                pass
            self.root = None
            self.hwnd = None

    def _build_layout(self):
        CustomTitleBar(self.root, f"SteelSeries OLED & RGB Hub v{__version__}").pack(side="top", fill="x")
        container = tk.Frame(self.root, bg=Colors.BG_ROOT)
        container.pack(fill="both", expand=True)

        # === SIDEBAR ===
        sidebar = tk.Frame(container, bg=Colors.SIDEBAR, width=180)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        
        nav_items = [
            ("General", "⚙️"), ("Display", "📺"), ("AFK Mode", "💤"),
            ("Multimedia", "🎵"), ("Hotkeys", "⌨️"), ("Advanced", "🔧"), 
            ("Backups", "💾"), ("Logs", "📄")
        ]
        
        for name, icon in nav_items:
            btn = SidebarButton(sidebar, name, icon, lambda n=name: self._switch_page(n), is_selected=(name=="General"))
            btn.pack(fill="x")
            self.nav_buttons[name] = btn
            
        spacer = tk.Frame(sidebar, bg=Colors.SIDEBAR)
        spacer.pack(fill="both", expand=True)

        tk.Label(
            sidebar,
            text=f"v{__version__} • Author: gizmous\nOriginal: 0z-zy / Firewee",
            font=("Segoe UI", 8),
            bg=Colors.SIDEBAR,
            fg=Colors.TEXT_DIM,
            justify="center",
            wraplength=160,
        ).pack(fill="x", padx=8, pady=(0, 4))
        
        save_btn = tk.Button(sidebar, text="SAVE", font=FONT_SUBHEADER,
                             bg=Colors.ACCENT_PRIMARY, fg="#000000",
                             activebackground=Colors.ACCENT_HOVER, activeforeground="#000000",
                             relief="flat", cursor="hand2", command=self._save_all)
        save_btn.pack(fill="x", padx=15, pady=(4, 16))

        # === CONTENT AREA ===
        self.content_container = tk.Frame(container, bg=Colors.CONTENT)
        self.content_container.pack(side="right", fill="both", expand=True)
        
        self.canvas = tk.Canvas(self.content_container, bg=Colors.CONTENT, highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self.content_container, orient="vertical", command=self.canvas.yview, 
                                      bg=Colors.SIDEBAR, troughcolor=Colors.BG_ROOT, width=10)
        
        self.content_area = tk.Frame(self.canvas, bg=Colors.CONTENT)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.content_area, anchor="nw")
        
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        
        def update_scroll(e=None):
            bbox = self.canvas.bbox("all")
            if not bbox:
                return
            self.canvas.configure(scrollregion=bbox)
            if bbox[3] > self.canvas.winfo_height():
                if not self.scrollbar.winfo_ismapped():
                    self.scrollbar.pack(side="right", fill="y", before=self.canvas)
            else:
                if self.scrollbar.winfo_ismapped():
                    self.scrollbar.pack_forget()

        self.content_area.bind("<Configure>", update_scroll)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width))
        
        def _on_mousewheel(event):
            self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        self.root.bind("<MouseWheel>", _on_mousewheel)
        
        self._init_variables()
        self._create_pages()
        self.root.update()
        self._switch_page("General")

    def _display_for(self, key, value):
        """Map a stored raw value to its dropdown display label."""
        for disp, val in self.DROPDOWN_MAPS.get(key, {}).items():
            if val == str(value):
                return disp
        return str(value)

    def _init_variables(self):
        self.vars["clock_style"] = tk.StringVar(value=self.prefs.get_preference("clock_style") or "Standard")
        self.vars["display_seconds"] = tk.BooleanVar(value=bool(self.prefs.get_preference("display_seconds")))
        initial_lang = self.prefs.get_preference("oled_language") or (
            "PL" if self.prefs.get_preference("use_polish_days") else "EN"
        )
        self.vars["oled_language"] = tk.StringVar(value=self._display_for("oled_language", initial_lang))
        self.vars["run_on_start"] = tk.BooleanVar(value=is_startup_enabled())
        self.vars["date_format"] = tk.BooleanVar(value=(str(self.prefs.get_preference("date_format")) == "24"))
        self.vars["player_style"] = tk.StringVar(value=self.prefs.get_preference("player_style") or "Standard")
        self.vars["display_timer"] = tk.BooleanVar(value=bool(self.prefs.get_preference("display_timer")))
        self.vars["display_player"] = tk.BooleanVar(value=bool(self.prefs.get_preference("display_player")))
        self.vars["display_hw_monitor"] = tk.BooleanVar(value=bool(self.prefs.get_preference("display_hw_monitor")))
        self.vars["spotify_enabled"] = tk.BooleanVar(value=bool(self.prefs.get_preference("spotify_enabled")))
        self.vars["spotify_client_id"] = tk.StringVar(value=self.prefs.get_preference("spotify_client_id") or "")
        self.vars["spotify_client_secret"] = tk.StringVar(value=self.prefs.get_preference("spotify_client_secret") or "")
        self.vars["spotify_redirect_uri"] = tk.StringVar(value=self.prefs.get_preference("spotify_redirect_uri") or "")
        self.vars["local_port"] = tk.StringVar(value=str(self.prefs.get_preference("local_port") or "2408"))
        self.vars["discord_local_port"] = tk.StringVar(value=str(self.prefs.get_preference("discord_local_port") or "8888"))
        self.vars["hotkey_monitor"] = tk.StringVar(value=self.prefs.get_preference("hotkey_monitor") or "")
        self.vars["hotkey_weather"] = tk.StringVar(value=self.prefs.get_preference("hotkey_weather") or "")
        self.vars["hotkey_mute"] = tk.StringVar(value=self.prefs.get_preference("hotkey_mute") or "")
        self.vars["hotkey_mute_2"] = tk.StringVar(value=self.prefs.get_preference("hotkey_mute_2") or "")
        self.vars["hotkey_calculator"] = tk.StringVar(value=self.prefs.get_preference("hotkey_calculator") or "")
        self.vars["scrollbar_padding"] = tk.StringVar(value=str(self.prefs.get_preference("scrollbar_padding") or "2"))
        self.vars["text_padding_left"] = tk.StringVar(value=str(self.prefs.get_preference("text_padding_left") or "30"))
        self.vars["auto_launch_gg"] = tk.BooleanVar(value=bool(self.prefs.get_preference("auto_launch_gg")))
        self.vars["hw_polling_interval"] = tk.StringVar(value=self._display_for("hw_polling_interval", self.prefs.get_preference("hw_polling_interval") or 1000))
        self.vars["show_game_fps"] = tk.BooleanVar(value=bool(self.prefs.get_preference("show_game_fps")))
        self.vars["auto_hw_monitor_game"] = tk.BooleanVar(value=bool(self.prefs.get_preference("auto_hw_monitor_game")))
        self.vars["show_headset_battery"] = tk.BooleanVar(value=bool(self.prefs.get_preference("show_headset_battery")))
        self.vars["show_battery_percent"] = tk.BooleanVar(value=bool(self.prefs.get_preference("show_battery_percent")))
        self.vars["selected_gpu"] = tk.StringVar(value=self.prefs.get_preference("selected_gpu") or "Auto")
        self.vars["spotify_fetch_delay"] = tk.StringVar(value=self._display_for("spotify_fetch_delay", self.prefs.get_preference("spotify_fetch_delay") or 2))
        self.vars["discord_client_id"] = tk.StringVar(value=self.prefs.get_preference("discord_client_id") or "")
        self.vars["discord_client_secret"] = tk.StringVar(value=self.prefs.get_preference("discord_client_secret") or "")
        self.vars["discord_mock_enabled"] = tk.BooleanVar(value=bool(self.prefs.get_preference("discord_mock_enabled")))
        self.vars["headset_hid_sync_enabled"] = tk.BooleanVar(value=bool(self.prefs.get_preference("headset_hid_sync_enabled")))
        self.vars["debug_enabled"] = tk.BooleanVar(value=bool(self.prefs.get_preference("debug_enabled")))
        self.vars["weather_enabled"] = tk.BooleanVar(value=bool(self.prefs.get_preference("weather_enabled")))
        self.vars["show_clock_mute_icons"] = tk.BooleanVar(value=bool(self.prefs.get_preference("show_clock_mute_icons")))
        self.vars["weather_city"] = tk.StringVar(value=self.prefs.get_preference("weather_city") or "Warsaw")
        self.vars["weather_aqi_region"] = tk.StringVar(
            value=self._display_for("weather_aqi_region", self.prefs.get_preference("weather_aqi_region") or "EU")
        )
        self.vars["weather_display_time"] = tk.StringVar(value=str(self.prefs.get_preference("weather_display_time") or "5"))
        afk_pref = self.prefs.get_preference("afk_enabled")
        self.vars["afk_enabled"] = tk.BooleanVar(value=afk_pref if afk_pref is not None else True)
        afk_game_pref = self.prefs.get_preference("afk_disable_in_games")
        self.vars["afk_disable_in_games"] = tk.BooleanVar(value=afk_game_pref if afk_game_pref is not None else True)
        self.vars["afk_timeout_minutes"] = tk.StringVar(value=str(self.prefs.get_preference("afk_timeout_minutes") or "5"))
        prof_sw_pref = self.prefs.get_preference("show_profile_switch_overlay")
        self.vars["show_profile_switch_overlay"] = tk.BooleanVar(value=prof_sw_pref if prof_sw_pref is not None else True)
        self.vars["profile_switch_display_time"] = tk.StringVar(value=str(self.prefs.get_preference("profile_switch_display_time") or "2.5"))

    def _create_pages(self):
        # General
        p_gen = tk.Frame(self.content_area, bg=Colors.CONTENT)
        self._header(p_gen, "⚙️ General Settings")
        self._dropdown_row(p_gen, "Clock Design", self.vars["clock_style"], ["Standard", "Big Timer", "Date Focused", "Analog"])
        self._toggle_row(p_gen, "Use 24-Hour Format", self.vars["date_format"])
        self._toggle_row(p_gen, "Show Seconds", self.vars["display_seconds"])
        self._dropdown_row(
            p_gen,
            "OLED Language (Days & Weather)",
            self.vars["oled_language"],
            list(self.DROPDOWN_MAPS["oled_language"].keys()),
            width=18,
        )
        tk.Frame(p_gen, bg=Colors.CONTENT, height=10).pack()
        self._toggle_row(p_gen, "Run on Start", self.vars["run_on_start"])
        tk.Frame(p_gen, bg=Colors.CONTENT, height=20).pack()
        self._header(p_gen, "✨ Software Update")
        self.update_btn_text = tk.StringVar(value="Check for Updates")
        self.update_btn = tk.Button(p_gen, textvariable=self.update_btn_text, font=FONT_SUBHEADER, bg=Colors.CARD_BG, fg=Colors.TEXT_MAIN, relief="flat", cursor="hand2", padx=20, pady=5, command=self._check_updates)
        self.update_btn.pack(anchor="w", padx=5)
        tk.Label(
            p_gen,
            text=f"Version: v{__version__}  •  Author: gizmous\nOriginal project: 0z-zy (based on Firewee)",
            font=FONT_SMALL,
            bg=Colors.CONTENT,
            fg=Colors.TEXT_DIM,
            anchor="w",
            justify="left",
            wraplength=420,
        ).pack(fill="x", anchor="w", padx=5, pady=(10, 0))
        self.pages["General"] = p_gen

        # Display
        p_disp = tk.Frame(self.content_area, bg=Colors.CONTENT)
        self._header(p_disp, "📺 Display Features")
        self._toggle_row(p_disp, "Enable Clock", self.vars["display_timer"], command=lambda: self._exclusive_toggle("display_timer", "display_hw_monitor"))
        self._toggle_row(p_disp, "Enable Music Info", self.vars["display_player"])
        self._dropdown_row(p_disp, "Player Style", self.vars["player_style"], ["Standard", "Compact", "Centered", "Ticker", "Minimal"])
        self._toggle_row(p_disp, "Always Show System Stats", self.vars["display_hw_monitor"], command=lambda: self._exclusive_toggle("display_hw_monitor", "display_timer"))
        tk.Frame(p_disp, bg=Colors.CONTENT, height=10).pack()
        self._toggle_row(p_disp, "Show Game FPS", self.vars["show_game_fps"])
        self._toggle_row(p_disp, "Auto-switch to System Stats in Games (needs FPS)", self.vars["auto_hw_monitor_game"])
        self._toggle_row(p_disp, "Show Headset Battery on Clock (needs HID Sync)", self.vars["show_headset_battery"])
        self._toggle_row(p_disp, "  └ Show Battery Percentage Number", self.vars["show_battery_percent"])
        available_gpus = ["Auto"]
        try:
            from src.HardwareMonitor import _lhm_worker
            available_gpus += _lhm_worker.get_available_gpus()
        except: pass
        self._dropdown_row(p_disp, "Selected GPU", self.vars["selected_gpu"], available_gpus)
        self._dropdown_row(p_disp, "HW Polling Rate", self.vars["hw_polling_interval"], ["500ms", "1s (Default)", "2s", "5s"])
        
        tk.Frame(p_disp, bg=Colors.CONTENT, height=10).pack()
        self._header(p_disp, "🌦️ Weather & Clock Left Widget")
        self._toggle_row(p_disp, "Enable Weather (Mini-icon on Clock)", self.vars["weather_enabled"])
        self._toggle_row(
            p_disp,
            "Show Mute / Deafen Icons on Clock (Replaces Weather Cloud)",
            self.vars["show_clock_mute_icons"],
            command=self._on_clock_mute_icons_toggle,
        )
        self._entry_row(p_disp, "City Name", self.vars["weather_city"], width=20)
        self._dropdown_row(
            p_disp,
            "AQI Region / Standard",
            self.vars["weather_aqi_region"],
            list(self.DROPDOWN_MAPS["weather_aqi_region"].keys()),
            width=24,
        )
        self._entry_row(p_disp, "Hotkey Display Time (s)", self.vars["weather_display_time"], width=10)

        tk.Frame(p_disp, bg=Colors.CONTENT, height=10).pack()
        self._header(p_disp, "🎯 Profile Switch Overlay")
        self._toggle_row(p_disp, "Show Profile Name on OLED when Changed", self.vars["show_profile_switch_overlay"])
        self._entry_row(p_disp, "Overlay Display Time (s)", self.vars["profile_switch_display_time"], width=10)
        prof_btn_row = tk.Frame(p_disp, bg=Colors.CONTENT)
        prof_btn_row.pack(fill="x", pady=(8, 0))
        tk.Button(
            prof_btn_row,
            text="🎯 Test Profile Overlay Now",
            bg=Colors.CARD_BG,
            fg=Colors.TEXT_MAIN,
            font=FONT_BODY,
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=4,
            command=self._test_profile_overlay,
        ).pack(side="left", padx=(5, 0))

        self.pages["Display"] = p_disp

        # AFK Mode
        p_afk = tk.Frame(self.content_area, bg=Colors.CONTENT)
        self._header(p_afk, "💤 Idle / AFK Mode")
        self._toggle_row(p_afk, "Enable OLED, Keyboard & Mouse Dimming on Idle", self.vars["afk_enabled"])
        self._toggle_row(p_afk, "Disable Idle / AFK Mode During Games", self.vars["afk_disable_in_games"])
        self._entry_row(p_afk, "Idle Timeout (minutes)", self.vars["afk_timeout_minutes"], width=10)
        afk_btn_row = tk.Frame(p_afk, bg=Colors.CONTENT)
        afk_btn_row.pack(fill="x", pady=(8, 0))
        tk.Button(
            afk_btn_row,
            text="🌙 Test AFK Mode Now (1.5s delay)",
            bg=Colors.CARD_BG,
            fg=Colors.TEXT_MAIN,
            font=FONT_BODY,
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=4,
            command=self._test_afk_mode,
        ).pack(side="left", padx=(5, 0))
        tk.Label(
            p_afk,
            text=(
                "Turns off the 128×40 OLED screen, keyboard RGB, and mouse RGB after the configured "
                "inactivity timeout, and automatically restores your active SteelSeries GG profile upon "
                "any mouse, keyboard, or controller activity."
            ),
            font=FONT_SMALL,
            bg=Colors.CONTENT,
            fg=Colors.TEXT_DIM,
            anchor="w",
            justify="left",
            wraplength=420,
        ).pack(fill="x", anchor="w", padx=5, pady=(12, 0))
        self.pages["AFK Mode"] = p_afk

        # Multimedia (Spotify + Player Layout Adjustments)
        p_media = tk.Frame(self.content_area, bg=Colors.CONTENT)
        self._header(p_media, "🎵 Spotify Configuration")
        self._toggle_row(p_media, "Enable Spotify Integration", self.vars["spotify_enabled"])
        self._entry_row(p_media, "Spotify Client ID", self.vars["spotify_client_id"], width=25)
        self._entry_row(p_media, "Spotify Client Secret", self.vars["spotify_client_secret"], width=25, show="*")
        self._entry_row(p_media, "Redirect URI", self.vars["spotify_redirect_uri"], width=25)
        self._entry_row(p_media, "Connection Port", self.vars["local_port"])
        self._dropdown_row(p_media, "Polling Rate", self.vars["spotify_fetch_delay"], ["1s (Fast)", "2s (Default)", "3s", "5s", "10s"])
        tk.Frame(p_media, bg=Colors.CONTENT, height=12).pack()
        self._header(p_media, "📐 Player Layout Adjustments")
        self._entry_row(p_media, "Scrollbar Margin (px)", self.vars["scrollbar_padding"])
        self._entry_row(p_media, "Text Indentation (px)", self.vars["text_padding_left"])
        tk.Label(
            p_media,
            text=(
                "Applies to the Standard music player layout: Scrollbar Margin adjusts horizontal "
                "padding of the bottom progress bar (default: 2 px); Text Indentation adjusts the left "
                "offset of track title & artist next to the media icon (default: 30 px)."
            ),
            font=FONT_SMALL,
            bg=Colors.CONTENT,
            fg=Colors.TEXT_DIM,
            anchor="w",
            justify="left",
            wraplength=420,
        ).pack(fill="x", anchor="w", padx=5, pady=(6, 0))
        self.pages["Multimedia"] = p_media

        # Hotkeys
        p_hot = tk.Frame(self.content_area, bg=Colors.CONTENT)
        self._header(p_hot, "⌨️ Keyboard Shortcuts")
        self._hotkey_row(p_hot, "Show System Stats", self.vars["hotkey_monitor"])
        self._hotkey_row(p_hot, "Show Weather", self.vars["hotkey_weather"])
        self._hotkey_row(p_hot, "Mute Mic (Primary)", self.vars["hotkey_mute"])
        self._hotkey_row(p_hot, "Mute Mic (Optional)", self.vars["hotkey_mute_2"])
        tk.Frame(p_hot, bg=Colors.CONTENT, height=10).pack()
        self._header(p_hot, "🖩 Calculator")
        self._hotkey_row(p_hot, "Toggle Calculator", self.vars["hotkey_calculator"])
        self.pages["Hotkeys"] = p_hot

        # Advanced
        p_adv = tk.Frame(self.content_area, bg=Colors.CONTENT)
        self._header(p_adv, "🔧 System & Diagnostics")
        self._toggle_row(p_adv, "Auto-Launch SteelSeries GG", self.vars["auto_launch_gg"])
        self._toggle_row(p_adv, "Enable Headset HID Sync", self.vars["headset_hid_sync_enabled"])
        self._toggle_row(p_adv, "Enable Diagnostic Debug Logging", self.vars["debug_enabled"], command=self._on_debug_toggle)
        tk.Frame(p_adv, bg=Colors.CONTENT, height=10).pack()
        self._header(p_adv, "🎙️ Discord Voice & Mute Icons")
        self._toggle_row(p_adv, "Mock Discord Mode (Local Mute Icons without OAuth)", self.vars["discord_mock_enabled"])
        self._toggle_row(
            p_adv,
            "Show Mute / Deafen Icons on Clock (Replaces Weather Cloud)",
            self.vars["show_clock_mute_icons"],
            command=self._on_clock_mute_icons_toggle,
        )
        mock_btn_row = tk.Frame(p_adv, bg=Colors.CONTENT)
        mock_btn_row.pack(fill="x", pady=(4, 4))
        tk.Button(
            mock_btn_row,
            text="🎤 Test Mic Mute on OLED",
            bg=Colors.CARD_BG,
            fg=Colors.TEXT_MAIN,
            font=FONT_BODY,
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=4,
            command=self._test_mock_discord_mute,
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            mock_btn_row,
            text="🔇 Test Deafen on OLED",
            bg=Colors.CARD_BG,
            fg=Colors.TEXT_MAIN,
            font=FONT_BODY,
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=4,
            command=self._test_mock_discord_deaf,
        ).pack(side="left", padx=6)
        self.mock_status_var = tk.StringVar(value=self._format_mock_status())
        tk.Label(
            p_adv,
            textvariable=self.mock_status_var,
            font=FONT_SMALL,
            bg=Colors.CONTENT,
            fg=Colors.ACCENT_PRIMARY,
            anchor="w",
            justify="left",
        ).pack(fill="x", anchor="w", padx=5, pady=(2, 8))
        self._entry_row(p_adv, "Discord App ID", self.vars["discord_client_id"], width=25)
        self._entry_row(p_adv, "Discord Client Secret", self.vars["discord_client_secret"], width=25, show="*")
        self._entry_row(p_adv, "Discord Port", self.vars["discord_local_port"], width=25)
        discord_btn_row = tk.Frame(p_adv, bg=Colors.CONTENT)
        discord_btn_row.pack(pady=10)
        self.discord_btn = tk.Button(discord_btn_row, text="🔗 Connect Discord", bg=Colors.CARD_BG, fg=Colors.TEXT_MAIN, font=FONT_BODY, relief="flat", cursor="hand2", padx=12, pady=4, command=self._authorize_discord_action)
        self.discord_btn.pack(side="left", padx=5)
        tk.Button(discord_btn_row, text="🌐 Developer Portal", bg=Colors.CARD_BG, fg=Colors.TEXT_MAIN, font=FONT_BODY, relief="flat", cursor="hand2", padx=10, pady=4, command=self._open_discord_portal).pack(side="left", padx=5)
        tk.Button(discord_btn_row, text="📋 Copy Redirect URI", bg=Colors.CARD_BG, fg=Colors.TEXT_MAIN, font=FONT_BODY, relief="flat", cursor="hand2", padx=10, pady=4, command=self._copy_discord_redirect_uri).pack(side="left", padx=5)
        self.pages["Advanced"] = p_adv

        # Backups
        p_back = tk.Frame(self.content_area, bg=Colors.CONTENT)
        self._create_backups_page(p_back)
        self.pages["Backups"] = p_back

        # Logs
        p_logs = tk.Frame(self.content_area, bg=Colors.CONTENT)
        self._header(p_logs, "📄 Application Logs")
        tools = tk.Frame(p_logs, bg=Colors.CONTENT)
        tools.pack(fill="x", pady=10)
        tk.Button(tools, text="🔄 Refresh", command=self._refresh_logs, bg=Colors.CARD_BG, fg=Colors.TEXT_MAIN, relief="flat").pack(side="left", padx=5)
        tk.Button(tools, text="📂 Open Folder", command=self._open_log_folder, bg=Colors.CARD_BG, fg=Colors.TEXT_MAIN, relief="flat").pack(side="left")
        self.log_text = tk.Text(p_logs, bg=Colors.INPUT_BG, fg=Colors.TEXT_MAIN, font=("Consolas", 9), state="disabled", height=20)
        self.log_text.pack(fill="both", expand=True)
        self.pages["Logs"] = p_logs

    def _create_backups_page(self, parent):
        self._header(parent, "💾 SteelSeries Backups")
        tools = tk.Frame(parent, bg=Colors.CONTENT)
        tools.pack(fill="x", pady=10)
        tk.Button(tools, text="🔄 Refresh", command=self._refresh_backups_list, bg=Colors.CARD_BG, fg=Colors.TEXT_MAIN, relief="flat").pack(side="left", padx=5)
        tk.Button(tools, text="🛡️ Backup Now", command=self._do_manual_backup, bg=Colors.CARD_BG, fg=Colors.TEXT_MAIN, relief="flat").pack(side="left")
        self.backups_scrollable_frame = tk.Frame(parent, bg=Colors.INPUT_BG)
        self.backups_scrollable_frame.pack(fill="both", expand=True)
        self.root.after(100, self._refresh_backups_list)

    def _refresh_backups_list(self):
        from src import ProfileBackup
        for w in self.backups_scrollable_frame.winfo_children(): w.destroy()
        backups = ProfileBackup.list_backups()
        for name, p, count, mtime in (backups or []):
            row = tk.Frame(self.backups_scrollable_frame, bg=Colors.CARD_BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=name, bg=Colors.CARD_BG, fg=Colors.TEXT_MAIN).pack(side="left", padx=10)
            tk.Button(row, text="🗑️", command=lambda path=p: self._delete_backup_action(path), bg=Colors.CARD_BG, fg=Colors.DANGER, relief="flat").pack(side="right")

    def _do_manual_backup(self):
        from src import ProfileBackup
        if ProfileBackup.backup_profiles(): self._refresh_backups_list()

    def _delete_backup_action(self, path):
        from src import ProfileBackup
        if messagebox.askyesno("Delete", "Delete backup?", parent=self.root):
            if ProfileBackup.delete_backup(path): self._refresh_backups_list()

    def _on_clock_mute_icons_toggle(self):
        enabled = bool(self.vars["show_clock_mute_icons"].get())
        if self.manager and hasattr(self.manager, "timer"):
            self.manager.timer.show_clock_mute_icons = enabled
        if self.prefs:
            self.prefs.preferences["show_clock_mute_icons"] = enabled
            self.prefs.save_preferences()

    def _format_mock_status(self, muted=None, deafened=None) -> str:
        if muted is None or deafened is None:
            if self.manager and hasattr(self.manager, "volume_overlay"):
                vo = self.manager.volume_overlay
                muted = bool(vo._discord_muted) if vo._discord_muted is not None else bool(vo._last_mic_mute)
                deafened = bool(vo._discord_deafened)
            else:
                muted = False
                deafened = False
        mic_label = "Muted (mic_off)" if (muted or deafened) else "Active (mic_on)"
        deaf_label = "Deafened (speaker_mute)" if deafened else "Normal (speaker_on)"
        return f"Current OLED Preview: Mic = {mic_label}  •  Speaker = {deaf_label}"

    def _test_mock_discord_mute(self):
        if self.manager and hasattr(self.manager, "trigger_mock_discord_state"):
            def _worker():
                try:
                    muted, deaf = self.manager.trigger_mock_discord_state(toggle_mute=True, toggle_deaf=False)
                    root = self.root
                    status_var = self.mock_status_var
                    if root is not None and status_var is not None:
                        root.after(0, lambda: status_var.set(self._format_mock_status(muted, deaf)) if self.mock_status_var is not None else None)
                except Exception as e:
                    logger.error(f"Mock Discord mute test failed: {e}")
            threading.Thread(target=_worker, daemon=True).start()

    def _test_mock_discord_deaf(self):
        if self.manager and hasattr(self.manager, "trigger_mock_discord_state"):
            def _worker():
                try:
                    muted, deaf = self.manager.trigger_mock_discord_state(toggle_mute=False, toggle_deaf=True)
                    root = self.root
                    status_var = self.mock_status_var
                    if root is not None and status_var is not None:
                        root.after(0, lambda: status_var.set(self._format_mock_status(muted, deaf)) if self.mock_status_var is not None else None)
                except Exception as e:
                    logger.error(f"Mock Discord deafen test failed: {e}")
            threading.Thread(target=_worker, daemon=True).start()

    def _test_afk_mode(self):
        if self.manager and hasattr(self.manager, "trigger_afk_test"):
            def _worker():
                try:
                    self.manager.trigger_afk_test()
                except Exception as e:
                    logger.error(f"AFK test failed: {e}")
            threading.Thread(target=_worker, daemon=True).start()

    def _test_profile_overlay(self):
        if self.manager and hasattr(self.manager, "trigger_profile_overlay_test"):
            def _worker():
                try:
                    self.manager.trigger_profile_overlay_test()
                except Exception as e:
                    logger.error(f"Profile overlay test failed: {e}")
            threading.Thread(target=_worker, daemon=True).start()

    def _get_discord_redirect_uri(self) -> str:
        raw_port = str(self.vars["discord_local_port"].get() or "8888").strip()
        port = int(raw_port) if raw_port.isdigit() else 8888
        return f"http://127.0.0.1:{port}"

    def _open_discord_portal(self):
        import webbrowser
        webbrowser.open("https://discord.com/developers/applications")

    def _copy_discord_redirect_uri(self):
        uri = self._get_discord_redirect_uri()
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(uri)
            self.root.update()
            messagebox.showinfo("Copied", f"Redirect URI copied to clipboard:\n\n{uri}\n\nPaste this into OAuth2 -> Redirects in the Discord Developer Portal.", parent=self.root)
        except Exception as e:
            logger.error(f"Clipboard copy failed: {e}")

    def _authorize_discord_action(self):
        import webbrowser
        import urllib.parse

        cid = str(self.vars["discord_client_id"].get() or "").strip()
        secret = str(self.vars["discord_client_secret"].get() or "").strip()
        raw_port = str(self.vars["discord_local_port"].get() or "8888").strip()
        port = int(raw_port) if raw_port.isdigit() else 8888
        redirect_uri = f"http://127.0.0.1:{port}"

        self.vars["discord_client_id"].set(cid)
        self.vars["discord_client_secret"].set(secret)
        self.vars["discord_local_port"].set(str(port))

        if not cid or not cid.isdigit() or not (15 <= len(cid) <= 22):
            open_portal = messagebox.askyesno(
                "Discord App ID Required",
                "Discord App ID is empty or invalid (Discord requires a numeric 17-20 digit Application ID / Snowflake).\n\n"
                "How to set up Discord RPC (takes ~1 minute):\n"
                "1. Open Discord Developer Portal (https://discord.com/developers/applications) and click 'New Application'.\n"
                "2. Go to the 'OAuth2' tab:\n"
                "   • Copy 'Client ID' -> paste into 'Discord App ID'\n"
                "   • Click 'Reset Secret' -> copy & paste into 'Discord Client Secret'\n"
                f"   • Under 'Redirects', add: {redirect_uri} and click 'Save Changes'.\n"
                "3. Click '🔗 Connect Discord' again.\n\n"
                "Would you like to open the Discord Developer Portal now (and copy the Redirect URI to clipboard)?",
                parent=self.root,
            )
            if open_portal:
                try:
                    self.root.clipboard_clear()
                    self.root.clipboard_append(redirect_uri)
                    self.root.update()
                except Exception:
                    pass
                webbrowser.open("https://discord.com/developers/applications")
            return

        if not secret:
            messagebox.showwarning(
                "Discord Client Secret Required",
                "Please enter your 'Discord Client Secret' (from the OAuth2 tab in Discord Developer Portal) before connecting.\n\n"
                f"Also make sure '{redirect_uri}' is added under OAuth2 -> Redirects in your Discord Application.",
                parent=self.root,
            )
            return

        prev_cid = self.prefs.preferences.get("discord_client_id")
        self.prefs.preferences["discord_client_id"] = cid
        self.prefs.preferences["discord_client_secret"] = secret
        self.prefs.preferences["discord_local_port"] = port
        if prev_cid != cid:
            self.prefs.preferences["discord_access_token"] = ""
        self.prefs.save_preferences()

        if self.on_save:
            def _notify_bg():
                try:
                    self.on_save()
                except Exception as e:
                    logger.error(f"Discord on_save reload failed: {e}")
            threading.Thread(target=_notify_bg, daemon=True).start()

        encoded_redirect = urllib.parse.quote(redirect_uri, safe="")
        url = (
            f"https://discord.com/api/oauth2/authorize?client_id={cid}"
            f"&redirect_uri={encoded_redirect}"
            f"&response_type=code&scope=rpc%20rpc.voice.read%20rpc.voice.write"
        )
        webbrowser.open(url)

    def _switch_page(self, name):
        for p in self.pages.values(): p.pack_forget()
        self.pages[name].pack(fill="both", expand=True, padx=20, pady=18)
        for n, btn in self.nav_buttons.items(): btn.set_selected(n == name)
        self.current_page = name
        try:
            self.canvas.yview_moveto(0)
        except Exception:
            pass
        if name == "Logs": self._refresh_logs()

    def _refresh_logs(self):
        self.log_text.config(state="normal")
        self.log_text.delete(1.0, tk.END)
        log_file = f_app_data("debug.log")
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                self.log_text.insert(tk.END, "".join(f.readlines()[-200:]))
        self.log_text.config(state="disabled")

    def _open_log_folder(self):
        os.startfile(f_app_data())

    def _exclusive_toggle(self, k1, k2):
        if self.vars[k1].get(): self.vars[k2].set(False)

    def _header(self, p, t):
        tk.Label(p, text=t, font=FONT_HEADER, fg=Colors.TEXT_MAIN, bg=Colors.CONTENT).pack(anchor="w", pady=(0, 14))

    def _row_frame(self, p):
        f = tk.Frame(p, bg=Colors.CARD_BG, height=44)
        f.pack(fill="x", pady=4)
        f.pack_propagate(False)
        return f

    def _toggle_row(self, p, l, v, command=None):
        f = self._row_frame(p)
        ToggleSwitch(f, v, command=command).pack(side="right", padx=(8, 15))
        tk.Label(
            f, text=l, font=FONT_BODY, fg=Colors.TEXT_MAIN, bg=Colors.CARD_BG,
            anchor="w", justify="left"
        ).pack(side="left", padx=(15, 4), fill="x", expand=True)

    def _dropdown_row(self, p, l, v, opts, width=15):
        f = self._row_frame(p)
        cb = ttk.Combobox(f, textvariable=v, values=opts, state="readonly", width=width)
        cb.pack(side="right", padx=(8, 15))
        tk.Label(
            f, text=l, font=FONT_BODY, fg=Colors.TEXT_MAIN, bg=Colors.CARD_BG,
            anchor="w", justify="left"
        ).pack(side="left", padx=(15, 4), fill="x", expand=True)

    def _entry_row(self, p, l, v, width=10, show=None):
        f = self._row_frame(p)
        tk.Entry(
            f, textvariable=v, bg=Colors.INPUT_BG, fg=Colors.INPUT_FG,
            relief="flat", width=width, show=show
        ).pack(side="right", padx=(8, 15))
        tk.Label(
            f, text=l, font=FONT_BODY, fg=Colors.TEXT_MAIN, bg=Colors.CARD_BG,
            anchor="w", justify="left"
        ).pack(side="left", padx=(15, 4), fill="x", expand=True)

    def _hotkey_row(self, p, l, v):
        f = self._row_frame(p)

        def small_btn(txt, cmd):
            return tk.Button(f, text=txt, command=cmd, bg=Colors.INPUT_BG, fg=Colors.TEXT_MAIN,
                             font=FONT_SMALL, relief="flat", cursor="hand2", padx=6)

        small_btn("✕", lambda: self._assign_hotkey(v, "")).pack(side="right", padx=(0, 15))
        small_btn("M5", lambda: self._assign_hotkey(v, "Key.mouse_5")).pack(side="right", padx=2)
        small_btn("M4", lambda: self._assign_hotkey(v, "Key.mouse_4")).pack(side="right", padx=2)
        small_btn("Set", lambda: self._begin_hotkey_capture(v)).pack(side="right", padx=6)
        tk.Label(f, textvariable=v, font=FONT_SMALL, fg=Colors.TEXT_DIM, bg=Colors.CARD_BG).pack(side="right", padx=8)
        tk.Label(
            f, text=l, font=FONT_BODY, fg=Colors.TEXT_MAIN, bg=Colors.CARD_BG,
            anchor="w", justify="left"
        ).pack(side="left", padx=(15, 4), fill="x", expand=True)

    def _assign_hotkey(self, var, value):
        """Directly assign a hotkey value, disarming any pending capture first
        (otherwise SAVE's capture-cancel would revert the visible assignment)."""
        if self._capture_target is var:
            try:
                self.root.unbind("<KeyPress>", self._capture_bind_id)
            except Exception:
                pass
            self._capture_target = None
        elif self._capture_target is not None:
            self._cancel_hotkey_capture()
        var.set(value)

    def _begin_hotkey_capture(self, var):
        if self._capture_target is not None:
            return
        self._capture_target = var
        self._capture_prev = var.get()
        var.set("Press a key… (Esc cancels)")
        self._capture_bind_id = self.root.bind("<KeyPress>", self._on_capture_key)

    def _cancel_hotkey_capture(self):
        if self._capture_target is None:
            return
        try:
            self.root.unbind("<KeyPress>", self._capture_bind_id)
        except Exception:
            pass
        self._capture_target.set(self._capture_prev)
        self._capture_target = None

    def _on_capture_key(self, event):
        var = self._capture_target
        if var is None:
            return "break"
        ks = event.keysym
        if ks in self._CAPTURE_MODIFIERS:
            return "break"  # keep waiting for a real key

        try:
            self.root.unbind("<KeyPress>", self._capture_bind_id)
        except Exception:
            pass
        self._capture_target = None

        if ks == "Escape":
            var.set(self._capture_prev)
            return "break"

        mapped = self.KEYSYM_TO_PREF.get(ks)
        if mapped is None and len(event.char) == 1 and event.char.isprintable() and not event.char.isspace():
            mapped = event.char.lower()
        if mapped is None and len(ks) == 1:
            mapped = ks.lower()
        var.set(mapped if mapped else self._capture_prev)
        return "break"

    def _check_updates(self):
        def worker():
            available, latest = is_update_available()
            label = f"Update to v{latest}" if available else "Up to Date"
            root = self.root
            btn_var = self.update_btn_text
            if root is not None and btn_var is not None:
                try:
                    root.after(0, lambda: btn_var.set(label) if self.update_btn_text is not None else None)
                except Exception:
                    pass
        threading.Thread(target=worker, daemon=True).start()

    def _save_all(self):
        try:
            # Don't save the "Press a key…" placeholder if SAVE is hit mid-capture
            self._cancel_hotkey_capture()

            # Gather all vars with proper type conversion
            for k, v in self.vars.items():
                val = v.get()

                # Map dropdown display labels back to raw values ("1s (Fast)" -> "1")
                if k in self.DROPDOWN_MAPS:
                    val = self.DROPDOWN_MAPS[k].get(val, val)

                # Integer fields
                if k in ["scrollbar_padding", "text_padding_left", "local_port", "discord_local_port", "spotify_fetch_delay", "hw_polling_interval", "weather_display_time"]:
                    try: val = int(val)
                    except: val = UserPreferences.DEFAULT.get(k, 0)

                if k == "afk_timeout_minutes":
                    try:
                        fval = max(float(str(val).replace(",", ".")), 0.1)
                        val = int(fval) if fval.is_integer() else round(fval, 2)
                    except Exception:
                        val = UserPreferences.DEFAULT.get(k, 5)

                if k == "profile_switch_display_time":
                    try:
                        fval = max(float(str(val).replace(",", ".")), 0.5)
                        val = int(fval) if fval.is_integer() else round(fval, 2)
                    except Exception:
                        val = UserPreferences.DEFAULT.get(k, 2.5)
                
                # Special mapping for date format checkbox
                if k == "date_format":
                    val = 24 if val else 12
                    
                self.prefs.preferences[k] = val
            
            self.prefs.preferences["use_polish_days"] = (self.prefs.preferences.get("oled_language") == "PL")
            self.prefs.preferences["rgb_enabled"] = False
            self.prefs.save_preferences()

            want_startup = bool(self.vars["run_on_start"].get())
            if want_startup != bool(is_startup_enabled()):
                set_startup(want_startup)
            
            if self.on_save:
                def safe_on_save():
                    try: self.on_save()
                    except Exception as e: logger.error(f"on_save failed: {e}")
                threading.Thread(target=safe_on_save, daemon=True).start()

            messagebox.showinfo("Success", "Settings saved!", parent=self.root)
                
        except Exception as e:
            logger.error(f"Save failed: {e}")
            messagebox.showerror("Error", f"Failed to save: {e}", parent=self.root)

    def _on_debug_toggle(self):
        from src.debug_utils import toggle_debug_logging
        toggle_debug_logging(self.vars["debug_enabled"].get())

# Tcl/Tk (tcl86t.dll) requires that all Tk interpreters and Variable/TkappObject
# finalizers (Tcl_AsyncDelete) run on the exact same OS thread that created them.
# Spawning disposable threads per tray click caused cyclic GC on the Main Thread
# to finalize dead threads' Tk objects and trigger Tcl_Panic (0x80000003).
# A single persistent daemon thread owns all SettingsGUI lifecycles and runs
# gc.collect() immediately upon window close.
_open_gui_lock = threading.Lock()
_open_gui = None
_settings_request_queue = queue.Queue(maxsize=1)
_settings_worker_started = False


def _settings_worker_loop():
    global _open_gui
    while True:
        prefs, callback, manager = _settings_request_queue.get()
        gui = None
        try:
            with _open_gui_lock:
                gui = SettingsGUI(prefs, callback, manager)
                _open_gui = gui
            gui.show()
        except Exception:
            logger.exception("Settings window crashed")
        finally:
            with _open_gui_lock:
                if _open_gui is gui:
                    _open_gui = None
            gui = None
            # Force cyclic GC on THIS persistent Tk thread so Tkapp_Dealloc /
            # Tcl_AsyncDelete never gets invoked from another thread during GC.
            try:
                gc.collect()
            except Exception:
                pass


def open_settings(prefs, callback=None, manager=None):
    global _settings_worker_started
    with _open_gui_lock:
        if _open_gui is not None:
            hwnd = _open_gui.hwnd
            if hwnd:
                try:
                    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
            return
        if not _settings_worker_started:
            _settings_worker_started = True
            threading.Thread(
                target=_settings_worker_loop,
                daemon=True,
                name="Settings-UI-Thread",
            ).start()
    try:
        _settings_request_queue.put_nowait((prefs, callback, manager))
    except queue.Full:
        pass
