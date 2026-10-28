"""
AI reading of NSE company filings (Google Gemini API, free tier): for each new filing on a watched stock, the model
returns its likely direction for the stock, how material it is and whether it looks like a surprise, plus one line of
why. For results, orders, deals and clarifications it reads the attached PDF (first pages: the results table or the
press release), not just NSE's one-line summary. The company's name is withheld from the model, so it judges the
document rather than recalling how the stock later moved (keeps live verdicts the same as the back-tested ones).
Verdicts are stored once per filing in the journal (ai_notes), so a filing is never sent twice, and a daily cap
keeps usage inside the free quota. Context only: shown on the call cards and in news alerts; it changes no grade or
TRADE/PAPER label until a back-test shows it helps. The filings are public; nothing private is sent.
Key: env GEMINI_API_KEY or secrets [gemini] api_key (optional [gemini] model).
"""
import hashlib
import io
import json
import os
import re

import pandas as pd
import requests

import engine as E
import net
import paper

MODELS = ["gemini-flash-lite-latest", "gemini-3.5-flash-lite"]  # free tier; "-latest" follows Google's current Flash-Lite
DAILY_CAP = int(os.environ.get("AI_DAILY_CAP", "300"))
PER_PASS = 20  # filings read per engine pass (each may download a PDF): keeps a pass short
PDF_CATS = {"Results", "Order win", "Acquisition / merger", "Buyback", "Fund raising", "Unusual move clarification"}
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
SQL = """CREATE TABLE IF NOT EXISTS ai_notes(key TEXT PRIMARY KEY, ts TEXT, day TEXT, symbol TEXT, subject TEXT,
  direction TEXT, materiality TEXT, surprise INTEGER, note TEXT, model TEXT)"""
SYSTEM = ("You read Indian stock-exchange (NSE) company filings for an options trader. For the filing given, judge "
          "its likely effect on the company's share price over the next 1-2 sessions. The company's name is "
          "withheld: judge only from the document. direction: positive, negative or neutral. materiality: high "
          "(likely to move the price on its own), medium, or low (routine paperwork, a notice of a future meeting). "
          "For financial results compare with the same quarter last year and the previous quarter as the document "
          "shows them (revenue, profit, margins, one-off items). For orders, deals and fund raising weigh the size "
          "against the company's revenue or net worth when the document gives them. surprise: true only if it is "
          "unscheduled news, not a routine or pre-announced item. note: one short sentence with the concrete fact "
          "that matters (amounts, growth rates). If the text does not say enough, use neutral and low.")
SCHEMA = {"type": "OBJECT", "properties": {
    "direction": {"type": "STRING", "enum": ["positive", "negative", "neutral"]},
    "materiality": {"type": "STRING", "enum": ["high", "medium", "low"]},
    "surprise": {"type": "BOOLEAN"},
    "note": {"type": "STRING"}}, "required": ["direction", "materiality", "surprise", "note"]}


def api_key():
    k = os.environ.get("GEMINI_API_KEY")
    if not k:
        try:
            import streamlit as st
            k = st.secrets["gemini"]["api_key"]
        except Exception:
            k = None
    return k


def _models():
    try:
        import streamlit as st
        m = st.secrets["gemini"].get("model")
    except Exception:
        m = None
    return [m] + MODELS if m else MODELS


def pdf_text(url, pages=8, chars=15000):
    """Text of a filing's attached PDF, first `pages` pages (cover letter, results table, press release). '' when
    there is none, it is too big or it is a scanned image."""
    if not url or not str(url).lower().endswith(".pdf"):
        return ""
    try:
        r = net.session("nse").get(url, headers=E.NSE_H, timeout=30)
        if not r.ok or len(r.content) > 20e6:
            return ""
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(r.content))
        out = "\n".join((pg.extract_text() or "") for pg in rd.pages[:pages])
    except Exception:
        return ""
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", out)).strip()[:chars]


def _anon(text, company, symbol):
    """Withhold the company's name (full, without "Limited", and the NSE symbol)."""
    c = str(company or "").strip()
    names = {c, re.sub(r"\s+(limited|ltd\.?)$", "", c, flags=re.I), str(symbol or "")}
    for w in sorted((w for w in names if len(w) > 2), key=len, reverse=True):
        text = re.sub(re.escape(w), "the company", text, flags=re.I)
    return text


def filing_prompt(row, body=""):
    """The text the model sees for one filing (same for live verdicts and the back-test)."""
    t = (f"Filing category: {row.get('category')}\nSubject: {row.get('subject')}\n"
         f"Summary: {row.get('text') or row.get('subject')}")
    if body:
        t += f"\nAttached document (first pages):\n{body}"
    return _anon(t, row.get("company"), row.get("symbol"))


last_error = [None]  # (status, body) of the last failed call, for the back-test's quota handling


def classify(text, key, models=None):
    """One filing -> dict(direction, materiality, surprise, note, model) or None (no key, quota hit, error)."""
    body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": text[:20000]}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 300, "responseMimeType": "application/json",
                                 "responseSchema": SCHEMA}}
    for m in models or _models():
        try:
            r = requests.post(URL.format(m=m), headers={"x-goog-api-key": key}, json=body, timeout=60)
        except requests.RequestException as e:
            last_error[0] = (0, str(e))
            continue
        if not r.ok:
            last_error[0] = (r.status_code, r.text[:2000])
        if r.status_code in (404, 429, 500, 503):  # model unavailable or free quota used up: try the next one
            continue
        if not r.ok:
            return None
        try:
            out = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
            return dict(direction=out["direction"], materiality=out["materiality"], surprise=bool(out["surprise"]),
                        note=str(out["note"])[:200], model=m)
        except Exception:
            continue
    return None


def _key(row):
    return hashlib.sha1(f"{row.get('link')}|{row.get('subject')}|{row.get('ts')}".encode()).hexdigest()[:20]


def score_filings(symbols, now=None, hours=24):
    """Read every not-yet-scored filing of `symbols` from the last `hours` (NSE announcements feed). -> new verdicts."""
    key = api_key()
    if not key:
        return 0
    import news
    now = now or pd.Timestamp.now(tz=E.IST)
    an = news.announcements()
    if an.empty:
        return 0
    an = an[an.symbol.isin(set(symbols)) & (an.ts >= now - pd.Timedelta(hours=hours))]
    if an.empty:
        return 0
    c = paper.conn()
    c.executescript(SQL)
    day = now.date().isoformat()
    used = c.execute("SELECT COUNT(*) FROM ai_notes WHERE day=?", (day,)).fetchone()[0]
    have = {r[0] for r in c.execute("SELECT key FROM ai_notes WHERE day>=?",
                                    ((now - pd.Timedelta(days=2)).date().isoformat(),)).fetchall()}
    n = 0
    for row in an.sort_values("ts").to_dict("records"):
        k = _key(row)
        if k in have:
            continue
        if used + n >= DAILY_CAP or n >= PER_PASS:
            break
        body = pdf_text(row.get("link")) if row.get("category") in PDF_CATS else ""
        v = classify(filing_prompt(row, body), key)
        if v is None:
            break  # quota or service trouble: try again on the next pass
        c.execute("INSERT OR IGNORE INTO ai_notes(key, ts, day, symbol, subject, direction, materiality, surprise, "
                  "note, model) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (k, row["ts"].isoformat(), day, row["symbol"], str(row.get("subject"))[:160], v["direction"],
                   v["materiality"], int(v["surprise"]), v["note"], v["model"]))
        n += 1
    c.commit()
    c.close()
    return n


def notes(symbol, now=None, hours=24):
    """This symbol's AI verdicts from the last `hours`, most material first -> list of dicts."""
    now = now or pd.Timestamp.now(tz=E.IST)
    try:
        c = paper.conn()
        c.executescript(SQL)
        rows = c.execute("SELECT * FROM ai_notes WHERE symbol=? AND ts>=? ORDER BY ts DESC",
                         (symbol, (now - pd.Timedelta(hours=hours)).isoformat())).fetchall()
        c.close()
    except Exception:
        return []
    rank = {"high": 0, "medium": 1, "low": 2}
    return sorted((dict(r) for r in rows), key=lambda r: rank.get(r["materiality"], 3))


def line(symbol, kind=None, now=None):
    """One-line card text for the most material recent filing, with a warning when it argues against the call."""
    ns = [n for n in notes(symbol, now) if n["materiality"] != "low"]
    if not ns:
        return ""
    n = ns[0]
    icon = {"positive": "🟢", "negative": "🔴"}.get(n["direction"], "⚪")
    against = kind and ((kind == "CE" and n["direction"] == "negative") or (kind == "PE" and n["direction"] == "positive"))
    return (("⚠ AI: this call goes against fresh news · " if against else "🧠 AI news: ")
            + f"{icon} {n['direction']} · {n['materiality']}{' · surprise' if n['surprise'] else ''} · {n['note']}")
