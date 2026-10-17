"""
Read a broker's research PDF that the user uploads (e.g. ICICI Direct Momentum Picks, HDFC Securities Intraday Pick)
and pull out its calls: stock, BUY/SELL, entry (range), target(s), stop-loss, duration. The user reviews the table and
picks which ones to track; nothing is fetched from broker sites. Personal use only (the reports' own disclaimers
forbid redistribution).

Two passes: a pattern for the common tabular layout "Date Scrip [Code] Buy|Sell 631-632 639/645 626.80 Intraday",
then a generic fallback for any line naming a stock with BUY/SELL and at least three prices (stop, entry and target
inferred from their order).
"""
import io
import re

import pandas as pd

import symbols

NUM = r"\d{1,6}(?:,\d{3})*(?:\.\d+)?"
ROW = re.compile(
    rf"(?P<name>[A-Za-z][A-Za-z0-9&.'\- ()]{{1,60}}?)\s+(?:[A-Z0-9&]{{3,10}}\s+)?(?P<side>Buy|Sell|BUY|SELL)\s+"
    rf"(?P<lo>{NUM})(?:\s*[-–]\s*(?P<hi>{NUM}))?\s+(?P<tgt>{NUM}(?:\s*/\s*{NUM})*)\s+(?P<sl>{NUM})"
    rf"(?:\s+(?P<dur>Intraday|\d+\s*Days?|\d+\s*Weeks?|\d+\s*Months?|Positional|BTST|STBT))?")
DATE = re.compile(r"^\s*\d{1,2}-[A-Za-z]{3}-\d{2,4}\s+")


def _f(x):
    return float(str(x).replace(",", ""))


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\b(ltd|limited|india|the|and|&|of|co|corp|corporation|company)\b", "",
                                           str(s).lower()))


def _resolver():
    """Name / symbol -> NSE symbol, from the exchange's equity list (+ indices)."""
    u = symbols.universe()
    by_sym = {s.upper(): s for s in u.symbol}
    names = [(_norm(n), s) for s, n in zip(u.symbol, u.name) if n]
    special = {"nifty": "NIFTY", "niftybank": "BANKNIFTY", "banknifty": "BANKNIFTY", "finnifty": "FINNIFTY",
               "niftyfinancialservices": "FINNIFTY"}

    def resolve(name):
        raw = re.sub(r"\(.*?\)", "", str(name)).strip()
        if raw.upper().replace(" ", "") in by_sym:
            return by_sym[raw.upper().replace(" ", "")]
        k = _norm(raw)
        if k in special:
            return special[k]
        if not k or len(k) < 3:
            return None
        hits = [s for n, s in names if n.startswith(k)] or [s for n, s in names if k.startswith(n) and len(n) >= 5]
        return hits[0] if len(hits) == 1 or (hits and len(set(hits)) == 1) else (hits[0] if hits else None)
    return resolve


def text_of(pdf_bytes, max_pages=40):
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join((p.extract_text() or "") for p in r.pages[:max_pages])


def parse_text(text):
    """-> DataFrame[symbol, name, side, entry, entry_hi, stop, t1, t2, duration, line] (deduplicated)."""
    resolve = _resolver()
    rows = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 12:
            continue
        m = ROW.search(DATE.sub("", line))
        if m:
            side = m.group("side").upper()
            lo = _f(m.group("lo"))
            hi = _f(m.group("hi")) if m.group("hi") else lo
            tg = [_f(x) for x in re.split(r"\s*/\s*", m.group("tgt"))]
            sl = _f(m.group("sl"))
            name = m.group("name").strip()
        else:  # generic: a stock name/symbol, BUY/SELL and >= 3 prices
            ms = re.search(r"\b(BUY|SELL|Buy|Sell)\b", line)
            nums = [_f(x) for x in re.findall(NUM, line) if 0.5 < _f(x) < 500000]
            if not ms or len(nums) < 3:
                continue
            side = ms.group(1).upper()
            name = line[:ms.start()].strip(" :-|") or line[ms.end():].split()[0]
            nums = sorted(set(nums))
            sl, tg = (nums[0], nums[2:]) if side == "BUY" else (nums[-1], list(reversed(nums[:-2])))
            lo = hi = nums[1] if side == "BUY" else nums[-2]
        sym = resolve(name)
        if not sym:
            continue
        entry = (lo + hi) / 2
        ok = (sl < entry < min(tg)) if side == "BUY" else (max(tg) < entry < sl)
        if not ok:
            continue
        dur = (m.group("dur") if m and m.group("dur") else "") or ("Intraday" if "intraday" in line.lower() else "")
        rows.append(dict(symbol=sym, name=name[:40], side=side, entry=round(entry, 2), entry_hi=hi, stop=sl,
                         t1=tg[0], t2=tg[1] if len(tg) > 1 else tg[0], duration=dur, line=line[:160]))
    df = pd.DataFrame(rows, columns=["symbol", "name", "side", "entry", "entry_hi", "stop", "t1", "t2", "duration", "line"])
    return df.drop_duplicates(["symbol", "side", "entry"]).reset_index(drop=True)


def parse_pdf(pdf_bytes):
    return parse_text(text_of(pdf_bytes))
