"""
Zerodha Kite Connect data adapter: daily login, candles and option-chain quotes in the shapes the app expects.

Setup (once):
  1. Create an app at https://developers.kite.trade (Kite Connect subscription with historical data).
  2. Set its redirect URL to the address the app runs on, e.g. http://localhost:8502/
  3. Put the key and secret in .streamlit/secrets.toml (see secrets.toml.example) or env vars
     KITE_API_KEY / KITE_API_SECRET.
Kite access tokens expire every morning (~06:00 IST), so the app asks you to log in once a day.
"""
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    from kiteconnect import KiteConnect
except ImportError:
    KiteConnect = None

IST = "Asia/Kolkata"
SESSION_FILE = Path(os.environ.get("TRAD_DATA_DIR") or Path(__file__).parent) / ".kite_session.json"
INTERVALS = {"1m": "minute", "5m": "5minute", "15m": "15minute"}
PERIOD_DAYS = {"5d": 7, "7d": 9, "1mo": 31, "30d": 31, "60d": 60}
# app name -> (exchange, underlying tradingsymbol, option name, option exchange)
INDEX_MAP = {"NIFTY": ("NSE", "NIFTY 50", "NIFTY", "NFO"), "BANKNIFTY": ("NSE", "NIFTY BANK", "BANKNIFTY", "NFO"),
             "FINNIFTY": ("NSE", "NIFTY FIN SERVICE", "FINNIFTY", "NFO"), "SENSEX": ("BSE", "SENSEX", "SENSEX", "BFO")}
MCX_MAP = {"CRUDE": "CRUDEOIL", "CRUDEOIL": "CRUDEOIL", "NATURALGAS": "NATURALGAS", "NATGAS": "NATURALGAS"}
_last_call = {"hist": 0.0, "quote": 0.0}


def _throttle(kind, gap):
    """Kite limits: historical ~3 req/s, quote ~1 req/s."""
    wait = _last_call[kind] + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_call[kind] = time.time()


def credentials():
    key, sec = os.environ.get("KITE_API_KEY"), os.environ.get("KITE_API_SECRET")
    try:
        key = key or st.secrets["kite"]["api_key"]
        sec = sec or st.secrets["kite"]["api_secret"]
    except Exception:
        pass
    return key, sec


def _token_day():
    """Trading 'day' for token validity: tokens issued before 06:00 IST belong to the previous day."""
    return (pd.Timestamp.now(tz=IST) - pd.Timedelta(hours=6)).date().isoformat()


def _saved_token():
    try:
        j = json.loads(SESSION_FILE.read_text())
        return j["access_token"] if j.get("date") == _token_day() else None
    except Exception:
        return None


@st.cache_resource
def _client(api_key):
    return KiteConnect(api_key=api_key)


@st.cache_data(ttl=300, show_spinner=False)
def _profile(_kite, token):
    try:
        return _kite.profile().get("user_name") or "Kite user"
    except Exception:
        return None


def session():
    """-> (kite client or None, status text, login URL or None). Completes the login redirect when present."""
    if KiteConnect is None:
        return None, "kiteconnect is not installed (pip install kiteconnect)", None
    key, sec = credentials()
    if not key or not sec:
        return None, "No Kite API key/secret: see .streamlit/secrets.toml.example", None
    kite = _client(key)
    qp = st.query_params
    if qp.get("request_token"):
        try:
            data = kite.generate_session(qp["request_token"], api_secret=sec)
            SESSION_FILE.write_text(json.dumps({"access_token": data["access_token"], "date": _token_day()}))
        except Exception as e:
            st.query_params.clear()
            return None, f"Kite login failed: {e}", kite.login_url()
        st.query_params.clear()
    token = _saved_token()
    if not token:
        return None, "Not logged in to Kite today", kite.login_url()
    kite.set_access_token(token)
    user = _profile(kite, token)
    if not user:
        return None, "Kite session expired or invalid", kite.login_url()
    return kite, f"Connected as {user}", None


def headless():
    """Kite client for the background runner, reusing today's token saved by the app's login. None if unavailable."""
    if KiteConnect is None:
        return None
    key, _ = credentials()
    token = _saved_token()
    if not key or not token:
        return None
    kite = KiteConnect(api_key=key)
    kite.set_access_token(token)
    return kite


def logout():
    try:
        SESSION_FILE.unlink()
    except FileNotFoundError:
        pass


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def instruments(_kite, exchange, day):
    return pd.DataFrame(_kite.instruments(exchange))


@st.cache_data(ttl=3600, show_spinner=False)
def underlying(_kite, name, kind, day):
    """App symbol -> dict(token, exchange, tradingsymbol, opt_name, opt_exch, inr) or None if Kite doesn't list it."""
    n = name.upper()
    try:
        if n in INDEX_MAP:
            ex, ts, on, oe = INDEX_MAP[n]
            df = instruments(_kite, ex, day)
            r = df[df.tradingsymbol == ts]
        elif n in MCX_MAP:
            ex, on, oe = "MCX", MCX_MAP[n], "MCX"
            df = instruments(_kite, ex, day)
            r = df[(df.name == on) & (df.instrument_type == "FUT") & (df.expiry >= date.today())].sort_values("expiry")
        elif kind == "stock":
            ex, on, oe = "NSE", n, "NFO"
            df = instruments(_kite, ex, day)
            r = df[(df.tradingsymbol == n) & (df.segment == "NSE")]
        else:
            return None
        if r.empty:
            return None
        r = r.iloc[0]
        return dict(token=int(r.instrument_token), exchange=ex, tradingsymbol=r.tradingsymbol, opt_name=on, opt_exch=oe)
    except Exception:
        return None


@st.cache_data(ttl=60, show_spinner=False)
def candles(_kite, token, interval, period):
    """OHLCV DataFrame (IST index) like yfinance's; includes the forming candle (the app drops it)."""
    to = datetime.now()
    _throttle("hist", 0.35)
    rows = _kite.historical_data(token, to - timedelta(days=PERIOD_DAYS.get(period, 31)), to, INTERVALS[interval])
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.rename(columns=str.capitalize).set_index("Date")
    df.index = pd.DatetimeIndex(df.index)
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


@st.cache_data(ttl=60, show_spinner=False)
def option_quotes(_kite, opt_name, opt_exch, ref_price, day, width=25):
    """Nearest-expiry CE/PE quotes for ±width strikes around ref_price.
    -> dict(expiry 'DD-Mon-YYYY', lot, exp_hm, quotes {strike: {CE/PE: ltp, bid, ask, oi}}) or None."""
    try:
        df = instruments(_kite, opt_exch, day)
        o = df[(df.name == opt_name) & df.instrument_type.isin(["CE", "PE"])]
        o = o[o.expiry >= date.today()]
        if o.empty:
            return None
        exp = min(o.expiry)
        o = o[o.expiry == exp]
        strikes = sorted(o.strike.unique())
        i = min(range(len(strikes)), key=lambda j: abs(strikes[j] - ref_price))
        keep = set(strikes[max(0, i - width): i + width + 1])
        o = o[o.strike.isin(keep)]
        keys = [f"{opt_exch}:{ts}" for ts in o.tradingsymbol]
        _throttle("quote", 1.0)
        q = _kite.quote(keys)
        quotes = {}
        for r in o.itertuples():
            x = q.get(f"{opt_exch}:{r.tradingsymbol}")
            if not x:
                continue
            dep = x.get("depth") or {}
            bid = (dep.get("buy") or [{}])[0].get("price", 0) or 0
            ask = (dep.get("sell") or [{}])[0].get("price", 0) or 0
            quotes.setdefault(float(r.strike), {})[r.instrument_type] = dict(
                ltp=x.get("last_price") or 0.0, bid=bid, ask=ask, oi=x.get("oi") or 0)
        return dict(expiry=pd.Timestamp(exp).strftime("%d-%b-%Y"), lot=int(o.lot_size.iloc[0]),
                    exp_hm=(23, 30) if opt_exch == "MCX" else (15, 30), quotes=quotes)
    except Exception:
        return None
