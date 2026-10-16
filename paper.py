"""
Paper trading, trade journal and alerts. SQLite (trading.db) shared by the Streamlit app and runner.py;
UNIQUE keys make every write idempotent, so the app and the background runner can both run.

Trade rules mirror the backtest: enter on a signal at the live/model premium, book 50% at T1 and move the stop
to cost, exit the rest at T2, on the premium/underlying stop, on a Supertrend flip, or at square-off.
"""
import json
import os
import sqlite3
from datetime import datetime, time as dtime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import engine as E

DATA_DIR = Path(os.environ.get("TRAD_DATA_DIR") or Path(__file__).parent)  # server: a persistent volume
DB = DATA_DIR / "trading.db"
SETTINGS = DATA_DIR / "engine_settings.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, kind TEXT, setup TEXT, signal_bar TEXT, opened_at TEXT,
  strike REAL, expiry TEXT, dte0 REAL, cal REAL, lot INTEGER, qty INTEGER, conf INTEGER, priced TEXT,
  entry_u REAL, stop_u REAL, t1_u REAL, t2_u REAL, entry_p REAL, sl0 REAL, sl_p REAL, t1_p REAL, t2_p REAL,
  status TEXT DEFAULT 'OPEN', half INTEGER DEFAULT 0, half_p REAL, last_p REAL, last_u REAL, updated_at TEXT,
  exit_at TEXT, exit_p REAL, exit_reason TEXT, pnl REAL, r_mult REAL,
  UNIQUE(symbol, signal_bar));
CREATE TABLE IF NOT EXISTS recs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, day TEXT, symbol TEXT, kind TEXT, mode TEXT, action TEXT, strike REAL,
  prem REAL, sl_p REAL, t1_p REAL, t2_p REAL, u_entry REAL, u_sl REAL, u_t1 REAL, u_t2 REAL, conf INTEGER, score REAL,
  source TEXT, status TEXT DEFAULT 'PENDING', outcome TEXT, triggered_at TEXT, resolved_at TEXT, exit_u REAL,
  r_mult REAL, best_r REAL, worst_r REAL, evaluated_at TEXT, UNIQUE(day, symbol, kind, mode));
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, key TEXT UNIQUE, symbol TEXT, kind TEXT, text TEXT,
  sent INTEGER DEFAULT 0);
"""


def conn():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


# ---------------- Settings shared with the background runner ----------------
def save_settings(P, symbols, every=60, telegram=True):
    p = {k: (v.strftime("%H:%M") if isinstance(v, dtime) else sorted(v) if isinstance(v, set) else v) for k, v in P.items()}
    SETTINGS.write_text(json.dumps(dict(P=p, symbols=symbols, every=every, telegram=telegram,
                                        saved=datetime.now().isoformat(timespec="seconds")), indent=1))


def load_settings():
    j = json.loads(SETTINGS.read_text())
    P = j["P"]
    for k in ("win_start", "win_end"):
        P[k] = datetime.strptime(P[k], "%H:%M").time()
    P["setups"] = set(P["setups"])
    return j | {"P": P}


# ---------------- Paper engine ----------------
def _premium_now(a, t, now):
    """Live quote when the chain is on the trade's expiry, else Black-Scholes calibrated at entry."""
    chain = a.get("chain")
    if chain and chain.get("expiry") == t["expiry"]:
        live = E.live_premium(chain, t["strike"], t["kind"])
        if live:
            return live, "live"
    elapsed = (now - pd.Timestamp(t["opened_at"])).total_seconds() / 86400
    T = max((t["dte0"] or 3.0) - elapsed, 0.02) / 365
    r = 0.0 if a["spec"]["kind"] == "commodity" else 0.065
    return E.bs(a["ltp"], t["strike"], T, a["iv"], t["kind"], r)[0] * (t["cal"] or 1.0), "model"


INDEX_GROUP = {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}  # move together: same-direction trades are one bet


def risk_status(P, now):
    """Portfolio limits before opening anything new. -> dict(ok, reasons, pnl_today, open, limit)."""
    df = trades_df()
    today = now.date().isoformat()
    closed = df[(df.status == "CLOSED") & df.exit_at.fillna("").str.startswith(today)]
    opn = df[df.status == "OPEN"]
    unreal = float(np.where(opn.half == 1, (opn.half_p - opn.entry_p) * opn.qty / 2 + (opn.last_p - opn.entry_p) * opn.qty / 2,
                            (opn.last_p - opn.entry_p) * opn.qty).sum()) if len(opn) else 0.0
    pnl = float(closed.pnl.sum()) + unreal
    limit = P.get("capital", 0) * P.get("daily_loss_pct", 3.0) / 100
    reasons = []
    if limit and pnl <= -limit:
        reasons.append(f"Daily loss limit hit: ₹{pnl:,.0f} vs limit -₹{limit:,.0f}. No new trades today.")
    if len(opn) >= P.get("max_open", 4):
        reasons.append(f"{len(opn)} open positions = max {P.get('max_open', 4)}. Close one before adding more.")
    return dict(ok=not reasons, reasons=reasons, pnl_today=pnl, open=len(opn), limit=limit,
                open_index=[(r.symbol, r.kind) for r in opn.itertuples() if r.symbol in INDEX_GROUP])


def paper_step(results, P, now):
    """Manage open paper trades and open new ones from fresh signals. -> list of alert events."""
    ev, c = [], conn()
    stamp = now.isoformat(timespec="seconds")
    for sym, a in results.items():
        if "error" in a:
            continue
        row, ltp, spec = a["row"], a["ltp"], a["spec"]
        sq = E.SESSIONS[spec["sess"]][2]
        for t in c.execute("SELECT * FROM trades WHERE status='OPEN' AND symbol=?", (sym,)).fetchall():
            prem, how = _premium_now(a, t, now)
            dr = 1 if t["kind"] == "CE" else -1
            opened = pd.Timestamp(t["opened_at"])
            reason = None
            if prem <= t["sl_p"] or (ltp - t["stop_u"]) * dr <= 0:
                reason = "breakeven stop" if t["half"] else "stop"
            elif (ltp - t["t2_u"]) * dr >= 0 or prem >= t["t2_p"]:
                reason = "target T2"
            elif row.STdir != dr:
                reason = "Supertrend flip"
            elif now.time() >= sq or now.date() > opened.date():
                reason = "square-off"
            if not reason and not t["half"] and ((ltp - t["t1_u"]) * dr >= 0 or prem >= t["t1_p"]):
                cur = c.execute("UPDATE trades SET half=1, half_p=?, sl_p=MAX(sl_p, entry_p) WHERE id=? AND half=0",
                                (prem, t["id"]))
                if cur.rowcount:
                    ev.append((f"t1:{t['id']}", sym, "T1",
                               f"🟡 {sym} {t['strike']:g} {t['kind']}: T1 hit at {prem:.2f} (entry {t['entry_p']:.2f}). "
                               f"Booked 50%, stop moved to cost."))
            if reason:
                half = t["half"] or 0
                qty = t["qty"]
                half_p = t["half_p"] if half else None
                pnl = ((half_p - t["entry_p"]) * qty / 2 + (prem - t["entry_p"]) * qty / 2) if half \
                    else (prem - t["entry_p"]) * qty
                risk = max((t["entry_p"] - t["sl0"]) * qty, 1e-9)
                cur = c.execute("UPDATE trades SET status='CLOSED', exit_at=?, exit_p=?, exit_reason=?, pnl=?, r_mult=?, "
                                "last_p=?, last_u=?, updated_at=? WHERE id=? AND status='OPEN'",
                                (stamp, prem, reason, pnl, pnl / risk, prem, ltp, stamp, t["id"]))
                if cur.rowcount:
                    icon = "🟢" if pnl > 0 else "🔴"
                    ev.append((f"exit:{t['id']}", sym, "EXIT",
                               f"{icon} EXIT {sym} {t['strike']:g} {t['kind']} at {prem:.2f} ({how}): {reason}. "
                               f"P&L ₹{pnl:,.0f} ({pnl / risk:+.2f}R)"))
            else:
                c.execute("UPDATE trades SET last_p=?, last_u=?, updated_at=? WHERE id=?", (prem, ltp, stamp, t["id"]))
        p = a.get("plan")
        if not p:
            continue
        if c.execute("SELECT 1 FROM trades WHERE status='OPEN' AND symbol=?", (sym,)).fetchone():
            continue
        c.commit()
        rs = risk_status(P, now)
        blocked = list(rs["reasons"])
        if P.get("corr_guard", True) and sym in INDEX_GROUP and any(k == p["kind"] for _, k in rs["open_index"]):
            blocked.append(f"Already holding an index {p['kind']} ({', '.join(s for s, k in rs['open_index'])}): "
                           f"{sym} {p['kind']} would double the same bet")
        if any(sev == "high" and "result" in t.lower() for sev, t in a.get("events", [])):
            blocked.append(f"{sym} has results today/tomorrow: skipped to avoid IV crush")
        if blocked:
            ev.append((f"block:{sym}:{row.name}", sym, "BLOCKED", f"⛔ {a['status']} {sym} not taken: " + blocked[0]))
            continue
        n_today = c.execute("SELECT COUNT(*) FROM trades WHERE symbol=? AND substr(opened_at,1,10)=?",
                            (sym, now.date().isoformat())).fetchone()[0]
        if n_today >= P.get("max_day", 2):
            continue
        lots = max(p["lots"], 1)  # paper: always at least 1 lot so the journal accrues
        r = 0.0 if spec["kind"] == "commodity" else 0.065
        model = E.bs(ltp, p["strike"], p["dte"] / 365, a["iv"], p["kind"], r)[0]
        cur = c.execute(
            "INSERT OR IGNORE INTO trades(symbol, kind, setup, signal_bar, opened_at, strike, expiry, dte0, cal, lot, qty, "
            "conf, priced, entry_u, stop_u, t1_u, t2_u, entry_p, sl0, sl_p, t1_p, t2_p, last_p, last_u, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sym, p["kind"], row.Setups, str(row.name), stamp, p["strike"], p["expiry"], p["dte"],
             p["prem"] / model if model > 0.05 else 1.0, p["lot"], lots * p["lot"], a["conf"],
             "live" if p["live"] else "model", p["entry"], p["stop"], p["t1"], p["t2"], p["prem"], p["p_sl"],
             p["p_sl"], p["p_t1"], p["p_t2"], p["prem"], ltp, stamp))
        if cur.rowcount:
            ev.append((f"entry:{sym}:{row.name}", sym, "ENTRY",
                       f"{'📗' if p['kind'] == 'CE' else '📕'} {a['status']} {sym} {p['strike']:g} {p['kind']} "
                       f"@ {p['prem']:.2f} · SL {p['p_sl']:.2f} · T1 {p['p_t1']:.2f} · T2 {p['p_t2']:.2f} · "
                       f"{lots} lot · {row.Setups} · conf {a['conf']}"))
    c.commit()
    c.close()
    return ev


def close_all(results, now, reason="manual close"):
    """Close every open paper trade at its current premium."""
    c = conn()
    stamp = now.isoformat(timespec="seconds")
    n = 0
    for t in c.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall():
        a = results.get(t["symbol"])
        prem = _premium_now(a, t, now)[0] if a and "error" not in a else (t["last_p"] or t["entry_p"])
        qty, half = t["qty"], t["half"] or 0
        pnl = ((t["half_p"] - t["entry_p"]) * qty / 2 + (prem - t["entry_p"]) * qty / 2) if half else (prem - t["entry_p"]) * qty
        risk = max((t["entry_p"] - t["sl0"]) * qty, 1e-9)
        n += c.execute("UPDATE trades SET status='CLOSED', exit_at=?, exit_p=?, exit_reason=?, pnl=?, r_mult=? "
                       "WHERE id=? AND status='OPEN'", (stamp, prem, reason, pnl, pnl / risk, t["id"])).rowcount
    c.commit()
    c.close()
    return n


def trades_df():
    c = conn()
    df = pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC", c)
    c.close()
    num = ["strike", "dte0", "cal", "lot", "qty", "conf", "entry_u", "stop_u", "t1_u", "t2_u", "entry_p", "sl0", "sl_p",
           "t1_p", "t2_p", "half", "half_p", "last_p", "last_u", "exit_p", "pnl", "r_mult"]
    df[num] = df[num].apply(pd.to_numeric, errors="coerce")
    df["half"] = df.half.fillna(0).astype(int)
    return df


def stats(closed):
    if closed.empty:
        return None
    r = closed.r_mult
    loss = -closed.pnl[closed.pnl < 0].sum()
    eq = closed.sort_values("exit_at").pnl.cumsum()
    return dict(trades=len(closed), win=(closed.pnl > 0).mean(), exp=r.mean(), pnl=closed.pnl.sum(),
                pf=closed.pnl[closed.pnl > 0].sum() / loss if loss > 0 else float("inf"),
                dd=float((eq - eq.cummax()).min()), best=closed.pnl.max(), worst=closed.pnl.min())


def reset_journal():
    c = conn()
    c.execute("DELETE FROM trades")
    c.execute("DELETE FROM alerts")
    c.commit()
    c.close()


# ---------------- Recommendation track record ----------------
FINAL = ("T2 HIT", "T1 HIT", "STOP HIT", "NOT TRIGGERED", "SQUARED OFF")


def log_recs(items, now, source):
    """items: [(symbol, recommendation dict)]. Logs the first BUY CALL/PUT (now or trigger) per symbol/side/mode/day."""
    c = conn()
    stamp, day = now.isoformat(timespec="seconds"), now.date().isoformat()
    n = 0
    for sym, r in items:
        if not r or r.get("mode") not in ("now", "trigger"):
            continue
        n += c.execute(
            "INSERT OR IGNORE INTO recs(ts, day, symbol, kind, mode, action, strike, prem, sl_p, t1_p, t2_p, u_entry, u_sl, "
            "u_t1, u_t2, conf, score, source, status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (stamp, day, sym, r["kind"], r["mode"], r["action"], r["strike"], r["prem"], r["sl"], r["t1"], r["t2"],
             r["u_entry"], r["u_sl"], r["u_t1"], r["u_t2"], r.get("conf"), r.get("score"), source,
             "OPEN" if r["mode"] == "now" else "WAITING")).rowcount
    c.commit()
    c.close()
    return n


def _judge(rec, bars, sq_min):
    """Walk bars after the recommendation: trigger (for conditional plans), then stop vs T1/T2 (stop first when both
    touch in one bar: conservative). -> dict of status fields."""
    dr = 1 if rec["kind"] == "CE" else -1
    entry, sl, t1, t2 = rec["u_entry"], rec["u_sl"], rec["u_t1"], rec["u_t2"]
    risk = max(abs(entry - sl), 1e-9)
    t0 = pd.Timestamp(rec["ts"])
    b = bars[bars.index >= t0.floor("5min")]
    b = b[[d == t0.date() for d in b.index.date]]  # intraday: same session only
    triggered = rec["mode"] == "now"
    trig_at, best, worst, hit1 = (rec["ts"] if triggered else None), 0.0, 0.0, None
    for ts, x in b.iterrows():
        mins = ts.hour * 60 + ts.minute
        if not triggered:
            if (x.Close - entry) * dr >= 0 and mins < sq_min:
                triggered, trig_at = True, ts.isoformat()
            continue
        fav, adv = (x.High, x.Low) if dr > 0 else (x.Low, x.High)
        best, worst = max(best, (fav - entry) * dr / risk), min(worst, (adv - entry) * dr / risk)
        if hit1 and (adv - entry) * dr <= 0:
            break  # after T1 the stop sits at cost: T1 success stands
        if (adv - sl) * dr <= 0:
            return dict(status="STOP HIT", outcome="❌ Failed: stop hit", triggered_at=trig_at, resolved_at=ts.isoformat(),
                        exit_u=sl, r_mult=-1.0, best_r=best, worst_r=worst)
        if (fav - t2) * dr >= 0:
            return dict(status="T2 HIT", outcome="✅✅ Success: target 2", triggered_at=trig_at, resolved_at=ts.isoformat(),
                        exit_u=t2, r_mult=(t2 - entry) * dr / risk, best_r=best, worst_r=worst)
        if hit1 is None and (fav - t1) * dr >= 0:
            hit1 = ts.isoformat()  # success; keep walking for T2 within the session
        if mins >= sq_min:
            break
    session_over = bool(len(b)) and (b.index[-1].hour * 60 + b.index[-1].minute) >= sq_min - 5
    if hit1:
        return dict(status="T1 HIT", outcome="✅ Success: target 1", triggered_at=trig_at, resolved_at=hit1, exit_u=t1,
                    r_mult=(t1 - entry) * dr / risk, best_r=best, worst_r=worst)
    if not triggered:
        return dict(status="NOT TRIGGERED" if session_over else "WAITING",
                    outcome="⏸ Trigger never reached" if session_over else "⏳ Waiting for trigger",
                    triggered_at=None, resolved_at=None, exit_u=None, r_mult=None, best_r=None, worst_r=None)
    last = float(b.Close.iloc[-1]) if len(b) else entry
    r_now = (last - entry) * dr / risk
    if session_over:
        return dict(status="SQUARED OFF", outcome=f"{'🟢' if r_now > 0 else '🔴'} Squared off {r_now:+.2f}R",
                    triggered_at=trig_at, resolved_at=b.index[-1].isoformat(), exit_u=last, r_mult=r_now,
                    best_r=best, worst_r=worst)
    return dict(status="OPEN", outcome=f"⏳ Running {r_now:+.2f}R", triggered_at=trig_at, resolved_at=None, exit_u=last,
                r_mult=r_now, best_r=best, worst_r=worst)


def evaluate_recs(get_bars, now, days=7):
    """Re-check every unresolved recommendation of the last `days` days. get_bars(symbol) -> 5-min OHLC (IST)."""
    c = conn()
    since = (now - pd.Timedelta(days=days)).date().isoformat()
    todo = c.execute(f"SELECT * FROM recs WHERE day>=? AND status NOT IN ({','.join('?' * len(FINAL))})",
                     (since, *FINAL)).fetchall()
    cache, n = {}, 0
    for rec in todo:
        sym = rec["symbol"]
        try:
            if sym not in cache:
                cache[sym] = get_bars(sym)
            bars, sq_min = cache[sym]
            if bars is None or bars.empty:
                continue
            res = _judge(dict(rec), bars, sq_min)
        except Exception:
            continue
        c.execute("UPDATE recs SET status=?, outcome=?, triggered_at=?, resolved_at=?, exit_u=?, r_mult=?, best_r=?, "
                  "worst_r=?, evaluated_at=? WHERE id=?",
                  (res["status"], res["outcome"], res["triggered_at"], res["resolved_at"], res["exit_u"], res["r_mult"],
                   res["best_r"], res["worst_r"], now.isoformat(timespec="seconds"), rec["id"]))
        n += 1
    c.commit()
    c.close()
    return n


def recs_df(days=30):
    c = conn()
    df = pd.read_sql_query("SELECT * FROM recs WHERE day>=? ORDER BY ts DESC", c,
                           params=((datetime.now() - pd.Timedelta(days=days)).date().isoformat(),))
    c.close()
    return df


# ---------------- Alerts ----------------
def telegram_creds():
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    try:
        import streamlit as st
        tok = tok or st.secrets["telegram"]["bot_token"]
        chat = chat or str(st.secrets["telegram"]["chat_id"])
    except Exception:
        pass
    return tok, chat


def send_telegram(text):
    tok, chat = telegram_creds()
    if not tok or not chat:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=8)
        return r.ok
    except Exception:
        return False


def notify(events, send=True):
    """Log each event once (UNIQUE key) and push new ones to Telegram. -> newly logged events."""
    if not events:
        return []
    c, new = conn(), []
    stamp = datetime.now().isoformat(timespec="seconds")
    for key, sym, kind, text in events:
        cur = c.execute("INSERT OR IGNORE INTO alerts(ts, key, symbol, kind, text) VALUES(?,?,?,?,?)",
                        (stamp, key, sym, kind, text))
        if cur.rowcount:
            new.append((key, sym, kind, text))
            if send and send_telegram(text):
                c.execute("UPDATE alerts SET sent=1 WHERE key=?", (key,))
    c.commit()
    c.close()
    return new


def alerts_df(limit=100):
    c = conn()
    df = pd.read_sql_query("SELECT ts, symbol, kind, text, sent FROM alerts ORDER BY id DESC LIMIT ?", c, params=(limit,))
    c.close()
    return df
