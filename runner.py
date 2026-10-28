"""
Background engine: scans the saved symbols on every candle, paper-trades each signal and sends Telegram alerts,
with or without the browser open. Uses the settings saved from the app's "Paper trading & alerts" tab.

Run:  run_engine.bat      (Ctrl+C to stop)      One pass only:  python runner.py --once
Scheduled cloud pass (GitHub Actions, .github/workflows/alerts.yml):  python runner.py --once --cloud
"""
import os
import sys
import time
import traceback
from datetime import time as dtime

import pandas as pd

import engine as E
import events
import kite_feed as kf
import paper
import upstox_feed as uf


DEFAULT_SYMBOLS = "NIFTY, BANKNIFTY, FINNIFTY, RELIANCE, HDFCBANK, ICICIBANK, SBIN, INFY, TCS, LT, CRUDE, NATURALGAS"


def default_settings():
    """The app's default sidebar settings, for cloud runs with no saved engine_settings.json (GitHub Actions).
    Symbols come from env WATCHLIST (comma separated, e.g. a GitHub repository variable) or the default watchlist."""
    P = dict(interval="15m", period="60d", th=6.0, adx_min=20, lookback=3, use_time=True, win_start=dtime(9, 30),
             win_end=dtime(14, 45), orb_cut=E.tmin(dtime(13, 0)), capital=200000, risk_pct=1.0,
             strike_mode="Delta target", delta=0.55, rr=2.0, atr_mult=1.5, max_loss=0.35, hold=1.0, dte=3, iv=0.0,
             step=0.0, lot=0, cost_pct=0.03, chain=True, news=False, setups={"VWAPPB", "STFLIP", "SQZ", "ORB"},
             exit_mode="2R target + flip", stop_min=0.75, max_day=2, **paper.RISK_DEFAULTS, corr_guard=True,
             ctx_filter=E.CTX_DEFAULT, alert_grade=os.environ.get("ALERT_GRADE") or "C", t1_r=E.T1_R)
    syms = [x.strip().upper() for x in (os.environ.get("WATCHLIST") or DEFAULT_SYMBOLS).split(",") if x.strip()]
    extra = []
    if (os.environ.get("SCAN_UNIVERSE") or "").lower() == "fno":  # every NSE stock with options, beyond the watchlist
        try:
            import symbols
            u = symbols.universe()
            extra = [x for x in dict.fromkeys(u[u.kind == "fno"].symbol) if x not in syms]
        except Exception:
            extra = []
    return dict(P=P, symbols=list(dict.fromkeys(syms)), extra=extra, every=300, telegram=True)


def scan_extra(extra, P, vix, fx, now):
    """Wide F&O scan within Upstox's rate limits: candles only for every symbol (no option chain, no news), then the
    full analysis with option chain only for the few that show a CALL/PUT setup. Runs every 10 minutes."""
    if not extra or now.minute % 10 >= 5:
        return {}
    light = E.analyze_many(extra, dict(P, chain=False, news=False), vix, fx, now, fn=E.analyze)
    hot = [s for s, a in light.items() if "error" not in a and E.recommend(a, dict(P, chain=False))["mode"] != "none"]
    print(f"    wide F&O scan: {len(extra)} symbols, {len(hot)} with a setup -> pricing their options")
    return E.analyze_many(hot, dict(P, news=False), vix, fx, now, fn=E.analyze) if hot else {}


def market_holiday(now):
    try:
        return now.date() in events.holidays()
    except Exception:
        return False


def session_live(symbols, now):
    """True while any watched market is open (NSE 09:15-15:30, MCX 09:00-23:30, weekdays)."""
    if now.weekday() >= 5:
        return False
    sessions = {E.resolve(s)["sess"] for s in symbols}
    return any(E.SESSIONS[s][0] <= now.time() <= E.SESSIONS[s][1] for s in sessions)


def _age_min(d, now):
    t = pd.Timestamp(d.index[-1])
    t = t.tz_localize(E.IST) if t.tzinfo is None else t.tz_convert(E.IST)
    return (now - t).total_seconds() / 60


def health_events(results, now, token_set):
    """Problems that would otherwise fail silently: no data, stale candles during the session, an Upstox token that
    no longer works; plus one 'engine alive' ping per trading day. Each problem alerts at most once an hour."""
    day, hr = now.date().isoformat(), now.strftime("%H")
    ok = {s: a for s, a in results.items() if "error" not in a}
    nse = {s: a for s, a in ok.items() if a["spec"]["sess"] == "nse"}
    ev = []
    if results and not ok:
        first = next(iter(results.values())).get("error", "?")
        ev.append((f"health:{day}:{hr}:nodata", "", "HEALTH",
                   f"⚠ Engine health: no market data, all {len(results)} symbols failed ({first}). Signals are paused."))
    if nse and now.weekday() < 5 and dtime(9, 45) <= now.time() <= dtime(15, 15):
        stale = {s: _age_min(a["d"], now) for s, a in nse.items()}
        stale = {s: m for s, m in stale.items() if m > (50 if nse[s].get("src") == "Yahoo" else 30)}  # Yahoo lags ~15 min
        if len(stale) * 2 >= len(nse):
            worst = max(stale.values())
            ev.append((f"health:{day}:{hr}:stale", "", "HEALTH",
                       f"⚠ Engine health: stale prices on {len(stale)}/{len(nse)} NSE symbols (newest candle up to "
                       f"{worst:.0f} min old). Signals may be late or wrong."))
        if token_set and not any(a.get("src") == "Upstox" for a in nse.values()):
            ev.append((f"health:{day}:{hr}:token", "", "HEALTH",
                       "⚠ Engine health: the Upstox token is set but not working, so the engine fell back to delayed "
                       "Yahoo data. Generate a new analytics token on Upstox and update the UPSTOX_ANALYTICS_TOKEN "
                       "secret (GitHub) and Streamlit secrets."))
    try:
        import db
        where, err = db.status()
        if err:
            ev.append((f"health:{day}:{hr}:db", "", "HEALTH",
                       f"⚠ Engine health: the shared database is unreachable ({err[:120]}). The engine is using a "
                       "temporary local file, so the track record and loss limits may be incomplete."))
    except Exception:
        pass
    if ok and now.weekday() < 5 and now.time() >= dtime(9, 15):
        srcs = sorted({a.get("src", "?") for a in ok.values()})
        ev.append((f"alive:{day}", "", "ALIVE",
                   f"✅ SRK engine running · {', '.join(srcs)} data · {len(ok)}/{len(results)} symbols OK · "
                   f"alerts at grade {os.environ.get('ALERT_GRADE') or 'C'} or better"))
    return ev


def bars_for(sym):
    """5-minute session bars (IST) + square-off minute: what evaluate_recs needs to score a logged call."""
    spec = E.resolve(sym)
    raw, src = E.get_prices(spec, "5m", "5d")
    fx = E.last_close("USDINR=X")[0] if spec["kind"] == "commodity" else None
    df, _ = E.prepare(raw, spec, "5m", fx if src == "Yahoo" else None)
    return df, E.tmin(E.SESSIONS[spec["sess"]][2])


def one_pass(cfg):
    P, symbols = cfg["P"], cfg["symbols"]
    for k, v in paper.RISK_DEFAULTS.items():
        P.setdefault(k, v)
    P.setdefault("corr_guard", True)
    now = pd.Timestamp.now(tz=E.IST)
    E.UPSTOX = uf.headless()
    E.KITE = None if E.UPSTOX else kf.headless()
    vix, _ = E.last_close("^INDIAVIX")
    fx, _ = E.last_close("USDINR=X")
    results = E.analyze_many(symbols, dict(P, news=False), vix, fx, now, fn=E.analyze)
    wide = scan_extra(cfg.get("extra") or [], P, vix, fx, now)  # alerts only: paper trades stay on the watchlist
    try:
        paper.save_chain_snaps(results, now)  # option-chain history for IV rank / OI build-up research
    except Exception:
        traceback.print_exc()
    send = cfg.get("telegram", True)
    P.update(paper.risk_controls(P, now))  # halt / half size from the journal's loss limits
    recs = [(s, E.recommend(a, P)) for s, a in {**wide, **results}.items() if "error" not in a]
    try:  # the track record: log every watchlist CALL/PUT (and filter-blocked ones), score them about once an hour
        paper.log_recs([(s, r) for s, r in recs if s in results], now, "runner")
        paper.log_recs([(s, E.blocked_rec(a, P)) for s, a in results.items() if "error" not in a], now, "blocked")
        if now.minute < 6:
            paper.evaluate_recs(bars_for, now)
    except Exception:
        traceback.print_exc()
    new = paper.push_signals(recs, now, P.get("alert_grade", "C"), send=send)  # CALL/PUT alerts to the phone
    new += paper.push_horizons(symbols, now, P.get("alert_grade", "C"), fx, send=send)  # BTST / 2-day before close
    new += paper.push_news(symbols, now, send=send)  # NSE corporate filings on watched symbols (context)
    try:  # AI reading of new filings (Gemini free tier, capped; stored once per filing): shown on the cards
        import ai_news
        ai_news.score_filings(set(symbols) | set(wide))
    except Exception:
        traceback.print_exc()
    new += paper.notify(paper.paper_step(results, P, now), send=send)
    new += paper.notify(health_events(results, now, bool(uf.analytics_token())), send=send)
    src = "Upstox" if E.UPSTOX else "Kite" if E.KITE is not None else "Yahoo/NSE"
    board = "  ".join(f"{s}:{a.get('status', 'ERR')}" for s, a in results.items())
    print(f"[{now:%H:%M:%S}] {src} | {board}")
    for _, _, _, text in new:
        print("   ", text)


def main():
    once = "--once" in sys.argv
    cloud = "--cloud" in sys.argv  # scheduled cloud run: default settings, skip closed markets and holidays
    vm = "--vm" in sys.argv  # always-on server (e.g. Oracle Cloud): default settings from env, every minute, forever
    print("Background engine started. Settings from", paper.SETTINGS.name, "| Ctrl+C to stop")
    tok, chat = paper.telegram_creds()
    print("Telegram:", "configured" if tok and chat else "not configured (alerts logged in the app only)")
    while True:
        try:
            cfg = default_settings() if vm else paper.load_settings()
        except FileNotFoundError:
            if cloud:
                cfg = default_settings()
            else:
                cfg = None
        if vm:
            cfg["every"] = int(os.environ.get("ENGINE_EVERY", "60"))
            paper.heartbeat("vm-engine", f"{len(cfg['symbols'])} symbols")
        if cloud:  # GitHub Actions is the backup: stand down while the always-on engine is reporting
            age = paper.heartbeat_age("vm-engine")
            if age is not None and age < 600:
                print(f"Always-on engine reported {age:.0f}s ago: this scheduled run is not needed")
                return
        if cfg is None:
            print("No saved settings yet: open the app > Paper trading & alerts > 'Save settings for background engine'.")
            if once:
                return
            time.sleep(60)
            continue
        now = pd.Timestamp.now(tz=E.IST)
        if cloud and (not session_live(cfg["symbols"], now) or market_holiday(now)):
            print(f"[{now:%H:%M:%S}] markets closed or holiday: nothing to do")
            return
        if vm and (not session_live(cfg["symbols"], now) or market_holiday(now)):
            time.sleep(300)  # closed: keep the heartbeat going, check again in 5 minutes
            continue
        if once or session_live(cfg["symbols"], now):
            try:
                one_pass(cfg)
            except Exception as e:
                traceback.print_exc()
                try:  # a crash must not be silent: tell the phone (once an hour)
                    paper.notify([(f"health:{now.date()}:{now:%H}:crash", "", "HEALTH",
                                   f"⚠ Engine health: scan crashed ({type(e).__name__}: {str(e)[:150]}). "
                                   "Alerts may be missing until it is fixed.")], send=cfg.get("telegram", True))
                except Exception:
                    traceback.print_exc()
        else:
            print(f"[{now:%H:%M:%S}] markets closed, waiting…")
        if once:
            return
        time.sleep(int(cfg.get("every", 60)))


if __name__ == "__main__":
    main()
