"""Build a self-contained India demo dataset. Re-run to reset."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash

from db import (
    DB_PATH,
    community_fee,
    execute,
    executemany,
    init_schema,
    iso,
    log_event,
    mall_fee,
    q,
)

rng = random.Random(42)

MODELS = [
    ("Maruti Swift", "hatch"),
    ("Maruti Baleno", "hatch"),
    ("Maruti Dzire", "sedan"),
    ("Maruti Brezza", "suv"),
    ("Maruti Ertiga", "mpv"),
    ("Maruti Grand Vitara", "suv"),
    ("Hyundai Creta", "suv"),
    ("Hyundai Venue", "suv"),
    ("Hyundai i20", "hatch"),
    ("Hyundai Verna", "sedan"),
    ("Tata Nexon", "suv"),
    ("Tata Punch", "suv"),
    ("Tata Curvv", "suv"),
    ("Tata Harrier", "suv"),
    ("Tata Altroz", "hatch"),
    ("Mahindra XUV700", "suv"),
    ("Mahindra Thar", "suv"),
    ("Mahindra Scorpio-N", "suv"),
    ("Mahindra XUV 3XO", "suv"),
    ("Kia Seltos", "suv"),
    ("Kia Sonet", "suv"),
    ("Kia Carens", "mpv"),
    ("Toyota Innova Hycross", "mpv"),
    ("Toyota Hyryder", "suv"),
    ("Honda City", "sedan"),
    ("Honda Elevate", "suv"),
    ("MG Hector", "suv"),
    ("Skoda Kushaq", "suv"),
    ("VW Taigun", "suv"),
    ("BMW X1", "luxury"),
    ("Mercedes-Benz GLC", "luxury"),
]

COLORS = [
    "Polar White", "Nexa Blue", "Fire Red", "Midnight Black", "Stealth Grey",
    "Slate Silver", "Grove Green", "Desert Sand", "Pearl White", "Rage Red",
    "Starry Black", "Harmony Beige", "Ocean Teal", "Sunburst Orange",
]

FIRST = [
    "Arjun", "Diya", "Kabir", "Meera", "Rohan", "Ananya", "Vikram", "Isha",
    "Aditya", "Pooja", "Nikhil", "Sana", "Harsh", "Naina", "Karan", "Riya",
    "Ayaan", "Tara", "Dev", "Myra", "Ishaan", "Kiara", "Reyansh", "Aarohi",
    "Samar", "Zara", "Yash", "Anvi", "Rudra", "Leela", "Veer", "Sia",
    "Kabira", "Inaaya", "Om", "Mira", "Aryan", "Jhanvi", "Shaurya", "Avni",
]
LAST = [
    "Mehta", "Kapoor", "Sharma", "Iyer", "Nair", "Khan", "Reddy", "Patel",
    "Singh", "Das", "Pillai", "Joshi", "Gupta", "Banerjee", "Chopra",
    "Malhotra", "Deshpande", "Qureshi", "Rao", "Menon", "Kulkarni", "Bose",
    "Ahuja", "Shetty", "Trivedi",
]
PURPOSES = [
    "Family visit", "Delivery drop", "Interior work", "Tuition pickup",
    "Weekend guest", "Society AGM", "Medical visit", "Courier",
    "Car service pickup", "Festival stay",
]


def pw(plain: str) -> str:
    return generate_password_hash(plain)


def wipe():
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_schema()


def seed_locations():
    rows = [
        (1, "nexus", "Nexus Mall Hyderabad", "mall", "Hyderabad",
         "Nexus Hyderabad, Kukatpally, Hyderabad 500072", 80, 30, 50, 300, 0, 1.2),
        (2, "inorbit", "Inorbit Mall Madhapur", "mall", "Hyderabad",
         "HITEC City, Madhapur, Hyderabad 500081", 64, 15, 40, 250, 0, 1.15),
        (3, "sarath", "Sarath City Capital Mall", "mall", "Hyderabad",
         "Kondapur, Hyderabad 500084", 80, 30, 40, 280, 0, 1.2),
        (4, "bhooja", "My Home Bhooja", "community", "Hyderabad",
         "Financial District, Gachibowli, Hyderabad 500032", 40, 0, 0, 0, 30, 1.0),
        (5, "avatar", "My Home Avatar", "community", "Hyderabad",
         "Narsingi, Hyderabad 500089", 32, 0, 0, 0, 20, 1.0),
    ]
    executemany(
        """INSERT INTO locations
           (id, slug, name, kind, city, address, total_spots, free_minutes,
            hourly_rate_inr, daily_cap_inr, visitor_fee_inr, suv_multiplier)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )


def seed_slots():
    specs = {
        1: [("B1", 28), ("B2", 26), ("B3", 26)],
        2: [("L1", 32), ("L2", 32)],
        3: [("P1", 40), ("P2", 40)],
        4: [("Block A", 16), ("Block B", 16), ("Visitors", 8)],
        5: [("Tower 1", 12), ("Tower 2", 12), ("Guest", 8)],
    }
    batch = []
    sid = 1
    for loc_id, zones in specs.items():
        n = 0
        total = sum(z[1] for z in zones)
        for zone, count in zones:
            for i in range(1, count + 1):
                kind = "standard"
                if zone in ("Visitors", "Guest"):
                    kind = "visitor"
                elif n % 18 == 0:
                    kind = "ev"
                elif n % 23 == 0:
                    kind = "ada"
                elif loc_id in (4, 5) and zone not in ("Visitors", "Guest"):
                    kind = "resident"
                code = f"{zone[:2].upper()}-{i:02d}" if loc_id <= 3 else f"{zone[0]}{zone[-1] if zone[-1].isdigit() else zone.split()[-1][0]}-{i:02d}"
                if loc_id <= 3:
                    code = f"{zone}-{i:02d}"
                else:
                    prefix = {"Block A": "A", "Block B": "B", "Visitors": "V",
                              "Tower 1": "T1", "Tower 2": "T2", "Guest": "G"}[zone]
                    code = f"{prefix}-{i:02d}"
                batch.append((sid, loc_id, code, zone, kind, "free"))
                sid += 1
                n += 1
        assert n == total
    executemany(
        "INSERT INTO slots (id, location_id, code, zone, kind, status) VALUES (?,?,?,?,?,?)",
        batch,
    )


def make_plate(used: set) -> str:
    states = (
        ["TS"] * 36 + ["AP"] * 14 + ["KA"] * 10 + ["MH"] * 10 + ["TN"] * 8
        + ["GJ"] * 6 + ["UP"] * 5 + ["RJ"] * 4 + ["DL"] * 4 + ["KL"] * 3
    )
    rto = {
        "MH": ["01", "02", "04", "12", "14", "43", "47"],
        "KA": ["01", "03", "05", "51", "53"],
        "DL": ["1C", "2C", "3C", "8C", "9C"],
        "TS": ["07", "08", "09", "13"],
        "AP": ["07", "16", "26", "31", "39"],
        "TN": ["01", "07", "09", "10"],
        "GJ": ["01", "05", "06", "18"],
        "UP": ["14", "16", "32", "80"],
        "RJ": ["14", "45", "51"],
        "HR": ["26", "51", "98"],
        "WB": ["02", "06", "26"],
        "KL": ["07", "11", "53"],
    }
    letters = "ABCDEFGHJKLMNPRSTUVWXYZ"
    for _ in range(5000):
        st = rng.choice(states)
        mid = "".join(rng.choice(letters) for _ in range(2))
        num = rng.randint(1000, 9899)
        plate = f"{st} {rng.choice(rto[st])} {mid} {num}"
        if plate not in used:
            used.add(plate)
            return plate
    raise RuntimeError("plate exhaustion")


def seed_vehicles():
    used = set()
    hero_plate = "TS 07 FK 4291"
    used.add(hero_plate)
    vehicles = []

    def phone():
        return f"+91 {rng.choice([98, 99, 97, 96, 90, 87])}{rng.randint(10000000, 99999999)}"

    names_used = set()

    def person():
        for _ in range(200):
            n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if n not in names_used and n != "Nani":
                names_used.add(n)
                return n
        return f"Driver {len(names_used)}"

    # 1 hero
    vehicles.append((
        1, hero_plate, "Nani", "+91 98765 44129",
        "Skoda Kushaq", "Candy White", "suv", 1, 2840.0, 0, 4,
    ))

    # 18 My Home Bhooja residents (ids 2-19)
    for i in range(2, 20):
        m, t = rng.choice(MODELS)
        vehicles.append((
            i, make_plate(used), person(), phone(), m, rng.choice(COLORS), t,
            1 if rng.random() > 0.12 else 0, rng.choice([620, 980, 1400, 2100, 3500, 180]),
            0, 4,
        ))

    # 12 Lakeview residents (20-31)
    for i in range(20, 32):
        m, t = rng.choice(MODELS)
        vehicles.append((
            i, make_plate(used), person(), phone(), m, rng.choice(COLORS), t,
            1 if rng.random() > 0.15 else 0, rng.choice([400, 900, 1600, 2400, 110]),
            0, 5,
        ))

    # remaining 32-100 mall regulars / walk-ins
    for i in range(32, 101):
        m, t = rng.choice(MODELS)
        has = 1 if rng.random() > 0.22 else 0
        bal = rng.choice([0, 80, 150, 340, 700, 1200, 1900, 2600, 4100]) if has else 0
        vehicles.append((
            i, make_plate(used), person(), phone(), m, rng.choice(COLORS), t,
            has, float(bal), 1 if i in (77, 88) else 0, None,
        ))

    executemany(
        """INSERT INTO vehicles
           (id, plate, owner_name, phone, model, color, vtype, has_fastag,
            fastag_balance, is_blacklisted, resident_of)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        vehicles,
    )
    return vehicles


def seed_users():
    executemany(
        """INSERT INTO users (id, username, password_hash, name, role, location_id, vehicle_id, phone)
           VALUES (?,?,?,?,?,?,?,?)""",
        [
            (1, "admin", pw("admin123"), "SmartPark HQ", "admin", None, None, "+91 1800 202 441"),
            (2, "kajal", pw("kajal123"), "Kajal", "mall_manager", 1, None, "+91 98700 11223"),
            (3, "samantha", pw("samantha123"), "Samantha", "community_manager", 4, None, "+91 98810 33445"),
            (4, "nani", pw("nani123"), "Nani", "user", 4, 1, "+91 98765 44129"),
        ],
    )


def loc_row(loc_id: int):
    return q("SELECT * FROM locations WHERE id=?", (loc_id,), one=True)


def slots_for(loc_id: int):
    return q("SELECT * FROM slots WHERE location_id=? ORDER BY id", (loc_id,))


def seed_visitors(today: datetime):
    rows = []
    vid = 1
    bhooja = list(q("SELECT * FROM vehicles WHERE resident_of=4"))
    avatar = list(q("SELECT * FROM vehicles WHERE resident_of=5"))
    outsiders = list(q("SELECT * FROM vehicles WHERE resident_of IS NULL"))

    def add(loc_id, host, flat, visitor, vphone, plate, purpose, day, fr, to, status):
        nonlocal vid
        code = None
        if status in ("approved", "used"):
            prefix = "BH" if loc_id == 4 else "AV"
            code = f"{prefix}-{day.strftime('%Y%m%d')}-{vid:04d}"
        rows.append((
            vid, loc_id, host, flat, visitor, vphone, plate, purpose,
            day.strftime("%Y-%m-%d"), fr, to, status, code, iso(day - timedelta(hours=8)),
        ))
        vid += 1

    def out(i):
        return outsiders[i % len(outsiders)]

    # —— My Home Bhooja today: mix of pending (to approve) and expected ——
    add(4, "Nani", "B-704", "Siddharth Rao", "+91 90000 11122",
        out(3)["plate"], "College friend from CBIT", today, "16:00", "21:00", "approved")
    add(4, "Nani", "B-704", "Neha Reddy", "+91 90000 11123",
        out(4)["plate"], "Family visit — sister", today, "11:00", "18:00", "pending")
    add(4, "Nani", "B-704", "Kiran Teja", "+91 90000 11124",
        out(5)["plate"], "Weekend guest", today, "14:00", "22:00", "pending")
    add(4, bhooja[2]["owner_name"], "A-102", "Swiggy Instamart",
        "+91 90000 20001", out(10)["plate"], "Grocery delivery", today, "10:00", "13:00", "approved")
    add(4, bhooja[3]["owner_name"], "A-118", "Dr. Anjali Rao",
        "+91 90000 20002", out(11)["plate"], "Home consult", today, "09:30", "11:00", "approved")
    add(4, bhooja[4]["owner_name"], "B-210", "Ramesh Painters",
        "+91 90000 20003", out(12)["plate"], "Interior work", today, "08:00", "17:00", "pending")
    add(4, bhooja[5]["owner_name"], "A-305", "Tuition — Maths",
        "+91 90000 20004", out(13)["plate"], "Tuition pickup", today, "17:30", "19:00", "pending")
    add(4, bhooja[6]["owner_name"], "B-412", "Amazon Fresh",
        "+91 90000 20005", out(14)["plate"], "Delivery drop", today, "11:00", "13:30", "approved")
    add(4, bhooja[7]["owner_name"], "A-501", "Priyanka Sharma",
        "+91 90000 20006", out(15)["plate"], "Society AGM guest", today, "18:00", "21:00", "pending")
    add(4, bhooja[8]["owner_name"], "B-603", "Car service — Spinny",
        "+91 90000 20007", out(16)["plate"], "Car service pickup", today, "09:00", "12:00", "approved")
    add(4, bhooja[9]["owner_name"], "A-214", "Deepak Nair",
        "+91 90000 20008", out(17)["plate"], "Family visit", today, "15:00", "20:00", "pending")
    add(4, bhooja[10]["owner_name"], "B-109", "Delhivery",
        "+91 90000 20009", out(18)["plate"], "Courier", today, "10:30", "12:00", "approved")

    # —— My Home Avatar today ——
    add(5, avatar[0]["owner_name"], "T1-1203", "Aunt from Warangal",
        "+91 90000 30001", out(20)["plate"], "Family visit", today, "12:00", "20:00", "approved")
    add(5, avatar[1]["owner_name"], "T2-804", "Blinkit",
        "+91 90000 30002", out(21)["plate"], "Delivery drop", today, "10:00", "12:00", "approved")
    add(5, avatar[2]["owner_name"], "T1-402", "Sanjay Goud",
        "+91 90000 30003", out(22)["plate"], "Weekend guest", today, "13:00", "22:00", "pending")
    add(5, avatar[3]["owner_name"], "T2-1101", "AC technician",
        "+91 90000 30004", out(23)["plate"], "Interior work", today, "09:00", "14:00", "pending")
    add(5, avatar[4]["owner_name"], "T1-905", "Meena Kapoor",
        "+91 90000 30005", out(24)["plate"], "Family visit", today, "16:00", "21:00", "pending")
    add(5, avatar[5]["owner_name"], "T2-210", "Zepto",
        "+91 90000 30006", out(25)["plate"], "Delivery drop", today, "11:30", "13:00", "approved")
    add(5, avatar[6]["owner_name"], "T1-708", "Harsha Vardhan",
        "+91 90000 30007", out(26)["plate"], "College friend", today, "18:00", "22:00", "pending")

    # Past used passes — Bhooja
    used_bhooja = [
        ("Nani", "B-704", "Rohit Varma", "Festival stay"),
        ("Nani", "B-704", "Sneha Iyer", "Family visit"),
        (bhooja[2]["owner_name"], "A-102", "Dunzo", "Courier"),
        (bhooja[3]["owner_name"], "A-118", "Plumber — Raju", "Interior work"),
        (bhooja[4]["owner_name"], "B-210", "Ananya Rao", "Weekend guest"),
        (bhooja[5]["owner_name"], "A-305", "BigBasket", "Delivery drop"),
        (bhooja[6]["owner_name"], "B-412", "Vikram Naidu", "Society AGM"),
        (bhooja[7]["owner_name"], "A-501", "Car wash van", "Car service pickup"),
        (bhooja[8]["owner_name"], "B-603", "Lakshmi Reddy", "Family visit"),
        (bhooja[9]["owner_name"], "A-214", "Flipkart", "Courier"),
        (bhooja[10]["owner_name"], "B-109", "Nitin Joshi", "Tuition pickup"),
        (bhooja[11]["owner_name"], "A-330", "Medical samples", "Medical visit"),
    ]
    for i, (host, flat, visitor, purpose) in enumerate(used_bhooja):
        day = today - timedelta(days=1 + (i % 12))
        vis = out(30 + i)
        add(4, host, flat, visitor, vis["phone"], vis["plate"], purpose, day, "10:00", "19:00", "used")

    # Past used passes — Avatar
    used_av = [
        (avatar[0]["owner_name"], "T1-1203", "Cousin from Vijayawada", "Family visit"),
        (avatar[1]["owner_name"], "T2-804", "Amazon", "Delivery drop"),
        (avatar[2]["owner_name"], "T1-402", "Pooja Reddy", "Weekend guest"),
        (avatar[3]["owner_name"], "T2-1101", "Carpenter", "Interior work"),
        (avatar[4]["owner_name"], "T1-905", "Swiggy", "Delivery drop"),
        (avatar[5]["owner_name"], "T2-210", "Ravi Teja", "College friend"),
        (avatar[6]["owner_name"], "T1-708", "Flipkart Minutes", "Courier"),
        (avatar[7]["owner_name"], "T2-501", "In-laws", "Festival stay"),
    ]
    for i, (host, flat, visitor, purpose) in enumerate(used_av):
        day = today - timedelta(days=1 + (i % 10))
        vis = out(50 + i)
        add(5, host, flat, visitor, vis["phone"], vis["plate"], purpose, day, "10:00", "20:00", "used")

    executemany(
        """INSERT INTO visitor_requests
           (id, location_id, host_name, host_flat, visitor_name, visitor_phone,
            plate, purpose, visit_date, window_from, window_to, status, pass_code, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )


def pay_session(veh, loc, amount, prefer_fastag=True):
    """Mutate in-memory vehicle balance; return method, status."""
    if amount <= 0:
        return "waived", "waived"
    if loc["kind"] == "community" and amount == 0:
        return "waived", "waived"
    if prefer_fastag and veh["has_fastag"] and veh["fastag_balance"] >= amount:
        veh["fastag_balance"] = round(veh["fastag_balance"] - amount, 2)
        return "fastag", "paid"
    if prefer_fastag and veh["has_fastag"] and veh["fastag_balance"] < amount:
        method = rng.choice(["upi", "cash"])
        return method, "paid"
    return rng.choice(["upi", "cash"]), "paid"


def seed_history(today: datetime, vehicles: list):
    veh_by_id = {v[0]: {
        "id": v[0], "plate": v[1], "owner_name": v[2], "vtype": v[6],
        "has_fastag": v[7], "fastag_balance": v[8], "resident_of": v[10],
    } for v in vehicles}

    locs = {r["id"]: r for r in q("SELECT * FROM locations")}
    slot_pool = {lid: [s["id"] for s in slots_for(lid)] for lid in locs}

    sessions = []
    events = []
    sid = 1
    balances = {k: veh_by_id[k]["fastag_balance"] for k in veh_by_id}

    def veh_obj(vid):
        v = dict(veh_by_id[vid])
        v["fastag_balance"] = balances[vid]
        v["has_fastag"] = veh_by_id[vid]["has_fastag"]
        return v

    def push_completed(vid, loc_id, entry, exit):
        nonlocal sid
        loc = locs[loc_id]
        v = veh_obj(vid)
        if loc["kind"] == "mall":
            amount = mall_fee(entry, exit, loc, v["vtype"])
        else:
            amount = community_fee(v["resident_of"] == loc_id, True, loc)
        method, status = pay_session(v, loc, amount)
        balances[vid] = v["fastag_balance"]
        slot_id = rng.choice(slot_pool[loc_id])
        dur = int((exit - entry).total_seconds() // 60)
        sessions.append((
            sid, vid, loc_id, slot_id, iso(entry), iso(exit), dur,
            amount, method, status, None, None,
        ))
        events.append((
            iso(entry), loc_id, v["plate"], "entry",
            f"{v['plate']} in · {locs[loc_id]['name']}",
        ))
        events.append((
            iso(exit), loc_id, v["plate"], "exit",
            f"{v['plate']} out · {dur}m · {amount:.0f} via {method}",
        ))
        sid += 1

    # --- curated month for Arjun: Hyderabad malls only ---
    arjun_days = [
        (29, 1, 11, 18, 94),   # Nexus
        (26, 1, 18, 40, 55),   # Nexus — evening
        (22, 3, 14, 10, 80),   # Sarath City
        (19, 1, 12, 5, 40),    # Nexus grocery
        (15, 2, 16, 20, 110),  # Inorbit
        (11, 1, 13, 0, 165),   # Nexus long shop
        (8, 3, 19, 15, 70),    # Sarath
        (5, 1, 10, 40, 50),    # Nexus
        (3, 2, 15, 5, 95),     # Inorbit
        (1, 1, 17, 30, 88),    # yesterday Nexus
    ]
    for days_ago, loc_id, hh, mm, dur in arjun_days:
        entry = (today - timedelta(days=days_ago)).replace(hour=hh, minute=mm, second=0)
        push_completed(1, loc_id, entry, entry + timedelta(minutes=dur))

    # Arjun already did a morning hop at Inorbit today, then went to Nexus (still inside)
    clock = datetime.now().replace(microsecond=0)
    morn = clock.replace(hour=9, minute=5, second=0)
    if morn < clock:
        push_completed(1, 2, morn, morn + timedelta(minutes=78))

    # bulk traffic last 30 days
    mall_ids = [1, 2, 3]
    comm_ids = [4, 5]
    all_ids = list(veh_by_id)

    for d in range(30, 0, -1):
        day = today - timedelta(days=d)
        weekend = day.weekday() >= 5
        for loc_id in mall_ids:
            n = rng.randint(42, 68) if weekend else rng.randint(32, 52)
            for _ in range(n):
                vid = rng.choice(all_ids)
                if vid == 1:
                    continue
                hour = rng.choice([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
                minute = rng.choice([0, 5, 10, 15, 20, 30, 40, 50])
                entry = day.replace(hour=hour, minute=minute, second=0)
                dur = rng.randint(22, 210)
                exit = entry + timedelta(minutes=dur)
                if exit.date() != entry.date() or exit.hour >= 23:
                    exit = entry.replace(hour=min(22, entry.hour + 2), minute=rng.randint(0, 50))
                push_completed(vid, loc_id, entry, exit)
        for loc_id in comm_ids:
            n = rng.randint(12, 22)
            residents = [i for i, v in veh_by_id.items() if v["resident_of"] == loc_id]
            others = [i for i, v in veh_by_id.items() if v["resident_of"] != loc_id]
            for _ in range(n):
                vid = rng.choice(residents + others[:20])
                if vid == 1:
                    continue
                hour = rng.choice([8, 9, 10, 11, 16, 17, 18, 19])
                entry = day.replace(hour=hour, minute=rng.choice([0, 10, 20, 30, 45]), second=0)
                dur = rng.randint(30, 240) if veh_by_id[vid]["resident_of"] != loc_id else rng.randint(40, 400)
                push_completed(vid, loc_id, entry, entry + timedelta(minutes=min(dur, 600)))

    # —— TODAY's hustle: cars already in AND out before now ——
    now_ts = datetime.now().replace(microsecond=0)
    today_start = now_ts.replace(hour=8, minute=0, second=0)
    today_wave = {1: 78, 2: 56, 3: 62, 4: 24, 5: 16}
    for loc_id, n in today_wave.items():
        for _ in range(n):
            vid = rng.choice(all_ids)
            if vid == 1:
                continue
            latest_entry = now_ts - timedelta(minutes=50)
            if latest_entry <= today_start:
                continue
            span = int((latest_entry - today_start).total_seconds() // 60)
            entry = today_start + timedelta(minutes=rng.randint(0, max(1, span)))
            dur = rng.choice([rng.randint(40, 110), rng.randint(70, 160), rng.randint(250, 340)]) if loc_id <= 3 else rng.randint(25, 140)
            exit_t = entry + timedelta(minutes=dur)
            if exit_t >= now_ts:
                exit_t = now_ts - timedelta(minutes=rng.randint(8, 35))
            if exit_t <= entry:
                continue
            push_completed(vid, loc_id, entry, exit_t)

    executemany(
        """INSERT INTO sessions
           (id, vehicle_id, location_id, slot_id, entry_time, exit_time, duration_min,
            amount_inr, payment_method, payment_status, visitor_request_id, note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        sessions,
    )
    executemany(
        "INSERT INTO events (ts, location_id, plate, kind, message) VALUES (?,?,?,?,?)",
        events,
    )
    return sid, balances


def seed_currently_parked(today: datetime, next_sid: int, balances: dict):
    locs = {r["id"]: r for r in q("SELECT * FROM locations")}
    # Pack the lots. Leave a handful outside so "Car at boom" still works.
    parked_counts = {1: 52, 2: 12, 3: 10, 4: 18, 5: 4}
    used_vehicles = set()
    batch = []
    events = []
    slot_updates = []
    sid = next_sid

    # Arjun currently at Nexus Mall Hyderabad
    arjun_slot = q(
        "SELECT * FROM slots WHERE location_id=1 AND status='free' ORDER BY id LIMIT 1",
        one=True,
    )
    entry = today.replace(hour=11, minute=10, second=0)
    if entry > datetime.now():
        entry = datetime.now() - timedelta(minutes=95)
    batch.append((
        sid, 1, 1, arjun_slot["id"], iso(entry), None, None, 0, None, "open", None,
        "Live stay — Nexus Mall Hyderabad",
    ))
    events.append((iso(entry), 1, "TS 07 FK 4291", "entry",
                   "TS 07 FK 4291 in · Nexus Mall Hyderabad · bay B1-01"))
    slot_updates.append(("occupied", arjun_slot["id"]))
    used_vehicles.add(1)
    sid += 1
    parked_counts[1] -= 1

    for loc_id, count in parked_counts.items():
        free_slots = q(
            "SELECT * FROM slots WHERE location_id=? AND status='free' ORDER BY id",
            (loc_id,),
        )
        candidates = q(
            "SELECT * FROM vehicles WHERE id NOT IN ({})".format(
                ",".join("?" * len(used_vehicles))
            ) if used_vehicles else "SELECT * FROM vehicles",
            tuple(used_vehicles) if used_vehicles else (),
        )
        # Prefer residents occupying community bays
        if loc_id in (4, 5):
            residents = [c for c in candidates if c["resident_of"] == loc_id]
            others = [c for c in candidates if c["resident_of"] != loc_id]
            pick = (residents + others)[: count + 5]
        else:
            pick = list(candidates)
        rng.shuffle(pick)
        pick = pick[:count]
        for i, veh in enumerate(pick):
            if i >= len(free_slots):
                break
            slot = free_slots[i]
            if loc_id <= 3:
                # ~40% sitting past 4h so overstay is real
                minutes_ago = rng.choice([rng.randint(270, 520), rng.randint(270, 480), rng.randint(25, 170)])
            else:
                minutes_ago = rng.randint(50, 780)
            entry = datetime.now().replace(microsecond=0) - timedelta(minutes=minutes_ago)
            batch.append((
                sid, veh["id"], loc_id, slot["id"], iso(entry), None, None, 0,
                None, "open", None, None,
            ))
            events.append((
                iso(entry), loc_id, veh["plate"], "entry",
                f"{veh['plate']} in · {locs[loc_id]['name']} · {slot['code']}",
            ))
            slot_updates.append(("occupied", slot["id"]))
            used_vehicles.add(veh["id"])
            sid += 1

    executemany(
        """INSERT INTO sessions
           (id, vehicle_id, location_id, slot_id, entry_time, exit_time, duration_min,
            amount_inr, payment_method, payment_status, visitor_request_id, note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        batch,
    )
    executemany("UPDATE slots SET status=? WHERE id=?", slot_updates)
    executemany(
        "INSERT INTO events (ts, location_id, plate, kind, message) VALUES (?,?,?,?,?)",
        events,
    )
    # persist mutated FASTag balances from history
    executemany(
        "UPDATE vehicles SET fastag_balance=? WHERE id=?",
        [(balances[vid], vid) for vid in balances],
    )


def seed_daily_stats():
    rows = q(
        """
        SELECT location_id,
               substr(entry_time,1,10) AS day,
               COUNT(*) AS entries,
               SUM(CASE WHEN exit_time IS NOT NULL THEN 1 ELSE 0 END) AS exits,
               SUM(CASE WHEN exit_time IS NOT NULL THEN amount_inr ELSE 0 END) AS revenue,
               AVG(CASE WHEN duration_min IS NOT NULL THEN duration_min END) AS avg_dur,
               SUM(CASE WHEN payment_method='fastag' THEN 1 ELSE 0 END) AS ft,
               SUM(CASE WHEN payment_method IN ('upi','cash') THEN 1 ELSE 0 END) AS counter
        FROM sessions
        GROUP BY location_id, substr(entry_time,1,10)
        """
    )
    caps = {r["id"]: r["total_spots"] for r in q("SELECT id, total_spots FROM locations")}
    payload = []
    for r in rows:
        entries = r["entries"] or 0
        peak = min(caps[r["location_id"]], int(entries * 0.42) + rng.randint(8, 28))
        payload.append((
            r["location_id"], r["day"], entries, r["exits"] or 0,
            round(r["revenue"] or 0, 2), peak, round(r["avg_dur"] or 0, 1),
            r["ft"] or 0, r["counter"] or 0,
        ))
    executemany(
        """INSERT INTO daily_stats
           (location_id, day, entries, exits, revenue_inr, peak_occupancy,
            avg_duration_min, fastag_payments, counter_payments)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        payload,
    )


def main():
    print("Seeding ParkSetu…")
    wipe()
    seed_locations()
    seed_slots()
    vehicles = seed_vehicles()
    seed_users()
    today = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    seed_visitors(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0))
    next_sid, balances = seed_history(today, vehicles)
    seed_currently_parked(today, next_sid, balances)
    seed_daily_stats()
    n_veh = q("SELECT COUNT(*) c FROM vehicles", one=True)["c"]
    n_ses = q("SELECT COUNT(*) c FROM sessions", one=True)["c"]
    n_live = q("SELECT COUNT(*) c FROM sessions WHERE exit_time IS NULL", one=True)["c"]
    print(f"Done. {n_veh} vehicles, {n_ses} sessions, {n_live} currently parked.")
    print("Logins:  admin/admin123  kajal/kajal123  samantha/samantha123  nani/nani123")


if __name__ == "__main__":
    main()
