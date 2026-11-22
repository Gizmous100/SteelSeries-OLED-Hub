import requests
import hashlib
import os
import re
import sys
import subprocess
import logging
import threading
from version import __version__

logger = logging.getLogger("OLED Customizer.Updater")

REPO_RAW_URL = "https://raw.githubusercontent.com/Gizmous100/SteelSeries-OLED-Hub/main/version.py"
RELEASE_EXE_URL = "https://github.com/Gizmous100/SteelSeries-OLED-Hub/releases/latest/download/SteelSeries-OLED-Hub.exe"
RELEASE_API_URL = "https://api.github.com/repos/Gizmous100/SteelSeries-OLED-Hub/releases/latest"
ASSET_NAME = "SteelSeries-OLED-Hub.exe"

# A real build is tens of megabytes; anything tiny is a captive-portal page.
MIN_EXE_SIZE = 1 * 1024 * 1024

_update_lock = threading.Lock()

def get_latest_version():
    """Fetches the latest version string from the GitHub repo."""
    try:
        response = requests.get(REPO_RAW_URL, timeout=5)
        if response.status_code == 200:
            # Parse __version__ = "x.y.z"
            content = response.text
            for line in content.splitlines():
                if '__version__' in line:
                    m = re.search(r'__version__\s*=\s*"([^"]+)"', line)
                    if m:
                        return m.group(1)
    except Exception as e:
        logger.error(f"Failed to fetch latest version: {e}")
    return None

def is_update_available():
    """Returns (True, latest_version) if an update is available, else (False, latest_version)."""
    latest = get_latest_version()
    if not latest:
        return False, None
    
    # Simple version comparison (e.g. "1.2.16" vs "1.2.17")
    try:
        local_parts = [int(p) for p in __version__.split('.')]
        latest_parts = [int(p) for p in latest.split('.')]
    except (TypeError, ValueError):
        logger.warning("Could not parse version strings: local=%r latest=%r", __version__, latest)
        return False, latest

    for i in range(max(len(local_parts), len(latest_parts))):
        lp = local_parts[i] if i < len(local_parts) else 0
        rp = latest_parts[i] if i < len(latest_parts) else 0
        if rp > lp:
            return True, latest
        if lp > rp:
            return False, latest

    return False, latest


def _expected_sha256():
    """Return the SHA-256 published for the release asset, if GitHub exposes it."""
    try:
        response = requests.get(RELEASE_API_URL, timeout=5)
        if response.status_code != 200:
            return None
        for asset in response.json().get("assets", []):
            if asset.get("name") == ASSET_NAME:
                digest = asset.get("digest") or ""
                if digest.startswith("sha256:"):
                    return digest.split(":", 1)[1].lower()
    except Exception as e:
        logger.debug("Could not read release digest: %s", e)
    return None


def _file_sha256(path):
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest().lower()


def _valid_download(path) -> bool:
    """Basic sanity checks: a real PE binary of plausible size."""
    try:
        if os.path.getsize(path) < MIN_EXE_SIZE:
            logger.error("Downloaded update is too small (%d bytes)", os.path.getsize(path))
            return False
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                logger.error("Downloaded update is not a Windows executable (missing MZ header)")
                return False
        return True
    except OSError as e:
        logger.error("Could not validate downloaded update: %s", e)
        return False

def start_update_process():
    """Download, validate and in-place replace the running executable.

    Must never run concurrently (the tray may fire it twice).
    """
    if not _update_lock.acquire(blocking=False):
        logger.warning("An update is already in progress; ignoring duplicate request.")
        return False

    try:
        if not getattr(sys, 'frozen', False):
            logger.warning("Auto-update is only available in the built executable.")
            return False

        current_exe = sys.executable
        new_exe = current_exe + ".new"
        old_exe = current_exe + ".old"

        # 1. Download the latest EXE
        logger.info(f"Downloading update from {RELEASE_EXE_URL}...")
        response = requests.get(RELEASE_EXE_URL, stream=True, timeout=30)
        if response.status_code != 200:
            logger.error(f"Failed to download update: HTTP {response.status_code}")
            return False

        expected_size = response.headers.get("Content-Length")
        downloaded = 0
        with open(new_exe, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
            f.flush()
            os.fsync(f.fileno())

        if expected_size and expected_size.isdigit() and downloaded != int(expected_size):
            logger.error(
                "Update download is truncated (%d of %s bytes)", downloaded, expected_size
            )
            _safe_remove(new_exe)
            return False

        # 2. Integrity checks: real PE binary + published SHA-256 when available.
        if not _valid_download(new_exe):
            _safe_remove(new_exe)
            return False

        expected_sha = _expected_sha256()
        if expected_sha:
            actual_sha = _file_sha256(new_exe)
            if actual_sha != expected_sha:
                logger.error(
                    "Update checksum mismatch (expected %s, got %s)", expected_sha, actual_sha
                )
                _safe_remove(new_exe)
                return False
            logger.info("Update SHA-256 verified.")
        else:
            logger.warning("Release digest unavailable; only basic integrity checks were applied.")

        # Remove "Mark of the Web" — Windows blocks DLL extraction from internet-downloaded exes
        try:
            zone_file = new_exe + ":Zone.Identifier"
            if os.path.exists(zone_file):
                os.remove(zone_file)
        except Exception:
            pass
        try:
            subprocess.run(["powershell", "-Command", f'Unblock-File -Path "{new_exe}"'],
                           capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass

        # 3. In-place swap with rollback.
        logger.info("Executing In-Place Swap...")
        try:
            if os.path.exists(old_exe):
                try:
                    os.remove(old_exe)
                except Exception as e:
                    logger.warning(f"Could not remove previous .old file: {e}")
                    _safe_remove(new_exe)
                    return False

            # Windows allows renaming an open executable, but not deleting it.
            os.rename(current_exe, old_exe)
            try:
                os.rename(new_exe, current_exe)
            except Exception:
                # Second rename failed — put the original executable back.
                if os.path.exists(old_exe) and not os.path.exists(current_exe):
                    os.rename(old_exe, current_exe)
                raise

            # 4. Launch the new version (UAC elevation, detached from this job).
            logger.info("Launching new executable requesting UAC Elevation...")
            import ctypes
            # SW_SHOWNORMAL = 1
            ret = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                current_exe,
                None,
                os.path.dirname(current_exe),
                1
            )

            if ret <= 32:
                logger.warning(f"ShellExecuteW runas failed (code {ret}). Falling back to Popen.")
                creation_flags = 0x01000000 | 0x00000008  # CREATE_BREAKAWAY_FROM_JOB | DETACHED_PROCESS
                subprocess.Popen([current_exe], creationflags=creation_flags)

            # 5. Release hardware resources before terminating.
            try:
                # Tell background hardware threads to shut down and release WMI/COM locks
                from src.HardwareMonitor import _lhm_worker
                _lhm_worker.stop()
            except Exception:
                pass

            logging.getLogger().info("Update installed; terminating the old process.")
            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                except Exception:
                    pass

            # Clean up any leftover temporary update files before exit
            _safe_remove(current_exe + ".new")

            if threading.current_thread() is threading.main_thread():
                # Normal shutdown path: atexit hooks still run.
                sys.exit(0)
            else:
                # The tray calls this from a worker thread. sys.exit() would
                # only end that thread and leave two instances running, so the
                # process must be terminated explicitly.
                os._exit(0)

        except Exception as e:
            logger.error(f"In-Place update failed: {e}")
            # Try to undo the rename if the swap failed
            try:
                if os.path.exists(old_exe) and not os.path.exists(current_exe):
                    os.rename(old_exe, current_exe)
            except Exception:
                pass
            _safe_remove(new_exe)
            return False

    except Exception as e:
        logger.error(f"Update failed: {e}")
        _safe_remove(current_exe + ".new")
        return False
    finally:
        try:
            _update_lock.release()
        except RuntimeError:
            pass
        # Always clean up leftover .new file on any exit path
        _safe_remove(current_exe + ".new")


def _safe_remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
