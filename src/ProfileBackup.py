"""
ProfileBackup - Backup and restore SteelSeries GG profiles.
Protects your keyboard macros, bindings, and lighting settings.
"""

import os
import shutil
try:
    import sqlite3
except Exception:
    sqlite3 = None
import logging
from datetime import datetime
from typing import List, Tuple, Optional

logger = logging.getLogger("OLED Customizer.ProfileBackup")

# SteelSeries database paths
PROGRAMDATA = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
SS_BASE = os.path.join(PROGRAMDATA, "SteelSeries", "GG")

DATABASES = {
    "engine": os.path.join(SS_BASE, "apps", "engine", "db", "database.db"),
    "prism": os.path.join(SS_BASE, "apps", "engine", "prism", "db", "database.db"),
    "sonar": os.path.join(SS_BASE, "apps", "sonar", "db", "database.db"),
}

# Any of these means SteelSeries GG is still writing to its databases.
GG_PROCESS_NAMES = [
    "SteelSeriesGG.exe",
    "SteelSeriesGGClient.exe",
    "SteelSeriesGGEZ.exe",
    "SteelSeriesEngine.exe",
    "SteelSeriesEngine3.exe",
    "SteelSeriesPrism.exe",
    "SteelSeriesSonar.exe",
]


def is_gg_running() -> bool:
    """Return True when any SteelSeries GG component is running."""
    from src.utils import is_process_running
    return is_process_running(GG_PROCESS_NAMES)


def _sqlite_uri(db_path: str) -> str:
    from urllib.parse import quote
    return "file:" + quote(db_path.replace("\\", "/")) + "?mode=ro"


def _copy_database(src: str, dest: str) -> bool:
    """Copy a SQLite database consistently, including committed WAL pages.

    Falls back to a byte copy (plus -wal/-shm sidecars) when the sqlite3
    module is unavailable or the database cannot be opened.
    """
    if sqlite3 is not None:
        src_conn = None
        dst_conn = None
        try:
            src_conn = sqlite3.connect(_sqlite_uri(src), uri=True, timeout=2.0)
            dst_conn = sqlite3.connect(dest, timeout=2.0)
            src_conn.backup(dst_conn)
            dst_conn.commit()
            return True
        except Exception as e:
            logger.warning("SQLite backup API failed for %s (%s); using file copy", src, e)
        finally:
            for conn in (dst_conn, src_conn):
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

    shutil.copy2(src, dest)
    for suffix in ("-wal", "-shm"):
        sidecar = src + suffix
        if os.path.exists(sidecar):
            shutil.copy2(sidecar, dest + suffix)
    _merge_wal_into(dest)
    return True


def _merge_wal_into(db_file: str):
    """Fold a copied -wal sidecar into the database file itself.

    A raw copy keeps committed pages in '<db>-wal'; restore_profiles() copies the
    .db file, so without this those changes would be lost. Best effort: without
    sqlite3 the sidecars stay next to the backup and are restored with it.
    """
    if sqlite3 is None or not os.path.exists(db_file + "-wal"):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_file, timeout=2.0)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")  # removes the sidecars on close
    except Exception as e:
        logger.warning("Could not merge WAL into backup %s: %s", db_file, e)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def get_backup_dir() -> str:
    """Get the backup directory path."""
    from src.utils import fetch_app_data_path
    return fetch_app_data_path("backups")


def list_backups() -> List[Tuple[str, str, int, float]]:
    """
    List all available backups.
    
    Returns:
        List of (backup_name, full_path, file_count, mtime) tuples, newest first.
    """
    backup_dir = get_backup_dir()
    if not os.path.exists(backup_dir):
        return []
    
    backups = []
    for name in os.listdir(backup_dir):
        path = os.path.join(backup_dir, name)
        if os.path.isdir(path):
            files = [f for f in os.listdir(path) if f.endswith(".db")]
            mtime = os.path.getmtime(path)
            backups.append((name, path, len(files), mtime))
    
    # Sort by modification time, newest first
    backups.sort(key=lambda x: x[3], reverse=True)
    return backups


def backup_profiles(max_backups: int = 5) -> Optional[str]:
    """
    Backup SteelSeries profile databases.
    
    Args:
        max_backups: Maximum number of backups to keep.
    
    Returns:
        Path to the backup folder, or None if failed.
    """
    # Create backup folder with timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_dir = get_backup_dir()
    backup_path = os.path.join(backup_dir, timestamp)
    
    try:
        os.makedirs(backup_path, exist_ok=True)
        
        backed_up = 0
        for name, db_path in DATABASES.items():
            if os.path.exists(db_path):
                dest = os.path.join(backup_path, f"{name}_database.db")
                _copy_database(db_path, dest)
                size_mb = os.path.getsize(dest) / 1024 / 1024
                logger.info(f"Backed up {name}: {size_mb:.1f} MB")
                backed_up += 1
        
        if backed_up == 0:
            logger.warning("No databases found to backup")
            os.rmdir(backup_path)
            return None
        
        logger.info(f"Backup complete: {backup_path}")
        
        # Cleanup old backups
        _cleanup_old_backups(max_backups)
        
        return backup_path
        
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        return None


def _cleanup_old_backups(max_backups: int):
    """Remove old backups beyond the limit."""
    backups = list_backups()
    while len(backups) > max_backups:
        _, old_path, _, _ = backups.pop()  # Remove oldest (name, path, count, mtime)
        try:
            shutil.rmtree(old_path)
            logger.info(f"Removed old backup: {old_path}")
        except Exception as e:
            logger.warning(f"Failed to remove old backup: {e}")


def restore_profiles(backup_path: str) -> bool:
    """
    Restore SteelSeries profiles from a backup.
    
    SAFETY: Creates a backup of current databases before restoring!
    Note: Caller should ensure SteelSeries GG is closed before calling.
    
    Args:
        backup_path: Path to the backup folder.
    
    Returns:
        True if successful, False otherwise.
    """
    if not os.path.exists(backup_path):
        logger.error(f"Backup not found: {backup_path}")
        return False

    if is_gg_running():
        logger.error("SteelSeries GG is running! Close it before restoring profiles.")
        return False

    try:
        # Determine what can actually be restored before touching anything.
        restorable = [
            (name, db_path, os.path.join(backup_path, f"{name}_database.db"))
            for name, db_path in DATABASES.items()
            if os.path.exists(os.path.join(backup_path, f"{name}_database.db"))
        ]
        if not restorable:
            logger.warning("No database files found in backup")
            return False

        # SAFETY: Create a backup of current databases BEFORE restoring
        logger.info("Creating safety backup before restore...")
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        safety_backup_dir = os.path.join(get_backup_dir(), f"pre_restore_{timestamp}")
        os.makedirs(safety_backup_dir, exist_ok=True)
        
        for name, db_path in DATABASES.items():
            if os.path.exists(db_path):
                dest = os.path.join(safety_backup_dir, f"{name}_database.db")
                _copy_database(db_path, dest)
                logger.info(f"Safety backup: {name} -> {dest}")
        
        logger.info(f"Safety backup created: {safety_backup_dir}")
        
        # Now restore from backup. WAL/SHM sidecars belong to the current
        # database, so they are removed only for files we are about to replace.
        restored = 0
        for name, db_path, backup_file in restorable:
            os.makedirs(os.path.dirname(db_path), exist_ok=True)

            for suffix in ("-wal", "-shm"):
                sidecar = db_path + suffix
                if os.path.exists(sidecar):
                    try:
                        os.remove(sidecar)
                        logger.info(f"Removed {suffix} file: {sidecar}")
                    except Exception as e:
                        logger.warning(f"Could not remove {sidecar}: {e}")

            # Copy to a temporary sibling first so a failed copy never leaves a
            # half-written database in place.
            tmp_path = db_path + ".restore_tmp"
            shutil.copy2(backup_file, tmp_path)
            os.replace(tmp_path, db_path)
            # A backup whose WAL could not be merged keeps committed pages in its
            # -wal sidecar: restore it together with the database file.
            if os.path.exists(backup_file + "-wal"):
                shutil.copy2(backup_file + "-wal", db_path + "-wal")
            logger.info(f"Restored {name} database")
            restored += 1

        logger.info(f"Restore complete! Restored {restored} databases.")
        logger.info(f"If something went wrong, you can restore from: {safety_backup_dir}")
        return True
        
    except Exception as e:
        logger.error(f"Restore failed: {e}")
        return False


def vacuum_databases() -> Tuple[float, float]:
    """
    Compact SteelSeries databases to reduce size.
    
    IMPORTANT: SteelSeries GG must be stopped!
    
    Returns:
        Tuple of (size_before_mb, size_after_mb).
    """
    if is_gg_running():
        logger.error("SteelSeries GG is running! Please close it before compacting.")
        return (0, 0)
    
    size_before = 0
    size_after = 0
    
    for name, db_path in DATABASES.items():
        if not os.path.exists(db_path):
            continue
            
        try:
            before = os.path.getsize(db_path)
            size_before += before
            
            if sqlite3:
                conn = sqlite3.connect(db_path)
                conn.execute("VACUUM")
                conn.close()
            
            after = os.path.getsize(db_path)
            size_after += after
            
            saved = (before - after) / 1024 / 1024
            logger.info(f"Vacuumed {name}: saved {saved:.1f} MB")
            
        except Exception as e:
            logger.error(f"Failed to vacuum {name}: {e}")
    
    return (size_before / 1024 / 1024, size_after / 1024 / 1024)


def get_database_sizes() -> dict:
    """Get current sizes of all databases."""
    sizes = {}
    for name, db_path in DATABASES.items():
        if os.path.exists(db_path):
            sizes[name] = os.path.getsize(db_path) / 1024 / 1024
    return sizes


def delete_backup(backup_path: str) -> bool:
    """Delete a backup folder."""
    logger.info(f"Backend: Attempting to delete backup at: {backup_path}")
    if not os.path.exists(backup_path):
        logger.error(f"Backend: Backup path does not exist: {backup_path}")
        return False
    try:
        shutil.rmtree(backup_path)
        logger.info(f"Backend: Deleted backup: {backup_path}")
        return True
    except Exception as e:
        logger.error(f"Backend: Failed to delete backup {backup_path}: {e}")
        return False


def duplicate_backup(backup_path: str) -> Optional[str]:
    """Duplicate a backup folder with a '_copy' suffix."""
    if not os.path.exists(backup_path):
        return None
    
    try:
        # Create new name
        base_dir = os.path.dirname(backup_path)
        base_name = os.path.basename(backup_path)
        
        # If it already ends in a timestamp or something, just append copy
        new_name = base_name + "_copy"
        new_path = os.path.join(base_dir, new_name)
        
        # Handle collision
        counter = 1
        while os.path.exists(new_path):
            new_path = os.path.join(base_dir, f"{new_name}_{counter}")
            counter += 1
            
        shutil.copytree(backup_path, new_path)
        logger.info(f"Duplicated backup: {backup_path} -> {new_path}")
        return new_path
    except Exception as e:
        logger.error(f"Failed to duplicate backup {backup_path}: {e}")
        return None
