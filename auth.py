"""
User login for the Streamlit app: local accounts in users.db, PBKDF2-SHA256 salted password hashes,
lockout after repeated failures, admin / user roles. The first visit creates the admin account.

Mobile + SMS OTP login: 6-digit codes, 5-minute expiry, hashed at rest, 5 tries per code, rate-limited sends.
Providers ([sms] in .streamlit/secrets.toml): twilio (Twilio Verify), msg91, fast2sms, or console (testing:
the code is printed in the server window).
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import requests
import streamlit as st

DATA_DIR = Path(os.environ.get("TRAD_DATA_DIR") or Path(__file__).parent)
DB = DATA_DIR / "users.db"
ITER = 240_000
MAX_FAILS, LOCK_MIN = 5, 5
OTP_TTL_MIN, OTP_TRIES, OTP_GAP_S, OTP_PER_HOUR = 5, 5, 30, 5
SCHEMA = """CREATE TABLE IF NOT EXISTS users(
  username TEXT PRIMARY KEY, name TEXT, role TEXT DEFAULT 'user', salt TEXT, pw TEXT,
  fails INTEGER DEFAULT 0, locked_until TEXT, created TEXT, last_login TEXT, mobile TEXT);
CREATE TABLE IF NOT EXISTS watchlists(username TEXT PRIMARY KEY, symbols TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS otps(
  mobile TEXT, code_hash TEXT, expires TEXT, tries INTEGER DEFAULT 0, sent_at TEXT, used INTEGER DEFAULT 0)"""


def _conn():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    if "mobile" not in [r[1] for r in c.execute("PRAGMA table_info(users)")]:  # upgrade older users.db
        c.execute("ALTER TABLE users ADD COLUMN mobile TEXT")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_mobile ON users(mobile) WHERE mobile IS NOT NULL")
    return c


def norm_mobile(m):
    """'98765 43210' / '+91-9876543210' / '919876543210' -> '+919876543210'; None if not a valid number."""
    d = re.sub(r"\D", "", m or "")
    if len(d) == 10 and d[0] in "6789":
        d = "91" + d
    if len(d) == 12 and d.startswith("91") and d[2] in "6789":
        return "+" + d
    if 11 <= len(d) <= 15 and (m or "").strip().startswith("+") and not d.startswith("91"):
        return "+" + d  # other countries, when entered with +country code
    return None


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITER).hex()


def _check_new(username, password):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username or ""):
        return "Username: 3-32 letters, digits, . _ -"
    if len(password or "") < 8 or password.isalpha() or password.isdigit():
        return "Password: at least 8 characters, mixing letters and numbers/symbols"
    return None


def has_users():
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0


def create_user(username, password, name="", role="user", mobile=""):
    err = _check_new(username, password)
    if err:
        return err
    mob = norm_mobile(mobile) if mobile else None
    if mobile and not mob:
        return "Mobile: enter a valid 10-digit Indian number (or +country code)"
    salt = secrets.token_hex(16)
    try:
        with _conn() as c:
            c.execute("INSERT INTO users(username, name, role, salt, pw, created, mobile) VALUES(?,?,?,?,?,?,?)",
                      (username.lower(), name or username, role, salt, _hash(password, salt),
                       datetime.now().isoformat(timespec="seconds"), mob))
    except sqlite3.IntegrityError:
        return "That username or mobile number is already registered"
    return None


def set_mobile(username, mobile):
    mob = norm_mobile(mobile) if mobile else None
    if mobile and not mob:
        return "Mobile: enter a valid 10-digit Indian number (or +country code)"
    try:
        with _conn() as c:
            if not c.execute("UPDATE users SET mobile=? WHERE username=?", (mob, username.lower())).rowcount:
                return "No such user"
    except sqlite3.IntegrityError:
        return "That mobile number belongs to another user"
    return None


# ---------------- Per-user watchlist ----------------
def get_watchlist(username):
    with _conn() as c:
        r = c.execute("SELECT symbols FROM watchlists WHERE username=?", (username.lower(),)).fetchone()
    return [s for s in (r["symbols"] if r else "").split(",") if s] if r else None


def set_watchlist(username, syms):
    clean = list(dict.fromkeys(s.strip().upper() for s in syms if s and s.strip()))[:40]
    with _conn() as c:
        c.execute("INSERT INTO watchlists(username, symbols, updated) VALUES(?,?,?) ON CONFLICT(username) DO UPDATE "
                  "SET symbols=excluded.symbols, updated=excluded.updated",
                  (username.lower(), ",".join(clean), datetime.now().isoformat(timespec="seconds")))
    return clean


# ---------------- SMS OTP ----------------
def _sms_cfg():
    cfg = {}
    try:
        cfg = dict(st.secrets["sms"])
    except Exception:
        pass
    for k in ("provider", "twilio_sid", "twilio_token", "twilio_verify_sid", "msg91_authkey", "msg91_template_id",
              "fast2sms_key"):
        cfg[k] = os.environ.get("SMS_" + k.upper(), cfg.get(k))
    return cfg


def sms_provider():
    return (_sms_cfg().get("provider") or "").lower() or None


def _mask(m):
    return m[:3] + "•" * (len(m) - 7) + m[-4:]


def _send_code(mobile, code, cfg):
    p = cfg["provider"].lower()
    if p == "console":
        print(f"[OTP testing] {mobile}: {code}", flush=True)
        return True, ""
    if p == "msg91":
        r = requests.post("https://control.msg91.com/api/v5/otp",
                          params={"template_id": cfg["msg91_template_id"], "mobile": mobile.lstrip("+"), "otp": code},
                          headers={"authkey": cfg["msg91_authkey"]}, timeout=10)
        return r.ok and r.json().get("type") == "success", r.text[:200]
    if p == "fast2sms":
        r = requests.get("https://www.fast2sms.com/dev/bulkV2", timeout=10,
                         params={"authorization": cfg["fast2sms_key"], "route": "otp", "variables_values": code,
                                 "numbers": mobile[-10:]})
        return r.ok and r.json().get("return") is True, r.text[:200]
    return False, f"Unknown SMS provider '{p}'"


def send_otp(mobile_raw):
    """-> (ok, message). The message never reveals whether the number is registered."""
    cfg = _sms_cfg()
    mob = norm_mobile(mobile_raw)
    if not cfg.get("provider"):
        return False, "SMS login is not configured yet (admin: add [sms] to .streamlit/secrets.toml)"
    if not mob:
        return False, "Enter a valid 10-digit mobile number"
    generic = f"If {_mask(mob)} is registered, an OTP has been sent. It is valid for {OTP_TTL_MIN} minutes."
    now = datetime.now()
    with _conn() as c:
        last = c.execute("SELECT sent_at FROM otps WHERE mobile=? ORDER BY sent_at DESC LIMIT 1", (mob,)).fetchone()
        if last and (now - datetime.fromisoformat(last["sent_at"])).total_seconds() < OTP_GAP_S:
            return False, f"Please wait {OTP_GAP_S} seconds before requesting another OTP"
        n_hour = c.execute("SELECT COUNT(*) FROM otps WHERE mobile=? AND sent_at>?",
                           (mob, (now - timedelta(hours=1)).isoformat())).fetchone()[0]
        if n_hour >= OTP_PER_HOUR:
            return False, "Too many OTP requests for this number. Try again in an hour."
        user = c.execute("SELECT username FROM users WHERE mobile=?", (mob,)).fetchone()
        c.execute("UPDATE otps SET used=1 WHERE mobile=? AND used=0", (mob,))
        if user is None:  # record the attempt for rate limiting, send nothing
            c.execute("INSERT INTO otps(mobile, code_hash, expires, sent_at, used) VALUES(?,?,?,?,1)",
                      (mob, "", now.isoformat(), now.isoformat()))
            return True, generic
        if cfg["provider"].lower() == "twilio":
            r = requests.post(f"https://verify.twilio.com/v2/Services/{cfg['twilio_verify_sid']}/Verifications",
                              data={"To": mob, "Channel": "sms"}, auth=(cfg["twilio_sid"], cfg["twilio_token"]), timeout=10)
            ok, detail = r.ok, r.text[:200]
            code_hash = "twilio"
        else:
            code = f"{secrets.randbelow(10 ** 6):06d}"
            ok, detail = _send_code(mob, code, cfg)
            code_hash = hashlib.sha256((mob + code).encode()).hexdigest()
        c.execute("INSERT INTO otps(mobile, code_hash, expires, sent_at, used) VALUES(?,?,?,?,?)",
                  (mob, code_hash, (now + timedelta(minutes=OTP_TTL_MIN)).isoformat(), now.isoformat(), 0 if ok else 1))
    if not ok:
        print(f"[OTP send failed] {_mask(mob)}: {detail}", flush=True)
        return False, "Could not send the SMS right now. Try again, or use password login."
    return True, generic


def verify_otp(mobile_raw, code):
    """-> (user dict or None, error text)."""
    mob = norm_mobile(mobile_raw)
    code = re.sub(r"\D", "", code or "")
    if not mob or len(code) != 6:
        return None, "Enter the 6-digit code"
    cfg = _sms_cfg()
    now = datetime.now()
    with _conn() as c:
        o = c.execute("SELECT rowid, * FROM otps WHERE mobile=? AND used=0 ORDER BY sent_at DESC LIMIT 1", (mob,)).fetchone()
        if o is None or datetime.fromisoformat(o["expires"]) < now:
            return None, "Code expired or not requested. Send a new OTP."
        if o["tries"] >= OTP_TRIES:
            c.execute("UPDATE otps SET used=1 WHERE rowid=?", (o["rowid"],))
            return None, "Too many wrong codes. Send a new OTP."
        if o["code_hash"] == "twilio":
            r = requests.post(f"https://verify.twilio.com/v2/Services/{cfg['twilio_verify_sid']}/VerificationCheck",
                              data={"To": mob, "Code": code}, auth=(cfg["twilio_sid"], cfg["twilio_token"]), timeout=10)
            good = r.ok and r.json().get("status") == "approved"
        else:
            good = hmac.compare_digest(hashlib.sha256((mob + code).encode()).hexdigest(), o["code_hash"])
        if not good:
            c.execute("UPDATE otps SET tries=tries+1 WHERE rowid=?", (o["rowid"],))
            left = OTP_TRIES - o["tries"] - 1
            return None, f"Wrong code ({left} tries left)" if left > 0 else "Too many wrong codes. Send a new OTP."
        c.execute("UPDATE otps SET used=1 WHERE rowid=?", (o["rowid"],))
        u = c.execute("SELECT * FROM users WHERE mobile=?", (mob,)).fetchone()
        if u is None:
            return None, "This number is not registered"
        c.execute("UPDATE users SET fails=0, locked_until=NULL, last_login=? WHERE username=?",
                  (now.isoformat(timespec="seconds"), u["username"]))
        return dict(username=u["username"], name=u["name"], role=u["role"]), ""


def set_password(username, password):
    err = _check_new(username, password)
    if err:
        return err
    salt = secrets.token_hex(16)
    with _conn() as c:
        c.execute("UPDATE users SET salt=?, pw=?, fails=0, locked_until=NULL WHERE username=?",
                  (salt, _hash(password, salt), username.lower()))
    return None


def verify(username, password):
    """-> (user dict or None, error text)."""
    u = (username or "").lower().strip()
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
        if r is None:
            _hash(password or "", "00" * 16)  # equalise timing for unknown users
            return None, "Wrong username or password"
        if r["locked_until"] and datetime.fromisoformat(r["locked_until"]) > datetime.now():
            return None, f"Too many attempts. Try again after {datetime.fromisoformat(r['locked_until']):%H:%M}"
        if not hmac.compare_digest(_hash(password or "", r["salt"]), r["pw"]):
            fails = (r["fails"] or 0) + 1
            lock = (datetime.now() + timedelta(minutes=LOCK_MIN)).isoformat() if fails >= MAX_FAILS else None
            c.execute("UPDATE users SET fails=?, locked_until=? WHERE username=?", (0 if lock else fails, lock, u))
            return None, "Wrong username or password" + (f" (locked for {LOCK_MIN} min)" if lock else "")
        c.execute("UPDATE users SET fails=0, locked_until=NULL, last_login=? WHERE username=?",
                  (datetime.now().isoformat(timespec="seconds"), u))
        return dict(username=u, name=r["name"], role=r["role"]), ""


def list_users():
    with _conn() as c:
        return [dict(r) | {"salt": None, "pw": None} for r in c.execute(
            "SELECT username, name, mobile, role, created, last_login, locked_until FROM users ORDER BY username")]


def delete_user(username):
    with _conn() as c:
        c.execute("DELETE FROM users WHERE username=?", (username.lower(),))


def set_role(username, role):
    with _conn() as c:
        c.execute("UPDATE users SET role=? WHERE username=?", (role, username.lower()))


# ---------------- Streamlit gate ----------------
def gate():
    """Render login / first-run setup until authenticated. -> user dict (stops the script otherwise)."""
    if st.session_state.get("user"):
        return st.session_state["user"]
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown("## 📈 Intraday CALL / PUT Engine")
        if not has_users():
            st.info("First run: create the **admin** account.")
            with st.form("setup"):
                name = st.text_input("Your name")
                mobile = st.text_input("Mobile number (for OTP login)", placeholder="10-digit mobile")
                user = st.text_input("Username", "admin")
                pw = st.text_input("Password", type="password")
                pw2 = st.text_input("Confirm password", type="password")
                if st.form_submit_button("Create admin account", type="primary", use_container_width=True):
                    if pw != pw2:
                        st.error("Passwords do not match")
                    else:
                        err = create_user(user, pw, name, role="admin", mobile=mobile)
                        if err:
                            st.error(err)
                        else:
                            st.session_state["user"] = verify(user, pw)[0]
                            st.rerun()
        else:
            t_otp, t_pw = st.tabs(["📱 Mobile OTP", "🔑 Password"])
            with t_otp:
                if not sms_provider():
                    st.info("SMS OTP isn't set up yet. Use **Password** login; the admin can enable SMS in secrets.toml.")
                mob = st.text_input("Mobile number", key="otp_mobile", placeholder="10-digit mobile")
                if st.button("Send OTP", use_container_width=True, disabled=not sms_provider()):
                    ok, msg = send_otp(mob)
                    (st.success if ok else st.error)(msg)
                    st.session_state["otp_sent"] = ok
                if st.session_state.get("otp_sent"):
                    with st.form("otp_form"):
                        code = st.text_input("6-digit OTP", max_chars=6, key="otp_code")
                        if st.form_submit_button("Verify & log in", type="primary", use_container_width=True):
                            u, err = verify_otp(mob, code)
                            if u:
                                st.session_state.pop("otp_sent", None)
                                st.session_state["user"] = u
                                st.rerun()
                            st.error(err)
                if sms_provider() == "console":
                    st.caption("🧪 Testing mode: the OTP is printed in the server window, not sent by SMS.")
            with t_pw:
                with st.form("login"):
                    user = st.text_input("Username")
                    pw = st.text_input("Password", type="password")
                    if st.form_submit_button("Log in", type="primary", use_container_width=True):
                        u, err = verify(user, pw)
                        if u:
                            st.session_state["user"] = u
                            st.rerun()
                        st.error(err)
            st.caption("Ask your admin for an account. Educational tool, not financial advice.")
    st.stop()


def logout():
    for k in ("user", "last_results"):
        st.session_state.pop(k, None)
    st.rerun()
