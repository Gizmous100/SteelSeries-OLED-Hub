import asyncio
import time
from datetime import datetime, timezone
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus
import logging

logger = logging.getLogger("OLED Customizer.WindowsMedia")

# Only these statuses mean the session has real, active media
VALID_STATUSES = {
    GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus.PAUSED,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus.CHANGING,
}

class WindowsMedia:
    def __init__(self):
        self.manager = None
        self._logged_once = set()
        self._props_cache = {}
        self._last_playing_by_source = {}

    def _log_once(self, key, msg, *args):
        """Log a recurring failure only the first time.

        This runs in a 5 Hz poll loop: a permanent failure (e.g. a missing
        winrt submodule) would otherwise write thousands of identical lines
        and push real diagnostics out of the rotating log.
        """
        if key in self._logged_once:
            return
        self._logged_once.add(key)
        logger.debug(msg + " (further identical messages suppressed)", *args)

    def _is_recently_playing(self, source_id, now_ts=None):
        if not source_id:
            return False
        if now_ts is None:
            now_ts = time.monotonic()
        last_ts = self._last_playing_by_source.get(source_id, 0.0)
        return (now_ts - last_ts) <= 5.0

    def _is_valid_session(self, session):
        """Check if an SMTC session is real and not stale/garbage.
        Returns False for dead sessions like Discord showing 0:15/0:15."""
        try:
            info = session.get_playback_info()
            if not info:
                return False
            
            status = info.playback_status
            source_id = (session.source_app_user_model_id or "").lower()
            now_ts = time.monotonic()

            if status == GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
                self._last_playing_by_source[source_id] = now_ts
                return True

            # Allow transitional CHANGING/PAUSED state if this source was playing within the last 5s
            if status in VALID_STATUSES and self._is_recently_playing(source_id, now_ts):
                return True
            
            # Filter 1: Only trust PLAYING or PAUSED sessions.
            # STOPPED, CLOSED, OPENED, CHANGING are dead/transitional.
            if status not in VALID_STATUSES:
                return False
            
            # Filter 2: Timeline sanity — if position == duration and not playing,
            # the track is finished/stuck (like Discord's 0:15/0:15).
            try:
                timeline = session.get_timeline_properties()
                if timeline and status != GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
                    pos = timeline.position
                    end = timeline.end_time
                    # Convert to seconds for comparison
                    pos_s = pos.total_seconds() if hasattr(pos, 'total_seconds') else (pos / 10_000_000 if isinstance(pos, int) else 0)
                    end_s = end.total_seconds() if hasattr(end, 'total_seconds') else (end / 10_000_000 if isinstance(end, int) else 0)
                    if end_s > 0 and abs(pos_s - end_s) < 0.5:
                        # Position is at the end — track is finished, skip it
                        return False
            except Exception as e:
                self._log_once("timeline", "Timeline check failed: %s", e)  # best-effort

            # Filter 3: Staleness — if last update was >60s ago and not playing,
            # the session is stale. Also check our own _last_playing_by_source timestamp
            # because UWP video apps (e.g. Prime Video) only update timeline.last_updated_time on seek.
            if status != GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
                try:
                    timeline = session.get_timeline_properties()
                    if timeline and hasattr(timeline, 'last_updated_time') and timeline.last_updated_time:
                        age = (datetime.now(timezone.utc) - timeline.last_updated_time).total_seconds()
                        last_play = self._last_playing_by_source.get(source_id, 0.0)
                        if last_play > 0:
                            age = min(age, now_ts - last_play)
                        if age > 60:
                            return False
                except Exception as e:
                    self._log_once("staleness", "Staleness check failed: %s", e)  # best-effort

            return True
        except (OSError, RuntimeError):
            return False
        except Exception:
            return False

    async def _ensure_manager(self):
        if self.manager:
            return
        try:
            logger.info("Requesting MediaManager...")
            # Add timeout to prevent hang
            self.manager = await asyncio.wait_for(
                GlobalSystemMediaTransportControlsSessionManager.request_async(),
                timeout=2.0
            )
            logger.info("MediaManager obtained successfully")
        except asyncio.TimeoutError:
            logger.error("MediaManager request TIMED OUT")
        except Exception as e:
            logger.warning(f"Failed to request MediaManager: {e}")

    async def get_media_info(self):
        try:
            await self._ensure_manager()
            if not self.manager:
                return {}
            
            # Try to get ALL sessions and find the one that is actually playing.
            # This fixes the issue where Windows thinks a paused background tab is "current".
            sessions = None
            try:
                sessions = self.manager.get_sessions()
            except (OSError, RuntimeError):
                # COM threading error - manager may be stale
                self.manager = None
                return {}
            except Exception as e:
                self._log_once("get_sessions", "Session validation failed: %s", e)

            current_session = None
            now_ts = time.monotonic()
            
            # Priority 1: Find a PLAYING session (that passes validation)
            if sessions:
                for session in sessions:
                    try:
                        if not self._is_valid_session(session):
                            continue
                        info = session.get_playback_info()
                        if info and info.playback_status == GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
                            current_session = session
                            break
                    except (OSError, RuntimeError):
                        # Skip sessions with COM errors
                        continue
            
            # Priority 1b: If no PLAYING session, check if the system current session is valid & playing/changing
            if not current_session:
                try:
                    fallback = self.manager.get_current_session()
                    if fallback and self._is_valid_session(fallback):
                        fb_info = fallback.get_playback_info()
                        if fb_info and fb_info.playback_status in (
                            GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING,
                            GlobalSystemMediaTransportControlsSessionPlaybackStatus.CHANGING,
                        ):
                            current_session = fallback
                except (OSError, RuntimeError):
                    self.manager = None
                    return {}

            # Priority 2: Fallback — prefer the valid session that was most recently playing
            # so pausing/buffering Prime Video doesn't jump to a paused background browser tab.
            if not current_session and sessions:
                best_session = None
                best_last_play = -1.0
                for session in sessions:
                    try:
                        if self._is_valid_session(session):
                            sid = (session.source_app_user_model_id or "").lower()
                            lp = self._last_playing_by_source.get(sid, 0.0)
                            if lp > best_last_play:
                                best_last_play = lp
                                best_session = session
                    except (OSError, RuntimeError):
                        continue
                current_session = best_session
            
            # Priority 3: Fallback to system "Current" session (also validated)
            if not current_session:
                try:
                    fallback = self.manager.get_current_session()
                    if fallback and self._is_valid_session(fallback):
                        current_session = fallback
                except (OSError, RuntimeError):
                    self.manager = None
                    return {}
            
            # If still nothing, bail
            if not current_session:
                return {}

            source = (current_session.source_app_user_model_id or "").lower()
            playback_info = current_session.get_playback_info()
            status = playback_info.playback_status if playback_info else None

            paused = True
            if status == GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
                paused = False
                self._last_playing_by_source[source] = now_ts
            elif status == GlobalSystemMediaTransportControlsSessionPlaybackStatus.CHANGING and self._is_recently_playing(source, now_ts):
                paused = False

            # Throttle cross-process RPC try_get_media_properties_async() to ~1 Hz per source
            # unless status changed or cache is empty, and fall back to cache on transient RPC errors.
            cached = self._props_cache.get(source)
            need_props = (
                cached is None
                or not (cached.get("title") or cached.get("artist"))
                or (now_ts - cached.get("ts", 0.0)) >= 1.0
                or cached.get("status") != status
            )

            title = cached.get("title", "") if cached else ""
            artist = cached.get("artist", "") if cached else ""

            if need_props:
                info = None
                try:
                    info = await asyncio.wait_for(
                        current_session.try_get_media_properties_async(),
                        timeout=1.5
                    )
                except asyncio.TimeoutError:
                    logger.debug("SMTC media properties request TIMED OUT")
                except Exception:
                    pass

                if info and (info.title or info.artist):
                    title = info.title or ""
                    artist = info.artist or ""
                    self._props_cache[source] = {
                        "title": title,
                        "artist": artist,
                        "ts": now_ts,
                        "status": status,
                    }
                elif title or artist:
                    # Keep cached properties and bump timestamp so we don't spam RPC on next tick
                    self._props_cache[source] = {
                        "title": title,
                        "artist": artist,
                        "ts": now_ts,
                        "status": status,
                    }
                else:
                    return {}

            timeline = current_session.get_timeline_properties()
            
            # Timeline might be None or zeros
            # We use -1 to indicate "unknown" so we don't force-reset the player to 0.
            position = -1
            duration = 0
            
            if timeline:
                pos_td = timeline.position
                end_td = timeline.end_time
                
                if hasattr(pos_td, "total_seconds"):
                    position = pos_td.total_seconds()
                elif isinstance(pos_td, int):
                    # 1 tick = 100ns. 10,000,000 ticks = 1s.
                    position = pos_td / 10_000_000
                elif hasattr(pos_td, "duration"): 
                     position = pos_td.duration / 10_000_000
                
                # EXTRAPOLATION: SMTC updates position only on state change.
                # We must add (Now - LastUpdatedTime) if playing.
                if (not paused) and hasattr(timeline, "last_updated_time"):
                     last_updated = timeline.last_updated_time
                     if last_updated:
                         now = datetime.now(timezone.utc)
                         diff = (now - last_updated).total_seconds()
                         if diff > 0:
                             position += diff

                if hasattr(end_td, "total_seconds"):
                    duration = end_td.total_seconds()
                elif isinstance(end_td, int):
                    duration = end_td / 10_000_000
                elif hasattr(end_td, "duration"):
                     duration = end_td.duration / 10_000_000
                
                # FALLBACK: If duration is 0, check max_seek_time (common in browsers)
                if duration == 0 and hasattr(timeline, "max_seek_time"):
                    mst = timeline.max_seek_time
                    if hasattr(mst, "total_seconds"):
                         duration = mst.total_seconds()
                    elif isinstance(mst, int):
                         duration = mst / 10_000_000
            
            return {
                "title": title,
                "artist": artist,
                "paused": paused,
                "progress": int(position * 1000) if position != -1 else -1,
                "duration": int(duration * 1000) if duration > 0 else -1,
                "source": source
            }

        except Exception:
            # If something fails, return empty
            return {}
