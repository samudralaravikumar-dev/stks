"""
Searchable symbol universe for type-ahead pickers: indices, MCX, NSE F&O stocks (options available) and all
NSE equities, with company names. Lists come from NSE's public archives, cached for a day, with a fallback.
"""
import io

import pandas as pd
import requests
import streamlit as st

_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 "
                    "Safari/537.36", "Referer": "https://www.nseindia.com/"}
SPECIAL = [("NIFTY", "Nifty 50 index", "index"), ("BANKNIFTY", "Nifty Bank index", "index"),
           ("FINNIFTY", "Nifty Financial Services index", "index"), ("SENSEX", "BSE Sensex index", "index"),
           ("CRUDE", "MCX crude oil", "mcx"), ("NATURALGAS", "MCX natural gas", "mcx")]
TAG = {"index": "Index · options", "mcx": "MCX · options", "fno": "F&O · options", "eq": "Equity only"}


def _csv(url):
    r = requests.get(url, headers=_H, timeout=20)
    r.raise_for_status()
    if not r.text.lstrip().upper().startswith(("SYMBOL", "UNDERLYING")):
        raise ValueError("not a CSV")
    return pd.read_csv(io.StringIO(r.text), skipinitialspace=True)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def universe():
    """-> DataFrame[symbol, name, kind, lot, label] sorted indices, MCX, F&O, then other equities."""
    names, fno = {}, {}
    try:
        eq = _csv("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv")
        eq.columns = [c.strip() for c in eq.columns]
        eq = eq[eq["SERIES"].astype(str).str.strip() == "EQ"]
        names = dict(zip(eq["SYMBOL"].astype(str).str.strip(), eq["NAME OF COMPANY"].astype(str).str.strip()))
    except Exception:
        pass
    try:
        fo = _csv("https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv")
        fo.columns = [c.strip() for c in fo.columns]
        lot_col = fo.columns[2]
        for _, r in fo.iterrows():
            s = str(r["SYMBOL"]).strip()
            if s and s.upper() != "SYMBOL":
                fno[s] = pd.to_numeric(str(r[lot_col]).strip(), errors="coerce")
    except Exception:
        pass
    if not fno:  # offline fallback: the scanner's F&O list
        from engine import FNO_STOCKS
        fno = {s.strip(): None for s in FNO_STOCKS.split(",")}
    rows = [dict(symbol=s, name=n, kind=k, lot=None) for s, n, k in SPECIAL]
    special = {s for s, _, _ in SPECIAL} | {"NIFTYNXT50", "MIDCPNIFTY"}
    for s in sorted(fno):
        if s not in special and (s in names or not names):  # skip index codes with no listed company
            rows.append(dict(symbol=s, name=names.get(s, ""), kind="fno", lot=fno[s]))
    for s in sorted(names):
        if s not in fno and s not in special:
            rows.append(dict(symbol=s, name=names[s], kind="eq", lot=None))
    df = pd.DataFrame(rows)
    df["label"] = df.symbol + "  ·  " + df.name.where(df.name != "", df.symbol) + "  ·  " + df.kind.map(TAG)
    return df


def labels(first=()):
    """All picker labels, with the given symbols (e.g. the watchlist) moved to the top."""
    df = universe()
    top = [df.label[df.symbol == s].iloc[0] for s in first if (df.symbol == s).any()]
    return top + [x for x in df.label if x not in set(top)]


def to_symbol(label):
    return label.split("  ·  ")[0].strip().upper()


def label_of(sym):
    df = universe()
    m = df.label[df.symbol == sym]
    return m.iloc[0] if len(m) else None
