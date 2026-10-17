"""
Pro analytics: options positioning (gamma exposure, straddle-implied move, IV smile/skew, OI structure, VIX rank)
and market intelligence (NIFTY 50 breadth heat map, sector strength, global cues, regime read).

GEX convention (SqueezeMetrics style): dealers are assumed long the calls (sold to them by overwriters) and short the
puts (bought for protection), so call gamma counts +, put gamma counts -. Positive net GEX -> dealer hedging dampens
moves (pinning, mean reversion); negative -> hedging amplifies moves (trend days, volatility expansion).
Shown in relative units because OI conventions (contracts vs quantity) differ between data sources.
"""
import io
from datetime import datetime
from math import erf, exp, log, pi, sqrt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

import engine as E
import net

UP, DN, MUT, ACC = "#22c55e", "#ef4444", "#64748b", "#38bdf8"
GLOBAL = [("S&P 500 fut", "ES=F"), ("Nasdaq fut", "NQ=F"), ("Dow fut", "YM=F"), ("Nikkei 225", "^N225"),
          ("Hang Seng", "^HSI"), ("WTI crude", "CL=F"), ("Gold", "GC=F"), ("US 10Y yield", "^TNX"),
          ("Dollar index", "DX-Y.NYB"), ("USD/INR", "USDINR=X")]
_H = {"User-Agent": E.NSE_H["User-Agent"], "Accept": "application/json, text/plain, */*", "Referer": "https://www.nseindia.com/"}


def _dark(fig, h=360):
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", height=h,
                      margin=dict(l=10, r=10, t=36, b=10), legend=dict(orientation="h", y=1.12), font=dict(size=12))
    fig.update_xaxes(gridcolor="#1e2d4a")
    fig.update_yaxes(gridcolor="#1e2d4a")
    return fig


# ---------------- Options positioning ----------------
def bs_gamma(S, K, T, sig, r=0.065):
    if T <= 0 or sig <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (log(S / K) + (r + sig ** 2 / 2) * T) / (sig * sqrt(T))
    return exp(-d1 * d1 / 2) / sqrt(2 * pi) / (S * sig * sqrt(T))


def options_analytics(a, P, now):
    chain = a.get("chain")
    if not chain or not chain.get("quotes"):
        return None
    S, spec = a["ltp"], a["spec"]
    dte = E.option_dte(chain, P, now)
    T, r = max(dte, 0.05) / 365, (0.0 if spec["kind"] == "commodity" else 0.065)
    base_iv = chain.get("atm_iv") or a["iv"]
    rows = []
    for K in sorted(chain["quotes"]):
        ce, pe = chain["quotes"][K].get("CE", {}), chain["quotes"][K].get("PE", {})
        ivc = (ce.get("iv") or 0) / 100 or base_iv
        ivp = (pe.get("iv") or 0) / 100 or base_iv
        rows.append(dict(K=K, ce_oi=ce.get("oi") or 0, pe_oi=pe.get("oi") or 0, ce_doi=ce.get("doi") or 0,
                         pe_doi=pe.get("doi") or 0, ce_iv=(ce.get("iv") or np.nan), pe_iv=(pe.get("iv") or np.nan),
                         ce_ltp=ce.get("ltp") or np.nan, pe_ltp=pe.get("ltp") or np.nan, ivc=ivc, ivp=ivp))
    df = pd.DataFrame(rows)
    df = df[(df.K > S * 0.85) & (df.K < S * 1.15)].reset_index(drop=True)
    if df.empty:
        return None

    def gex_at(x):
        g = sum(bs_gamma(x, k, T, c, r) * co - bs_gamma(x, k, T, p, r) * po
                for k, c, p, co, po in zip(df.K, df.ivc, df.ivp, df.ce_oi, df.pe_oi))
        return g * x * x * 0.01

    df["gex"] = [(bs_gamma(S, k, T, c, r) * co - bs_gamma(S, k, T, p, r) * po) * S * S * 0.01
                 for k, c, p, co, po in zip(df.K, df.ivc, df.ivp, df.ce_oi, df.pe_oi)]
    grid = np.linspace(S * 0.97, S * 1.03, 61)
    prof = np.array([gex_at(x) for x in grid])
    scale = max(np.abs(prof).max(), np.abs(df.gex).max(), 1e-9)
    net = gex_at(S)
    flips = [grid[i] - prof[i] * (grid[i + 1] - grid[i]) / (prof[i + 1] - prof[i])
             for i in range(len(grid) - 1) if np.sign(prof[i]) != np.sign(prof[i + 1])]
    flip = min(flips, key=lambda x: abs(x - S)) if flips else None
    atm = df.K.iloc[(df.K - S).abs().argmin()]
    straddle = None
    if chain["quotes"].get(atm):
        c = E.live_premium(chain, atm, "CE")
        p = E.live_premium(chain, atm, "PE")
        straddle = (c or 0) + (p or 0) if c and p else None
    near = lambda tgt, col: df.loc[(df.K - tgt).abs().argmin(), col]
    put_iv, call_iv = near(S * 0.98, "pe_iv"), near(S * 1.02, "ce_iv")
    skew = put_iv - call_iv if np.isfinite(put_iv) and np.isfinite(call_iv) else None
    ce_tot, pe_tot = df.ce_oi.sum(), df.pe_oi.sum()
    return dict(df=df, grid=grid, prof=prof / scale * 100, gex_rel=df.gex / scale * 100, net=net / scale * 100,
                flip=flip, regime="Positive" if net > 0 else "Negative", atm=atm, straddle=straddle, dte=dte,
                em_lo=S - straddle if straddle else None, em_hi=S + straddle if straddle else None,
                day_move=straddle / sqrt(max(dte, 1)) if straddle else None, skew=skew,
                pcr=pe_tot / ce_tot if ce_tot else None, chain=chain, S=S,
                top_ce=df.nlargest(3, "ce_oi").K.tolist(), top_pe=df.nlargest(3, "pe_oi").K.tolist(),
                build_ce=df.nlargest(2, "ce_doi").K.tolist(), build_pe=df.nlargest(2, "pe_doi").K.tolist())


def options_read(o, a):
    """Plain-English interpretation."""
    S = o["S"]
    out = []
    if o["regime"] == "Positive":
        out.append(("🧲", f"Positive gamma: option writers' hedging tends to **dampen moves** around {S:,.0f}. "
                          "Expect range trading / pinning: favour quick targets (T1), spreads over naked buys."))
    else:
        out.append(("⚡", "Negative gamma: hedging **amplifies moves**. Breakouts tend to extend and intraday ranges widen: "
                          "trend setups and holding to T2 work better."))
    if o["flip"]:
        side = "above" if S > o["flip"] else "below"
        out.append(("🎚", f"Gamma flip at **{o['flip']:,.0f}** (spot is {side}). Crossing it would switch the regime."))
    if o["straddle"]:
        out.append(("📏", f"ATM straddle {o['straddle']:,.1f} → market prices a **±{o['straddle']:,.0f}** move by expiry "
                          f"({o['em_lo']:,.0f} – {o['em_hi']:,.0f}), roughly ±{o['day_move']:,.0f} per day."))
    out.append(("🧱", f"Biggest call OI (resistance): {', '.join(f'{k:g}' for k in o['top_ce'])} · biggest put OI (support): "
                      f"{', '.join(f'{k:g}' for k in o['top_pe'])}"))
    out.append(("🆕", f"Fresh writing today: calls at {', '.join(f'{k:g}' for k in o['build_ce'])}, puts at "
                      f"{', '.join(f'{k:g}' for k in o['build_pe'])}"))
    if o["skew"] is not None:
        out.append(("↘" if o["skew"] > 0 else "↗", f"Skew {o['skew']:+.1f} vol pts (2% OTM put IV minus call IV): "
                    + ("downside protection is in demand." if o["skew"] > 1 else
                       "calls priced richer than puts: upside chase." if o["skew"] < -1 else "balanced.")))
    return out


def fig_oi(o):
    df, S = o["df"], o["S"]
    fig = go.Figure()
    fig.add_bar(x=df.K, y=df.ce_oi, name="Call OI", marker_color="rgba(239,68,68,.75)")
    fig.add_bar(x=df.K, y=df.pe_oi, name="Put OI", marker_color="rgba(34,197,94,.75)")
    fig.add_scatter(x=df.K, y=df.ce_doi, name="Call ΔOI", mode="lines+markers", line=dict(color="#fca5a5", width=1.5))
    fig.add_scatter(x=df.K, y=df.pe_doi, name="Put ΔOI", mode="lines+markers", line=dict(color="#86efac", width=1.5))
    fig.add_vline(x=S, line_color=ACC, annotation_text=f"Spot {S:,.0f}", annotation_position="top")
    fig.add_vline(x=o["chain"]["max_pain"], line_dash="dot", line_color="#eab308", annotation_text="Max pain",
                  annotation_position="bottom")
    fig.update_layout(barmode="group", title="Open interest by strike (red = call writers' resistance, green = put support)")
    return _dark(fig, 380)


def fig_gex(o):
    df, S = o["df"], o["S"]
    fig = go.Figure()
    fig.add_bar(x=df.K, y=o["gex_rel"], name="GEX by strike", marker_color=np.where(o["gex_rel"] >= 0, UP, DN), opacity=.7)
    fig.add_scatter(x=o["grid"], y=o["prof"], name="Net GEX if spot moves here", line=dict(color=ACC, width=2.5))
    fig.add_hline(y=0, line_color=MUT)
    fig.add_vline(x=S, line_color="#e2e8f0", annotation_text="Spot", annotation_position="top")
    if o["flip"]:
        fig.add_vline(x=o["flip"], line_dash="dash", line_color="#eab308", annotation_text="Gamma flip",
                      annotation_position="bottom")
    fig.update_layout(title="Gamma exposure (relative, OI-weighted)")
    return _dark(fig, 360)


def fig_smile(o):
    df, S = o["df"], o["S"]
    fig = go.Figure()
    fig.add_scatter(x=df.K, y=df.ce_iv, name="Call IV", mode="lines+markers", line=dict(color="#f87171"))
    fig.add_scatter(x=df.K, y=df.pe_iv, name="Put IV", mode="lines+markers", line=dict(color="#4ade80"))
    fig.add_vline(x=S, line_color=ACC, annotation_text="Spot", annotation_position="top")
    fig.update_layout(title="IV smile (%)")
    return _dark(fig, 320)


@st.cache_data(ttl=3600, show_spinner=False)
def vix_rank():
    try:
        v = E._flat(yf.download("^INDIAVIX", period="1y", interval="1d", progress=False))["Close"].dropna()
        cur = float(v.iloc[-1])
        return dict(vix=cur, rank=(cur - v.min()) / (v.max() - v.min()) * 100, pct=(v < cur).mean() * 100,
                    lo=float(v.min()), hi=float(v.max()))
    except Exception:
        return None


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _year_range(ticker):
    v = E._flat(yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=False))
    lo, hi = float(v["Low"].dropna().min()), float(v["High"].dropna().max())
    if not (np.isfinite(lo) and np.isfinite(hi) and 0 < lo <= hi):
        raise ValueError("no 1y range")
    return lo, hi


_YR_FAIL = {}  # ticker -> time of the last failed fetch (retried after 10 min, not every fragment run)


def year_range(ticker):
    """-> (52-week low, high) from Yahoo daily bars (Ticker.history: thread-safe), cached 6h; None on failure.
    Failures are cached for 10 minutes only, so a blocked call doesn't hide the row for 6h."""
    import time
    if not ticker or time.time() - _YR_FAIL.get(ticker, 0) < 600:
        return None
    try:
        return _year_range(ticker)
    except Exception:
        _YR_FAIL[ticker] = time.time()
        return None


# ---------------- Market intelligence ----------------
@st.cache_data(ttl=120, show_spinner=False)
def nse_indices():
    try:
        j = net.nse_get("https://www.nseindia.com/api/allIndices", _H)
        df = pd.DataFrame(j["data"])
        for c in ("last", "percentChange", "perChange30d", "perChange365d", "pe", "advances", "declines"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df, dict(adv=int(j.get("advances") or 0), dec=int(j.get("declines") or 0), unch=int(j.get("unchanged") or 0))
    except Exception:
        return None, None


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def nifty50_list():
    for u in ("https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
              "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"):
        try:
            r = net.session().get(u, headers=_H, timeout=10)
            if r.ok:
                return pd.read_csv(io.StringIO(r.text))
        except Exception:
            continue
    return None


# Fallback when the NSE / niftyindices CSVs are blocked (cloud IPs). Index reviews: Mar & Sep; update twice a year.
NIFTY50 = (("ADANIENT", "Metals & Mining"), ("ADANIPORTS", "Services"), ("APOLLOHOSP", "Healthcare"),
           ("ASIANPAINT", "Consumer Durables"), ("AXISBANK", "Financial Services"), ("BSE", "Financial Services"),
           ("BAJAJ-AUTO", "Automobile and Auto Components"), ("BAJFINANCE", "Financial Services"),
           ("BAJAJFINSV", "Financial Services"), ("BEL", "Capital Goods"), ("BHARTIARTL", "Telecommunication"),
           ("CIPLA", "Healthcare"), ("COALINDIA", "Oil Gas & Consumable Fuels"), ("DRREDDY", "Healthcare"),
           ("EICHERMOT", "Automobile and Auto Components"), ("ETERNAL", "Consumer Services"),
           ("GRASIM", "Construction Materials"), ("HCLTECH", "Information Technology"), ("HDFCBANK", "Financial Services"),
           ("HDFCLIFE", "Financial Services"), ("HINDALCO", "Metals & Mining"), ("HINDUNILVR", "Fast Moving Consumer Goods"),
           ("ICICIBANK", "Financial Services"), ("ITC", "Fast Moving Consumer Goods"), ("INFY", "Information Technology"),
           ("INDIGO", "Services"), ("JSWSTEEL", "Metals & Mining"), ("JIOFIN", "Financial Services"),
           ("KOTAKBANK", "Financial Services"), ("LT", "Construction"), ("M&M", "Automobile and Auto Components"),
           ("MARUTI", "Automobile and Auto Components"), ("MAXHEALTH", "Healthcare"), ("NTPC", "Power"),
           ("NESTLEIND", "Fast Moving Consumer Goods"), ("ONGC", "Oil Gas & Consumable Fuels"), ("POWERGRID", "Power"),
           ("RELIANCE", "Oil Gas & Consumable Fuels"), ("SBILIFE", "Financial Services"), ("SHRIRAMFIN", "Financial Services"),
           ("SBIN", "Financial Services"), ("SUNPHARMA", "Healthcare"), ("TCS", "Information Technology"),
           ("TATACONSUM", "Fast Moving Consumer Goods"), ("TMPV", "Automobile and Auto Components"),
           ("TATASTEEL", "Metals & Mining"), ("TECHM", "Information Technology"), ("TITAN", "Consumer Durables"),
           ("TRENT", "Consumer Services"), ("ULTRACEMCO", "Construction Materials"))


def nifty50_constituents():
    """NIFTY 50 Symbol / Industry / Company Name: the NSE list (24h cache), else the NIFTY50 fallback above."""
    lst = nifty50_list()
    if lst is not None and {"Symbol", "Industry"} <= set(lst.columns) and len(lst):
        return lst if "Company Name" in lst else lst.assign(**{"Company Name": lst.Symbol})
    df = pd.DataFrame(NIFTY50, columns=["Symbol", "Industry"])
    return df.assign(**{"Company Name": df.Symbol})


@st.cache_data(ttl=120, show_spinner=False)
def nifty50_moves():
    lst = nifty50_constituents()
    if lst is None:
        return None
    tick = [f"{s}.NS" for s in lst.Symbol]
    raw = yf.download(tick, period="5d", interval="1d", auto_adjust=True, progress=False, group_by="ticker", threads=True)
    out = []
    for (_, row), t in zip(lst.iterrows(), tick):
        try:
            c = raw[t]["Close"].dropna()
            v = raw[t]["Volume"].dropna()
            out.append(dict(Symbol=row.Symbol, Industry=row.Industry, Company=row["Company Name"], last=float(c.iloc[-1]),
                            chg=float(c.iloc[-1] / c.iloc[-2] - 1) * 100, value=float(c.iloc[-1] * v.iloc[-1]) / 1e7))
        except Exception:
            continue
    return pd.DataFrame(out)


@st.cache_data(ttl=300, show_spinner=False)
def global_cues():
    raw = yf.download([t for _, t in GLOBAL], period="5d", interval="1d", auto_adjust=True, progress=False,
                      group_by="ticker", threads=True)
    out = []
    for name, t in GLOBAL:
        try:
            c = raw[t]["Close"].dropna()
            out.append((name, float(c.iloc[-1]), float(c.iloc[-1] / c.iloc[-2] - 1)))
        except Exception:
            out.append((name, None, None))
    return out


def fig_sectors(idx):
    s = idx[idx.key == "SECTORAL INDICES"].copy()
    s["name"] = s["index"].str.replace("NIFTY ", "", regex=False).str.title()
    s = s.sort_values("percentChange")
    fig = go.Figure(go.Bar(x=s.percentChange, y=s.name, orientation="h",
                           marker_color=np.where(s.percentChange >= 0, UP, DN),
                           text=[f"{x:+.2f}%" for x in s.percentChange], textposition="outside",
                           customdata=np.stack([s.perChange30d, s.advances, s.declines], axis=-1),
                           hovertemplate="%{y}: %{x:+.2f}% today<br>30d %{customdata[0]:+.1f}%<br>"
                                         "adv %{customdata[1]} / dec %{customdata[2]}<extra></extra>"))
    fig.update_layout(title="Sector strength today")
    return _dark(fig, max(360, 22 * len(s)))


def fig_heatmap(m):
    m = m.copy()
    m["label"] = m.Symbol + "<br>" + m.chg.map(lambda x: f"{x:+.2f}%")
    fig = go.Figure(go.Treemap(
        labels=list(m.label) + list(m.Industry.unique()), parents=list(m.Industry) + [""] * m.Industry.nunique(),
        values=list(m.value.clip(lower=1)) + [0] * m.Industry.nunique(),
        marker=dict(colors=list(m.chg) + [0] * m.Industry.nunique(), colorscale=[[0, "#b91c1c"], [.5, "#1f2937"], [1, "#15803d"]],
                    cmid=0, cmin=-3, cmax=3), branchvalues="remainder", textinfo="label",
        hovertemplate="%{label}<extra></extra>"))
    fig.update_layout(title="NIFTY 50 heat map (size = traded value, colour = % change)")
    return _dark(fig, 520)


def regime_read(nifty_chg, breadth, vr, sectors, cues):
    """One-paragraph market read from breadth, volatility, leadership and global cues."""
    bits, score = [], 0
    if nifty_chg is not None:
        bits.append(f"NIFTY {nifty_chg * 100:+.2f}%")
        score += np.sign(nifty_chg) * (1 if abs(nifty_chg) > 0.003 else 0.5)
    if breadth and breadth["adv"] + breadth["dec"]:
        ad = breadth["adv"] / max(breadth["dec"], 1)
        bits.append(f"breadth {breadth['adv']}↑/{breadth['dec']}↓ (A/D {ad:.2f})")
        score += 1 if ad > 1.3 else -1 if ad < 0.77 else 0
    if vr:
        bits.append(f"VIX {vr['vix']:.1f} (1-yr rank {vr['rank']:.0f}%)")
    if sectors is not None and len(sectors):
        top, bot = sectors.iloc[-1], sectors.iloc[0]
        bits.append(f"leaders {top['index'].replace('NIFTY ', '').title()} {top.percentChange:+.1f}%, "
                    f"laggards {bot['index'].replace('NIFTY ', '').title()} {bot.percentChange:+.1f}%")
    us = next((c for n, _, c in cues if n == "S&P 500 fut" and c is not None), None)
    if us is not None:
        bits.append(f"US futures {us * 100:+.2f}%")
        score += 0.5 * np.sign(us)
    tone = ("Risk-on: broad participation favours CALL setups and buying dips" if score >= 1.5 else
            "Risk-off: weak breadth favours PUT setups and selling rallies" if score <= -1.5 else
            "Mixed: be selective, prefer stocks showing relative strength/weakness vs the index")
    vol = ""
    if vr:
        vol = (" Options are cheap vs the past year (low VIX rank): buying premium is relatively favourable."
               if vr["rank"] < 25 else " Options are expensive (high VIX rank): prefer spreads or ITM strikes."
               if vr["rank"] > 70 else "")
    return tone, " · ".join(bits), vol
