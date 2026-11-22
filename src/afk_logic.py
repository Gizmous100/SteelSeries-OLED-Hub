"""Pure AFK / idle helpers.

These functions contain the decision logic used by the render loop. They are
kept free of Win32 calls so they can be unit-tested directly instead of being
re-implemented inside the test suite.
"""

# Minimum AFK timeout accepted by the settings GUI (5 seconds).
MIN_AFK_TIMEOUT_MS = 5000

# A mouse movement of this many pixels counts as deliberate activity.
CURSOR_MOVE_PIXELS = 3

# A movement of this many pixels wakes the screen from AFK.
WAKE_CURSOR_PIXELS = 10


def afk_timeout_ms(minutes) -> int:
    """Convert an AFK timeout expressed in minutes to milliseconds."""
    try:
        value = float(minutes)
    except (TypeError, ValueError):
        value = 5.0
    return max(int(value * 60 * 1000), MIN_AFK_TIMEOUT_MS)


def is_gamepad_or_external_active(hook_idle_ms, cursor_idle_ms, win_idle_ms) -> bool:
    """Detect controllers / external devices that reset GetLastInputInfo."""
    return min(hook_idle_ms, cursor_idle_ms) > 5000 and win_idle_ms < 2500


def compute_idle_ms(win_idle_ms, hook_idle_ms, cursor_idle_ms, decoupled=False) -> int:
    """Return the idle time used for AFK decisions.

    When a gamepad or external device keeps resetting the Windows idle timer,
    only physical keyboard/mouse activity is considered.
    """
    if decoupled:
        return min(hook_idle_ms, cursor_idle_ms)
    return min(win_idle_ms, hook_idle_ms, cursor_idle_ms)


def cursor_moved(prev_pos, cur_pos, threshold=CURSOR_MOVE_PIXELS) -> bool:
    """True when the cursor moved at least ``threshold`` pixels."""
    if not cur_pos or cur_pos == (0, 0) or not prev_pos or prev_pos == (0, 0):
        return False
    dx = abs(cur_pos[0] - prev_pos[0])
    dy = abs(cur_pos[1] - prev_pos[1])
    return dx >= threshold or dy >= threshold


def wake_reason(
    now_ms,
    settled_time,
    last_physical_key_ms,
    last_physical_mouse_click_ms,
    cursor_pos,
    baseline_cursor_pos,
    win_idle_ms,
    last_input_tick,
    baseline_input_tick,
    decoupled=False,
):
    """Return a human-readable wake reason, or None to stay in AFK."""
    if last_physical_key_ms > settled_time:
        return "keyboard keypress"
    if last_physical_mouse_click_ms > settled_time:
        return "mouse click/wheel"

    if cursor_pos and cursor_pos != (0, 0):
        if not baseline_cursor_pos or baseline_cursor_pos == (0, 0):
            return None
        dx = abs(cursor_pos[0] - baseline_cursor_pos[0])
        dy = abs(cursor_pos[1] - baseline_cursor_pos[1])
        if dx >= WAKE_CURSOR_PIXELS or dy >= WAKE_CURSOR_PIXELS:
            return f"mouse movement (dx={dx}, dy={dy})"
        return None

    if (
        not decoupled
        and win_idle_ms < 500
        and last_input_tick > baseline_input_tick
        and (now_ms - settled_time) > 1000
    ):
        return f"user input detected (idle={win_idle_ms}ms)"

    return None
