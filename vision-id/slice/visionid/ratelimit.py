"""In-process fixed-window rate limiting + login lockout.

Slice uses process memory (documented gap: production must use Redis so limits
are shared across replicas and survive restarts)."""
import threading
import time

_lock = threading.Lock()
_windows: dict[tuple, list] = {}          # key -> [window_start, count]
_login_failures: dict[tuple, list] = {}    # (ip, email) -> [first_fail_ts, count, locked_until]

LOGIN_MAX_FAILURES = 5
LOGIN_FAILURE_WINDOW = 15 * 60
LOGIN_LOCKOUT = 15 * 60


def _now() -> float:
    return time.time()


def check(key: str, limit: int, window_s: int) -> tuple[bool, int]:
    """Generic fixed window. Returns (allowed, retry_after_seconds)."""
    now = _now()
    with _lock:
        start, count = _windows.get(key, (now, 0))
        if now - start >= window_s:
            start, count = now, 0
        if count >= limit:
            return False, int(window_s - (now - start)) + 1
        _windows[key] = (start, count + 1)
        return True, 0


def login_allowed(ip: str, email: str) -> tuple[bool, int]:
    now = _now()
    with _lock:
        first, count, locked_until = _login_failures.get((ip, email.lower()), (now, 0, 0))
        if locked_until and now < locked_until:
            return False, int(locked_until - now) + 1
        if now - first >= LOGIN_FAILURE_WINDOW:
            _login_failures[(ip, email.lower())] = (now, 0, 0)
        return True, 0


def record_login_failure(ip: str, email: str) -> bool:
    """Returns True if this failure triggered a lockout."""
    now = _now()
    key = (ip, email.lower())
    with _lock:
        first, count, locked_until = _login_failures.get(key, (now, 0, 0))
        if now - first >= LOGIN_FAILURE_WINDOW:
            first, count = now, 0
        count += 1
        triggered = False
        if count >= LOGIN_MAX_FAILURES and not locked_until:
            locked_until = now + LOGIN_LOCKOUT
            triggered = True
        _login_failures[key] = (first, count, locked_until)
        return triggered


def record_login_success(ip: str, email: str):
    with _lock:
        _login_failures.pop((ip, email.lower()), None)


def reset_for_tests():
    with _lock:
        _windows.clear()
        _login_failures.clear()
