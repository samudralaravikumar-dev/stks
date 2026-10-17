"""
Event risk: NSE corporate event calendar (results, dividends, splits...), F&O trading holidays and expiry day.
Results days matter most for option buyers: implied volatility usually collapses once results are out (IV crush),
so a correct direction call can still lose money. Data from NSE's public API, cached; failures return no events.
"""
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import net

_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 "
                    "Safari/537.36", "Accept": "application/json, text/plain, */*", "Referer": "https://www.nseindia.com/"}


def _nse(path):
    return net.nse_get("https://www.nseindia.com" + path, _H, timeout=10)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _calendar():
    df = pd.DataFrame(_nse("/api/event-calendar"))
    df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce").dt.date
    return df.dropna(subset=["date"])[["symbol", "company", "purpose", "date"]]


def calendar():
    """-> DataFrame[symbol, company, purpose, date] for upcoming corporate events. Failures are not cached, so one
    blocked NSE call doesn't hide results-day warnings for hours."""
    try:
        return _calendar()
    except Exception:
        return pd.DataFrame(columns=["symbol", "company", "purpose", "date"])


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def _holidays():
    j = _nse("/api/holiday-master?type=trading")
    rows = j.get("FO") or j.get("CM") or []
    return {datetime.strptime(r["tradingDate"], "%d-%b-%Y").date(): r["description"] for r in rows}


def holidays():
    """-> {date: description} for F&O trading holidays ({} on failure, not cached)."""
    try:
        return _holidays()
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
                if today >= date(2026, 12, 7):  # SEBI, Oct 2026: settlement moves off the closing auction
                    out.append(("info", "Expiring contracts settle at the VWAP of the last 30 minutes of trading, not "
                                        "the close: positions held into the close settle near that average"))
        except ValueError:
            pass
    if spec.get("kind") == "commodity":
        out += mcx_report_flags(spec, now)
    hol = holidays()
    nxt = [d for d in hol if today < d <= today + timedelta(days=4)]
    if nxt:
        d = min(nxt)
        out.append(("info", f"Market holiday {_when(d, today)} ({hol[d]}): extra theta decay over the break"))
    return out


MCX_REPORTS = {  # weekly US EIA reports that move MCX energy: (weekday Mon=0, report)
    "crude": (2, "EIA crude oil inventory report"),
    "gas": (3, "EIA natural gas storage report"),
}


def mcx_report_flags(spec, now):
    """Weekly EIA releases (10:30 New York time, ~20:00 IST in US summer / 21:00 IST in winter) for MCX crude oil
    and natural gas: option premiums jump and spreads widen around them, so new option buys are risky."""
    from zoneinfo import ZoneInfo
    name = (spec.get("name") or "").upper()
    key = "crude" if "CRUDE" in name else "gas" if ("GAS" in name or "NATGAS" in name) else None
    if key is None:
        return []
    wd, label = MCX_REPORTS[key]
    now = pd.Timestamp(now).tz_convert("Asia/Kolkata") if pd.Timestamp(now).tzinfo else \
        pd.Timestamp(now).tz_localize("Asia/Kolkata")
    out = []
    for add in range(0, 3):
        d = (now + pd.Timedelta(days=add)).date()
        ny = pd.Timestamp(datetime(d.year, d.month, d.day, 10, 30), tz=ZoneInfo("America/New_York"))
        rel = ny.tz_convert("Asia/Kolkata")
        if rel.date() != d or rel.weekday() != wd:
            continue
        mins = (rel - now).total_seconds() / 60
        when = f"{rel:%H:%M} IST"
        if -45 <= mins <= 0:
            out.append(("high", f"{label} released at {when}: volatility spike, let it settle before buying options"))
        elif 0 < mins <= 12 * 60 and rel.date() == now.date():
            out.append(("high", f"{label} today at {when}: avoid holding fresh option buys into it (premium and "
                                f"spread jump, direction is a coin toss)"))
        elif mins > 0:
            out.append(("info", f"{label} {_when(rel.date(), now.date())} at {when}"))
        break
    return out


def results_soon(events):
    """True when the flags include results today or tomorrow."""
    return any(sev == "high" and "result" in t.lower() for sev, t in events)
