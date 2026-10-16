"""
Intraday CALL / PUT Signal Engine: Streamlit UI.
Run:  run.bat   (or: .venv\\Scripts\\python -m streamlit run trading_signals_app.py)
Analysis lives in engine.py; paper trading & alerts in paper.py; background runner in runner.py.
Educational tool only. NOT financial advice.
"""
from datetime import time as dtime

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Intraday CALL/PUT Engine", layout="wide")

import engine as E  # noqa: E402
import auth  # noqa: E402
import paper  # noqa: E402
import pro  # noqa: E402
import ui_kit  # noqa: E402
import symbols  # noqa: E402
import upstox_feed  # noqa: E402

DEFAULT_WATCHLIST = "NIFTY, BANKNIFTY, FINNIFTY, RELIANCE, HDFCBANK, ICICIBANK, SBIN, INFY, TCS, LT, CRUDE, NATURALGAS"


def wl_list():
    return list(dict.fromkeys(s.strip().upper() for s in st.session_state.get("wl_text", "").split(",") if s.strip()))


def save_watchlist():
    u = st.session_state.get("user")
    if u:
        st.session_state["wl_text"] = ", ".join(auth.set_watchlist(u["username"], wl_list()))


def wl_add_one():
    wl_add_from("wl_search")


def wl_add_from(key):
    """Search box callback: add the picked symbol immediately and clear the box."""
    pick = st.session_state.get(key)
    if pick:
        sym = symbols.to_symbol(pick)
        if sym not in wl_list():
            st.session_state["wl_text"] = ", ".join(wl_list() + [sym])
            save_watchlist()
            st.session_state["wl_flash"] = f"Added {sym}"
        else:
            st.session_state["wl_flash"] = f"{sym} is already in your watchlist"
    st.session_state[key] = None


def wl_quick_add():
    picks = st.session_state.get("wl_quick") or []
    if picks:
        st.session_state["wl_text"] = ", ".join(dict.fromkeys(wl_list() + list(picks)))
        save_watchlist()
        st.session_state["wl_flash"] = "Added " + ", ".join(picks)
    st.session_state["wl_quick"] = []


def wl_delete(sym):
    st.session_state["wl_text"] = ", ".join(s for s in wl_list() if s != sym)
    save_watchlist()
    st.session_state["wl_flash"] = f"Removed {sym}"


def wl_clear():
    st.session_state["wl_text"] = ""
    save_watchlist()
    st.session_state["wl_flash"] = "Watchlist cleared"


POPULAR = ["NIFTY", "BANKNIFTY", "FINNIFTY", "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "LT",
           "AXISBANK", "BHARTIARTL", "ITC", "TATASTEEL", "M&M", "BAJFINANCE", "CRUDE", "NATURALGAS"]


def symbol_options():
    return symbols.labels(first=wl_list())
from engine import *  # noqa: E402,F401,F403


def price_check():
    st.markdown("Enter a stock and the **CE / PE strike and price** you see at your broker "
                "(leave 0 to use the live NSE quote). Tick **I hold it** to get hold / book / exit advice instead.")
    with st.form("pc_form"):
        sym = st.text_input("Stock / index", "RELIANCE").strip().upper()
        cc, cp = st.columns(2)
        inp = {}
        for col, k in ((cc, "CE"), (cp, "PE")):
            col.markdown(f"**{'📗 CALL' if k == 'CE' else '📕 PUT'} ({k})**")
            strike = col.number_input(f"{k} strike (0 = ATM)", 0.0, 1e7, 0.0, key=f"pc_k_{k}")
            price = col.number_input(f"{k} market price (0 = live)", 0.0, 1e6, 0.0, key=f"pc_p_{k}")
            held = col.checkbox(f"I hold this {k}", key=f"pc_h_{k}")
            paid = col.number_input(f"{k} price paid (if held)", 0.0, 1e6, 0.0, key=f"pc_b_{k}")
            inp[k] = (strike, price, held, paid)
        go_ = st.form_submit_button("Get BUY / EXIT signals", type="primary")
    if not go_:
        return
    now = pd.Timestamp.now(tz=IST)
    vix, _ = last_close("^INDIAVIX")
    fx, _ = last_close("USDINR=X")
    with st.spinner(f"Analysing {sym}…"):
        a = analyze_fast(sym, dict(P, news=False), vix, fx, now)
    if "error" in a:
        st.error(a["error"])
        return
    row = a["row"]
    st.markdown(f"**{sym}** LTP {a['ltp']:.2f} · engine: **{a['status']}** · score {row.Score:+.1f} · {row.Regime} · "
                f"IV {a['iv'] * 100:.1f}% ({a['iv_src']})" + (f" · expiry {a['chain']['expiry']}" if a["chain"] else ""))
    styles = {"BUY": st.success, "HOLD": st.success, "WAIT": st.info, "BOOK PARTIAL": st.warning,
              "AVOID": st.error, "EXIT": st.error}
    cols = st.columns(2)
    for col, k in zip(cols, ("CE", "PE")):
        strike, price, held, paid = inp[k]
        s = option_price_signal(a, k, strike, price, held, paid, P, now)
        with col:
            src = "your price" if price else ("live quote" if s["live"] else "model price")
            styles[s["action"]](f"### {s['action']} · {a['spec']['name']} {s['strike']:g} {k}\n"
                                f"Price {s['price']:.2f} ({src}) · fair {s['fair']:.2f} ({s['rich'] * 100:+.0f}%) · "
                                f"delta {s['delta']:+.2f}" + (f" · your IV {s['piv'] * 100:.1f}%" if s["piv"] else ""))
            if held and paid:
                st.metric("P&L per unit", f"{s['pnl']:+.2f}", f"{(s['price'] / paid - 1) * 100:+.1f}%")
            st.write(f"**Premium stop {s['p_sl']:.2f}** (underlying {s['stop_lvl']:.2f})")
            if s["tgts"]:
                st.dataframe(pd.DataFrame([{"Target": f"T{i + 1}", "Underlying": round(t["level"], 2),
                                            "Premium ≈": round(t["prem"], 2),
                                            "Gain": f"{(t['prem'] / (paid or s['price']) - 1) * 100:+.0f}%",
                                            "Touch prob": f"{t['prob'] * 100:.0f}%"} for i, t in enumerate(s["tgts"])]),
                             hide_index=True, width="stretch")
            for w in s["why_for"]:
                st.write("🟢 " + w)
            for w in s["why_against"]:
                st.write("🔴 " + w)
    st.caption("BUY needs an engine setup on that side with no blockers (overpriced, opposite signal, Supertrend against, "
               "expiry day, session ending). WAIT = momentum without a setup. Held positions EXIT on the premium stop, "
               "an opposite signal, or a trend/momentum break. Not financial advice.")


# ---------------- UI ----------------
user = auth.gate()

st.markdown("""<style>
.block-container {padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1500px;}
div[data-testid="stMetricValue"] {font-size: 1.3rem;}
div[data-testid="stMetricLabel"] p {font-size: .78rem; opacity: .75;}
div[data-testid="stVerticalBlockBorderWrapper"] h4 {margin: 0 0 .1rem 0;}
</style>""", unsafe_allow_html=True)
st.markdown(ui_kit.CSS, unsafe_allow_html=True)

with st.sidebar:
    st.markdown(f"### 👤 {user['name']}")
    st.caption(f"@{user['username']} · {user['role']}")
    cu1, cu2 = st.columns(2)
    with cu1.popover("🔑 Password", use_container_width=True):
        with st.form("pw_form", clear_on_submit=True):
            old_pw = st.text_input("Current password", type="password")
            new_pw = st.text_input("New password", type="password")
            new_pw2 = st.text_input("Confirm new password", type="password")
            if st.form_submit_button("Change password", type="primary"):
                if not auth.verify(user["username"], old_pw)[0]:
                    st.error("Current password is wrong")
                elif new_pw != new_pw2:
                    st.error("New passwords do not match")
                else:
                    err = auth.set_password(user["username"], new_pw)
                    if err:
                        st.error(err)
                    else:
                        auth._remember(user)  # other devices are signed out; keep this one signed in
                        st.success("Password changed. Other devices were signed out.")
    if cu2.button("Log out", use_container_width=True):
        auth.logout()
    st.divider()
    with st.expander("📡 Data & watchlist", expanded=True):
        up_key = upstox_feed.analytics_token() or upstox_feed.credentials()[0]
        returning = str(st.query_params.get("state", "")).startswith("upstox")
        sources = ["Upstox (live)", "Zerodha Kite (live)", "Yahoo + NSE (free, delayed)"]
        source = st.radio("Market data", sources, index=0 if (up_key or returning) else 2)
        if source.startswith("Upstox"):
            _tok, up_status, up_url = upstox_feed.session()
            if _tok:
                E.UPSTOX = _tok
                st.success(f"🟢 Upstox: {up_status}")
                st.caption("Live candles · option chain with IV, greeks and real ΔOI")
                if st.button("Log out of Upstox"):
                    upstox_feed.logout()
                    st.rerun()
            else:
                st.warning(f"Upstox: {up_status}. Using Yahoo + NSE meanwhile.")
                if up_url:
                    st.link_button("🔑 Log in to Upstox", up_url, type="primary")
                    st.caption("You return here after login; the session is saved until ~03:30 tomorrow.")
        elif source.startswith("Zerodha"):
            _kite, kite_status, login_url = kf.session()
            if _kite is not None:
                E.KITE = _kite
                st.success(f"🟢 Kite: {kite_status}")
                if st.button("Log out of Kite"):
                    kf.logout()
                    st.rerun()
            else:
                st.warning(f"Kite: {kite_status}. Using Yahoo + NSE meanwhile.")
                if login_url:
                    st.link_button("🔑 Log in to Zerodha Kite", login_url, type="primary")
        if "wl_text" not in st.session_state:
            saved = auth.get_watchlist(user["username"])
            st.session_state["wl_text"] = ", ".join(saved) if saved else DEFAULT_WATCHLIST
        names = st.text_area("Watchlist (comma separated)", key="wl_text", height=90, on_change=save_watchlist,
                             help="Saved to your account. Manage it with search in the ⭐ Watchlist tab.")
        c1, c2 = st.columns(2)
        interval = c1.selectbox("Candles", ["1m", "5m", "15m"], index=2,
                                help="15m backtested markedly better than 5m/1m")
        period = c2.selectbox("History", ["5d", "30d", "60d"], index=2)
    with st.expander("🧠 Signal engine"):
        setups_on = st.multiselect("Setups", [k for k, _, _, _ in SETUPS], ["VWAPPB", "STFLIP", "SQZ", "ORB"],
                                   format_func=lambda k: SETUP_LABEL[k])
        th = st.slider("Confluence threshold (±)", 3.0, 12.0, 6.0, 0.5, help="Mixed regime adds +1")
        adx_min = st.slider("Trend regime: min ADX", 15, 30, 20)
        lookback = st.slider("Setup valid for N closed bars", 1, 6, 3)
        use_time = st.checkbox("NSE entry window", True)
        c1, c2 = st.columns(2)
        win_start = c1.time_input("From", dtime(9, 30), step=300)
        win_end = c2.time_input("To", dtime(14, 45), step=300)
        orb_cut = st.time_input("ORB valid until", dtime(13, 0), step=900)
    with st.expander("🛡 Risk & options"):
        capital = st.number_input("Capital (₹)", 10000, 100000000, 200000, 10000)
        risk_pct = st.slider("Risk per trade (% of capital)", 0.25, 3.0, 1.0, 0.25)
        strike_mode = st.selectbox("Strike selection", ["Delta target", "ATM", "1 step ITM", "2 steps ITM", "1 step OTM"])
        target_delta = st.slider("Target |delta|", 0.35, 0.80, 0.55, 0.05)
        exit_mode = st.selectbox("Exit after T1", ["2R target + flip", "Run to close"])
        rr = st.slider("Final target (R multiple)", 1.5, 4.0, 2.0, 0.5)
        stop_min = st.slider("Minimum stop (× ATR)", 0.5, 2.0, 0.75, 0.25)
        atr_mult = st.slider("Fallback stop (× ATR)", 0.75, 2.5, 1.5, 0.25)
        max_day = st.slider("Max trades per symbol per day", 1, 5, 2)
        st.markdown("**Portfolio risk guard**")
        daily_loss_pct = st.slider("Daily loss limit (% of capital)", 1.0, 10.0, 3.0, 0.5,
                                   help="Paper trading stops opening trades for the day once hit")
        max_open = st.slider("Max open positions", 1, 10, 4)
        corr_guard = st.checkbox("Treat NIFTY / BANKNIFTY / FINNIFTY same-side trades as one bet", True)
        max_loss = st.slider("Max premium loss before exit", 0.2, 0.6, 0.35, 0.05)
        hold = st.slider("Expected hold (hours)", 0.25, 5.0, 1.0, 0.25)
        st.markdown("**Overrides**")
        dte = st.number_input("Days to expiry (no chain)", 0, 45, 3)
        iv_over = st.number_input("IV % (0 = auto)", 0.0, 150.0, 0.0)
        step_over = st.number_input("Strike step (0 = auto)", 0.0, 1000.0, 0.0)
        lot_over = st.number_input("Lot size (0 = auto)", 0, 100000, 0)
        cost_pct = st.number_input("Backtest cost (% of underlying)", 0.0, 1.0, 0.03, 0.01)
        use_chain = st.checkbox("Live option chain", True)
        use_news = st.checkbox("News sentiment", True)
    with st.expander("📒 Paper trading & alerts"):
        paper_on = st.toggle("Paper-trade every signal", True)
        alerts_on = st.toggle("Send Telegram alerts", True)
    st.markdown("**🔄 Refresh**")
    c1, c2 = st.columns(2)
    auto = c1.toggle("Auto", False, help="Keep on during market hours so signals and paper trades update")
    every = c2.selectbox("Every", [60, 120, 300], index=0, format_func=lambda s: f"{s // 60} min",
                         label_visibility="collapsed")

P = dict(interval=interval, period=period, th=th, adx_min=adx_min, lookback=lookback, use_time=use_time,
         win_start=win_start, win_end=win_end, orb_cut=tmin(orb_cut), capital=capital, risk_pct=risk_pct,
         strike_mode=strike_mode, delta=target_delta, rr=rr, atr_mult=atr_mult, max_loss=max_loss, hold=hold,
         dte=dte, iv=iv_over, step=step_over, lot=lot_over, cost_pct=cost_pct, chain=use_chain, news=use_news,
         setups=set(setups_on), exit_mode=exit_mode, stop_min=stop_min, max_day=max_day,
         daily_loss_pct=daily_loss_pct, max_open=max_open, corr_guard=corr_guard)
SYMBOLS = list(dict.fromkeys(s.strip().upper() for s in names.split(",") if s.strip()))

COLORS = {"BUY CALL (CE)": "background-color:#1e8449;color:white", "BUY PUT (PE)": "background-color:#b03a2e;color:white",
          "WATCH: CALL bias": "background-color:#d5f5e3;color:#145a32", "WATCH: PUT bias": "background-color:#fadbd8;color:#78281f",
          "NO TRADE": "background-color:#7f8c8d;color:white"}
BADGE = {"BUY CALL (CE)": ("BUY CALL ▲", "green"), "BUY PUT (PE)": ("BUY PUT ▼", "red"),
         "WATCH: CALL bias": ("WATCH · CALL bias", "orange"), "WATCH: PUT bias": ("WATCH · PUT bias", "violet"),
         "NO TRADE": ("NO TRADE", "gray")}


def pct(a, b):
    return f"{(a / b - 1) * 100:+.0f}%" if b else None


def run_scan(now):
    vix, vix_chg = last_close("^INDIAVIX")
    fx, _ = last_close("USDINR=X")
    results = {}
    with st.spinner(f"Scanning {len(SYMBOLS)} symbols…"):
        for sym in SYMBOLS:
            try:
                results[sym] = analyze_fast(sym, P, vix, fx, now)
            except Exception as e:
                results[sym] = dict(spec=resolve(sym), error=f"{type(e).__name__}: {e}")
    st.session_state["last_results"] = results
    try:
        paper.log_recs([(s, recommend(a, P)) for s, a in results.items() if "error" not in a], now, "watchlist")
    except Exception:
        pass
    st.session_state["scan_time"] = now
    st.session_state["vix"] = (vix, vix_chg, fx)
    if paper_on:
        for _, _, kind, text in paper.notify(paper.paper_step(results, P, now), send=alerts_on):
            st.toast(text, icon={"ENTRY": "📥", "T1": "🟡", "EXIT": "📤"}.get(kind))
    return results, vix, vix_chg


def header_strip(now, vix, vix_chg):
    n, n_chg = last_close("^NSEI")
    b, b_chg = last_close("^NSEBANK")
    nse_open = now.weekday() < 5 and dtime(9, 15) <= now.time() <= dtime(15, 30)
    tr = paper.trades_df()
    today = now.date().isoformat()
    closed_today = tr[(tr.status == "CLOSED") & tr.exit_at.fillna("").str.startswith(today)]
    opn = tr[tr.status == "OPEN"]
    unreal = float(np.where(opn.half == 1, (opn.half_p - opn.entry_p) * opn.qty / 2 + (opn.last_p - opn.entry_p) * opn.qty / 2,
                            (opn.last_p - opn.entry_p) * opn.qty).sum()) if len(opn) else 0.0
    m = st.columns(6)
    m[0].metric("NIFTY 50", f"{n:,.2f}" if n else "n/a", f"{n_chg * 100:+.2f}%" if n else None)
    m[1].metric("BANK NIFTY", f"{b:,.2f}" if b else "n/a", f"{b_chg * 100:+.2f}%" if b else None)
    m[2].metric("India VIX", f"{vix:.2f}" if vix else "n/a", f"{vix_chg * 100:+.1f}%" if vix else None, delta_color="inverse")
    m[3].metric("Market (IST)", now.strftime("%H:%M"), "NSE open" if nse_open else "NSE closed",
                delta_color="normal" if nse_open else "off")
    m[4].metric("Paper P&L today", f"₹{closed_today.pnl.sum() + unreal:,.0f}", f"{len(closed_today)} closed")
    m[5].metric("Open paper trades", len(opn), f"₹{unreal:,.0f} unrealized", delta_color="off")


def idea_card(sym, a, key=""):
    row, p, spec = a["row"], a["plan"], a["spec"]
    label, color = BADGE[a["status"]]
    with st.container(border=True):
        h1, h2 = st.columns([3, 2])
        h1.markdown(f"#### {sym}")
        h1.caption(f"LTP **{a['ltp']:,.2f}** · {row.Regime} · score {row.Score:+.1f}")
        with h2:
            st.badge(label, color=color)
        exp_txt = p["expiry"] or f"~{p['dte']:.0f} days"
        st.markdown(f"**{spec['name']} {p['strike']:g} {p['kind']}** · {exp_txt} · "
                    + ("live premium" if p["live"] else "model premium*"))
        m = st.columns(4)
        m[0].metric("Entry", f"{p['prem']:.2f}")
        m[1].metric("Stop", f"{p['p_sl']:.2f}", pct(p["p_sl"], p["prem"]))
        m[2].metric("Target 1", f"{p['p_t1']:.2f}", pct(p["p_t1"], p["prem"]))
        m[3].metric("Target 2", f"{p['p_t2']:.2f}", pct(p["p_t2"], p["prem"]))
        st.caption(f"Underlying {p['entry']:,.2f} → stop **{p['stop']:,.2f}** · T1 **{p['t1']:,.2f}** · T2 **{p['t2']:,.2f}**")
        st.progress(min(a["conf"], 100) / 100, text=f"Confidence {a['conf']}/100 · {row.Setups}")
        size = (f"Size {p['lots']} lot × {p['lot']} · max risk ₹{p['risk']:,.0f}" if p["lots"]
                else f"1 lot ({p['lot']}) risks ₹{(p['prem'] - p['p_sl']) * p['lot']:,.0f}: above your risk budget")
        st.caption("📦 " + size)
        for n in p["notes"] + [x for x in a["notes"] if "edge" in x.lower()]:
            if "budget" not in n:
                st.caption("⚠️ " + n)


def watch_card(sym, a):
    row, tg, sa = a["row"], a["tg"], a["sa"]
    label, color = BADGE[a["status"]]
    k = "CE" if row.Score > 0 else "PE"
    s_ = sa["sides"][k]
    zs = tg["up"] if k == "CE" else tg["down"]
    with st.container(border=True):
        h1, h2 = st.columns([1, 1])
        h1.markdown(f"**{sym}** · {a['ltp']:,.2f}")
        with h2:
            st.badge(label, color=color)
        st.caption(f"Score {row.Score:+.1f} · {row.Regime} · waiting for a setup")
        st.markdown(f"Idea: **{s_['strike']:g} {k} @ {s_['prem']:.2f}**" + ("" if s_["live"] else "*")
                    + (f" → T1 {zs[0]['level']:,.2f}" if zs else ""))
        why = next((n for n in a["notes"] if "chase" in n or "trigger" in n), "")
        if why:
            st.caption("⏳ " + why)


def grid(items, n_cols, fn):
    for i in range(0, len(items), n_cols):
        cols = st.columns(n_cols)
        for col, (sym, a) in zip(cols, items[i:i + n_cols]):
            with col:
                fn(sym, a)


def _chg(a):
    return a["ltp"] / a["row"].PDC - 1 if pd.notna(a["row"].PDC) and a["row"].PDC else None


def _spark(a, n=26):
    return [float(x) for x in a["d"].Close.tail(n)] + [float(a["ltp"])]


def dashboard():
    now = pd.Timestamp.now(tz=IST)
    results, vix, vix_chg = run_scan(now)
    fx = st.session_state.get("vix", (None, None, None))[2]
    n, n_chg = last_close("^NSEI")
    b, b_chg = last_close("^NSEBANK")
    live = now.weekday() < 5 and dtime(9, 15) <= now.time() <= dtime(15, 30)
    ok = {s: a for s, a in results.items() if "error" not in a}
    items = [("NIFTY 50", f"{n:,.2f}" if n else "n/a", n_chg), ("BANK NIFTY", f"{b:,.2f}" if b else "n/a", b_chg),
             ("India VIX", f"{vix:.2f}" if vix else "n/a", vix_chg), ("USD/INR", f"{fx:.2f}" if fx else "n/a", None)]
    if "CRUDE" in ok:
        items.append(("Crude (MCX≈)", f"{ok['CRUDE']['ltp']:,.0f}", _chg(ok["CRUDE"])))
    st.html(ui_kit.ribbon(items, live, now.strftime("%H:%M IST")))
    tr = paper.trades_df()
    today = now.date().isoformat()
    closed = tr[tr.status == "CLOSED"]
    closed_today = closed[closed.exit_at.fillna("").str.startswith(today)]
    opn = tr[tr.status == "OPEN"]
    unreal = float(np.where(opn.half == 1, (opn.half_p - opn.entry_p) * opn.qty / 2 + (opn.last_p - opn.entry_p) * opn.qty / 2,
                            (opn.last_p - opn.entry_p) * opn.qty).sum()) if len(opn) else 0.0
    ideas = sorted([(s, a) for s, a in ok.items() if a["plan"]], key=lambda x: -x[1]["conf"])
    bull = sum(a["row"].Score >= a["row"].Need for a in ok.values())
    bear = sum(a["row"].Score <= -a["row"].Need for a in ok.values())
    st.html(ui_kit.kpis(sum(a["plan"]["kind"] == "CE" for _, a in ideas), sum(a["plan"]["kind"] == "PE" for _, a in ideas),
                        bull, bear, len(ok) - bull - bear, float(closed_today.pnl.sum()) + unreal, len(closed_today),
                        len(opn), unreal, float((closed.pnl > 0).mean()) if len(closed) else None))
    rs = paper.risk_status(P, now)
    if not rs["ok"]:
        st.error("🛑 **Risk guard active** · " + " · ".join(rs["reasons"]))
    evs = [(s, sev, t) for s, a in ok.items() for sev, t in a.get("events", [])]
    hi = [x for x in evs if x[1] == "high"]
    if evs:
        txt = " · ".join(f"**{s}**: {t.split(':')[0]}" for s, _, t in (hi or evs)[:6])
        (st.warning if hi else st.info)(f"📅 Events this week · {txt}")
    st.html(ui_kit.section(f"Trade ideas · {len(ideas)}"))
    st.html(ui_kit.ideas_grid([ui_kit.idea_card(s, a, _chg(a), ui_kit.sparkline(_spark(a))) for s, a in ideas]))
    rest = sorted([(s, a) for s, a in ok.items() if not a["plan"]], key=lambda x: -abs(x[1]["row"].Score))
    if rest:
        st.html(ui_kit.section("Watchlist heat-map · strongest first"))
        st.html(ui_kit.tiles_grid([ui_kit.tile(s, a, _chg(a), _spark(a)) for s, a in rest]))
    st.html(ui_kit.section("All symbols"))
    rows = []
    for sym, a in results.items():
        if "error" in a:
            rows.append({"Symbol": sym, "Signal": "ERROR", "Setup": a["error"]})
            continue
        row, tg, sa = a["row"], a["tg"], a["sa"]
        c = _chg(a)
        rows.append({"Symbol": sym, "Trend": _spark(a), "LTP": a["ltp"], "Day %": c * 100 if c is not None else None,
                     "Signal": a["status"], "View": sa["verdict"].split(":")[0], "Score": row.Score,
                     "Up T1": tg["up"][0]["level"] if tg["up"] else None,
                     "Up T2": tg["up"][1]["level"] if len(tg["up"]) > 1 else None,
                     "Down T1": tg["down"][0]["level"] if tg["down"] else None,
                     "Down T2": tg["down"][1]["level"] if len(tg["down"]) > 1 else None,
                     "CALL idea": f"{sa['sides']['CE']['strike']:g} @ {sa['sides']['CE']['prem']:.2f}",
                     "PUT idea": f"{sa['sides']['PE']['strike']:g} @ {sa['sides']['PE']['prem']:.2f}",
                     "Setup": row.Setups or "-",
                     "Edge": f"{a['bt']['exp']:+.2f}R × {a['bt']['trades']}" if a["bt"] else "-",
                     "Data": a["src"] + (f"+{a['chain']['source']}" if a["chain"] else "")})
    if not rows:
        st.info("Your watchlist is empty: add stocks in the ⭐ Watchlist tab.")
        return
    num = st.column_config.NumberColumn
    st.dataframe(pd.DataFrame(rows).style.map(lambda x: COLORS.get(x, ""), subset=["Signal"]), width="stretch",
                 hide_index=True, column_config={
                     "Trend": st.column_config.LineChartColumn("Trend", width="small"),
                     "LTP": num(format="%.2f"), "Day %": num(format="%+.2f%%"), "Score": num(format="%+.1f"),
                     "Up T1": num(format="%.2f"), "Up T2": num(format="%.2f"),
                     "Down T1": num(format="%.2f"), "Down T2": num(format="%.2f")})
    st.caption(f"Updated {now:%H:%M:%S} IST" + (f" · auto-refresh every {every // 60} min" if auto else
                                               " · turn on Auto refresh in the sidebar for live updates")
               + " · model premiums are estimates: confirm on your broker's chain · open 🔎 Analysis for any symbol.")


def render_targets(a, sym):
    tg, sa, spec, chain = a["tg"], a["sa"], a["spec"], a["chain"]
    cu, cd = st.columns(2)
    for col, side, title in ((cu, "up", "⬆ Upside targets"), (cd, "down", "⬇ Downside targets")):
        col.markdown(f"**{title}**")
        zs = tg[side]
        if zs:
            col.dataframe(pd.DataFrame([{"Target": f"T{i + 1}", "Level": round(z["level"], 2),
                                         "Move": f"{z['dist']:+.2f} ({z['pct']:+.2f}%)",
                                         "Touch prob": f"{z['prob'] * 100:.0f}%",
                                         "Confluence": "★" * min(z["strength"], 5),
                                         "Built from": ", ".join(z["labels"])} for i, z in enumerate(zs)]),
                          hide_index=True, width="stretch")
        else:
            col.caption("No level found on this side.")
    cc, cp = st.columns(2)
    for col, k, title in ((cc, "CE", "📗 CALL (CE)"), (cp, "PE", "📕 PUT (PE)")):
        s_ = sa["sides"][k]
        with col.container(border=True):
            st.markdown(f"**{title} · {spec['name']} {s_['strike']:g} {k}** · " + ("live" if s_["live"] else "model*"))
            m = st.columns(4)
            m[0].metric("Premium", f"{s_['prem']:.2f}")
            m[1].metric("Stop", f"{s_['p_sl']:.2f}", pct(s_["p_sl"], s_["prem"]))
            m[2].metric("Delta", f"{s_['delta']:+.2f}")
            m[3].metric("Theta/day", f"{-s_['theta']:.2f}")
            st.caption(f"Breakeven at expiry {s_['breakeven']:,.2f} · stop if underlying {s_['stop_lvl']:,.2f}"
                       + (f" · IV {s_['iv_k']:.1f}%" if s_["iv_k"] else "")
                       + (f" · OI {s_['oi']:,.0f}" if s_["oi"] else ""))
            if s_["targets"]:
                st.dataframe(pd.DataFrame([{"Underlying at": f"T{i + 1} {t['level']:,.2f}", "Premium ≈": round(t["prem"], 2),
                                            "Gain": pct(t["prem"], s_["prem"]), "Touch prob": f"{t['prob'] * 100:.0f}%"}
                                           for i, t in enumerate(s_["targets"])]), hide_index=True, width="stretch")
    for w in sa["why"]:
        st.caption("• " + w)
    if chain:
        with st.expander(f"Option-chain OI around ATM · {chain['source']} {chain['expiry']} · PCR {chain['pcr'] or 0:.2f} · "
                         f"call wall {chain['call_wall']:g} · put wall {chain['put_wall']:g} · max pain {chain['max_pain']:g}"):
            st.dataframe(oi_table(chain, a["ltp"]), hide_index=True, width="stretch")
    elif spec["kind"] == "commodity":
        st.caption("MCX proxy (connect Kite for real MCX options): premiums are Black-76 estimates.")


def analysis_tab():
    res = st.session_state.get("last_results", {})
    c = st.columns([4, 3])
    pick = c[0].selectbox("Symbol (type to search)", symbol_options(), key="an_pick", accept_new_options=True)
    sym = symbols.to_symbol(pick) if pick else None
    if not sym:
        return
    a = res.get(sym)
    if a is None:
        now = pd.Timestamp.now(tz=IST)
        vix, _, fx = st.session_state.get("vix", (None, None, None))
        with st.spinner(f"Analysing {sym}…"):
            a = analyze_fast(sym, P, vix, fx, now)
    if "error" in a:
        st.error(a["error"])
        return
    row, p, spec, d = a["row"], a["plan"], a["spec"], a["d"]
    label, color = BADGE[a["status"]]
    with c[1]:
        st.badge(label, color=color)
        st.caption(f"{spec['ticker']} · LTP **{a['ltp']:,.2f}** · {row.Regime} · score {row.Score:+.1f} · "
                   f"IV {a['iv'] * 100:.1f}% ({a['iv_src']}) · view **{a['sa']['verdict']}**")
    left, right = st.columns([3, 2])
    with left:
        st.plotly_chart(make_chart(d, sym, spec, a["has_vol"]), width="stretch", key="an_chart")
    with right:
        if p:
            idea_card(sym, a)
        else:
            with st.container(border=True):
                st.markdown(f"#### {sym}: no active trade")
                for n in a["notes"]:
                    st.caption("⏳ " + n)
        with st.container(border=True):
            st.markdown("**Six parameter groups**")
            for pl in PILLARS:
                sc = row["P_" + pl]
                texts = [t_ for s_, cat, t_ in a["why"] if cat == pl]
                st.markdown(f"{'🟢' if sc > 0 else '🔴' if sc < 0 else '⚪'} **{pl} {sc:+.1f}**"
                            + (f"  \n<small>{'; '.join(texts)}</small>" if texts else ""), unsafe_allow_html=True)
            for s_, cat, text in a["ctx"]:
                st.caption(("🟢 " if s_ == "+" else "🔴 ") + f"[{cat}] {text}")
    st.markdown("### 🎯 Targets and CALL vs PUT")
    render_targets(a, sym)
    t1, t2, t3 = st.tabs(["📐 Indicator values", "📰 News", "🧪 Backtest"])
    with t1:
        groups = {
            "1. Moving averages": {"EMA9": row.EMA9, "EMA21": row.EMA21, "EMA50": row.EMA50, "VWAP": row.VWAP,
                                   "Supertrend": row.ST, "ADX": row.ADX, "+DI": row.PDI, "-DI": row.MDI},
            "2. RSI": {"RSI(14)": row.RSI, "RSI(2)": row.RSI2, "CCI(20)": row.CCI},
            "3. MACD": {"MACD": row.MACD, "Signal": row.MACDsig, "Histogram": row.MACDh},
            "4. Volume": {"Volume": row.Volume if a["has_vol"] else np.nan, "RVOL": row.RVOL, "MFI(14)": row.MFI},
            "5. Bollinger/ATR": {"BB upper": row.BBu, "BB middle": row.BBm, "BB lower": row.BBl, "BB width %": row.BBw * 100,
                                 "ATR(14)": row.ATR, "ATR 20-avg": row.ATRma, "Choppiness": row.CHOP},
            "6. Support/Resistance": {"Swing high": row.SwingHi, "Swing low": row.SwingLo, "Prev-day high": row.PDH,
                                      "Prev-day low": row.PDL, "Pivot": row.Pivot, "R1": row.R1, "S1": row.S1,
                                      "CPR top": row.CPRtop, "CPR bottom": row.CPRbot, "OR high": row.ORH, "OR low": row.ORL},
        }
        gcols = st.columns(3)
        for gi, (gname, vals) in enumerate(groups.items()):
            gcols[gi % 3].markdown(f"**{gname}**")
            gcols[gi % 3].dataframe(pd.DataFrame({"Value": {k: (round(float(x), 2) if pd.notna(x) else None)
                                                            for k, x in vals.items()}}), width="stretch")
    with t2:
        if a["news"]:
            st.markdown(f"Headline sentiment **{a['ns']:+.2f}**")
            for n in a["news"][:10]:
                icon = "🟢" if n["sentiment"] > .15 else "🔴" if n["sentiment"] < -.15 else "⚪"
                st.markdown(f"{icon} [{n['title']}]({n['link']}) · {n['source']}")
        else:
            st.caption("No headlines (or news is off in the sidebar).")
    with t3:
        t, s = a["bt_trades"], a["bt"]
        if s is None:
            st.info("No trades in this history with the current settings.")
        else:
            k = st.columns(6)
            k[0].metric("Trades", s["trades"])
            k[1].metric("Win rate", f"{s['win'] * 100:.0f}%")
            k[2].metric("Expectancy", f"{s['exp']:+.2f} R")
            k[3].metric("Profit factor", f"{s['pf']:.2f}")
            k[4].metric("Total", f"{s['total']:+.1f} R")
            k[5].metric("Max drawdown", f"{s['dd']:.1f} R")
            st.line_chart(t.set_index("exit_time").R.cumsum().rename("Cumulative R"))
            st.dataframe(t, width="stretch", hide_index=True)
            st.caption("R = multiples of the initial underlying risk after costs; underlying move only (no theta/IV).")


def users_tab():
    st.markdown("Manage who can log in. Passwords are stored as salted PBKDF2-SHA256 hashes in `users.db`.")
    st.dataframe(pd.DataFrame(auth.list_users()).drop(columns=["salt", "pw"]), hide_index=True, width="stretch")
    c1, c2 = st.columns(2)
    with c1.form("add_user", clear_on_submit=True):
        st.markdown("**Add user**")
        un = st.text_input("Username")
        nm = st.text_input("Display name")
        mob = st.text_input("Mobile (for OTP login)")
        pw = st.text_input("Temporary password", type="password")
        role = st.selectbox("Role", ["user", "admin"])
        if st.form_submit_button("Create", type="primary"):
            err = auth.create_user(un, pw, nm, role, mobile=mob)
            st.error(err) if err else st.success(f"Created {un}. Share the temporary password privately.")
    with c2.form("manage_user"):
        st.markdown("**Manage user**")
        everyone = [u["username"] for u in auth.list_users()]
        others = [u for u in everyone if u != user["username"]]
        who = st.selectbox("User", everyone)
        action = st.radio("Action", ["Set mobile", "Reset password", "Make admin", "Make user", "Delete"], horizontal=True)
        newmob = st.text_input("Mobile (for Set mobile; blank removes it)")
        newpw = st.text_input("New password (for reset)", type="password")
        if st.form_submit_button("Apply"):
            if action == "Set mobile":
                err = auth.set_mobile(who, newmob)
                st.error(err) if err else st.success("Mobile updated")
            elif who == user["username"] and action in ("Delete", "Make user"):
                st.error("You can't delete or demote your own account")
            elif action == "Reset password":
                err = auth.set_password(who, newpw)
                st.error(err) if err else st.success("Password reset")
            elif action == "Delete":
                auth.delete_user(who)
                st.success(f"Deleted {who}")
            else:
                auth.set_role(who, "admin" if action == "Make admin" else "user")
                st.success("Role updated")


def scanner():
    st.markdown("Ranks liquid F&O stocks by the six-group momentum score and **relative strength vs NIFTY** "
                "(stocks leading the index suit CALLs, laggards suit PUTs). Add a symbol to the sidebar list for its full option plan.")
    uni = st.text_area("Universe", FNO_STOCKS, height=90)
    syms = list(dict.fromkeys(s.strip().upper() for s in uni.split(",") if s.strip()))
    if not st.button("Scan now", type="primary"):
        st.caption("Press **Scan now** (about 20-40 s for 40 stocks).")
        return
    now = pd.Timestamp.now(tz=IST)
    specs = {s: resolve(s) for s in syms}
    per = "1mo" if P["interval"] != "1m" else "5d"
    if E.KITE is not None:
        with st.spinner(f"Downloading {len(specs) + 1} symbols from Kite (rate-limited, ~3/s)…"):
            data = {sp["ticker"]: get_prices(sp, P["interval"], per)[0] for sp in specs.values()}
            data["^NSEI"] = get_prices(resolve("NIFTY"), P["interval"], per)[0]
    else:
        tickers = tuple(sorted({sp["ticker"] for sp in specs.values()} | {"^NSEI"}))
        with st.spinner(f"Downloading {len(tickers)} symbols…"):
            data = load_many(tickers, P["interval"], per)
    nraw = data.get("^NSEI")
    nifty_chg = np.nan
    if nraw is not None and len(nraw):
        ndf, nltp = prepare(nraw, resolve("NIFTY"), P["interval"], None)
        nifty_chg = day_change(ndf, nltp)
    rows = []
    prog = st.progress(0.0)
    for i, (sym, spec) in enumerate(specs.items()):
        prog.progress((i + 1) / len(specs))
        raw = data.get(spec["ticker"])
        if raw is None or len(raw) < 80:
            continue
        try:
            df, ltp = prepare(raw, spec, P["interval"], None)
            d, _ = compute(df, spec, P, bool(df.Volume.sum() > 0))
            row = d.iloc[-1]
            chg = day_change(df, ltp)
            hour_bars = max(1, 60 // int(P["interval"][:-1]))
            dr = 1 if row.Score >= 0 else -1
            exits = exit_advice(d, ltp, dr, float(row.Close), 0, spec, now)["exit"]
            rows.append({"Symbol": sym, "LTP": round(ltp, 2), "Day %": round(chg * 100, 2),
                         "RS vs NIFTY %": round((chg - nifty_chg) * 100, 2),
                         "1h ROC %": round((d.Close.iloc[-1] / d.Close.iloc[-1 - hour_bars] - 1) * 100, 2),
                         "Stock view": stock_view(row),
                         "Option": "BUY CALL" if row.Signal > 0 else "BUY PUT" if row.Signal < 0 else "-",
                         "Score": round(row.Score, 1),
                         **{PILLAR_SHORT[pl]: round(row["P_" + pl], 1) for pl in PILLARS},
                         "RSI": round(row.RSI, 1), "ADX": round(row.ADX, 1), "RVOL": round(row.RVOL, 2),
                         "Setup": row.Setups or "-",
                         "Warning for holders": "; ".join(exits[:2]) or "-"})
        except Exception:
            continue
    prog.empty()
    if not rows:
        st.warning("No data returned.")
        return
    t = pd.DataFrame(rows)
    t["Rank"] = t.Score + t["RS vs NIFTY %"].fillna(0) * 2
    vc = {"BUY": "background-color:#1e8449;color:white", "SELL / SHORT": "background-color:#b03a2e;color:white",
          "Momentum ↑ (wait for setup)": "background-color:#d5f5e3;color:#145a32",
          "Momentum ↓ (wait for setup)": "background-color:#fadbd8;color:#78281f"}
    st.markdown(f"NIFTY today: **{nifty_chg * 100:+.2f}%** · {len(t)} stocks scanned")
    c1, c2 = st.columns(2)
    c1.markdown("**Strongest momentum (CALL candidates)**")
    c1.dataframe(t.nlargest(8, "Rank")[["Symbol", "LTP", "Day %", "RS vs NIFTY %", "Score", "Stock view"]],
                 hide_index=True, width="stretch")
    c2.markdown("**Weakest momentum (PUT candidates)**")
    c2.dataframe(t.nsmallest(8, "Rank")[["Symbol", "LTP", "Day %", "RS vs NIFTY %", "Score", "Stock view"]],
                 hide_index=True, width="stretch")
    st.markdown("**Full scan**")
    st.dataframe(t.sort_values("Rank", ascending=False).drop(columns="Rank").style.map(
        lambda x: vc.get(x, ""), subset=["Stock view"]), hide_index=True, width="stretch")


def position_manager():
    st.markdown("Tell it what you hold; it says **hold, book partial, trail or exit** from live closed candles.")
    c = st.columns(4)
    sym = c[0].text_input("Symbol", "RELIANCE", key="pm_sym").strip().upper()
    inst = c[1].selectbox("Position", ["CALL (CE) bought", "PUT (PE) bought", "Stock long", "Stock short"], key="pm_inst")
    entry = c[2].number_input("Underlying price at entry", 0.0, 1e7, 0.0, key="pm_entry",
                              help="0 = use the current price")
    stop = c[3].number_input("Underlying stop (0 = 1.5 ATR)", 0.0, 1e7, 0.0, key="pm_stop")
    c = st.columns(4)
    strike = c[0].number_input("Option strike (optional)", 0.0, 1e7, 0.0, key="pm_k")
    prem_in = c[1].number_input("Premium paid (optional)", 0.0, 1e6, 0.0, key="pm_prem")
    qty = c[2].number_input("Quantity", 0, 10_000_000, 0, key="pm_qty")
    if not c[3].button("Check position", type="primary"):
        return
    spec = resolve(sym)
    fx, _ = last_close("USDINR=X")
    raw, src = get_prices(spec, P["interval"], "7d" if P["interval"] == "1m" else "1mo")
    if raw.empty:
        st.error("No data for that symbol.")
        return
    df, ltp = prepare(raw, spec, P["interval"], fx if src == "Yahoo" else None)
    d, _ = compute(df, spec, P, bool(df.Volume.sum() > 0))
    dr = 1 if inst in ("CALL (CE) bought", "Stock long") else -1
    adv = exit_advice(d, ltp, dr, entry or ltp, stop, spec, pd.Timestamp.now(tz=IST))
    color = {"EXIT": st.error, "BOOK PARTIAL & TRAIL": st.warning, "HOLD": st.success}[adv["action"]]
    color(f"### {adv['action']}\n{sym} {inst} · LTP {ltp:.2f} · underlying move {adv['pnl_pts']:+.2f} pts "
          f"({adv['gain']:+.2f} R) · stop {adv['stop']:.2f} → **trail to {adv['trail']:.2f}**")
    for x in adv["exit"]:
        st.write("🔴 " + x)
    for x in adv["book"]:
        st.write("🟡 " + x)
    if not adv["exit"] and not adv["book"]:
        st.write("🟢 Trend, momentum and structure still support the position. Keep the stop at "
                 f"{adv['trail']:.2f}.")
    if strike and inst.startswith(("CALL", "PUT")):
        chain = get_chain(spec, ltp, pd.Timestamp.now(tz=IST))
        kind = "CE" if dr > 0 else "PE"
        q = chain["quotes"].get(float(strike), {}).get(kind) if chain else None
        if q and q["ltp"]:
            pnl = (q["ltp"] - prem_in) * qty if prem_in and qty else None
            st.info(f"Live {sym} {strike:g} {kind} ({chain['expiry']}): LTP {q['ltp']:.2f}, IV {q['iv']:.1f}%"
                    + (f" · P&L ₹{pnl:,.0f}" if pnl is not None else "")
                    + (f" · premium {(q['ltp'] / prem_in - 1) * 100:+.1f}%" if prem_in else ""))
            if prem_in and q["ltp"] <= prem_in * (1 - P["max_loss"]):
                st.error(f"Premium is down more than {P['max_loss'] * 100:.0f}%: exit per the premium stop rule.")
        else:
            st.caption("Live quote for that strike not available (nearest weekly/monthly expiry only).")
    st.plotly_chart(make_chart(d, sym, spec, bool(df.Volume.sum() > 0)), width="stretch", key="pm_chart")


def spread_tab():
    st.markdown("Buying a single option pays full theta. A **debit spread** (buy one strike, sell a further one) "
                "cuts cost, theta and the breakeven distance, in exchange for capping the profit at the sell strike.")
    with st.form("sp_form"):
        c = st.columns([2, 3, 1.4, 1.4, 1])
        sym = c[0].text_input("Symbol", "NIFTY", key="sp_sym").strip().upper()
        strat = c[1].selectbox("Strategy", SPREADS, key="sp_strat")
        kb = c[2].number_input("Buy strike (0 = auto)", 0.0, 1e7, 0.0, key="sp_kb")
        ks = c[3].number_input("Sell strike (0 = auto)", 0.0, 1e7, 0.0, key="sp_ks",
                               help="Auto: first strike at/after the T2 target zone")
        lots = c[4].number_input("Lots (0 = auto)", 0, 1000, 0, key="sp_lots")
        go_ = st.form_submit_button("Build", type="primary")
    if not go_:
        return
    now = pd.Timestamp.now(tz=IST)
    vix, _ = last_close("^INDIAVIX")
    fx, _ = last_close("USDINR=X")
    with st.spinner(f"Pricing {sym}…"):
        a = analyze_fast(sym, dict(P, news=False), vix, fx, now)
    if "error" in a:
        st.error(a["error"])
        return
    sa = spread_analysis(a, strat, kb, ks, lots, P, now)
    alt_name = ("Long CE" if sa["kind"] == "CE" else "Long PE") if not sa["single"] else \
        ("Bull call spread (CE debit)" if sa["kind"] == "CE" else "Bear put spread (PE debit)")
    alt = spread_analysis(a, alt_name, sa["kb"], 0, sa["lots"], P, now)
    qty = max(sa["lots"], 1) * sa["lot"]
    row = a["row"]
    st.markdown(f"**{sym}** LTP {a['ltp']:.2f} · engine **{a['status']}** · score {row.Score:+.1f} · "
                f"IV {a['iv'] * 100:.1f}% ({a['iv_src']}) · {sa['dte']:.1f} days to expiry · view: **{a['sa']['verdict']}**")
    legs = " / ".join(f"{'BUY' if x['sgn'] > 0 else 'SELL'} {x['K']:g} {sa['kind']} @ {x['prem']:.2f}"
                      + ("" if x["live"] else "*") for x in sa["legs"])
    st.success(f"### {strat}\n{legs} · **net debit {sa['debit']:.2f}** · {sa['lots']} lot(s) × {sa['lot']} = {qty} qty "
               f"· cost ₹{sa['debit'] * qty:,.0f}")
    if sa["lots"] == 0:
        st.warning(f"Risk budget ₹{capital * risk_pct / 100:,.0f} buys 0 lots at this debit (chart shows 1 lot).")
    m = st.columns(6)
    m[0].metric("Max loss", f"₹{sa['max_loss'] * qty:,.0f}")
    m[1].metric("Max profit", "unlimited" if sa["single"] else f"₹{sa['max_profit'] * qty:,.0f}")
    m[2].metric("Breakeven", f"{sa['be']:.2f}", f"{(sa['be'] / a['ltp'] - 1) * 100:+.2f}%", delta_color="off")
    m[3].metric("Prob. of profit", f"{sa['pop'] * 100:.0f}%")
    m[4].metric("Theta / day", f"₹{sa['theta'] * qty:,.0f}")
    m[5].metric("Delta (qty)", f"{sa['delta'] * qty:+,.0f}")
    st.plotly_chart(spread_chart(sa, a, qty), width="stretch", key="sp_chart")

    def summary(x, name):
        q = max(x["lots"], 1) * x["lot"]
        return {"Strategy": name, "Strikes": " / ".join(f"{l['K']:g}" for l in x["legs"]),
                "Cost (₹)": round(x["debit"] * q), "Max loss (₹)": round(x["max_loss"] * q),
                "Max profit (₹)": "unlimited" if x["single"] else round(x["max_profit"] * q),
                "Breakeven": round(x["be"], 2), "POP": f"{x['pop'] * 100:.0f}%",
                "Theta/day (₹)": round(x["theta"] * q), "Vega/1% IV (₹)": round(x["vega"] * q),
                **{f"P&L at T{i + 1} (₹)": round((t["value"] - x["debit"]) * q) for i, t in enumerate(x["targets"])},
                "Return at T1": f"{(x['targets'][0]['value'] / x['debit'] - 1) * 100:+.0f}%" if x["targets"] and x["debit"] > 0 else "-"}
    st.markdown("**Spread vs single option (same quantity)**")
    st.dataframe(pd.DataFrame([summary(sa, strat), summary(alt, alt_name)]), hide_index=True, width="stretch")
    st.caption("P&L at targets assumes the move happens within the expected hold time. * = model price (no live quote). "
               "Sell-leg margin is blocked by your broker for spreads; check it before placing.")


def paper_tab(symbols):
    tok, chat = paper.telegram_creds()
    df = paper.trades_df()
    opn, closed = df[df.status == "OPEN"], df[df.status == "CLOSED"]
    s = paper.stats(closed)
    st.markdown("Every signal is paper-traded with the same rules as the backtest: **book 50% at T1 and move the stop "
                "to cost, exit the rest at T2, on the stop, on a Supertrend flip, or at square-off.** "
                + ("🟢 Paper trading is on." if paper_on else "⚪ Paper trading is off (sidebar)."))
    c = st.columns(3)
    if c[0].button("💾 Save settings for background engine", help="runner.py uses these symbols and settings"):
        paper.save_settings(P, symbols, every, telegram=alerts_on)
        st.success(f"Saved {len(symbols)} symbols to {paper.SETTINGS.name}. Start it with run_engine.bat.")
    if c[1].button("📨 Send test Telegram alert"):
        st.success("Sent ✅") if paper.send_telegram("✅ Test alert from your intraday CALL/PUT engine") else \
            st.error("Not sent: add [telegram] bot_token and chat_id to .streamlit/secrets.toml")
    c[2].caption(("🟢 Telegram configured" if tok and chat else "⚪ Telegram not configured")
                 + (f" · engine settings saved {pd.Timestamp(paper.load_settings()['saved']):%d %b %H:%M}"
                    if paper.SETTINGS.exists() else " · engine settings not saved yet"))
    with st.expander("How to get alerts on your phone (2 minutes)"):
        st.markdown("1. In Telegram, message **@BotFather** → `/newbot` → copy the **bot token**.\n"
                    "2. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` "
                    "and copy `chat.id`.\n"
                    "3. Add to `.streamlit/secrets.toml`:\n```toml\n[telegram]\nbot_token = \"123:ABC...\"\nchat_id = \"123456789\"\n```\n"
                    "4. Click **Send test Telegram alert**. For alerts with the browser closed, click **Save settings** "
                    "and run **run_engine.bat**.")
    st.subheader(f"Open paper positions ({len(opn)})")
    if len(opn):
        o = opn.copy()
        o["Unrealized ₹"] = np.where(o.half == 1, (o.half_p - o.entry_p) * o.qty / 2 + (o.last_p - o.entry_p) * o.qty / 2,
                                     (o.last_p - o.entry_p) * o.qty).round(0)
        st.dataframe(o[["symbol", "kind", "strike", "expiry", "qty", "entry_p", "last_p", "sl_p", "t1_p", "t2_p", "half",
                        "Unrealized ₹", "entry_u", "last_u", "setup", "conf", "priced", "opened_at", "updated_at"]],
                     hide_index=True, width="stretch")
    else:
        st.caption("No open paper trades.")
    st.subheader("Performance")
    if s:
        k = st.columns(7)
        k[0].metric("Closed trades", s["trades"])
        k[1].metric("Win rate", f"{s['win'] * 100:.0f}%")
        k[2].metric("Avg R", f"{s['exp']:+.2f}")
        k[3].metric("Profit factor", f"{s['pf']:.2f}")
        k[4].metric("Net P&L", f"₹{s['pnl']:,.0f}")
        k[5].metric("Max drawdown", f"₹{s['dd']:,.0f}")
        k[6].metric("Best / worst", f"₹{s['best']:,.0f}", f"₹{s['worst']:,.0f}", delta_color="off")
        cl = closed.sort_values("exit_at")
        st.line_chart(cl.set_index(pd.to_datetime(cl.exit_at)).pnl.cumsum().rename("Cumulative P&L (₹)"))
        g1, g2 = st.columns(2)
        agg = dict(trades=("pnl", "count"), win_rate=("pnl", lambda x: (x > 0).mean()), avg_R=("r_mult", "mean"),
                   pnl=("pnl", "sum"))
        g1.markdown("**By setup**")
        g1.dataframe(closed.groupby("setup").agg(**agg).round(2), width="stretch")
        g2.markdown("**By symbol**")
        g2.dataframe(closed.groupby("symbol").agg(**agg).round(2), width="stretch")
        if s["trades"] < 30:
            st.info(f"{s['trades']} closed trades so far: wait for 30+ before judging the edge.")
        st.markdown("**Journal**")
        st.dataframe(closed[["id", "symbol", "kind", "strike", "setup", "opened_at", "entry_p", "exit_at", "exit_p",
                             "exit_reason", "half", "qty", "pnl", "r_mult", "conf", "priced"]].round(2),
                     hide_index=True, width="stretch")
        st.download_button("⬇ Download journal (CSV)", df.to_csv(index=False), "paper_journal.csv", "text/csv")
    else:
        st.caption("No closed trades yet. Keep the Signals tab on auto-refresh, or run run_engine.bat, during market hours.")
    st.subheader("Alert log")
    al = paper.alerts_df()
    if len(al):
        st.dataframe(al, hide_index=True, width="stretch")
    else:
        st.caption("No alerts yet.")
    with st.expander("Danger zone"):
        if st.button("Close all open paper trades now"):
            n = paper.close_all(st.session_state.get("last_results", {}), pd.Timestamp.now(tz=IST))
            st.success(f"Closed {n} trade(s).")
        if st.checkbox("I understand this deletes the whole paper journal") and st.button("Reset journal"):
            paper.reset_journal()
            st.success("Journal cleared.")


def _get_analysis(sym):
    res = st.session_state.get("last_results", {})
    a = res.get(sym)
    if a is None or "error" in a:
        now = pd.Timestamp.now(tz=IST)
        vix, _, fx = st.session_state.get("vix", (None, None, None))
        if vix is None:
            vix, _ = last_close("^INDIAVIX")
            fx, _ = last_close("USDINR=X")
        a = analyze_fast(sym, dict(P, news=False), vix, fx, now)
    return a


def quick_tab():
    st.markdown("Start typing a **stock name or symbol** (e.g. *rel*, *tata*, *hdfc*), pick up to 8, and get a CALL or "
                "PUT recommendation with entry, stop and targets. F&O stocks (options available) are listed first.")
    opts = symbol_options()
    with st.form("quick_form"):
        c1, c2 = st.columns([5, 1])
        chosen = c1.multiselect("Stock / index", opts, default=[x for x in (symbols.label_of("RELIANCE"),
                                                                            symbols.label_of("TATASTEEL")) if x],
                                max_selections=8, accept_new_options=True, label_visibility="collapsed",
                                placeholder="Type a few letters to search…", key="quick_pick")
        go_ = c2.form_submit_button("Get CALL / PUT", type="primary", use_container_width=True)
    syms = list(dict.fromkeys(symbols.to_symbol(x) for x in chosen))
    if not go_ and not st.session_state.get("quick_syms"):
        return
    if go_:
        st.session_state["quick_syms"] = syms
    now = pd.Timestamp.now(tz=IST)
    for sym in st.session_state["quick_syms"]:
        with st.spinner(f"Analysing {sym}…"):
            try:
                a = _get_analysis(sym)
            except Exception as e:
                a = dict(error=f"{type(e).__name__}: {e}")
        if "error" in a:
            st.error(f"{sym}: {a['error']} (check the symbol, e.g. M&M, BAJAJ-AUTO)")
            continue
        r = recommend(a, P)
        paper.log_recs([(sym, r)], now, "search")
        o = pro.options_analytics(a, P, now) if a.get("chain") else None
        gamma = (f"{o['regime']} gamma · straddle ±{o['straddle']:,.0f}" if o and o["straddle"] else
                 f"{o['regime']} gamma" if o else None)
        c1, c2 = st.columns([3, 2])
        with c1:
            st.html(ui_kit.reco_card(r, _chg(a), ui_kit.sparkline(_spark(a)), gamma))
        with c2:
            tg = a["tg"]
            st.markdown("**Intraday levels**")
            lv = [{"Side": "⬆ Up" if i < len(tg["up"]) else "", "Level": round(z["level"], 2), "Touch": f"{z['prob'] * 100:.0f}%",
                   "From": ", ".join(z["labels"][:2])} for i, z in enumerate(tg["up"])]
            lv += [{"Side": "⬇ Down", "Level": round(z["level"], 2), "Touch": f"{z['prob'] * 100:.0f}%",
                    "From": ", ".join(z["labels"][:2])} for z in tg["down"]]
            st.dataframe(pd.DataFrame(lv), hide_index=True, width="stretch")
            if o:
                for icon, text in pro.options_read(o, a)[:3]:
                    st.caption(f"{icon} {text}")
            st.caption(f"IV {a['iv'] * 100:.1f}% ({a['iv_src']}) · data {a['src']}"
                       + (f" + {a['chain']['source']} chain" if a["chain"] else ""))
    st.caption("Recommendations follow the engine's rules, which have no proven edge yet: size small, always use the stop, "
               "book 50% at T1. Model premiums are estimates; confirm on your broker's option chain. Not financial advice.")


def options_tab():
    res = st.session_state.get("last_results", {})
    c = st.columns([4, 3])
    pick = c[0].selectbox("Symbol (type to search)", symbol_options(), key="oa_pick", accept_new_options=True)
    sym = symbols.to_symbol(pick) if pick else None
    if not sym:
        return
    now = pd.Timestamp.now(tz=IST)
    with st.spinner(f"Loading {sym} option chain…"):
        a = _get_analysis(sym)
    if "error" in a:
        st.error(a["error"])
        return
    o = pro.options_analytics(a, P, now)
    if not o:
        st.warning(f"No option chain for {sym} right now (MCX needs Kite/Upstox; NSE may be blocking).")
        return
    vr = pro.vix_rank() if a["spec"]["kind"] == "index" else None
    items = [("Spot", f"{o['S']:,.2f}", _chg(a)),
             ("ATM straddle", f"±{o['straddle']:,.1f}" if o["straddle"] else "n/a", None),
             ("Expiry range", f"{o['em_lo']:,.0f}–{o['em_hi']:,.0f}" if o["straddle"] else "n/a", None),
             ("Gamma regime", o["regime"], None), ("Gamma flip", f"{o['flip']:,.0f}" if o["flip"] else "none near", None),
             ("PCR (OI)", f"{o['pcr']:.2f}" if o["pcr"] else "n/a", None),
             ("Max pain", f"{o['chain']['max_pain']:g}", None),
             ("Skew 2% OTM", f"{o['skew']:+.1f} vol" if o["skew"] is not None else "n/a", None)]
    if vr:
        items.append(("VIX 1-yr rank", f"{vr['rank']:.0f}%", None))
    st.html(ui_kit.ribbon(items, True, f"{o['chain']['source']} · {o['chain']['expiry']} · {o['dte']:.1f}d"))
    with st.container(border=True):
        for icon, text in pro.options_read(o, a):
            st.markdown(f"{icon} {text}")
    st.plotly_chart(pro.fig_oi(o), width="stretch", key="oa_oi")
    c1, c2 = st.columns([3, 2])
    c1.plotly_chart(pro.fig_gex(o), width="stretch", key="oa_gex")
    c2.plotly_chart(pro.fig_smile(o), width="stretch", key="oa_smile")
    st.caption("GEX assumes dealers are long calls and short puts (SqueezeMetrics convention); relative units. "
               "Positive = moves get damped (range/pin), negative = moves get amplified (trend/expansion).")


def market_tab():
    now = pd.Timestamp.now(tz=IST)
    with st.spinner("Loading market data…"):
        cues = pro.global_cues()
        idx, breadth = pro.nse_indices()
        moves = pro.nifty50_moves()
        vr = pro.vix_rank()
    n, n_chg = last_close("^NSEI")
    sectors = idx[idx.key == "SECTORAL INDICES"].sort_values("percentChange") if idx is not None else None
    tone, facts, vol = pro.regime_read(n_chg, breadth, vr, sectors, cues)
    color = "#22c55e" if tone.startswith("Risk-on") else "#ef4444" if tone.startswith("Risk-off") else "#f59e0b"
    st.html(f'<div class="tk"><div class="kpi" style="border-left:4px solid {color}"><div class="l">Market read · {now:%H:%M} IST</div>'
            f'<div class="big" style="font-size:22px;color:{color}">{tone}</div><div class="s">{facts}{vol}</div></div></div>')
    st.html(ui_kit.section("Global cues"))
    st.html(ui_kit.ribbon([(nm, f"{v:,.2f}" if v is not None else "n/a", c) for nm, v, c in cues], True,
                          "overnight / live"))
    c1, c2 = st.columns([3, 2])
    with c1:
        if moves is not None and len(moves):
            up, dn = int((moves.chg > 0).sum()), int((moves.chg < 0).sum())
            st.html(ui_kit.section(f"NIFTY 50 breadth · {up} up / {dn} down · avg {moves.chg.mean():+.2f}%"))
            st.plotly_chart(pro.fig_heatmap(moves), width="stretch", key="mi_heat")
        else:
            st.info("NIFTY 50 constituents unavailable right now.")
    with c2:
        if sectors is not None:
            st.html(ui_kit.section("Sectors"))
            st.plotly_chart(pro.fig_sectors(idx), width="stretch", key="mi_sec")
        if breadth:
            st.caption(f"All NSE stocks: {breadth['adv']:,} advancing · {breadth['dec']:,} declining · {breadth['unch']} unchanged")
    if moves is not None and len(moves):
        g1, g2 = st.columns(2)
        g1.markdown("**Top NIFTY gainers**")
        g1.dataframe(moves.nlargest(5, "chg")[["Symbol", "Industry", "last", "chg"]].round(2), hide_index=True, width="stretch")
        g2.markdown("**Top NIFTY losers**")
        g2.dataframe(moves.nsmallest(5, "chg")[["Symbol", "Industry", "last", "chg"]].round(2), hide_index=True, width="stretch")


def watchlist_tab():
    wl = wl_list()
    if st.session_state.get("wl_flash"):
        st.toast(st.session_state.pop("wl_flash"), icon="⭐")
    c1, c2 = st.columns([3, 2])
    c1.selectbox("➕ Add a stock", [x for x in symbols.labels() if symbols.to_symbol(x) not in wl], index=None,
                 key="wl_search", on_change=wl_add_one, accept_new_options=True,
                 placeholder="🔍 Type 2-3 letters (rel, tata, bank…) and pick: it's added instantly")
    c2.pills("Quick add", [s for s in POPULAR if s not in wl][:12], selection_mode="multi", key="wl_quick",
             on_change=wl_quick_add)
    if not wl:
        st.info("Your watchlist is empty. Search above or tap a quick-add chip.")
        return
    res = st.session_state.get("last_results", {})
    recs = []
    h1, h2 = st.columns([4, 1])
    view = h1.segmented_control("View", ["List", "Cards"], default="List", key="wl_view", label_visibility="collapsed")
    with h2.popover("🗑 Clear all", use_container_width=True):
        st.write(f"Remove all {len(wl)} stocks?")
        st.button("Yes, clear watchlist", on_click=wl_clear, type="primary")
    for sym in wl:
        a = res.get(sym)
        r = recommend(a, P) if a is not None and "error" not in a else None
        if r:
            recs.append((sym, a, r))
        if view == "Cards" and r:
            continue
        with st.container(border=True):
            c = st.columns([2.1, 2.4, 4.8, 0.55], vertical_alignment="center")
            if r is None:
                c[0].markdown(f"**{sym}**")
                c[1].caption("⏳ scanning… (refresh)" if a is None else "⚠ " + a["error"][:70])
            else:
                chg = _chg(a) or 0
                c[0].markdown(f"**{sym}**  \n{a['ltp']:,.2f} :{'green' if chg >= 0 else 'red'}[{'▲' if chg >= 0 else '▼'} "
                              f"{abs(chg) * 100:.2f}%]")
                if r["mode"] == "none":
                    with c[1]:
                        st.badge("NO TRADE", color="gray")
                        st.caption(f"score {r['score']:+.1f} · no edge")
                    up, dn = r["alt"]
                    c[2].markdown(" · ".join(x for x in (
                        f":green[**CALL** if above **{up['trigger']:,.2f}**] ({up['strike']:g} CE ≈ {up['prem']:.2f})" if up else "",
                        f":red[**PUT** if below **{dn['trigger']:,.2f}**] ({dn['strike']:g} PE ≈ {dn['prem']:.2f})" if dn else "")
                        if x))
                else:
                    ce = r["kind"] == "CE"
                    with c[1]:
                        st.badge(("BUY CALL" if ce else "BUY PUT") + (" now" if r["mode"] == "now" else
                                 f" {'>' if ce else '<'} {r['u_entry']:,.0f}"), color="green" if ce else "red")
                        st.caption(f"confidence {r['conf']}" + (f" · T1 hit {r['bt']['t1'] * 100:.0f}% hist" if r.get("bt") else ""))
                    c[2].markdown(f"**{r['strike']:g} {r['kind']}** · Entry **{r['prem']:.2f}** · SL :red[{r['sl']:.2f}] · "
                                  f"T1 :green[**{r['t1']:.2f}**] · T2 :green[{r['t2']:.2f}]  \n"
                                  f"<small>Underlying → SL {r['u_sl']:,.2f} · T1 {r['u_t1']:,.2f} · T2 {r['u_t2']:,.2f}"
                                  + ("" if r["mode"] == "now" else
                                     f" · enter after a 15-min close {'above' if ce else 'below'} {r['u_entry']:,.2f}")
                                  + "</small>", unsafe_allow_html=True)
                for sev, t in a.get("events", []):
                    if sev == "high":
                        c[1].caption("📅 :orange[" + t.split(":")[0] + "]")
            c[3].button("✕", key=f"wl_del_{sym}", on_click=wl_delete, args=(sym,), help=f"Remove {sym}")
    if view == "Cards":
        for i in range(0, len(recs), 2):
            cols = st.columns(2)
            for col, (sym, a, r) in zip(cols, recs[i:i + 2]):
                with col:
                    st.html(ui_kit.reco_card(r, _chg(a), ui_kit.sparkline(_spark(a))))
                    st.button(f"✕ Remove {sym}", key=f"wl_delc_{sym}", on_click=wl_delete, args=(sym,))
    calls = sum(r["kind"] == "CE" and r["mode"] != "none" for _, _, r in recs)
    puts = sum(r["kind"] == "PE" and r["mode"] != "none" for _, _, r in recs)
    st.caption(f"{len(wl)} stocks · {calls} CALL · {puts} PUT · {len(recs) - calls - puts} no trade · saved to your account · "
               "Entry/SL/T1/T2 are option premiums. Not financial advice.")


def nifty50_tab():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    lst = pro.nifty50_list()
    if lst is None or lst.empty:
        st.warning("NIFTY 50 constituents list is unavailable right now (NSE archive unreachable).")
        return
    names = dict(zip(lst.Symbol, lst["Company Name"]))
    inds = dict(zip(lst.Symbol, lst.Industry))
    data = st.session_state.get("n50")
    c1, c2, c3 = st.columns([2, 3, 3], vertical_alignment="center")
    run = c1.button("🔄 Scan NIFTY 50" if data else "▶ Scan all 50 stocks", type="primary", use_container_width=True)
    filt = c2.segmented_control("Show", ["All", "CALL", "PUT", "No trade"], default="All", key="n50_filter",
                                label_visibility="collapsed")
    if data:
        c3.caption(f"Last scan {data['time']:%H:%M:%S} IST · {data['src']} data · {len(data['rows'])} stocks")
    if run:
        now = pd.Timestamp.now(tz=IST)
        vix, _, fx = st.session_state.get("vix", (None, None, None))
        if vix is None:
            vix, _ = last_close("^INDIAVIX")
            fx, _ = last_close("USDINR=X")
        Pn = dict(P, news=False)
        bar = st.progress(0.0, text="Scanning NIFTY 50…")
        out, syms = {}, list(lst.Symbol)
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(analyze_fast, s, Pn, vix, fx, now): s for s in syms}
            for i, fu in enumerate(as_completed(futs), 1):
                s = futs[fu]
                try:
                    out[s] = fu.result()
                except Exception as e:
                    out[s] = dict(error=f"{type(e).__name__}: {e}")
                bar.progress(i / len(syms), text=f"Scanned {i}/{len(syms)} · {s}")
        bar.empty()
        rows = []
        for s in syms:
            a = out.get(s, {})
            if "error" in a:
                rows.append({"Symbol": s, "Company": names.get(s, ""), "Industry": inds.get(s, ""),
                             "Recommendation": "⚠ data error", "_rank": 9})
                continue
            r = recommend(a, Pn)
            base = {"Symbol": s, "Company": names.get(s, ""), "Industry": inds.get(s, ""), "Trend": _spark(a),
                    "LTP": a["ltp"], "Day %": (_chg(a) or 0) * 100, "Score": round(r["score"], 1)}
            ev = next((t.split(":")[0] for sev, t in a.get("events", []) if sev == "high"), "")
            if r["mode"] == "none":
                up, dn = r["alt"]
                rows.append(base | {"Recommendation": "NO TRADE", "_rank": 3,
                                    "Plan": " / ".join(x for x in ((f"CALL > {up['trigger']:,.2f}" if up else ""),
                                                                   (f"PUT < {dn['trigger']:,.2f}" if dn else "")) if x),
                                    "Event": ev})
            else:
                rows.append(base | {
                    "Recommendation": r["action"], "_rank": 0 if r["mode"] == "now" else 1,
                    "Plan": "enter now" if r["mode"] == "now" else
                    f"after 15-min close {'above' if r['kind'] == 'CE' else 'below'} {r['u_entry']:,.2f}",
                    "Option": f"{r['strike']:g} {r['kind']}", "Entry ₹": r["prem"], "Stop ₹": r["sl"],
                    "Target 1 ₹": r["t1"], "Target 2 ₹": r["t2"], "Stock target 1": r["u_t1"], "Stock target 2": r["u_t2"],
                    "Stock stop": r["u_sl"], "Confidence": r["conf"], "Event": ev})
        paper.log_recs([(s, recommend(a, Pn)) for s, a in out.items() if "error" not in a], now, "nifty50")
        data = dict(rows=rows, time=now, src="Upstox" if E.UPSTOX else "Kite" if E.KITE is not None else "Yahoo/NSE")
        st.session_state["n50"] = data
    if not data:
        st.info("Press **Scan all 50 stocks** to get a CALL / PUT recommendation with targets for every NIFTY 50 stock "
                "(about 30-60 seconds the first time, instant on re-scans within a minute).")
        return
    df = pd.DataFrame(data["rows"])
    rec = df.Recommendation.astype(str)
    n_call, n_put = int(rec.str.startswith("BUY CALL").sum()), int(rec.str.startswith("BUY PUT").sum())
    n_none = int((rec == "NO TRADE").sum())
    k = st.columns(5)
    k[0].metric("CALL ideas", n_call, f"{int((rec.str.startswith('BUY CALL') & df.Plan.eq('enter now')).sum())} enter now",
                delta_color="off")
    k[1].metric("PUT ideas", n_put, f"{int((rec.str.startswith('BUY PUT') & df.Plan.eq('enter now')).sum())} enter now",
                delta_color="off")
    k[2].metric("No trade", n_none)
    k[3].metric("Breadth", f"{int((df['Day %'] > 0).sum())}↑ / {int((df['Day %'] < 0).sum())}↓")
    k[4].metric("Avg score", f"{df.Score.mean():+.1f}", "bullish" if df.Score.mean() > 2 else
                "bearish" if df.Score.mean() < -2 else "mixed", delta_color="off")
    if filt == "CALL":
        df = df[rec.str.startswith("BUY CALL")]
    elif filt == "PUT":
        df = df[rec.str.startswith("BUY PUT")]
    elif filt == "No trade":
        df = df[rec == "NO TRADE"]
    df = df.assign(_abs=df.Score.abs()).sort_values(["_rank", "_abs"], ascending=[True, False]).drop(columns=["_rank", "_abs"])

    def color(v):
        s = str(v)
        return ("background-color:#14532d;color:#bbf7d0;font-weight:700" if s.startswith("BUY CALL") else
                "background-color:#7f1d1d;color:#fecaca;font-weight:700" if s.startswith("BUY PUT") else
                "background-color:#334155;color:#e2e8f0" if s == "NO TRADE" else "")
    num = st.column_config.NumberColumn
    cols = [c for c in ["Symbol", "Trend", "LTP", "Day %", "Recommendation", "Plan", "Option", "Entry ₹", "Stop ₹",
                        "Target 1 ₹", "Target 2 ₹", "Stock target 1", "Stock target 2", "Stock stop", "Confidence",
                        "Score", "Event", "Industry", "Company"] if c in df.columns]
    st.dataframe(df[cols].style.map(color, subset=["Recommendation"]), hide_index=True, width="stretch",
                 height=min(80 + 35 * len(df), 1400),
                 column_config={"Trend": st.column_config.LineChartColumn("Trend", width="small"),
                                "LTP": num(format="%.2f"), "Day %": num(format="%+.2f%%"), "Score": num(format="%+.1f"),
                                **{c: num(format="%.2f") for c in ("Entry ₹", "Stop ₹", "Target 1 ₹", "Target 2 ₹",
                                                                   "Stock target 1", "Stock target 2", "Stock stop")},
                                "Confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=100,
                                                                              format="%d")})
    add = st.multiselect("⭐ Add to my watchlist", [s for s in df.Symbol if s not in wl_list()], key="n50_add",
                         placeholder="Pick stocks from this scan to track them in ⭐ Watchlist")
    if add and st.button(f"Add {len(add)} to watchlist"):
        st.session_state["wl_text"] = ", ".join(dict.fromkeys(wl_list() + add))
        save_watchlist()
        st.success("Added " + ", ".join(add))
    st.caption("Sorted: enter-now ideas first, then trigger plans, then no trade; strongest score first. Entry/Stop/"
               "Target ₹ are option premiums; Stock target/stop are share-price levels. 📅 Event = results/expiry "
               "risk (avoid holding options into results). Not financial advice.")


def _bars_for(sym):
    """5-min session bars (IST) + square-off minute for checking recommendation outcomes."""
    spec = resolve(sym)
    raw, src = get_prices(spec, "5m", "5d")
    fx = st.session_state.get("vix", (None, None, None))[2] or last_close("USDINR=X")[0]
    df, _ = prepare(raw, spec, "5m", fx if src == "Yahoo" else None)
    return df, tmin(SESSIONS[spec["sess"]][2])


def track_tab():
    st.markdown("Every **BUY CALL / BUY PUT** the portal shows (watchlist, NIFTY 50 scan, stock search) is logged once per "
                "stock per day and checked against what the price did next: **target hit ✅, stop hit ❌**, trigger never "
                "reached, or squared off at the close.")
    now = pd.Timestamp.now(tz=IST)
    c = st.columns([1.4, 2, 1.8, 2.2], vertical_alignment="center")
    run = c[0].button("🔄 Check results now", type="primary", use_container_width=True)
    period = c[1].segmented_control("Period", ["Today", "7 days", "30 days"], default="7 days", key="tr_period",
                                    label_visibility="collapsed")
    side = c[2].segmented_control("Side", ["All", "CALL", "PUT"], default="All", key="tr_side", label_visibility="collapsed")
    mode = c[3].segmented_control("Type", ["All", "Enter now", "Trigger"], default="All", key="tr_mode",
                                  label_visibility="collapsed")
    last = st.session_state.get("tr_eval")
    if run or last is None or (now - last).total_seconds() > 300:
        with st.spinner("Checking outcomes against live prices…"):
            paper.evaluate_recs(_bars_for, now)
        st.session_state["tr_eval"] = now
    days = {"Today": 0, "7 days": 7, "30 days": 30}[period or "7 days"]
    df = paper.recs_df(days=max(days, 1))
    if days == 0:
        df = df[df.day == now.date().isoformat()]
    if side in ("CALL", "PUT"):
        df = df[df.kind == ("CE" if side == "CALL" else "PE")]
    if mode == "Enter now":
        df = df[df["mode"] == "now"]
    elif mode == "Trigger":
        df = df[df["mode"] == "trigger"]
    if df.empty:
        st.info("No recommendations logged for this filter yet. They are recorded automatically as the dashboard, "
                "NIFTY 50 scan and stock search show BUY CALL / BUY PUT ideas.")
        return
    win = df.status.isin(["T1 HIT", "T2 HIT"]) | ((df.status == "SQUARED OFF") & (df.r_mult > 0))
    loss = (df.status == "STOP HIT") | ((df.status == "SQUARED OFF") & (df.r_mult <= 0))
    resolved = win | loss
    k = st.columns(6)
    k[0].metric("Recommendations", len(df))
    k[1].metric("Triggered", int(df.triggered_at.notna().sum()), f"{int((df.status == 'NOT TRIGGERED').sum())} never triggered",
                delta_color="off")
    k[2].metric("✅ Succeeded", int(win.sum()), f"{int(df.status.isin(['T1 HIT', 'T2 HIT']).sum())} hit target",
                delta_color="off")
    k[3].metric("❌ Failed", int(loss.sum()), f"{int((df.status == 'STOP HIT').sum())} hit stop", delta_color="off")
    k[4].metric("Success rate", f"{win.sum() / resolved.sum() * 100:.0f}%" if resolved.any() else "–",
                f"of {int(resolved.sum())} resolved", delta_color="off")
    k[5].metric("Avg result", f"{df.r_mult[resolved].mean():+.2f} R" if resolved.any() else "–",
                f"{int(df.status.isin(['OPEN', 'WAITING']).sum())} still running", delta_color="off")
    g1, g2 = st.columns([2, 3])
    with g1:
        cnt = df.status.value_counts()
        order = ["T2 HIT", "T1 HIT", "SQUARED OFF", "OPEN", "WAITING", "NOT TRIGGERED", "STOP HIT"]
        colors = {"T2 HIT": "#16a34a", "T1 HIT": "#22c55e", "SQUARED OFF": "#eab308", "OPEN": "#38bdf8",
                  "WAITING": "#64748b", "NOT TRIGGERED": "#475569", "STOP HIT": "#ef4444"}
        xs = [s for s in order if s in cnt]
        fig = go.Figure(go.Bar(x=[cnt[s] for s in xs], y=xs, orientation="h", marker_color=[colors[s] for s in xs],
                               text=[cnt[s] for s in xs], textposition="outside"))
        fig.update_layout(title="Outcomes", template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                          plot_bgcolor="rgba(0,0,0,0)", height=320, margin=dict(l=10, r=10, t=36, b=10))
        st.plotly_chart(fig, width="stretch", key="tr_fig")
    with g2:
        grp = df.assign(win=win, loss=loss, side=df.kind.map({"CE": "CALL", "PE": "PUT"}),
                        type=df["mode"].map({"now": "Enter now", "trigger": "Trigger"}))
        summ = grp.groupby(["side", "type"]).agg(ideas=("id", "count"), succeeded=("win", "sum"), failed=("loss", "sum"),
                                                 avg_R=("r_mult", "mean")).reset_index()
        summ["success_rate"] = (summ.succeeded / (summ.succeeded + summ.failed).replace(0, np.nan) * 100).round(0)
        st.markdown("**By side and type**")
        st.dataframe(summ.round(2), hide_index=True, width="stretch")
        by_sym = grp.groupby("symbol").agg(ideas=("id", "count"), succeeded=("win", "sum"), failed=("loss", "sum"),
                                           avg_R=("r_mult", "mean")).sort_values("ideas", ascending=False).head(10)
        st.markdown("**By stock (top 10)**")
        st.dataframe(by_sym.round(2), width="stretch")

    def color(v):
        s = str(v)
        return ("color:#4ade80;font-weight:700" if s.startswith("✅") or s.startswith("🟢") else
                "color:#f87171;font-weight:700" if s.startswith("❌") or s.startswith("🔴") else "color:#94a3b8")
    show = df.assign(Time=pd.to_datetime(df.ts).dt.strftime("%d %b %H:%M"), Option=df.strike.map(lambda x: f"{x:g}") + " " + df.kind)
    show = show[["Time", "symbol", "action", "Option", "prem", "u_entry", "u_sl", "u_t1", "u_t2", "outcome", "r_mult",
                 "best_r", "worst_r", "conf", "source"]].rename(columns={
        "symbol": "Stock", "action": "Recommendation", "prem": "Entry ₹", "u_entry": "Stock entry", "u_sl": "Stock stop",
        "u_t1": "Stock T1", "u_t2": "Stock T2", "outcome": "Result", "r_mult": "R", "best_r": "Best R", "worst_r": "Worst R",
        "conf": "Conf", "source": "From"})
    st.dataframe(show.style.map(color, subset=["Result"]), hide_index=True, width="stretch",
                 height=min(80 + 35 * len(show), 900),
                 column_config={c: st.column_config.NumberColumn(format="%.2f") for c in
                                ("Entry ₹", "Stock entry", "Stock stop", "Stock T1", "Stock T2", "R", "Best R", "Worst R")})
    st.download_button("⬇ Download track record (CSV)", df.to_csv(index=False), "track_record.csv", "text/csv")
    st.caption("Judged on 5-min candles of the underlying, same session only; if stop and target touch in one candle the "
               "stop counts first (conservative). After T1 the stop moves to cost, so T1 stays a success. R = move in "
               "multiples of the planned risk (entry to stop). Logged ideas are not trades: see Paper trading for P&L.")


def _quote_key(sym):
    try:
        u = upstox_feed.underlying(sym, resolve(sym)["kind"], kite_day())
        return u["key"] if u else None
    except Exception:
        return None


INDEX_STRIP = [("NIFTY 50", "NIFTY", "^NSEI"), ("SENSEX", "SENSEX", "^BSESN"), ("BANK NIFTY", "BANKNIFTY", "^NSEBANK"),
               ("FIN NIFTY", "FINNIFTY", "NIFTY_FIN_SERVICE.NS"), ("INDIA VIX", None, "^INDIAVIX")]


def _pc(ltp, ch):
    prev = (ltp or 0) - (ch or 0)
    return (ch / prev * 100) if prev else None


def home_tab():
    now = pd.Timestamp.now(tz=IST)
    res = st.session_state.get("last_results")
    if not res:
        res, _, _ = run_scan(now)
    wl = wl_list()
    tok = E.UPSTOX
    qkeys = {s: _quote_key(s) for s in wl} if tok else {}
    ikeys = {n: (upstox_feed.INDEX_KEYS[k] if k else upstox_feed.VIX_KEY) for n, k, _ in INDEX_STRIP}
    q = upstox_feed.quotes(tok, tuple(dict.fromkeys(list(ikeys.values()) + [k for k in qkeys.values() if k]))) if tok else {}
    items = []
    for name, _, yt in INDEX_STRIP:
        v = q.get(ikeys[name])
        if v:
            items.append((name, f"{v['last_price']:,.2f}", v.get("net_change"), _pc(v["last_price"], v.get("net_change"))))
        else:
            lc, chg = last_close(yt)
            items.append((name, f"{lc:,.2f}" if lc else "n/a", (lc * chg / (1 + chg)) if lc and chg is not None else None,
                          chg * 100 if chg is not None else None))
    st.html(ui_kit.kite_strip(items))

    left, right = st.columns([4, 8], gap="medium")
    with left:
        st.markdown(f"**Watchlist** <small style='color:#8ea0bf'>({len(wl)})</small>", unsafe_allow_html=True)
        st.selectbox("Search & add", [x for x in symbols.labels() if symbols.to_symbol(x) not in wl], index=None,
                     key="home_search", on_change=wl_add_from, args=("home_search",), accept_new_options=True,
                     placeholder="🔍 Search eg: infy, nifty, tata…", label_visibility="collapsed")
        rows = []
        for s in wl:
            a = res.get(s)
            v = q.get(qkeys.get(s)) if tok else None
            if v:
                ltp, ch = v["last_price"], v.get("net_change")
            elif a and "error" not in a:
                ltp, ch = a["ltp"], (a["ltp"] - a["row"].PDC) if pd.notna(a["row"].PDC) else None
            else:
                ltp, ch = None, None
            sig = "·"
            if a and "error" not in a:
                r = recommend(a, P)
                sig = ("▲ CALL" if r["kind"] == "CE" else "▼ PUT") + ("" if r["mode"] == "now" else " ⏳") if r["mode"] != "none" else "·"
            rows.append({"Instrument": s, "LTP": ltp, "Chg": ch, "Chg %": _pc(ltp, ch) if ltp and ch is not None else None,
                         "Signal": sig})
        if not rows:
            st.info("Search above to add stocks.")
            return
        wdf = pd.DataFrame(rows)

        def col(v):
            try:
                return "color:#4caf50" if float(v) > 0 else "color:#ef5350" if float(v) < 0 else ""
            except (TypeError, ValueError):
                s = str(v)
                return ("color:#4caf50;font-weight:700" if "CALL" in s else "color:#ef5350;font-weight:700" if "PUT" in s
                        else "color:#64748b")
        ev = st.dataframe(wdf.style.map(col, subset=["Chg", "Chg %", "Signal"]), hide_index=True, width="stretch",
                          on_select="rerun", selection_mode="single-row", key="home_wl",
                          height=min(40 + 35 * len(wdf), 760),
                          column_config={"LTP": st.column_config.NumberColumn(format="%.2f"),
                                         "Chg": st.column_config.NumberColumn(format="%+.2f"),
                                         "Chg %": st.column_config.NumberColumn(format="%+.2f%%")})
        picked = ev.selection.rows if ev and ev.selection else []
        if picked:
            st.session_state["home_sel"] = wdf.Instrument.iloc[picked[0]]
        sel = st.session_state.get("home_sel")
        if sel not in wl:
            sel = wl[0]
        st.caption("Tap a row to open it · ▲ CALL / ▼ PUT = signal, ⏳ = waiting for trigger")
        if st.button(f"✕ Remove {sel}", key="home_del", use_container_width=True):
            wl_delete(sel)
            st.rerun()

    with right:
        a = res.get(sel)
        if a is None or "error" in a:
            with st.spinner(f"Loading {sel}…"):
                a = _get_analysis(sel)
        if "error" in a:
            st.error(f"{sel}: {a['error']}")
            return
        r = recommend(a, P)
        v = q.get(qkeys.get(sel)) if tok else None
        ltp, ch = (v["last_price"], v.get("net_change")) if v else (a["ltp"], (a["ltp"] - a["row"].PDC)
                                                                      if pd.notna(a["row"].PDC) else None)
        exch = {"index": "INDEX", "commodity": "MCX", "stock": "NSE"}[a["spec"]["kind"]]

        def side(x, kind, conditional):
            if not x:
                return None
            word = "BUY CALL" if kind == "CE" else "BUY PUT"
            when = (f"if {'above' if kind == 'CE' else 'below'} {x['u_entry']:,.2f} · " if conditional else "")
            return (f"{word} · {x['strike']:g} {kind} @ {x['prem']:.2f}",
                    f"{when}SL {x['sl']:.2f} · T1 {x['t1']:.2f} · T2 {x['t2']:.2f}"
                    + (f" · Upstox POP {x['pop']:.0f}%" if x.get("pop") is not None else ""))
        if r["mode"] == "none":
            up_, dn_ = r["alt"]
            buy, sell = side(up_, "CE", True), side(dn_, "PE", True)
        else:
            buy = side(r, "CE", r["mode"] == "trigger") if r["kind"] == "CE" else None
            sell = side(r, "PE", r["mode"] == "trigger") if r["kind"] == "PE" else None
        st.html(ui_kit.quote_header(sel, exch, ltp, ch, _pc(ltp, ch) if ch is not None else None, buy, sell))
        status = (f"**{r['action']}** · confidence {r['conf']}" if r["mode"] != "none" else
                  f"**No trade** · score {r['score']:+.1f}: conditional plans shown on the buttons")
        st.caption(status + " · display only: place orders in your broker app")
        for n in r.get("notes", [])[:2]:
            st.caption("⚠ " + n)
        t_chart, t_depth, t_chain, t_reco = st.tabs(["Chart", "Market depth", "Option chain", "Recommendation"])
        with t_chart:
            st.plotly_chart(make_chart(a["d"], sel, a["spec"], a["has_vol"]), width="stretch", key="home_chart")
        with t_depth:
            if v:
                st.html(ui_kit.depth_html(v))
                if not (v.get("depth") or {}).get("buy") or not any(x.get("price") for x in v["depth"]["buy"]):
                    st.caption("Depth is empty outside market hours or for indices.")
            else:
                st.info("Market depth needs the Upstox data source (sidebar).")
        with t_chain:
            if a.get("chain"):
                st.dataframe(oi_table(a["chain"], ltp), hide_index=True, width="stretch")
                st.caption(f"{a['chain']['source']} · expiry {a['chain']['expiry']} · PCR {a['chain']['pcr'] or 0:.2f} · "
                           f"max pain {a['chain']['max_pain']:g}")
            else:
                st.info("No option chain for this symbol.")
        with t_reco:
            st.html(ui_kit.reco_card(r, _chg(a), ui_kit.sparkline(_spark(a))))

    st.markdown("#### Positions")
    tr = paper.trades_df()
    opn = tr[tr.status == "OPEN"]
    if opn.empty:
        st.caption("No open paper positions. Paper trades open automatically when a BUY CALL / BUY PUT signal fires.")
    else:
        pnl = np.where(opn.half == 1, (opn.half_p - opn.entry_p) * opn.qty / 2 + (opn.last_p - opn.entry_p) * opn.qty / 2,
                       (opn.last_p - opn.entry_p) * opn.qty)
        pos = pd.DataFrame({"Instrument": opn.symbol + " " + opn.strike.map(lambda x: f"{x:g}") + " " + opn.kind,
                            "Qty.": opn.qty.where(opn.half == 0, opn.qty / 2).astype(int), "Avg.": opn.entry_p,
                            "LTP": opn.last_p, "P&L": pnl, "Chg %": (opn.last_p / opn.entry_p - 1) * 100,
                            "SL": opn.sl_p, "T1": opn.t1_p, "T2": opn.t2_p})
        tot = float(pnl.sum())
        st.dataframe(pos.style.map(lambda x: "color:#4caf50" if x > 0 else "color:#ef5350" if x < 0 else "",
                                   subset=["P&L", "Chg %"]), hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.2f") for c in
                                    ("Avg.", "LTP", "P&L", "SL", "T1", "T2")} | {
                         "Chg %": st.column_config.NumberColumn(format="%+.2f%%")})
        st.markdown(f"Total P&L <span style='color:{'#4caf50' if tot >= 0 else '#ef5350'};font-weight:700'>"
                    f"₹{tot:,.0f}</span> <small style='color:#8ea0bf'>(paper)</small>", unsafe_allow_html=True)


TABS = ["🏠 Home", "📊 Signals board", "⭐ Watchlist", "📈 NIFTY 50", "✅ Track record", "⚡ Stock → CALL/PUT", "🔎 Analysis", "🧠 Options analytics", "🌐 Market intel",
        "💰 CE/PE price check", "🧮 Spread builder", "📒 Paper trading & alerts", "🚀 Momentum scanner",
        "🎯 Position manager", "📖 Method"] + (["👥 Users"] if user["role"] == "admin" else [])
tabs = st.tabs(TABS)
with tabs[0]:
    st.fragment(home_tab, run_every=every if auto else None)()
with tabs[1]:
    st.fragment(dashboard, run_every=every if auto else None)()
with tabs[2]:
    watchlist_tab()
with tabs[3]:
    nifty50_tab()
with tabs[4]:
    track_tab()
with tabs[5]:
    quick_tab()
with tabs[6]:
    analysis_tab()
with tabs[7]:
    options_tab()
with tabs[8]:
    market_tab()
with tabs[9]:
    price_check()
with tabs[10]:
    spread_tab()
with tabs[11]:
    paper_tab(SYMBOLS)
with tabs[12]:
    scanner()
with tabs[13]:
    position_manager()
if user["role"] == "admin":
    with tabs[15]:
        users_tab()

with tabs[14]:
    st.markdown("""
**Six parameter groups** feed the confluence score, each shown with its own sub-score:
1. **Moving averages** (trend direction): EMA9/21/50, VWAP, Supertrend, ADX/DMI, 15- and 60-min trend
2. **RSI** (momentum strength): RSI(14) range and slope, CCI
3. **MACD** (momentum change): histogram direction, signal and zero-line position
4. **Volume** (participation): relative-volume spikes, OBV, MFI
5. **Bollinger Bands & ATR** (volatility): band expansion, ATR expansion, squeeze; ATR also sizes every stop
6. **Support/resistance & structure**: opening range, previous-day high/low, CPR, pivots, swing highs/lows, HH/HL, candle patterns, room to the next level

**Pipeline.** A trade needs all of: (1) a named **setup** firing within the last *N* closed bars, (2) **confluence** votes agreeing with it
(threshold +1 in a Mixed regime), (3) the **15-min trend** not against it, (4) the **entry window**, and (5) price not more than 2.5 ATR from EMA21.
Trend setups are disabled in a **Range** regime (ADX < 20 and Choppiness > 61.8); only VWAP-band reversion runs there.

| Component | Rule | Reference |
|---|---|---|
| Opening-range breakout | close crosses the 15-min OR high/low, RVOL > 1.2, before the ORB cut-off | T. Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990) |
| VWAP pullback | trend aligned, price tags VWAP and closes back through the prior bar's extreme | Berkowitz, Logue & Noser, *J. Finance* (1988); B. Shannon, *Maximum Trading Gains with Anchored VWAP* (2023) |
| Supertrend flip | ATR(10)×3 trailing band flips with EMA9/21 aligned | J. W. Wilder, *New Concepts in Technical Trading Systems* (1978) |
| PDH/PDL + CPR breakout | close through prior-day extreme and Central Pivot Range | F. Ochoa, *Secrets of a Pivot Boss* (2010) |
| Squeeze release | Bollinger Bands exit the Keltner Channel, MACD histogram gives the side | J. Carter, *Mastering the Trade* (2nd ed. 2012); J. Bollinger, *Bollinger on Bollinger Bands* (2001) |
| VWAP ±2σ reversion | close back inside the band after RSI(2) < 10 / > 90, range regime only | L. Connors & C. Alvarez, *Short Term Trading Strategies That Work* (2008) |
| Regime | ADX/DMI and Choppiness Index | Wilder (1978); E. W. Dreiss (Choppiness Index) |
| Multi-timeframe filter | 15-min and 60-min close vs EMA21 from completed bars | A. Elder, *Trading for a Living* (1993), Triple Screen |
| Momentum votes | MACD, RSI range shift (55/45), CCI ±100 | G. Appel (MACD); A. Cardwell (RSI ranges); D. Lambert (CCI, 1980) |
| Volume | relative volume vs same time of day, OBV | J. Granville (OBV, 1963) |
| Option pricing | Black-Scholes; Black-76 (r = 0) for MCX options on futures; calibrated to the live NSE quote | Black & Scholes (1973); Black (1976) |
| Price targets | confluence of floor pivots R1–R3/S1–S3, Camarilla H3/H4/L3/L4, PDH/PDL, OR ± 1×range, swings, OI walls, IV-implied 1σ/2σ move | T. Person, *A Complete Guide to Technical Trading Tactics* (2004); N. Stott (Camarilla, 1989) |
| Touch probability | reflection principle for a driftless random walk: P = 2·(1 − N(\\|ln(L/S)\\| / σ√t)) | J. C. Hull, *Options, Futures, and Other Derivatives* |
| Risk & sizing | fixed-fractional risk in R multiples, 50% at 1R + breakeven, trail | V. K. Tharp, *Trade Your Way to Financial Freedom* (1998) |

**Known limits.** Yahoo intraday data can be delayed and NSE indices carry no volume. The NSE option-chain API is unofficial. MCX prices are
NYMEX × USDINR proxies, not MCX quotes. The backtest measures the underlying in R, not option P&L.
""")
st.caption("Rules: risk ≤1% of capital per trade, always use the stop, avoid result days and expiry-day lottery tickets, square off "
           "before 15:15 IST (23:00 MCX). Confirm every strike and premium on your broker's chain before trading.")
