"""
Shared HTTP sessions: keep-alive connections and one TLS setup per host instead of a new connection (and a fresh
certificate-store load, ~0.5 s on Windows) for every request. NSE cookies are kept and refreshed only when rejected.
"""
import threading

import requests
from requests.adapters import HTTPAdapter

NSE_HOME = "https://www.nseindia.com/"
_lock = threading.Lock()
_sessions = {}
_nse_lock = threading.Lock()  # separate from _lock: session() takes _lock and Lock is not re-entrant
_gen = [0]  # bumped on every NSE cookie refresh


def session(name="default"):
    """Process-wide requests.Session per name, with a connection pool sized for the scan thread pool."""
    with _lock:
        s = _sessions.get(name)
        if s is None:
            s = requests.Session()
            ad = HTTPAdapter(pool_connections=8, pool_maxsize=16)
            s.mount("https://", ad)
            s.mount("http://", ad)
            _sessions[name] = s
        return s


def _prime(s, headers, timeout, gen):
    """Fresh NSE cookies from the home page, once per rejection even when several threads hit it together."""
    with _nse_lock:
        if _gen[0] != gen:  # another thread already refreshed them
            return
        s.cookies.clear()
        try:
            s.get(NSE_HOME, headers=headers, timeout=timeout)
        except requests.RequestException:
            pass  # the API call itself may still work without cookies
        _gen[0] += 1


def nse_get(url, headers, timeout=8):
    """GET an NSE JSON API with the shared cookie session; fetch the home page for cookies only when needed.
    A rejected cookie gets 401/403, an HTML page or a stalled connection (Akamai tarpit): all three refresh once."""
    s = session("nse")
    gen = _gen[0]
    if not s.cookies:
        _prime(s, headers, timeout, gen)
        gen = _gen[0]
    try:
        r = s.get(url, headers=headers, timeout=min(timeout, 4))  # short first try: a tarpit costs 4 s, not 8
        bad = r.status_code in (401, 403) or "json" not in r.headers.get("content-type", "")
    except (requests.Timeout, requests.ConnectionError):
        bad = True
    if bad:
        _prime(s, headers, timeout, gen)
        r = s.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()
