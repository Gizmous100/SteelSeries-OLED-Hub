import requests
import hashlib
import os
import sys
import subprocess
import logging
import threading
from version import __version__

logger = logging.getLogger("OLED Customizer.Updater")

RELEASE_API_URL = "https://api.github.com/repos/Gizmous100/SteelSeries-OLED-Hub/releases/latest"
ASSET_NAME = "SteelSeries-OLED-Hub.exe"

# A real build is tens of megabytes; anything tiny is a captive-portal page.
MIN_EXE_SIZE = 1 * 1024 * 1024

_update_lock = threading.Lock()


def _parse_version(text):
    """'v1.54' / '1.54' -> (1, 54); None when it is not a dotted number."""
    try:
        return tuple(int(part) for part in str(text).strip().lstrip("vV").split("."))
    except (TypeError, ValueError):
        return None


def _is_newer(candidate, current=__version__) -> bool:
    cand, cur = _parse_version(candidate), _parse_version(current)
    if cand is None or cur is None:
        return False
    width = max(len(cand), len(cur))
    return cand + (0,) * (width - len(cand)) > cur + (0,) * (width - len(cur))


def _fetch_latest_release():
    """Version, download URL and SHA-256 of the *same* published release.

    The version used to come from version.py on the main branch while the binary
    came from releases/latest, so a version bump without a published release made
    every client reinstall (or downgrade to) the older release in a loop.
    """
    try:
        response = requests.get(RELEASE_API_URL, timeout=5,
                                headers={"Accept": "application/vnd.github+json"})
        if response.status_code != 200:
            logger.warning("Release check failed: HTTP %s", response.status_code)
            return None
        data = response.json()
        version = str(data.get("tag_name") or "").strip().lstrip("vV")
        for asset in data.get("assets", []):
            if asset.get("name") == ASSET_NAME:
                digest = str(asset.get("digest") or "")
                return {
                    "version": version,
                    "url": asset.get("browser_download_url") or "",
                    "sha256": digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None,
                }
        logger.warning("Latest release %s has no %s asset", version, ASSET_NAME)
    except Exception as e:
        logger.error(f"Failed to fetch latest release: {e}")
    return None


def get_latest_version():
    """Version of the latest published release (None when unavailable)."""
    release = _fetch_latest_release()
    return release["version"] if release and release.get("version") else None


def is_update_available():
    """Returns (True, latest_version) if an update is available, else (False, latest_version)."""
    latest = get_latest_version()
    if not latest:
        return False, None
    if _parse_version(latest) is None:
        logger.warning("Could not parse version strings: local=%r latest=%r", __version__, latest)
        return False, latest
    return _is_newer(latest), latest


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

        # 1. Resolve the release once: version, asset URL and digest all come from it.
        release = _fetch_latest_release()
        if not release or not release.get("url"):
            logger.error("Update aborted: latest release information is unavailable.")
            return False
        if not _is_newer(release["version"]):
            logger.warning("Update aborted: release %s is not newer than %s.", release["version"], __version__)
            return False
        if not release.get("sha256"):
            # Never install (and run elevated) a binary that cannot be verified.
            logger.error("Update aborted: release %s has no published SHA-256 digest.", release["version"])
            return False

        # 2. Download that release's EXE
        logger.info(f"Downloading update {release['version']} from {release['url']}...")
        response = requests.get(release["url"], stream=True, timeout=30)
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

        # 3. Integrity checks: real PE binary + the release's published SHA-256 (required).
        if not _valid_download(new_exe):
            _safe_remove(new_exe)
            return False

        expected_sha = release["sha256"]
        actual_sha = _file_sha256(new_exe)
        if actual_sha != expected_sha:
            logger.error(
                "Update checksum mismatch (expected %s, got %s)", expected_sha, actual_sha
            )
            _safe_remove(new_exe)
            return False
        logger.info("Update SHA-256 verified.")

        # Remove "Mark of the Web" — Windows blocks DLL extraction from internet-downloaded
        # exes. Deleting the Zone.Identifier stream is all Unblock-File does; the old
        # PowerShell call interpolated the path into a command string ($ / backtick
        # in a folder name were expanded).
        try:
            zone_file = new_exe + ":Zone.Identifier"
            if os.path.exists(zone_file):
                os.remove(zone_file)
        except Exception:
            pass

        # 4. In-place swap with rollback.
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

            # 5. Launch the new version (UAC elevation, detached from this job).
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

            # 6. Release hardware resources before terminating.
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
