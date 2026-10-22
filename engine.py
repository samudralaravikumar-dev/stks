"""
Intraday CALL / PUT Signal Engine: analysis core (no UI). Used by the Streamlit app and the background runner.
NSE indices (NIFTY, BANKNIFTY, FINNIFTY, SENSEX), NSE F&O stocks, MCX crude / natural gas (NYMEX proxy in INR)

Run:
    pip install streamlit yfinance pandas numpy feedparser plotly vaderSentiment requests
    streamlit run trading_signals_app.py

How a signal is produced (same code path for live signals and the backtest):
  1. Regime     ADX (Wilder) + Choppiness Index (Dreiss) -> Trend / Range / Mixed
  2. Setup      a named, rule-based trigger fired in the last N closed bars
                (ORB, VWAP pullback, Supertrend flip, PDH/CPR breakout, squeeze release, VWAP-band reversion)
  3. Confluence weighted trend/momentum/structure/volume votes must agree with the setup
  4. Filters    15-min trend alignment (Elder triple screen), entry time window, no chasing (> 2.5 ATR from EMA21)
  5. Risk       structure/ATR stop, 1R partial + breakeven, 2R target, Supertrend trail, R-based position sizing
Only CLOSED candles are used, so signals do not repaint.

Educational tool only. NOT financial advice. Buying options can lose 100% of the premium.
Paper-trade any rule set before risking money.
"""
import urllib.parse
from datetime import datetime, time as dtime
from functools import reduce
from math import erf, exp, gcd, log, sqrt

import feedparser
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

import events
import kite_feed as kf
import net
import upstox_feed as uf


IST = "Asia/Kolkata"

# name: (yahoo ticker, NSE option-chain symbol, strike step, fallback lot).
# Lot sizes change by exchange circular; when the NSE chain loads, the lot is inferred from live order quantities.
INDEXES = {
    "NIFTY": ("^NSEI", "NIFTY", 50, 65),
    "BANKNIFTY": ("^NSEBANK", "BANKNIFTY", 100, 30),
    "FINNIFTY": ("NIFTY_FIN_SERVICE.NS", "FINNIFTY", 50, 65),
    "SENSEX": ("^BSESN", None, 100, 20),
}
# name: (NYMEX ticker, MCX strike step in INR, MCX lot, news query). Converted to INR with USDINR.
COMMODITIES = {
    "CRUDE": ("CL=F", 50, 100, "crude oil price"),
    "CRUDEOIL": ("CL=F", 50, 100, "crude oil price"),
    "BRENT": ("BZ=F", 50, 100, "brent crude oil"),
    "NATURALGAS": ("NG=F", 5, 1250, "natural gas price"),
    "NATGAS": ("NG=F", 5, 1250, "natural gas price"),
}
SESSIONS = {  # (open, close, square-off) IST
    "nse": (dtime(9, 15), dtime(15, 30), dtime(15, 15)),
    "mcx": (dtime(9, 0), dtime(23, 30), dtime(23, 0)),
}
SETUPS = [  # key, type, label, reference
    ("ORB", "trend", "Opening-range breakout", "Crabel (1990)"),
    ("VWAPPB", "trend", "VWAP pullback in trend", "Berkowitz et al. (1988); Shannon (2023)"),
    ("STFLIP", "trend", "Supertrend flip + EMA alignment", "Wilder ATR (1978)"),
    ("CPRBO", "trend", "Prev-day high/low + CPR breakout", "Ochoa (2010)"),
    ("SQZ", "trend", "Squeeze release (BB inside Keltner)", "Carter (2012)"),
    ("VWAPMR", "mr", "VWAP ±2σ reversion, RSI(2) extreme", "Connors & Alvarez (2008)"),
]
SETUP_LABEL = {k: lbl for k, _, lbl, _ in SETUPS}
TREND_KEYS = [k for k, t, _, _ in SETUPS if t == "trend"]
CORE = ["EMA21", "RSI", "MACDh", "ATR", "ADX", "CHOP", "CCI", "BBu"]


def resolve(name):
    n = name.strip().upper().replace(" ", "")
    if n in INDEXES:
        t, nse, step, lot = INDEXES[n]
        return dict(name=n, ticker=t, kind="index", nse=nse, step=step, lot=lot, sess="nse",
                    query=f"{n} stock market today")
    if n in COMMODITIES:
        t, step, lot, q = COMMODITIES[n]
        return dict(name=n, ticker=t, kind="commodity", nse=None, step=step, lot=lot, sess="mcx", query=q)
    if n.endswith((".NS", ".BO")):
        base = n.rsplit(".", 1)[0]
        return dict(name=base, ticker=n, kind="stock", nse=base if n.endswith(".NS") else None, step=None,
                    lot=None, sess="nse", query=f"{base} share")
    if "=" in n or n.startswith("^"):
        return dict(name=n, ticker=n, kind="stock", nse=None, step=None, lot=None, sess="nse", query=n)
    return dict(name=n, ticker=n + ".NS", kind="stock", nse=n, step=None, lot=None, sess="nse",
                query=f"{n} share NSE")


def auto_step(price):
    return 5 if price < 250 else 10 if price < 1000 else 20 if price < 2000 else 50 if price < 4000 else 100


def tmin(t):
    return t.hour * 60 + t.minute


# ---------------- Data ----------------
def _flat(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


@st.cache_data(ttl=60, show_spinner=False)
def load_prices(ticker, interval, period):
    # Ticker.history, not yf.download: download() shares global state and is unsafe from the scan thread pool
    try:
        return _flat(yf.Ticker(ticker).history(interval=interval, period=period, auto_adjust=True)).dropna()
    except Exception:  # history() re-raises rate limits (429) that download() swallowed: keep the empty-frame contract
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def last_close(ticker):
    """-> (last daily close, % change) or (None, None)."""
    try:
        v = _flat(yf.download(ticker, period="5d", interval="1d", progress=False))["Close"].dropna()
        return float(v.iloc[-1]), (float(v.iloc[-1] / v.iloc[-2] - 1) if len(v) > 1 else 0.0)
    except Exception:
        return None, None


def prepare(raw, spec, interval, fx):
    """Session bars in IST, commodities in INR, and the still-forming candle dropped (no repainting)."""
    df = raw[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    df.index = df.index.tz_localize(IST) if df.index.tz is None else df.index.tz_convert(IST)
    ltp = float(df.Close.iloc[-1])
    op, cl, _ = SESSIONS[spec["sess"]]
    df = df.between_time(op, cl, inclusive="left")
    if spec["kind"] == "commodity" and fx:
        df[["Open", "High", "Low", "Close"]] *= fx
        ltp *= fx
    if len(df) and pd.Timestamp.now(tz=IST) < df.index[-1] + pd.Timedelta(minutes=int(interval[:-1])):
        df = df.iloc[:-1]
    return df, ltp


NSE_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36",
         "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
         "Referer": "https://www.nseindia.com/option-chain"}


@st.cache_data(ttl=90, show_spinner=False)
def nse_chain(sym, is_index):
    """Nearest-expiry chain from NSE's v3 API (unofficial; returns None on failure).
    Gives PCR, OI walls, max pain, ATM IV, live quotes per strike, strike step and lot size."""
    try:
        q = urllib.parse.quote(sym)
        expiry = net.nse_get(f"https://www.nseindia.com/api/option-chain-contract-info?symbol={q}", NSE_H)["expiryDates"][0]
        j = net.nse_get(f"https://www.nseindia.com/api/option-chain-v3?type={'Indices' if is_index else 'Equity'}"
                        f"&symbol={q}&expiry={expiry}", NSE_H)
        und = float(j["records"]["underlyingValue"])
        quotes, qtys = {}, []
        for x in j["records"]["data"]:
            for side in ("CE", "PE"):
                o = x.get(side)
                if not o:
                    continue
                quotes.setdefault(float(x["strikePrice"]), {})[side] = dict(
                    ltp=o.get("lastPrice") or 0.0, bid=o.get("buyPrice1") or 0.0, ask=o.get("sellPrice1") or 0.0,
                    iv=o.get("impliedVolatility") or 0.0, oi=o.get("openInterest") or 0,
                    doi=o.get("changeinOpenInterest") or 0)
                qtys += [int(v) for v in (o.get("buyQuantity1"), o.get("sellQuantity1")) if v]
        lot = reduce(gcd, qtys) if qtys else None
        return summarize_chain(quotes, und, expiry, lot if lot and lot > 1 else None, "NSE")
    except Exception:
        return None


def summarize_chain(quotes, und, expiry, lot, source, exp_hm=(15, 30)):
    """quotes {strike: {CE/PE: ltp, bid, ask, iv(%), oi, doi}} -> PCR, OI walls, max pain, ATM IV, step, lot."""
    strikes = sorted(quotes)
    g = lambda k, side, f: quotes[k].get(side, {}).get(f) or 0
    ce_oi, pe_oi = sum(g(k, "CE", "oi") for k in strikes), sum(g(k, "PE", "oi") for k in strikes)
    ce_d, pe_d = sum(g(k, "CE", "doi") for k in strikes), sum(g(k, "PE", "doi") for k in strikes)
    pain = min(strikes, key=lambda K: sum(g(k, "CE", "oi") * max(K - k, 0) + g(k, "PE", "oi") * max(k - K, 0)
                                          for k in strikes))
    atm = min(strikes, key=lambda k: abs(k - und))
    ivs = [v for v in (g(atm, "CE", "iv"), g(atm, "PE", "iv")) if v > 0]
    return dict(expiry=expiry, und=und, pcr=pe_oi / ce_oi if ce_oi else None,
                pcr_chg=pe_d / ce_d if ce_d > 0 else None,
                call_wall=max(strikes, key=lambda k: g(k, "CE", "oi")),
                put_wall=max(strikes, key=lambda k: g(k, "PE", "oi")), max_pain=pain,
                atm_iv=float(np.mean(ivs)) / 100 if ivs else None, lot=lot,
                step=float(pd.Series(np.diff(strikes)).mode().iloc[0]) if len(strikes) > 1 else None,
                quotes=quotes, source=source, exp_hm=exp_hm)


# ---------------- Indicators ----------------
def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(100.0).where(up.notna())


def supertrend(df, n=10, m=3.0):
    h, l, c = df.High.values, df.Low.values, df.Close.values
    tr = pd.concat([df.High - df.Low, (df.High - df.Close.shift()).abs(),
                    (df.Low - df.Close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean().bfill().values
    hl2 = (h + l) / 2
    ub, lb = hl2 + m * atr, hl2 - m * atr
    fub, flb, d = ub.copy(), lb.copy(), np.ones(len(c))
    for i in range(1, len(c)):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
        if d[i - 1] == 1 and c[i] < flb[i]:
            d[i] = -1
        elif d[i - 1] == -1 and c[i] > fub[i]:
            d[i] = 1
        else:
            d[i] = d[i - 1]
    return np.where(d == 1, flb, fub), d


def htf_trend(c, rule, span=21):
    """Sign of close vs EMA on a higher timeframe, from completed HTF bars only (no look-ahead)."""
    cc = c.resample(rule, label="left", closed="left").last().dropna()
    sig = np.sign(cc - cc.ewm(span=span, adjust=False).mean()).shift(1)
    return sig.reindex(c.index, method="ffill").fillna(0)


def add_indicators(df, has_vol):
    df = df.copy()
    if not has_vol:
        df["Volume"] = 0.0
    o, h, l, c, v = df.Open, df.High, df.Low, df.Close, df.Volume
    day = df.index.date
    # Trend
    df["EMA9"], df["EMA21"] = c.ewm(span=9, adjust=False).mean(), c.ewm(span=21, adjust=False).mean()
    df["EMA50"] = c.ewm(span=50, adjust=False).mean()
    df["ST"], df["STdir"] = supertrend(df)
    df["HTF15"], df["HTF60"] = htf_trend(c, "15min"), htf_trend(c, "60min")
    # Momentum
    df["RSI"], df["RSI2"] = rsi(c), rsi(c, 2)
    e12, e26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    df["MACD"] = e12 - e26
    df["MACDsig"] = df.MACD.ewm(span=9, adjust=False).mean()
    df["MACDh"] = df.MACD - df.MACDsig
    tp = (h + l + c) / 3
    mad = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df["CCI"] = (tp - tp.rolling(20).mean()) / (0.015 * mad.replace(0, np.nan))
    # Volatility / regime
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    df["PDI"] = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / df.ATR
    df["MDI"] = 100 * mdm.ewm(alpha=1 / 14, adjust=False).mean() / df.ATR
    dx = 100 * (df.PDI - df.MDI).abs() / (df.PDI + df.MDI).replace(0, np.nan)
    df["ADX"] = dx.ewm(alpha=1 / 14, adjust=False).mean()
    rng14 = (h.rolling(14).max() - l.rolling(14).min()).replace(0, np.nan)
    df["CHOP"] = 100 * np.log10(tr.rolling(14).sum() / rng14) / np.log10(14)
    ma, sd = c.rolling(20).mean(), c.rolling(20).std()
    df["BBm"], df["BBu"], df["BBl"] = ma, ma + 2 * sd, ma - 2 * sd
    df["BBw"] = 4 * sd / ma
    df["ATRma"] = df.ATR.rolling(20).mean()
    kc_mid = c.ewm(span=20, adjust=False).mean()
    df["Squeeze"] = (df.BBu < kc_mid + 1.5 * df.ATR) & (df.BBl > kc_mid - 1.5 * df.ATR)
    # Session VWAP with ±2σ bands (equal-weighted session average when the feed has no volume, e.g. indices)
    w = v if has_vol else pd.Series(1.0, index=df.index)
    cw = w.groupby(day).cumsum().replace(0, np.nan)
    vwap = (tp * w).groupby(day).cumsum() / cw
    vsd = np.sqrt(((tp * tp * w).groupby(day).cumsum() / cw - vwap ** 2).clip(lower=0))
    df["VWAP"], df["VWAPu"], df["VWAPl"] = vwap, vwap + 2 * vsd, vwap - 2 * vsd
    # Volume: relative volume vs the same time of day on previous sessions
    # Swing structure: prior 20-bar high/low as nearest S/R, and higher-highs/higher-lows over two 10-bar blocks
    df["SwingHi"], df["SwingLo"] = h.rolling(20).max().shift(), l.rolling(20).min().shift()
    hh, ll10 = h.rolling(10).max(), l.rolling(10).min()
    df["HHHL"] = np.where((hh > hh.shift(10)) & (ll10 > ll10.shift(10)), 1,
                          np.where((hh < hh.shift(10)) & (ll10 < ll10.shift(10)), -1, 0))
    if has_vol:
        rmf = tp * v
        pos_mf = rmf.where(tp > tp.shift(), 0).rolling(14).sum()
        neg_mf = rmf.where(tp < tp.shift(), 0).rolling(14).sum()
        df["MFI"] = 100 - 100 / (1 + pos_mf / neg_mf.replace(0, np.nan))
    else:
        df["MFI"] = np.nan
    if has_vol:
        tod = df.index.strftime("%H:%M")
        prev = v.groupby(tod).transform(lambda s: s.shift().rolling(5, min_periods=1).mean())
        df["RVOL"] = (v / prev.replace(0, np.nan)).fillna(v / v.rolling(20, min_periods=5).mean())
        df["OBV"] = (np.sign(c.diff()).fillna(0) * v).cumsum()
        df["OBVema"] = df.OBV.ewm(span=20, adjust=False).mean()
    else:
        df["RVOL"], df["OBV"], df["OBVema"] = np.nan, np.nan, np.nan
    # Previous-day levels, pivots, CPR
    daily = df.groupby(day).agg(H=("High", "max"), L=("Low", "min"), C=("Close", "last")).shift(1)
    m = lambda col: pd.Series(day, index=df.index).map(daily[col])
    df["PDH"], df["PDL"], pc = m("H"), m("L"), m("C")
    piv = (df.PDH + df.PDL + pc) / 3
    bc = (df.PDH + df.PDL) / 2
    tc = 2 * piv - bc
    df["Pivot"], df["CPRtop"], df["CPRbot"] = piv, np.maximum(tc, bc), np.minimum(tc, bc)
    df["R1"], df["S1"] = 2 * piv - df.PDL, 2 * piv - df.PDH
    rng_pd = df.PDH - df.PDL
    df["R2"], df["S2"] = piv + rng_pd, piv - rng_pd
    df["R3"], df["S3"] = df.PDH + 2 * (piv - df.PDL), df.PDL - 2 * (df.PDH - piv)
    df["CamH3"], df["CamH4"] = pc + 1.1 * rng_pd / 4, pc + 1.1 * rng_pd / 2  # Camarilla (N. Stott, 1989)
    df["CamL3"], df["CamL4"] = pc - 1.1 * rng_pd / 4, pc - 1.1 * rng_pd / 2
    df["PDC"] = pc
    # Opening range (first 15 min)
    first = df.index.to_series().groupby(day).transform("min")
    in_or = (df.index.to_series() - first) < pd.Timedelta(minutes=15)
    df["ORH"] = h.where(in_or).groupby(day).transform("max").where(~in_or)
    df["ORL"] = l.where(in_or).groupby(day).transform("min").where(~in_or)
    # Candles
    body = (c - o).abs()
    lw, uw = np.minimum(o, c) - l, h - np.maximum(o, c)
    hammer = (lw > 2 * body) & (uw <= body) & (h > l)
    star = (uw > 2 * body) & (lw <= body) & (h > l)
    bull_eng = (c.shift() < o.shift()) & (c > o) & (c >= o.shift()) & (o <= c.shift())
    bear_eng = (c.shift() > o.shift()) & (c < o) & (c <= o.shift()) & (o >= c.shift())
    df["BullPat"], df["BearPat"] = hammer | bull_eng, star | bear_eng
    return df


# ---------------- Confluence votes ----------------
PILLARS = ["Moving averages", "RSI", "MACD", "Volume", "Bollinger/ATR", "Support/Resistance"]
PILLAR_SHORT = {"Moving averages": "①MA", "RSI": "②RSI", "MACD": "③MACD", "Volume": "④Vol",
                "Bollinger/ATR": "⑤BB/ATR", "Support/Resistance": "⑥S/R"}
# Confirmation gates (not votes): as extra score votes they changed nothing in a 30-symbol, 60-day back-test, but as
# filters all three together cut the loss per trade from -0.065R to -0.011R and lifted T1 hits 22% -> 26%, in both
# halves of the sample. They do not by themselves create a proven edge.
CTX_FILTERS = {"mkt": "NIFTY 50 trend agrees", "rs": "Beats (CALL) / lags (PUT) NIFTY 50 today",
               "d1": "Daily trend agrees (close vs 20-day EMA)", "macd": "MACD histogram agrees"}
# macd: the only one of 17 further technical gates that improved both the selection (Jun-Sep) and the unseen
# (Sep-Oct) period in a 40-symbol, 120-day walk-forward test (-0.092 -> -0.082R and +0.063 -> +0.088R per trade).
CTX_DEFAULT = ("mkt", "rs", "d1", "macd")
# Partial target (R multiples of the stop distance). Option-priced walk-forward test, 41 symbols x 120 days: book half
# at 0.5R then run to the final target with the stop at breakeven -> win rate 47% -> 60% (IV x1.1-1.6 alike), train
# half -1.0% -> +0.1% of premium per trade, all-period average +3.1% -> +2.6%. Higher success rate, similar profit.
T1_R = 0.5


def add_market_context(d, bench):
    """Market confirmation, relative strength and daily trend (completed bars only, no look-ahead).
    bench: NIFTY 50 closes on the same timeframe (None for NIFTY itself and for commodities).
    MKT: NIFTY trend +1/-1/0 · RS: session % change minus NIFTY's (points) · D1: last daily close vs 20-day EMA."""
    c = d.Close
    d["D1"] = htf_trend(c, "1D", span=20)  # last completed daily close vs its 20-day EMA
    if bench is None or len(bench) < 30:
        d["RS"], d["MKT"] = np.nan, 0.0
        return d
    b = bench[~bench.index.duplicated()].sort_index()
    bday = pd.Series(b.index.date, index=b.index)
    b_pdc = bday.map(b.groupby(b.index.date).last().shift(1))  # NIFTY previous close per bar
    b_chg = (b / b_pdc - 1).reindex(d.index, method="ffill")
    same_day = pd.Series(b.index.date, index=b.index).reindex(d.index, method="ffill") == pd.Series(d.index.date,
                                                                                                    index=d.index)
    d["RS"] = ((c / d.PDC - 1) - b_chg).where(same_day) * 100  # session % change vs NIFTY's, in points
    e9, e21 = b.ewm(span=9, adjust=False).mean(), b.ewm(span=21, adjust=False).mean()
    mkt = pd.Series(np.where((e9 > e21) & (b > e21), 1.0, np.where((e9 < e21) & (b < e21), -1.0, 0.0)), index=b.index)
    d["MKT"] = mkt.reindex(d.index, method="ffill").where(same_day).fillna(0.0)
    return d


def build_votes(d, has_vol):
    """Six pillars -> (pillar, weight, bullish mask, bearish mask, bull text, bear text).
    Trend-following only: oversold/overbought readings live in the VWAPMR setup instead of fighting the trend vote."""
    c, o = d.Close, d.Open
    green, red = c > o, c < o
    V = [
        # 1. Moving averages: trend direction
        ("Moving averages", 1.0, d.EMA9 > d.EMA21, d.EMA9 < d.EMA21, "EMA9 > EMA21", "EMA9 < EMA21"),
        ("Moving averages", 0.5, (c > d.EMA50) & (d.EMA21 > d.EMA50), (c < d.EMA50) & (d.EMA21 < d.EMA50),
         "Price & EMA21 above EMA50", "Price & EMA21 below EMA50"),
        ("Moving averages", 1.5, d.STdir == 1, d.STdir == -1, "Supertrend(10,3) up", "Supertrend(10,3) down"),
        ("Moving averages", 1.0 if has_vol else 0.5, c > d.VWAP, c < d.VWAP, "Above VWAP", "Below VWAP"),
        ("Moving averages", 1.0, (d.PDI > d.MDI) & (d.ADX > 20), (d.MDI > d.PDI) & (d.ADX > 20),
         "+DI > -DI, ADX > 20", "-DI > +DI, ADX > 20"),
        ("Moving averages", 1.5, d.HTF15 > 0, d.HTF15 < 0, "15-min close above EMA21", "15-min close below EMA21"),
        ("Moving averages", 1.0, d.HTF60 > 0, d.HTF60 < 0, "60-min close above EMA21", "60-min close below EMA21"),
        # 2. RSI: momentum strength
        ("RSI", 0.5, d.RSI > 55, d.RSI < 45, "RSI(14) > 55 (bull range)", "RSI(14) < 45 (bear range)"),
        ("RSI", 0.5, (d.RSI > 50) & (d.RSI > d.RSI.shift(3)), (d.RSI < 50) & (d.RSI < d.RSI.shift(3)),
         "RSI above 50 and rising", "RSI below 50 and falling"),
        ("RSI", 0.5, d.CCI > 100, d.CCI < -100, "CCI(20) > +100", "CCI(20) < -100"),
        # 3. MACD: momentum change
        ("MACD", 1.0, (d.MACDh > 0) & (d.MACDh >= d.MACDh.shift()), (d.MACDh < 0) & (d.MACDh <= d.MACDh.shift()),
         "MACD histogram positive & rising", "MACD histogram negative & falling"),
        ("MACD", 0.5, (d.MACD > d.MACDsig) & (d.MACD > 0), (d.MACD < d.MACDsig) & (d.MACD < 0),
         "MACD above signal and zero line", "MACD below signal and zero line"),
        # 5. Bollinger Bands & ATR: volatility
        ("Bollinger/ATR", 0.5, (c > d.BBm) & (d.BBw > d.BBw.shift()), (c < d.BBm) & (d.BBw > d.BBw.shift()),
         "Above BB middle, bands expanding", "Below BB middle, bands expanding"),
        ("Bollinger/ATR", 0.5, (d.ATR > d.ATRma) & green, (d.ATR > d.ATRma) & red,
         "ATR expanding on a green candle", "ATR expanding on a red candle"),
        # 6. Support, resistance & price structure
        ("Support/Resistance", 1.5, c > d.ORH, c < d.ORL, "Above opening-range high", "Below opening-range low"),
        ("Support/Resistance", 1.0, c > d.PDH, c < d.PDL, "Above previous-day high", "Below previous-day low"),
        ("Support/Resistance", 1.0, c > d.CPRtop, c < d.CPRbot, "Above CPR", "Below CPR"),
        ("Support/Resistance", 0.5, d.HHHL == 1, d.HHHL == -1, "Higher highs & higher lows", "Lower highs & lower lows"),
        ("Support/Resistance", 0.5, d.BullPat, d.BearPat, "Hammer / bullish engulfing", "Shooting star / bearish engulfing"),
    ]
    if has_vol:  # 4. Volume: participation
        V += [("Volume", 1.0, (d.RVOL > 1.5) & green, (d.RVOL > 1.5) & red,
               "Volume spike (RVOL > 1.5) on green candle", "Volume spike (RVOL > 1.5) on red candle"),
              ("Volume", 0.5, d.OBV > d.OBVema, d.OBV < d.OBVema, "OBV above its EMA", "OBV below its EMA"),
              ("Volume", 0.5, (d.MFI > 50) & (d.MFI < 80), (d.MFI < 50) & (d.MFI > 20),
               "MFI 50-80 (money flowing in)", "MFI 20-50 (money flowing out)")]
    return V


def setup_triggers(d, has_vol, P):
    """Each named setup -> +1 (CALL) / -1 (PUT) / 0 on the bar where it fires."""
    c, o, h, l, a = d.Close, d.Open, d.High, d.Low, d.ATR
    day = pd.Series(d.index.date, index=d.index)
    new_day = day.ne(day.shift())
    mins = pd.Series(d.index.hour * 60 + d.index.minute, index=d.index)
    vol_ok = d.RVOL.gt(1.2) if has_vol else pd.Series(True, index=d.index)
    xup = lambda lvl: c.gt(lvl) & c.shift().le(lvl.shift()) & ~new_day
    xdn = lambda lvl: c.lt(lvl) & c.shift().ge(lvl.shift()) & ~new_day
    sig = lambda bull, bear: pd.Series(np.where(bull, 1, np.where(bear, -1, 0)), index=d.index)
    early = mins < P["orb_cut"]
    up_tr = (d.EMA9 > d.EMA21) & (d.STdir == 1) & (d.HTF15 >= 0)
    dn_tr = (d.EMA9 < d.EMA21) & (d.STdir == -1) & (d.HTF15 <= 0)
    fired = d.Squeeze.shift().eq(True) & ~d.Squeeze
    barno = d.groupby(day.values).cumcount()
    return pd.DataFrame({
        "ORB": sig(xup(d.ORH) & vol_ok & early, xdn(d.ORL) & vol_ok & early),
        "VWAPPB": sig(up_tr & (l.rolling(3).min() <= d.VWAP + 0.2 * a) & (c > d.VWAP) & (c > o) & (c > h.shift()),
                      dn_tr & (h.rolling(3).max() >= d.VWAP - 0.2 * a) & (c < d.VWAP) & (c < o) & (c < l.shift())),
        "STFLIP": sig((d.STdir == 1) & (d.STdir.shift() == -1) & (d.EMA9 > d.EMA21),
                      (d.STdir == -1) & (d.STdir.shift() == 1) & (d.EMA9 < d.EMA21)),
        "CPRBO": sig(xup(np.fmax(d.PDH, d.CPRtop)) & vol_ok, xdn(np.fmin(d.PDL, d.CPRbot)) & vol_ok),
        "SQZ": sig(fired & (d.MACDh > 0) & (c > d.BBm), fired & (d.MACDh < 0) & (c < d.BBm)),
        "VWAPMR": sig((c.shift() < d.VWAPl.shift()) & (c > d.VWAPl) & (d.RSI2.shift() < 10) & (barno >= 6),
                      (c.shift() > d.VWAPu.shift()) & (c < d.VWAPu) & (d.RSI2.shift() > 90) & (barno >= 6)),
    })


def entry_window(spec, P):
    op, _, sq = SESSIONS[spec["sess"]]
    if spec["sess"] == "nse" and P["use_time"]:
        return tmin(P["win_start"]), tmin(P["win_end"])
    return tmin(op) + 15, tmin(sq) - 30


def compute(df, spec, P, has_vol, bench=None):
    """Indicators + votes + setups + regime -> Signal (+1 CALL / -1 PUT / 0) for every closed bar.
    bench: NIFTY 50 closes on the same timeframe for the market/relative-strength votes (optional)."""
    d = add_market_context(add_indicators(df, has_vol), bench)
    votes = build_votes(d, has_vol)
    for pil in PILLARS:
        d["P_" + pil] = sum((w * (b.eq(True).astype(float) - r.eq(True).astype(float))
                             for cat, w, b, r, _, _ in votes if cat == pil), pd.Series(0.0, index=d.index))
    d["Score"] = d[["P_" + p for p in PILLARS]].sum(axis=1)
    S = setup_triggers(d, has_vol, P)
    lb = P["lookback"]
    R = S if lb <= 1 else S.replace(0, np.nan).groupby(d.index.date).ffill(limit=lb - 1).fillna(0)
    R = R[[k for k in R.columns if k in P["setups"]]].reindex(columns=S.columns, fill_value=0)
    d["TDir"] = np.sign(R[TREND_KEYS].sum(axis=1))
    d["MDir"] = R["VWAPMR"]
    d["Regime"] = np.select([(d.ADX >= P["adx_min"]) & (d.CHOP < 55), (d.ADX < 20) & (d.CHOP > 61.8)],
                            ["Trend", "Range"], "Mixed")
    ws, we = entry_window(spec, P)
    mins = d.index.hour * 60 + d.index.minute
    d["InWin"] = (mins >= ws) & (mins <= we)
    d["Ext"] = (d.Close - d.EMA21).abs() > 2.5 * d.ATR
    d["Need"] = P["th"] + np.where(d.Regime == "Mixed", 1.0, 0.0)
    ok = d.InWin & ~d.Ext & (d.Regime != "Range")
    long = ok & (d.TDir > 0) & (d.Score >= d.Need) & (d.HTF15 >= 0)
    short = ok & (d.TDir < 0) & (d.Score <= -d.Need) & (d.HTF15 <= 0)
    # Confirmation gates (CTX_FILTERS): the trade must not fight NIFTY's trend, must lead/lag NIFTY, and must agree
    # with the daily trend. Missing data (NIFTY itself, commodities, short history) never blocks.
    gate_l, gate_s = {}, {}
    for f in P.get("ctx_filter", CTX_DEFAULT):
        x = d[{"mkt": "MKT", "rs": "RS", "d1": "D1", "macd": "MACDh"}[f]]
        if f in ("rs", "macd"):  # strictly on the trade's side
            gate_l[f], gate_s[f] = (x > 0) | x.isna(), (x < 0) | x.isna()
        else:  # neutral (0) allowed
            gate_l[f], gate_s[f] = x.fillna(0) >= 0, x.fillna(0) <= 0
    pre_l, pre_s = long.copy(), short.copy()
    for f in gate_l:
        long &= gate_l[f]
        short &= gate_s[f]
    d["CtxBlock"] = ""
    for f in gate_l:  # name the checks that stopped an otherwise valid signal
        hit = (pre_l & ~gate_l[f]) | (pre_s & ~gate_s[f])
        d.loc[hit, "CtxBlock"] = d.loc[hit, "CtxBlock"] + "; " + CTX_FILTERS[f]
    d["CtxBlock"] = d.CtxBlock.str.lstrip("; ")
    rng = d.InWin & (d.Regime == "Range")
    mrl = rng & (d.MDir > 0) & (d.Score > -P["th"] / 2)
    mrs = rng & (d.MDir < 0) & (d.Score < P["th"] / 2)
    d["Signal"] = np.where(long | mrl, 1, np.where(short | mrs, -1, 0))
    d["SigType"] = np.where(long | short, "trend", np.where(mrl | mrs, "mr", ""))
    sigv, Rv = d.Signal.to_numpy(), R.to_numpy()
    d["Setups"] = [", ".join(SETUP_LABEL[k] for j, k in enumerate(R.columns) if Rv[i, j] == s) if s else ""
                   for i, s in enumerate(sigv)]
    d["NSetups"] = [(Rv[i] == s).sum() if s else 0 for i, s in enumerate(sigv)]
    # Stop distance: Supertrend line (structure) bounded to 0.75-2.5 ATR; reversion trades stop beyond the band
    c, a, sg = d.Close, d.ATR, d.Signal
    st_dist = pd.Series(np.where(sg > 0, c - d.ST, d.ST - c), index=d.index).where(d.STdir == sg)
    trend_dist = st_dist.fillna(P["atr_mult"] * a).clip(lower=P["stop_min"] * a, upper=max(2.5, P["stop_min"] + 1) * a)
    mr_dist = (pd.Series(np.where(sg > 0, c - d.VWAPl, d.VWAPu - c), index=d.index) + 0.5 * a).clip(0.75 * a, 2 * a)
    d["StopDist"] = np.where(d.SigType == "mr", mr_dist, trend_dist)
    d["T2Dist"] = np.where(d.SigType == "mr", np.maximum((d.VWAP - c).abs(), d.StopDist), P["rr"] * d.StopDist)
    d = d[d[CORE].notna().all(axis=1)]
    return d.iloc[30:] if len(d) > 90 else d, votes


# ---------------- Options ----------------
def bs(S, K, T, sig, kind, r=0.065):
    """Black-Scholes price and delta (r=0 gives Black-76 for options on futures)."""
    N = lambda x: 0.5 * (1 + erf(x / sqrt(2)))
    if T <= 0 or sig <= 0:
        itm = S > K if kind == "CE" else S < K
        return (max(S - K, 0) if kind == "CE" else max(K - S, 0)), (1.0 if itm else 0.0) * (1 if kind == "CE" else -1)
    d1 = (log(S / K) + (r + sig ** 2 / 2) * T) / (sig * sqrt(T))
    d2 = d1 - sig * sqrt(T)
    if kind == "CE":
        return S * N(d1) - K * exp(-r * T) * N(d2), N(d1)
    return K * exp(-r * T) * N(-d2) - S * N(-d1), N(d1) - 1


def realized_vol(d, spec, interval):
    """Annualised intraday realized volatility (overnight gaps excluded); 0 when there are too few bars."""
    op, cl, _ = SESSIONS[spec["sess"]]
    bars_day = (tmin(cl) - tmin(op)) / int(interval[:-1])
    lr = np.log(d.Close).diff()[pd.Series(d.index.date, index=d.index).eq(pd.Series(d.index.date, index=d.index).shift())]
    return float(lr.std() * sqrt(bars_day * 252)) if lr.notna().sum() > 20 else 0.0


def estimate_iv(spec, d, P, chain, vix, interval):
    if P["iv"]:
        return P["iv"] / 100, "manual"
    if chain and chain.get("atm_iv"):
        return chain["atm_iv"], "NSE ATM IV"
    rv = realized_vol(d, spec, interval)
    if spec["kind"] == "index" and vix:
        return vix / 100 * (1.2 if spec["name"] == "BANKNIFTY" else 1.0), "India VIX"
    floor = {"index": 0.12, "stock": 0.20, "commodity": 0.25}[spec["kind"]]
    return max(rv * 1.2, floor), "realized vol x1.2"


def option_dte(chain, P, now):
    """Days to expiry: from the live NSE expiry (15:30 IST) when available, else the sidebar value."""
    if chain:
        hh, mm = chain.get("exp_hm", (15, 30))
        exp_dt = pd.Timestamp(datetime.strptime(chain["expiry"], "%d-%b-%Y")).tz_localize(IST) + pd.Timedelta(hours=hh, minutes=mm)
        return max((exp_dt - now).total_seconds() / 86400, 0.02)
    return max(P["dte"], 0.1)


def pick_strike(entry, kind, T, iv, r, chain, spec, P):
    step = P["step"] or (chain or {}).get("step") or spec["step"] or auto_step(entry)
    if chain and chain["quotes"]:
        strikes = sorted(chain["quotes"])
    else:
        atm0 = round(entry / step) * step
        strikes = [atm0 + k * step for k in range(-10, 11) if atm0 + k * step > 0] or [step]
    i = strikes.index(min(strikes, key=lambda k: abs(k - entry)))
    near = strikes[max(0, i - 10): i + 11]
    if P["strike_mode"] == "Delta target":
        return min(near, key=lambda k: abs(abs(bs(entry, k, T, iv, kind, r)[1]) - P["delta"]))
    sh = {"ATM": 0, "1 step ITM": 1, "2 steps ITM": 2, "1 step OTM": -1}[P["strike_mode"]]
    return strikes[min(max(i - sh if kind == "CE" else i + sh, 0), len(strikes) - 1)]


def live_premium(chain, K, kind):
    """Mid of bid/ask when the book is sane, else last traded price, else None."""
    q = chain["quotes"].get(K, {}).get(kind) if chain else None
    if not q:
        return None
    return (q["bid"] + q["ask"]) / 2 if q["bid"] > 0 and q["ask"] >= q["bid"] else (q["ltp"] or None)


def option_plan(spec, row, ltp, P, chain, iv, now):
    dr = int(row.Signal)
    kind = "CE" if dr > 0 else "PE"
    R = float(row.StopDist)
    entry = ltp
    # T1 (book half, stop to breakeven) at T1_R x the stop distance: 0.5R lifted the option win rate from ~47% to
    # ~60% in a 41-symbol, 120-day option-priced test, positive in both halves (see T1_R)
    stop, t1, t2 = entry - dr * R, entry + dr * P.get("t1_r", T1_R) * R, entry + dr * float(row.T2Dist)
    notes = []
    dte = option_dte(chain, P, now)
    if chain:
        wall = chain["call_wall"] if dr > 0 else chain["put_wall"]
        if (wall - entry) * dr > 0 and (t2 - wall) * dr > 0:
            notes.append(f"{'Call' if dr > 0 else 'Put'}-OI wall {wall:g} sits before T2: expect supply there, consider booking at it")
    # Room to the next support/resistance level in the trade's direction
    lvls = [row.SwingHi, row.PDH, row.R1, row.ORH, row.CPRtop] if dr > 0 else [row.SwingLo, row.PDL, row.S1, row.ORL, row.CPRbot]
    ahead = [x for x in lvls if pd.notna(x) and (x - entry) * dr > 0]
    if ahead:
        nxt = min(ahead, key=lambda x: abs(x - entry))
        if abs(nxt - entry) < R:
            notes.append(f"{'Resistance' if dr > 0 else 'Support'} {nxt:,.2f} is {abs(nxt - entry):,.2f} pts away, closer than "
                         f"the stop ({R:,.2f} pts): wait for a 15-min close {'above' if dr > 0 else 'below'} it")
    T, r = dte / 365, (0.0 if spec["kind"] == "commodity" else 0.065)
    K = pick_strike(entry, kind, T, iv, r, chain, spec, P)
    model, delta = bs(entry, K, T, iv, kind, r)
    q = chain["quotes"].get(K, {}).get(kind, {}) if chain else {}
    if q.get("delta") is not None:  # broker greeks (Upstox) beat the model
        delta = q["delta"]
    live = live_premium(chain, K, kind)
    prem = live or model
    cal = prem / model if live and model > 0.05 else 1.0  # calibrate the model to the live quote
    Te = max(T - P["hold"] / 24 / 365, 1e-6)
    p_sl_u = bs(stop, K, Te, iv, kind, r)[0] * cal
    p_sl = max(p_sl_u, prem * (1 - P["max_loss"]))
    if p_sl > p_sl_u + 1e-9:
        notes.append(f"Premium stop (-{P['max_loss'] * 100:.0f}%) is tighter than the underlying stop")
    p_t1, p_t2 = bs(t1, K, Te, iv, kind, r)[0] * cal, bs(t2, K, Te, iv, kind, r)[0] * cal
    theta = (model - bs(entry, K, max(T - 1 / 365, 1e-6), iv, kind, r)[0]) * cal
    if q.get("theta") is not None:
        theta = -q["theta"]
    lot = P["lot"] or (chain or {}).get("lot") or spec["lot"] or 1
    budget = P["capital"] * P["risk_pct"] / 100
    risk_lot = max(prem - p_sl, 1e-9) * lot
    lots = int(min(budget // risk_lot, P["capital"] // max(prem * lot, 1e-9)))
    if lots == 0:
        notes.append(f"1 lot risks ₹{risk_lot:,.0f}, more than the ₹{budget:,.0f} risk budget: skip or size up capital")
    if dte < 1:
        notes.append("Expiry day: gamma and theta are extreme. Prefer ITM and exit fast")
    if abs(delta) < 0.3:
        notes.append(f"Low delta {delta:+.2f}: the option barely moves with the underlying")
    return dict(kind=kind, strike=K, prem=prem, live=live is not None, model=model, delta=delta, theta=theta, pop=q.get("pop"),
                p_sl=p_sl, p_t1=p_t1, p_t2=p_t2, stop=stop, t1=t1, t2=t2, entry=entry, lot=lot, lots=lots,
                risk=risk_lot * lots, dte=dte, expiry=(chain or {}).get("expiry"), notes=notes)


# ---------------- Intraday targets & CALL / PUT analysis ----------------
def session_left_years(spec, now):
    """Remaining session as a fraction of a 252-day trading year (for IV-based expected move)."""
    op, cl, _ = SESSIONS[spec["sess"]]
    total = tmin(cl) - tmin(op)
    left = tmin(cl) - (now.hour * 60 + now.minute) if now.weekday() < 5 else 0
    left = left if 0 < left <= total else total  # market closed -> project the next full session
    return left / total / 252, left


def touch_prob(S, L, sig, t):
    """Probability price touches level L before the close: reflection principle, driftless GBM."""
    if sig <= 0 or t <= 0:
        return 0.0
    z = abs(log(L / S)) / (sig * sqrt(t))
    return float(2 * (1 - 0.5 * (1 + erf(z / sqrt(2)))))


def price_targets(row, ltp, iv, chain, spec, now, n=3):
    """Upside / downside target zones at least 0.5 ATR away: levels from several methods, merged when within
    0.4 ATR of the zone's first level (confluence), so successive targets are meaningfully spaced."""
    t, mins_left = session_left_years(spec, now)
    em = ltp * iv * sqrt(t)  # 1-sigma expected move to the close, from implied volatility
    orr = (row.ORH - row.ORL) if pd.notna(row.ORH) and pd.notna(row.ORL) else np.nan
    cand = [("R1", row.R1), ("R2", row.R2), ("R3", row.R3), ("S1", row.S1), ("S2", row.S2), ("S3", row.S3),
            ("Pivot", row.Pivot), ("CPR top", row.CPRtop), ("CPR bottom", row.CPRbot),
            ("Camarilla H3", row.CamH3), ("Camarilla H4", row.CamH4), ("Camarilla L3", row.CamL3),
            ("Camarilla L4", row.CamL4), ("Prev-day high", row.PDH), ("Prev-day low", row.PDL),
            ("Prev close", row.PDC), ("OR high", row.ORH), ("OR low", row.ORL),
            ("OR high + 1×OR", row.ORH + orr), ("OR low − 1×OR", row.ORL - orr),
            ("Swing high", row.SwingHi), ("Swing low", row.SwingLo), ("VWAP +2σ", row.VWAPu), ("VWAP −2σ", row.VWAPl),
            ("VWAP", row.VWAP), ("IV +1σ to close", ltp + em), ("IV −1σ to close", ltp - em),
            ("IV +2σ to close", ltp + 2 * em), ("IV −2σ to close", ltp - 2 * em)]
    pdr = row.PDH - row.PDL  # Fibonacci retracements / extensions of the previous day's range
    if pd.notna(pdr) and pdr > 0:
        cand += [(f"Fib {f * 100:.1f}%", row.PDL + f * pdr) for f in (0.382, 0.5, 0.618)]
        cand += [(f"Fib ext {(1 + f) * 100:.1f}%", row.PDH + f * pdr) for f in (0.272, 0.618)]
        cand += [(f"Fib ext −{(1 + f) * 100:.1f}%", row.PDL - f * pdr) for f in (0.272, 0.618)]
    if chain:
        cand += [("Call-OI wall", chain["call_wall"]), ("Put-OI wall", chain["put_wall"]), ("Max pain", chain["max_pain"])]
    cand = [(lbl, float(v)) for lbl, v in cand if pd.notna(v) and v > 0]
    tol, sig = 0.4 * row.ATR, iv

    def zones(side):
        pts = sorted([(lbl, v) for lbl, v in cand if (v - ltp) * side >= 0.5 * row.ATR], key=lambda x: abs(x[1] - ltp))
        out = []
        for lbl, v in pts:
            if out and abs(v - out[-1]["levels"][0]) <= tol:
                out[-1]["labels"].append(lbl); out[-1]["levels"].append(v)
            else:
                out.append(dict(labels=[lbl], levels=[v]))
        res = []
        for z in out[:n]:
            lvl = float(np.mean(z["levels"]))
            res.append(dict(level=lvl, labels=z["labels"], strength=len(z["labels"]),
                            dist=lvl - ltp, dist_atr=(lvl - ltp) / row.ATR, pct=(lvl / ltp - 1) * 100,
                            prob=touch_prob(ltp, lvl, sig, t)))
        return res

    return dict(up=zones(1), down=zones(-1), em=em, mins_left=mins_left, iv=iv)


def side_analysis(spec, row, ltp, P, chain, iv, now, tg):
    """Both sides at once: best CE and PE, live premium, greeks, breakeven, premium at each target."""
    dte = option_dte(chain, P, now)
    T, r = dte / 365, (0.0 if spec["kind"] == "commodity" else 0.065)
    Te = max(T - P["hold"] / 24 / 365, 1e-6)
    out = {}
    for kind, fav, adv in (("CE", tg["up"], tg["down"]), ("PE", tg["down"], tg["up"])):
        K = pick_strike(ltp, kind, T, iv, r, chain, spec, P)
        model, delta = bs(ltp, K, T, iv, kind, r)
        live = live_premium(chain, K, kind)
        prem = live or model
        cal = prem / model if live and model > 0.05 else 1.0
        theta = (model - bs(ltp, K, max(T - 1 / 365, 1e-6), iv, kind, r)[0]) * cal
        q = chain["quotes"].get(K, {}).get(kind, {}) if chain else {}
        if q.get("delta") is not None:
            delta = q["delta"]
        if q.get("theta") is not None:
            theta = -q["theta"]
        tgts = [dict(level=z["level"], prem=bs(z["level"], K, Te, iv, kind, r)[0] * cal, prob=z["prob"],
                     labels=z["labels"]) for z in fav]
        stop_lvl = adv[0]["level"] if adv else ltp - (1 if kind == "CE" else -1) * 1.5 * row.ATR
        p_sl = max(bs(stop_lvl, K, Te, iv, kind, r)[0] * cal, prem * (1 - P["max_loss"]))
        out[kind] = dict(strike=K, prem=prem, live=live is not None, delta=delta, theta=theta, iv_k=q.get("iv"), pop=q.get("pop"),
                         oi=q.get("oi"), doi=q.get("doi"), breakeven=K + prem if kind == "CE" else K - prem,
                         targets=tgts, stop_lvl=stop_lvl, p_sl=p_sl,
                         rr=((tgts[0]["prem"] - prem) / max(prem - p_sl, 1e-9)) if tgts else np.nan)
    # Verdict: technical bias first, then option-specific reasons
    s, need = row.Score, row.Need
    why = []
    if s >= need:
        verdict = "CALL favoured"
    elif s <= -need:
        verdict = "PUT favoured"
    else:
        verdict = "Neither: no clear edge (theta decays both sides)"
    why.append(f"Confluence score {s:+.1f} vs ±{need:.1f} needed")
    if chain and chain["pcr"]:
        why.append(f"PCR {chain['pcr']:.2f}: " + ("put writers active (supportive)" if chain["pcr"] > 1.1
                                                 else "call writers active (capping)" if chain["pcr"] < 0.8 else "balanced"))
    for kind, side in (("CE", "up"), ("PE", "down")):
        if tg[side]:
            z = tg[side][0]
            why.append(f"{'Upside' if kind == 'CE' else 'Downside'} T1 {z['level']:.2f} is {abs(z['dist_atr']):.1f} ATR away, "
                       f"{z['prob'] * 100:.0f}% touch probability by close")
    if tg["mins_left"] < 60:
        why.append(f"Only {tg['mins_left']} min of session left: little time for a target to be reached")
    return dict(verdict=verdict, why=why, dte=dte, sides=out)


def oi_table(chain, ltp, width=5):
    """Strikes around ATM with OI build-up; rising CE OI = call writing (resistance), rising PE OI = put writing (support)."""
    strikes = sorted(chain["quotes"])
    i = strikes.index(min(strikes, key=lambda k: abs(k - ltp)))
    rows = []
    for k in strikes[max(0, i - width): i + width + 1]:
        ce, pe = chain["quotes"][k].get("CE", {}), chain["quotes"][k].get("PE", {})
        read = []
        if ce.get("doi", 0) > 0 and ce.get("doi", 0) >= 1.5 * max(pe.get("doi", 0), 1):
            read.append("call writing → resistance")
        if pe.get("doi", 0) > 0 and pe.get("doi", 0) >= 1.5 * max(ce.get("doi", 0), 1):
            read.append("put writing → support")
        if ce.get("doi", 0) < 0 and k >= ltp:
            read.append("call unwinding → upside freeing")
        if pe.get("doi", 0) < 0 and k <= ltp:
            read.append("put unwinding → support weakening")
        rows.append({"CE OI": ce.get("oi"), "CE ΔOI": ce.get("doi"), "CE IV": ce.get("iv"), "CE LTP": ce.get("ltp"),
                     "Strike": f"{k:g}" + (" ◀ ATM" if k == strikes[i] else ""),
                     "PE LTP": pe.get("ltp"), "PE IV": pe.get("iv"), "PE ΔOI": pe.get("doi"), "PE OI": pe.get("oi"),
                     "Read": ", ".join(read) or "-"})
    return pd.DataFrame(rows)


# ---------------- News ----------------
FEEDS = {
    "Google News": "https://news.google.com/rss/search?q={q}+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "Bing News": "https://www.bing.com/news/search?q={q}&format=rss",
    "Yahoo Finance": "https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US",
}


@st.cache_resource
def sentiment_analyzer():
    a = SentimentIntensityAnalyzer()
    a.lexicon.update({"surge": 2.0, "rally": 2.0, "plunge": -2.5, "slump": -2.0, "bullish": 2.0, "bearish": -2.0,
                      "upgrade": 1.8, "downgrade": -1.8, "profit": 1.5, "loss": -1.5, "fraud": -3.0, "probe": -1.5,
                      "record": 1.2, "oversupply": -1.5, "shortage": 1.2, "beat": 1.2, "miss": -1.2})
    return a


@st.cache_data(ttl=300, show_spinner=False)
def fetch_news(query, ticker, limit=8):
    out, seen, a = [], set(), sentiment_analyzer()
    for src, url in FEEDS.items():
        try:
            # fetch with a timeout (feedparser's own fetch has none, so one hung feed could stall the whole scan)
            r = net.session().get(url.format(q=urllib.parse.quote(query), t=urllib.parse.quote(ticker)), timeout=6,
                                  headers={"User-Agent": NSE_H["User-Agent"]})
            feed = feedparser.parse(r.content)
            for e in feed.entries[:limit]:
                k = e.get("title", "").lower()[:60]
                if k and k not in seen:
                    seen.add(k)
                    out.append(dict(source=src, title=e.title, link=e.get("link", ""),
                                    sentiment=a.polarity_scores(e.title)["compound"]))
        except Exception:
            continue
    return out


# ---------------- Backtest (trade-level, same signals as live) ----------------
def backtest(d, spec, P):
    """Enter next bar's open; stop checked before target inside a bar (conservative); book 50% at T1 (T1_R, default 0.5R) and move the
    stop to breakeven; rest at T2, Supertrend trail/flip, or square-off time. Results in R multiples after costs."""
    o, h, l, c = (d[x].to_numpy() for x in ("Open", "High", "Low", "Close"))
    sig, dist, t2d = d.Signal.to_numpy(), d.StopDist.to_numpy(), d.T2Dist.to_numpy()
    st_line, st_dir = d.ST.to_numpy(), d.STdir.to_numpy()
    setups, types, idx = d.Setups.to_numpy(), d.SigType.to_numpy(), d.index
    days = np.array(d.index.date)
    mins = (d.index.hour * 60 + d.index.minute).to_numpy()
    sqm = tmin(SESSIONS[spec["sess"]][2])
    cost = P["cost_pct"] / 100
    trades, pos, pend, n = [], None, None, len(d)
    per_day = {}
    for i in range(n):
        if pend is not None and per_day.get(days[i], 0) >= P["max_day"]:
            pend = None
        if pend is not None:
            if days[i] == pend["day"] and mins[i] < sqm:
                e, dr, R = o[i], pend["dir"], pend["dist"]
                run = pend["typ"] == "trend" and P["exit_mode"] == "Run to close"
                pos = dict(dir=dr, entry=e, R=R, stop=e - dr * R, t1=e + dr * P.get("t1_r", T1_R) * R,
                           t2=e + dr * (1e9 if run else pend["t2d"]), half=False,
                           booked=0.0, t_in=idx[i], setup=pend["setup"], typ=pend["typ"])
                per_day[days[i]] = per_day.get(days[i], 0) + 1
            pend = None
        if pos is not None:
            dr, e, R = pos["dir"], pos["entry"], pos["R"]
            adv, fav = (l[i], h[i]) if dr > 0 else (h[i], l[i])
            exit_px, why = None, ""
            if (adv - pos["stop"]) * dr <= 0:
                exit_px = o[i] if (o[i] - pos["stop"]) * dr < 0 else pos["stop"]
                why = "trail/breakeven" if pos["half"] else "stop"
            else:
                if not pos["half"] and (fav - pos["t1"]) * dr >= 0:
                    pos["half"], pos["stop"] = True, e
                    pos["booked"] += 0.5 * (pos["t1"] - e) * dr / R
                last_bar = i == n - 1 or days[i + 1] != days[i]
                if (fav - pos["t2"]) * dr >= 0:
                    exit_px, why = pos["t2"], "target"
                elif pos["typ"] == "trend" and P["exit_mode"] == "2R target + flip" and st_dir[i] != dr:
                    exit_px, why = c[i], "supertrend flip"
                elif mins[i] >= sqm or last_bar:
                    exit_px, why = c[i], "square-off"
                elif pos["half"] and pos["typ"] == "trend" and st_dir[i] == dr and (st_line[i] - pos["stop"]) * dr > 0:
                    pos["stop"] = st_line[i]
            if exit_px is not None:
                rest = 0.5 if pos["half"] else 1.0
                rr_ = pos["booked"] + rest * (exit_px - e) * dr / R - cost * e / R
                trades.append(dict(entry_time=pos["t_in"], exit_time=idx[i], side="CALL" if dr > 0 else "PUT",
                                   setup=pos["setup"], entry=round(e, 2), exit=round(exit_px, 2), exit_reason=why,
                                   t1_hit=bool(pos["half"]),
                                   R=round(rr_, 2), pct=round((exit_px - e) * dr / e * 100, 3)))
                pos = None
        if pos is None and pend is None and sig[i] != 0 and i + 1 < n and days[i + 1] == days[i]:
            pend = dict(dir=sig[i], dist=dist[i], t2d=t2d[i], day=days[i], setup=setups[i], typ=types[i])
    t = pd.DataFrame(trades)
    if t.empty:
        return t, None
    eq = t.R.cumsum()
    loss = -t.R[t.R < 0].sum()
    return t, dict(trades=len(t), win=(t.R > 0).mean(), exp=t.R.mean(), total=t.R.sum(), t1=t.t1_hit.mean(),
                   pf=(t.R[t.R > 0].sum() / loss) if loss > 0 else float("inf"), dd=(eq - eq.cummax()).min(),
                   avg_win=t.R[t.R > 0].mean() if (t.R > 0).any() else 0.0,
                   avg_loss=t.R[t.R < 0].mean() if (t.R < 0).any() else 0.0)


# ---------------- Option-priced back-test ----------------
def next_expiry(ts, weekly, mcx_day=None):
    """Next expiry 15:30 IST: Tuesday weekly (NIFTY), the month's last Tuesday (NSE monthly), or for MCX options the
    month's mcx_day (~17th crude, ~24th natural gas; approximate). Holiday shifts ignored."""
    d = ts.normalize().tz_localize(None)
    if mcx_day:
        e = d.replace(day=mcx_day)
        e = e if e >= d else (d.replace(day=1) + pd.offsets.MonthBegin(1)).replace(day=mcx_day)
        return (e + pd.Timedelta(hours=17)).tz_localize(IST)
    if weekly:
        e = d + pd.Timedelta(days=(1 - d.weekday()) % 7)
    else:
        e = None
        for m in range(3):
            first = d.replace(day=1) + pd.offsets.MonthBegin(m) if m else d.replace(day=1)
            last = first + pd.offsets.MonthEnd(0)
            cand = last - pd.Timedelta(days=(last.weekday() - 1) % 7)
            if cand >= d:
                e = cand
                break
    return (e + pd.Timedelta(hours=15, minutes=30)).tz_localize(IST)


def option_backtest(d, spec, P, ivr=1.3, cost=0.015):
    """The same signals and exits as backtest(), scored on a delta-0.55 option's premium: Black-Scholes with IV =
    trailing 5-session realized vol x ivr (held constant), next weekly/monthly expiry, 1.5% round-trip cost.
    -> (trades DataFrame with pnl as a fraction of premium, stats dict or None). A model, not real option prices."""
    o, h, l, c = (d[x].to_numpy() for x in ("Open", "High", "Low", "Close"))
    sig, dist, t2d = d.Signal.to_numpy(), d.StopDist.to_numpy(), d.T2Dist.to_numpy()
    idx, n = d.index, len(d)
    days = np.array(idx.date)
    mins = (idx.hour * 60 + idx.minute).to_numpy()
    sqm = tmin(SESSIONS[spec["sess"]][2])
    lr = np.log(d.Close).diff().where(pd.Series(days, index=idx).eq(pd.Series(days, index=idx).shift()))
    bpd = max(1, (tmin(SESSIONS[spec["sess"]][1]) - tmin(SESSIONS[spec["sess"]][0])) // int(P["interval"][:-1]))
    vol = (lr.rolling(bpd * 5, min_periods=bpd * 2).std() * sqrt(bpd * 252)).bfill().to_numpy()
    r = 0.0 if spec["kind"] == "commodity" else 0.065
    weekly = spec["name"] == "NIFTY"
    mcx_day = (17 if "CRUDE" in spec["name"] else 24) if spec["kind"] == "commodity" else None
    step = spec["step"] or auto_step(float(c[-1]))
    yrs = lambda t, exp: max((exp - t).total_seconds() / 86400 / 365, 1 / 365 / 48)
    t1r = P.get("t1_r", T1_R)
    out, i, per_day = [], 0, {}
    while i < n - 1:
        if sig[i] == 0 or days[i + 1] != days[i] or per_day.get(days[i], 0) >= P["max_day"] or mins[i + 1] >= sqm:
            i += 1
            continue
        dr, j0 = int(sig[i]), i + 1
        kind, S0, R = ("CE" if dr > 0 else "PE"), o[j0], dist[i]
        exp, iv = next_expiry(idx[j0], weekly, mcx_day), max(vol[i] * ivr, 0.08)
        if ROLL_DTE[0] <= (exp - idx[j0]).days <= ROLL_DTE[1]:  # same expiry roll as live calls
            exp = next_expiry(exp + pd.Timedelta(days=1), weekly, mcx_day)
        atm = round(S0 / step) * step
        K = min((atm + k * step for k in range(-8, 9) if atm + k * step > 0),
                key=lambda k: abs(abs(bs(S0, k, yrs(idx[j0], exp), iv, kind, r)[1]) - P["delta"]))
        prem = lambda S, t: bs(S, K, yrs(t, exp), iv, kind, r)[0]
        p0 = prem(S0, idx[j0])
        if p0 <= 0.5:
            i += 1
            continue
        per_day[days[i]] = per_day.get(days[i], 0) + 1
        sl_u, t1_u, t2_u = S0 - dr * R, S0 + dr * t1r * R, S0 + dr * t2d[i]
        half, booked, exit_p, why, j = False, 0.0, None, "", j0
        while j < n and days[j] == days[j0]:
            adv, fav = (l[j], h[j]) if dr > 0 else (h[j], l[j])
            stop_lvl = S0 if half else sl_u
            if (adv - stop_lvl) * dr <= 0:
                exit_p, why = prem(stop_lvl if (o[j] - stop_lvl) * dr >= 0 else o[j], idx[j]), "breakeven" if half else "stop"
            else:
                if not half and (fav - t1_u) * dr >= 0:
                    half, booked = True, 0.5 * (prem(t1_u, idx[j]) - p0)
                if (fav - t2_u) * dr >= 0:
                    exit_p, why = prem(t2_u, idx[j]), "target"
                elif mins[j] >= sqm or j + 1 >= n or days[j + 1] != days[j]:
                    exit_p, why = prem(c[j], idx[j]), "square-off"
            if exit_p is not None:
                break
            j += 1
        if exit_p is None:
            break
        pnl = (booked + (0.5 if half else 1.0) * (exit_p - p0)) / p0 - cost
        out.append(dict(entry_time=idx[j0], side="CALL" if dr > 0 else "PUT", strike=K, prem=round(p0, 2), iv=iv,
                        dte=round((exp - idx[j0]).total_seconds() / 86400, 1), exit_reason=why, t1_hit=half,
                        pnl_pct=round(pnl * 100, 2)))
        i = j + 1
    t = pd.DataFrame(out)
    if t.empty:
        return t, None
    # Under a day to expiry a constant-IV model badly overstates option gains (real expiry-day IV and spreads move
    # against the buyer), so those trades stay in the list but are left out of the statistics
    p = t.pnl_pct[t.dte >= 1] / 100
    if p.empty:
        return t, None
    loss = -p[p < 0].sum()
    return t, dict(trades=len(p), win=float((p > 0).mean()), avg=float(p.mean()), t1=float(t.t1_hit[t.dte >= 1].mean()),
                   floor=float(p.mean() - 1.645 * p.std(ddof=1) / sqrt(len(p))) if len(p) > 1 else -1.0,
                   pf=float(p[p > 0].sum() / loss) if loss > 0 else float("inf"))


# ---------------- Chart ----------------
CHART_OVERLAYS = ("EMA", "VWAP", "Supertrend", "OR", "PDH/PDL", "Signals")  # make_chart(overlays=...) names
_OV_LINES = {"EMA": ("EMA9", "EMA21"), "VWAP": ("VWAP", "VWAPu", "VWAPl"), "OR": ("ORH", "ORL"), "PDH/PDL": ("PDH", "PDL")}


def make_chart(d, name, spec, has_vol, levels=None, compact=False, overlays=None):
    """Candles + overlays; the full stack adds volume, confluence, RSI, MACD and ADX panes (980px).
    compact: price (+ volume) only, 560px, dark. overlays: CHART_OVERLAYS names to draw (None = all).
    levels: [(label, price, colour, dash[, note])] drawn as right-labelled lines on the price pane; levels far outside
    the visible bars are skipped and their labels listed in fig.layout.meta['off']."""
    x = d.tail(220).copy()
    x.index = x.index.tz_localize(None)
    on = (lambda k: True) if overlays is None else (lambda k: k in overlays)
    if compact:
        rows, heights = (2, [.8, .2]) if has_vol else (1, [1])
    else:
        rows = 6 if has_vol else 5
        heights = [.42, .1, .12, .12, .12, .12][:rows] if has_vol else [.46, .12, .14, .14, .14]
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, row_heights=heights, vertical_spacing=.015)
    fig.add_trace(go.Candlestick(x=x.index, open=x.Open, high=x.High, low=x.Low, close=x.Close, name=name), 1, 1)
    for col, color, dash in [("EMA9", "#f39c12", None), ("EMA21", "#8e44ad", None), ("VWAP", "#2980b9", None),
                             ("VWAPu", "#2980b9", "dot"), ("VWAPl", "#2980b9", "dot"), ("ORH", "#27ae60", "dash"),
                             ("ORL", "#c0392b", "dash"), ("PDH", "#16a085", "dot"), ("PDL", "#d35400", "dot")]:
        if on(next(k for k, cs in _OV_LINES.items() if col in cs)):
            fig.add_trace(go.Scatter(x=x.index, y=x[col], name=col, line=dict(width=1, color=color, dash=dash)), 1, 1)
    if on("Supertrend"):
        fig.add_trace(go.Scatter(x=x.index, y=x.ST.where(x.STdir == 1), name="ST up", line=dict(width=1.5, color="#27ae60")), 1, 1)
        fig.add_trace(go.Scatter(x=x.index, y=x.ST.where(x.STdir == -1), name="ST down", line=dict(width=1.5, color="#c0392b")), 1, 1)
    fresh = x.Signal.ne(0) & x.Signal.ne(x.Signal.shift())
    for s, sym_, col, ycol, off in [(1, "triangle-up", "#1e8449", "Low", -1), (-1, "triangle-down", "#b03a2e", "High", 1)]:
        if not on("Signals"):
            break
        m = x[fresh & (x.Signal == s)]
        fig.add_trace(go.Scatter(x=m.index, y=m[ycol] + off * 0.3 * m.ATR, mode="markers", name="CALL" if s > 0 else "PUT",
                                 marker=dict(symbol=sym_, size=12, color=col), text=m.Setups), 1, 1)
    off_view = []
    if levels and len(x):
        lo, hi = float(x.Low.min()), float(x.High.max())
        band = max(hi - lo, 0.01 * abs(float(x.Close.iloc[-1])))  # keep far levels from squashing the candles
        for lv in levels:
            lbl, y, color, dash = lv[:4]
            try:
                y = float(y)
            except (TypeError, ValueError):
                continue
            if not np.isfinite(y):
                continue
            if not lo - band <= y <= hi + band:
                off_view.append(f"{lbl} {y:,.2f}")
                continue
            note = lv[4] if len(lv) > 4 and lv[4] else ""
            fig.add_hline(y=y, row=1, col=1, line_color=color, line_dash=dash, line_width=1, opacity=.9,
                          annotation_text=f"{lbl} {y:,.2f}" + (f" · {note}" if note else ""),
                          annotation_position="top right", annotation_font=dict(size=10, color=color))
    op, cl, _ = SESSIONS[spec["sess"]]
    rb = [dict(bounds=["sat", "mon"]), dict(bounds=[cl.hour + cl.minute / 60, op.hour + op.minute / 60], pattern="hour")]
    if compact:
        if has_vol:
            fig.add_trace(go.Bar(x=x.index, y=x.Volume, name="Volume", showlegend=False,
                                 marker_color=np.where(x.Close >= x.Open, "#26a69a", "#ef5350")), 2, 1)
        fig.update_xaxes(rangebreaks=rb, gridcolor="#1e2d4a")
        fig.update_yaxes(gridcolor="#1e2d4a", side="right")
        fig.update_layout(height=560, template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          xaxis_rangeslider_visible=False, margin=dict(l=6, r=6, t=26, b=6), font=dict(size=11),
                          legend=dict(orientation="h", y=1.0, yanchor="bottom", x=0, font=dict(size=10)),
                          meta=dict(off=off_view))
        return fig
    r = 2
    if has_vol:
        fig.add_trace(go.Bar(x=x.index, y=x.Volume, name="Volume",
                             marker_color=np.where(x.Close >= x.Open, "#27ae60", "#c0392b")), r, 1)
        r += 1
    fig.add_trace(go.Bar(x=x.index, y=x.Score, name="Confluence", marker_color=np.where(x.Score >= 0, "#27ae60", "#c0392b")), r, 1)
    fig.add_hline(y=x.Need.iloc[-1], line_dash="dot", row=r, col=1)
    fig.add_hline(y=-x.Need.iloc[-1], line_dash="dot", row=r, col=1)
    fig.add_trace(go.Scatter(x=x.index, y=x.RSI, name="RSI", line=dict(color="#8e44ad")), r + 1, 1)
    fig.add_hline(y=70, line_dash="dot", row=r + 1, col=1)
    fig.add_hline(y=30, line_dash="dot", row=r + 1, col=1)
    fig.add_trace(go.Bar(x=x.index, y=x.MACDh, name="MACD hist"), r + 2, 1)
    fig.add_trace(go.Scatter(x=x.index, y=x.MACD, name="MACD", line=dict(width=1)), r + 2, 1)
    fig.add_trace(go.Scatter(x=x.index, y=x.MACDsig, name="Signal", line=dict(width=1)), r + 2, 1)
    for col, color in [("ADX", "#2c3e50"), ("PDI", "#27ae60"), ("MDI", "#c0392b"), ("CHOP", "#95a5a6")]:
        fig.add_trace(go.Scatter(x=x.index, y=x[col], name=col, line=dict(width=1, color=color)), r + 3, 1)
    fig.update_xaxes(rangebreaks=rb)
    fig.update_layout(height=980, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", y=1.02))
    if off_view:
        fig.update_layout(meta=dict(off=off_view))
    return fig


# ---------------- Data source routing (Zerodha Kite when connected, else Yahoo / NSE) ----------------
KITE = None  # set by the sidebar when a Kite session is live
UPSTOX = None  # Upstox access token, set by the sidebar when an Upstox session is live


def kite_day():
    return pd.Timestamp.now(tz=IST).date().isoformat()


def get_prices(spec, interval, period):
    """-> (raw OHLCV, source). Broker prices are exchange-native (MCX already in INR). Upstox > Kite > Yahoo."""
    if UPSTOX:
        u = uf.underlying(spec["name"], spec["kind"], kite_day())
        if u:
            try:
                df = uf.candles(UPSTOX, u["key"], interval, period)
                if len(df):
                    return df, "Upstox"
            except Exception:
                pass
    if KITE is not None:
        u = kf.underlying(KITE, spec["name"], spec["kind"], kite_day())
        if u:
            try:
                df = kf.candles(KITE, u["token"], interval, period)
                if len(df):
                    return df, "Kite"
            except Exception:
                pass
    return load_prices(spec["ticker"], interval, period), "Yahoo"


@st.cache_resource
def _oi_open():
    return {}  # first OI seen per contract today -> ΔOI for Kite (Kite quotes carry no previous-day OI)


# Expiry roll: when the nearest expiry is 3-7 days away, trade the next one. On real Upstox option prices (41
# symbols, 120 days, 1,200 trades) contracts 3-7 days from expiry lost -6.9% of premium per trade versus about -2%
# further out, and rolling them lifted the average from -2.27% to -1.39% (train -4.3% -> -3.3%, test +1.6% -> +2.1%).
ROLL_DTE = (3, 7)


def _mcx_chain(spec, ltp, now):
    """Live MCX option chain (Upstox quotes) with IV from the prices: real premiums for CRUDE / NATURALGAS calls."""
    raw = uf.mcx_option_chain(UPSTOX, spec["name"], kite_day())
    if raw:
        dte = (datetime.strptime(raw["expiry"], "%d-%b-%Y").date() - now.date()).days
        if ROLL_DTE[0] <= dte <= ROLL_DTE[1]:
            nxt = (datetime.strptime(raw["expiry"], "%d-%b-%Y").date() + pd.Timedelta(days=1)).isoformat()
            raw = uf.mcx_option_chain(UPSTOX, spec["name"], kite_day(), min_expiry=nxt) or raw
    if not raw:
        return None
    S = raw["spot"] or ltp
    T = option_dte(dict(expiry=raw["expiry"], exp_hm=(23, 30)), {"dte": 0}, now) / 365
    for k, sides in raw["quotes"].items():
        for side, q in sides.items():
            px = (q["bid"] + q["ask"]) / 2 if q["bid"] > 0 and q["ask"] >= q["bid"] else q["ltp"]
            v = implied_vol(px, S, k, T, side, 0.0) if px else None
            q["iv"] = round(v * 100, 1) if v else 0.0
    return summarize_chain(raw["quotes"], S, raw["expiry"], raw["lot"], "Upstox MCX", (23, 30))


def get_chain(spec, ltp, now):
    if UPSTOX and spec["kind"] == "commodity":
        try:
            ch = _mcx_chain(spec, ltp, now)
            if ch:
                return ch
        except Exception:
            pass
    if UPSTOX:
        u = uf.underlying(spec["name"], spec["kind"], kite_day())
        if u and u["chain_key"]:
            raw = uf.option_chain(UPSTOX, u["chain_key"], kite_day())
            if raw:
                dte = (datetime.strptime(raw["expiry"], "%d-%b-%Y").date() - now.date()).days
                if ROLL_DTE[0] <= dte <= ROLL_DTE[1]:  # decaying week: price the next expiry instead
                    nxt = (datetime.strptime(raw["expiry"], "%d-%b-%Y").date() + pd.Timedelta(days=1)).isoformat()
                    raw = uf.option_chain(UPSTOX, u["chain_key"], kite_day(), min_expiry=nxt) or raw
            if raw:  # Upstox supplies IV, greeks and previous-day OI directly
                return summarize_chain(raw["quotes"], raw["spot"] or ltp, raw["expiry"], raw["lot"], "Upstox")
    if KITE is not None:
        u = kf.underlying(KITE, spec["name"], spec["kind"], kite_day())
        if u:
            raw = kf.option_quotes(KITE, u["opt_name"], u["opt_exch"], float(f"{ltp:.3g}"), kite_day())
            if raw and raw["quotes"]:
                tmp = dict(expiry=raw["expiry"], exp_hm=raw["exp_hm"])
                T = option_dte(tmp, {"dte": 0}, now) / 365
                r = 0.0 if spec["kind"] == "commodity" else 0.065
                first, today = _oi_open(), kite_day()
                for k, sides in raw["quotes"].items():
                    for side, q in sides.items():
                        px = (q["bid"] + q["ask"]) / 2 if q["bid"] > 0 and q["ask"] >= q["bid"] else q["ltp"]
                        v = implied_vol(px, ltp, k, T, side, r) if px else None
                        q["iv"] = round(v * 100, 1) if v else 0.0
                        key = (today, u["opt_name"], k, side)
                        first.setdefault(key, q["oi"])
                        q["doi"] = q["oi"] - first[key]
                return summarize_chain(raw["quotes"], ltp, raw["expiry"], raw["lot"], "Kite", raw["exp_hm"])
    if spec["nse"]:
        return nse_chain(spec["nse"], spec["kind"] == "index")
    return None


# ---------------- Analysis per symbol ----------------
def benchmark(spec, interval, period):
    """NIFTY 50 closes on the same timeframe for relative strength / market confirmation; None for NIFTY itself,
    for commodities (different session and driver) or when the index can't be loaded."""
    if spec["kind"] == "commodity" or spec["name"] == "NIFTY":
        return None
    try:
        nspec = resolve("NIFTY")
        raw, _ = get_prices(nspec, interval, period)
        return prepare(raw, nspec, interval, None)[0].Close if len(raw) else None
    except Exception:
        return None


def analyze(sym, P, vix, fx, now):
    spec = resolve(sym)
    period = "7d" if P["interval"] == "1m" else P["period"]  # Yahoo serves 1m candles for ~7 days only
    raw, src = get_prices(spec, P["interval"], period)
    if raw.empty:
        return dict(spec=spec, error=f"no data from {src}")
    df, ltp = prepare(raw, spec, P["interval"], fx if src == "Yahoo" else None)  # only NYMEX proxies need USDINR
    if len(df) < 60:
        return dict(spec=spec, error=f"only {len(df)} session bars; choose a longer history")
    has_vol = bool(df.Volume.sum() > 0)
    d, votes = compute(df, spec, P, has_vol, benchmark(spec, P["interval"], period))
    if d.empty:
        return dict(spec=spec, error="not enough bars after indicator warm-up")
    row = d.iloc[-1]
    chain = get_chain(spec, ltp, now) if P["chain"] else None
    news = fetch_news(spec["query"], spec["ticker"]) if P["news"] else []
    ns = float(np.mean([n["sentiment"] for n in news])) if news else 0.0
    iv, iv_src = estimate_iv(spec, d, P, chain, vix, P["interval"])
    why = [("+" if b.iloc[-1] == True else "-", cat, tb if b.iloc[-1] == True else tr)  # noqa: E712
           for cat, _, b, r, tb, tr in votes if b.iloc[-1] == True or r.iloc[-1] == True]  # noqa: E712
    # Live context (not part of the backtest): news + option-chain positioning
    ctx, sg = [], int(np.sign(row.Signal or row.Score))
    if abs(ns) >= 0.15:
        ctx.append(("+" if ns > 0 else "-", "News", f"Headline sentiment {ns:+.2f}"))
    if chain and chain["pcr"]:
        if chain["pcr"] > 1.2:
            ctx.append(("+", "Options OI", f"PCR {chain['pcr']:.2f}: heavy put writing (support)"))
        elif chain["pcr"] < 0.7:
            ctx.append(("-", "Options OI", f"PCR {chain['pcr']:.2f}: heavy call writing (resistance)"))
    agree = sum(1 if (s == "+") == (sg > 0) else -1 for s, _, _ in ctx) if sg else 0
    # Status, confidence, reasons
    notes = []
    if row.Signal > 0:
        status = "BUY CALL (CE)"
    elif row.Signal < 0:
        status = "BUY PUT (PE)"
    elif row.Score >= row.Need and row.TDir >= 0:
        status = "WATCH: CALL bias"
    elif row.Score <= -row.Need and row.TDir <= 0:
        status = "WATCH: PUT bias"
    else:
        status = "NO TRADE"
    if row.Signal == 0:
        ws, we = entry_window(spec, P)
        if not row.InWin:
            notes.append(f"Outside entry window {ws // 60:02d}:{ws % 60:02d}-{we // 60:02d}:{we % 60:02d} IST")
        if row.Regime == "Range" and row.MDir == 0:
            notes.append(f"Range regime (ADX {row.ADX:.0f}, CHOP {row.CHOP:.0f}): trend setups off, waiting for a VWAP-band reversion")
        if row.TDir == 0 and row.MDir == 0:
            notes.append(f"No setup trigger in the last {P['lookback']} closed bar(s)")
        elif row.TDir != 0 and row.Score * row.TDir < row.Need:
            notes.append(f"Setup fired but confluence {row.Score:+.1f} is short of ±{row.Need:.1f}")
        if row.TDir != 0 and row.HTF15 * row.TDir < 0:
            notes.append("15-min trend disagrees with the setup")
        if row.get("CtxBlock"):
            notes.append(f"Setup blocked by confirmation filter: {row.CtxBlock}")
        if row.Ext:
            notes.append(f"Price {abs(row.Close - row.EMA21) / row.ATR:.1f} ATR from EMA21: don't chase, wait for a pullback")
    if vix and vix > 22 and spec["kind"] != "commodity":
        notes.append(f"India VIX {vix:.1f}: premiums are expensive, prefer ITM strikes")
    age = (now - row.name).total_seconds() / 60 - int(P["interval"][:-1])
    if age > 20:
        notes.append(f"Last closed candle is {age:.0f} min old (market closed or feed delayed)")
    conf = 0
    if row.Signal != 0:
        dr = int(row.Signal)
        conf = (40 * min(abs(row.Score) / 9, 1) + 12 * min(row.NSetups, 2) + 12 * (row.HTF15 == dr)
                + 6 * (row.HTF60 == dr) + 6 * (row.Regime in ("Trend", "Range")) + 4 * agree)
        conf = int(np.clip(conf, 0, 100))
    plan = option_plan(spec, row, ltp, P, chain, iv, now) if row.Signal != 0 else None
    tg = price_targets(row, ltp, iv, chain, spec, now)
    sa = side_analysis(spec, row, ltp, P, chain, iv, now, tg)
    bt_trades, bt = backtest(d, spec, P)
    obt_trades, obt = option_backtest(d, spec, P)  # same signals scored on the option premium (model)
    rbt_trades, rbt = None, None
    if UPSTOX and spec["kind"] != "commodity" and P.get("real_bt", True):  # REAL premiums (Upstox expired options)
        try:
            import optdata  # lazy: optdata imports engine
            keyp = repr(sorted((k, str(P.get(k))) for k in ("interval", "ctx_filter", "win_end", "t1_r", "delta", "rr",
                                                             "setups", "th", "max_day")))
            rbt_trades, rbt = optdata.daily_real_bt(sym, now.date().isoformat(), keyp, d, spec, P, UPSTOX)
        except Exception:
            rbt_trades, rbt = None, None
    if plan and (bt is None or bt["trades"] < 15 or bt["exp"] <= 0):
        notes.append("These rules have NO proven edge on this symbol's loaded history "
                     + (f"({bt['trades']} trades, {bt['exp']:+.2f} R each)" if bt else "(no trades)")
                     + ": paper-trade it or skip")
        conf = min(conf, 40)
    ev = events.flags(spec, chain, now)
    for sev, text in ev:
        if sev == "high":
            notes.insert(0, "📅 " + text)
            if plan:
                plan["notes"].insert(0, "📅 " + text)
    if events.results_soon(ev):
        conf = min(conf, 25)
    return dict(spec=spec, d=d, ltp=ltp, row=row, status=status, conf=conf, why=why, ctx=ctx, notes=notes, plan=plan,
                chain=chain, news=news, ns=ns, iv=iv, iv_src=iv_src, has_vol=has_vol, bt=bt, bt_trades=bt_trades,
                tg=tg, sa=sa, src=src, events=ev, obt=obt, obt_trades=obt_trades, rbt=rbt,
                rbt_trades=rbt_trades)


@st.cache_data(ttl=90, show_spinner=False, max_entries=300)
def _analyze_cached(sym, p_key, src_key, vix, fx, minute, _P):
    return analyze(sym, _P, vix, fx, pd.Timestamp(minute))


def analyze_fast(sym, P, vix, fx, now):
    """analyze() memoised per symbol, settings, data source and minute: UI clicks don't re-run the whole scan."""
    p_key = repr(sorted((k, sorted(v) if isinstance(v, set) else str(v)) for k, v in P.items()))
    src_key = "upstox" if UPSTOX else "kite" if KITE is not None else "yahoo"
    minute = now.floor("min").isoformat()
    return _analyze_cached(sym, p_key, src_key, round(vix or 0, 2), round(fx or 0, 2), minute, P)


def analyze_many(syms, P, vix, fx, now, fn=None, workers=8, progress=None):
    """Analyse many symbols on a thread pool (the work is network-bound) -> {sym: analysis} in input order.
    fn defaults to analyze_fast; progress(i, n, sym) is called on the calling thread as each symbol finishes."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading
    fn = fn or analyze_fast
    try:  # let worker threads use Streamlit caches without "missing ScriptRunContext" warnings
        from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
        ctx = get_script_run_ctx()
        init = (lambda: add_script_run_ctx(threading.current_thread(), ctx)) if ctx else None
    except Exception:
        init = None
    out = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(syms))), initializer=init) as pool:
        futs = {pool.submit(fn, s, P, vix, fx, now): s for s in syms}
        for i, fu in enumerate(as_completed(futs), 1):
            s = futs[fu]
            try:
                out[s] = fu.result()
            except Exception as e:
                out[s] = dict(spec=resolve(s), error=f"{type(e).__name__}: {e}")
            if progress:
                progress(i, len(syms), s)
    return {s: out[s] for s in syms}


# ---------------- Momentum scanner & exit management ----------------
FNO_STOCKS = ("RELIANCE, HDFCBANK, ICICIBANK, INFY, TCS, SBIN, AXISBANK, KOTAKBANK, LT, ITC, BHARTIARTL, HINDUNILVR, "
              "BAJFINANCE, MARUTI, M&M, SUNPHARMA, TITAN, ULTRACEMCO, NTPC, POWERGRID, ONGC, TATASTEEL, JSWSTEEL, "
              "HCLTECH, WIPRO, TECHM, ADANIENT, ADANIPORTS, ASIANPAINT, BAJAJ-AUTO, HINDALCO, COALINDIA, DRREDDY, "
              "CIPLA, INDUSINDBK, BEL, TRENT, ETERNAL, DLF, VEDL")


@st.cache_data(ttl=120, show_spinner=False)
def load_many(tickers, interval, period):
    raw = yf.download(list(tickers), interval=interval, period=period, auto_adjust=True, progress=False,
                      group_by="ticker", threads=True)
    out = {}
    for t in tickers:
        try:
            df = raw[t].dropna() if isinstance(raw.columns, pd.MultiIndex) else raw.dropna()
            if len(df):
                out[t] = df
        except KeyError:
            continue
    return out


def day_change(df, ltp):
    """% change of ltp vs the previous session's close."""
    dates = pd.Series(df.index.date, index=df.index)
    prev = df.Close[dates < dates.iloc[-1]]
    return ltp / float(prev.iloc[-1]) - 1 if len(prev) else np.nan


def stock_view(row):
    """Buy / sell view for the stock itself (cash or futures) from the same engine."""
    if row.Signal > 0:
        return "BUY"
    if row.Signal < 0:
        return "SELL / SHORT"
    if row.Score >= row.Need and row.TDir >= 0:
        return "Momentum ↑ (wait for setup)"
    if row.Score <= -row.Need and row.TDir <= 0:
        return "Momentum ↓ (wait for setup)"
    return "Neutral"


def exit_advice(d, ltp, dr, entry, stop, spec, now):
    """When to sell: HOLD / BOOK PARTIAL & TRAIL / EXIT for an open position in direction dr (+1 long, -1 short)."""
    row, prev = d.iloc[-1], d.iloc[-2]
    if not stop:
        stop = entry - dr * 1.5 * row.ATR
    R = max(abs(entry - stop), 1e-9)
    gain = (ltp - entry) * dr / R
    side = "long" if dr > 0 else "short"
    ex, book = [], []
    if (ltp - stop) * dr <= 0:
        ex.append(f"Stop {stop:.2f} hit")
    if row.STdir != dr:
        ex.append("Supertrend flipped against the position")
    if (row.Close - row.VWAP) * dr < 0 and (row.Close - row.EMA21) * dr < 0:
        ex.append("Closed on the wrong side of both VWAP and EMA21")
    if row.MACDh * dr < 0 and prev.MACDh * dr < 0 and (row.RSI - 50) * dr < 0:
        ex.append("Momentum faded: MACD histogram and RSI both turned against the trade")
    if row.HTF15 * dr < 0:
        ex.append("15-min trend turned against the trade")
    if now.time() >= SESSIONS[spec["sess"]][2]:
        ex.append("Square-off time")
    if gain >= 1:
        book.append(f"Up {gain:.1f}R: book 50% and move the stop to cost")
    if (dr > 0 and (row.RSI > 75 or ltp >= row.VWAPu)) or (dr < 0 and (row.RSI < 25 or ltp <= row.VWAPl)):
        book.append("Stretched (RSI extreme or at VWAP ±2σ): book partial profits")
    lvls = [row.SwingHi, row.PDH, row.R1] if dr > 0 else [row.SwingLo, row.PDL, row.S1]
    near = [x for x in lvls if pd.notna(x) and 0 <= (x - ltp) * dr < 0.3 * row.ATR]
    if near:
        book.append(f"At {'resistance' if dr > 0 else 'support'} {near[0]:.2f}: book partial or tighten the stop")
    trail = stop
    if row.STdir == dr and (row.ST - trail) * dr > 0:
        trail = row.ST
    if gain >= 1 and (entry - trail) * dr > 0:
        trail = entry
    action = "EXIT" if ex else ("BOOK PARTIAL & TRAIL" if book else "HOLD")
    return dict(action=action, gain=gain, pnl_pts=(ltp - entry) * dr, stop=stop, trail=trail, exit=ex, book=book,
                side=side)


def implied_vol(price, S, K, T, kind, r):
    """Volatility implied by an option price (bisection on Black-Scholes); None if the price is below intrinsic."""
    intrinsic = max(S - K, 0) if kind == "CE" else max(K - S, 0)
    if price <= intrinsic + 1e-6 or T <= 0:
        return None
    lo, hi = 0.005, 3.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs(S, K, T, mid, kind, r)[0] > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def option_price_signal(a, kind, K, price, held, paid, P, now):
    """BUY / WAIT / AVOID (not held) or HOLD / BOOK PARTIAL / EXIT (held) for one CE or PE at a given market price."""
    spec, row, d, ltp, chain, iv, tg = a["spec"], a["row"], a["d"], a["ltp"], a["chain"], a["iv"], a["tg"]
    dr = 1 if kind == "CE" else -1
    dte = option_dte(chain, P, now)
    T, r = dte / 365, (0.0 if spec["kind"] == "commodity" else 0.065)
    if not K:
        K = pick_strike(ltp, kind, T, iv, r, chain, spec, dict(P, strike_mode="ATM"))
    live = live_premium(chain, K, kind)
    price = price or live or bs(ltp, K, T, iv, kind, r)[0]
    fair, delta = bs(ltp, K, T, iv, kind, r)
    piv = implied_vol(price, ltp, K, T, kind, r)
    rich = (price / fair - 1) if fair > 0.05 else 0.0
    Te = max(T - P["hold"] / 24 / 365, 1e-6)
    cal = price / fair if fair > 0.05 else 1.0  # project from the entered price, not the model's
    fav, adv = (tg["up"], tg["down"]) if dr > 0 else (tg["down"], tg["up"])
    tgts = [dict(level=z["level"], prem=bs(z["level"], K, Te, iv, kind, r)[0] * cal, prob=z["prob"]) for z in fav]
    stop_lvl = adv[0]["level"] if adv else ltp - dr * 1.5 * row.ATR
    base = paid if held and paid else price
    p_sl = max(bs(stop_lvl, K, Te, iv, kind, r)[0] * cal, base * (1 - P["max_loss"]))
    ex = exit_advice(d, ltp, dr, ltp, 0, spec, now)
    why_for, why_against = [], []
    # Direction from the engine
    if row.Signal == dr:
        why_for.append(f"Engine signal: {a['status']} ({row.Setups})")
    elif row.Signal == -dr:
        why_against.append(f"Engine signal is the opposite side: {a['status']}")
    if row.Score * dr >= row.Need:
        why_for.append(f"Confluence score {row.Score:+.1f} supports {'upside' if dr > 0 else 'downside'}")
    elif row.Score * dr <= -row.Need:
        why_against.append(f"Confluence score {row.Score:+.1f} points the other way")
    else:
        why_against.append(f"Confluence score {row.Score:+.1f} is not strong enough (±{row.Need:.1f})")
    why_against += ex["exit"]
    # Price quality
    if rich > 0.15:
        why_against.append(f"Price {price:.2f} is {rich * 100:.0f}% above fair value {fair:.2f}: overpaying")
    elif rich < -0.10:
        why_for.append(f"Price {price:.2f} is {-rich * 100:.0f}% below fair value {fair:.2f}")
    if piv and iv and piv > iv * 1.25:
        why_against.append(f"Implied vol at your price {piv * 100:.1f}% vs market {iv * 100:.1f}%: premium is expensive")
    if abs(delta) < 0.3:
        why_against.append(f"Delta {delta:+.2f}: far OTM, needs a big move and decays fast")
    if dte < 1:
        why_against.append("Expiry day: theta and gamma are extreme")
    if tg["mins_left"] < 45:
        why_against.append(f"Only {tg['mins_left']} min left in the session")
    pnl = None
    if held:
        pnl = (price - paid) if paid else None
        if paid and price <= paid * (1 - P["max_loss"]):
            action = "EXIT"; why_against.insert(0, f"Premium down {(1 - price / paid) * 100:.0f}% from {paid:.2f}: premium stop hit")
        elif ex["action"] == "EXIT" or row.Signal == -dr:
            action = "EXIT"
        elif (tgts and price >= tgts[0]["prem"]) or ex["book"] or (paid and price >= paid * 1.5):
            action = "BOOK PARTIAL"
            why_for += ex["book"] + ([f"Premium up {(price / paid - 1) * 100:.0f}%: book half, trail the rest"]
                                     if paid and price >= paid * 1.5 else [])
        else:
            action = "HOLD"
        if action == "EXIT" and paid and price > paid:
            why_for.insert(0, f"Exit locks in a {(price / paid - 1) * 100:+.0f}% gain ({price - paid:+.2f} per unit)")
    else:
        blockers = [w for w in why_against if "overpaying" in w or "opposite side" in w or "Supertrend" in w
                    or "Expiry day" in w or "min left" in w]
        if row.Signal == dr and not blockers:
            action = "BUY"
        elif row.Score * dr >= row.Need and not blockers:
            action = "WAIT"
            why_for.append("Momentum is there but no setup has triggered yet: buy on the next setup/pullback")
        else:
            action = "AVOID"
    return dict(kind=kind, strike=K, price=price, live=live, fair=fair, rich=rich, piv=piv, delta=delta, dte=dte,
                p_sl=p_sl, stop_lvl=stop_lvl, tgts=tgts, action=action, why_for=why_for, why_against=why_against,
                pnl=pnl, paid=paid, held=held)


# ---------------- Option spreads ----------------
SPREADS = ["Bull call spread (CE debit)", "Bear put spread (PE debit)", "Long CE", "Long PE"]


def strike_grid(entry, chain, spec, P):
    step = P.get("step") or (chain or {}).get("step") or spec["step"] or auto_step(entry)
    if chain and chain["quotes"]:
        return sorted(chain["quotes"]), step
    atm0 = round(entry / step) * step
    return [atm0 + k * step for k in range(-15, 16)], step


def spread_analysis(a, strategy, kb, ks, lots, P, now):
    """Debit spread or single long option: premiums (live when quoted), payoff, net greeks, breakeven, POP, sizing."""
    spec, ltp, iv, chain, tg, row = a["spec"], a["ltp"], a["iv"], a["chain"], a["tg"], a["row"]
    kind = "CE" if ("call" in strategy or strategy == "Long CE") else "PE"
    dr = 1 if kind == "CE" else -1
    single = strategy.startswith("Long")
    dte = option_dte(chain, P, now)
    T, r = dte / 365, (0.0 if spec["kind"] == "commodity" else 0.065)
    strikes, step = strike_grid(ltp, chain, spec, P)
    kb = kb or pick_strike(ltp, kind, T, iv, r, chain, spec, P)
    if not single and not ks:
        zs = tg["up"] if dr > 0 else tg["down"]
        lvl = zs[1]["level"] if len(zs) > 1 else zs[0]["level"] if zs else ltp + dr * 2 * row.ATR
        beyond = [k for k in strikes if (k - lvl) * dr >= 0 and (k - kb) * dr > 0]
        ks = min(beyond, key=lambda k: abs(k - lvl)) if beyond else kb + dr * 2 * step
    legs = [(1, kb)] if single else [(1, kb), (-1, ks)]
    info = []
    for sgn, K in legs:
        live = live_premium(chain, K, kind)
        model = bs(ltp, K, T, iv, kind, r)[0]
        info.append(dict(sgn=sgn, K=K, prem=live or model, live=live is not None,
                         cal=(live / model) if live and model > 0.05 else 1.0))
    debit = sum(x["sgn"] * x["prem"] for x in info)

    def value(S, t, sig=iv):
        return sum(x["sgn"] * bs(S, x["K"], max(t, 1e-6), sig, kind, r)[0] * x["cal"] for x in info)

    def payoff(S):
        return sum(x["sgn"] * (max(S - x["K"], 0) if kind == "CE" else max(x["K"] - S, 0)) for x in info)

    h = max(ltp * 0.001, 0.01)
    delta = (value(ltp + h, T) - value(ltp - h, T)) / (2 * h)
    theta = value(ltp, T - 1 / 365) - value(ltp, T)
    vega = value(ltp, T, iv + 0.01) - value(ltp, T)
    be = kb + dr * debit
    s_t = iv * sqrt(max(T, 1e-6))
    N = lambda x: 0.5 * (1 + erf(x / sqrt(2)))
    prob_beyond = lambda L: N(dr * (log(ltp / L) + (r - iv ** 2 / 2) * T) / s_t) if L > 0 else 0.0
    width = abs(ks - kb) if not single else None
    lot = P.get("lot") or (chain or {}).get("lot") or spec["lot"] or 1
    budget = P["capital"] * P["risk_pct"] / 100
    auto_lots = int(budget // max(debit * lot, 1e-9))
    lots = lots or max(auto_lots, 0)
    Te = max(T - P["hold"] / 24 / 365, 1e-6)
    zs = tg["up"] if dr > 0 else tg["down"]
    tgt = [dict(level=z["level"], value=value(z["level"], Te), prob=z["prob"]) for z in zs]
    return dict(kind=kind, single=single, legs=info, kb=kb, ks=None if single else ks, debit=debit, be=be,
                max_loss=debit, max_profit=None if single else width - debit, width=width, delta=delta, theta=theta,
                vega=vega, pop=prob_beyond(be), p_max=None if single else prob_beyond(ks), lot=lot, lots=lots,
                auto_lots=auto_lots, dte=dte, T=T, Te=Te, value=value, payoff=payoff, targets=tgt, iv=iv)


def spread_chart(sa, a, qty):
    import plotly.graph_objects as go
    ltp, s_t = a["ltp"], sa["iv"] * sqrt(max(sa["T"], 1e-6))
    xs = np.linspace(ltp * exp(-3 * s_t), ltp * exp(3 * s_t), 160)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs, y=[(sa["payoff"](x) - sa["debit"]) * qty for x in xs], name="At expiry",
                             line=dict(color="#2c3e50", width=2)))
    fig.add_trace(go.Scatter(x=xs, y=[(sa["value"](x, sa["Te"]) - sa["debit"]) * qty for x in xs],
                             name="After expected hold", line=dict(color="#2980b9", dash="dash")))
    fig.add_hline(y=0, line_color="#95a5a6")
    for x, lbl, col in [(ltp, "LTP", "#8e44ad"), (sa["be"], "Breakeven", "#d35400")] + \
            [(t["level"], f"T{i + 1}", "#27ae60") for i, t in enumerate(sa["targets"])]:
        fig.add_vline(x=x, line_dash="dot", line_color=col, annotation_text=lbl, annotation_position="top")
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10), xaxis_title="Underlying at that time",
                      yaxis_title="P&L (₹)", legend=dict(orientation="h", y=1.1))
    return fig


# ---------------- Recommendation credibility ----------------
def wilson_lo(k, n, z=1.645):
    """One-sided 95% lower bound of a hit rate (Wilson score): what the record supports, not the lucky point estimate."""
    if n <= 0:
        return 0.0
    p = k / n
    return (p + z * z / (2 * n) - z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def _live_stats(sym, kind):
    try:
        import paper  # lazy: paper imports engine
        return paper.live_stats(sym, kind)
    except Exception:
        return None


def proof(a, r):
    """TRADE only when the call has a record behind it: its symbol's past signals made money on REAL option prices
    (12+ trades) and the app's own recent live calls on that symbol/side are not losing. Otherwise PAPER.
    -> dict(trade, why)."""
    ls = _live_stats(r.get("sym") or a["spec"]["name"], r.get("kind"))
    if ls and ls["last_n"] >= 5 and (ls["last_avg_r"] or 0) < 0:
        return dict(trade=False, why=f"demoted: last {ls['last_n']} live calls averaged {ls['last_avg_r']:+.2f}R")
    rb = a.get("rbt")
    if not rb or rb["trades"] < 12:
        n = rb["trades"] if rb else 0
        return dict(trade=False, why=f"unproven: {n} past trades on real option prices (needs 12+)")
    if rb["avg"] <= 0:
        return dict(trade=False, why=f"past signals lost on real option prices ({rb['avg'] * 100:+.1f}% per trade, "
                                     f"{rb['trades']} trades)")
    live = (f" · live {ls['hits']}/{ls['n']} hit target" if ls and ls["n"] else "")
    return dict(trade=True, why=f"real option prices: {rb['avg'] * 100:+.1f}% per trade over {rb['trades']} trades" + live)


def _live_record(sym, kind):
    try:
        import paper  # lazy: paper imports engine
        return paper.live_record(sym, kind)
    except Exception:
        return None


CRED_W = {"pass": 1.0, "warn": 0.5, "fail": 0.0}


def credibility(a, r, P):
    """Evidence behind one BUY CALL / PUT plan -> grade A-D, score 0-100, evidence-based T1 hit estimate and the checks.
    Each check: state 'pass' | 'warn' | 'fail' | 'n/a', weight, name, detail. Unknowns are left out of the score."""
    spec, row, chain = a["spec"], a["row"], a["chain"]
    now = pd.Timestamp.now(tz=IST)
    kind, dr = r["kind"], 1 if r["kind"] == "CE" else -1
    side = "CALL" if dr > 0 else "PUT"
    C = []
    add = lambda state, w, name, detail: C.append(dict(state=state, w=w, name=name, detail=detail))
    # Model chance of reaching T1 before the close (x chance the trigger trades first, for conditional plans)
    t, _ = session_left_years(spec, now)
    p_reach = touch_prob(r["u_entry"], r["u_t1"], a["iv"], t) * ((r.get("prob") or 1.0) if r["mode"] == "trigger" else 1.0)
    # 1. Back-test edge on this symbol: average R and its 95% floor
    tr = a.get("bt_trades")
    if tr is None or tr.empty:
        add("fail", 3, "Back-test edge", "no past signals on the loaded history")
    else:
        n, R = len(tr), tr.R
        lo = R.mean() - 1.645 * R.std(ddof=1) / sqrt(n) if n > 1 else -1.0
        add("pass" if n >= 30 and lo > 0 else "warn" if R.mean() > 0 and n >= 15 else "fail", 3, "Back-test edge",
            f"{n} trades, {R.mean():+.2f}R avg, 95% floor {lo:+.2f}R" + ("" if n >= 30 else " (small sample)"))
    # 1b. The same signals scored on the option premium (Black-Scholes model, theta and costs included)
    ob, real = (a.get("rbt"), True) if a.get("rbt") else (a.get("obt"), False)
    if not ob:
        add("n/a", 2, "Option back-test", "no past signals to price")
    else:
        n = ob["trades"]
        how = "on REAL Upstox option prices" if real else "with a pricing model (no real option history)"
        add("pass" if n >= 20 and ob["floor"] > 0 else "warn" if ob["avg"] > 0 and n >= 10 else "fail", 2,
            "Option back-test", f"{n} past trades {how}: {ob['avg'] * 100:+.1f}% of premium avg, "
            f"{ob['win'] * 100:.0f}% winners, PF {ob['pf']:.2f} (1.5% costs)")
    # 2. T1 hit rate for this side (and this setup when it has enough history)
    hits = nt = 0
    if tr is not None and not tr.empty:
        m, scope = tr[tr.side == side], f"{side} signals"
        labels = {s.strip() for s in (r.get("setup") or "").split(",") if s.strip()}
        ms = m[m.setup.map(lambda s: bool(labels & {x.strip() for x in str(s).split(",")}))] if labels else m.iloc[:0]
        if len(ms) >= 8:
            m, scope = ms, "this setup"
        hits, nt = int(m.t1_hit.sum()), len(m)
    if nt < 5:
        add("n/a", 2, "T1 hit record", f"only {nt} past {side} signals")
    else:
        lo = wilson_lo(hits, nt)
        add("pass" if nt >= 10 and lo >= 0.45 else "warn" if hits / nt >= 0.5 else "fail", 2, "T1 hit record",
            f"{hits}/{nt} {scope} reached T1 ({hits / nt * 100:.0f}%, at least {lo * 100:.0f}% with 95% confidence)")
    # 3. Forward (live) track record: recommendations this app logged and later judged
    lr = _live_record(r["sym"], kind)
    if not lr or lr[1] < 5:
        add("n/a", 2, "Live track record", f"{lr[1] if lr else 0} resolved {side} calls on {r['sym']} so far")
    else:
        k, n = lr
        lo = wilson_lo(k, n)
        add("pass" if lo >= 0.4 else "warn" if k / n >= 0.45 else "fail", 2, "Live track record",
            f"{k}/{n} past {side} calls on {r['sym']} hit a target ({k / n * 100:.0f}%, floor {lo * 100:.0f}%)")
    # 4. Liquidity of the recommended contract
    q = (chain or {}).get("quotes", {}).get(r["strike"], {}).get(kind) if chain else None
    if not q:
        add("fail", 2, "Contract liquidity", "no live quote: premium is a model estimate, verify on your broker")
    elif not (q["bid"] > 0 and q["ask"] >= q["bid"]):
        add("warn", 2, "Contract liquidity", f"no two-sided book (LTP {q['ltp']:.2f}), fills may slip")
    else:
        sp = (q["ask"] - q["bid"]) / ((q["ask"] + q["bid"]) / 2) * 100
        oi = q.get("oi") or 0
        add("pass" if sp <= 3 and oi > 0 else "warn" if sp <= 8 else "fail", 2, "Contract liquidity",
            f"bid-ask spread {sp:.1f}% of premium, OI {oi:,.0f}")
    # 5. Can the underlying realistically reach T1 today?
    add("pass" if p_reach >= 0.45 else "warn" if p_reach >= 0.25 else "fail", 2, "T1 reachable today",
        f"{p_reach * 100:.0f}% model chance (IV {a['iv'] * 100:.0f}%) of touching {r['u_t1']:,.2f} before the close")
    # 6. Premium reward : risk
    risk = max(r["prem"] - r["sl"], 1e-9)
    rr1, rr2 = (r["t1"] - r["prem"]) / risk, (r["t2"] - r["prem"]) / risk
    add("pass" if rr2 >= 1.5 else "warn" if rr2 >= 1 else "fail", 1.5, "Premium reward:risk",
        f"T1 {rr1:.1f}:1, T2 {rr2:.1f}:1 on the option itself")
    # 7. Time decay versus the expected gain over the holding period
    th = a["sa"]["sides"][kind]["theta"]
    op, cl, _ = SESSIONS[spec["sess"]]
    drag = max(th, 0) * min(P["hold"] * 60 / (tmin(cl) - tmin(op)), 1)
    gain = max(r["t1"] - r["prem"], 1e-9)
    add("pass" if drag <= 0.15 * gain else "warn" if drag <= 0.35 * gain else "fail", 1, "Theta drag",
        f"≈₹{drag:.1f} decay over a {P['hold']:g}h hold vs ₹{gain:.1f} gain to T1 (θ {th:.1f}/day)")
    # 8. Implied vs realized volatility: is the buyer overpaying for movement?
    rv = realized_vol(a["d"], spec, P["interval"])
    if a["iv_src"] in ("manual", "realized vol x1.2") or rv <= 0:
        add("n/a", 1, "IV vs realized", "no market implied volatility to compare")
    else:
        x = a["iv"] / rv
        add("pass" if x <= 1.25 else "warn" if x <= 1.6 else "fail", 1, "IV vs realized",
            f"IV {a['iv'] * 100:.0f}% is {x:.2f}× realized {rv * 100:.0f}%" + (": premium is rich" if x > 1.25 else ""))
    # 9a. Fresh corporate filing (NSE announcements): event-driven moves make technical levels less reliable
    try:
        import news  # lazy: news imports engine
        cat = [x for x in news.catalysts(spec.get("nse") or r["sym"], now, 24) if x["alert"]]
    except Exception:
        cat = None
    if cat is None:
        add("n/a", 1, "News catalyst", "NSE announcements feed unavailable")
    elif cat:
        add("warn", 1, "News catalyst", f"{cat[0]['category']} filed {cat[0]['ts']:%d %b %H:%M}: "
            f"{cat[0]['subject'][:70]} (event-driven moves can run through levels)")
    else:
        add("pass", 1, "News catalyst", "no market-moving NSE filing in the last 24 h")
    # 9b. Market confirmation: NIFTY trend, relative strength and daily trend on the trade's side
    agree, against = [], []
    if pd.notna(row.get("MKT")) and row.MKT != 0:
        (agree if row.MKT * dr > 0 else against).append("NIFTY trend")
    if pd.notna(row.get("RS")):
        (agree if row.RS * dr > 0 else against).append(f"RS vs NIFTY {row.RS:+.2f}%")
    if pd.notna(row.get("D1")) and row.D1 != 0:
        (agree if row.D1 * dr > 0 else against).append("daily trend")
    if agree or against:
        add("pass" if not against else "warn" if len(against) == 1 and agree else "fail", 1.5, "Market confirmation",
            (("with: " + ", ".join(agree)) if agree else "") + ("; " if agree and against else "")
            + (("against: " + ", ".join(against)) if against else ""))
    else:
        add("n/a", 1.5, "Market confirmation", "no NIFTY benchmark for this instrument")
    # 9. Data freshness
    age = (now - row.name).total_seconds() / 60 - int(P["interval"][:-1])
    add("pass" if age <= 20 else "warn" if age <= 45 else "fail", 1.5, "Fresh data",
        f"last candle {max(age, 0):.0f} min old · {a['src']}" + (f" + {chain['source']} chain" if chain else ", no chain"))
    # 10. Scheduled events
    ev = a.get("events", [])
    if events.results_soon(ev):
        add("fail", 2, "Event risk", "results due: IV crush and gaps make the plan unreliable")
    else:
        hi = [txt for sev, txt in ev if sev == "high"]
        add("warn" if hi else "pass", 1, "Event risk", hi[0] if hi else "no high-impact event flagged")
    known = [c for c in C if c["state"] != "n/a"]
    score = 100 * sum(CRED_W[c["state"]] * c["w"] for c in known) / max(sum(c["w"] for c in known), 1e-9)
    hard = {c["name"] for c in C if c["state"] == "fail"} & {"Back-test edge", "Contract liquidity", "Fresh data"}
    if hard:
        score = min(score, 64)  # unproven rules, an untradable contract or stale data cap the grade at C
    if events.results_soon(ev):
        score = min(score, 40)
    score = int(round(score))
    grade = "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D"
    # Evidence-based T1 odds: back-test record shrunk toward the model probability (10 pseudo-trades of prior)
    p_t1 = (hits + 10 * p_reach) / (nt + 10)
    label = {"A": "Strong evidence", "B": "Fair evidence", "C": "Weak evidence", "D": "Poor evidence"}[grade]
    return dict(grade=grade, score=score, label=label, p_t1=p_t1, checks=C,
                fails=[c["name"] for c in C if c["state"] == "fail"])


# ---------------- One-stock CALL / PUT recommendation ----------------
def recommend(a, P):
    """recommend_plan() plus a credibility grade; confidence is capped by the credibility score."""
    r = recommend_plan(a, P)
    if r["mode"] in ("now", "trigger"):
        ce = r["kind"] == "CE"
        risk_u = abs(r["u_entry"] - r["u_sl"])
        r["rr"] = (abs(r["u_t1"] - r["u_entry"]) / risk_u, abs(r["u_t2"] - r["u_entry"]) / risk_u) if risk_u else None
        r["invalid"] = (f"Invalid if a 15-min candle closes {'below' if ce else 'above'} {r['u_sl']:,.2f} "
                        f"({'support' if ce else 'resistance'} lost)")
        r["cred"] = credibility(a, r, P)
        r["conf"] = min(r["conf"], r["cred"]["score"])
        r.update(execution(a, r, P))
    return r


SIZE_BY_GRADE = {"A": 1.0, "B": 0.5, "C": 0.25, "D": 0.0}


def execution(a, r, P):
    """How to act on a call: a limit price from the live bid/ask (never a market order), a skip warning when the spread
    is wide, and lots scaled by the credibility grade (A full risk budget, B half, C quarter, D paper only).
    Real option back-tests showed spreads and weak setups are where the losses come from."""
    out = {}
    q = ((a.get("chain") or {}).get("quotes") or {}).get(r.get("strike"), {}).get(r.get("kind")) or {}
    if q.get("instrument_key"):
        out["ikey"] = q["instrument_key"]  # the exact contract: lets the track record score the call on its real price
    bid, ask = q.get("bid") or 0, q.get("ask") or 0
    if bid > 0 and ask >= bid:
        mid = (bid + ask) / 2
        if r.get("mode") == "now":  # a trigger plan's entry premium is only known when the trigger trades
            out["limit"] = round(round(min(ask, mid + 0.25 * (ask - bid)) / 0.05) * 0.05, 2)  # a little above mid
        out["spread_pct"] = (ask - bid) / mid * 100
        out["spread_ok"] = out["spread_pct"] <= 3
    lot = P.get("lot") or (a.get("chain") or {}).get("lot") or a["spec"].get("lot") or 1
    risk_lot = max((r.get("prem") or 0) - (r.get("sl") or 0), 0) * lot
    budget = P.get("capital", 0) * P.get("risk_pct", 1) / 100
    g = (r.get("cred") or {}).get("grade", "D")
    lots = int(budget * SIZE_BY_GRADE.get(g, 0) // risk_lot) if risk_lot > 0 else 0
    pf = proof(a, r)
    out["proof"] = pf
    if P.get("proof_gate", True) and not pf["trade"]:  # no record behind it: track it, don't put money on it
        out.update(lot_size=lot, lots=0, risk_lot=risk_lot, cost_lot=(r.get("prem") or 0) * lot,
                   allow_block=bool(P.get("allowlist")), size_note=f"paper only: {pf['why']}")
        return out
    if P.get("halt"):  # portfolio risk guard: daily loss, trades per day, losing streak, open positions
        out.update(lot_size=lot, lots=0, risk_lot=risk_lot, cost_lot=(r.get("prem") or 0) * lot,
                   size_note=f"no new trade: {P['halt']}")
        return out
    sf = P.get("size_factor", 1.0)
    lots = int(budget * sf * SIZE_BY_GRADE.get(g, 0) // risk_lot) if risk_lot > 0 else 0
    out.update(lot_size=lot, lots=lots, risk_lot=risk_lot, cost_lot=(r.get("prem") or 0) * lot,
               size_note=(f"{lots} lot{'s' if lots != 1 else ''} × {lot} (grade {g}: "
                          f"{int(SIZE_BY_GRADE.get(g, 0) * 100)}% of the ₹{budget:,.0f} risk budget"
                          + (", halved after a losing week)" if sf < 1 else ")") if lots else
                          f"paper only (grade {g})" if SIZE_BY_GRADE.get(g, 0) == 0 else
                          f"1 lot risks ₹{risk_lot:,.0f}, above the ₹{budget * SIZE_BY_GRADE.get(g, 0):,.0f} budget for grade {g}"))
    return out


CRED_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}


def blocked_rec(a, P):
    """The trade a setup would have produced when a confirmation filter blocked it (mode 'blocked'), so the live track
    record can show whether the filters help. None when nothing was blocked on the last bar."""
    row = a["row"]
    if row.Signal != 0 or not row.get("CtxBlock") or not row.TDir:
        return None
    r2 = row.copy()
    r2["Signal"] = int(np.sign(row.TDir))
    p = option_plan(a["spec"], r2, a["ltp"], P, a["chain"], a["iv"], pd.Timestamp.now(tz=IST))
    return dict(mode="blocked", kind=p["kind"], action=f"BLOCKED BUY {'CALL' if p['kind'] == 'CE' else 'PUT'}",
                strike=p["strike"], prem=p["prem"], sl=p["p_sl"], t1=p["p_t1"], t2=p["p_t2"], u_entry=p["entry"],
                u_sl=p["stop"], u_t1=p["t1"], u_t2=p["t2"], conf=0, score=row.Score, block=row.CtxBlock)


def credible_ideas(results, P, min_grade="B", recs=None, near_n=5):
    """BUY CALL / PUT plans from scan results that clear the credibility bar -> (passing, near misses), best first:
    grade, then enter-now before trigger plans, then credibility score and evidence-based T1 odds.
    recs: optional {sym: recommend()} already computed this run, to avoid recomputing."""
    rows = []
    for sym, a in (results or {}).items():
        if not a or "error" in a:
            continue
        r = (recs or {}).get(sym) or recommend(a, P)
        if r.get("mode") in ("now", "trigger") and r.get("cred"):
            rows.append(dict(r, symbol=sym))
    rows.sort(key=lambda r: (CRED_RANK[r["cred"]["grade"]], r["mode"] != "now", -r["cred"]["score"], -r["cred"]["p_t1"]))
    bar = CRED_RANK.get(min_grade, 1)
    ok = [r for r in rows if CRED_RANK[r["cred"]["grade"]] <= bar]
    return ok, [r for r in rows if CRED_RANK[r["cred"]["grade"]] > bar][:near_n]


def recommend_plan(a, P):
    """-> dict with action, mode ('now' | 'trigger' | 'none'), contract, premium & underlying entry/SL/T1/T2, trigger,
    confidence and reasons. Always gives a concrete plan, conditional when no setup has fired."""
    row, sa, tg, p, spec = a["row"], a["sa"], a["tg"], a["plan"], a["spec"]
    why = []
    for pl in PILLARS:
        sc = row["P_" + pl]
        if abs(sc) >= 1:
            why.append(f"{pl} {sc:+.1f}")
    evn = ["📅 " + t for sev, t in a.get("events", []) if sev == "high"]
    base = dict(sym=spec["name"], ltp=a["ltp"], score=row.Score, regime=row.Regime, bt=a.get("bt"), notes=[], why=why)
    if p:
        dr = 1 if p["kind"] == "CE" else -1
        return base | dict(action=f"BUY {'CALL' if dr > 0 else 'PUT'}", mode="now", kind=p["kind"], strike=p["strike"],
                           expiry=p["expiry"], live=p["live"], prem=p["prem"], sl=p["p_sl"], t1=p["p_t1"], t2=p["p_t2"],
                           u_entry=p["entry"], u_sl=p["stop"], u_t1=p["t1"], u_t2=p["t2"], trigger=None,
                           conf=a["conf"], setup=row.Setups, delta=p["delta"], pop=p.get("pop"),
                           notes=[n for n in p["notes"] if "budget" not in n])

    def conditional(kind):
        dr = 1 if kind == "CE" else -1
        s = sa["sides"][kind]
        zs = tg["up"] if dr > 0 else tg["down"]
        if not zs:
            return None
        trig = zs[0]["level"]
        risk_u = max(row.ATR, abs(trig - a["ltp"]) * 0.6)
        u_sl = trig - dr * risk_u
        u_t1 = zs[1]["level"] if len(zs) > 1 else trig + dr * risk_u
        u_t2 = zs[2]["level"] if len(zs) > 2 else trig + dr * 2 * risk_u
        if (u_t1 - trig) * dr < risk_u * 0.8:  # keep at least ~1R to T1 and ~2R to T2
            u_t1 = trig + dr * risk_u
        if (u_t2 - trig) * dr < 2 * risk_u:
            u_t2 = trig + dr * 2 * risk_u
        prem_at = lambda u: max(s["prem"] + s["delta"] * (u - a["ltp"]), 0.05)  # delta-projected premium
        entry = prem_at(trig)
        return dict(kind=kind, strike=s["strike"], live=s["live"], prem=entry, sl=max(prem_at(u_sl), entry * (1 - P["max_loss"])),
                    t1=prem_at(u_t1), t2=prem_at(u_t2), u_entry=trig, u_sl=u_sl, u_t1=u_t1, u_t2=u_t2, trigger=trig,
                    delta=s["delta"], now_prem=s["prem"], prob=zs[0]["prob"], pop=s.get("pop"))

    need = row.Need
    lean = "CE" if row.Score >= need else "PE" if row.Score <= -need else None
    conf = int(min(100, 25 + 35 * min(abs(row.Score) / (need * 2), 1)))
    exp = (a["chain"] or {}).get("expiry")
    if lean:
        c = conditional(lean)
        if c:
            dr = 1 if lean == "CE" else -1
            notes = evn + [n for n in a["notes"] if "chase" in n] + [
                f"Momentum favours {'CALL' if dr > 0 else 'PUT'} but no setup has fired: wait for a 15-min close "
                f"{'above' if dr > 0 else 'below'} {c['trigger']:,.2f} ({c['prob'] * 100:.0f}% chance it trades there today)"]
            return base | c | dict(action=f"BUY {'CALL' if dr > 0 else 'PUT'} {'above' if dr > 0 else 'below'} "
                                          f"{c['trigger']:,.0f}", mode="trigger", expiry=exp, conf=conf, setup="",
                                   notes=notes)
    up, dn = conditional("CE"), conditional("PE")
    return base | dict(action="NO TRADE", mode="none", conf=0, expiry=exp, setup="", kind=None, alt=(up, dn),
                       notes=evn + [f"Score {row.Score:+.1f} is inside ±{need:.1f}: no edge either way (theta decays both sides)"])
