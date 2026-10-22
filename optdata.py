"""
Real historical option premiums from Upstox (Plus plan): expired contracts via /v2/expired-instruments/*, the current
series via the normal historical-candle API. Used to back-test the engine's CALL/PUT signals on what the option really
traded at (actual IV moves, theta, expiry-day behaviour), instead of a Black-Scholes model.

Coverage (checked Oct 2026): NIFTY weekly + BANKNIFTY / FINNIFTY / stock monthlies, about 2 years. MCX: none.
Candles are cached on disk (DATA_DIR/optcache) since past days never change.
"""
import os
import pickle
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import engine as E
import upstox_feed as uf

CACHE = Path(os.environ.get("TRAD_DATA_DIR") or Path(__file__).parent) / "optcache"


def _disk(name, fn):
    """Small on-disk memo for immutable past data."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (name.replace("|", "_").replace(":", "_").replace("/", "_") + ".pkl")
    if f.exists():
        try:
            return pickle.loads(f.read_bytes())
        except Exception:
            pass
    v = fn()
    if v is not None:
        f.write_bytes(pickle.dumps(v))
    return v


def chain_key(spec):
    u = uf.underlying(spec["name"], spec["kind"], E.kite_day())
    return (u or {}).get("chain_key")


def expiries(token, key):
    """Sorted expiry dates (date objects): expired ones (2 years) + the live ones."""
    today = pd.Timestamp.now(tz=E.IST).date()
    old = _disk(f"exp_{key}_{today}", lambda: uf.api(token, "/v2/expired-instruments/expiries", {"instrument_key": key}))
    live = _disk(f"live_{key}_{today}", lambda: uf.api(token, "/v2/option/contract", {"instrument_key": key}))
    out = {date.fromisoformat(x) for x in (old or [])}
    out |= {date.fromisoformat(c["expiry"]) for c in (live or []) if c.get("expiry")}
    return sorted(out)


def contracts(token, key, expiry):
    """{(strike, 'CE'/'PE'): instrument_key} for one expiry."""
    today = pd.Timestamp.now(tz=E.IST).date()
    if expiry < today:
        cs = _disk(f"con_{key}_{expiry}", lambda: uf.api(token, "/v2/expired-instruments/option/contract",
                                                          {"instrument_key": key, "expiry_date": expiry.isoformat()}))
    else:
        cs = [c for c in (_disk(f"live_{key}_{today}", lambda: uf.api(token, "/v2/option/contract",
                                                                       {"instrument_key": key})) or [])
              if c.get("expiry") == expiry.isoformat()]
    return {(float(c["strike_price"]), c["instrument_type"]): c["instrument_key"] for c in (cs or [])
            if c.get("instrument_type") in ("CE", "PE")}


def candles(token, ikey, day, expired):
    """15-minute candles of one option contract on one day -> DataFrame[Open, High, Low, Close, Volume, OI] (IST)."""
    d = day.isoformat()
    k = urllib.parse.quote(ikey, safe="")

    def fetch():
        if expired:
            j = uf.api(token, f"/v2/expired-instruments/historical-candle/{k}/15minute/{d}/{d}", None)
        else:
            j = uf.api(token, f"/v3/historical-candle/{k}/minutes/15/{d}/{d}", None)
        rows = (j or {}).get("candles") or []
        if not rows:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "OI"])
        df = pd.DataFrame([r[:7] for r in rows], columns=["t", "Open", "High", "Low", "Close", "Volume", "OI"][:len(rows[0])])
        df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("t"))).tz_convert(E.IST)
        return df.sort_index().astype(float)

    today = pd.Timestamp.now(tz=E.IST).date()
    try:
        if day >= today:  # today's candles are still forming: never cache
            return fetch()
        return _disk(f"c_{ikey}_{d}", fetch)  # a failed fetch raises, so it is never cached
    except Exception:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "OI"])


def daily_real_bt(sym, day, p_key, _d, _spec, _P, _token):
    """real_option_backtest once per symbol / day / settings (Streamlit-cached; candles are disk-cached too)."""
    try:
        return real_option_backtest(_d, _spec, _P, _token)
    except Exception:
        return pd.DataFrame(), None


try:
    import streamlit as _st
    daily_real_bt = _st.cache_data(ttl=12 * 3600, show_spinner=False, max_entries=500)(daily_real_bt)
except Exception:
    pass


def real_option_backtest(d, spec, P, token, cost=0.015, max_trades=None, roll="default", spread=None,
                         time_stop=None, trail=None, keep_t2=True):
    """The engine's signals with the same entries/exits as backtest() (next-bar entry, underlying stop / T1 half /
    T2, breakeven after T1, square-off), priced on the REAL option: the delta-0.55 strike of the nearest expiry on or
    after the trade day, entry at that contract's 15-min open, exits at the contract's prices on the exit bar
    (stop: bar low; target / T1 / square-off: bar close; conservative). -> (trades DataFrame, stats dict or None).
    spread='t2': a debit spread instead - also SELL the same-expiry option nearest the underlying's T2 level (max profit
    at the target); stops value it at long low / short high, other exits at both closes; costs on both legs.
    time_stop=N: exit at the bar close N bars after entry when T1 has not been reached (stops theta bleed).
    trail=k: after T1 the stop trails the best underlying price reached so far by k x the initial stop distance (never
    below cost); keep_t2=False lets the second half run to the trailing stop or the square-off instead of T2."""
    key = chain_key(spec)
    if not key:
        return pd.DataFrame(), None
    exps = expiries(token, key)
    o, h, l, c = (d[x].to_numpy() for x in ("Open", "High", "Low", "Close"))
    sig, dist, t2d = d.Signal.to_numpy(), d.StopDist.to_numpy(), d.T2Dist.to_numpy()
    idx, n = d.index, len(d)
    days = np.array(idx.date)
    mins = (idx.hour * 60 + idx.minute).to_numpy()
    sqm = E.tmin(E.SESSIONS[spec["sess"]][2])
    t1r = P.get("t1_r", E.T1_R)
    today = pd.Timestamp.now(tz=E.IST).date()
    out, i, per_day, chains = [], 0, {}, {}
    while i < n - 1 and (max_trades is None or len(out) < max_trades):
        if sig[i] == 0 or days[i + 1] != days[i] or per_day.get(days[i], 0) >= P["max_day"] or mins[i + 1] >= sqm:
            i += 1
            continue
        dr, j0, day = int(sig[i]), i + 1, days[i + 1]
        kind, S0, R = ("CE" if dr > 0 else "PE"), o[j0], dist[i]
        exp = next((e for e in exps if e >= day), None)
        roll = E.ROLL_DTE if roll == "default" else roll
        if roll and exp is not None and roll[0] <= (exp - day).days <= roll[1]:  # skip a decaying expiry
            exp = next((e for e in exps if e > exp), exp)
        if exp is None:
            i += 1
            continue
        if exp not in chains:
            chains[exp] = contracts(token, key, exp)
        strikes = sorted({k for k, t in chains[exp] if t == kind})
        if not strikes:
            i += 1
            continue
        T = max((pd.Timestamp(exp).tz_localize(E.IST) + pd.Timedelta(hours=15, minutes=30) - idx[j0]).total_seconds()
                / 86400 / 365, 1 / 365 / 48)
        iv = 0.15 if spec["kind"] == "index" else 0.25
        near = [k for k in strikes if abs(k - S0) / S0 < 0.06] or strikes
        K = min(near, key=lambda k: abs(abs(E.bs(S0, k, T, iv, kind)[1]) - P["delta"]))
        ob = candles(token, chains[exp][(K, kind)], day, exp < today)
        if ob.empty or idx[j0] not in ob.index:
            i += 1
            continue
        p0 = float(ob.loc[idx[j0], "Open"])
        if p0 <= 0.5:
            i += 1
            continue
        sl_u, t1_u, t2_u = S0 - dr * R, S0 + dr * t1r * R, S0 + dr * t2d[i]
        obs, Ks, q0 = None, None, 0.0
        if spread:  # short leg: same expiry, strike nearest the underlying's T2, beyond the long strike
            beyond = [k for k in strikes if (k - K) * dr > 0]
            Ks = min(beyond, key=lambda k: abs(k - t2_u)) if beyond else None
            obs = candles(token, chains[exp][(Ks, kind)], day, exp < today) if Ks is not None else None
            if obs is None or obs.empty or idx[j0] not in obs.index:
                i += 1
                continue
            q0 = float(obs.loc[idx[j0], "Open"])
            if p0 - q0 <= 0.5:
                i += 1
                continue
        net0 = p0 - q0
        per_day[days[i]] = per_day.get(days[i], 0) + 1
        half, booked, exit_v, why, j = False, 0.0, None, "", j0
        peak = S0  # best underlying price since entry (updated after each bar: no peeking inside the bar)

        def val(bar, bar2, worst=False):  # position value: long leg minus short leg
            if obs is None:
                return float(bar.Low) if worst else float(bar.Close)
            return (float(bar.Low) - float(bar2.High)) if worst else (float(bar.Close) - float(bar2.Close))
        while j < n and days[j] == day:
            bar = ob.loc[idx[j]] if idx[j] in ob.index else None
            bar2 = obs.loc[idx[j]] if obs is not None and idx[j] in obs.index else None
            if bar is None or (obs is not None and bar2 is None):
                j += 1
                continue
            adv, fav = (l[j], h[j]) if dr > 0 else (h[j], l[j])
            stop_lvl = S0 if half else sl_u
            if half and trail:
                cand = peak - dr * trail * R
                stop_lvl = cand if (cand - S0) * dr > 0 else S0
            if (adv - stop_lvl) * dr <= 0:
                exit_v = val(bar, bar2, worst=True)
                why = ("trail stop" if stop_lvl != S0 else "breakeven") if half else "stop"
            else:
                if not half and (fav - t1_u) * dr >= 0:
                    half, booked = True, 0.5 * (val(bar, bar2) - net0)
                if keep_t2 and (fav - t2_u) * dr >= 0:
                    exit_v, why = val(bar, bar2), "target"
                elif time_stop and not half and j - j0 >= time_stop:
                    exit_v, why = val(bar, bar2), "time stop"
                elif mins[j] >= sqm or j + 1 >= n or days[j + 1] != days[j]:
                    exit_v, why = val(bar, bar2), "square-off"
            if exit_v is not None:
                break
            if (fav - peak) * dr > 0:
                peak = fav
            j += 1
        if exit_v is None:
            i = j + 1
            continue
        pnl = (booked + (0.5 if half else 1.0) * (exit_v - net0)) / net0 - cost * (p0 + q0) / net0
        out.append(dict(entry_time=idx[j0], side="CALL" if dr > 0 else "PUT", expiry=exp, strike=K, prem=round(p0, 2),
                        short_strike=Ks, net_debit=round(net0, 2), dte=(exp - day).days, exit_reason=why,
                        t1_hit=half, pnl_pct=round(pnl * 100, 2)))
        i = j + 1
    t = pd.DataFrame(out)
    if t.empty:
        return t, None
    p = t.pnl_pct / 100
    loss = -p[p < 0].sum()
    return t, dict(trades=len(t), win=float((p > 0).mean()), avg=float(p.mean()), t1=float(t.t1_hit.mean()),
                   floor=float(p.mean() - 1.645 * p.std(ddof=1) / np.sqrt(len(p))) if len(p) > 1 else -1.0,
                   pf=float(p[p > 0].sum() / loss) if loss > 0 else float("inf"), source="real Upstox premiums")


# ---------------- Futures with open interest (for F&O build-up signals) ----------------
def _fut_contract(token, key, expiry):
    """Futures instrument key for one expiry: live contracts from the instrument list, expired ones from Upstox."""
    today = pd.Timestamp.now(tz=E.IST).date()
    if expiry >= today:
        fo = uf.instruments("NSE", E.kite_day())
        f = fo[(fo.instrument_type == "FUT") & (fo.underlying_key == key)]
        exp = pd.to_datetime(f.expiry, unit="ms").dt.tz_localize("UTC").dt.tz_convert(E.IST).dt.date
        hit = f[exp == expiry]
        return hit.instrument_key.iloc[0] if len(hit) else None
    cs = _disk(f"fut_{key}_{expiry}", lambda: uf.api(token, "/v2/expired-instruments/future/contract",
                                                      {"instrument_key": key, "expiry_date": expiry.isoformat()}))
    return (cs or [{}])[0].get("instrument_key")


def futures_series(token, spec, start):
    """Front-month futures 15-min candles with OI from `start` to today -> DataFrame[Open, High, Low, Close, Volume, OI]."""
    key = chain_key(spec)
    if not key:
        return pd.DataFrame()
    today = pd.Timestamp.now(tz=E.IST).date()
    fo = uf.instruments("NSE", E.kite_day())
    live = fo[(fo.instrument_type == "FUT") & (fo.underlying_key == key)]
    live_exp = set(pd.to_datetime(live.expiry, unit="ms").dt.tz_localize("UTC").dt.tz_convert(E.IST).dt.date)
    old = _disk(f"exp_{key}_{today}", lambda: uf.api(token, "/v2/expired-instruments/expiries", {"instrument_key": key}))
    exps = sorted({date.fromisoformat(x) for x in (old or [])} | live_exp)
    monthly = sorted({max(e for e in exps if (e.year, e.month) == (x.year, x.month)) for x in exps})  # futures: monthly
    parts, prev = [], start - timedelta(days=1)
    for e in monthly:
        if e < start:
            continue
        ik = _fut_contract(token, key, e)
        frm, to = max(prev + timedelta(days=1), start), min(e, today)
        if ik and frm <= to:
            enc = urllib.parse.quote(ik, safe="")
            def fetch(enc=enc, frm=frm, to=to, expired=e < today):
                path = (f"/v2/expired-instruments/historical-candle/{enc}/15minute/{to}/{frm}" if expired else
                        f"/v3/historical-candle/{enc}/minutes/15/{to}/{frm}")
                rows = (uf.api(token, path, None) or {}).get("candles") or []
                if not rows:
                    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "OI"])
                df = pd.DataFrame([r[:7] for r in rows], columns=["t", "Open", "High", "Low", "Close", "Volume", "OI"])
                df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("t"))).tz_convert(E.IST)
                return df.sort_index().astype(float)
            try:
                parts.append(_disk(f"futc_{ik}_{frm}_{to}", fetch) if to < today else fetch())
            except Exception:
                pass
        prev = e
        if e >= today:
            break
    df = pd.concat(parts) if parts else pd.DataFrame()
    return df[~df.index.duplicated()].sort_index() if len(df) else df


def buildup(fut):
    """Classic F&O build-up per bar from the day's change vs the previous session's close / OI:
    LB long build-up (price up, OI up), SB short build-up (down, up), SC short covering (up, down), LU long unwinding."""
    if fut.empty or "OI" not in fut:
        return pd.DataFrame()
    day = pd.Series(fut.index.date, index=fut.index)
    last = fut.groupby(day.values)[["Close", "OI"]].last().shift(1)
    pc, poi = day.map(last.Close), day.map(last.OI)
    out = pd.DataFrame({"fut_chg": (fut.Close / pc - 1) * 100, "oi_chg": (fut.OI / poi.replace(0, np.nan) - 1) * 100},
                       index=fut.index)
    p, o = out.fut_chg, out.oi_chg
    out["build"] = np.select([(p > 0.3) & (o > 2), (p < -0.3) & (o > 2), (p > 0.3) & (o < -2), (p < -0.3) & (o < -2)],
                             ["LB", "SB", "SC", "LU"], "")
    return out
