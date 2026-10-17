"""
Upstox API adapter: daily OAuth login, candles (history + today's intraday) and the option chain with IV, greeks
and previous-day OI, in the shapes the app expects.

Setup (once):
  1. Create an app at https://account.upstox.com/developer/apps
  2. Redirect URL: the address the app runs on, e.g. http://localhost:8502/  (server: https://<your-domain>/)
  3. .streamlit/secrets.toml:
        [upstox]
        api_key = "..."
        api_secret = "..."
        redirect_uri = "http://localhost:8502/"
Upstox access tokens expire around 03:30 IST every day, so the app asks you to log in once a day.
"""
import gzip
import io
import json
import os
import secrets
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

import net

IST = "Asia/Kolkata"
API = "https://api.upstox.com"
SESSION_FILE = Path(os.environ.get("TRAD_DATA_DIR") or Path(__file__).parent) / ".upstox_session.json"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/{ex}.json.gz"
INDEX_KEYS = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank",
              "FINNIFTY": "NSE_INDEX|Nifty Fin Service", "SENSEX": "BSE_INDEX|SENSEX"}
VIX_KEY = "NSE_INDEX|India VIX"
MCX_MAP = {"CRUDE": "CRUDEOIL", "CRUDEOIL": "CRUDEOIL", "NATURALGAS": "NATURALGAS", "NATGAS": "NATURALGAS"}
UNITS = {"1m": 1, "5m": 5, "15m": 15}
PERIOD_DAYS = {"5d": 7, "7d": 9, "1mo": 31, "30d": 31, "60d": 60}
_last = [0.0]
_rate_lock = threading.Lock()


def credentials():
    key, sec, redir = (os.environ.get("UPSTOX_API_KEY"), os.environ.get("UPSTOX_API_SECRET"),
                       os.environ.get("UPSTOX_REDIRECT_URI"))
    try:
        cfg = st.secrets["upstox"]
        key, sec, redir = key or cfg["api_key"], sec or cfg["api_secret"], redir or cfg.get("redirect_uri")
    except Exception:
        pass
    return key, sec, redir or "http://localhost:8502/"


def _token_day():
    """Tokens expire ~03:30 IST: a token belongs to the 'day' starting 03:30."""
    return (pd.Timestamp.now(tz=IST) - pd.Timedelta(hours=3, minutes=30)).date().isoformat()


def _saved_token():
    try:
        j = json.loads(SESSION_FILE.read_text(encoding="utf8"))
        return j["access_token"] if j.get("date") == _token_day() else None
    except Exception:
        return None


def login_url():
    key, _, redir = credentials()
    state = "upstox-" + secrets.token_urlsafe(8)
    return (f"{API}/v2/login/authorization/dialog?response_type=code&client_id={urllib.parse.quote(key)}"
            f"&redirect_uri={urllib.parse.quote(redir, safe='')}&state={state}")


def api(token, path, params=None):
    """GET an Upstox endpoint -> 'data' payload. Raises on HTTP/API errors. Thread-safe."""
    with _rate_lock:  # space calls out across the scan threads to stay well under the per-second limits
        wait = _last[0] + 0.12 - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    r = net.session("upstox").get(API + path, params=params, timeout=12,
                                  headers={"Accept": "application/json", "Authorization": f"Bearer {token}"})
    j = r.json()
    if not r.ok or j.get("status") != "success":
        raise RuntimeError(f"Upstox {path}: {r.status_code} {str(j)[:200]}")
    return j["data"]


def _exchange_code(code):
    key, sec, redir = credentials()
    r = requests.post(f"{API}/v2/login/authorization/token", timeout=12,
                      headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                      data={"code": code, "client_id": key, "client_secret": sec, "redirect_uri": redir,
                            "grant_type": "authorization_code"})
    j = r.json()
    if not r.ok or "access_token" not in j:
        raise RuntimeError(str(j)[:200])
    return j


def analytics_token():
    """Long-lived read-only market-data token ([upstox] analytics_token or env UPSTOX_ANALYTICS_TOKEN)."""
    tok = os.environ.get("UPSTOX_ANALYTICS_TOKEN")
    if not tok:
        try:
            tok = st.secrets["upstox"].get("analytics_token")
        except Exception:
            tok = None
    return tok or None


def token_expiry(token):
    """Expiry date from the JWT payload (no verification; display only)."""
    try:
        import base64
        part = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return datetime.fromtimestamp(payload["exp"])
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _check(token):
    """Validate a token with a market-data call (profile needs a static IP for some token types)."""
    try:
        api(token, "/v2/market-quote/ltp", {"instrument_key": INDEX_KEYS["NIFTY"]})
        return True
    except Exception:
        return False


def session():
    """-> (token or None, status text, login URL or None). Uses the analytics token when configured, otherwise the
    daily OAuth login (completing the redirect ?code=...&state=upstox-...)."""
    at = analytics_token()
    if at:
        if not _check(at):
            return None, "Analytics token rejected or expired: generate a new one in the Upstox developer console", None
        exp = token_expiry(at)
        return at, "Connected · analytics token" + (f" (valid till {exp:%d %b %Y})" if exp else ""), None
    key, sec, _ = credentials()
    if not key or not sec:
        return None, "No Upstox token or api_key/api_secret: see .streamlit/secrets.toml", None
    qp = st.query_params
    if qp.get("code") and str(qp.get("state", "")).startswith("upstox"):
        try:
            j = _exchange_code(qp["code"])
            SESSION_FILE.write_text(json.dumps({"access_token": j["access_token"], "date": _token_day(),
                                                "user": j.get("user_name", "")}), encoding="utf8")
        except Exception as e:
            st.query_params.clear()
            return None, f"Upstox login failed: {e}", login_url()
        st.query_params.clear()
    token = _saved_token()
    if not token:
        return None, "Not logged in to Upstox today", login_url()
    if not _check(token):
        return None, "Upstox session expired or invalid", login_url()
    try:
        user = json.loads(SESSION_FILE.read_text(encoding="utf8")).get("user") or "Upstox user"
    except Exception:
        user = "Upstox user"
    return token, f"Connected as {user}", None


def headless():
    """Token for the background runner: the analytics token, else today's saved OAuth token. None if unavailable."""
    return analytics_token() or _saved_token()


def logout():
    try:
        SESSION_FILE.unlink()
    except FileNotFoundError:
        pass


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def instruments(exchange, day):
    raw = requests.get(INSTRUMENTS_URL.format(ex=exchange), timeout=60).content
    df = pd.DataFrame(json.load(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf8")))
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def underlying(name, kind, day):
    """App symbol -> dict(key, chain_key, inr) or None."""
    n = name.upper()
    try:
        if n in INDEX_KEYS:
            return dict(key=INDEX_KEYS[n], chain_key=INDEX_KEYS[n])
        if n in MCX_MAP:
            df = instruments("MCX", day)
            sym = MCX_MAP[n]
            fut = df[(df.instrument_type == "FUT") & (
                (df.get("underlying_symbol", pd.Series(index=df.index, dtype=object)) == sym)
                | df.trading_symbol.str.split(" ").str[0].eq(sym))]
            fut = fut[pd.to_datetime(fut.expiry, unit="ms").dt.date >= date.today()].sort_values("expiry")
            if fut.empty:
                return None
            return dict(key=fut.iloc[0].instrument_key, chain_key=None)  # Upstox option-chain API covers NSE only
        if kind == "stock":
            df = instruments("NSE", day)
            r = df[(df.segment == "NSE_EQ") & (df.trading_symbol == n)]
            if r.empty:
                return None
            return dict(key=r.iloc[0].instrument_key, chain_key=r.iloc[0].instrument_key)
    except Exception:
        return None
    return None


def _to_df(candles):
    if not candles:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    df = pd.DataFrame([c[:6] for c in candles], columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("Date")))
    return df.astype(float)


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=500)
def _history(token, key, unit, days, day):
    """Completed past-day candles (v3 historical, ≤1 month per call). They don't change during the day, so they
    are fetched once per day instead of on every refresh."""
    k = urllib.parse.quote(key, safe="")
    today = date.fromisoformat(day)
    end, start = today - timedelta(days=1), today - timedelta(days=days)
    parts = []
    while end >= start:
        frm = max(start, end - timedelta(days=28))
        d = api(token, f"/v3/historical-candle/{k}/minutes/{unit}/{end:%Y-%m-%d}/{frm:%Y-%m-%d}")
        parts.append(_to_df(d.get("candles", [])))
        end = frm - timedelta(days=1)
    return pd.concat(parts) if parts else _to_df([])


@st.cache_data(ttl=60, show_spinner=False)
def candles(token, key, interval, period):
    """Past days (cached for the day) + today's intraday candles (refetched each minute), oldest first."""
    unit = UNITS[interval]
    k = urllib.parse.quote(key, safe="")
    day = pd.Timestamp.now(tz=IST).date().isoformat()  # IST trading day, also on UTC servers (Docker/Cloud)
    parts = [_history(token, key, unit, PERIOD_DAYS.get(period, 31), day)]
    try:
        parts.append(_to_df(api(token, f"/v3/historical-candle/intraday/{k}/minutes/{unit}").get("candles", [])))
    except Exception:
        pass  # before market open / holiday
    df = pd.concat(parts) if parts else pd.DataFrame()
    return df[~df.index.duplicated()].sort_index() if len(df) else df


@st.cache_data(ttl=10, show_spinner=False)
def quotes(token, keys):
    """Full market quotes for up to ~500 instrument keys in one call -> {instrument_key: quote dict}.
    Each quote: last_price, net_change, ohlc, volume, average_price, depth{buy,sell}[5], circuits, last_trade_time."""
    out = {}
    keys = [k for k in keys if k]
    for i in range(0, len(keys), 450):
        try:
            data = api(token, "/v2/market-quote/quotes", {"instrument_key": ",".join(keys[i:i + 450])})
        except Exception:
            continue
        for v in data.values():
            if v.get("instrument_token"):
                out[v["instrument_token"]] = v
    return out


@st.cache_data(ttl=60, show_spinner=False)
def option_chain(token, chain_key, day, min_expiry=None):
    """Nearest expiry chain (or the first expiry on/after min_expiry 'YYYY-MM-DD') -> dict(expiry 'DD-Mon-YYYY', lot, quotes {strike: {CE/PE: ltp, bid, ask, iv, oi, doi,
    delta, theta, pop, gamma, vega, vol, cp (prev close), instrument_key}}, spot) or None."""
    try:
        contracts = api(token, "/v2/option/contract", {"instrument_key": chain_key})
        floor = max(date.today().isoformat(), min_expiry or "")
        exps = sorted({c["expiry"] for c in contracts if c.get("expiry") and c["expiry"] >= floor})
        if not exps:
            return None
        exp = exps[0]
        lot = next((int(c["lot_size"]) for c in contracts if c.get("expiry") == exp and c.get("lot_size")), None)
        rows = api(token, "/v2/option/chain", {"instrument_key": chain_key, "expiry_date": exp})
        quotes, spot = {}, None
        for r in rows:
            spot = r.get("underlying_spot_price") or spot
            for side, fld in (("CE", "call_options"), ("PE", "put_options")):
                o = r.get(fld) or {}
                md, gk = o.get("market_data") or {}, o.get("option_greeks") or {}
                if not md:
                    continue
                oi, prev = md.get("oi") or 0, md.get("prev_oi")
                quotes.setdefault(float(r["strike_price"]), {})[side] = dict(
                    ltp=md.get("ltp") or 0.0, bid=md.get("bid_price") or 0.0, ask=md.get("ask_price") or 0.0,
                    iv=gk.get("iv") or 0.0, oi=oi, doi=(oi - prev) if prev is not None else 0,
                    delta=gk.get("delta"), theta=gk.get("theta"), pop=gk.get("pop"), gamma=gk.get("gamma"),
                    vega=gk.get("vega"), vol=md.get("volume"), cp=md.get("close_price"),
                    instrument_key=o.get("instrument_key"))
        if not quotes:
            return None
        return dict(expiry=datetime.strptime(exp, "%Y-%m-%d").strftime("%d-%b-%Y"), lot=lot, quotes=quotes, spot=spot)
    except Exception:
        return None
