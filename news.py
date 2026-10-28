"""
Corporate catalysts from NSE's own announcements RSS feed (the exchange offers it for subscription; polled at most
every 10 minutes). Each filing is tagged with a category that Indian desks trade on: order wins, acquisitions,
buybacks, fund raising, results, rating actions, management changes, unusual-volume clarifications.

Evidence: news alone is not a reliable signal in India (analyst-call hit
rates <= 50%, post-results drift mixed, rating changes move prices little; block deals ~+1.3% on the day). So these
are shown as context and alerts, and as a caution in the credibility check, not as stand-alone CALL/PUT signals.
"""
import re

import feedparser
import pandas as pd
import streamlit as st

import engine as E
import net
import symbols

RSS = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
# (category, pattern, usual reading, alert-worthy)
CATS = [
    ("Order win", r"bagging|receiving of order|order(s)? (worth|received|win)|letter of (award|intent)|contract award", "+", True),
    ("Acquisition / merger", r"acquisition|amalgamation|merger|scheme of arrangement|takeover|open offer", "±", True),
    ("Buyback", r"buy ?-?back", "+", True),
    ("Fund raising", r"fund raising|qualified institution|\bqip\b|preferential (issue|allotment)|rights issue", "−", True),
    ("Results", r"financial results|un-?audited|audited results", "±", True),
    ("Credit rating", r"credit rating", "±", False),
    ("Management change", r"resignation|cessation|change in (directors|management)|appointment of (md|ceo|cfo)", "±", False),
    ("Unusual move clarification", r"spurt in volume|price movement|clarification", "±", True),
    ("Pledge / insider", r"pledge|encumbrance|insider trading|\bsast\b", "±", False),
    ("Dividend / bonus / split", r"dividend|bonus|split|sub-division", "+", False),
]
SKIP = r"newspaper publication|trading window|analysts?/institutional investor meet|investor presentation|loss of share " \
       r"certificate|duplicate share|certificate under|74\s*\(5\)|esop|esos|record date|book closure|agm|egm|postal ballot"


def _norm(name):
    return re.sub(r"\b(limited|ltd|the|india|and|&)\b|[^a-z0-9]", "", str(name).lower())


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def _name_map():
    u = symbols.universe()
    return {_norm(n): s for s, n in zip(u.symbol, u.name) if n}


def _known():
    return set(symbols.universe().symbol)


@st.cache_data(ttl=600, show_spinner=False)
def announcements():
    """Latest ~200 NSE corporate filings -> DataFrame[ts, symbol, company, subject, category, reading, alert, link]."""
    try:
        r = net.session().get(RSS, headers=E.NSE_H, timeout=12)
        f = feedparser.parse(r.content)
    except Exception:
        return pd.DataFrame(columns=["ts", "symbol", "company", "subject", "category", "reading", "alert", "link"])
    names, known, rows = _name_map(), _known(), []
    for e in f.entries:
        desc = e.get("description") or e.get("summary") or ""
        subject = desc.split("SUBJECT:")[-1].strip() if "SUBJECT:" in desc else desc
        text = f"{subject} {desc}".lower()
        if re.search(SKIP, text):
            continue
        cat = next(((c, rd, al) for c, pat, rd, al in CATS if re.search(pat, text)), None)
        if not cat:
            continue
        link = e.get("link", "")
        m = re.search(r"/corporate/([A-Z0-9&\-]+?)_\d{8}", link)
        sym = m.group(1) if m else None
        if sym not in known:  # file names are not always the symbol ("NAUKRI2_..."): fall back to the company name
            sym = names.get(_norm(e.get("title", ""))) or (re.sub(r"\d+$", "", sym) if sym else None)
        try:
            ts = pd.to_datetime(e.get("published"), format="%d-%b-%Y %H:%M:%S").tz_localize(E.IST)
        except Exception:
            ts = pd.NaT
        rows.append(dict(ts=ts, symbol=sym, company=e.get("title", ""), subject=subject[:160], category=cat[0],
                         reading=cat[1], alert=cat[2], link=link, text=desc[:1500]))
    return pd.DataFrame(rows, columns=["ts", "symbol", "company", "subject", "category", "reading", "alert", "link",
                                       "text"])


def catalysts(sym, now, hours=24):
    """This symbol's tagged filings in the last `hours` -> list of dict (newest first)."""
    a = announcements()
    if a.empty:
        return []
    x = a[(a.symbol == sym) & (a.ts >= now - pd.Timedelta(hours=hours))]
    return x.sort_values("ts", ascending=False).to_dict("records")
