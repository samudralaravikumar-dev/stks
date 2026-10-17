"""
Background engine: scans the saved symbols on every candle, paper-trades each signal and sends Telegram alerts,
with or without the browser open. Uses the settings saved from the app's "Paper trading & alerts" tab.

Run:  run_engine.bat      (Ctrl+C to stop)      One pass only:  python runner.py --once
"""
import sys
import time
import traceback
from datetime import time as dtime

import pandas as pd

import engine as E
import kite_feed as kf
import paper
import upstox_feed as uf


def session_live(symbols, now):
    """True while any watched market is open (NSE 09:15-15:30, MCX 09:00-23:30, weekdays)."""
    if now.weekday() >= 5:
        return False
    sessions = {E.resolve(s)["sess"] for s in symbols}
    return any(E.SESSIONS[s][0] <= now.time() <= E.SESSIONS[s][1] for s in sessions)


def one_pass(cfg):
    P, symbols = cfg["P"], cfg["symbols"]
    P.setdefault("daily_loss_pct", 3.0)
    P.setdefault("max_open", 4)
    P.setdefault("corr_guard", True)
    now = pd.Timestamp.now(tz=E.IST)
    E.UPSTOX = uf.headless()
    E.KITE = None if E.UPSTOX else kf.headless()
    vix, _ = E.last_close("^INDIAVIX")
    fx, _ = E.last_close("USDINR=X")
    results = E.analyze_many(symbols, dict(P, news=False), vix, fx, now, fn=E.analyze)
    try:
        paper.save_chain_snaps(results, now)  # option-chain history for IV rank / OI build-up research
    except Exception:
        traceback.print_exc()
    send = cfg.get("telegram", True)
    recs = [(s, E.recommend(a, P)) for s, a in results.items() if "error" not in a]
    new = paper.push_signals(recs, now, P.get("alert_grade", "C"), send=send)  # CALL/PUT alerts to the phone
    new += paper.push_horizons(symbols, now, P.get("alert_grade", "C"), fx, send=send)  # BTST / 2-day before close
    new += paper.push_news(symbols, now, send=send)  # NSE corporate filings on watched symbols (context)
    new += paper.notify(paper.paper_step(results, P, now), send=send)
    src = "Upstox" if E.UPSTOX else "Kite" if E.KITE is not None else "Yahoo/NSE"
    board = "  ".join(f"{s}:{a.get('status', 'ERR')}" for s, a in results.items())
    print(f"[{now:%H:%M:%S}] {src} | {board}")
    for _, _, _, text in new:
        print("   ", text)


def main():
    once = "--once" in sys.argv
    print("Background engine started. Settings from", paper.SETTINGS.name, "| Ctrl+C to stop")
    tok, chat = paper.telegram_creds()
    print("Telegram:", "configured" if tok and chat else "not configured (alerts logged in the app only)")
    while True:
        try:
            cfg = paper.load_settings()
        except FileNotFoundError:
            print("No saved settings yet: open the app > Paper trading & alerts > 'Save settings for background engine'.")
            if once:
                return
            time.sleep(60)
            continue
        now = pd.Timestamp.now(tz=E.IST)
        if once or session_live(cfg["symbols"], now):
            try:
                one_pass(cfg)
            except Exception:
                traceback.print_exc()
        else:
            print(f"[{now:%H:%M:%S}] markets closed, waiting…")
        if once:
            return
        time.sleep(int(cfg.get("every", 60)))


if __name__ == "__main__":
    main()
