"""ParkSetu — India parking control room. Demo, not production."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, g, jsonify, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash

from db import (
    community_fee, dicts, execute, init_schema, inr, iso, log_event,
    mall_fee, norm_plate, now, parse_dt, q, rowd,
)
from seed import main as seed_main
from db import DB_PATH

app = Flask(__name__)
app.secret_key = "parksetu-demo-key-not-for-production"


def current_user():
    if "user_id" not in session:
        return None
    return rowd(q("SELECT * FROM users WHERE id=?", (session["user_id"],), one=True))


def login_required(*roles):
    def deco(fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            user = current_user()
            if not user:
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Sign in first"}), 401
                return redirect(url_for("login"))
            if roles and user["role"] not in roles:
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Not allowed for this desk"}), 403
                return redirect(url_for("home"))
            g.user = user
            return fn(*args, **kwargs)
        return inner
    return deco


@app.context_processor
def inject():
    return {"user": current_user(), "inr": inr}


@app.route("/")
def home():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    return redirect(url_for({
        "admin": "admin_desk",
        "mall_manager": "mall_desk",
        "community_manager": "community_desk",
        "user": "user_desk",
    }[user["role"]]))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if current_user():
            return redirect(url_for("home"))
        return render_template("login.html")
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = rowd(q("SELECT * FROM users WHERE username=?", (username,), one=True))
    if not user or not check_password_hash(user["password_hash"], password):
        if request.is_json:
            return jsonify({"ok": False, "error": "Wrong desk code. Try a demo login."}), 400
        return render_template("login.html", error="Wrong desk code.")
    session.clear()
    session["user_id"] = user["id"]
    if request.is_json:
        return jsonify({"ok": True, "next": url_for("home")})
    return redirect(url_for("home"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin")
@login_required("admin")
def admin_desk():
    return render_template("admin.html")


@app.route("/mall")
@login_required("mall_manager")
def mall_desk():
    loc = rowd(q("SELECT * FROM locations WHERE id=?", (g.user["location_id"],), one=True))
    return render_template("mall.html", location=loc)


@app.route("/community")
@login_required("community_manager")
def community_desk():
    loc = rowd(q("SELECT * FROM locations WHERE id=?", (g.user["location_id"],), one=True))
    return render_template("community.html", location=loc)


@app.route("/me")
@login_required("user")
def user_desk():
    veh = rowd(q("SELECT * FROM vehicles WHERE id=?", (g.user["vehicle_id"],), one=True))
    return render_template("user.html", vehicle=veh)


# ── helpers ──────────────────────────────────────────────

def scoped_location_id():
    user = g.user
    if user["role"] == "admin":
        raw = request.args.get("location_id") or (request.get_json(silent=True) or {}).get("location_id")
        return int(raw) if raw else None
    return user["location_id"]


def dur_label(minutes) -> str:
    minutes = int(minutes or 0)
    hours, mins = divmod(max(0, minutes), 60)
    return f"{hours}h {mins:02d}m" if hours else f"{mins}m"


def today_stats(lid=None) -> dict:
    day = now().strftime("%Y-%m-%d")
    loc_sql = " AND location_id=?" if lid else ""
    args = (day, lid) if lid else (day,)
    entered = q(
        f"SELECT COUNT(*) c FROM sessions WHERE substr(entry_time,1,10)=?{loc_sql}",
        args, one=True,
    )["c"]
    done = q(
        f"""SELECT COUNT(*) c,
                   COALESCE(SUM(amount_inr),0) rev,
                   COALESCE(AVG(duration_min),0) avgd,
                   SUM(CASE WHEN payment_method='fastag' THEN 1 ELSE 0 END) ft,
                   SUM(CASE WHEN payment_method IN ('upi','cash') THEN 1 ELSE 0 END) counter
            FROM sessions
            WHERE exit_time IS NOT NULL AND substr(exit_time,1,10)=?{loc_sql}""",
        args, one=True,
    )
    inside_sql = "SELECT COUNT(*) c FROM sessions WHERE exit_time IS NULL" + (" AND location_id=?" if lid else "")
    inside = q(inside_sql, (lid,) if lid else (), one=True)["c"]
    spots = q(
        "SELECT COALESCE(SUM(total_spots),0) t FROM locations" + (" WHERE id=?" if lid else ""),
        (lid,) if lid else (), one=True,
    )["t"]
    over_sql = """
        SELECT COUNT(*) c FROM sessions s
        JOIN vehicles v ON v.id = s.vehicle_id
        JOIN locations l ON l.id = s.location_id
        WHERE s.exit_time IS NULL AND (
            (l.kind='mall' AND (julianday('now','localtime') - julianday(s.entry_time)) * 24 > 4)
            OR (l.kind='community'
                AND (v.resident_of IS NULL OR v.resident_of != s.location_id)
                AND (julianday('now','localtime') - julianday(s.entry_time)) * 24 > 3)
        )
    """
    over_args = ()
    if lid:
        over_sql += " AND s.location_id=?"
        over_args = (lid,)
    overstay = q(over_sql, over_args, one=True)["c"]
    longest = rowd(q(
        PARKED_SQL + " WHERE s.exit_time IS NULL"
        + (" AND s.location_id=?" if lid else "")
        + " ORDER BY s.entry_time ASC LIMIT 1",
        (lid,) if lid else (), one=True,
    ))
    return {
        "day": day,
        "entered": entered,
        "exited": done["c"] or 0,
        "revenue": round(done["rev"] or 0, 2),
        "revenue_label": inr(done["rev"] or 0),
        "avg_min": int(round(done["avgd"] or 0)),
        "avg_label": dur_label(done["avgd"] or 0),
        "inside": inside,
        "spots": spots,
        "free": spots - inside,
        "fastag_exits": done["ft"] or 0,
        "counter_exits": done["counter"] or 0,
        "overstay": overstay,
        "longest_plate": longest["plate"] if longest else None,
        "longest_since": longest["entry_time"] if longest else None,
    }


def range_stats(lid=None, days=1) -> dict:
    end = now()
    start = end.replace(hour=0, minute=0, second=0) if days <= 1 else (end - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0
    )
    start_s = iso(start)
    loc_sql = " AND location_id=?" if lid else ""
    loc_args = (lid,) if lid else ()
    entered = q(
        f"SELECT COUNT(*) c FROM sessions WHERE entry_time>=?{loc_sql}",
        (start_s,) + loc_args, one=True,
    )["c"]
    done = q(
        f"""SELECT COUNT(*) c,
                   COALESCE(SUM(amount_inr),0) rev,
                   COALESCE(AVG(duration_min),0) avgd,
                   SUM(CASE WHEN duration_min >= 240 THEN 1 ELSE 0 END) long_stays
            FROM sessions
            WHERE exit_time IS NOT NULL AND exit_time>=?{loc_sql}""",
        (start_s,) + loc_args, one=True,
    )
    occ = occupancy_for(lid) if lid else {
        "filled": q("SELECT COUNT(*) c FROM sessions WHERE exit_time IS NULL", one=True)["c"],
        "free": 0,
        "total": q("SELECT COALESCE(SUM(total_spots),0) t FROM locations", one=True)["t"],
    }
    if not lid:
        occ["free"] = occ["total"] - occ["filled"]
    return {
        "days": days,
        "from": start_s,
        "entered": entered,
        "exited": done["c"] or 0,
        "revenue": round(done["rev"] or 0, 2),
        "revenue_label": inr(done["rev"] or 0),
        "avg_min": int(round(done["avgd"] or 0)),
        "avg_label": dur_label(done["avgd"] or 0),
        "inside": occ["filled"],
        "free": occ["free"],
        "spots": occ["total"],
        "overstay": today_stats(lid)["overstay"],
        "long_stays": done["long_stays"] or 0,
    }


def occupancy_for(loc_id: int):
    loc = rowd(q("SELECT * FROM locations WHERE id=?", (loc_id,), one=True))
    filled = q(
        "SELECT COUNT(*) c FROM sessions WHERE location_id=? AND exit_time IS NULL",
        (loc_id,), one=True,
    )["c"]
    return {
        "location": dict(loc),
        "filled": filled,
        "free": loc["total_spots"] - filled,
        "total": loc["total_spots"],
        "pct": round(100 * filled / loc["total_spots"]) if loc["total_spots"] else 0,
    }


def session_view(row, loc=None) -> dict:
    entry = parse_dt(row["entry_time"])
    exit_t = parse_dt(row["exit_time"]) if row["exit_time"] else None
    end = exit_t or now()
    minutes = int((end - entry).total_seconds() // 60)
    hours, mins = divmod(minutes, 60)
    loc = loc or rowd(q("SELECT * FROM locations WHERE id=?", (row["location_id"],), one=True))
    live_fee = 0
    if not exit_t:
        if loc["kind"] == "mall":
            live_fee = mall_fee(entry, end, loc, row["vtype"])
        else:
            live_fee = community_fee(row["resident_of"] == loc["id"], False, loc)
    return {
        "id": row["id"],
        "plate": row["plate"],
        "owner_name": row["owner_name"],
        "model": row["model"],
        "color": row["color"],
        "vtype": row["vtype"],
        "has_fastag": bool(row["has_fastag"]),
        "fastag_balance": row["fastag_balance"],
        "slot_code": row["slot_code"],
        "zone": row["zone"],
        "location_id": row["location_id"],
        "location_name": loc["name"],
        "city": loc["city"],
        "kind": loc["kind"],
        "entry_time": row["entry_time"],
        "exit_time": row["exit_time"],
        "duration_min": row["duration_min"] if row["exit_time"] else minutes,
        "duration_label": f"{hours}h {mins:02d}m" if hours else f"{mins}m",
        "amount_inr": row["amount_inr"] if row["exit_time"] else live_fee,
        "amount_label": inr(row["amount_inr"] if row["exit_time"] else live_fee),
        "payment_method": row["payment_method"],
        "payment_status": row["payment_status"],
        "open": row["exit_time"] is None,
        "phone": row["phone"] if "phone" in row.keys() else "",
        "slot_kind": row["slot_kind"] if "slot_kind" in row.keys() else "standard",
        "resident_of": row["resident_of"] if "resident_of" in row.keys() else None,
        "fastag_balance_label": inr(row["fastag_balance"] or 0),
    }


PARKED_SQL = """
    SELECT s.*, v.plate, v.owner_name, v.phone, v.model, v.color, v.vtype,
           v.has_fastag, v.fastag_balance, v.resident_of,
           sl.code AS slot_code, sl.zone, sl.kind AS slot_kind
    FROM sessions s
    JOIN vehicles v ON v.id = s.vehicle_id
    LEFT JOIN slots sl ON sl.id = s.slot_id
"""


# ── APIs ─────────────────────────────────────────────────

@app.route("/api/me")
@login_required()
def api_me():
    u = dict(g.user)
    u.pop("password_hash", None)
    loc = rowd(q("SELECT * FROM locations WHERE id=?", (u["location_id"],), one=True)) if u["location_id"] else None
    veh = rowd(q("SELECT * FROM vehicles WHERE id=?", (u["vehicle_id"],), one=True)) if u["vehicle_id"] else None
    return jsonify({"ok": True, "user": u, "location": loc, "vehicle": veh})


@app.route("/api/locations")
@login_required()
def api_locations():
    rows = dicts(q("SELECT * FROM locations ORDER BY id"))
    out = []
    for loc in rows:
        occ = occupancy_for(loc["id"])
        loc.update({k: occ[k] for k in ("filled", "free", "pct")})
        out.append(loc)
    return jsonify({"ok": True, "locations": out})


@app.route("/api/overview")
@login_required()
def api_overview():
    user = g.user
    locations = dicts(q("SELECT * FROM locations ORDER BY id"))
    loc_id = scoped_location_id()

    def kpis_for(lid=None):
        where = "WHERE 1=1"
        args = []
        if lid:
            where += " AND location_id=?"
            args = [lid]
        live = q(f"SELECT COUNT(*) c FROM sessions {where} AND exit_time IS NULL", args, one=True)["c"]
        today = now().strftime("%Y-%m-%d")
        day = q(
            f"""SELECT COALESCE(SUM(entries),0) entries, COALESCE(SUM(exits),0) exits,
                       COALESCE(SUM(revenue_inr),0) revenue
                FROM daily_stats {where.replace('location_id','location_id')} AND day=?""",
            args + [today], one=True,
        )
        month = q(
            f"""SELECT COALESCE(SUM(revenue_inr),0) revenue,
                       COALESCE(SUM(entries),0) entries,
                       COALESCE(SUM(fastag_payments),0) fastag,
                       COALESCE(SUM(counter_payments),0) counter
                FROM daily_stats {where}""",
            args, one=True,
        )
        spots = q(
            "SELECT COALESCE(SUM(total_spots),0) t FROM locations" + (" WHERE id=?" if lid else ""),
            (lid,) if lid else (), one=True,
        )["t"]
        overstay = q(
            f"""SELECT COUNT(*) c FROM sessions s
                JOIN locations l ON l.id = s.location_id
                {where.replace('location_id','s.location_id')}
                AND s.exit_time IS NULL AND l.kind='mall'
                AND (julianday('now','localtime') - julianday(s.entry_time)) * 24 > 4""",
            args, one=True,
        )["c"]
        today_live = today_stats(lid)
        return {
            "live": live,
            "spots": spots,
            "free": spots - live,
            "today_entries": today_live["entered"],
            "today_exits": today_live["exited"],
            "today_revenue": today_live["revenue"],
            "today_revenue_label": today_live["revenue_label"],
            "today_avg_label": today_live["avg_label"],
            "today": today_live,
            "month_revenue": month["revenue"],
            "month_revenue_label": inr(month["revenue"]),
            "month_entries": month["entries"],
            "fastag_share": round(100 * month["fastag"] / max(1, month["fastag"] + month["counter"])),
            "overstay": overstay,
        }

    cards = [occupancy_for(l["id"]) for l in locations]
    payload = {
        "ok": True,
        "kpis": kpis_for(loc_id if user["role"] != "admin" else None),
        "occupancy": cards if user["role"] == "admin" else [occupancy_for(user["location_id"])],
    }
    if user["role"] == "admin":
        payload["per_location"] = [kpis_for(l["id"]) | {"id": l["id"], "name": l["name"], "kind": l["kind"]} for l in locations]
    return jsonify(payload)


@app.route("/api/today")
@login_required()
def api_today():
    return jsonify({"ok": True, **today_stats(scoped_location_id())})


@app.route("/api/overstay")
@login_required()
def api_overstay():
    lid = scoped_location_id()
    sql = PARKED_SQL + """
        WHERE s.exit_time IS NULL
          AND (julianday('now','localtime') - julianday(s.entry_time)) * 24 > 4
    """
    args = []
    if lid:
        sql += " AND s.location_id=?"
        args.append(lid)
    sql += " ORDER BY s.entry_time ASC"
    locs = {r["id"]: r for r in q("SELECT * FROM locations")}
    rows = [session_view(r, locs[r["location_id"]]) for r in q(sql, args)]
    return jsonify({"ok": True, "rows": rows, "count": len(rows)})


@app.route("/api/periods")
@login_required()
def api_periods():
    lid = scoped_location_id()
    return jsonify({
        "ok": True,
        "now": today_stats(lid),
        "d1": range_stats(lid, 1),
        "d7": range_stats(lid, 7),
        "d30": range_stats(lid, 30),
    })


@app.route("/api/parked")
@login_required()
def api_parked():
    loc_id = scoped_location_id()
    sql = PARKED_SQL + " WHERE s.exit_time IS NULL"
    args = []
    if loc_id:
        sql += " AND s.location_id=?"
        args.append(loc_id)
    sql += " ORDER BY s.entry_time ASC"
    rows = q(sql, args)
    locs = {r["id"]: r for r in q("SELECT * FROM locations")}
    return jsonify({"ok": True, "rows": [session_view(r, locs[r["location_id"]]) for r in rows]})


@app.route("/api/slots")
@login_required()
def api_slots():
    loc_id = scoped_location_id()
    if not loc_id:
        return jsonify({"ok": False, "error": "Pick a site"}), 400
    slots = dicts(q("SELECT * FROM slots WHERE location_id=? ORDER BY id", (loc_id,)))
    live = q(
        PARKED_SQL + " WHERE s.exit_time IS NULL AND s.location_id=?",
        (loc_id,),
    )
    by_slot = {r["slot_id"]: r for r in live}
    loc = rowd(q("SELECT * FROM locations WHERE id=?", (loc_id,), one=True))
    out = []
    for s in slots:
        item = dict(s)
        if s["id"] in by_slot:
            item["vehicle"] = session_view(by_slot[s["id"]], loc)
        else:
            item["vehicle"] = None
        out.append(item)
    zones = []
    seen = []
    for s in out:
        if s["zone"] not in seen:
            seen.append(s["zone"])
            zones.append(s["zone"])
    return jsonify({"ok": True, "slots": out, "zones": zones})


@app.route("/api/daily")
@login_required()
def api_daily():
    loc_id = scoped_location_id()
    days = int(request.args.get("days") or 30)
    since = (now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    sql = "SELECT * FROM daily_stats WHERE day>=?"
    args = [since]
    if loc_id:
        sql += " AND location_id=?"
        args.append(loc_id)
    sql += " ORDER BY day"
    rows = dicts(q(sql, args))
    if not loc_id:
        # roll up for admin
        rolled = {}
        for r in rows:
            d = rolled.setdefault(r["day"], {
                "day": r["day"], "entries": 0, "exits": 0, "revenue_inr": 0,
                "peak_occupancy": 0, "fastag_payments": 0, "counter_payments": 0,
                "avg_duration_min": 0, "_n": 0,
            })
            rolled[r["day"]]["entries"] += r["entries"]
            rolled[r["day"]]["exits"] += r["exits"]
            rolled[r["day"]]["revenue_inr"] += r["revenue_inr"]
            rolled[r["day"]]["peak_occupancy"] += r["peak_occupancy"]
            rolled[r["day"]]["fastag_payments"] += r["fastag_payments"]
            rolled[r["day"]]["counter_payments"] += r["counter_payments"]
            rolled[r["day"]]["avg_duration_min"] += r["avg_duration_min"]
            rolled[r["day"]]["_n"] += 1
        rows = []
        for d, r in sorted(rolled.items()):
            r["avg_duration_min"] = round(r["avg_duration_min"] / max(1, r["_n"]), 1)
            r.pop("_n")
            rows.append(r)
    return jsonify({"ok": True, "rows": rows})


@app.route("/api/feed")
@login_required()
def api_feed():
    loc_id = scoped_location_id()
    sql = """SELECT e.*, l.name AS location_name, l.city
             FROM events e LEFT JOIN locations l ON l.id = e.location_id"""
    args = []
    if loc_id:
        sql += " WHERE e.location_id=?"
        args.append(loc_id)
    sql += " ORDER BY e.id DESC LIMIT 40"
    return jsonify({"ok": True, "rows": dicts(q(sql, args))})


@app.route("/api/vehicles")
@login_required()
def api_vehicles():
    term = norm_plate(request.args.get("q") or "")
    compact = term.replace(" ", "")
    if compact:
        rows = q(
            """SELECT * FROM vehicles
               WHERE replace(plate,' ','') LIKE ? OR owner_name LIKE ? OR model LIKE ?
               ORDER BY plate LIMIT 25""",
            (f"%{compact}%", f"%{request.args.get('q')}%", f"%{request.args.get('q')}%"),
        )
    else:
        rows = q("SELECT * FROM vehicles ORDER BY id LIMIT 100")
    out = []
    for r in rows:
        d = dict(r)
        live = rowd(q(
            PARKED_SQL + " WHERE s.exit_time IS NULL AND s.vehicle_id=?",
            (r["id"],), one=True,
        ))
        d["parked"] = session_view(live) if live else None
        d["fastag_balance_label"] = inr(d["fastag_balance"])
        out.append(d)
    return jsonify({"ok": True, "rows": out})


@app.route("/api/vehicle")
@login_required()
def api_vehicle():
    plate = norm_plate(request.args.get("plate") or "")
    veh = rowd(q("SELECT * FROM vehicles WHERE replace(plate,' ','')=?", (plate.replace(" ", ""),), one=True))
    if not veh:
        return jsonify({"ok": False, "error": "Plate is not in the 100-car registry."}), 404
    loc_id = scoped_location_id()
    live = rowd(q(
        PARKED_SQL + " WHERE s.exit_time IS NULL AND s.vehicle_id=?",
        (veh["id"],), one=True,
    ))
    hist = q(
        PARKED_SQL + " WHERE s.vehicle_id=? AND s.exit_time IS NOT NULL ORDER BY s.entry_time DESC LIMIT 12",
        (veh["id"],),
    )
    locs = {r["id"]: r for r in q("SELECT * FROM locations")}
    visitor = None
    if loc_id:
        visitor = rowd(q(
            """SELECT * FROM visitor_requests
               WHERE location_id=? AND replace(plate,' ','')=? AND visit_date=?
               ORDER BY id DESC LIMIT 1""",
            (loc_id, plate.replace(" ", ""), now().strftime("%Y-%m-%d")), one=True,
        ))
    return jsonify({
        "ok": True,
        "vehicle": dict(veh),
        "parked": session_view(live, locs[live["location_id"]]) if live else None,
        "history": [session_view(r, locs[r["location_id"]]) for r in hist],
        "visitor": dict(visitor) if visitor else None,
        "resident_here": bool(loc_id and veh["resident_of"] == loc_id),
    })


def assign_slot(loc_id: int, vtype: str, is_visitor: bool):
    loc = rowd(q("SELECT * FROM locations WHERE id=?", (loc_id,), one=True))
    prefer = []
    if loc["kind"] == "community" and is_visitor:
        prefer = ["visitor"]
    elif loc["kind"] == "community":
        prefer = ["resident", "standard"]
    else:
        prefer = ["standard", "ev"]
    for kind in prefer + ["standard", "visitor", "ev", "ada"]:
        slot = rowd(q(
            "SELECT * FROM slots WHERE location_id=? AND status='free' AND kind=? ORDER BY id LIMIT 1",
            (loc_id, kind), one=True,
        ))
        if slot:
            return slot
    return rowd(q(
        "SELECT * FROM slots WHERE location_id=? AND status='free' ORDER BY id LIMIT 1",
        (loc_id,), one=True,
    ))


@app.route("/api/gate/entry", methods=["POST"])
@login_required("admin", "mall_manager", "community_manager")
def api_entry():
    data = request.get_json(force=True)
    plate = norm_plate(data.get("plate") or "")
    loc_id = int(data.get("location_id") or scoped_location_id() or 0)
    if not plate or not loc_id:
        return jsonify({"ok": False, "error": "Plate and site required"}), 400
    loc = rowd(q("SELECT * FROM locations WHERE id=?", (loc_id,), one=True))
    if g.user["role"] != "admin" and g.user["location_id"] != loc_id:
        return jsonify({"ok": False, "error": "Wrong site for this desk"}), 403

    veh = rowd(q("SELECT * FROM vehicles WHERE replace(plate,' ','')=?", (plate.replace(" ", ""),), one=True))
    if not veh:
        return jsonify({"ok": False, "error": f"{plate} is not in today's registry. Add it at HQ or try another plate."}), 404
    if veh["is_blacklisted"]:
        log_event(loc_id, plate, "denied", f"{plate} blocked — watchlist")
        return jsonify({"ok": False, "error": f"{plate} is on the watchlist. Boom stays down."}), 403

    already = rowd(q(
        "SELECT * FROM sessions WHERE vehicle_id=? AND exit_time IS NULL",
        (veh["id"],), one=True,
    ))
    if already:
        return jsonify({"ok": False, "error": f"{plate} is already inside. Close the previous stay first."}), 409

    visitor = rowd(q(
        """SELECT * FROM visitor_requests
           WHERE location_id=? AND replace(plate,' ','')=? AND visit_date=? AND status IN ('approved','pending')
           ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END LIMIT 1""",
        (loc_id, plate.replace(" ", ""), now().strftime("%Y-%m-%d")), one=True,
    ))
    is_resident = veh["resident_of"] == loc_id
    is_visitor = bool(visitor and visitor["status"] == "approved")

    if loc["kind"] == "community" and not is_resident and not is_visitor:
        if visitor and visitor["status"] == "pending":
            return jsonify({
                "ok": False,
                "error": f"Visitor pass for {plate} is still pending host approval.",
                "visitor": dict(visitor),
            }), 409
        if not data.get("override"):
            return jsonify({
                "ok": False,
                "error": f"{plate} is not a resident and has no approved pass for today. Override only if the secretary says so.",
                "needs_override": True,
            }), 409

    slot = assign_slot(loc_id, veh["vtype"], is_visitor and not is_resident)
    if not slot:
        return jsonify({"ok": False, "error": "Lot is full. Hold the boom."}), 409

    sid = execute(
        """INSERT INTO sessions (vehicle_id, location_id, slot_id, entry_time, payment_status, visitor_request_id, note)
           VALUES (?,?,?,?, 'open', ?, ?)""",
        (veh["id"], loc_id, slot["id"], iso(now()),
         visitor["id"] if visitor else None,
         "Walk-in override" if data.get("override") else None),
    )
    execute("UPDATE slots SET status='occupied' WHERE id=?", (slot["id"],))
    if visitor and visitor["status"] == "approved":
        execute("UPDATE visitor_requests SET status='used' WHERE id=?", (visitor["id"],))
    who = "resident" if is_resident else ("approved visitor" if is_visitor else "mall visit")
    log_event(loc_id, plate, "entry", f"{plate} in · {slot['code']} · {who}")
    day = now().strftime("%Y-%m-%d")
    existing = rowd(q("SELECT * FROM daily_stats WHERE location_id=? AND day=?", (loc_id, day), one=True))
    if existing:
        execute("UPDATE daily_stats SET entries=entries+1 WHERE location_id=? AND day=?", (loc_id, day))
    else:
        execute(
            """INSERT INTO daily_stats (location_id, day, entries, exits, revenue_inr, peak_occupancy,
               avg_duration_min, fastag_payments, counter_payments) VALUES (?,?,1,0,0,0,0,0,0)""",
            (loc_id, day),
        )
    live = rowd(q(PARKED_SQL + " WHERE s.id=?", (sid,), one=True))
    return jsonify({
        "ok": True,
        "message": f"Boom up. {plate} → {slot['code']}.",
        "session": session_view(live, loc),
        "who": who,
    })


@app.route("/api/gate/exit", methods=["POST"])
@login_required("admin", "mall_manager", "community_manager")
def api_exit():
    data = request.get_json(force=True)
    plate = norm_plate(data.get("plate") or "")
    loc_id = int(data.get("location_id") or scoped_location_id() or 0)
    method_pref = (data.get("method") or "").lower()
    loc = rowd(q("SELECT * FROM locations WHERE id=?", (loc_id,), one=True))
    veh = rowd(q("SELECT * FROM vehicles WHERE replace(plate,' ','')=?", (plate.replace(" ", ""),), one=True))
    if not veh:
        return jsonify({"ok": False, "error": "Unknown plate"}), 404
    live = rowd(q(
        PARKED_SQL + " WHERE s.exit_time IS NULL AND s.vehicle_id=? AND s.location_id=?",
        (veh["id"], loc_id), one=True,
    ))
    if not live:
        return jsonify({"ok": False, "error": f"{plate} is not inside this site."}), 404

    entry = parse_dt(live["entry_time"])
    exit_t = now()
    minutes = int((exit_t - entry).total_seconds() // 60)
    if loc["kind"] == "mall":
        amount = mall_fee(entry, exit_t, loc, veh["vtype"])
    else:
        amount = community_fee(veh["resident_of"] == loc_id, True, loc)

    method = "waived"
    status = "waived"
    note = live["note"]
    if amount > 0:
        if method_pref in ("upi", "cash"):
            method, status = method_pref, "paid"
        elif veh["has_fastag"] and veh["fastag_balance"] >= amount:
            method, status = "fastag", "paid"
            execute(
                "UPDATE vehicles SET fastag_balance = ROUND(fastag_balance - ?, 2) WHERE id=?",
                (amount, veh["id"]),
            )
        elif veh["has_fastag"] and veh["fastag_balance"] < amount:
            if method_pref in ("upi", "cash"):
                method, status = method_pref, "paid"
                note = (note or "") + " | FASTag short, collected at boom"
            else:
                return jsonify({
                    "ok": False,
                    "error": f"FASTag has {inr(veh['fastag_balance'])}, bill is {inr(amount)}. Collect UPI or cash.",
                    "needs_counter": True,
                    "amount": amount,
                    "amount_label": inr(amount),
                    "balance": veh["fastag_balance"],
                }), 409
        else:
            if method_pref in ("upi", "cash"):
                method, status = method_pref, "paid"
            else:
                return jsonify({
                    "ok": False,
                    "error": f"No FASTag. Collect {inr(amount)} at the boom (UPI / cash).",
                    "needs_counter": True,
                    "amount": amount,
                    "amount_label": inr(amount),
                }), 409

    execute(
        """UPDATE sessions SET exit_time=?, duration_min=?, amount_inr=?,
           payment_method=?, payment_status=?, note=? WHERE id=?""",
        (iso(exit_t), minutes, amount, method, status, note, live["id"]),
    )
    if live["slot_id"]:
        execute("UPDATE slots SET status='free' WHERE id=?", (live["slot_id"],))

    day = exit_t.strftime("%Y-%m-%d")
    ft = 1 if method == "fastag" else 0
    ct = 1 if method in ("upi", "cash") else 0
    existing = rowd(q("SELECT * FROM daily_stats WHERE location_id=? AND day=?", (loc_id, day), one=True))
    if existing:
        execute(
            """UPDATE daily_stats SET exits=exits+1, revenue_inr=revenue_inr+?,
               fastag_payments=fastag_payments+?, counter_payments=counter_payments+? WHERE location_id=? AND day=?""",
            (amount, ft, ct, loc_id, day),
        )
    else:
        execute(
            """INSERT INTO daily_stats (location_id, day, entries, exits, revenue_inr, peak_occupancy,
               avg_duration_min, fastag_payments, counter_payments) VALUES (?,?,0,1,?,0,?,?,?)""",
            (loc_id, day, amount, minutes, ft, ct),
        )

    hours, mins = divmod(minutes, 60)
    msg = f"{plate} out · {hours}h {mins:02d}m · {inr(amount)} · {method}"
    log_event(loc_id, plate, "exit", msg)
    if method == "fastag":
        log_event(loc_id, plate, "fastag", f"FASTag pulled {inr(amount)} from {plate}")

    receipt = {
        "plate": plate,
        "owner_name": veh["owner_name"],
        "location": loc["name"],
        "slot": live["slot_code"],
        "entry_time": live["entry_time"],
        "exit_time": iso(exit_t),
        "duration_label": f"{hours}h {mins:02d}m" if hours else f"{mins}m",
        "amount": amount,
        "amount_label": inr(amount),
        "method": method,
        "status": status,
    }
    return jsonify({"ok": True, "message": msg, "receipt": receipt})


@app.route("/api/simulate", methods=["POST"])
@login_required("admin", "mall_manager", "community_manager")
def api_simulate():
    loc_id = int((request.get_json(force=True) or {}).get("location_id") or scoped_location_id() or 0)
    loc = rowd(q("SELECT * FROM locations WHERE id=?", (loc_id,), one=True))
    inside = {r["vehicle_id"] for r in q("SELECT vehicle_id FROM sessions WHERE exit_time IS NULL")}
    if loc["kind"] == "community":
        today = now().strftime("%Y-%m-%d")
        visitors = q(
            """SELECT v.* FROM vehicles v
               JOIN visitor_requests r ON replace(r.plate,' ','') = replace(v.plate,' ','')
               WHERE r.location_id=? AND r.visit_date=? AND r.status='approved'""",
            (loc_id, today),
        )
        residents = q("SELECT * FROM vehicles WHERE resident_of=?", (loc_id,))
        pool = [v for v in list(residents) + list(visitors) if v["id"] not in inside]
    else:
        pool = [v for v in q("SELECT * FROM vehicles WHERE is_blacklisted=0") if v["id"] not in inside]
    if not pool:
        return jsonify({"ok": False, "error": "Everyone in the registry is already inside."}), 409
    import random
    pick = random.choice(pool)
    return jsonify({"ok": True, "plate": pick["plate"], "owner_name": pick["owner_name"], "model": pick["model"]})


@app.route("/api/visitors")
@login_required()
def api_visitors():
    loc_id = scoped_location_id()
    user = g.user
    sql = """SELECT r.*, l.name AS location_name, l.city
             FROM visitor_requests r
             JOIN locations l ON l.id = r.location_id"""
    if user["role"] == "user":
        rows = q(sql + " WHERE r.host_name=? ORDER BY r.visit_date DESC, r.id DESC", (user["name"],))
    elif loc_id:
        rows = q(sql + " WHERE r.location_id=? ORDER BY r.visit_date DESC, r.id DESC", (loc_id,))
    else:
        rows = q(sql + " ORDER BY r.visit_date DESC, r.id DESC LIMIT 120")
    return jsonify({"ok": True, "rows": dicts(rows)})


@app.route("/api/visitors", methods=["POST"])
@login_required("admin", "community_manager", "user")
def api_visitor_create():
    data = request.get_json(force=True)
    loc_id = int(data.get("location_id") or scoped_location_id() or g.user["location_id"] or 0)
    if not loc_id:
        return jsonify({"ok": False, "error": "Which society?"}), 400
    plate = norm_plate(data.get("plate") or "")
    visit_date = data.get("visit_date") or now().strftime("%Y-%m-%d")
    vid = execute(
        """INSERT INTO visitor_requests
           (location_id, host_name, host_flat, visitor_name, visitor_phone, plate,
            purpose, visit_date, window_from, window_to, status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?, 'pending', ?)""",
        (
            loc_id,
            data.get("host_name") or g.user["name"],
            data.get("host_flat") or "—",
            data.get("visitor_name") or "Guest",
            data.get("visitor_phone") or "",
            plate,
            data.get("purpose") or "Visit",
            visit_date,
            data.get("window_from") or "10:00",
            data.get("window_to") or "20:00",
            iso(now()),
        ),
    )
    log_event(loc_id, plate, "visitor", f"Pass requested for {plate} · {data.get('visitor_name')}")
    row = rowd(q("SELECT * FROM visitor_requests WHERE id=?", (vid,), one=True))
    return jsonify({"ok": True, "row": dict(row)})


@app.route("/api/visitors/<int:vid>/decide", methods=["POST"])
@login_required("admin", "community_manager")
def api_visitor_decide(vid):
    data = request.get_json(force=True)
    action = data.get("action")
    row = rowd(q("SELECT * FROM visitor_requests WHERE id=?", (vid,), one=True))
    if not row:
        return jsonify({"ok": False, "error": "No such pass"}), 404
    if action == "approve":
        prefix = "BH" if row["location_id"] == 4 else "AV"
        code = f"{prefix}-{now().strftime('%Y%m%d')}-{vid:04d}"
        execute("UPDATE visitor_requests SET status='approved', pass_code=? WHERE id=?", (code, vid))
        log_event(row["location_id"], row["plate"], "visitor", f"Pass {code} approved for {row['plate']}")
    elif action == "deny":
        execute("UPDATE visitor_requests SET status='denied' WHERE id=?", (vid,))
        log_event(row["location_id"], row["plate"], "visitor", f"Pass denied for {row['plate']}")
    else:
        return jsonify({"ok": False, "error": "approve or deny"}), 400
    row = rowd(q("SELECT * FROM visitor_requests WHERE id=?", (vid,), one=True))
    return jsonify({"ok": True, "row": dict(row)})


@app.route("/api/user/statement")
@login_required("user", "admin")
def api_statement():
    plate = norm_plate(request.args.get("plate") or "")
    user = g.user
    if user["role"] == "user":
        veh = rowd(q("SELECT * FROM vehicles WHERE id=?", (user["vehicle_id"],), one=True))
        if plate and plate.replace(" ", "") != veh["plate"].replace(" ", ""):
            # demo: allow lookup of any registry plate from the owner desk
            veh = rowd(q("SELECT * FROM vehicles WHERE replace(plate,' ','')=?", (plate.replace(" ", ""),), one=True))
            if not veh:
                return jsonify({"ok": False, "error": "No vehicle for that plate"}), 404
        plate = veh["plate"]
    else:
        veh = rowd(q("SELECT * FROM vehicles WHERE replace(plate,' ','')=?", (plate.replace(" ", ""),), one=True))
        if not veh:
            return jsonify({"ok": False, "error": "No vehicle for that plate"}), 404

    since = (now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    rows = q(
        PARKED_SQL + " WHERE s.vehicle_id=? AND s.entry_time>=? ORDER BY s.entry_time DESC",
        (veh["id"], since),
    )
    locs = {r["id"]: r for r in q("SELECT * FROM locations")}
    views = [session_view(r, locs[r["location_id"]]) for r in rows]
    completed = [v for v in views if not v["open"]]
    spend = sum(v["amount_inr"] or 0 for v in completed)
    by_loc = {}
    for v in completed:
        b = by_loc.setdefault(v["location_name"], {"name": v["location_name"], "city": v["city"], "visits": 0, "minutes": 0, "spend": 0})
        b["visits"] += 1
        b["minutes"] += v["duration_min"] or 0
        b["spend"] += v["amount_inr"] or 0
    live = next((v for v in views if v["open"]), None)
    invites = dicts(q(
        """SELECT r.*, l.name AS location_name
           FROM visitor_requests r JOIN locations l ON l.id = r.location_id
           WHERE r.host_name=? ORDER BY r.visit_date DESC, r.id DESC""",
        (user["name"] if user["role"] == "user" else veh["owner_name"],),
    ))

    def slice_days(days):
        cutoff = now() - timedelta(days=days)
        part = [v for v in completed if parse_dt(v["entry_time"]) >= cutoff]
        if live and parse_dt(live["entry_time"]) >= cutoff:
            part = part + [live]
        mins = sum(v["duration_min"] or 0 for v in part)
        pay = sum(v["amount_inr"] or 0 for v in part if not v.get("open"))
        avg = (mins / len(part)) if part else 0
        return {
            "visits": len(part),
            "spend": pay,
            "spend_label": inr(pay),
            "avg_label": dur_label(avg),
            "minutes": mins,
        }

    return jsonify({
        "ok": True,
        "vehicle": dict(veh),
        "fastag_label": inr(veh["fastag_balance"]),
        "month_spend": spend,
        "month_spend_label": inr(spend),
        "visits": len(completed),
        "open": live,
        "by_location": list(by_loc.values()),
        "rows": views,
        "invites": invites,
        "d1": slice_days(1),
        "d7": slice_days(7),
        "d30": slice_days(30),
    })


@app.route("/api/watchlist", methods=["POST"])
@login_required("admin", "mall_manager", "community_manager")
def api_watchlist():
    data = request.get_json(force=True)
    plate = norm_plate(data.get("plate") or "")
    flag = 1 if data.get("on", True) else 0
    veh = rowd(q("SELECT * FROM vehicles WHERE replace(plate,' ','')=?", (plate.replace(" ", ""),), one=True))
    if not veh:
        return jsonify({"ok": False, "error": "Unknown plate"}), 404
    execute("UPDATE vehicles SET is_blacklisted=? WHERE id=?", (flag, veh["id"]))
    log_event(scoped_location_id(), plate, "watchlist",
              f"{plate} {'added to' if flag else 'cleared from'} watchlist")
    return jsonify({"ok": True})


def boot():
    init_schema()
    n = 0
    try:
        n = q("SELECT COUNT(*) c FROM vehicles", one=True)["c"]
    except sqlite3.OperationalError:
        n = 0
    if n == 0:
        seed_main()


if __name__ == "__main__":
    boot()
    app.run(host="127.0.0.1", port=5050, debug=True)
