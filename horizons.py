"""
BTST (buy today, sell tomorrow) and 2-day swing calls from daily candles, each with its own 2-year track record on
the same symbol, so a call shows how often that exact setup worked before.

BTST  : strong close (CLV = (C-L)/(H-L) >= 0.75) through the previous day's high, daily EMA20 > EMA50 and close above
        EMA20, volume >= 1.2x its 20-day average (stocks), leading NIFTY 50 on the day (stocks). Mirror for PUT.
        Exit next session: T1 = close + 0.5 ATR, stop = close - 0.75 ATR (stop counted first when both touch).
Swing : daily EMA20 > EMA50, ADX(14) > 20, close above the prior 10-day high. Mirror for PUT. Exit within 2
        sessions: T1 = close + 1 ATR, stop = close - 1 ATR, else at the 2nd close.
Pullback: daily EMA20 > EMA50, yesterday's low within 0.5% of EMA20, today closes above yesterday's high and EMA20 with
        RSI(14) 50-65 (PUT: mirror, RSI 35-50). Same exits as Swing.
2-day range: close +/- k x ATR14 x sqrt(2), with k the symbol's own 80th percentile of |2-day move| / (ATR x sqrt 2).
Daily bars come from Yahoo (MCX: NYMEX proxy x USD/INR, so levels are approximate; % and ATR ratios are what count).
"""
from math import sqrt

import numpy as np
import pandas as pd
import streamlit as st

import engine as E

HORIZONS = {"BTST": dict(t1=0.5, sl=0.75, hold=1), "SWING2D": dict(t1=1.0, sl=1.0, hold=2),
            "SWINGPB": dict(t1=1.0, sl=1.0, hold=2)}


@st.cache_data(ttl=600, show_spinner=False)
def daily(ticker):
    df = E.load_prices(ticker, "1d", "2y")
    if df.empty:
        return df
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    return df[~df.index.duplicated(keep="last")]


def features(df, bench=None):
    d = df.copy()
    c, h, l = d.Close, d.High, d.Low
    d["EMA20"], d["EMA50"] = c.ewm(span=20, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["ATR"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=d.index).ewm(alpha=1 / 14, adjust=False).mean()
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=d.index).ewm(alpha=1 / 14, adjust=False).mean()
    pdi, mdi = 100 * pdm / d.ATR, 100 * mdm / d.ATR
    d["ADX"] = (100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)).ewm(alpha=1 / 14, adjust=False).mean()
    d["CLV"] = ((c - l) / (h - l).replace(0, np.nan))
    d["VOLx"] = d.Volume / d.Volume.rolling(20).mean().shift().replace(0, np.nan)
    d["PDH"], d["PDL"] = h.shift(), l.shift()
    d["HI10"], d["LO10"] = h.rolling(10).max().shift(), l.rolling(10).min().shift()
    d["RSI"] = E.rsi(c)
    d["CHG"] = c.pct_change() * 100
    d["RS"] = d.CHG - bench.Close.pct_change().reindex(d.index).mul(100) if bench is not None and len(bench) else np.nan
    return d


def signals(d, kind):
    """+1 CALL / -1 PUT / 0 for each completed daily bar."""
    c = d.Close
    up, dn = (d.EMA20 > d.EMA50) & (c > d.EMA20), (d.EMA20 < d.EMA50) & (c < d.EMA20)
    vol_ok = (d.VOLx >= 1.2) | d.VOLx.isna()
    if kind == "BTST":
        rs_l, rs_s = (d.RS > 0) | d.RS.isna(), (d.RS < 0) | d.RS.isna()
        long = up & (d.CLV >= 0.75) & (c > d.PDH) & vol_ok & rs_l
        short = dn & (d.CLV <= 0.25) & (c < d.PDL) & vol_ok & rs_s
    elif kind == "SWINGPB":  # trend pullback: dip to EMA20 yesterday, today closes back through yesterday's extreme
        long = (d.EMA20 > d.EMA50) & (d.Low.shift() <= d.EMA20.shift() * 1.005) & (c > d.PDH) & (c > d.EMA20) &             d.RSI.between(50, 65)
        short = (d.EMA20 < d.EMA50) & (d.High.shift() >= d.EMA20.shift() * 0.995) & (c < d.PDL) & (c < d.EMA20) &             d.RSI.between(35, 50)
    else:
        long = (d.EMA20 > d.EMA50) & (d.ADX > 20) & (c > d.HI10)
        short = (d.EMA20 < d.EMA50) & (d.ADX > 20) & (c < d.LO10)
    return pd.Series(np.where(long, 1, np.where(short, -1, 0)), index=d.index)


def backtest(d, kind):
    """Entry at the signal day's close; T1 / stop in ATR; stop first when a day touches both (conservative)."""
    cfg, sig = HORIZONS[kind], signals(d, kind)
    o, h, l, c, a = (d[x].to_numpy() for x in ("Open", "High", "Low", "Close", "ATR"))
    out, n = [], len(d)
    for i in np.flatnonzero(sig.to_numpy()[:-1]):
        dr, e, atr = int(sig.iloc[i]), c[i], a[i]
        t1, sl, res = e + dr * cfg["t1"] * atr, e - dr * cfg["sl"] * atr, None
        for j in range(i + 1, min(i + 1 + cfg["hold"], n)):
            if (l[j] - sl) * dr <= 0 if dr > 0 else (h[j] - sl) * dr <= 0:
                px = o[j] if (o[j] - sl) * dr < 0 else sl  # gapped through the stop: filled at the open
                res = ("stop", (px - e) * dr / (cfg["sl"] * atr))
                break
            if ((h[j] if dr > 0 else l[j]) - t1) * dr >= 0:
                px = o[j] if (o[j] - t1) * dr > 0 else t1
                res = ("target", (px - e) * dr / (cfg["sl"] * atr))
                break
        if res is None and i + cfg["hold"] < n:
            j = i + cfg["hold"]
            res = ("time", (c[j] - e) * dr / (cfg["sl"] * atr))
        if res:
            gap = (o[i + 1] - e) * dr / atr if i + 1 < n else np.nan
            out.append(dict(date=d.index[i], side="CALL" if dr > 0 else "PUT", exit=res[0], R=res[1], gap_atr=gap))
    return pd.DataFrame(out)


def range_k(d, q=0.8):
    """k so that close +/- k*ATR*sqrt(2) held the next 2 sessions' close-to-close move q of the time (own history)."""
    mv = (d.Close.shift(-2) - d.Close).abs() / (d.ATR * sqrt(2))
    mv = mv.dropna().tail(250)
    return float(mv.quantile(q)) if len(mv) > 50 else 1.0


@st.cache_data(ttl=300, show_spinner=False)
def analyze(sym, fx=None):
    """-> dict with today's BTST / 2-day setups, the 2-day range and each setup's own 2-year record, or error."""
    spec = E.resolve(sym)
    df = daily(spec["ticker"])
    if df.empty or len(df) < 80:
        return dict(sym=sym, error="no daily history")
    if spec["kind"] == "commodity" and fx:
        df = df.copy()
        df[["Open", "High", "Low", "Close"]] *= fx
    bench = None if spec["kind"] == "commodity" or spec["name"] == "NIFTY" else daily("^NSEI")
    d = features(df, bench)
    today = pd.Timestamp.now(tz=E.IST).tz_localize(None).normalize()
    hist = d[d.index < today]  # completed days only for the record
    row = d.iloc[-1]
    k = range_k(hist)
    r2 = k * row.ATR * sqrt(2)
    out = dict(sym=sym, spec=spec, date=d.index[-1], close=float(row.Close), atr=float(row.ATR), clv=row.CLV,
               adx=row.ADX, volx=row.VOLx, rs=row.RS, ema_up=bool(row.EMA20 > row.EMA50),
               range2d=(float(row.Close - r2), float(row.Close + r2)), k=k, live_bar=d.index[-1] >= today, setups={})
    for kind, cfg in HORIZONS.items():
        dr = int(signals(d, kind).iloc[-1])
        t = backtest(hist, kind)
        side = t[t.side == ("CALL" if dr > 0 else "PUT")] if dr and len(t) else t
        rec = dict(n=len(side), hit=float((side.exit == "target").mean()) if len(side) else np.nan,
                   avg_r=float(side.R.mean()) if len(side) else np.nan,
                   gap_against=float((side.gap_atr < -0.25).mean()) if len(side) else np.nan,
                   n_all=len(t), hit_all=float((t.exit == "target").mean()) if len(t) else np.nan)
        out["setups"][kind] = dict(dir=dr, t1=float(row.Close + dr * cfg["t1"] * row.ATR) if dr else None,
                                   sl=float(row.Close - dr * cfg["sl"] * row.ATR) if dr else None, rec=rec)
    return out


def grade(rec):
    """Evidence grade for a horizon setup from its own record: needs enough past signals and a positive average."""
    n, hit, avg = rec["n"], rec["hit"], rec["avg_r"]
    if not n or n < 8 or np.isnan(avg):
        return "D", f"only {n} past signals"
    lo = E.wilson_lo(int(round(hit * n)), n)
    if n >= 20 and avg > 0.1 and lo >= 0.45:
        return "A", f"{n} past signals, {hit * 100:.0f}% hit T1 (≥{lo * 100:.0f}% at 95%), {avg:+.2f}R avg"
    if avg > 0 and hit >= 0.5:
        return "B", f"{n} past signals, {hit * 100:.0f}% hit T1, {avg:+.2f}R avg"
    if avg > -0.1:
        return "C", f"{n} past signals, {hit * 100:.0f}% hit T1, {avg:+.2f}R avg (no proven edge)"
    return "D", f"{n} past signals, {hit * 100:.0f}% hit T1, {avg:+.2f}R avg (loses on its own history)"


@st.cache_data(ttl=1800, show_spinner=False)
def portfolio_record(syms, fx=None):
    """Pooled record of every BTST / 2-day signal on these symbols over 2 years -> {kind: dict}."""
    pooled = {k: [] for k in HORIZONS}
    for s in syms:
        spec = E.resolve(s)
        df = daily(spec["ticker"])
        if df.empty or len(df) < 80:
            continue
        bench = None if spec["kind"] == "commodity" or spec["name"] == "NIFTY" else daily("^NSEI")
        d = features(df, bench)
        d = d[d.index < pd.Timestamp.now(tz=E.IST).tz_localize(None).normalize()]
        for k in HORIZONS:
            t = backtest(d, k)
            if len(t):
                pooled[k].append(t.assign(sym=s))
    res = {}
    for k, ts in pooled.items():
        t = pd.concat(ts) if ts else pd.DataFrame(columns=["R", "exit", "side"])
        res[k] = dict(n=len(t), hit=float((t.exit == "target").mean()) if len(t) else np.nan,
                      avg_r=float(t.R.mean()) if len(t) else np.nan,
                      by_side={s: (len(g), float((g.exit == "target").mean()), float(g.R.mean()))
                               for s, g in t.groupby("side")} if len(t) else {})
    return res


def alert_window(spec, now):
    """True in the last ~45 minutes before the close (NSE 14:45-15:29, MCX 22:30-23:25 IST), when today's daily
    candle is close to final and a BTST / 2-day call can still be acted on."""
    t = now.hour * 60 + now.minute
    if now.weekday() >= 5:
        return False
    return 1350 <= t <= 1405 if spec["kind"] == "commodity" else 885 <= t <= 929
