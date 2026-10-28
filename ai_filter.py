"""
AI score of a CALL/PUT call: a logistic model fitted on two years of the engine's own signals priced on REAL option
contracts (ai_filter.json; 41 NSE symbols). Inputs are what was known at the signal bar: the engine's indicators (signed
so that + favours the trade), time of day, days to expiry, India VIX (previous close) and FII index-futures positioning
(NSE participant-wise OI, previous sessions). Walk-forward out of sample, the top third of signals won ~51% of trades
against ~46% for all signals, and lost about half as much per trade, in both halves of the test months. No band was
profitable on options by itself, so the score is shown on the cards and logged with each call (recs.ai_score); it does
not change the grade, the lots or the TRADE/PAPER label. NSE stocks and indices only (no MCX in the training data), and only calls standing on a signal bar ("BUY now"; a
waiting "buy above X" trigger has no score).
"""
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

import engine as E
import net

MODEL = json.loads((Path(__file__).parent / "ai_filter.json").read_text())
PART_URL = "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv"


@st.cache_data(ttl=3 * 3600, show_spinner=False)
def context(day_iso):
    """Daily inputs known before `day`: India VIX previous close, FII share of index-futures longs and its 5-session
    change. -> dict (values may be None when a source is down: the model then uses its fill values)."""
    day = pd.Timestamp(day_iso)
    out = dict(vix=None, fii_long=None, fii_chg5=None)
    try:
        v = E.load_prices("^INDIAVIX", "1d", "1mo")
        v = v[pd.DatetimeIndex(v.index).tz_localize(None).normalize() < day]
        out["vix"] = float(v.Close.iloc[-1]) if len(v) else None
    except Exception:
        pass
    longs, d = [], day - pd.Timedelta(days=1)
    s = net.session("nse")
    while len(longs) < 6 and d > day - pd.Timedelta(days=16):  # 6 sessions back, skipping weekends and holidays
        if d.weekday() < 5:
            try:
                r = s.get(PART_URL.format(d=d), headers=E.NSE_H, timeout=10)
                if r.ok and b"Client Type" in r.content[:400]:
                    x = pd.read_csv(io.BytesIO(r.content), skiprows=1)
                    x.columns = [c.strip() for c in x.columns]
                    f = x[x["Client Type"].astype(str).str.strip() == "FII"].iloc[0]
                    lo, sh = float(f["Future Index Long"]), float(f["Future Index Short"])
                    longs.append(lo / (lo + sh))
            except Exception:
                pass
        d -= pd.Timedelta(days=1)
    if longs:
        out["fii_long"] = longs[0]
        out["fii_chg5"] = longs[0] - longs[5] if len(longs) >= 6 else None
    return out


def features(a, r, now):
    """The model's inputs for this call, from the analysis' last closed bar (the signal bar). -> dict or None."""
    spec, x = a["spec"], a["row"]
    if spec["kind"] == "commodity" or r.get("kind") not in ("CE", "PE"):
        return None
    dr = 1 if r["kind"] == "CE" else -1
    if int(np.sign(x.get("Signal") or 0)) != dr:  # trained on signal bars only: a waiting trigger call is not one
        return None
    g = lambda k: float(x[k]) if k in x and pd.notna(x[k]) else np.nan  # noqa: E731
    atr = g("ATR") if g("ATR") > 0 else np.nan
    entry = x.name + pd.Timedelta(minutes=15)
    try:
        dte = (pd.Timestamp(r["expiry"]).date() - now.date()).days
    except Exception:
        dte = np.nan
    cx = context(now.date().isoformat())
    nz = lambda v: np.nan if v is None else v  # noqa: E731
    return dict(index=float(spec["kind"] == "index"), score=g("Score") * dr, need=g("Need"), nsetups=g("NSetups"),
                adx=g("ADX"), chop=g("CHOP"), rsi=(g("RSI") - 50) * dr, macdh=g("MACDh") / atr * dr,
                vwap=(g("Close") - g("VWAP")) / atr * dr, ema21=(g("Close") - g("EMA21")) / atr * dr,
                ema9_21=(g("EMA9") - g("EMA21")) / atr * dr, bbw=g("BBw"), squeeze=float(bool(x.get("Squeeze"))),
                rvol=g("RVOL"), mfi=(g("MFI") - 50) * dr, cci=g("CCI") / 100 * dr, st=g("STdir") * dr, d1=g("D1") * dr,
                rs=g("RS") * dr, htf15=g("HTF15") * dr, htf60=g("HTF60") * dr, ext=g("Ext"), stop_atr=g("StopDist") / atr,
                rr=g("T2Dist") / g("StopDist") if g("StopDist") else np.nan,
                atr_x=g("ATR") / g("ATRma") if g("ATRma") else np.nan, pdh=(g("Close") - g("PDH")) / atr * dr,
                pdl=(g("Close") - g("PDL")) / atr * dr, minute=entry.hour * 60 + entry.minute, dte=dte,
                vix=nz(cx["vix"]), fii_long=nz(cx["fii_long"]),
                fii_long_dir=(cx["fii_long"] - 0.5) * dr if cx["fii_long"] is not None else np.nan,
                fii_chg5=cx["fii_chg5"] * dr if cx["fii_chg5"] is not None else np.nan)


def score(a, r, now=None):
    """Model probability that this call's option trade ends in profit (out of sample ~0.40-0.55). None if not scored."""
    f = features(a, r, now or pd.Timestamp.now(tz=E.IST))
    if f is None:
        return None
    v = np.array([f[k] for k in MODEL["feats"]], dtype=float)
    v = np.where(np.isfinite(v), v, MODEL["fill"])
    v = np.clip(v, MODEL["lo"], MODEL["hi"])  # outside what the model saw (after hours, odd bars): its edge values
    z =(v - np.array(MODEL["mean"])) / np.array(MODEL["scale"])
    return float(1 / (1 + np.exp(-(z @ np.array(MODEL["coef"]) + MODEL["intercept"]))))


def band(p):
    lo, hi = MODEL["cut"]
    return None if p is None else "high" if p >= hi else "mid" if p >= lo else "low"


def line(p):
    """Card / alert line: the band and how that band did out of sample."""
    b = band(p)
    if b is None:
        return ""
    o, allr = MODEL["oos"][b], MODEL["oos"]["all"]
    icon = {"high": "🟢", "mid": "🟡", "low": "🔴"}[b]
    return (f"🤖 AI score {icon} {b.upper()} ({p * 100:.0f}) · past {b} calls won {o['win']:.0f}% vs "
            f"{allr['win']:.0f}% for all")
