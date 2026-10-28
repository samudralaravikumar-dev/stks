"""
Paper trading, trade journal and alerts. One database shared by the Streamlit app and runner.py: trading.db, or the
shared Postgres (Neon) when configured (db.py). UNIQUE keys make every write idempotent, so several copies can run.

Trade rules mirror the backtest: enter on a signal at the live/model premium, book 50% at T1 and move the stop
to cost, exit the rest at T2, on the premium/underlying stop, on a Supertrend flip, or at square-off.
"""
import json
import os
import threading
import time
from datetime import date, datetime, time as dtime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import db
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


_MIGRATED = set()
NEW_COLS = [("recs", "grade", "TEXT"), ("recs", "p_t1", "REAL"),  # calibration
            ("trades", "proof", "INTEGER"),
            ("recs", "expiry", "TEXT"), ("recs", "ikey", "TEXT"),  # the exact contract, for real-price scoring
            ("recs", "real_entry", "REAL"), ("recs", "real_exit", "REAL"), ("recs", "real_pct", "REAL")]  # 1 = TRADE call (real-money limits), 0 = PAPER call (tracked only)  # added after the first release: calibration


def conn():
    """The journal database: the shared Postgres (Neon) when configured, else trading.db (see db.py)."""
    c = db.connect(DB, timeout=15)
    c.executescript(SCHEMA)
    kind = "pg" if db.is_pg(c) else str(DB)
    if kind not in _MIGRATED:  # older databases: add the newer columns once per process
        for t, col, typ in NEW_COLS:
            try:
                if db.is_pg(c):
                    c.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {col} {'DOUBLE PRECISION' if typ == 'REAL' else typ}")
                elif col not in [r[1] for r in c.execute(f"PRAGMA table_info({t})")]:
                    c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {typ}")
            except Exception:
                pass
        _MIGRATED.add(kind)
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


def unrealized(df):
    """Open-trade MTM ₹ per row (trades_df rows): after T1 half is booked at half_p, the rest marks at last_p."""
    if df is None or not len(df):
        return pd.Series(dtype=float)
    return pd.Series(np.where(df.half == 1, (df.half_p - df.entry_p) * df.qty / 2 + (df.last_p - df.entry_p) * df.qty / 2,
                              (df.last_p - df.entry_p) * df.qty), index=df.index, dtype=float)


RISK_DEFAULTS = dict(daily_loss_pct=2.0, max_open=3, max_trades_day=3, max_consec_loss=2, weekly_loss_pct=5.0)
PAPER_LIMITS = dict(max_open_paper=6, max_paper_day=8)  # keeps the PAPER journal readable; no money is at stake


def risk_status(P, now, scope="trade"):
    """Portfolio limits before opening anything new, from the app's trade journal: daily loss cap, max open positions,
    max new trades per day, stop after N losses in a row today, and half size after a bad week.
    scope "trade": only positions from TRADE calls count (the real-money limits; journal rows from before the
    TRADE/PAPER flag count too). PAPER calls never use up these slots: on 22 Oct three PAPER positions blocked the
    day's only TRADE call. scope "paper": PAPER positions against their own, looser caps.
    -> dict(ok, reasons, pnl_today, open, limit, left, trades_today, streak, week_pnl, size_factor, open_index)."""
    g = lambda k: P.get(k, RISK_DEFAULTS[k])
    df = trades_df()
    is_paper = df.proof.fillna(1) == 0
    df = df[is_paper] if scope == "paper" else df[~is_paper]
    today = now.date().isoformat()
    monday = (now - pd.Timedelta(days=now.weekday())).date().isoformat()
    ex = df.exit_at.fillna("")
    closed = df[(df.status == "CLOSED") & ex.str.startswith(today)]
    week = df[(df.status == "CLOSED") & (ex.str[:10] >= monday)]
    opn = df[df.status == "OPEN"]
    unreal = float(unrealized(opn).sum())
    pnl = float(closed.pnl.sum()) + unreal
    week_pnl = float(week.pnl.sum()) + unreal
    cap = P.get("capital", 0)
    limit = cap * g("daily_loss_pct") / 100
    n_today = int(df.opened_at.fillna("").str.startswith(today).sum())
    streak = 0
    for v in closed.sort_values("exit_at", ascending=False).pnl:
        if v >= 0:
            break
        streak += 1
    reasons = []
    if scope == "paper":
        if len(opn) >= P.get("max_open_paper", PAPER_LIMITS["max_open_paper"]):
            reasons.append(f"{len(opn)} open PAPER positions = max {P.get('max_open_paper', 6)}")
        if n_today >= P.get("max_paper_day", PAPER_LIMITS["max_paper_day"]):
            reasons.append(f"{n_today} PAPER trades today = max {P.get('max_paper_day', 8)}")
        return dict(ok=not reasons, reasons=reasons, pnl_today=pnl, open=len(opn), limit=0, left=None,
                    trades_today=n_today, streak=streak, week_pnl=week_pnl, week_limit=0, size_factor=1.0,
                    open_index=[(r.symbol, r.kind) for r in opn.itertuples() if r.symbol in INDEX_GROUP])
    if limit and pnl <= -limit:
        reasons.append(f"Daily loss limit hit: ₹{pnl:,.0f} vs limit -₹{limit:,.0f}. No new trades today.")
    if len(opn) >= g("max_open"):
        reasons.append(f"{len(opn)} open positions = max {g('max_open')}. Close one before adding more.")
    if n_today >= g("max_trades_day"):
        reasons.append(f"{n_today} trades today = max {g('max_trades_day')}. Done for the day.")
    if streak >= g("max_consec_loss"):
        reasons.append(f"{streak} losses in a row today: stop for the day (no revenge trades).")
    wk = cap * g("weekly_loss_pct") / 100
    size_factor = 0.5 if wk and week_pnl <= -wk else 1.0
    return dict(ok=not reasons, reasons=reasons, pnl_today=pnl, open=len(opn), limit=limit,
                left=max(limit + min(pnl, 0), 0) if limit else None, trades_today=n_today, streak=streak,
                week_pnl=week_pnl, week_limit=wk, size_factor=size_factor,
                open_index=[(r.symbol, r.kind) for r in opn.itertuples() if r.symbol in INDEX_GROUP])


def risk_controls(P, now):
    """Settings the engine sizes with: size_factor (0.5 after a bad week) and halt (reason no new trade is allowed)."""
    try:
        rs = risk_status(P, now)
    except Exception:
        return dict(size_factor=1.0, halt=None)
    return dict(size_factor=rs["size_factor"], halt=rs["reasons"][0] if rs["reasons"] else None)


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
        try:  # TRADE calls use the real-money limits; PAPER calls are journalled under their own caps
            real = bool(E.proof(a, dict(sym=spec["name"], kind=p["kind"]))["trade"]) or not P.get("proof_gate", True)
        except Exception:
            real = True
        rs = risk_status(P, now, "trade" if real else "paper")
        blocked = list(rs["reasons"])
        if real and P.get("corr_guard", True) and sym in INDEX_GROUP and any(k == p["kind"] for _, k in rs["open_index"]):
            blocked.append(f"Already holding an index {p['kind']} ({', '.join(s for s, k in rs['open_index'])}): "
                           f"{sym} {p['kind']} would double the same bet")
        if any(sev == "high" and "result" in t.lower() for sev, t in a.get("events", [])):
            blocked.append(f"{sym} has results today/tomorrow: skipped to avoid IV crush")
        if blocked:
            if real:  # a skipped PAPER entry is not worth a phone alert
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
            "conf, priced, entry_u, stop_u, t1_u, t2_u, entry_p, sl0, sl_p, t1_p, t2_p, last_p, last_u, updated_at, proof) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sym, p["kind"], row.Setups, str(row.name), stamp, p["strike"], p["expiry"], p["dte"],
             p["prem"] / model if model > 0.05 else 1.0, p["lot"], lots * p["lot"], a["conf"],
             "live" if p["live"] else "model", p["entry"], p["stop"], p["t1"], p["t2"], p["prem"], p["p_sl"],
             p["p_sl"], p["p_t1"], p["p_t2"], p["prem"], ltp, stamp, int(real)))
        if cur.rowcount:
            ev.append((f"entry:{sym}:{row.name}", sym, "ENTRY",
                       ("" if real else "📝 PAPER · ") + f"{'📗' if p['kind'] == 'CE' else '📕'} {a['status']} {sym} {p['strike']:g} {p['kind']} "
                       f"@ {p['prem']:.2f} · SL {p['p_sl']:.2f} · T1 {p['p_t1']:.2f} · T2 {p['p_t2']:.2f} · "
                       f"{lots} lot · {row.Setups} · conf {a['conf']}"))
    c.commit()
    c.close()
    return ev


def _close_row(c, t, results, stamp, now, reason):
    """Close one OPEN trade row at its current premium (live/model from the analysis, else last/entry premium).
    Idempotent (WHERE status='OPEN'). -> (rowcount, pnl, prem, how)."""
    a = (results or {}).get(t["symbol"])
    if a and "error" not in a:
        prem, how = _premium_now(a, t, now)
    else:
        prem, how = (t["last_p"] or t["entry_p"]), "last"
    qty, half = t["qty"], t["half"] or 0
    pnl = ((t["half_p"] - t["entry_p"]) * qty / 2 + (prem - t["entry_p"]) * qty / 2) if half else (prem - t["entry_p"]) * qty
    risk = max((t["entry_p"] - t["sl0"]) * qty, 1e-9)
    last_u = a["ltp"] if a and "error" not in a else t["last_u"]
    n = c.execute("UPDATE trades SET status='CLOSED', exit_at=?, exit_p=?, exit_reason=?, pnl=?, r_mult=?, last_p=?, "
                  "last_u=?, updated_at=? WHERE id=? AND status='OPEN'",
                  (stamp, prem, reason, pnl, pnl / risk, prem, last_u, stamp, t["id"])).rowcount
    return n, pnl, prem, how


def close_all(results, now, reason="manual close"):
    """Close every open paper trade at its current premium."""
    c = conn()
    stamp = now.isoformat(timespec="seconds")
    n = sum(_close_row(c, t, results, stamp, now, reason)[0]
            for t in c.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall())
    c.commit()
    c.close()
    return n


def close_trade(trade_id, results, now, reason="manual exit (paper)"):
    """Paper exit of one open trade (no broker order). Logs an EXIT alert once. -> dict(id, symbol, pnl, prem, text)
    or None when the trade is not open (already closed by the engine or another session)."""
    c = conn()
    t = c.execute("SELECT * FROM trades WHERE id=? AND status='OPEN'", (int(trade_id),)).fetchone()
    out = None
    if t:
        n, pnl, prem, how = _close_row(c, t, results, now.isoformat(timespec="seconds"), now, reason)
        if n == 1:
            risk = max((t["entry_p"] - t["sl0"]) * t["qty"], 1e-9)
            text = (f"{'🟢' if pnl > 0 else '🔴'} EXIT {t['symbol']} {t['strike']:g} {t['kind']} at {prem:.2f} ({how}): "
                    f"{reason}. P&L ₹{pnl:,.0f} ({pnl / risk:+.2f}R)")
            out = dict(id=t["id"], symbol=t["symbol"], pnl=pnl, prem=prem, text=text)
    c.commit()
    c.close()
    if out:
        notify([(f"exit:{out['id']}", out["symbol"], "EXIT", out["text"])], send=False)
    return out


def trades_df():
    c = conn()
    df = db.read_df(c, "SELECT * FROM trades ORDER BY id DESC")
    c.close()
    if "proof" not in df.columns:
        df["proof"] = np.nan
    num = ["proof", "strike", "dte0", "cal", "lot", "qty", "conf", "entry_u", "stop_u", "t1_u", "t2_u", "entry_p", "sl0", "sl_p",
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
        if not r or r.get("mode") not in ("now", "trigger", "blocked"):
            continue
        n += c.execute(
            "INSERT OR IGNORE INTO recs(ts, day, symbol, kind, mode, action, strike, prem, sl_p, t1_p, t2_p, u_entry, u_sl, "
            "u_t1, u_t2, conf, score, source, status, grade, p_t1, expiry, ikey) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (stamp, day, sym, r["kind"], r["mode"], r["action"], r["strike"], r["prem"], r["sl"], r["t1"], r["t2"],
             r["u_entry"], r["u_sl"], r["u_t1"], r["u_t2"], r.get("conf"), r.get("score"), source,
             "WAITING" if r["mode"] == "trigger" else "OPEN", (r.get("cred") or {}).get("grade"),
             (r.get("cred") or {}).get("p_t1"), r.get("expiry"), r.get("ikey"))).rowcount
    c.commit()
    c.close()
    return n


_RESOLVED = {"t": 0.0, "rows": []}
_RESOLVED_LOCK = threading.Lock()
RESOLVED_TTL_S = 60


def _resolved(days=60):
    """Every resolved app call of the last `days` days in ONE query, reused for a minute: the credibility grade and the
    TRADE/PAPER gate ask for each symbol on every refresh (one query each was slow over a remote database)."""
    with _RESOLVED_LOCK:
        if time.time() - _RESOLVED["t"] > RESOLVED_TTL_S or _RESOLVED.get("days") != days:
            c = conn()
            since = (pd.Timestamp.now() - pd.Timedelta(days=days)).date().isoformat()
            rows = c.execute("SELECT symbol, kind, status, r_mult, real_pct FROM recs WHERE day>=? AND mode IN ('now','trigger') "
                             "AND status IN ('T1 HIT','T2 HIT','STOP HIT','SQUARED OFF') ORDER BY ts DESC",
                             (since,)).fetchall()
            c.close()
            _RESOLVED.update(t=time.time(), days=days, rows=[dict(r) for r in rows])
        return _RESOLVED["rows"]


def live_record(sym, kind, days=60):
    """-> (target hits, resolved triggered calls) for one symbol/side over the last `days` days."""
    rows = [r for r in _resolved(days) if r["symbol"] == sym and r["kind"] == kind]
    return sum(r["status"] in ("T1 HIT", "T2 HIT") for r in rows), len(rows)


def live_stats(sym, kind, days=60, last=5):
    """Forward record of this app's own calls on one symbol/side.
    -> dict(n, hits, avg_r, last_n, last_avg_r, real_n, real_avg): real_* = calls scored on the real option contract."""
    rows = [x for x in _resolved(days) if x["symbol"] == sym and x["kind"] == kind]  # newest first
    r = [float(x["r_mult"]) for x in rows if x["r_mult"] is not None]
    rp = [float(x["real_pct"]) for x in rows if x.get("real_pct") is not None]
    tail = r[:last]
    return dict(n=len(rows), hits=sum(x["status"] in ("T1 HIT", "T2 HIT") for x in rows),
                avg_r=sum(r) / len(r) if r else None, last_n=len(tail), last_avg_r=sum(tail) / len(tail) if tail else None,
                real_n=len(rp), real_avg=sum(rp) / len(rp) if rp else None)


def scorecard(days=28):
    """Weekly scorecard of resolved calls: by week, side, symbol and grade-free source. -> dict of DataFrames."""
    df = recs_df(days)
    df = df[df["mode"].isin(["now", "trigger"]) & df.status.isin(["T1 HIT", "T2 HIT", "STOP HIT", "SQUARED OFF"])].copy()
    if df.empty:
        return {}
    df["r_mult"] = pd.to_numeric(df.r_mult, errors="coerce")
    df["win"] = df.r_mult > 0
    df["week"] = pd.to_datetime(df.day).dt.to_period("W-SUN").dt.start_time.dt.date
    df["real_pct"] = pd.to_numeric(df["real_pct"], errors="coerce") if "real_pct" in df.columns else np.nan
    agg = lambda g: g.agg(calls=("r_mult", "size"), win_pct=("win", "mean"), avg_r=("r_mult", "mean"),
                          total_r=("r_mult", "sum"), real_n=("real_pct", "count"),
                          real_avg=("real_pct", "mean")).reset_index()
    out = {k: agg(df.groupby(k)) for k in ("week", "kind", "symbol")}
    for v in out.values():
        v["win_pct"] = v.win_pct * 100
    out["symbol"] = out["symbol"].sort_values("total_r", ascending=False)
    # calibration: do calls labelled "T1 odds 60%" really reach T1 about 60% of the time?
    if "p_t1" in df.columns:
        x = df.dropna(subset=["p_t1"]).copy()
        if len(x):
            x["p_t1"] = pd.to_numeric(x.p_t1, errors="coerce")
            x["hit"] = x.status.isin(["T1 HIT", "T2 HIT"])
            x["band"] = pd.cut(x.p_t1, [0, 0.4, 0.55, 0.7, 1.0], labels=["under 40%", "40-55%", "55-70%", "over 70%"])
            cal = x.groupby("band", observed=True).agg(calls=("hit", "size"), said=("p_t1", "mean"),
                                                       actual=("hit", "mean")).reset_index()
            cal[["said", "actual"]] *= 100
            out["calibration"] = cal
            g = x.groupby("grade").agg(calls=("hit", "size"), t1_hit_pct=("hit", "mean"),
                                       avg_r=("r_mult", "mean")).reset_index()
            g["t1_hit_pct"] *= 100
            out["grade"] = g
    return out


def add_broker_call(sym, side, entry, sl, t1, t2, source, now):
    """Track an external intraday call (broker desk, TV, newsletter) against live prices with the same judge as the app's
    own calls. ICICI-style: counted only if price trades within +/-0.25% of the entry ('not triggered' otherwise).
    side 'BUY' / 'SELL'. -> True if added (one call per symbol/side/source/day)."""
    src = "".join(ch for ch in str(source).strip() if ch.isalnum() or ch in " -_.&")[:40] or "unnamed"
    c = conn()
    n = c.execute(
        "INSERT OR IGNORE INTO recs(ts, day, symbol, kind, mode, action, u_entry, u_sl, u_t1, u_t2, source, status) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (now.isoformat(timespec="seconds"), now.date().isoformat(), sym.upper(), "CE" if side == "BUY" else "PE",
         f"broker:{src}", f"{side} · {src}", float(entry), float(sl), float(t1), float(t2 or t1), f"broker:{src}",
         "WAITING")).rowcount
    c.commit()
    c.close()
    return bool(n)


def _judge(rec, bars, sq_min):
    """Walk bars after the recommendation: trigger (for conditional plans), then stop vs T1/T2 (stop first when both
    touch in one bar: conservative). -> dict of status fields."""
    dr = 1 if rec["kind"] == "CE" else -1
    entry, sl, t1, t2 = rec["u_entry"], rec["u_sl"], rec["u_t1"], rec["u_t2"]
    risk = max(abs(entry - sl), 1e-9)
    t0 = pd.Timestamp(rec["ts"])
    b = bars[bars.index >= t0.floor("5min")]
    b = b[[d == t0.date() for d in b.index.date]]  # intraday: same session only
    triggered = rec["mode"] in ("now", "blocked")
    trig_at, best, worst, hit1 = (rec["ts"] if triggered else None), 0.0, 0.0, None
    for ts, x in b.iterrows():
        mins = ts.hour * 60 + ts.minute
        if not triggered:
            if str(rec["mode"]).startswith("broker"):  # entered only if price trades within +/-0.25% of the entry
                hit = x.Low <= entry * 1.0025 and x.High >= entry * 0.9975
            else:
                hit = (x.Close - entry) * dr >= 0
            if hit and mins < sq_min:
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
    try:
        score_real(now, days)
    except Exception:
        pass
    return n


REAL_COST_PCT = 1.5  # round-trip costs + slippage, % of premium (as in the real-price back-tests)


def _bar_at(c, ts):
    """The 15-minute option candle that contains timestamp ts (IST), or None."""
    t = pd.Timestamp(ts)
    t = t.tz_localize(E.IST) if t.tzinfo is None else t.tz_convert(E.IST)
    x = c[c.index <= t]
    return x.iloc[-1] if len(x) and t - x.index[-1] < pd.Timedelta(minutes=15) else None


def score_real(now, days=7):
    """Score resolved calls on the REAL price of the exact option contract (Upstox candles): bought at the open of
    the bar when the call triggered, sold when its outcome happened (stop: that bar's low; target or square-off:
    that bar's close), minus REAL_COST_PCT. Only past days (their candles are final). -> calls scored."""
    if not E.UPSTOX:
        return 0
    import optdata  # lazy: optdata imports engine
    c = conn()
    since = (now - pd.Timedelta(days=days)).date().isoformat()
    todo = c.execute("SELECT * FROM recs WHERE day>=? AND day<? AND ikey IS NOT NULL AND real_pct IS NULL AND "
                     "triggered_at IS NOT NULL AND resolved_at IS NOT NULL AND status IN "
                     "('T1 HIT','T2 HIT','STOP HIT','SQUARED OFF')", (since, now.date().isoformat())).fetchall()
    today, n = now.date(), 0
    for rec in todo:
        try:
            day = date.fromisoformat(rec["day"])
            exp = datetime.strptime(rec["expiry"], "%d-%b-%Y").date() if rec["expiry"] else None
            bars = optdata.candles(E.UPSTOX, rec["ikey"], day, bool(exp and exp < today))
            if bars is None or bars.empty:
                continue
            b0, b1 = _bar_at(bars, rec["triggered_at"]), _bar_at(bars, rec["resolved_at"])
            if b0 is None or b1 is None or b0.Open <= 0:
                continue
            entry = float(b0.Open)
            exit_ = float(b1.Low) if rec["status"] == "STOP HIT" else float(b1.Close)
            pct = (exit_ - entry) / entry * 100 - REAL_COST_PCT
            c.execute("UPDATE recs SET real_entry=?, real_exit=?, real_pct=? WHERE id=?",
                      (round(entry, 2), round(exit_, 2), round(pct, 2), rec["id"]))
            n += 1
        except Exception:
            continue
    c.commit()
    c.close()
    return n


def recs_df(days=30):
    c = conn()
    df = db.read_df(c, "SELECT * FROM recs WHERE day>=? ORDER BY ts DESC",
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


def _secret(section, key):
    try:
        import streamlit as st
        return st.secrets[section].get(key)
    except Exception:
        return None


def ntfy_target():
    """(server, topic) for ntfy.sh phone push ([ntfy] topic / env NTFY_TOPIC), or (None, None)."""
    topic = os.environ.get("NTFY_TOPIC") or _secret("ntfy", "topic")
    server = os.environ.get("NTFY_SERVER") or _secret("ntfy", "server") or "https://ntfy.sh"
    return (server.rstrip("/"), topic) if topic else (None, None)


def portal_url():
    return (os.environ.get("PORTAL_URL") or _secret("app", "url") or "https://srktrade.streamlit.app").rstrip("/")


def call_links(sym):
    """(app link, web link) that open this symbol's call: srkoptions:// opens the Android app, https the browser."""
    from urllib.parse import quote
    q = quote(str(sym))
    return f"srkoptions://open?sym={q}", f"{portal_url()}/?sym={q}"


def send_ntfy(text, title=None, tags=None, priority=None, sym=None):
    """Phone push through ntfy (install the ntfy app and subscribe to the topic). True when delivered.
    sym: tapping the notification opens that symbol's call in the SRK app; a button opens it in the browser."""
    server, topic = ntfy_target()
    if not topic:
        return False
    hdr = {k: v for k, v in (("Title", title), ("Tags", tags), ("Priority", priority)) if v}
    if sym:
        app, web = call_links(sym)
        hdr.update({"Click": app, "Actions": f"view, Open in browser, {web}"})
    try:
        r = requests.post(f"{server}/{topic}", data=text.encode("utf-8"),
                          headers={k: v.encode("utf-8").decode("latin-1") for k, v in hdr.items()}, timeout=8)
        return r.ok
    except Exception:
        return False


def push(text, title=None, tags=None, priority=None, sym=None):
    """Send to every configured channel (Telegram and/or ntfy). True if any delivered."""
    tg = text if not title else f"{title}\n{text}"
    a = send_telegram(tg + (f"\nOpen: {call_links(sym)[1]}" if sym else ""))
    b = send_ntfy(text, title, tags, priority, sym)
    return a or b


ALERT_TAGS = {"SIGNAL": "chart_with_upwards_trend", "ENTRY": "inbox_tray", "T1": "dart", "EXIT": "outbox_tray",
              "NEWS": "newspaper", "HEALTH": "warning", "ALIVE": "white_check_mark"}
LINKED = {"SIGNAL", "ENTRY", "T1", "EXIT", "BLOCKED", "HORIZON", "NEWS"}  # alerts about one symbol: tap opens its call


def push_signals(items, now, min_grade="C", send=True):
    """items: [(symbol, recommend() dict)]. One phone alert per symbol/side/day for each enter-now BUY CALL / BUY PUT
    whose credibility grade is min_grade or better ("D" = every signal). -> newly logged events."""
    rank = E.CRED_RANK
    day = now.date().isoformat()
    ev = []
    for sym, r in items:
        c = (r or {}).get("cred")
        if not r or r.get("mode") != "now" or not c or rank[c["grade"]] > rank.get(min_grade, 2) or r.get("allow_block"):
            continue
        ce = r["kind"] == "CE"
        rr = r.get("rr")
        text = (("" if not r.get("proof") or r["proof"]["trade"] else "📝 PAPER · ")
                + f"{'🟢 BUY CALL' if ce else '🔴 BUY PUT'} {sym} {r['strike']:g} {r['kind']}"
                + (f" ({r['expiry']})" if r.get("expiry") else "")
                + f"\nEntry ₹{r['prem']:.2f} · SL ₹{r['sl']:.2f} · T1 ₹{r['t1']:.2f} · T2 ₹{r['t2']:.2f}"
                + ("" if r.get("live") else " (model premium)")
                + f"\nUnderlying {r['u_entry']:,.2f} → SL {r['u_sl']:,.2f} · T1 {r['u_t1']:,.2f} · T2 {r['u_t2']:,.2f}"
                + (f"\nR:R 1:{rr[0]:.1f} / 1:{rr[1]:.1f}" if rr else "")
                + (f"\nLimit buy ≤ ₹{r['limit']:.2f} (spread {r.get('spread_pct', 0):.1f}%"
                   + (")" if r.get("spread_ok", True) else ", wide: skip or wait)") if r.get("limit") else "")
                + (f"\nSize: {r['size_note']} · ₹{r.get('cost_lot', 0):,.0f} premium/lot" if r.get("size_note") else "")
                + (f"\n{'✅ TRADE' if r['proof']['trade'] else '📝 PAPER ONLY'}: {r['proof']['why']}" if r.get("proof") else "")
                + f"\nCredibility {c['grade']} ({c['score']}/100) · T1 odds {c['p_t1'] * 100:.0f}%"
                + (f" · weak: {', '.join(c['fails'][:3])}" if c.get("fails") else "")
                + (f"\n{r['invalid']}" if r.get("invalid") else "")
                + "\nPaper/educational signal, not advice. Confirm on your broker's chain.")
        ev.append((f"sig:{day}:{sym}:{r['kind']}", sym, "SIGNAL", text))
    return notify(ev, send=send)


def push_news(syms, now, send=True, hours=6):
    """Phone alert once per filing for alert-worthy NSE announcements (order wins, acquisitions, buybacks, fund raising,
    results, unusual-move clarifications) on these symbols in the last `hours`. -> newly logged events."""
    try:
        import news
        a = news.announcements()
    except Exception:
        return []
    if a.empty:
        return []
    x = a[a.alert & a.symbol.isin(set(syms)) & (a.ts >= now - pd.Timedelta(hours=hours))]
    ev = [(f"news:{r.link}", r.symbol, "NEWS",
           f"📰 {r.symbol}: {r.category} ({r.reading} usual reading)\n{r.subject}\nFiled {r.ts:%d %b %H:%M} · {r.link}\n"
           f"Context only: news alone is not a reliable signal; wait for price confirmation.") for r in x.itertuples()]
    return notify(ev, send=send)


def push_horizons(syms, now, min_grade="B", fx=None, send=True):
    """BTST / 2-day swing calls for these symbols, pushed once per symbol/horizon/day, only inside each market's
    pre-close window and only when the setup's own 2-year record earns min_grade or better. -> newly logged events."""
    import horizons as H
    ev, day = [], now.date().isoformat()
    for sym in syms:
        spec = E.resolve(sym)
        if not H.alert_window(spec, now):
            continue
        a = H.analyze(sym, fx if spec["kind"] == "commodity" else None)
        if a.get("error"):
            continue
        for kind, x in a["setups"].items():
            g, why = H.grade(x["rec"])
            if not x["dir"] or E.CRED_RANK[g] > E.CRED_RANK.get(min_grade, 1):
                continue
            ce = x["dir"] > 0
            lbl = {"BTST": "BTST", "SWING2D": "2-day breakout swing", "SWINGPB": "2-day pullback swing"}[kind]
            text = (f"{'🟢 BUY CALL' if ce else '🔴 BUY PUT'} {sym} · {lbl} · grade {g}"
                    f"\nClose {a['close']:,.2f} → T1 {x['t1']:,.2f} · SL {x['sl']:,.2f}"
                    f"\n2-day range {a['range2d'][0]:,.2f} – {a['range2d'][1]:,.2f} (~80%)"
                    f"\nRecord: {why}"
                    f"\nOvernight gaps can skip the stop. Educational signal, not advice.")
            ev.append((f"{kind}:{day}:{sym}:{x['dir']}", sym, "SIGNAL", text))
    return notify(ev, send=send)


def notify(events, send=True):
    """Log each event once (UNIQUE key) and push new ones to the phone (Telegram / ntfy). -> newly logged events."""
    if not events:
        return []
    c, new = conn(), []
    stamp = pd.Timestamp.now(tz=E.IST).isoformat(timespec="seconds")  # IST like trades (server may run in UTC)
    for key, sym, kind, text in events:
        cur = c.execute("INSERT OR IGNORE INTO alerts(ts, key, symbol, kind, text) VALUES(?,?,?,?,?)",
                        (stamp, key, sym, kind, text))
        if cur.rowcount:
            new.append((key, sym, kind, text))
            if send and push(text, tags=ALERT_TAGS.get(kind), priority="high" if kind in ("SIGNAL", "HEALTH") else None,
                             sym=sym if sym and kind in LINKED else None):
                c.execute("UPDATE alerts SET sent=1 WHERE key=?", (key,))
    c.commit()
    c.close()
    return new


def alerts_df(limit=100):
    c = conn()
    df = db.read_df(c, "SELECT id, ts, symbol, kind, text, sent FROM alerts ORDER BY id DESC LIMIT ?",
                           params=(limit,))
    c.close()
    return df


def alerts_since(last_id, limit=20):
    """Alerts logged after id `last_id`, oldest first (the newest `limit`). -> list of dicts id, ts, key, symbol, kind, text."""
    c = conn()
    rows = c.execute("SELECT * FROM (SELECT id, ts, key, symbol, kind, text FROM alerts WHERE id>? ORDER BY id DESC "
                     "LIMIT ?) ORDER BY id", (int(last_id or 0), limit)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def last_alert_id():
    c = conn()
    n = c.execute("SELECT COALESCE(MAX(id), 0) FROM alerts").fetchone()[0]
    c.close()
    return int(n)


# ---------------- Option-chain history (for IV rank and OI build-up research) ----------------
SNAP_SQL = """CREATE TABLE IF NOT EXISTS chain_snaps(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, day TEXT, symbol TEXT, source TEXT, expiry TEXT, spot REAL,
  atm_iv REAL, pcr REAL, pcr_chg REAL, call_wall REAL, put_wall REAL, max_pain REAL, strikes TEXT)"""
SNAP_EVERY_MIN = 15


def save_chain_snaps(results, now, width=10):
    """Store each symbol's option chain (summary + the +/-width strikes around spot) at most every 15 minutes.
    Builds the history that IV-rank and OI build-up signals need before they can be back-tested. -> rows saved."""
    c = conn()
    c.executescript(SNAP_SQL + "; CREATE INDEX IF NOT EXISTS ix_snaps ON chain_snaps(symbol, ts)")
    stamp, n = now.isoformat(timespec="seconds"), 0
    cutoff = (now - pd.Timedelta(minutes=SNAP_EVERY_MIN - 1)).isoformat(timespec="seconds")
    for sym, a in results.items():
        ch = (a or {}).get("chain")
        if not ch or not ch.get("quotes") or "error" in a:
            continue
        if c.execute("SELECT 1 FROM chain_snaps WHERE symbol=? AND ts>=?", (sym, cutoff)).fetchone():
            continue
        spot = float(a["ltp"])
        ks = sorted(ch["quotes"])
        i = min(range(len(ks)), key=lambda j: abs(ks[j] - spot))
        near = {f"{k:g}": ch["quotes"][k] for k in ks[max(0, i - width): i + width + 1]}
        c.execute("INSERT INTO chain_snaps(ts, day, symbol, source, expiry, spot, atm_iv, pcr, pcr_chg, call_wall, put_wall, "
                  "max_pain, strikes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (stamp, now.date().isoformat(), sym, ch.get("source"), ch.get("expiry"), spot, ch.get("atm_iv"),
                   ch.get("pcr"), ch.get("pcr_chg"), ch.get("call_wall"), ch.get("put_wall"), ch.get("max_pain"),
                   json.dumps(near, default=float)))
        n += 1
    c.commit()
    c.close()
    return n


def chain_history(symbol, days=365):
    """-> DataFrame of saved snapshots for one symbol (newest last)."""
    c = conn()
    c.executescript(SNAP_SQL)
    since = (pd.Timestamp.now() - pd.Timedelta(days=days)).date().isoformat()
    df = db.read_df(c, "SELECT * FROM chain_snaps WHERE symbol=? AND day>=? ORDER BY ts", params=(symbol, since))
    c.close()
    return df
