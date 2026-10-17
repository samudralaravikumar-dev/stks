"""
Storage for the trade journal, track record, alerts and users.

Default: SQLite files next to the app (trading.db, users.db). When a Postgres URL is configured (env DATABASE_URL or
Streamlit secrets [db] url, e.g. a free Neon database), every copy of the app uses that one shared database instead:
the portal on Streamlit Cloud (whose files are wiped on each restart), the scheduled alert job on GitHub Actions and
the local app all see the same track record, loss limits and alert history.

The modules keep writing SQLite-style SQL; this adapter translates the few differences for Postgres (placeholders,
INSERT OR IGNORE, AUTOINCREMENT, REAL precision, two-argument MAX, SUM of a boolean, rowid).

Transport: a normal Postgres connection (port 5432) where the network allows it (Streamlit Cloud, GitHub Actions);
otherwise Neon's HTTPS SQL endpoint (port 443), e.g. on office networks that block database ports. If neither works
the app falls back to the local SQLite file and says so (status()), rather than stopping.
"""
import json
import math
import os
import re
import sqlite3
import threading
import time
from decimal import Decimal

import numpy as np
import pandas as pd

_local = threading.local()
_ready = set()  # schema scripts already applied to Postgres in this process
_lock = threading.Lock()
_state = {"error": None, "until": 0.0, "mode": None}  # mode: None (try direct first) / "pg" / "http"
RETRY_S = 60  # after a failed connect, use SQLite for a minute before trying Postgres again


def url():
    u = os.environ.get("DATABASE_URL")
    if not u:
        try:
            import streamlit as st
            u = st.secrets["db"]["url"]
        except Exception:
            u = None
    return u.strip() if isinstance(u, str) and u.strip().startswith("postgres") else None


def status():
    """-> ('Postgres (shared)' | 'Postgres over HTTPS (shared)' | 'local file', error or None)."""
    if not url():
        return "local file", None
    if _state["error"] and time.time() < _state["until"]:
        return "local file", _state["error"]
    return ("Postgres over HTTPS (shared)" if _state["mode"] == "http" else "Postgres (shared)"), None


class HttpIntegrityError(Exception):
    pass


class HttpError(Exception):
    pass


try:
    import psycopg
    IntegrityError = (sqlite3.IntegrityError, psycopg.errors.IntegrityError, HttpIntegrityError)
except Exception:  # driver not installed: SQLite or HTTPS only
    psycopg = None
    IntegrityError = (sqlite3.IntegrityError, HttpIntegrityError)


# ---------------- SQL translation (SQLite dialect -> Postgres) ----------------
def _ddl(sql):
    s = re.sub(r"INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY", sql, flags=re.I)
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)  # SQLite REAL is 8 bytes; Postgres REAL is only 4
    # SQLite's implicit rowid (used by the OTP table) becomes a real column
    s = re.sub(r"(CREATE TABLE IF NOT EXISTS otps\()", r"\1rowid BIGSERIAL, ", s, flags=re.I)
    return s


def _sql(sql, dollar=False):
    s = sql
    ignore = bool(re.search(r"INSERT\s+OR\s+IGNORE", s, re.I))
    s = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", s, flags=re.I)
    if re.match(r"\s*CREATE\s+TABLE", s, re.I):
        s = _ddl(s)
    s = re.sub(r"\bMAX\(\s*([\w.]+)\s*,\s*([\w.]+)\s*\)", r"GREATEST(\1, \2)", s)  # scalar 2-arg MAX
    s = re.sub(r"\bSUM\(\s*(\w+)\s+IN\s+(\([^)]*\))\s*\)", r"SUM(CASE WHEN \1 IN \2 THEN 1 ELSE 0 END)", s)
    if dollar:  # HTTPS endpoint: $1, $2, ... placeholders, no % escaping
        n = iter(range(1, 10_000))
        s = re.sub(r"\?", lambda m: f"${next(n)}", s)
    else:
        s = s.replace("%", "%%").replace("?", "%s")
    if ignore:
        s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return s


def _py(v):
    if isinstance(v, (bool, np.bool_)):
        return int(v)
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None  # SQLite stores NaN as NULL: keep the same meaning
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v


class Row(dict):
    """Row usable both ways, like sqlite3.Row: r["col"] and r[0]."""
    __slots__ = ("_v",)

    def __init__(self, cols, vals):
        vals = tuple(float(x) if isinstance(x, Decimal) else x for x in vals)
        super().__init__(zip(cols, vals))
        self._v = vals

    def __getitem__(self, k):
        return self._v[k] if isinstance(k, int) else dict.__getitem__(self, k)

    def __iter__(self):  # tuple-unpacking like sqlite3.Row: k, n = row
        return iter(self._v)


def _row_factory(cur):
    cols = [d.name for d in (cur.description or [])]
    return lambda vals: Row(cols, vals)


class _Cursor:
    def __init__(self, cur):
        self._c = cur
        self.rowcount = cur.rowcount
        self.description = cur.description

    def fetchone(self):
        return self._c.fetchone() if self._c.description else None

    def fetchall(self):
        return self._c.fetchall() if self._c.description else []

    def __iter__(self):
        return iter(self.fetchall())


class PgConn:
    """The slice of the sqlite3.Connection API the app uses, on a per-thread autocommit Postgres connection."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        q = _sql(sql)
        args = tuple(_py(v) for v in params) if params else None
        try:
            cur = self._raw.execute(q, args)
        except psycopg.OperationalError:  # dropped (Neon suspends idle computes): reconnect once and retry
            self._raw = _reconnect()
            cur = self._raw.execute(q, args)
        return _Cursor(cur)

    def executescript(self, script):
        key = hash(script)
        if key in _ready:
            return
        for stmt in [x.strip() for x in script.split(";") if x.strip()]:
            self.execute(stmt)
        _ready.add(key)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass  # the per-thread connection is reused

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Desc:
    def __init__(self, name):
        self.name = name


_INT, _FLOAT, _BOOL = {20, 21, 23, 26}, {700, 701, 1700}, {16}


def _val(v, oid):
    if v is None or not isinstance(v, str):
        return v
    try:
        return int(v) if oid in _INT else float(v) if oid in _FLOAT else v in ("t", "true") if oid in _BOOL else v
    except ValueError:
        return v


class _HttpCursor:
    def __init__(self, res):
        fields = res.get("fields") or []
        self.description = [_Desc(f["name"]) for f in fields] or None
        self.rowcount = res.get("rowCount") if res.get("rowCount") is not None else -1
        cols, oids = [f["name"] for f in fields], [f.get("dataTypeID") for f in fields]
        self._rows = [Row(cols, [_val(v, o) for v, o in zip(r, oids)]) for r in (res.get("rows") or [])]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class HttpConn(PgConn):
    """Same API over Neon's HTTPS SQL endpoint (one POST per statement, keep-alive session)."""

    def __init__(self):
        super().__init__(None)
        if getattr(_local, "http", None) is None:
            import requests
            _local.http = requests.Session()
        self._s = _local.http
        u = url()
        self._endpoint = f"https://{u.split('@', 1)[1].split('/', 1)[0].split(':')[0]}/sql"
        self._hdr = {"Neon-Connection-String": u, "Neon-Array-Mode": "true", "Neon-Raw-Text-Output": "true",
                     "Content-Type": "application/json"}

    def execute(self, sql, params=()):
        body = {"query": _sql(sql, dollar=True), "params": [_py(v) for v in params] if params else []}
        last = None
        for attempt in range(3):
            try:
                r = self._s.post(self._endpoint, data=json.dumps(body, default=str), headers=self._hdr, timeout=20)
            except Exception as e:  # network blip: retry
                last = e
                time.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code == 200:
                return _HttpCursor(r.json())
            try:
                err = r.json()
            except Exception:
                err = {"message": r.text[:200]}
            if str(err.get("code", "")).startswith("23"):
                raise HttpIntegrityError(err.get("message"))
            if r.status_code < 500:
                raise HttpError(f"{r.status_code}: {err.get('message')}")
            last = HttpError(f"{r.status_code}: {err.get('message')}")
            time.sleep(0.5 * (attempt + 1))
        raise last


def _reconnect():
    old = getattr(_local, "pg", None)
    if old is not None:
        try:
            old.close()
        except Exception:
            pass
    # prepare_threshold=None: no server-side prepared statements, so Neon's pooled (PgBouncer) endpoint works
    _local.pg = psycopg.connect(url(), autocommit=True, prepare_threshold=None, row_factory=_row_factory,
                                connect_timeout=6)
    return _local.pg


def connect(sqlite_path, timeout=15):
    """Postgres when configured and reachable (direct, else HTTPS), else the SQLite file. Rows work as r["col"] / r[0]."""
    if url() and time.time() >= _state["until"]:
        if _state["mode"] != "http" and psycopg is not None:
            try:
                raw = getattr(_local, "pg", None)
                if raw is None or raw.closed:
                    raw = _reconnect()
                _state.update(error=None, mode="pg")
                return PgConn(raw)
            except Exception as e:
                if _state["mode"] == "pg":  # worked before in this process: a temporary outage, not a blocked port
                    _state.update(error=f"{type(e).__name__}: {str(e)[:160]}", until=time.time() + RETRY_S)
                    return _sqlite(sqlite_path, timeout)
                _state["mode"] = "http"  # direct port blocked here: use HTTPS from now on in this process
        try:
            c = HttpConn()
            if not _state.get("http_ok"):
                c.execute("SELECT 1")
                _state["http_ok"] = True
            _state["error"] = None
            return c
        except Exception as e:
            _state.update(error=f"{type(e).__name__}: {str(e)[:160]}", until=time.time() + RETRY_S)
    return _sqlite(sqlite_path, timeout)


def _sqlite(sqlite_path, timeout):
    c = sqlite3.connect(sqlite_path, timeout=timeout)
    c.row_factory = sqlite3.Row
    return c


def is_pg(c):
    return isinstance(c, PgConn)  # HttpConn included


def read_df(c, sql, params=()):
    """pd.read_sql_query for either backend."""
    if not is_pg(c):
        return pd.read_sql_query(sql, c, params=params or None)
    cur = c.execute(sql, params)
    cols = [d.name for d in (cur.description or [])]
    return pd.DataFrame([tuple(r._v) for r in cur.fetchall()], columns=cols)
