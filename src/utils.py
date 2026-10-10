import os
from os import path, getenv
import winreg
import sys
import logging

logger = logging.getLogger("OLED Customizer.Utils")

_appdata_migrated = False

def fetch_app_data_path(content=''):
    global _appdata_migrated
    appdata = getenv('APPDATA') or ''
    new_dir = path.abspath(path.join(appdata, 'SteelSeries OLED Hub'))
    old_dir = path.abspath(path.join(appdata, 'OLED Customizer'))

    if not _appdata_migrated:
        _appdata_migrated = True
        try:
            import shutil
            new_cfg = path.join(new_dir, 'config.json')
            old_cfg = path.join(old_dir, 'config.json')
            if not path.exists(new_cfg) and path.exists(old_cfg):
                os.makedirs(new_dir, exist_ok=True)
                shutil.copy2(old_cfg, new_cfg)
                old_backups = path.join(old_dir, 'backups')
                new_backups = path.join(new_dir, 'backups')
                if path.exists(old_backups) and not path.exists(new_backups):
                    shutil.copytree(old_backups, new_backups)
        except Exception:
            pass

    return path.abspath(path.join(new_dir, content))

def fetch_content_path(content):
    if getattr(sys, 'frozen', False):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    else:
        base_path = path.join(path.dirname(path.abspath(__file__)), '../')
    
    return path.abspath(path.join(base_path, content))

def normalize_text(text: str) -> str:
    """
    Replaces diacritics (Polish, Turkish, German, Romance, etc.) with their ASCII equivalents
    to prevent missing-glyph rendering issues on 7-bit ASCII OLED fonts (DS-DIGIB, MunroSmall).
    """
    if not text:
        return ""

    import unicodedata

    replacements = {
        "ą": "a", "Ą": "A",
        "ć": "c", "Ć": "C",
        "ę": "e", "Ę": "E",
        "ł": "l", "Ł": "L",
        "ń": "n", "Ń": "N",
        "ó": "o", "Ó": "O",
        "ś": "s", "Ś": "S",
        "ź": "z", "Ź": "Z",
        "ż": "z", "Ż": "Z",
        "ş": "s", "Ş": "S",
        "ç": "c", "Ç": "C",
        "ü": "u", "Ü": "U",
        "ö": "o", "Ö": "O",
        "ğ": "g", "Ğ": "G",
        "ı": "i", "İ": "I",
        "ß": "ss", "ẞ": "SS",
        "ä": "a", "Ä": "A",
    }

    for src_char, ascii_char in replacements.items():
        text = text.replace(src_char, ascii_char)

    # Strip any remaining combining accents (e.g. é -> e, ñ -> n, ã -> a)
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))

def set_startup(enable: bool):
    """
    Add or remove the application from Windows Startup via Task Scheduler.
    Uses PowerShell to create a robust task that runs with highest privileges
    and ignores AC power restrictions.
    """
    import subprocess
    
    task_name = "SteelSeries OLED & RGB Hub"
    old_task_name = "OLED Customizer"
    
    try:
        if getattr(sys, 'frozen', False):
            exe_path = sys.executable
        else:
            # Dev mode - include the script path
            exe_path = f"{sys.executable}\" \"{os.path.abspath(sys.argv[0])}"
        
        # Remove legacy task if present
        subprocess.run(
            ['schtasks', '/delete', '/tn', old_task_name, '/f'],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        if enable:
            # PowerShell script to create a robust task
            ps_cmd = f"""
            $action = New-ScheduledTaskAction -Execute '{exe_path}'
            $trigger = New-ScheduledTaskTrigger -AtLogon
            $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0
            Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force
            """
            
            result = subprocess.run(
                ["powershell", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            if result.returncode == 0:
                logger.info(f"Added to startup (Task Scheduler): {exe_path}")
                _cleanup_old_registry_startup()
            else:
                logger.error(f"Failed to create scheduled task: {result.stderr}")
                # Fallback to simple schtasks if PowerShell fails
                subprocess.run(
                    ['schtasks', '/create', '/tn', task_name, '/tr', f'"{exe_path}"', '/sc', 'onlogon', '/rl', 'highest', '/f'],
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
        else:
            # Delete the scheduled task
            subprocess.run(
                ['schtasks', '/delete', '/tn', task_name, '/f'],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            logger.info("Removed from startup (Task Scheduler)")
            _cleanup_old_registry_startup()
                
    except Exception as e:
        logger.error(f"Failed to change startup settings: {e}")
    finally:
        # Every caller (tray toggle, settings save) must see fresh state
        invalidate_startup_cache()

def _cleanup_old_registry_startup():
    """Remove any old registry-based startup entry."""
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    for app_name in ("OLED Customizer", "SteelSeries OLED & RGB Hub"):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            try:
                winreg.DeleteValue(key, app_name)
                logger.info("Cleaned up old registry startup entry")
            except FileNotFoundError:
                pass
            winreg.CloseKey(key)
        except Exception:
            pass

# Cache for is_startup_enabled: pystray re-evaluates menu checkmarks on every
# open, and each query spawns a schtasks subprocess. Invalidated by set_startup.
_startup_cache = {"ts": 0.0, "val": False}


def invalidate_startup_cache():
    _startup_cache["ts"] = 0.0


def cached_is_startup_enabled() -> bool:
    import time as _t
    now = _t.time()
    if now - _startup_cache["ts"] > 10.0:
        _startup_cache["val"] = is_startup_enabled()
        _startup_cache["ts"] = now
    return _startup_cache["val"]


def is_startup_enabled() -> bool:
    """Check if the startup task exists in Task Scheduler."""
    import subprocess

    for task_name in ("SteelSeries OLED & RGB Hub", "OLED Customizer"):
        try:
            result = subprocess.run(
                ['schtasks', '/query', '/tn', task_name],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode == 0:
                return True
        except Exception as e:
            logger.error(f"Failed to check startup status: {e}")
    return False

def is_process_running(process_names):
    """
    Check if any of the given process names are running.
    """
    import psutil
    if isinstance(process_names, str):
        process_names = [process_names]
        
    process_names = [p.lower() for p in process_names]
    
    for proc in psutil.process_iter(['name']):
        try:
            if proc.info['name'].lower() in process_names:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False

def find_steelseries_gg_path():
    """
    Attempts to locate the SteelSeries GG executable in common installation directories.
    """
    common_paths = [
        r"C:\Program Files\SteelSeries\GG\SteelSeriesGG.exe",
        r"C:\Program Files (x86)\SteelSeries\GG\SteelSeriesGG.exe",
        r"C:\Program Files\SteelSeries\SteelSeries Engine 3\SteelSeriesEngine3.exe",
        r"C:\Program Files (x86)\SteelSeries\SteelSeries Engine 3\SteelSeriesEngine3.exe"
    ]
    
    for p in common_paths:
        if path.exists(p):
            return p
            
    return None

def launch_process(process_path, arguments=None, minimized=False):
    """
    Launches a process from the given path non-blocking.
    Uses ShellExecute to handle UAC elevation if required (WinError 740).
    If minimized=True, starts the window minimized.
    """
    if not process_path or not path.exists(process_path):
        return False
        
    try:
        if not arguments:
            os.startfile(process_path)
            logger.info(f"Launched process (standard): {process_path}")
            return True
            
        import ctypes
        # ShellExecuteW(hwnd, operation, file, parameters, directory, show_cmd)
        # SW_SHOWNORMAL = 1 (SW_HIDE/SW_SHOWMINIMIZED don't work with SteelSeries GG)
        show_cmd = 1
        
        directory = path.dirname(process_path)
        ret = ctypes.windll.shell32.ShellExecuteW(None, "open", process_path, arguments, directory, show_cmd)
        
        # Returns > 32 on success
        if ret > 32:
            logger.info(f"Launched process: {process_path} args={arguments} minimized={minimized}")
            return True
        else:
            logger.error(f"ShellExecute failed with code {ret} for {process_path}")
            return False
            
    except Exception as e:
        logger.error(f"Failed to launch process {process_path}: {e}")
        return False

import ctypes
from ctypes import wintypes

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

def get_idle_time_ms() -> int:
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF
    return 0

