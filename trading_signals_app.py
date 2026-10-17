"""
Intraday CALL / PUT Signal Engine: Streamlit UI.
Run:  run.bat   (or: .venv\\Scripts\\python -m streamlit run trading_signals_app.py)
Analysis lives in engine.py; paper trading & alerts in paper.py; background runner in runner.py.
Educational tool only. NOT financial advice.
"""
import os
import re
import sys
import time
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Intraday CALL/PUT Engine", layout="wide")
_HERE = Path(__file__).resolve().parent


def _local_modules():
    return {n: m for n, m in list(sys.modules.items())
            if getattr(m, "__file__", None) and Path(m.__file__).resolve().parent == _HERE}


# Streamlit Cloud pulls new code into the running process and reruns only this script: helper modules (ui_kit,
# engine, ...) imported earlier would stay the old version (AttributeError on anything new). If any of their files
# changed since import, drop them all so the imports below load the new code together.
def _stale(m):
    try:
        changed = os.path.getmtime(m.__file__)
        if hasattr(m, "_loaded_at"):
            return changed > m._loaded_at
        pyc = getattr(getattr(m, "__spec__", None), "cached", None)  # loaded by older code: compare with its .pyc
        return bool(pyc) and os.path.exists(pyc) and changed > os.path.getmtime(pyc)
    except OSError:
        return False


if any(_stale(m) for m in _local_modules().values()):
    for _n in _local_modules():
        sys.modules.pop(_n, None)

import engine as E  # noqa: E402
import auth  # noqa: E402
import paper  # noqa: E402
import pro  # noqa: E402
import theme  # noqa: E402
import ui_kit  # noqa: E402
import symbols  # noqa: E402
import upstox_feed  # noqa: E402

for _m in _local_modules().values():
    _m.__dict__.setdefault("_loaded_at", time.time())

DEFAULT_WATCHLIST = "NIFTY, BANKNIFTY, FINNIFTY, RELIANCE, HDFCBANK, ICICIBANK, SBIN, INFY, TCS, LT, CRUDE, NATURALGAS"


def _conservative():
    st.session_state.update(risk_pct=0.25, daily_loss_pct=0.75, max_open=2, max_trades_day=2, max_consec_loss=1)


def wl_list():
    return list(dict.fromkeys(s.strip().upper() for s in st.session_state.get("wl_text", "").split(",") if s.strip()))


def save_watchlist():
    """Save the active list: the legacy watchlists row (SYMBOLS at login) and its numbered slot (sidebar, ⭐, N50, Home)."""
    u = st.session_state.get("user")
    if u:
        st.session_state["wl_text"] = ", ".join(auth.set_watchlist(u["username"], wl_list()))
        auth.set_watchlist_slot(u["username"], st.session_state.get("wl_slot", 1), wl_list())


def wl_switch_slot():
    """Home 'List 1-5' callback: park the current list in its slot, load the new slot into wl_text (the sidebar widget
    key, so callback only; empty slot 1 = DEFAULT_WATCHLIST), save it as the active list and ask home_tab for one
    full rerun (sidebar, SYMBOLS and the scan follow the new list)."""
    ss, u = st.session_state, st.session_state.get("user")
    old, new = ss.get("wl_slot", 1), ss.get("home_wl_slot")
    if new is None or new == old or not u:  # required control: None only if deselected somehow; keep the old slot
        return
    auth.set_watchlist_slot(u["username"], old, wl_list())
    syms = (auth.get_watchlist_sets(u["username"]).get(new) or {}).get("symbols") or []
    ss["wl_slot"] = new  # before save_watchlist, which writes the active slot
    ss["wl_text"] = ", ".join(syms) if syms or new != 1 else DEFAULT_WATCHLIST
    save_watchlist()
    for k in ("last_results", "home_sel", "home_peek"):
        ss.pop(k, None)
    ss["wl_slot_note"] = ss["home_full_rerun"] = True


def _wl_rename():
    """Home '✎' popover Save: name the active slot ('' resets it to 'Watchlist n')."""
    u = st.session_state.get("user")
    if u:
        auth.set_watchlist_slot(u["username"], st.session_state.get("wl_slot", 1), wl_list(),
                                st.session_state.get("wl_slot_name") or "")


def wl_add_sym(sym):
    """'+ Add {sym} to watchlist' for a symbol opened from Movers (writes wl_text: callback only)."""
    ss = st.session_state
    if sym in wl_list():
        ss.pop("home_peek", None)
    elif len(wl_list()) >= 40:
        ss["wl_flash"] = "Watchlist is full (40): remove one first"
        return
    else:
        ss["wl_text"] = ", ".join(wl_list() + [sym])
        save_watchlist()
        ss["wl_flash"] = f"Added {sym}"
        ss.pop("home_peek", None)
    ss["home_sel"] = sym
    ss["home_full_rerun"] = True


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
    wl_add_many("wl_quick")


def wl_add_many(key):
    """Multiselect + button callback (wl_text is a sidebar widget key: only write it from callbacks)."""
    picks = st.session_state.get(key) or []
    if picks:
        st.session_state["wl_text"] = ", ".join(dict.fromkeys(wl_list() + list(picks)))
        save_watchlist()
        st.session_state["wl_flash"] = "Added " + ", ".join(picks)
    st.session_state[key] = []


def wl_delete(sym):
    st.session_state["wl_text"] = ", ".join(s for s in wl_list() if s != sym)
    save_watchlist()
    st.session_state["wl_flash"] = f"Removed {sym}"


def _home_remove(sym):
    """Home '✕ Remove' callback: drop the symbol, then ask home_tab for one full rerun (sidebar + scan list)."""
    wl_delete(sym)
    st.session_state.pop("home_sel", None)
    st.session_state["home_full_rerun"] = True


def _wl_pick():
    """Home watchlist on_select callback: open the clicked row (display order kept in home_wl_order)."""
    ev = st.session_state.get(st.session_state.get("home_wl_key", "home_wl")) or {}
    rows = (ev.get("selection") or {}).get("rows") or []
    order = st.session_state.get("home_wl_order") or []
    if rows and rows[0] < len(order):
        st.session_state["home_sel"] = order[rows[0]]
        st.session_state["home_peek"] = False  # back to normal watchlist behaviour


def _mv_pick():
    """Home Movers on_select callback: open the clicked NIFTY 50 stock; one outside the watchlist is 'peeked'."""
    ev = st.session_state.get(st.session_state.get("home_mv_key", "home_mv")) or {}
    rows = (ev.get("selection") or {}).get("rows") or []
    order = st.session_state.get("home_mv_order") or []
    if rows and rows[0] < len(order):
        st.session_state["home_sel"] = order[rows[0]]
        st.session_state["home_peek"] = order[rows[0]] not in wl_list()


def _paper_exit(tid):
    """Home Positions 'Exit (paper)' callback (admins only: the journal is shared). Closes one paper trade at its
    current premium via paper.close_trade; no broker order is ever placed. The toast shows on the next run."""
    if (st.session_state.get("user") or {}).get("role") != "admin":
        return
    r = paper.close_trade(tid, st.session_state.get("last_results") or {}, pd.Timestamp.now(tz=E.IST))
    if r:  # toasted via pos_flash, not the alert diff; a close by someone else keeps its own (stop/target) toast
        st.session_state.setdefault("notif_own", set()).add(f"exit:{int(tid)}")
    st.session_state["pos_flash"] = f"Paper exit · {r['text']}" if r else "That paper trade was already closed."


def _notif_read():
    """'Mark all read' callback: everything logged so far counts as seen."""
    st.session_state["notif_seen_id"] = paper.last_alert_id()


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
theme.install()  # colour scheme (Upstox purple by default): recolours HTML, CSS and charts per session
theme.apply_native()
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
    st.selectbox("🎨 Colour scheme", list(theme.SCHEMES), format_func=theme.SCHEMES.get, key="color_scheme")
    if "simple_view" not in st.session_state:
        ua = str(st.context.headers.get("User-Agent", "")) if hasattr(st, "context") else ""
        st.session_state["simple_view"] = bool(re.search(r"Android|iPhone|iPad|Mobile", ua))
    st.toggle("📱 Simple view", key="simple_view",
              help="One screen for the phone: best calls, live signals, positions and warnings. On by default on phones.")
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
            if st.session_state.pop("upstox_token_save", None):
                upstox_feed.remember_token_in_browser(st.session_state.get("upstox_token"))
            if _tok:
                E.UPSTOX = _tok
                st.success(f"🟢 Upstox: {up_status}")
                st.caption("Live candles · option chain with IV, greeks and real ΔOI")
                if st.button("Log out of Upstox"):
                    upstox_feed.logout()
                    st.rerun()
            else:
                st.warning(f"Upstox: {up_status}. Using Yahoo + NSE meanwhile.")
                if not upstox_feed.analytics_token():  # no token on this server (e.g. Streamlit Cloud without secrets)
                    with st.form("upstox_token_form", clear_on_submit=True):
                        pasted = st.text_input("Paste your Upstox analytics token", type="password",
                                               help="From the Upstox developer console (Analytics token). Saved only "
                                                    "in this browser for a year, not on the server or in GitHub.")
                        if st.form_submit_button("Use this token", type="primary", use_container_width=True):
                            pasted = (pasted or "").strip()
                            if upstox_feed._looks_like_token(pasted) and upstox_feed._check(pasted):
                                st.session_state["upstox_token"] = pasted
                                st.session_state["upstox_token_save"] = pasted
                                st.rerun()
                            st.error("Upstox rejected that token, or it isn't an analytics token. Copy it again.")
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
        if "wl_slot" not in st.session_state:  # numbered lists 1-5: active = the slot holding the saved list, else 1
            _sets = auth.get_watchlist_sets(user["username"])
            st.session_state["wl_slot"] = next((k for k, v in sorted(_sets.items()) if v["symbols"]
                                                and v["symbols"] == wl_list()), 1)
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
        win_end = c2.time_input("To", dtime(14, 45), step=300,
                                help="New entries only until this time. On REAL option prices (41 symbols, 120 days) "
                                     "entries until 14:45 did better than ICICI's 11:00 rule in both halves of the test "
                                     "(-4.3% vs -4.9% and +1.6% vs +1.1% of premium per trade), with ~65% more trades.")
        orb_cut = st.time_input("ORB valid until", dtime(13, 0), step=900)
        ctx_filter = st.multiselect("Confirmation filters", list(E.CTX_FILTERS), list(E.CTX_DEFAULT),
                                    format_func=E.CTX_FILTERS.get,
                                    help="A CALL/PUT must agree with these (NIFTY itself and MCX skip them). In a 30-stock, "
                                         "60-day back-test all three together cut the loss per trade from -0.065R to "
                                         "-0.004R and lifted T1 hits 22% → 26%; the MACD check helped in both halves of a 120-day walk-forward test.")
    with st.expander("🛡 Risk & options"):
        capital = st.number_input("Capital (₹)", 10000, 100000000, 200000, 10000)
        st.button("🛡 Conservative preset (0.25% · 0.75% · 2)", on_click=_conservative, use_container_width=True,
                  help="Risk 0.25% per trade, stop for the day at -0.75%, at most 2 open positions")
        risk_pct = st.slider("Risk per trade (% of capital)", 0.25, 3.0, 1.0, 0.25, key="risk_pct")
        strike_mode = st.selectbox("Strike selection", ["Delta target", "ATM", "1 step ITM", "2 steps ITM", "1 step OTM"])
        target_delta = st.slider("Target |delta|", 0.35, 0.80, 0.55, 0.05)
        exit_mode = st.selectbox("Exit after T1", ["2R target + flip", "Run to close"])
        rr = st.slider("Final target (R multiple)", 1.5, 4.0, 2.0, 0.5)
        proof_gate = st.toggle("Size only proven calls (TRADE / PAPER)", True, key="proof_gate",
                               help="TRADE = the symbol's past signals made money on REAL Upstox option prices (12+ trades) "
                                    "and its last 5 live calls are not losing. Everything else is PAPER: shown and "
                                    "tracked with 0 lots until it earns a record.")
        allowlist = st.toggle("Also hide PAPER calls from phone alerts", False,
                              help="Off: PAPER calls still alert, marked 📝 PAPER, so you can watch them build a record.")
        t1_r = st.select_slider("Book half at (R multiple)", [0.5, 0.75, 1.0], E.T1_R,
                                help="T1: book 50% and move the stop to breakeven. 0.5R raised the win rate from ~47% to "
                                     "~60% in an option-priced 120-day test with similar profit per trade; 1.0R wins less "
                                     "often but lets more of each winner run.")
        stop_min = st.slider("Minimum stop (× ATR)", 0.5, 2.0, 0.75, 0.25)
        atr_mult = st.slider("Fallback stop (× ATR)", 0.75, 2.5, 1.5, 0.25)
        max_day = st.slider("Max trades per symbol per day", 1, 5, 2)
        st.markdown("**Portfolio risk guard**")
        daily_loss_pct = st.slider("Daily loss limit (% of capital)", 0.5, 10.0, 2.0, 0.25, key="daily_loss_pct",
                                   help="No new trades for the day once hit")
        max_open = st.slider("Max open positions", 1, 10, 3, key="max_open")
        max_trades_day = st.slider("Max new trades per day", 1, 10, 3, key="max_trades_day")
        max_consec_loss = st.slider("Stop after losses in a row (today)", 1, 5, 2, key="max_consec_loss")
        weekly_loss_pct = st.slider("Weekly loss that halves size (% of capital)", 1.0, 15.0, 5.0, 0.5,
                                    key="weekly_loss_pct")
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
        alerts_on = st.toggle("Phone alerts (Telegram / ntfy)", True,
                              help="New BUY CALL / BUY PUT signals and paper-trade events are pushed to your phone. "
                                   "Set up: [ntfy] topic (ntfy app) and/or [telegram] in secrets.toml.")
        alert_grade = st.select_slider("Alert on CALL/PUT graded", ["A", "B", "C", "D"], value="C",
                                       format_func=lambda g: {"A": "A only", "B": "B or better", "C": "C or better",
                                                              "D": "every signal"}[g],
                                       help="Credibility grade a signal needs before it is pushed to your phone")
        ch = [n for n, ok in (("Telegram", all(paper.telegram_creds())), ("ntfy", paper.ntfy_target()[1])) if ok]
        st.caption("📲 Alerts go to: " + (", ".join(ch) if ch else "nowhere yet: add [ntfy] topic or [telegram] to secrets"))
    st.markdown("**🔄 Refresh**")
    c1, c2 = st.columns(2)
    auto = c1.toggle("Auto", True, help="On by default: signals, prices and paper trades refresh by themselves "
                                        "(every minute unless you pick a longer interval)")
    every = c2.selectbox("Every", [60, 120, 300], index=0, format_func=lambda s: f"{s // 60} min",
                         label_visibility="collapsed")

P = dict(interval=interval, period=period, th=th, adx_min=adx_min, lookback=lookback, use_time=use_time,
         win_start=win_start, win_end=win_end, orb_cut=tmin(orb_cut), capital=capital, risk_pct=risk_pct,
         strike_mode=strike_mode, delta=target_delta, rr=rr, atr_mult=atr_mult, max_loss=max_loss, hold=hold,
         dte=dte, iv=iv_over, step=step_over, lot=lot_over, cost_pct=cost_pct, chain=use_chain, news=use_news,
         setups=set(setups_on), exit_mode=exit_mode, stop_min=stop_min, max_day=max_day, ctx_filter=tuple(ctx_filter), alert_grade=alert_grade, t1_r=t1_r, allowlist=allowlist,
         daily_loss_pct=daily_loss_pct, max_open=max_open, corr_guard=corr_guard, proof_gate=proof_gate,
         max_trades_day=max_trades_day, max_consec_loss=max_consec_loss, weekly_loss_pct=weekly_loss_pct)
P.update(paper.risk_controls(P, pd.Timestamp.now(tz=IST)))
SYMBOLS = list(dict.fromkeys(s.strip().upper() for s in names.split(",") if s.strip()))

COLORS = {"BUY CALL (CE)": "background-color:#1e8449;color:white", "BUY PUT (PE)": "background-color:#b03a2e;color:white",
          "WATCH: CALL bias": "background-color:#d5f5e3;color:#145a32", "WATCH: PUT bias": "background-color:#fadbd8;color:#78281f",
          "NO TRADE": "background-color:#7f8c8d;color:white"}
BADGE = {"BUY CALL (CE)": ("BUY CALL ▲", "green"), "BUY PUT (PE)": ("BUY PUT ▼", "red"),
         "WATCH: CALL bias": ("WATCH · CALL bias", "orange"), "WATCH: PUT bias": ("WATCH · PUT bias", "violet"),
         "NO TRADE": ("NO TRADE", "gray")}


ALERT_ICON = {"ENTRY": "📥", "T1": "🟡", "EXIT": "📤", "BLOCKED": "⛔", "SIGNAL": "📣", "NEWS": "📰"}


def pct(a, b):
    return f"{(a / b - 1) * 100:+.0f}%" if b else None


def run_scan(now):
    vix, vix_chg = last_close("^INDIAVIX")
    fx, _ = last_close("USDINR=X")
    with st.spinner(f"Scanning {len(SYMBOLS)} symbols…"):
        results = E.analyze_many(SYMBOLS, P, vix, fx, now)
    st.session_state["last_results"] = results
    try:
        recs = [(s, recommend(a, P)) for s, a in results.items() if "error" not in a]
        paper.log_recs(recs, now, "watchlist")
        paper.log_recs([(s, E.blocked_rec(a, P)) for s, a in results.items() if "error" not in a], now, "blocked")
        paper.save_chain_snaps(results, now)  # option-chain history (IV rank / OI build-up research), every 15 min
        if alerts_on:  # phone push (Telegram / ntfy) once per symbol/side/day for each CALL/PUT at the chosen grade
            pushed = paper.push_signals(recs, now, alert_grade)
            pushed += paper.push_horizons(SYMBOLS, now, alert_grade, fx)  # BTST / 2-day, pre-close window only
            pushed += paper.push_news(SYMBOLS, now)  # NSE filings: order wins, buybacks, results… (context)
            for key, sym, kind, text in pushed:
                st.toast(text.split("\n")[0], icon=ALERT_ICON.get(kind, "📣"))
                st.session_state.setdefault("notif_own", set()).add(key)
                if key.startswith("sig:"):  # intraday CALL/PUT only (not BTST / 2-day): Home skips its flip toast
                    st.session_state.setdefault("paper_syms", set()).add(sym)
    except Exception:
        pass
    st.session_state["scan_time"] = now
    st.session_state["vix"] = (vix, vix_chg, fx)
    if paper_on:
        before = paper.last_alert_id()
        new = paper.notify(paper.paper_step(results, P, now), send=alerts_on)
        for key, _, kind, text in new:
            st.toast(text, icon=ALERT_ICON.get(kind))
        if new:  # already toasted here: Home's alert diff must not toast them again
            st.session_state.setdefault("notif_own", set()).update(k for k, _, _, _ in new)
            st.session_state.setdefault("paper_syms", set()).update(s for _, s, k, _ in new if k in ("ENTRY", "BLOCKED"))
            if st.session_state.get("notif_last_id", before) >= before:  # no unseen rows from others in between
                st.session_state["notif_last_id"] = paper.last_alert_id()
    return results, vix, vix_chg


def header_strip(now, vix, vix_chg):
    n, n_chg = last_close("^NSEI")
    b, b_chg = last_close("^NSEBANK")
    nse_open = now.weekday() < 5 and dtime(9, 15) <= now.time() <= dtime(15, 30)
    tr = paper.trades_df()
    today = now.date().isoformat()
    closed_today = tr[(tr.status == "CLOSED") & tr.exit_at.fillna("").str.startswith(today)]
    opn = tr[tr.status == "OPEN"]
    unreal = float(paper.unrealized(opn).sum())
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


def _full_view():
    st.session_state["simple_view"] = False


def _horizon_calls(ok):
    """BTST / 2-day setups on the watchlist as card-ready dicts (premium targets estimated with the option's delta)."""
    import horizons as H
    fx = st.session_state.get("vix", (None, None, None))[2]
    out = []
    for s in wl_list():
        try:
            a = H.analyze(s, (fx or last_close("USDINR=X")[0]) if resolve(s)["kind"] == "commodity" else None)
        except Exception:
            continue
        if a.get("error"):
            continue
        a2 = ok.get(s)
        for kind, dur in (("BTST", "Overnight (BTST): exit next session"), ("SWING2D", "Up to 2 days"),
                          ("SWINGPB", "Up to 2 days (pullback)")):
            x = a["setups"][kind]
            if not x["dir"]:
                continue
            g, why = H.grade(x["rec"])
            k = "CE" if x["dir"] > 0 else "PE"
            r = dict(kind=k, mode="now", u_entry=a["close"], u_t1=x["t1"], u_t2=x["t1"], u_sl=x["sl"],
                     cred=dict(grade=g, p_t1=x["rec"]["hit"] if x["rec"]["n"] else 0), lots=1, lot_size=1,
                     invalid=f"{kind} record: {why}")
            if a2 and a2.get("sa"):
                sd = a2["sa"]["sides"][k]
                dl = abs(sd.get("delta") or 0.5)
                r.update(strike=sd["strike"], prem=sd["prem"], live=sd["live"], expiry=(a2.get("chain") or {}).get("expiry"),
                         t1=sd["prem"] + dl * abs(x["t1"] - a["close"]), sl=max(sd["prem"] - dl * abs(a["close"] - x["sl"]), 0.05),
                         lot_size=int((a2.get("chain") or {}).get("lot") or a2["spec"].get("lot") or 1))
                r["t2"] = r["t1"]
            out.append((s, r, dur))
    return out


def _exited_calls(days=7):
    df = paper.recs_df(days)
    done = df[df.status.isin(["T1 HIT", "T2 HIT", "STOP HIT", "SQUARED OFF", "NOT TRIGGERED"])]
    return done.sort_values("ts", ascending=False).to_dict("records")


def simple_view():
    """Phone-first screen styled like a trading app: Active / Exited calls as cards, Options or Futures legs."""
    now = pd.Timestamp.now(tz=IST)
    res = st.session_state.get("last_results")
    if not res:
        res, _, _ = run_scan(now)
    ok = {s: a for s, a in res.items() if "error" not in a}
    recs = {s: recommend(a, P) for s, a in ok.items()}
    px = []
    for lbl, tk in (("NIFTY", "^NSEI"), ("BANKNIFTY", "^NSEBANK"), ("VIX", "^INDIAVIX")):
        v, chg = last_close(tk)
        px.append((lbl, f"{v:,.2f}" if v else "n/a", chg * 100 if v and chg is not None else None))
    st.html(ui_kit.APP_CSS)
    st.html(ui_kit.hero("Options", f"SRK signals · {now:%a %d %b, %H:%M} IST", px))
    st.html(ui_kit.sheet_title("Today's & this week's calls"))
    try:
        st.html(ui_kit.risk_meter(paper.risk_status(P, now), P))
    except Exception:
        pass
    c1, c2 = st.columns([3, 2])
    tab = c1.segmented_control("Calls", ["Active calls", "Exited calls"], default="Active calls", key="app_tab",
                               label_visibility="collapsed") or "Active calls"
    view = c2.segmented_control("Instrument", ["Options", "Futures"], default="Options", key="app_view",
                                label_visibility="collapsed") or "Options"
    if tab == "Active calls":
        live = sorted([(s, r, "Intraday: square off by 15:15") for s, r in recs.items() if r.get("mode") in ("now", "trigger")],
                      key=lambda x: (not (x[1].get("proof") or {}).get("trade"),
                                     E.CRED_RANK.get((x[1].get("cred") or {}).get("grade", "D"), 3), x[1]["mode"] != "now"))
        try:
            live += _horizon_calls(ok)
        except Exception:
            pass
        if live:
            st.html('<div class="tk">' + "".join(ui_kit.call_card(s, r, view, d) for s, r, d in live) + '</div>')
        else:
            st.html('<div class="tk"><div class="empty2">No active CALL/PUT right now.<br>New calls appear here and '
                    'on your phone as alerts as soon as one forms.</div></div>')
        st.caption("✅ TRADE = the symbol's signals made money on real option prices and its recent live calls are not "
                   "losing. 📝 PAPER = no such record yet: watch it, 0 lots. Expected profit / stop loss are for the "
                   "suggested lots. BTST/2-day premium targets are estimated from the option's delta.")
    else:
        ex = _exited_calls()
        if ex:
            st.html('<div class="tk">' + "".join(ui_kit.exited_card(r) for r in ex[:40]) + '</div>')
        else:
            st.html('<div class="tk"><div class="empty2">No calls closed in the last 7 days yet.</div></div>')
    tr = paper.trades_df()
    opn = tr[tr.status == "OPEN"]
    st.html(ui_kit.section(f"Open paper positions · {len(opn)}"))
    if len(opn):
        pnl = paper.unrealized(opn) if hasattr(paper, "unrealized") else (opn.last_p - opn.entry_p) * opn.qty
        st.dataframe(pd.DataFrame({"Position": opn.symbol + " " + opn.strike.map(lambda x: f"{x:g}") + " " + opn.kind,
                                   "Avg": opn.entry_p, "LTP": opn.last_p, "P&L ₹": pnl, "SL": opn.sl_p, "T1": opn.t1_p}),
                     hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.2f") for c in ("Avg", "LTP", "P&L ₹", "SL", "T1")})
    else:
        st.caption("No open paper positions.")
    warn = [f"📅 {s}: {t}" for s, a in ok.items() for sev, t in a.get("events", []) if sev == "high"]
    try:
        import news
        an = news.announcements()
        if len(an):
            x = an[an.alert & an.symbol.isin(ok) & (an.ts >= now - pd.Timedelta(hours=12))]
            warn += [f"📰 {r.symbol}: {r.category} · {r.subject[:70]}" for r in x.itertuples()]
    except Exception:
        pass
    if warn:
        st.html(ui_kit.section("Warnings"))
        for w in warn[:8]:
            st.caption(w)
    st.button("🖥 Open the full dashboard", on_click=_full_view, use_container_width=True)
    st.caption(f"Updated {now:%H:%M} IST" + (f" · refreshes every {every // 60} min" if auto else "")
               + " · educational signals, not advice.")


def credible_panel(results, key, recs=None):
    """High-credibility CALL / PUT plans only (grade bar chosen by the user), with the evidence behind each."""
    h1, h2 = st.columns([5, 2], vertical_alignment="bottom")
    bar = h2.segmented_control("Credibility bar", ["A", "B", "C"], default="B", key=f"cred_bar_{key}",
                               help="A: strong evidence · B: fair evidence, no hard failure · C: weak (unproven back-test, "
                                    "illiquid contract or stale data allowed)") or "B"
    good, near = E.credible_ideas(results, P, bar, recs)
    with h1:
        st.html(ui_kit.section(f"High-credibility calls & puts · grade {bar} or better · {len(good)}"))
    st.html(ui_kit.credible_board(good, near, bar))
    st.caption("Graded on back-test edge, T1 hit record, live track record, contract liquidity, reachability, premium "
               "reward:risk, theta, IV vs realized, data freshness and events. T1 odds = back-test T1 hit rate shrunk toward "
               "the model touch probability. Credible ≠ certain: size small and use the stop. Not financial advice.")


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
    unreal = float(paper.unrealized(opn).sum())
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
    credible_panel(ok, "sb")
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
        rt, rs_ = a.get("rbt_trades"), a.get("rbt")
        if rs_:
            st.markdown("**Same signals on REAL option prices** (Upstox expired + live contracts)")
            k = st.columns(5)
            k[0].metric("Real trades", rs_["trades"])
            k[1].metric("Winners", f"{rs_['win'] * 100:.0f}%")
            k[2].metric("Avg per trade", f"{rs_['avg'] * 100:+.1f}% of premium")
            k[3].metric("Profit factor", f"{rs_['pf']:.2f}")
            k[4].metric("T1 reached", f"{rs_['t1'] * 100:.0f}%")
            st.dataframe(rt, width="stretch", hide_index=True,
                         column_config={"pnl_pct": st.column_config.NumberColumn("P&L % of premium", format="%+.2f")})
            st.caption("Each trade bought the delta-0.55 strike at its real 15-min open (the next expiry when the nearest "
                       "was 3-7 days away) and exited at the contract's real prices: stop at the bar low, targets and "
                       "square-off at the bar close (conservative), 1.5% round-trip costs.")
        ot, os_ = a.get("obt_trades"), a.get("obt")
        st.markdown("**Same signals priced with a model**" + (" (for comparison)" if rs_ else ""))
        if not os_:
            st.info("No option-priced trades (or only expiry-day trades) in this history.")
        else:
            k = st.columns(5)
            k[0].metric("Option trades", os_["trades"])
            k[1].metric("Winners", f"{os_['win'] * 100:.0f}%")
            k[2].metric("Avg per trade", f"{os_['avg'] * 100:+.1f}% of premium")
            k[3].metric("Profit factor", f"{os_['pf']:.2f}")
            k[4].metric("T1 reached", f"{os_['t1'] * 100:.0f}%")
            st.dataframe(ot, width="stretch", hide_index=True,
                         column_config={"iv": st.column_config.NumberColumn("IV", format="%.2f"),
                                        "pnl_pct": st.column_config.NumberColumn("P&L % of premium", format="%+.2f")})
            st.caption("Black-Scholes model: delta-0.55 strike, IV = 5-day realized vol × 1.3 held constant, next weekly/"
                       "monthly expiry, 1.5% round-trip cost. Trades under a day to expiry are listed but left out of the "
                       "statistics because the model overstates expiry-day gains. A model, not real option fills.")


def users_tab():
    st.markdown("Manage who can log in. Passwords are stored as salted PBKDF2-SHA256 hashes in `users.db`.")
    with st.expander("☁ Keep accounts and sign-ins across Streamlit Cloud restarts"):
        st.markdown("Streamlit Cloud wipes `users.db` whenever the app restarts (after sleeping, on every redeploy). "
                    "Paste this into the app's **Settings → Secrets** (keep your existing `[upstox]` / `[ntfy]` lines). "
                    "Accounts are then restored automatically and everyone stays signed in until they log out or change "
                    "their password. It contains salted password **hashes**, never passwords; keep it private.")
        try:
            sec = st.secrets["auth"]["secret"]
        except Exception:
            sec = None
        st.code("[auth]\nsecret = \"" + (sec or "paste-a-long-random-secret-here") + "\"\n\n"
                + auth.users_secrets_snippet(), language="toml")
        st.caption("After anyone changes a password on Streamlit Cloud, copy this block again; otherwise the next restart "
                   "brings back the old password. Locally, users.db persists, so nothing is needed.")
    st.dataframe(pd.DataFrame(auth.list_users()).drop(columns=["salt", "pw"], errors="ignore"), hide_index=True, width="stretch")
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
        o["Unrealized ₹"] = paper.unrealized(o).round(0)
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
        st.dataframe(al.drop(columns="id"), hide_index=True, width="stretch")
    else:
        st.caption("No alerts yet.")
    with st.expander("Danger zone"):
        if user["role"] != "admin":  # same gate as Home's paper exit: the journal is shared by every user
            st.caption("Paper journal is shared; only admins can close or reset paper trades.")
        else:
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


def horizons_tab():
    """BTST and 2-day swing calls from daily candles, each graded on its own 2-year record on that symbol."""
    import horizons as H
    syms = [s for s in wl_list() if resolve(s)["kind"] != "commodity"] + \
           [s for s in wl_list() if resolve(s)["kind"] == "commodity"]
    if not syms:
        st.info("Add symbols to your watchlist first.")
        return
    fx = st.session_state.get("vix", (None, None, None))[2] or last_close("USDINR=X")[0]
    st.caption("**BTST**: strong close (CLV ≥ 0.75) through yesterday's high, daily EMA20 > EMA50, volume ≥ 1.2× average, "
               "leading NIFTY; exit next session at +0.5 ATR or −0.75 ATR. **2-day swing**: daily EMA20 > EMA50, ADX > 20, "
               "close above the 10-day high; exit within 2 sessions at ±1 ATR. **Pullback**: daily EMA20 > EMA50, yesterday dipped to "
               "EMA20, today closes above yesterday's high with RSI 50–65; same exits. PUTs mirror. **2-day range** holds the next "
               "2 sessions' move ~80% of the time (calibrated per symbol; 79% on unseen data for RELIANCE).")
    with st.spinner("Loading daily history…"):
        res = {s: H.analyze(s, fx if resolve(s)["kind"] == "commodity" else None) for s in syms}
    rows = []
    for s, a in res.items():
        if a.get("error"):
            rows.append({"Symbol": s, "BTST": "⚠ " + a["error"]})
            continue
        row = {"Symbol": s, "Close": a["close"], "2-day low": a["range2d"][0], "2-day high": a["range2d"][1],
               "CLV": a["clv"], "ADX": a["adx"], "Vol ×": a["volx"], "RS vs NIFTY %": a["rs"]}
        for kind, lbl in (("BTST", "BTST"), ("SWING2D", "2-day"), ("SWINGPB", "Pullback")):
            x = a["setups"][kind]
            g, why = H.grade(x["rec"])
            if x["dir"]:
                row[lbl] = f"{'▲ BUY CALL' if x['dir'] > 0 else '▼ BUY PUT'} · grade {g}"
                row[f"{lbl} T1 / SL"] = f"{x['t1']:,.2f} / {x['sl']:,.2f}"
                row[f"{lbl} record"] = why
            else:
                row[lbl] = "–"
                rec = x["rec"]
                row[f"{lbl} record"] = (f"no setup today · {rec['n_all']} past, {rec['hit_all'] * 100:.0f}% hit T1"
                                        if rec["n_all"] else "no setup today")
        a2 = st.session_state.get("last_results", {}).get(s)
        if a2 and "error" not in a2 and any(a["setups"][k]["dir"] for k in a["setups"]):
            dr = next(a["setups"][k]["dir"] for k in ("BTST", "SWING2D", "SWINGPB") if a["setups"][k]["dir"])
            side = a2["sa"]["sides"]["CE" if dr > 0 else "PE"]
            row["Option idea"] = f"{side['strike']:g} {'CE' if dr > 0 else 'PE'} @ {side['prem']:.2f}" + \
                ("" if side["live"] else " (model)")
        rows.append(row)
    df = pd.DataFrame(rows)
    num = st.column_config.NumberColumn

    def color(v):
        v = str(v)
        return ("color:#4caf50;font-weight:700" if "CALL" in v else "color:#ef5350;font-weight:700" if "PUT" in v else "")
    cols = [c for c in ["Symbol", "Close", "BTST", "BTST T1 / SL", "BTST record", "2-day", "2-day T1 / SL", "2-day record",
                        "Pullback", "Pullback T1 / SL", "Pullback record", "Option idea", "2-day low", "2-day high", "CLV", "ADX", "Vol ×", "RS vs NIFTY %"] if c in df.columns]
    st.dataframe(df[cols].style.map(color, subset=[c for c in ("BTST", "2-day", "Pullback") if c in df.columns]), hide_index=True,
                 width="stretch", column_config={"Close": num(format="%.2f"), "2-day low": num(format="%.2f"),
                                                  "2-day high": num(format="%.2f"), "CLV": num(format="%.2f"),
                                                  "ADX": num(format="%.0f"), "Vol ×": num(format="%.2f"),
                                                  "RS vs NIFTY %": num(format="%+.2f")})
    with st.expander("How these setups did over 2 years (all your watchlist symbols pooled)"):
        rec = H.portfolio_record(tuple(syms), fx)
        for k, v in rec.items():
            if v["n"]:
                st.markdown(f"**{dict(BTST='BTST', SWING2D='2-day breakout swing', SWINGPB='2-day pullback swing')[k]}**: {v['n']} signals, T1 hit {v['hit'] * 100:.0f}%, "
                            f"average {v['avg_r']:+.2f}R per trade · "
                            + " · ".join(f"{s} {n} signals, {h * 100:.0f}% hit, {a_:+.2f}R" for s, (n, h, a_) in v["by_side"].items()))
        st.caption("Across 30 NSE stocks these rules averaged about −0.03R (BTST) and −0.06R (2-day) over 2 years, and MCX "
                   "about −0.2R: no proven edge overall, so each call is graded on its own symbol's record and only "
                   "grade A/B setups are worth acting on.")
    st.caption("BTST uses today's live daily candle: final after ~15:15 IST (MCX ~23:00). Overnight gaps can jump past a stop. "
               "MCX daily levels come from NYMEX × USD/INR and are approximate. Educational, not financial advice.")


@st.cache_data(ttl=300, show_spinner=False)
def _results_rows(items, src_key, _now):
    """Assess each upcoming result (history + implied move); cached 5 min per data source."""
    import results as R
    out = []
    for sym, rdate in items:
        h, imp = R.history(sym), R.implied(sym, rdate, _now)
        v, why = R.verdict(h, imp)
        R.log(sym, rdate, imp, h, v, _now)
        out.append((sym, rdate, h, imp, v, why))
    return out


def results_tab():
    """Upcoming results: is the ATM straddle cheap or rich versus the stock's own results-day history?"""
    import results as R
    now = pd.Timestamp.now(tz=IST)
    lst = pro.nifty50_list()
    uni = set(s.strip().upper() for s in E.FNO_STOCKS.split(",")) | set(wl_list()) | \
        (set(lst.Symbol) if lst is not None else set())
    st.caption("Before results, direction is a coin flip (the 5-day pre-results drift matched the reaction 45% of the time) "
               "and option premiums collapse after the announcement (IV crush). The one pre-results bet that suits an option "
               "buyer is a **long straddle (ATM CALL + PUT) when it is cheap**: its cost, the break-even move, is below the "
               "move this stock has usually made on results. **Rich** straddles usually lose to IV crush: avoid buying "
               "options into those results.")
    days = st.segmented_control("Window", [3, 7, 14], default=7, format_func=lambda d: f"next {d} days",
                                key="res_days") or 7
    up = R.upcoming(uni, now, days)
    if up.empty:
        st.info("No results for F&O / NIFTY 50 stocks in this window (or the NSE event calendar is unreachable).")
    else:
        items = tuple((r.symbol, r.date) for r in up.head(30).itertuples())
        src = "upstox" if E.UPSTOX else "nse"
        with st.spinner(f"Pricing {len(items)} results straddles…"):
            rows = _results_rows(items, src, now.floor("5min"))
        tag = {"cheap": "🟢 cheap: straddle candidate", "rich": "🔴 rich: avoid (IV crush)", "fair": "🟡 fair", "n/a": "–"}
        tbl = [{"Symbol": s, "Results": d, "In days": (d - now.date()).days, "Verdict": tag[v],
                "Break-even move %": imp["pct"] if imp else None, "Usual results move %": h.get("mean_pct"),
                "Median %": h.get("median_pct"), ">2 ATR %": h.get("big2atr", 0) * 100 if h.get("n") else None,
                "Past results": h.get("n", 0), "Straddle ₹": imp["straddle"] if imp else None,
                "Per lot ₹": imp["straddle"] * imp["lot"] if imp and imp.get("lot") else None,
                "Strike": imp["strike"] if imp else None, "Expiry": imp["expiry"] if imp else None, "Why": why}
               for s, d, h, imp, v, why in rows]
        df = pd.DataFrame(tbl)
        num = st.column_config.NumberColumn

        def col(v):
            v = str(v)
            return "color:#4caf50;font-weight:700" if "cheap" in v else "color:#ef5350;font-weight:700" if "rich" in v else ""
        st.dataframe(df.style.map(col, subset=["Verdict"]), hide_index=True, width="stretch",
                     column_config={"Break-even move %": num(format="%.1f"), "Usual results move %": num(format="%.1f"),
                                    "Median %": num(format="%.1f"), ">2 ATR %": num(format="%.0f"),
                                    "Straddle ₹": num(format="%.2f"), "Per lot ₹": num(format="₹%.0f"),
                                    "Strike": num(format="%g")})
        cheap = [r for r in rows if r[4] == "cheap" and (r[1] - now.date()).days <= 2]
        if cheap and alerts_on:  # one phone alert per symbol/result for cheap straddles close to the date
            ev = [(f"results:{s}:{d}", s, "SIGNAL",
                   f"📅 RESULTS STRADDLE {s} ({d}): BUY {imp['strike']:g} CE + {imp['strike']:g} PE ({imp['expiry']}) "
                   f"≈ ₹{imp['straddle']:.2f}\n{why}\nExit after the results reaction; premium collapses if the move is "
                   f"small. Educational, not advice.") for s, d, h, imp, v, why in cheap]
            for key, sym, kind, text in paper.notify(ev, send=True):
                st.toast(text.split("\n")[0], icon="📅")
        st.caption("Usual results move = average |close before announcement → close of the reaction session| over this "
                   "stock's past results (NSE filings). Break-even = ATM straddle ÷ spot on the first expiry after the "
                   "results date (Upstox live chain). Cheap ≥ 1.15×, rich ≤ 0.85×. A straddle still loses if the stock "
                   "moves less than its break-even.")
    rc = R.recent(uni, now)
    if not rc.empty:
        st.markdown("**Reported in the last few days**")
        rr = []
        for r in rc.head(15).itertuples():
            h = R.history(r.symbol)
            last = h["rows"].iloc[-1] if h.get("n") else None
            rr.append({"Symbol": r.symbol, "Results": r.date,
                       "Reaction %": float(last.move_pct) if last is not None and last.date.date() >= r.date else None,
                       "Reaction (ATR)": float(last.move_atr) if last is not None and last.date.date() >= r.date else None})
        st.dataframe(pd.DataFrame(rr), hide_index=True, width="stretch",
                     column_config={"Reaction %": st.column_config.NumberColumn(format="%+.2f"),
                                    "Reaction (ATR)": st.column_config.NumberColumn(format="%+.2f")})
        st.caption("After results the IV premium is gone, so the normal intraday CALL/PUT signals apply again at fair "
                   "premiums. Chasing the reaction for days showed no reliable edge here (66 reactions, 39 stocks).")
    R.score_log(now)
    lg = R.log_df()
    with st.expander(f"Straddle track record · {int(lg.actual_pct.notna().sum())} scored of {len(lg)} logged"):
        if lg.empty:
            st.caption("Assessments are logged before each result and scored after it.")
        else:
            sc = lg[lg.actual_pct.notna()]
            if len(sc):
                for v, g in sc.groupby("verdict"):
                    st.markdown(f"**{v}**: {len(g)} results, moved more than break-even "
                                f"{(g.straddle_edge_pct > 0).mean() * 100:.0f}% of the time, average edge "
                                f"{g.straddle_edge_pct.mean():+.2f}% of spot")
            st.dataframe(lg.drop(columns=["id"]), hide_index=True, width="stretch")


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
    import news
    st.html(ui_kit.section("Corporate catalysts · NSE filings, last 24 h"))
    ann = news.announcements()
    if ann.empty:
        st.caption("NSE announcements feed unavailable right now.")
    else:
        fno = set(s.strip() for s in E.FNO_STOCKS.split(",")) | set(wl_list())
        scope = st.segmented_control("Show", ["F&O + watchlist", "All companies"], default="F&O + watchlist",
                                     key="mi_news_scope", label_visibility="collapsed") or "F&O + watchlist"
        x = ann[ann.ts >= now - pd.Timedelta(hours=24)]
        if scope == "F&O + watchlist":
            x = x[x.symbol.isin(fno)]
        if moves is not None and len(moves):
            x = x.merge(moves[["Symbol", "chg"]].rename(columns={"Symbol": "symbol", "chg": "Day %"}), on="symbol", how="left")
        st.dataframe(x.drop(columns=["alert"]).rename(columns={"ts": "Filed", "reading": "Usual"}), hide_index=True,
                     width="stretch", column_config={"link": st.column_config.LinkColumn("Filing", display_text="PDF"),
                                                     "Filed": st.column_config.DatetimeColumn(format="DD MMM HH:mm")})
        st.caption("From NSE's announcements RSS (refreshed every 10 min). Order wins, acquisitions and buybacks usually "
                   "read positive and fund raising negative, but evidence says news alone is not a reliable CALL/PUT "
                   "signal in India: wait for price and volume to confirm. Alert-worthy filings on your watchlist are "
                   "pushed to your phone.")


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
    lst = pro.nifty50_list()
    if lst is None or lst.empty:
        st.warning("NIFTY 50 constituents list is unavailable right now (NSE archive unreachable).")
        return
    names = dict(zip(lst.Symbol, lst["Company Name"]))
    inds = dict(zip(lst.Symbol, lst.Industry))
    uni = st.segmented_control("Universe", ["NIFTY 50", "All F&O stocks"], default="NIFTY 50", key="n50_uni",
                               help="All F&O stocks scans every NSE stock with options (~210): about 1-2 minutes on "
                                    "Upstox. More setups, so more of them clear grade A/B.") or "NIFTY 50"
    syms = list(lst.Symbol)
    if uni == "All F&O stocks":
        u = symbols.universe()
        fno = u[u.kind == "fno"]
        syms = list(dict.fromkeys(fno.symbol))
        names = names | dict(zip(fno.symbol, fno.name))
    data = st.session_state.get("n50")
    if data and data.get("uni", "NIFTY 50") != uni:
        data = None
    c1, c2, c3 = st.columns([2, 3, 3], vertical_alignment="center")
    run = c1.button(f"🔄 Scan {uni}" if data else f"▶ Scan {len(syms)} stocks", type="primary", use_container_width=True)
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
        bar = st.progress(0.0, text=f"Scanning {uni}…")
        out = E.analyze_many(syms, Pn, vix, fx, now,
                             progress=lambda i, n, s: bar.progress(i / n, text=f"Scanned {i}/{n} · {s}"))
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
                    "Stock stop": r["u_sl"], "Confidence": r["conf"],
                    "Credibility": f"{r['cred']['grade']} · {r['cred']['score']}", "T1 odds": round(r["cred"]["p_t1"] * 100),
                    "Event": ev})
        paper.log_recs([(s, recommend(a, Pn)) for s, a in out.items() if "error" not in a], now, "nifty50")
        data = dict(rows=rows, time=now, src="Upstox" if E.UPSTOX else "Kite" if E.KITE is not None else "Yahoo/NSE",
                    uni=uni)
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
                        "Target 1 ₹", "Target 2 ₹", "Stock target 1", "Stock target 2", "Stock stop", "Credibility",
                        "T1 odds", "Confidence", "Score", "Event", "Industry", "Company"] if c in df.columns]
    st.dataframe(df[cols].style.map(color, subset=["Recommendation"]), hide_index=True, width="stretch",
                 height=min(80 + 35 * len(df), 1400),
                 column_config={"Trend": st.column_config.LineChartColumn("Trend", width="small"),
                                "LTP": num(format="%.2f"), "Day %": num(format="%+.2f%%"), "Score": num(format="%+.1f"),
                                **{c: num(format="%.2f") for c in ("Entry ₹", "Stop ₹", "Target 1 ₹", "Target 2 ₹",
                                                                   "Stock target 1", "Stock target 2", "Stock stop")},
                                "T1 odds": num(format="%d%%", help="Back-test T1 hit rate shrunk toward the model "
                                                                    "touch probability"),
                                "Credibility": st.column_config.TextColumn(
                                    help="A-D grade from back-test edge, live track record, contract liquidity, "
                                         "reachability, reward:risk, theta, IV and data freshness"),
                                "Confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=100,
                                                                              format="%d")})
    add = st.multiselect("⭐ Add to my watchlist", [s for s in df.Symbol if s not in wl_list()], key="n50_add",
                         placeholder="Pick stocks from this scan to track them in ⭐ Watchlist")
    if add:
        st.button(f"Add {len(add)} to watchlist", key="n50_add_btn", on_click=wl_add_many, args=("n50_add",))
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
    mode = c[3].segmented_control("Type", ["All", "Enter now", "Trigger", "Blocked by filters", "Broker calls"], default="All", key="tr_mode",
                                  label_visibility="collapsed")
    last = st.session_state.get("tr_eval")
    if run or last is None or (now - last).total_seconds() > 300:
        with st.spinner("Checking outcomes against live prices…"):
            paper.evaluate_recs(_bars_for, now)
        st.session_state["tr_eval"] = now
    sc = paper.scorecard(28)
    with st.expander("📋 Weekly scorecard · last 4 weeks of the app's own calls (in R: 1R = the planned risk)", expanded=bool(sc)):
        if not sc:
            st.caption("No resolved calls yet. Every CALL/PUT is judged automatically after the session; the scorecard "
                       "fills in as they resolve.")
        else:
            cfg = dict(win_pct=st.column_config.NumberColumn("Win %", format="%.0f"),
                       avg_r=st.column_config.NumberColumn("Avg R", format="%+.2f"),
                       total_r=st.column_config.NumberColumn("Total R", format="%+.2f"))
            w1, w2 = st.columns([3, 2])
            w1.dataframe(sc["week"].sort_values("week", ascending=False), hide_index=True, width="stretch", column_config=cfg)
            w2.dataframe(sc["kind"].replace({"CE": "CALL", "PE": "PUT"}), hide_index=True, width="stretch", column_config=cfg)
            st.dataframe(sc["symbol"], hide_index=True, width="stretch", column_config=cfg)
            st.caption("A symbol whose last 5 live calls average below 0R is automatically demoted to 📝 PAPER (0 lots) "
                       "until it recovers.")
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
    elif mode == "Blocked by filters":  # setups the confirmation filters stopped: did the filters save money?
        df = df[df["mode"] == "blocked"]
        st.caption("Setups the confirmation filters blocked, scored as if they had been taken. If these do worse than "
                   "'Enter now', the filters are earning their keep.")
    elif mode == "Broker calls":  # external calls you entered below, judged with the same rules
        df = df[df["mode"].str.startswith("broker")]
    else:
        df = df[~df["mode"].isin(["blocked"]) & ~df["mode"].str.startswith("broker")]
    with st.expander("➕ Track a broker / TV intraday call (check its real accuracy)"):
        with st.form("broker_call", clear_on_submit=True):
            f = st.columns([2, 1, 1, 1, 1, 1, 2])
            b_sym = f[0].text_input("Stock", placeholder="e.g. DABUR")
            b_side = f[1].selectbox("Side", ["BUY", "SELL"])
            b_entry = f[2].number_input("Entry", min_value=0.0, step=0.05, format="%.2f")
            b_sl = f[3].number_input("Stop", min_value=0.0, step=0.05, format="%.2f")
            b_t1 = f[4].number_input("Target 1", min_value=0.0, step=0.05, format="%.2f")
            b_t2 = f[5].number_input("Target 2 (opt.)", min_value=0.0, step=0.05, format="%.2f")
            b_src = f[6].text_input("Source", placeholder="ICICI Momentum Pick / TV show…")
            if st.form_submit_button("Track this call", type="primary"):
                ok_dir = (b_sl < b_entry < b_t1) if b_side == "BUY" else (b_t1 < b_entry < b_sl)
                if not b_sym or not b_entry or not ok_dir:
                    st.error("Need stock, entry, stop and target with stop and target on opposite sides of the entry.")
                elif paper.add_broker_call(symbols.to_symbol(b_sym) or b_sym, b_side, b_entry, b_sl, b_t1,
                                           b_t2 or b_t1, b_src, now):
                    st.success(f"Tracking {b_side} {b_sym.upper()} from {b_src or 'unnamed'}: see Type → Broker calls.")
                else:
                    st.warning("Already tracking that call today.")
        st.caption("Judged on live 5-min prices with ICICI Direct's own rules: entered only if price trades within ±0.25% "
                   "of the entry (otherwise 'not triggered', like their 'not initiated'), then stop vs target within the "
                   "same session. Intraday calls only. Over a few weeks this shows each source's real hit rate.")
        st.markdown("**📄 Or upload a broker's research PDF** (e.g. ICICI Direct Momentum Picks, HDFC Securities "
                    "Intraday Pick): its calls are read out for you to review and track.")
        up = st.file_uploader("Broker PDF", type=["pdf"], key="broker_pdf", label_visibility="collapsed")
        if up is not None:
            import broker_import
            src_name = st.text_input("Source name", value=re.sub(r"[_\-]+", " ", up.name.rsplit(".", 1)[0])[:40],
                                     key="broker_pdf_src")
            try:
                calls = broker_import.parse_pdf(up.getvalue())
            except Exception as e:
                calls = None
                st.error(f"Couldn't read that PDF: {e}")
            if calls is not None and calls.empty:
                st.warning("No calls with a stock, BUY/SELL, entry, target and stop-loss were found in this PDF.")
            elif calls is not None:
                calls.insert(0, "track", calls.duration.str.lower().eq("intraday") | calls.duration.eq(""))
                ed = st.data_editor(calls.drop(columns=["entry_hi"]), hide_index=True, width="stretch", key="broker_pdf_ed",
                                    column_config={"track": st.column_config.CheckboxColumn("Track", default=False),
                                                   "line": st.column_config.TextColumn("From the PDF", width="large")},
                                    disabled=["name", "duration", "line"])
                st.caption("Check each row (the stock name is matched to an NSE symbol; fix it if it's wrong). Intraday "
                           "calls are ticked by default; positional ones are judged on the same session only.")
                if st.button(f"Track {int(ed.track.sum())} selected call(s)", type="primary", key="broker_pdf_go"):
                    sel = ed[ed.track]
                    res = st.session_state.get("last_results", {})
                    missing = [x for x in dict.fromkeys(sel.symbol) if x not in res]
                    if missing:  # engine view for the agreement flag (light: no option chain, no news)
                        res = {**res, **E.analyze_many(missing, dict(P, chain=False, news=False), None, None, now)}
                    added = 0
                    for r in sel.itertuples():
                        a = res.get(r.symbol)
                        rec = recommend(a, P) if a and "error" not in a else None
                        ek = rec.get("kind") if rec and rec.get("mode") != "none" else None
                        agree = ("engine agrees" if ek == ("CE" if r.side == "BUY" else "PE") else
                                 "engine disagrees" if ek else "engine neutral")
                        added += paper.add_broker_call(r.symbol, r.side, r.entry, r.stop, r.t1, r.t2,
                                                       f"{src_name} · {agree}", now)
                    st.success(f"Tracking {added} call(s) from {src_name}. See Type → Broker calls.")
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
                        type=df["mode"].map(lambda m: {"now": "Enter now", "trigger": "Trigger", "blocked": "Blocked"}.get(m, m.replace("broker:", "Broker: "))))
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


def _oc_reads(chain, spot, w):
    """Strike -> OI read text from engine.oi_table (the chain table's strike tooltips)."""
    try:
        df = oi_table(chain, spot, width=w)
        return {float(s.split(" ")[0]): rd for s, rd in zip(df.Strike, df.Read)}
    except Exception:
        return {}


def _home_oa(sel, a, spot, now):
    """pro.options_analytics at the chain's live spot for the chips, memoised per symbol, chain object, minute and 0.1%
    spot bucket (GEX grid is slow-ish); the expected range is then re-centred on the exact spot."""
    key = (sel, id(a.get("chain")), now.floor("min"), round(float(np.log(spot)) * 1000) if spot and spot > 0 else None)
    hit = st.session_state.get("home_oa")
    if hit and hit[0] == key:
        o = hit[1]
    else:
        try:
            o = pro.options_analytics(dict(a, ltp=spot) if spot and spot > 0 else a, P, now)
        except Exception:
            o = None
        st.session_state["home_oa"] = (key, o)
    if o and o.get("straddle") and spot and spot > 0:
        o = dict(o, S=spot, em_lo=spot - o["straddle"], em_hi=spot + o["straddle"])
    return o


def _ev_cell(events):
    """Watchlist 'Event' text from analysis['events']: the most severe symbol event ('⚠ Results tmrw' high,
    '📅 Dividend 3d' info) or ''. Market-wide holidays are left to the quote header."""
    ev = sorted([e for e in (events or []) if e and len(e) == 2 and "holiday" not in str(e[1]).lower()],
                key=lambda e: e[0] != "high")
    return f"{'⚠' if ev[0][0] == 'high' else '📅'} {ui_kit.event_short(ev[0][1])}" if ev else ""


def _mkt_open(sess, now):
    """E.SESSIONS window on a weekday (exchange holidays are not checked)."""
    op, cl, _ = E.SESSIONS[sess]
    return now.weekday() < 5 and op <= now.time() < cl


def _mkt_status(now):
    """Index-strip market pill: NSE and MCX (approximate: some MCX contracts trade to 23:55) from E.SESSIONS."""
    hm = lambda s: "-".join(t.strftime("%H:%M") for t in E.SESSIONS[s][:2])
    return [("NSE", _mkt_open("nse", now), f"Weekdays {hm('nse')} IST (exchange holidays not checked)"),
            ("MCX", _mkt_open("mcx", now), f"≈ weekdays {hm('mcx')} IST; hours vary by contract (some to 23:55)")]


def _ago(mins):
    m = max(int(mins), 0)
    return ("just now" if m < 1 else f"{m} min ago" if m < 60 else f"{m // 60}h {m % 60:02d}m ago" if m < 1440
            else f"{m // 1440}d ago")


def _fresh(a, v, now):
    """Quote-header freshness: the last tick (Upstox last_trade_time) else the last closed candle, its age and the data
    sources. cls 'w' (amber) when stale while the symbol's market is open, 'ok' when fresh, '' when closed."""
    ts = None
    try:
        if v and v.get("last_trade_time"):
            ts = pd.Timestamp(int(v["last_trade_time"]), unit="ms", tz="UTC").tz_convert(IST)
    except (TypeError, ValueError, OverflowError):
        ts = None
    tick, iv = ts is not None, int(P["interval"][:-1])
    if not tick:
        ts = a["row"].name + pd.Timedelta(minutes=iv)  # bars are stamped at their start; prepare() drops the forming one
    age = (now - ts).total_seconds() / 60
    live = _mkt_open(a["spec"]["sess"], now)
    stale = live and age > (5 if tick else iv + 10)
    when = (f"{ts:%d %b} " if ts.date() != now.date() else "") + (f"{ts:%H:%M:%S}" if tick else f"{ts:%H:%M}")
    src = " · ".join(x for x in (f"{a.get('src') or '?'} candles", "Upstox quote" if v else None,
                                 f"{a['chain']['source']} chain" if a.get("chain") else None) if x)
    return dict(text=(f"Last tick {when}" if tick else f"{P['interval']} bar closed {when}") + f" · {_ago(age)} · {src}"
                + ("" if live else " · market closed"), cls="w" if stale else "ok" if live else "",
                tip="Data freshness: last traded tick (Upstox) or last closed candle, its age, and the candle / quote / "
                    "option-chain sources" + (". Stale for an open market: check the feed." if stale else ""))


def _ranges(a, v, ltp, exch):
    """Inputs for ui_kit.range_bars -> (day_lo, day_hi, day_title, wk_lo, wk_hi, stats). Upstox quote: day OHLC,
    average price, volume and OI; otherwise today's closed bars of a['d'] widened to include the LTP (the forming
    candle is not in d) and row.VWAP / PDC. 52-week from Yahoo daily bars; skipped for MCX (Yahoo ticker is a USD
    proxy)."""
    d, row, spec, num = a["d"], a["row"], a["spec"], ui_kit._num
    o, idx, now = (v or {}).get("ohlc") or {}, exch == "INDEX", pd.Timestamp.now(tz=IST)
    lo, hi, op = num(o.get("low")), num(o.get("high")), num(o.get("open"))
    td = d[d.index.date == d.index[-1].date()] if len(d) else d
    try:  # session the range belongs to: the quote's last trade (Upstox), else today while the market is open, else
        day = pd.Timestamp(int(v["last_trade_time"]), unit="ms", tz="UTC").tz_convert(IST).date()  # the last bar's day
    except (TypeError, ValueError, KeyError, OverflowError):
        day = now.date() if _mkt_open(spec["sess"], now) else td.index[-1].date() if len(td) else None
    title = "Today's range" if day == now.date() else f"Day range ({day:%d %b})" if day else "Day range"
    fresh_td = len(td) > 0 and td.index[-1].date() == day  # False early in a session: no closed bar of `day` yet
    if not (lo and hi):  # Yahoo / Kite, or Upstox pre-open zeros; never mix yesterday's bars into today's range
        lo = min(float(td.Low.min()), ltp) if fresh_td and ltp else None
        hi = max(float(td.High.max()), ltp) if fresh_td and ltp else None
        op = float(td.Open.iloc[0]) if fresh_td else None
    avg = num((v or {}).get("average_price")) if not idx else None
    avg = avg or (num(row.VWAP) if fresh_td else None)
    prev = ((v["last_price"] - v["net_change"]) if v and v.get("net_change") is not None else num(row.PDC) if fresh_td
            else float(d.Close.iloc[-1]) if len(d) else None)  # new session without bars: last close is prev. close
    vol = None if idx else num(v.get("volume")) if v else (float(td.Volume.sum()) if a["has_vol"] and fresh_td else None)
    oi = num((v or {}).get("oi"))
    f = lambda x: None if num(x) is None else f"{num(x):,.2f}"
    stats = [("Open", f(op)), ("Prev. close", f(prev)), ("VWAP" if a["has_vol"] else "Avg", f(avg)),
             ("Volume", ui_kit._qty(vol) if vol else None), ("OI", ui_kit._qty(oi) if oi else None)]
    wl_, wh = None, None
    yr = pro.year_range(spec["ticker"]) if spec["kind"] != "commodity" else None
    if yr and ltp and 0.7 * yr[0] <= ltp <= 1.3 * yr[1]:  # hide a mismatched / badly adjusted Yahoo series
        wl_, wh = min(yr[0], lo or yr[0]), max(yr[1], hi or yr[1])  # Yahoo daily bars can lag today's range
    return lo, hi, title, wl_, wh, stats


CHART_OV = list(E.CHART_OVERLAYS) + ["Levels"]


def _chart_levels(sel, a, r, spot, now):
    """Home chart lines [(label, price, colour, dash[, note])] on the underlying: the plan (entry / trigger, SL, T1, T2;
    both conditional triggers when there is no trade), an open paper trade (avg, stop, targets), the chain's OI walls
    and max pain, and the straddle expected range."""
    L = []
    if r.get("mode") in ("now", "trigger"):
        L += [("Entry" if r["mode"] == "now" else "Trigger", r.get("u_entry"), "#4184f3", "solid"),
              ("SL", r.get("u_sl"), "#ef5350", "dash"), ("T1", r.get("u_t1"), "#4caf50", "dot"),
              ("T2", r.get("u_t2"), "#4caf50", "dot")]
    else:
        alt = tuple(r.get("alt") or ()) + (None, None)
        L += [(lbl, x.get("u_entry"), col, "dot") for x, lbl, col in ((alt[0], "CALL above", "#4caf50"),
                                                                      (alt[1], "PUT below", "#ef5350")) if x]
    try:
        tr = paper.trades_df()
        t = tr[(tr.status == "OPEN") & (tr.symbol == sel)]
        pnl = paper.unrealized(t)
    except Exception:
        t, pnl = pd.DataFrame(), pd.Series(dtype=float)
    for i, x in t.iterrows():
        qty = (x.qty / 2 if x.half == 1 else x.qty) if pd.notna(x.qty) else None
        p = pnl.get(i)
        L.append(("Paper avg", x.entry_u, "#e6edf7", "solid", f"{x.kind} {_k(x.strike)} · "
                  + (f"{qty:,.0f} × " if qty is not None else "") + (f"P&L ₹{p:,.0f}" if pd.notna(p) else "P&L n/a")))
        L += [("Paper SL", x.stop_u, "#ef5350", "dashdot")] + \
             ([("Paper T1", x.t1_u, "#4caf50", "dashdot")] if x.half != 1 else []) + [("Paper T2", x.t2_u, "#4caf50", "dashdot")]
    ch = a.get("chain") or {}
    L += [("Call wall", ch.get("call_wall"), "#ef9a9a", "dot"), ("Put wall", ch.get("put_wall"), "#a5d6a7", "dot"),
          ("Max pain", ch.get("max_pain"), "#fbbf24", "dot")]
    o = _home_oa(sel, a, spot, now) if ch.get("quotes") else None
    if o:
        L += [("Exp. low", o.get("em_lo"), "#94a3b8", "dot"), ("Exp. high", o.get("em_hi"), "#94a3b8", "dot")]
    return [x for x in L if ui_kit._num(x[1]) is not None]


def _home_chart(sel, a, r, spot, now):
    """Home 'Chart' sub-tab: interval switch (chart only; signals stay on the sidebar interval), overlay pills,
    Indicators toggle (off = compact price + volume, on = the full 980px stack), OHLC legend and level lines."""
    ss = st.session_state
    pf = ss.setdefault("home_ch_pref", {"iv": None, "ov": list(CHART_OV), "ind": False, "base": P["interval"]})
    if pf["base"] != P["interval"]:  # sidebar interval changed: the chart follows it again
        pf.update(iv=None, base=P["interval"])
        ss.pop("home_iv", None)
    c1, c2, c3 = st.columns([3, 5, 2], vertical_alignment="center")
    pf["iv"] = c1.segmented_control("Interval", ["1m", "5m", "15m"], default=pf["iv"] or P["interval"], key="home_iv",
                                    required=True, label_visibility="collapsed")
    pf["ov"] = c2.pills("Overlays", CHART_OV, selection_mode="multi", default=pf["ov"], key="home_ov",
                        label_visibility="collapsed")
    pf["ind"] = c3.toggle("Indicators", value=pf["ind"], key="home_ind",
                          help="Off: compact price + volume. On: confluence, RSI, MACD and ADX panes (taller chart)")
    iv, ov, ca, note = pf["iv"] or P["interval"], list(pf["ov"] or []), a, None
    if iv != P["interval"]:  # the sidebar interval reuses a['d']: no extra fetch
        vix, _, fx = ss.get("vix", (None, None, None))
        try:
            with st.spinner(f"Loading {iv} candles…"):
                a2 = analyze_fast(sel, dict(P, interval=iv, news=False, chain=False), vix, fx, now)
        except Exception as e:
            a2 = {"error": f"{type(e).__name__}: {e}"}
        if "error" in a2:
            note, iv = f"{iv} chart unavailable ({a2['error']}): showing {P['interval']}.", P["interval"]
        else:
            ca, note = a2, f"Signals use the sidebar interval ({P['interval']}); chart shows {iv}."
    d = ca["d"]
    st.html(ui_kit.ohlc_legend(sel, iv, ca["row"], spot, ca.get("src"), ca["has_vol"],
                               d.Close.iloc[-2] if len(d) > 1 else None))
    levels = _chart_levels(sel, a, r, spot, now) if "Levels" in ov else None
    fig = make_chart(d, sel, a["spec"], ca["has_vol"], levels=levels, compact=not pf["ind"],
                     overlays=[x for x in ov if x != "Levels"])
    st.plotly_chart(fig, width="stretch", key="home_chart")
    off = (fig.layout.meta or {}).get("off") if isinstance(fig.layout.meta, dict) else None
    st.caption(" · ".join(x for x in (note, ("Off-chart: " + ", ".join(off)) if off else None,
                                      "Levels: blue entry / trigger, red SL, green targets, white paper avg, dotted = OI "
                                      "walls, max pain, expected range" if levels else None) if x))


def _home_chain(sel, a, spot, q, now):
    """Home 'Option chain' sub-tab: controls, summary chips, Calls | Strike | Puts table and a strike drill-down."""
    chain = a.get("chain")
    if not chain or not chain.get("quotes"):
        st.info("No MCX option chain: connect Zerodha Kite (Upstox and NSE chains cover NSE/BSE only)."
                if a["spec"]["kind"] == "commodity" else "No option chain for this symbol.")
        return
    spot = spot or chain.get("und") or a["ltp"]
    # widget state is dropped on runs that skip these controls (no-chain symbol): keep the choices in a plain key
    pf = st.session_state.setdefault("home_oc_pref", {"w": "±10", "view": "Calls & Puts", "greeks": False,
                                                      "side": "CE", "k": {}})
    c1, c2, c3 = st.columns([3, 3, 2], vertical_alignment="bottom")
    pf["w"] = c1.segmented_control("Strikes", ["±5", "±10", "±15", "All"], default=pf["w"], key="home_oc_w")
    pf["view"] = c2.segmented_control("View", ["Calls & Puts", "Calls", "Puts"], default=pf["view"], key="home_oc_view")
    pf["greeks"] = greeks = c3.toggle("Greeks", value=pf["greeks"], key="home_oc_greeks",
                                      help="Upstox: live Δ, θ/day and POP. Kite/NSE: model Δ (Black-Scholes from each "
                                           "strike's IV)")
    wk, view = pf["w"] or "±10", pf["view"] or "Calls & Puts"
    w = {"±5": 5, "±10": 10, "±15": 15, "All": len(chain["quotes"])}[wk]
    dte = E.option_dte(chain, P, now)
    vq = q.get(upstox_feed.VIX_KEY) if q else None
    vix = vq.get("last_price") if vq else (st.session_state.get("vix") or (None,))[0]
    st.html(ui_kit.chain_chips(chain, spot, vix, dte, _home_oa(sel, a, spot, now)))
    mdelta = None
    if greeks:
        T, rf = dte / 365, (0.0 if a["spec"]["kind"] == "commodity" else 0.065)
        base = chain.get("atm_iv") or a.get("iv") or 0
        mdelta = {(K, s): E.bs(spot, K, T, (x.get("iv") or 0) / 100 or base, s, rf)[1]
                  for K, sides in chain["quotes"].items() for s, x in sides.items() if x and x.get("delta") is None}
    st.html(ui_kit.option_chain_html(chain, spot, w, view, greeks, _oc_reads(chain, spot, w), mdelta))
    st.caption(ui_kit.chain_caption(chain, len(ui_kit.chain_window(chain, spot, w)[0]))
               + (" · model Δ = Black-Scholes from each strike's IV" if mdelta else ""))
    ks = sorted(chain["quotes"])
    atm = ui_kit.chain_window(chain, spot, 1)[1]
    with st.expander("🔍 Strike drill-down: fair value, premium stop and targets", expanded=False):
        d1, d2 = st.columns([3, 2], vertical_alignment="bottom")
        k0 = next((k for k in (pf["k"].get(sel), atm) if k in ks), ks[0])  # last pick for this symbol, else ATM
        K = pf["k"][sel] = d1.selectbox("Strike", ks, index=ks.index(k0), key=f"home_oc_k_{sel}",
                                        format_func=lambda k: f"{k:g}")
        pf["side"] = d2.segmented_control("Side", ["CE", "PE"], default=pf["side"], key="home_oc_side")
        kind = pf["side"] or "CE"
        try:
            s = E.option_price_signal(a, kind, K, 0, False, 0, P, now)
        except Exception as e:
            st.caption(f"Drill-down unavailable: {type(e).__name__}: {e}")
        else:
            st.html(ui_kit.strike_drill(s, a["spec"]["name"]))
            st.caption(f"Priced off the analysis LTP {a['ltp']:,.2f} with IV {a['iv'] * 100:.1f}% ({a['iv_src']}); "
                       "live = chain mid/last. Display only: no order is placed.")


def _sig(r):
    """Watchlist signal text of a recommend() dict: '▲ CALL' / '▼ PUT' (+ ' ⏳' while waiting for its trigger) or '·'."""
    if not r or r.get("mode") not in ("now", "trigger"):
        return "·"
    return ("▲ CALL" if r.get("kind") == "CE" else "▼ PUT") + ("" if r["mode"] == "now" else " ⏳")


def _k(x):
    x = ui_kit._num(x)
    return "–" if x is None else f"{x:g}"


def _home_toasts(recs, wl):
    """Home toasts: the pending paper-exit result, alerts rows logged by runner.py / other sessions since
    notif_last_id (rows this session already toasted are skipped), and watchlist flips into a live BUY CALL / PUT.
    The first run only seeds notif_* and home_prev_sig. At most 3 alert and 3 signal toasts per run. A flip the paper
    engine or intraday phone push already announced (ENTRY / BLOCKED / 'sig:' SIGNAL from run_scan or in the alert diff)
    is not toasted twice."""
    ss = st.session_state
    if ss.get("pos_flash"):
        st.toast(ss.pop("pos_flash"), icon="📤")
    sig = {s: _sig(recs.get(s)) for s in wl if s in recs}
    done = ss.pop("paper_syms", None) or set()
    if "notif_seen_id" not in ss:
        ss["notif_seen_id"] = ss["notif_last_id"] = paper.last_alert_id()
        ss["home_prev_sig"] = sig
        return
    try:
        new = paper.alerts_since(ss.get("notif_last_id", 0), limit=50)
    except Exception:
        new = []
    own = ss.get("notif_own") or set()
    for x in [x for x in new if x["key"] not in own][-3:]:
        st.toast(str(x["text"] or "").split("\n")[0], icon=ALERT_ICON.get(x["kind"]))  # SIGNAL texts are multi-line
    if new:
        ss["notif_last_id"] = new[-1]["id"]
    done |= {x["symbol"] for x in new if x["kind"] in ("ENTRY", "BLOCKED")
             or str(x.get("key") or "").startswith("sig:")}  # intraday push_signals only, not BTST / 2-day SIGNALs
    prev, n = ss.get("home_prev_sig") or {}, 0
    for s, g in sig.items():
        r = recs[s]
        if g in ("▲ CALL", "▼ PUT") and prev.get(s, g) != g and s not in done and n < 3:
            n += 1
            prem = ui_kit._num(r.get("prem"))
            st.toast(f"{s} {'BUY CALL' if r['kind'] == 'CE' else 'BUY PUT'} {_k(r.get('strike'))} {r['kind']}"
                     + (f" @ {prem:.2f}" if prem is not None else ""), icon="📗" if r["kind"] == "CE" else "📕")
    ss["home_prev_sig"] = sig


def _home_bell(now):
    """Header bell: unread alert count (ids above notif_seen_id) and the latest 8 alerts."""
    seen = st.session_state.get("notif_seen_id", 0)
    try:
        n, al = len(paper.alerts_since(seen, limit=100)), paper.alerts_df(8)
    except Exception:
        n, al = 0, pd.DataFrame()
    with st.popover(f"🔔 {n if n < 100 else '99+'}" if n else "🔔", key="home_bell", width="stretch",
                    help="Paper engine alerts (shared journal)"):
        st.html(ui_kit.notif_list(al.to_dict("records"), seen, now.date().isoformat()))
        st.button("Mark all read", key="notif_read_pop", on_click=_notif_read, disabled=not n, width="stretch")
    return n


# Upstox NSE_INDEX sector indices: (tile label, instrument 'name' as in the NSE instruments file, checked Oct 2026)
SECTORS = (("Bank", "Nifty Bank"), ("Pvt Bank", "Nifty Pvt Bank"), ("PSU Bank", "Nifty PSU Bank"),
           ("Fin Service", "Nifty Fin Service"), ("IT", "Nifty IT"), ("Auto", "Nifty Auto"), ("Pharma", "Nifty Pharma"),
           ("Healthcare", "NIFTY HEALTHCARE"), ("FMCG", "Nifty FMCG"), ("Metal", "Nifty Metal"), ("Realty", "Nifty Realty"),
           ("Energy", "Nifty Energy"), ("Oil & Gas", "NIFTY OIL AND GAS"), ("Media", "Nifty Media"),
           ("Cons. Durables", "NIFTY CONSR DURBL"), ("Infra", "Nifty Infra"), ("Commodities", "Nifty Commodities"),
           ("Chemicals", "Nifty Chemicals"), ("CPSE", "Nifty CPSE"), ("Capital Mkt", "Nifty Capital Mkt"))
WL_SORTS = ["Default", "A-Z", "% chg ↓", "% chg ↑", "LTP ↓", "Signal first"]


@st.cache_data(ttl=3600, show_spinner=False)
def _up_keys_c(day):
    df = upstox_feed.instruments("NSE", day)  # cached 6h; raises (not cached) when the download fails
    lst = pro.nifty50_constituents()
    eq = df[df.segment == "NSE_EQ"].drop_duplicates("trading_symbol").set_index("trading_symbol").instrument_key
    ix = df[df.segment == "NSE_INDEX"].drop_duplicates("name").set_index("name").instrument_key
    return ({s: (eq[s], ind) for s, ind in zip(lst.Symbol, lst.Industry) if s in eq.index},
            {lbl: ix[nm] for lbl, nm in SECTORS if nm in ix.index})


def _up_keys(day):
    """Upstox keys for Home's single batched quote: NIFTY 50 {symbol: (NSE_EQ key, industry)} and sector indices
    {label: NSE_INDEX key}, from the instruments file. ({}, {}) when it is unreachable."""
    try:
        return _up_keys_c(day)
    except Exception:
        return {}, {}


def _caret(v):
    v = ui_kit._num(v)
    return "–" if v is None else f"{'▲' if v >= 0 else '▼'} {abs(v):.2f}%"


def _n50_live(q, keys, yahoo=True):
    """NIFTY 50 movers frame: Symbol, Industry, last, chg (%), value (₹ cr), src (+ volume on Upstox). From the batched
    Upstox quotes q when they hold the constituents, else (yahoo=True) pro.nifty50_moves (Yahoo daily, 120s cache).
    None when neither works."""
    rows = []
    for s, (k, ind) in (keys or {}).items():
        v = (q or {}).get(k) or {}
        last, nc = ui_kit._num(v.get("last_price")), ui_kit._num(v.get("net_change"))
        if last:
            vol = ui_kit._num(v.get("volume")) or 0.0
            rows.append(dict(Symbol=s, Industry=ind, last=last, chg=_pc(last, nc) if nc is not None else None,
                             value=vol * (ui_kit._num(v.get("average_price")) or last) / 1e7, volume=vol, src="Upstox live"))
    if rows:
        return pd.DataFrame(rows)
    if not yahoo:
        return None
    try:
        df = pro.nifty50_moves()
    except Exception:
        df = None
    if df is None or df.empty or not {"Symbol", "last", "chg"} <= set(df.columns):
        return None
    df = df.reindex(columns=["Symbol", "Industry", "last", "chg", "value"])
    return df.assign(src="Yahoo daily close")


def _nse_indices(now):
    """pro.nse_indices() (120s cache), skipped for 10 minutes after a failure (NSE blocks cloud IPs: 2 × 8s timeouts)."""
    ss = st.session_state
    if ss.get("nse_down_until", 0) > now.timestamp():
        return None, None
    try:
        df, br = pro.nse_indices()
    except Exception:
        df, br = None, None
    if df is None:
        ss["nse_down_until"] = now.timestamp() + 600
    return df, br


def _breadth(n50, live, now):
    """Advance / decline / unchanged -> dict(adv, dec, unch, src) or None: NIFTY 50 frame (Upstox live, or Yahoo daily
    when Movers fetched it this run), else the watchlist's change sign, else NSE allIndices (nse_down guard), else the
    Yahoo NIFTY 50 frame."""
    def count(ch, src):
        ch = [c for c in (ui_kit._num(x) for x in ch) if c is not None]
        return dict(adv=sum(c > 0 for c in ch), dec=sum(c < 0 for c in ch), unch=sum(c == 0 for c in ch), src=src) \
            if ch else None
    if n50 is not None and len(n50):
        b = count(n50.chg, "NIFTY 50 live" if n50.src.iloc[0] == "Upstox live" else "NIFTY 50, daily")
        if b:
            return b
    b = count([c for _, c in live.values()], "Watchlist")
    if b:
        return b
    _, br = _nse_indices(now)
    if br and (br.get("adv") or br.get("dec")):
        return dict(br, src="NSE all")
    y = _n50_live({}, {}, yahoo=True)
    return count(y.chg, "NIFTY 50, daily") if y is not None else None


def _sector_rows(q, sec, n50, keys, now):
    """Sector tiles -> (rows, title): Upstox sector indices from the batched quote, else NSE allIndices SECTORAL
    INDICES (nse_down guard), else NIFTY 50 industry averages of the movers frame; ([], None) when none work."""
    rows = []
    for lbl, k in (sec or {}).items():
        v = (q or {}).get(k) or {}
        last, nc = ui_kit._num(v.get("last_price")), ui_kit._num(v.get("net_change"))
        if last:
            rows.append(dict(name=lbl, chg=_pc(last, nc) if nc is not None else None))
    title = "NSE sector indices · Upstox live"
    if not rows:
        df, _ = _nse_indices(now)
        if df is not None and {"key", "index", "percentChange"} <= set(df.columns):
            s = df[df.key == "SECTORAL INDICES"]
            rows = [dict(name=str(n).replace("NIFTY ", ""), chg=c, adv=a, dec=d) for n, c, a, d in
                    zip(s["index"], s.percentChange, s.get("advances", [None] * len(s)), s.get("declines", [None] * len(s)))]
            title = "NSE sectoral indices · NSE"
    if not rows:
        m = n50 if n50 is not None else _n50_live(q, keys, yahoo=True)
        if m is not None and len(m) and "Industry" in m:
            g = m.dropna(subset=["chg"]).groupby("Industry").chg
            rows = [dict(name=i, chg=c, adv=a, dec=d) for i, c, a, d in
                    zip(g.mean().index, g.mean(), g.apply(lambda x: (x > 0).sum()), g.apply(lambda x: (x < 0).sum()))]
            title = f"NIFTY 50 industries (average) · {m.src.iloc[0]}"
    rows.sort(key=lambda r: -(ui_kit._num(r["chg"]) if ui_kit._num(r["chg"]) is not None else -1e9))
    return rows, (title if rows else None)


def _overview(now, tr, recs, wl):
    """Overview card inputs: paper funds (capital + realised today - premium in open trades), paper positions (same
    figures as the Positions strip) and the watchlist Signal-column counts."""
    opn = tr[tr.status == "OPEN"]
    cls = tr[(tr.status == "CLOSED") & tr.exit_at.fillna("").str.startswith(now.date().isoformat())]
    realised, un = float(cls.pnl.sum()), paper.unrealized(opn)
    used = float((opn.entry_p * opn.qty / np.where(opn.half == 1, 2, 1)).sum()) if len(opn) else 0.0
    try:
        rs = paper.risk_status(P, now)
    except Exception:
        rs = {}
    funds = dict(avail=P["capital"] + realised - used, used=used, capital=P["capital"], realised=realised,
                 risk_ok=rs.get("ok", True), reasons=rs.get("reasons"))
    pos = dict(n=len(opn), pnl=realised + float(un.sum()), items=list(zip(_instr(opn), un.tolist())))
    g = [_sig(recs.get(s)) for s in wl]
    return funds, pos, dict(call=sum("▲" in x for x in g), put=sum("▼" in x for x in g), wait=sum("⏳" in x for x in g),
                            none=sum(x == "·" for x in g))


def _flash(prev, syms):
    """Tick flash for Styler.apply on LTP: green / red background where LTP rose / fell since the last Home run."""
    def f(col):
        out = []
        for s, x in zip(syms, col):
            x, p = ui_kit._num(x), ui_kit._num(prev.get(s))
            out.append("" if x is None or p is None or x == p else
                       f"background-color:{'rgba(76,175,80,.28)' if x > p else 'rgba(239,83,80,.28)'}")
        return out
    return f


def movers_block(n50, now):
    """Home left 'Movers': NIFTY 50 top 10 gainers / losers / most active (₹ value); a click opens it on the right."""
    ss = st.session_state
    kind = st.segmented_control("Movers", ["Gainers", "Losers", "Most active"], key="home_mv_kind", required=True,
                                default=ss.get("home_mv_pref", "Gainers"), label_visibility="collapsed") or "Gainers"
    ss["home_mv_pref"] = kind
    if n50 is None or n50.empty:
        st.info("Market movers unavailable (NSE list and Upstox both unreachable).")
        return
    act = kind == "Most active"
    d = n50.dropna(subset=["value" if act else "chg"])
    d = d.sort_values("value" if act else "chg", ascending=kind == "Losers", kind="stable").head(10).reset_index(drop=True)
    t = pd.DataFrame({"Symbol": d.Symbol, "LTP": d["last"], "Chg %": d.chg} | ({"Value (₹ cr)": d.value} if act else {}))
    if ss.get("home_mv_order") != list(t.Symbol):  # rows moved: new key drops a stale positional highlight
        ss["home_mv_v"] = ss.get("home_mv_v", 0) + 1
    ss["home_mv_order"] = list(t.Symbol)
    ss["home_mv_key"] = mkey = f"home_mv_{ss.get('home_mv_v', 0)}"
    tint = lambda v: ("" if ui_kit._num(v) is None else "color:#4caf50" if v > 0 else "color:#ef5350" if v < 0 else "")
    sty = t.style.map(tint, subset=["Chg %"]).format(_caret, subset=["Chg %"]).apply(
        lambda c: [tint(x) for x in t["Chg %"]], subset=["Symbol"])
    st.dataframe(sty, hide_index=True, width="stretch", on_select=_mv_pick, selection_mode="single-row", key=mkey,
                 height=40 + 35 * len(t), column_config={"LTP": st.column_config.NumberColumn(format="%.2f"),
                                                         "Value (₹ cr)": st.column_config.NumberColumn(format="%.1f")})
    src = str(d.src.iloc[0]) if len(d) else ""
    st.caption(f"{src} · {now.strftime('%H:%M')}" + (" · as of last trade" if src == "Upstox live" and
                                                       not _mkt_open("nse", now) else "")
               + " · tap a row to open it; '+ Add' puts it in your list")


def home_tab():
    if st.session_state.pop("home_full_rerun", False):
        st.rerun(scope="app")  # after ✕ Remove / + Add / a list 1-5 switch: refresh the sidebar list and the scan
    now = pd.Timestamp.now(tz=IST)
    res = st.session_state.get("last_results")
    if not res:
        res, _, _ = run_scan(now)
    wl, ss = wl_list(), st.session_state
    sel = ss.get("home_sel")
    peek = bool(ss.get("home_peek")) and bool(sel) and sel not in wl  # opened from Movers, not in the list
    if sel not in wl and not peek:
        sel = wl[0] if wl else None
    view = ss.get("home_left_view") or "Watchlist"  # read before the control renders: Yahoo movers fetch only when open
    tok = E.UPSTOX
    qkeys = {s: _quote_key(s) for s in wl + ([sel] if peek else [])} if tok else {}
    n50k, seck = _up_keys(kite_day()) if tok else ({}, {})
    ikeys = {n: (upstox_feed.INDEX_KEYS[k] if k else upstox_feed.VIX_KEY) for n, k, _ in INDEX_STRIP}
    q = upstox_feed.quotes(tok, tuple(dict.fromkeys(  # one batched call: indices, list (+ peek), NIFTY 50, sectors
        list(ikeys.values()) + [k for k in qkeys.values() if k] + [k for k, _ in n50k.values()]
        + list(seck.values())))) if tok else {}
    items = []
    for name, _, yt in INDEX_STRIP:
        v = q.get(ikeys[name])
        if v:
            items.append((name, f"{v['last_price']:,.2f}", v.get("net_change"), _pc(v["last_price"], v.get("net_change"))))
        else:
            lc, chg = last_close(yt)
            items.append((name, f"{lc:,.2f}" if lc else "n/a", (lc * chg / (1 + chg)) if lc and chg is not None else None,
                          chg * 100 if chg is not None else None))
    recs = {s: recommend(a, P) for s, a in res.items() if a and "error" not in a}  # once per run, reused below
    _home_toasts(recs, wl)
    hs, hb = st.columns([14, 2], vertical_alignment="center")
    with hs:
        st.html(ui_kit.kite_strip(items, _mkt_status(now)))
    with hb:
        unread = _home_bell(now)

    def lc(s):
        """(LTP, abs change) for a watchlist symbol: batched Upstox quote, else the scan's analysis."""
        a, v = res.get(s), (q.get(qkeys.get(s)) if tok else None)
        if v:
            return v["last_price"], v.get("net_change")
        if a and "error" not in a:
            return a["ltp"], (a["ltp"] - a["row"].PDC) if pd.notna(a["row"].PDC) else None
        return None, None
    live = {s: lc(s) for s in wl}
    n50 = _n50_live(q, n50k, yahoo=view == "Movers")
    tr = paper.trades_df()
    st.html(ui_kit.overview_cards(user.get("name") or user.get("username"), *_overview(now, tr, recs, wl),
                                  _breadth(n50, live, now)))
    credible_panel(res, "home", recs)

    def watchlist_block():
        """Left 'Watchlist': numbered lists 1-5, search + sort, the table (carets, tick flash; click a row to open it)."""
        sets = auth.get_watchlist_sets(user["username"])
        slot = ss.get("wl_slot", 1)
        nm = (sets.get(slot) or {}).get("name") or f"Watchlist {slot}"
        st.markdown(f"**{ui_kit.escape(nm)}** <small style='color:#8ea0bf'>({len(wl)})</small>", unsafe_allow_html=True)
        c_s, c_o = st.columns([3, 2], vertical_alignment="center")
        c_s.selectbox("Search & add", [x for x in symbols.labels() if symbols.to_symbol(x) not in wl], index=None,
                      key="home_search", on_change=wl_add_from, args=("home_search",), accept_new_options=True,
                      placeholder="🔍 Search eg: infy, nifty, tata…", label_visibility="collapsed")
        srt = c_o.selectbox("Sort", WL_SORTS, key="home_wl_sort", label_visibility="collapsed",  # state dropped while
                            index=WL_SORTS.index(ss.get("home_wl_sort_pref", "Default")))   # another view is open
        ss["home_wl_sort_pref"] = srt = srt or "Default"
        rows = []
        for s in wl:
            r = recs.get(s)
            ltp, ch = live[s]
            rows.append({"Instrument": s, "LTP": ltp, "Chg": ch, "Chg %": _pc(ltp, ch) if ltp and ch is not None else None,
                         "Signal": _sig(r), "Cred.": ((r or {}).get("cred") or {}).get("grade") or "–",
                         "Event": _ev_cell((res.get(s) or {}).get("events"))})
        if not rows:
            st.info("Search above to add stocks.")
        else:
            wdf = pd.DataFrame(rows)
            if not wdf.Event.astype(bool).any():
                wdf = wdf.drop(columns="Event")
            if srt == "A-Z":  # display order only: the saved list order is untouched
                wdf = wdf.sort_values("Instrument", kind="stable")
            elif srt.startswith("% chg"):
                wdf = wdf.sort_values("Chg %", ascending=srt.endswith("↑"), na_position="last", kind="stable")
            elif srt == "LTP ↓":
                wdf = wdf.sort_values("LTP", ascending=False, na_position="last", kind="stable")
            elif srt == "Signal first":
                wdf = wdf.iloc[np.argsort([2 if g == "·" else 1 if "⏳" in g else 0 for g in wdf.Signal], kind="stable")]
            wdf = wdf.reset_index(drop=True)
            if ss.get("home_wl_order") != list(wdf.Instrument):  # rows moved: new key resets a stale highlight
                ss["home_wl_v"] = ss.get("home_wl_v", 0) + 1
            ss["home_wl_order"] = list(wdf.Instrument)  # _wl_pick maps the clicked row through this
            ss["home_wl_key"] = wkey = f"home_wl_{ss.get('home_wl_v', 0)}"

            def col(v):
                try:
                    return "color:#4caf50" if float(v) > 0 else "color:#ef5350" if float(v) < 0 else ""
                except (TypeError, ValueError):
                    s = str(v)
                    return ("color:#4caf50;font-weight:700" if "CALL" in s else "color:#ef5350;font-weight:700" if "PUT" in s
                            else "color:#64748b")
            grade = {"A": "color:#4caf50;font-weight:700", "B": "color:#38bdf8;font-weight:700", "C": "color:#f59e0b",
                     "D": "color:#ef5350"}
            prev = ss.get("home_prev_ltp") or {}
            sty = (wdf.style.map(col, subset=["Chg", "Chg %", "Signal"])
                   .map(lambda g: grade.get(g, "color:#64748b"), subset=["Cred."])
                   .apply(lambda c: [col(x) if ui_kit._num(x) is not None else "" for x in wdf.Chg], subset=["Instrument"])
                   .apply(_flash(prev, list(wdf.Instrument)), subset=["LTP"])
                   .format(_caret, subset=["Chg %"]))  # ▲/▼ carets: Styler text, so no NumberColumn format on Chg %
            if "Event" in wdf:
                sty = sty.map(lambda e: "color:#fbbf24;font-weight:700" if str(e).startswith("⚠") else "color:#8ea0bf",
                              subset=["Event"])
            st.dataframe(sty, hide_index=True, width="stretch", on_select=_wl_pick, selection_mode="single-row", key=wkey,
                         height=min(40 + 35 * len(wdf), 760),
                         column_config={"LTP": st.column_config.NumberColumn(format="%.2f"),
                                        "Chg": st.column_config.NumberColumn(format="%+.2f"),
                                        "Cred.": st.column_config.TextColumn(
                                            "Cred.", width="small", help="Credibility grade A-D of the signal "
                                                                         "(back-test edge, live record, liquidity, data)"),
                                        "Event": st.column_config.TextColumn(
                                            "Event", width="small", help="Corporate event or expiry within 5 days. "
                                                                         "⚠ amber = results today/tomorrow or expiry day "
                                                                         "(IV crush / extreme theta). Details in the quote "
                                                                         "header badge.")})
            ss["home_prev_ltp"] = {s: x for s, x in zip(wdf.Instrument, wdf.LTP) if ui_kit._num(x) is not None}
            st.caption("Tap a row to open it · ▲ CALL / ▼ PUT = signal, ⏳ = waiting for trigger · Cred. = credibility A-D"
                       + (" · ⚠ Event = results / expiry risk" if "Event" in wdf else "")
                       + " · LTP flashes green / red when it moved since the last refresh")
        c_l, c_n, c_e = st.columns([6, 2, 1], vertical_alignment="center")
        c_l.segmented_control("List", [1, 2, 3, 4, 5], key="home_wl_slot", default=slot, required=True,
                              on_change=wl_switch_slot, label_visibility="collapsed",
                              format_func=lambda k: str(k), help="Watchlists 1-5: " + " · ".join(
                                  f"{k}: {(sets.get(k) or {}).get('name') or f'Watchlist {k}'} "
                                  f"({len((sets.get(k) or {}).get('symbols') or [])})" for k in range(1, 6)))
        c_n.caption(f"{len(wl)} / 40")
        with c_e.popover("✎", help="Rename this list"):
            st.text_input("Name", key="wl_slot_name", max_chars=24, placeholder=f"Watchlist {slot}")
            st.button("Save", key="wl_slot_save", on_click=_wl_rename, type="primary", width="stretch")
        if ss.get("wl_slot_note") and paper.SETTINGS.exists():
            try:
                same = set(paper.load_settings().get("symbols") or []) == set(wl)
            except Exception:
                same = False
            if not same:
                st.caption("Background engine still uses the symbols saved in Paper trading; re-save to update")

    def sectors_block():
        """Left 'Sectors': heat tiles (Upstox sector indices, else NSE allIndices, else NIFTY 50 industry averages)."""
        rows, title = _sector_rows(q, seck, n50, n50k, now)
        if rows:
            st.html(ui_kit.sector_tiles(rows, title))
        else:
            st.caption("Sector data unavailable")

    def quote_block(sel):
        """Right column: quote header, action buttons and the Chart / depth / chain / recommendation sub-tabs."""
        a = res.get(sel)
        if a is None or "error" in a:
            with st.spinner(f"Loading {sel}…"):
                a = _get_analysis(sel)
        if "error" in a:
            st.error(f"{sel}: {a['error']}")
            return
        r = recs.get(sel) or recommend(a, P)
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
        st.html(ui_kit.quote_header(sel, exch, ltp, ch, _pc(ltp, ch) if ch is not None else None, buy, sell,
                                    a.get("events"), _fresh(a, v, now)))
        try:
            lo_, hi_, dt_, wlo, whi, qs_ = _ranges(a, v, ltp, exch)
            st.html(ui_kit.range_bars(lo_, hi_, ltp, wlo, whi, qs_, dt_))
        except Exception as e:  # ranges are cosmetic: never break the quote panel
            st.caption(f"Ranges unavailable: {type(e).__name__}")
        status = (f"**{r['action']}** · confidence {r['conf']}" if r["mode"] != "none" else
                  f"**No trade** · score {r['score']:+.1f}: conditional plans shown on the buttons")
        st.caption(status + " · display only: place orders in your broker app")
        for n in r.get("notes", [])[:2]:
            st.caption("⚠ " + n)
        t_chart, t_depth, t_chain, t_reco = st.tabs(["Chart", "Market depth", "Option chain", "Recommendation"])
        with t_chart:
            _home_chart(sel, a, r, ltp, now)
        with t_depth:
            if v:
                st.html(ui_kit.depth_html(v, exch))
                if not (v.get("depth") or {}).get("buy") or not any(x.get("price") for x in v["depth"]["buy"]):
                    st.caption("Depth is empty outside market hours or for indices.")
            else:
                st.info("Market depth needs the Upstox data source (sidebar).")
        with t_chain:
            _home_chain(sel, a, ltp, q, now)
        with t_reco:
            st.html(ui_kit.reco_card(r, _chg(a), ui_kit.sparkline(_spark(a))))

    left, right = st.columns([4, 8], gap="medium")
    with left:
        lv = st.segmented_control("Left view", ["Watchlist", "Movers", "Sectors"], default="Watchlist", required=True,
                                  key="home_left_view", label_visibility="collapsed") or "Watchlist"
        if lv == "Movers":  # only the open view's body runs (no NSE / Yahoo calls for hidden views)
            movers_block(n50, now)
        elif lv == "Sectors":
            sectors_block()
        else:
            watchlist_block()
        if peek:
            st.button(f"+ Add {sel} to watchlist", key="home_add", type="primary", width="stretch", on_click=wl_add_sym,
                      args=(sel,))
        elif sel and lv == "Watchlist":
            st.button(f"✕ Remove {sel}", key="home_del", use_container_width=True, on_click=_home_remove, args=(sel,))
    with right:
        if sel:
            if peek:
                st.caption(f"{sel} is not in your watchlist (opened from Movers).")
            quote_block(sel)
    home_bottom(now, res, recs, {s: x[0] for s, x in live.items()}, wl, unread, tr)  # always (empty list / failed symbol)


def home_bottom(now, res, recs, ltps, wl, unread=0, tr=None):
    """Home bottom section: Positions / Triggers (GTT view) / Notifications. A segmented control rather than st.tabs,
    so only the open view's body runs on each fragment run."""
    st.divider()
    tr = paper.trades_df() if tr is None else tr
    n_open = int((tr.status == "OPEN").sum())
    lbl = {"Positions": f"Positions ({n_open})", "Notifications": f"Notifications ({unread})" if unread else "Notifications"}
    b1, b2 = st.columns([5, 3], vertical_alignment="bottom")
    view = b1.segmented_control("Section", ["Positions", "Triggers (GTT view)", "Notifications"], default="Positions",
                                required=True, format_func=lambda x: lbl.get(x, x), key="home_bottom",
                                label_visibility="collapsed") or "Positions"
    if view == "Positions":
        positions_panel(now, res, b2, tr)
    elif view == "Notifications":
        notifications_panel(now, unread)
    else:
        df = triggers_table(recs, ltps, tr[tr.status == "OPEN"], wl, res)
        if df.empty:
            st.caption("Nothing pending: no watchlist symbol has a BUY CALL / BUY PUT plan and no paper trade is open.")
        else:
            pc = ["Trig. % away", "SL % away", "Tgt % away"]
            amber = lambda x: ("background-color:rgba(245,158,11,.22);color:#fbbf24;font-weight:700"
                               if pd.notna(x) and abs(x) <= 0.25 else "")
            st.dataframe(df.style.map(amber, subset=pc), hide_index=True, width="stretch",
                         column_config={c: st.column_config.NumberColumn(format="%.2f") for c in
                                        ("Trigger", "SL", "Target", "LTP")} |
                                       {c: st.column_config.NumberColumn(format="%+.2f%%") for c in pc})
        st.caption("Display only: no GTT or order is placed. Levels are on the underlying; premiums are in the "
                   "Recommendation tab. Amber = a pending level within 0.25% of LTP (SL / target % away start once a "
                   "Waiting trigger fills). Symbols with an open paper trade show only the "
                   "paper OCO row. Updates (and toasts) while Home runs: turn Auto on.")


def _instr(df):
    return [f"{s} {_k(k)} {t}" + (f" · {e}" if isinstance(e, str) and e else "")
            for s, k, t, e in zip(df.symbol, df.strike, df.kind, df.expiry)]


def _pos_frame(opn, cls):
    """Kite-style positions rows: open (remaining qty, MTM) then closed today (qty 0, exit premium, realised)."""
    o = pd.DataFrame({"id": opn.id, "Product": "MIS", "Instrument": _instr(opn),
                      "Qty.": opn.qty / np.where(opn.half == 1, 2, 1), "Avg.": opn.entry_p, "LTP": opn.last_p,
                      "P&L": paper.unrealized(opn), "Chg %": (opn.last_p / opn.entry_p - 1) * 100, "SL": opn.sl_p,
                      "T1": opn.t1_p, "T2": opn.t2_p, "Status": np.where(opn.half == 1, "T1 booked", "Open"),
                      "Time": opn.opened_at.fillna("").str[11:16], "_k": "o"})
    c = pd.DataFrame({"id": cls.id, "Product": "MIS", "Instrument": _instr(cls), "Qty.": 0.0, "Avg.": cls.entry_p,
                      "LTP": cls.exit_p, "P&L": cls.pnl, "Chg %": (cls.exit_p / cls.entry_p - 1) * 100, "SL": cls.sl_p,
                      "T1": cls.t1_p, "T2": cls.t2_p, "Status": cls.exit_reason.fillna("closed"),
                      "Time": cls.exit_at.fillna("").str[11:16], "_k": "c",
                      "Avg buy": cls.entry_p, "Avg sell": np.where(cls.half == 1, (cls.half_p + cls.exit_p) / 2, cls.exit_p),
                      "Realised P&L": cls.pnl, "R": cls.r_mult})
    return o, c


def _with_total(df, col):
    return pd.concat([df, pd.DataFrame([{"Instrument": "Total", col: float(df[col].sum()), "_k": "t"}])],
                     ignore_index=True)


def _pos_table(df, cols, money, **kw):
    """Styled positions dataframe: closed rows greyed, Total row bold, P&L / Chg % green-red; id and _k hidden."""
    def row(r):
        return ["color:#64748b" if r["_k"] == "c" else "font-weight:700" if r["_k"] == "t" else ""] * len(r)
    tint = lambda x: "color:#4caf50" if pd.notna(x) and x > 0 else "color:#ef5350" if pd.notna(x) and x < 0 else ""
    fmt = {c: st.column_config.NumberColumn(format="%.2f") for c in ("Avg.", "LTP", "SL", "T1", "T2", "Avg buy", "Avg sell")}
    fmt |= {c: st.column_config.NumberColumn(format="₹%.0f") for c in money}
    fmt |= {"Chg %": st.column_config.NumberColumn(format="%+.2f%%"), "R": st.column_config.NumberColumn(format="%+.2f"),
            "Qty.": st.column_config.NumberColumn(format="%g")}  # %g: half of an odd lot stays 37.5, not 37
    return st.dataframe(df.style.apply(row, axis=1).map(tint, subset=[c for c in money + ["Chg %"] if c in df]),
                        column_order=cols, hide_index=True, width="stretch", column_config=fmt, **kw)


def positions_panel(now, res, slot=None, tr=None):
    """Home Positions: paper P&L strip (shared journal), Open / Closed today / All today views with Kite day's history,
    and a paper exit for admins. Display only: never a broker order."""
    tr = paper.trades_df() if tr is None else tr
    opn = tr[tr.status == "OPEN"]
    cls = tr[(tr.status == "CLOSED") & tr.exit_at.fillna("").str.startswith(now.date().isoformat())]
    realised, unreal = float(cls.pnl.sum()), float(paper.unrealized(opn).sum())
    # the filter is not rendered while another bottom view is open (state dropped): keep the choice in a plain key
    f = (slot or st).segmented_control("Show", ["Open", "Closed today", "All today"], required=True, key="home_pos_f",
                                       default=st.session_state.get("home_pos_pref", "Open"),
                                       label_visibility="collapsed") or "Open"
    st.session_state["home_pos_pref"] = f
    st.html(ui_kit.pnl_summary(realised + unreal, realised, unreal, len(opn), len(cls)))
    o, c = _pos_frame(opn, cls)
    base = ["Product", "Instrument", "Qty.", "Avg.", "LTP", "P&L", "Chg %", "SL", "T1", "T2", "Status", "Time"]
    if f == "Closed today":
        if c.empty:
            st.caption("Nothing closed today.")
        else:
            _pos_table(_with_total(c, "Realised P&L"), ["Product", "Instrument", "Avg buy", "Avg sell", "Realised P&L",
                                                        "R", "Chg %", "Status", "Time"], ["Realised P&L"])
        return
    if f == "All today":
        if o.empty and c.empty:
            st.caption("No paper trades today.")
        else:
            _pos_table(_with_total(pd.concat([o, c[o.columns]], ignore_index=True), "P&L"), base, ["P&L"])
        return
    if o.empty:
        st.caption("No open paper positions. Paper trades open automatically when a BUY CALL / BUY PUT signal fires.")
        return
    ids = [int(x) for x in o.id]
    if st.session_state.get("home_pos_ids") != ids:  # rows changed: new key drops a stale positional selection
        st.session_state["home_pos_v"] = st.session_state.get("home_pos_v", 0) + 1
    st.session_state["home_pos_ids"] = ids
    o = o.reset_index(drop=True)  # only open rows are selectable; Total goes in its own non-selectable table below
    ev = _pos_table(o, base, ["P&L"], on_select="rerun", selection_mode="single-row",
                    key=f"home_pos_{st.session_state.get('home_pos_v', 0)}")
    _pos_table(_with_total(o, "P&L").tail(1), base, ["P&L"], key="home_pos_total")
    rows = list(((ev or {}).get("selection") or {}).get("rows") or [])
    if user["role"] != "admin":
        st.caption("Paper journal is shared; only admins can exit paper trades.")
    elif rows and rows[0] < len(o):
        t = o.iloc[rows[0]]
        with st.popover(f"Exit (paper) {t.Instrument}", type="primary"):
            st.caption("Closes this paper trade at the current premium. No broker order is placed.")
            st.button("Exit (paper)", type="primary", key=f"pos_exit_{int(t.id)}", on_click=_paper_exit,
                      args=(int(t.id),))
    else:
        st.caption("Select a row to exit it (paper). Paper journal is shared: an exit affects every user.")


def triggers_table(recs, ltps, opn, wl, res=None):
    """GTT-style view (display only): one row per watchlist BUY CALL / PUT plan ('Single' trigger while waiting, 'OCO'
    SL/target once live) and one OCO row per open paper trade; a symbol with an open paper trade shows only that row.
    % away = level / LTP - 1. Sorted by the nearest pending level (trigger while waiting, else SL / target)."""
    def away(x, l):
        x, l = ui_kit._num(x), ui_kit._num(l)
        return (x / l - 1) * 100 if x is not None and l else None
    held, rows = set(opn.symbol), []
    for s in wl:
        r = recs.get(s)
        if not r or r.get("mode") not in ("now", "trigger") or s in held:
            continue
        w, l = r["mode"] == "trigger", ltps.get(s)
        rows.append(dict(Instrument=f"{s} {_k(r.get('strike'))} {r.get('kind') or ''}", Type="Single" if w else "OCO",
                         Trigger=r.get("u_entry") if w else None, SL=r.get("u_sl"), Target=r.get("u_t1"), LTP=l,
                         Status="Waiting" if w else "Active"))
    for t in opn.itertuples():
        a = (res or {}).get(t.symbol) or {}
        l = ltps.get(t.symbol) or (a.get("ltp") if "error" not in a else None) or ui_kit._num(t.last_u)
        rows.append(dict(Instrument=f"{t.symbol} {_k(t.strike)} {t.kind} · paper", Type="OCO", Trigger=None,
                         SL=t.stop_u, Target=t.t2_u if t.half == 1 else t.t1_u, LTP=l,
                         Status="T1 booked" if t.half == 1 else "Active"))
    cols = ["Instrument", "Type", "Trigger", "Trig. % away", "SL", "SL % away", "Target", "Tgt % away", "LTP", "Status"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["LTP"] = pd.to_numeric(df.LTP, errors="coerce")
    for lv, pc in (("Trigger", "Trig. % away"), ("SL", "SL % away"), ("Target", "Tgt % away")):
        df[lv] = pd.to_numeric(df[lv], errors="coerce")
        df[pc] = pd.to_numeric(pd.Series([away(x, l) for x, l in zip(df[lv], df.LTP)], index=df.index), errors="coerce")
    df.loc[df.Status == "Waiting", ["SL % away", "Tgt % away"]] = np.nan  # only the trigger is pending before a fill

    def near(r):
        v = [r["Trig. % away"]] if r.Status == "Waiting" else [r["SL % away"], r["Tgt % away"]]
        v = [abs(x) for x in v if pd.notna(x)]
        return min(v) if v else np.inf
    return df.assign(_n=df.apply(near, axis=1)).sort_values("_n", kind="stable")[cols].reset_index(drop=True)


def notifications_panel(now, unread=None):
    """Home Notifications: the latest 30 paper-engine alerts (shared journal), unread since 'Mark all read'. unread is
    the bell's count (all rows above notif_seen_id, capped at 100), so the label, bell and caption agree."""
    try:
        al = paper.alerts_df(30)
    except Exception:
        al = pd.DataFrame(columns=["id"])
    seen = st.session_state.get("notif_seen_id", 0)
    if unread is None:
        try:
            unread = len(paper.alerts_since(seen, limit=100))
        except Exception:
            unread = 0
    n = int(unread)
    c1, c2 = st.columns([5, 2], vertical_alignment="center")
    c1.caption(f"{n if n < 100 else '99+'} unread · CALL/PUT phone alerts, paper entries, T1, exits and blocked signals (app or runner.py). The "
               "journal is shared, so every user sees every event. Toasts show while Home runs (turn Auto on).")
    c2.button("Mark all read", key="notif_read", on_click=_notif_read, disabled=not n, width="stretch")
    st.html(ui_kit.notif_list(al.to_dict("records"), seen, now.date().isoformat()))


TABS = ["🏠 Home", "📊 Signals board", "⭐ Watchlist", "📈 NIFTY 50", "✅ Track record", "⚡ Stock → CALL/PUT", "🌙 BTST & 2-day", "📅 Results", "🔎 Analysis", "🧠 Options analytics", "🌐 Market intel",
        "💰 CE/PE price check", "🧮 Spread builder", "📒 Paper trading & alerts", "🚀 Momentum scanner",
        "🎯 Position manager", "📖 Method"] + (["👥 Users"] if user["role"] == "admin" else [])
if st.session_state.get("simple_view"):  # phones: one compact screen instead of 20+ tabs
    st.fragment(simple_view, run_every=every if auto else None)()
    st.stop()
tabs = st.tabs(TABS)
with tabs[TABS.index("🏠 Home")]:
    st.fragment(home_tab, run_every=every if auto else None)()
with tabs[TABS.index("📊 Signals board")]:
    st.fragment(dashboard, run_every=every if auto else None)()
with tabs[TABS.index("⭐ Watchlist")]:
    watchlist_tab()
with tabs[TABS.index("📈 NIFTY 50")]:
    nifty50_tab()
with tabs[TABS.index("✅ Track record")]:
    track_tab()
with tabs[TABS.index("⚡ Stock → CALL/PUT")]:
    quick_tab()
with tabs[TABS.index("🌙 BTST & 2-day")]:
    horizons_tab()
with tabs[TABS.index("📅 Results")]:
    results_tab()
with tabs[TABS.index("🔎 Analysis")]:
    analysis_tab()
with tabs[TABS.index("🧠 Options analytics")]:
    options_tab()
with tabs[TABS.index("🌐 Market intel")]:
    market_tab()
with tabs[TABS.index("💰 CE/PE price check")]:
    price_check()
with tabs[TABS.index("🧮 Spread builder")]:
    spread_tab()
with tabs[TABS.index("📒 Paper trading & alerts")]:
    paper_tab(SYMBOLS)
with tabs[TABS.index("🚀 Momentum scanner")]:
    scanner()
with tabs[TABS.index("🎯 Position manager")]:
    position_manager()
if user["role"] == "admin":
    with tabs[TABS.index("👥 Users")]:
        users_tab()

with tabs[TABS.index("📖 Method")]:
    st.markdown("""
**Six parameter groups** feed the confluence score, each shown with its own sub-score:
1. **Moving averages** (trend direction): EMA9/21/50, VWAP, Supertrend, ADX/DMI, 15- and 60-min trend
2. **RSI** (momentum strength): RSI(14) range and slope, CCI
3. **MACD** (momentum change): histogram direction, signal and zero-line position
4. **Volume** (participation): relative-volume spikes, OBV, MFI
5. **Bollinger Bands & ATR** (volatility): band expansion, ATR expansion, squeeze; ATR also sizes every stop
6. **Support/resistance & structure**: opening range, previous-day high/low, CPR, pivots, swing highs/lows, HH/HL, candle patterns, room to the next level

**Pipeline.** A trade needs all of: (1) a named **setup** firing within the last *N* closed bars, (2) **confluence** votes agreeing with it
(threshold +1 in a Mixed regime), (3) the **15-min trend** not against it, (4) the **entry window**, (5) price not more than 2.5 ATR from EMA21,
and (6) the **confirmation filters** (sidebar): NIFTY 50 trend not against the trade, the stock leading (CALL) or lagging (PUT) NIFTY today,
and the daily close on the right side of its 20-day EMA. Each call shows its risk:reward and the level that invalidates it.
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
| Risk & sizing | fixed-fractional risk in R multiples, 50% at T1 (0.5R default) + breakeven, trail to 2R | V. K. Tharp, *Trade Your Way to Financial Freedom* (1998) |

**Known limits.** Yahoo intraday data can be delayed and NSE indices carry no volume. The NSE option-chain API is unofficial. MCX prices are
NYMEX × USDINR proxies, not MCX quotes. The backtest measures the underlying in R, not option P&L.
""")
st.caption("Rules: risk ≤1% of capital per trade, always use the stop, avoid result days and expiry-day lottery tickets, square off "
           "before 15:15 IST (23:00 MCX). Confirm every strike and premium on your broker's chain before trading.")
