"""
Live trigger watcher: Upstox market-data stream (websocket, tick by tick) for the symbols that have a waiting
"BUY CALL above X / BUY PUT below X" plan today, and a phone alert within seconds of the level trading, instead of
up to a minute (always-on engine) or 5+ minutes (scheduled GitHub run) later.

Pending plans come from the shared journal (recs: mode 'trigger', status 'WAITING', logged by runner.py every pass).
Same alert channels and de-duplication as every other alert (paper.notify). Display only: it never places orders.
Run:  python stream.py      (on the always-on server, next to `python runner.py --vm`)
"""
import os
import threading
import time
import traceback

import pandas as pd

import engine as E
import paper
import upstox_feed as uf

RELOAD_S = 30  # re-read pending plans this often
GRADE_RANK = E.CRED_RANK


class Watcher:
    def __init__(self, token):
        self.token = token
        self.lock = threading.Lock()
        self.plans = {}  # instrument_key -> [plan dicts]
        self.keys = set()
        self.fired = set()
        self.streamer = None
        self.ticks = 0

    # ---------- pending plans ----------
    def load_plans(self, now):
        day = now.date().isoformat()
        c = paper.conn()
        rows = c.execute("SELECT symbol, kind, action, strike, prem, u_entry, u_sl, u_t1, grade, expiry FROM recs "
                         "WHERE day=? AND mode='trigger' AND status='WAITING'", (day,)).fetchall()
        c.close()
        bar = os.environ.get("ALERT_GRADE") or "C"
        plans = {}
        for r in rows:
            r = dict(r)
            if r["grade"] and GRADE_RANK.get(r["grade"], 3) > GRADE_RANK.get(bar, 2):
                continue
            spec = E.resolve(r["symbol"])
            if not E.SESSIONS[spec["sess"]][0] <= now.time() <= E.SESSIONS[spec["sess"]][2]:
                continue  # outside that market's session (or past its square-off)
            u = uf.underlying(spec["name"], spec["kind"], E.kite_day())
            if u and u.get("key") and r["u_entry"]:
                plans.setdefault(u["key"], []).append(r)
        with self.lock:
            self.plans = plans
        return set(plans)

    # ---------- stream ----------
    def on_message(self, msg):
        feeds = (msg or {}).get("feeds") or {}
        if not feeds:
            return
        self.ticks += 1
        now = pd.Timestamp.now(tz=E.IST)
        events = []
        with self.lock:
            for key, f in feeds.items():
                ltp = ((f or {}).get("ltpc") or {}).get("ltp")
                if not ltp:
                    continue
                for p in self.plans.get(key, []):
                    ce = p["kind"] == "CE"
                    if (ltp >= p["u_entry"]) if ce else (ltp <= p["u_entry"]):
                        k = f"trig:{now.date()}:{p['symbol']}:{p['kind']}"
                        if k in self.fired:
                            continue
                        self.fired.add(k)
                        events.append((k, p["symbol"], "SIGNAL", self.text(p, ltp, now)))
        if events:
            try:
                paper.notify(events)
            except Exception:
                traceback.print_exc()

    @staticmethod
    def text(p, ltp, now):
        ce = p["kind"] == "CE"
        return (f"🔔 TRIGGER HIT {now:%H:%M:%S} · {'🟢 BUY CALL' if ce else '🔴 BUY PUT'} {p['symbol']} "
                f"{(p['strike'] or 0):g} {p['kind']}" + (f" ({p['expiry']})" if p.get("expiry") else "")
                + f"\nUnderlying {ltp:,.2f} crossed {p['u_entry']:,.2f} · SL {p['u_sl'] or 0:,.2f} · T1 {p['u_t1'] or 0:,.2f}"
                + f"\nPlanned premium ₹{(p['prem'] or 0):,.2f} · grade {p.get('grade') or '–'}"
                + "\nCheck the card in the app (TRADE or PAPER, size, limit price) before acting. Not advice.")

    def connect(self, keys):
        import upstox_client
        cfg = upstox_client.Configuration()
        cfg.access_token = self.token
        self.streamer = upstox_client.MarketDataStreamerV3(upstox_client.ApiClient(cfg), sorted(keys), "ltpc")
        self.streamer.on("message", self.on_message)
        self.streamer.on("error", lambda e: print("stream error:", e, flush=True))
        self.streamer.on("close", lambda *a: print("stream closed", flush=True))
        try:
            self.streamer.auto_reconnect(True, 10, 30)
        except Exception:
            pass
        threading.Thread(target=self.streamer.connect, daemon=True).start()
        self.keys = set(keys)

    def resubscribe(self, keys):
        add, drop = keys - self.keys, self.keys - keys
        try:
            if add:
                self.streamer.subscribe(sorted(add), "ltpc")
            if drop:
                self.streamer.unsubscribe(sorted(drop))
            self.keys = set(keys)
        except Exception:  # the connection may be mid-reconnect: start a fresh one
            self.disconnect()
            self.connect(keys)

    def disconnect(self):
        try:
            if self.streamer:
                self.streamer.disconnect()
        except Exception:
            pass
        self.streamer, self.keys = None, set()


def main():
    token = uf.headless()
    if not token:
        raise SystemExit("No Upstox token: set UPSTOX_ANALYTICS_TOKEN")
    w = Watcher(token)
    print("Live trigger watcher started", flush=True)
    while True:
        now = pd.Timestamp.now(tz=E.IST)
        try:
            keys = w.load_plans(now)
            if keys and w.streamer is None:
                w.connect(keys)
            elif keys:
                w.resubscribe(keys)
            elif w.streamer is not None:
                w.disconnect()
            paper.heartbeat("stream", f"{sum(len(v) for v in w.plans.values())} plans · {len(w.keys)} symbols · "
                                      f"{w.ticks} tick batches")
        except Exception:
            traceback.print_exc()
        if now.hour == 0 and now.minute < 1:
            w.fired.clear()
        time.sleep(RELOAD_S)


if __name__ == "__main__":
    main()
