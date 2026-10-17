"""
Quarterly results: what the options market prices in versus what each stock has actually done on results.

Option premiums swell before results and collapse right after them (IV crush), and the results-day direction is a coin
flip (5-day pre-results drift matched the reaction 45% of the time, 66 reactions, 39 F&O stocks). So the only
pre-results bet that makes sense for an option *buyer* is a long straddle (ATM CALL + PUT) when it is CHEAP: its cost
(the break-even move) is below the move this stock has typically made on results. Results-day moves are ~1.5x a normal
day's (median), and > 2 ATR on ~18% of results days.

Reaction = close before the announcement -> close of the reaction session (same day if announced before 15:30 IST,
next session if after; unknown time: the day before -> the day after). Every assessment is logged before results and
scored after, so the straddle rule builds its own track record.
"""
from datetime import timedelta

import pandas as pd
import streamlit as st

import engine as E
import events
import horizons as H
import net
import paper
import upstox_feed as uf

CHEAP, RICH = 1.15, 0.85  # historical mean |move| / implied break-even move


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def result_times(nse_sym):
    """Past results announcements -> sorted list of (Timestamp, time_known). NSE financial-results filings (with the
    broadcast time) plus board-meeting dates whose purpose is financial results (date only)."""
    out = {}
    try:
        j = net.nse_get(f"https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol="
                        f"{nse_sym}&period=Quarterly", E.NSE_H, timeout=12)
        df = pd.DataFrame(j if isinstance(j, list) else j.get("data", []))
        if "broadCastDate" in df:
            for t in pd.to_datetime(df.broadCastDate, format="%d-%b-%Y %H:%M:%S", errors="coerce").dropna():
                out.setdefault(t.date(), (t, True))
    except Exception:
        pass
    try:
        j = net.nse_get(f"https://www.nseindia.com/api/corporate-board-meetings?index=equities&symbol={nse_sym}",
                        E.NSE_H, timeout=12)
        df = pd.DataFrame(j if isinstance(j, list) else j.get("data", []))
        if "bm_date" in df:
            purpose = (df.get("bm_purpose", "").astype(str) + " " + df.get("bm_desc", "").astype(str)).str.lower()
            for dt in pd.to_datetime(df.bm_date[purpose.str.contains("result")], format="%d-%b-%Y", errors="coerce").dropna():
                out.setdefault(dt.date(), (dt, False))
    except Exception:
        pass
    return sorted(out.values(), key=lambda x: x[0])


def _reaction(d, t, known):
    """(pre close, reaction close, ATR before, reaction date) for one announcement, or None."""
    idx = d.index
    day = pd.Timestamp(t.date())
    if known:
        after = t.hour * 60 + t.minute >= 15 * 60 + 30
        pos = idx.searchsorted(day + pd.Timedelta(days=1 if after else 0))
        pre = pos - 1
    else:
        p0 = idx.searchsorted(day)
        pre, pos = p0 - 1, p0 + 1
    if pre < 20 or pos >= len(idx) or (idx[pos] - day).days > 6:
        return None
    return float(d.Close.iloc[pre]), float(d.Close.iloc[pos]), float(d.ATR.iloc[pre]), idx[pos]


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def history(sym):
    """Past results reactions for one stock -> dict(n, mean_pct, median_pct, big2atr, rows) or dict(n=0)."""
    spec = E.resolve(sym)
    d = H.daily(spec["ticker"])
    if d.empty:
        return dict(n=0)
    d = H.features(d)
    rows = []
    for t, known in result_times(spec["nse"] or sym):
        r = _reaction(d, pd.Timestamp(t), known)
        if r:
            pre, post, atr, when = r
            rows.append(dict(date=when, move_pct=(post / pre - 1) * 100, move_atr=(post - pre) / atr, time_known=known))
    if not rows:
        return dict(n=0)
    df = pd.DataFrame(rows).drop_duplicates("date")
    a = df.move_pct.abs()
    return dict(n=len(df), mean_pct=float(a.mean()), median_pct=float(a.median()),
                big2atr=float((df.move_atr.abs() > 2).mean()), up=float((df.move_pct > 0).mean()), rows=df)


def implied(sym, result_date, now):
    """ATM straddle on the first expiry on/after the results date -> dict(expiry, spot, strike, straddle, pct, lot,
    source) or None. Upstox reaches later expiries; the NSE fallback only has the nearest one."""
    spec = E.resolve(sym)
    chain = None
    if E.UPSTOX:
        u = uf.underlying(spec["name"], spec["kind"], E.kite_day())
        if u and u.get("chain_key"):
            raw = uf.option_chain(E.UPSTOX, u["chain_key"], E.kite_day(), min_expiry=str(result_date))
            if raw:
                chain = dict(raw, source="Upstox")
    if chain is None and spec["nse"]:
        ch = E.nse_chain(spec["nse"], False)
        if ch and pd.to_datetime(ch["expiry"]).date() >= result_date:
            chain = dict(ch, spot=ch["und"])
    if not chain or not chain.get("quotes"):
        return None
    spot = float(chain.get("spot") or 0)
    if spot <= 0:
        return None
    K = min(chain["quotes"], key=lambda k: abs(k - spot))
    legs = [E.live_premium(dict(quotes=chain["quotes"]), K, s) for s in ("CE", "PE")]
    if not all(legs):
        return None
    straddle = float(sum(legs))
    return dict(expiry=chain["expiry"], spot=spot, strike=K, straddle=straddle, pct=straddle / spot * 100,
                lot=chain.get("lot"), source=chain.get("source", "NSE"))


def verdict(hist, imp):
    if not hist.get("n") or hist["n"] < 4:
        return "n/a", f"only {hist.get('n', 0)} past results"
    if not imp:
        return "n/a", "no option chain for the results expiry"
    ratio = hist["mean_pct"] / imp["pct"]
    if ratio >= CHEAP:
        return "cheap", f"usual results move {hist['mean_pct']:.1f}% > break-even {imp['pct']:.1f}% ({ratio:.2f}×)"
    if ratio <= RICH:
        return "rich", f"break-even {imp['pct']:.1f}% > usual results move {hist['mean_pct']:.1f}% ({ratio:.2f}×): IV crush likely"
    return "fair", f"break-even {imp['pct']:.1f}% ≈ usual results move {hist['mean_pct']:.1f}% ({ratio:.2f}×)"


def upcoming(universe, now, days=10):
    """Results in the next `days` days for symbols in `universe` -> DataFrame[symbol, date, purpose]."""
    cal = events.calendar()
    if cal.empty:
        return cal
    today = now.date()
    m = cal.purpose.str.lower().str.contains("result") & (cal.date >= today) & \
        (cal.date <= today + timedelta(days=days)) & cal.symbol.isin(set(universe))
    return cal[m].drop_duplicates("symbol").sort_values("date")


def recent(universe, now, days=4):
    cal = events.calendar()
    if cal.empty:
        return cal
    today = now.date()
    m = cal.purpose.str.lower().str.contains("result") & (cal.date < today) & \
        (cal.date >= today - timedelta(days=days)) & cal.symbol.isin(set(universe))
    return cal[m].drop_duplicates("symbol").sort_values("date", ascending=False)


# ---------------- Log: assessed before results, scored after ----------------
LOG_SQL = """CREATE TABLE IF NOT EXISTS results_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, result_date TEXT, logged_at TEXT, spot REAL, strike REAL,
  straddle REAL, implied_pct REAL, expiry TEXT, hist_mean_pct REAL, hist_n INTEGER, verdict TEXT,
  actual_pct REAL, straddle_edge_pct REAL, scored_at TEXT, UNIQUE(symbol, result_date))"""


def log(sym, result_date, imp, hist, verd, now):
    """First assessment per symbol/results date wins (the pre-results view); later ones are ignored."""
    if not imp:
        return
    c = paper.conn()
    c.execute(LOG_SQL)
    c.execute("INSERT OR IGNORE INTO results_log(symbol, result_date, logged_at, spot, strike, straddle, implied_pct, "
              "expiry, hist_mean_pct, hist_n, verdict) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (sym, str(result_date), now.isoformat(timespec="seconds"), imp["spot"], imp["strike"], imp["straddle"],
               imp["pct"], imp["expiry"], hist.get("mean_pct"), hist.get("n", 0), verd))
    c.commit()
    c.close()


def score_log(now):
    """Fill in the actual reaction for logged results that have happened. -> rows scored."""
    c = paper.conn()
    c.execute(LOG_SQL)
    todo = c.execute("SELECT id, symbol, result_date, spot, implied_pct FROM results_log WHERE actual_pct IS NULL AND "
                     "result_date < ?", (now.date().isoformat(),)).fetchall()
    n = 0
    for r in todo:
        spec = E.resolve(r["symbol"])
        d = H.daily(spec["ticker"])
        if d.empty:
            continue
        d = H.features(d)
        times = {t.date(): (t, k) for t, k in result_times(spec["nse"] or r["symbol"])}
        rd = pd.Timestamp(r["result_date"]).date()
        t, known = times.get(rd, (pd.Timestamp(rd), False))
        x = _reaction(d, pd.Timestamp(t), known)
        if not x:
            continue
        pre, post = x[0], x[1]
        actual = (post / pre - 1) * 100
        c.execute("UPDATE results_log SET actual_pct=?, straddle_edge_pct=?, scored_at=? WHERE id=?",
                  (actual, abs(actual) - r["implied_pct"], now.isoformat(timespec="seconds"), r["id"]))
        n += 1
    c.commit()
    c.close()
    return n


def log_df():
    c = paper.conn()
    c.execute(LOG_SQL)
    df = pd.read_sql_query("SELECT * FROM results_log ORDER BY result_date DESC", c)
    c.close()
    return df
