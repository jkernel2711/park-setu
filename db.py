"""ParkSetu data layer. SQLite, demo-grade, one file."""
from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "parksetu.db"

SUV_TYPES = {"suv", "luxury", "mpv"}


@contextmanager
def conn(write: bool = False):
    cx = sqlite3.connect(DB_PATH)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys = ON")
    try:
        yield cx
        if write:
            cx.commit()
    except Exception:
        cx.rollback()
        raise
    finally:
        cx.close()


def q(sql: str, args=(), one: bool = False):
    with conn() as cx:
        cur = cx.execute(sql, args)
        rows = cur.fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql: str, args=()):
    with conn(write=True) as cx:
        cur = cx.execute(sql, args)
        return cur.lastrowid


def executemany(sql: str, rows):
    with conn(write=True) as cx:
        cx.executemany(sql, rows)


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    value = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(value)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def norm_plate(plate: str) -> str:
    raw = "".join(ch for ch in (plate or "").upper() if ch.isalnum())
    if len(raw) < 8:
        return " ".join(raw[i:i + 2] for i in range(0, len(raw), 2)).strip()
    # XX00XX0000 -> XX 00 XX 0000
    return f"{raw[:2]} {raw[2:4]} {raw[4:6]} {raw[6:]}"


def inr(amount: float) -> str:
    n = float(amount or 0)
    if abs(n - round(n)) < 0.001:
        return f"₹{int(round(n)):,}"
    return f"₹{n:,.2f}"


def init_schema():
    with conn(write=True) as cx:
        cx.executescript(
            """
            CREATE TABLE IF NOT EXISTS locations (
                id INTEGER PRIMARY KEY,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                city TEXT NOT NULL,
                address TEXT NOT NULL,
                total_spots INTEGER NOT NULL,
                free_minutes INTEGER NOT NULL DEFAULT 0,
                hourly_rate_inr REAL NOT NULL DEFAULT 0,
                daily_cap_inr REAL NOT NULL DEFAULT 0,
                visitor_fee_inr REAL NOT NULL DEFAULT 0,
                suv_multiplier REAL NOT NULL DEFAULT 1.2
            );

            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY,
                location_id INTEGER NOT NULL REFERENCES locations(id),
                code TEXT NOT NULL,
                zone TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'standard',
                status TEXT NOT NULL DEFAULT 'free',
                UNIQUE(location_id, code)
            );

            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY,
                plate TEXT UNIQUE NOT NULL,
                owner_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                model TEXT NOT NULL,
                color TEXT NOT NULL,
                vtype TEXT NOT NULL,
                has_fastag INTEGER NOT NULL DEFAULT 1,
                fastag_balance REAL NOT NULL DEFAULT 0,
                is_blacklisted INTEGER NOT NULL DEFAULT 0,
                resident_of INTEGER REFERENCES locations(id)
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                location_id INTEGER REFERENCES locations(id),
                vehicle_id INTEGER REFERENCES vehicles(id),
                phone TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY,
                vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
                location_id INTEGER NOT NULL REFERENCES locations(id),
                slot_id INTEGER REFERENCES slots(id),
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                duration_min INTEGER,
                amount_inr REAL NOT NULL DEFAULT 0,
                payment_method TEXT,
                payment_status TEXT NOT NULL DEFAULT 'pending',
                visitor_request_id INTEGER,
                note TEXT
            );

            CREATE TABLE IF NOT EXISTS visitor_requests (
                id INTEGER PRIMARY KEY,
                location_id INTEGER NOT NULL REFERENCES locations(id),
                host_name TEXT NOT NULL,
                host_flat TEXT NOT NULL,
                visitor_name TEXT NOT NULL,
                visitor_phone TEXT NOT NULL,
                plate TEXT NOT NULL,
                purpose TEXT NOT NULL,
                visit_date TEXT NOT NULL,
                window_from TEXT NOT NULL,
                window_to TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                pass_code TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                location_id INTEGER NOT NULL REFERENCES locations(id),
                day TEXT NOT NULL,
                entries INTEGER NOT NULL DEFAULT 0,
                exits INTEGER NOT NULL DEFAULT 0,
                revenue_inr REAL NOT NULL DEFAULT 0,
                peak_occupancy INTEGER NOT NULL DEFAULT 0,
                avg_duration_min REAL NOT NULL DEFAULT 0,
                fastag_payments INTEGER NOT NULL DEFAULT 0,
                counter_payments INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (location_id, day)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                ts TEXT NOT NULL,
                location_id INTEGER REFERENCES locations(id),
                plate TEXT,
                kind TEXT NOT NULL,
                message TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(location_id, exit_time);
            CREATE INDEX IF NOT EXISTS idx_sessions_vehicle ON sessions(vehicle_id, entry_time);
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
            CREATE INDEX IF NOT EXISTS idx_visitors_day ON visitor_requests(location_id, visit_date, status);
            """
        )


def log_event(location_id, plate, kind, message, ts=None):
    execute(
        "INSERT INTO events (ts, location_id, plate, kind, message) VALUES (?,?,?,?,?)",
        (iso(ts or now()), location_id, plate, kind, message),
    )


def mall_fee(entry: datetime, exit: datetime, loc, vtype: str) -> float:
    minutes = max(0, (exit - entry).total_seconds() / 60)
    billable = max(0, minutes - (loc["free_minutes"] or 0))
    if billable <= 0:
        return 0.0
    hours = math.ceil(billable / 60)
    rate = float(loc["hourly_rate_inr"])
    if (vtype or "").lower() in SUV_TYPES:
        rate *= float(loc["suv_multiplier"] or 1)
    amount = hours * rate
    cap = float(loc["daily_cap_inr"] or 0)
    if cap:
        amount = min(amount, cap)
    return round(amount, 2)


def community_fee(is_resident: bool, approved_visitor: bool, loc) -> float:
    if is_resident:
        return 0.0
    fee = float(loc["visitor_fee_inr"] or 0)
    if approved_visitor:
        return fee
    return round(fee * 2, 2)  # walk-in penalty


def dicts(rows):
    return [dict(r) for r in rows]


def rowd(row):
    return dict(row) if row else None
