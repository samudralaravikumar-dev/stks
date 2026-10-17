"""
NSE bulk and block deals: large trades by funds, promoters and big investors, published by NSE after each session
(bulk: one client trading over 0.5% of a company's shares in a day; block: a single large trade in the block window).
Public data from NSE's own download; shown as context on the call cards, not as a signal by itself.
"""
import io

import pandas as pd
import streamlit as st

import events
import net


def _download(kind, start, end):
    url = (f"https://www.nseindia.com/api/historicalOR/bulk-block-short-deals?optionType={kind}"
           f"&from={start:%d-%m-%Y}&to={end:%d-%m-%Y}&csv=true")
    s = net.session("nse")
    for _ in range(2):
        r = s.get(url, headers=events._H, timeout=30)
        if r.ok and "csv" in r.headers.get("content-type", ""):
            df = pd.read_csv(io.BytesIO(r.content))
            df.columns = [c.strip() for c in df.columns]
            return df.assign(kind=kind.split("_")[0])
        events._nse("/api/snapshot-capital-market-largedeal")  # refresh NSE cookies, then retry once
    return pd.DataFrame()


@st.cache_data(ttl=3 * 3600, show_spinner=False)
def recent(days=10):
    """Bulk + block deals of the last `days` calendar days. -> DataFrame[date, symbol, client, side, qty, price, kind,
    value_cr (signed: + buy, - sell)]; empty on failure."""
    end = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None).normalize()
    try:
        d = pd.concat([_download(k, end - pd.Timedelta(days=days), end) for k in ("bulk_deals", "block_deals")],
                      ignore_index=True)
    except Exception:
        return pd.DataFrame()
    if d.empty:
        return d
    d = d.rename(columns={"Date": "date", "Symbol": "symbol", "Client Name": "client", "Buy / Sell": "side",
                          "Quantity Traded": "qty", "Trade Price / Wght. Avg. Price": "price"})
    d["date"] = pd.to_datetime(d.date, format="%d-%b-%Y", errors="coerce")
    for c in ("qty", "price"):
        d[c] = pd.to_numeric(d[c].astype(str).str.replace(",", ""), errors="coerce")
    d["symbol"] = d.symbol.astype(str).str.strip()
    d["side"] = d.side.astype(str).str.strip().str.upper()
    d["client"] = d.client.astype(str).str.strip()
    d = d.dropna(subset=["date", "qty", "price"])
    d["value_cr"] = d.qty * d.price / 1e7 * d.side.map({"BUY": 1, "SELL": -1}).fillna(0)
    return d[["date", "symbol", "client", "side", "qty", "price", "kind", "value_cr"]]


def summary(sym, sessions=5, min_cr=1.0):
    """Net bulk/block money in `sym` over its last few deal days. -> dict(net_cr, buy_cr, sell_cr, n, top, last) or None."""
    d = recent()
    if d.empty:
        return None
    x = d[d.symbol == sym]
    if x.empty:
        return None
    x = x[x.date >= sorted(d.date.unique())[-sessions:][0]] if d.date.nunique() >= sessions else x
    net_cr, buy, sell = x.value_cr.sum(), x.value_cr.clip(lower=0).sum(), -x.value_cr.clip(upper=0).sum()
    if max(buy, sell) < min_cr:
        return None
    top = x.assign(a=x.value_cr.abs()).sort_values("a", ascending=False).iloc[0]
    return dict(net_cr=net_cr, buy_cr=buy, sell_cr=sell, n=len(x), last=x.date.max(),
                top=f"{top.client.title()[:28]} {'bought' if top.value_cr > 0 else 'sold'} ₹{abs(top.value_cr):,.1f} Cr")


def line(sym):
    """One-line card text, or ''."""
    s = summary(sym)
    if not s:
        return ""
    gross = s["buy_cr"] + s["sell_cr"]
    if abs(s["net_cr"]) < 0.2 * gross:  # buyers and sellers matched (e.g. a block crossed between two funds)
        return (f"🏦 Bulk/block deals ({s['n']}, to {s['last']:%d %b}): two-way, ₹{s['buy_cr']:,.1f} Cr bought "
                f"and ₹{s['sell_cr']:,.1f} Cr sold (no clear direction)")
    lean = "net BUY" if s["net_cr"] > 0 else "net SELL"
    return (f"🏦 Bulk/block deals ({s['n']}, to {s['last']:%d %b}): {lean} ₹{abs(s['net_cr']):,.1f} Cr · {s['top']}")
