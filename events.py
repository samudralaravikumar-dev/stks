"""
Event risk: NSE corporate event calendar (results, dividends, splits...), F&O trading holidays and expiry day.
Results days matter most for option buyers: implied volatility usually collapses once results are out (IV crush),
so a correct direction call can still lose money. Data from NSE's public API, cached; failures return no events.
"""
from datetime import date, datetime, timedelta

import pandas as pd
import requests
import streamlit as st

_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 "
                    "Safari/537.36", "Accept": "application/json, text/plain, */*", "Referer": "https://www.nseindia.com/"}


def _nse(path):
    s = requests.Session()
    s.get("https://www.nseindia.com/", headers=_H, timeout=8)
    return s.get("https://www.nseindia.com" + path, headers=_H, timeout=10).json()


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def calendar():
    """-> DataFrame[symbol, company, purpose, date] for upcoming corporate events."""
    try:
        df = pd.DataFrame(_nse("/api/event-calendar"))
        df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce").dt.date
        return df.dropna(subset=["date"])[["symbol", "company", "purpose", "date"]]
    except Exception:
        return pd.DataFrame(columns=["symbol", "company", "purpose", "date"])


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def holidays():
    """-> {date: description} for F&O trading holidays."""
    try:
        j = _nse("/api/holiday-master?type=trading")
        rows = j.get("FO") or j.get("CM") or []
        return {datetime.strptime(r["tradingDate"], "%d-%b-%Y").date(): r["description"] for r in rows}
    except Exception:
        return {}


def _when(d, today):
    n = (d - today).days
    return "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days ({d:%a %d %b})"


def flags(spec, chain, now):
    """-> list of (severity 'high'|'info', text) for this symbol around today."""
    today = now.date() if hasattr(now, "date") else date.today()
    out = []
    cal = calendar()
    sym = (spec.get("nse") or spec.get("name") or "").upper()
    if sym and len(cal):
        ev = cal[(cal.symbol == sym) & (cal.date >= today) & (cal.date <= today + timedelta(days=5))]
        for r in ev.sort_values("date").itertuples():
            results = "result" in r.purpose.lower()
            sev = "high" if results and (r.date - today).days <= 1 else "info"
            out.append((sev, f"{r.purpose} {_when(r.date, today)}"
                        + (": IV usually collapses after results, avoid holding options into it" if results else "")))
    if chain and chain.get("expiry"):
        try:
            if datetime.strptime(chain["expiry"], "%d-%b-%Y").date() == today:
                out.append(("high", "Expiry day: theta and gamma are extreme, prefer ITM strikes and quick exits"))
        except ValueError:
            pass
    hol = holidays()
    nxt = [d for d in hol if today < d <= today + timedelta(days=4)]
    if nxt:
        d = min(nxt)
        out.append(("info", f"Market holiday {_when(d, today)} ({hol[d]}): extra theta decay over the break"))
    return out


def results_soon(events):
    """True when the flags include results today or tomorrow."""
    return any(sev == "high" and "result" in t.lower() for sev, t in events)
