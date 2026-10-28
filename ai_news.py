"""
AI reading of NSE company filings (Google Gemini API, free tier): for each new filing on a watched stock, the model
returns its likely direction for the stock, how material it is and whether it looks like a surprise, plus one line of
why. Verdicts are stored once per filing in the journal (ai_notes), so a filing is never sent twice, and a daily cap
keeps usage inside the free quota. Context only: shown on the call cards and in news alerts; it changes no grade or
TRADE/PAPER label until a back-test shows it helps. The filings are public; nothing private is sent.
Key: env GEMINI_API_KEY or secrets [gemini] api_key (optional [gemini] model).
"""
import hashlib
import json
import os

import pandas as pd
import requests

import engine as E
import paper

MODELS = ["gemini-flash-lite-latest", "gemini-3.5-flash-lite"]  # free tier; "-latest" follows Google's current Flash-Lite
DAILY_CAP = int(os.environ.get("AI_DAILY_CAP", "300"))
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
SQL = """CREATE TABLE IF NOT EXISTS ai_notes(key TEXT PRIMARY KEY, ts TEXT, day TEXT, symbol TEXT, subject TEXT,
  direction TEXT, materiality TEXT, surprise INTEGER, note TEXT, model TEXT)"""
SYSTEM = ("You read Indian stock-exchange (NSE) company filings for an options trader. For the filing given, judge "
          "its likely effect on the company's share price over the next 1-2 sessions. direction: positive, negative "
          "or neutral. materiality: high (likely to move the price on its own), medium, or low (routine paperwork). "
          "surprise: true only if it is unscheduled news, not a routine or pre-announced item. note: one short "
          "sentence with the concrete fact that matters (amounts, names). Judge only from the text; if the text "
          "does not say enough, use neutral and low.")
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


def classify(text, key):
    """One filing -> dict(direction, materiality, surprise, note, model) or None (no key, quota hit, error)."""
    body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": text[:3000]}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 300, "responseMimeType": "application/json",
                                 "responseSchema": SCHEMA}}
    for m in _models():
        try:
            r = requests.post(URL.format(m=m), headers={"x-goog-api-key": key}, json=body, timeout=30)
        except requests.RequestException:
            continue
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
        if used + n >= DAILY_CAP:
            break
        text = (f"Company: {row.get('company')} (NSE: {row['symbol']})\nFiled: {row['ts']:%d-%b-%Y %H:%M}\n"
                f"Subject: {row.get('subject')}\nDetails: {row.get('text') or row.get('subject')}")
        v = classify(text, key)
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
