"""Rebuild the staff services database from the deployment seed.

Every environment other than production is generated: the directory, the equipment
register, the requests and the claims are all derived from DEPLOY_SEED, so two
estates running the same release do not share a single name, serial or postcode. The
generator is pure -- same seed, same rows, same identifiers, same dates -- because the
support desk raises tickets against row ids and those ids have to survive a rebuild.

Four accounts are NOT generated: the two depot accounts the induction pack hands out,
the operations account and the depot approver. Their addresses appear in runbooks and
in the payroll provider's allow list, so they are fixed while everything about them --
their names, their teams, their equipment -- still follows the seed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .config import settings

HERE = Path(__file__).resolve().parent

# The application's own calendar: content dates are laid out from this Monday so that
# a rebuilt estate always shows the same week, whatever day the rebuild happened.
EPOCH = date(2026, 1, 5)

PBKDF2_ROUNDS = 120_000

FIRST_NAMES = [
    "Amara", "Bram", "Caitriona", "Dermot", "Elin", "Faisal", "Greta", "Halvard",
    "Ines", "Joris", "Kasia", "Lorcan", "Maeve", "Nils", "Orla", "Petra",
    "Quentin", "Rasmus", "Sofie", "Tomas", "Ursula", "Viggo", "Wren", "Yannick",
    "Zofia", "Astrid", "Bastien", "Clara", "Diarmuid", "Eero", "Freya", "Gunnar",
    "Hanne", "Ivar", "Jonna", "Kerstin", "Lasse", "Mirren", "Noor", "Osian",
]
LAST_NAMES = [
    "Aldridge", "Bergqvist", "Carrick", "Delaney", "Eriksen", "Fenwick", "Gallagher",
    "Haverkamp", "Ingstad", "Janowski", "Kingsley", "Lindqvist", "Mortensen",
    "Nordahl", "Oyelaran", "Pettersen", "Quainton", "Rutherford", "Sandoval",
    "Thorbjornsen", "Underhill", "Vasquez", "Wexford", "Yardley", "Zielinski",
    "Ashworth", "Brennan", "Colquhoun", "Dunmore", "Ellingsen", "Fairbairn",
    "Gudmundur", "Halloran", "Iversen", "Jarratt", "Kilbride", "Lofthouse",
    "Merrick", "Nyholm", "Ormsby",
]

TEAMS = [
    "Depot Operations",
    "Fleet Engineering",
    "Customer Service",
    "Finance",
    "People Team",
    "IT Services",
    "Network Planning",
    "Health and Safety",
]

TITLES = {
    "Depot Operations": ["Depot Coordinator", "Shift Supervisor", "Yard Planner", "Loading Lead"],
    "Fleet Engineering": ["Fleet Technician", "Workshop Engineer", "Compliance Engineer"],
    "Customer Service": ["Account Handler", "Claims Handler", "Service Adviser"],
    "Finance": ["Management Accountant", "Payroll Officer", "Credit Controller"],
    "People Team": ["People Partner", "Recruitment Adviser", "Learning Coordinator"],
    "IT Services": ["Service Desk Analyst", "Infrastructure Engineer", "Applications Analyst"],
    "Network Planning": ["Route Planner", "Capacity Analyst", "Timetable Coordinator"],
    "Health and Safety": ["Safety Adviser", "Site Auditor"],
}

SITES = ["Immingham", "Warrington", "Motherwell", "Avonmouth", "Head Office"]
BANDS = ["B2", "B3", "B4", "C1", "C2", "C3", "D1"]

ASSET_CATEGORIES = [
    ("Laptop", ["Latitude 5450", "ThinkPad L14", "EliteBook 645"]),
    ("Handheld terminal", ["MC3300x", "TC22", "CK65"]),
    ("Access point", ["AP-515", "C9120AX", "U6-Pro"]),
    ("Yard camera", ["FD9367", "P3245-LV"]),
    ("Printer", ["ZT411", "B432dn"]),
    ("Vehicle tablet", ["Tab Active5", "Rugged 8"]),
]

# Equipment hostnames follow the network team's naming standard and are the same in
# every estate, because the standard is published in the handbook and the switch
# configurations are generated from it.
NETWORK_HOSTS = [
    "ap-fleet-01", "ap-fleet-02", "ap-fleet-03", "ap-yard-01", "ap-yard-02",
    "cam-yard-01", "cam-yard-02", "prn-desk-01", "prn-desk-02", "scan-dock-01",
    "scan-dock-02", "scan-dock-03", "tab-cab-01", "tab-cab-02", "tab-cab-03",
    "lap-ops-01", "lap-ops-02", "lap-ops-03", "lap-fin-01", "lap-fin-02",
    "lap-hr-01", "lap-it-01", "lap-it-02", "lap-plan-01",
]

FIXED_ACCOUNTS = {
    # id: (local part, password, role, team)
    1007: ("s.varga", "Wharfside-Beacon!41", "operations", "IT Services"),
    1016: ("r.achterberg", "kestrel-siding-6620", "approver", "Depot Operations"),
    1041: ("j.hollis", "harbour-lantern-5183", "staff", "Depot Operations"),
    1042: ("p.rasmussen", "cobble-thicket-2947", "staff", "Depot Operations"),
}

STAFF_IDS = list(range(1001, 1049))


def password_hash(password: str, salt: str) -> str:
    """Salted PBKDF2, with the salt derived from the account rather than drawn.

    A drawn salt would make two rebuilds of the same estate produce different rows,
    and the deployment check compares the rebuilt file against a known digest.
    """
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return f"pbkdf2:sha256:{PBKDF2_ROUNDS}${salt}${digest.hex()}"


def _rng(seed: str, stream: str) -> random.Random:
    material = hashlib.sha256(f"{seed}/{stream}".encode()).digest()
    return random.Random(int.from_bytes(material, "big"))


def _day(offset: int) -> str:
    return (EPOCH + timedelta(days=offset)).isoformat()


def _stamp(offset_days: int, hour: int, minute: int) -> str:
    d = EPOCH + timedelta(days=offset_days)
    return f"{d.isoformat()}T{hour:02d}:{minute:02d}:00Z"


def build_people(seed: str, domain: str) -> list[dict]:
    rng = _rng(seed, "people")
    firsts = FIRST_NAMES[:]
    lasts = LAST_NAMES[:]
    rng.shuffle(firsts)
    rng.shuffle(lasts)

    people: list[dict] = []
    used_emails: set[str] = set()
    for index, pid in enumerate(STAFF_IDS):
        first = firsts[index % len(firsts)]
        last = lasts[(index * 7 + 3) % len(lasts)]
        team = TEAMS[index % len(TEAMS)]
        fixed = FIXED_ACCOUNTS.get(pid)
        if fixed:
            local, password, role, team = fixed
            display = f"{first} {last}"
        else:
            local = f"{first[0].lower()}.{last.lower()}"
            password = f"{rng.choice(['harbour', 'siding', 'quayside', 'lantern', 'gantry'])}" \
                       f"-{rng.choice(['thistle', 'kestrel', 'marram', 'cobble', 'juniper'])}" \
                       f"-{rng.randrange(1000, 9999)}"
            role = "staff"
            display = f"{first} {last}"
        suffix = 2
        base_local = local
        while local in used_emails:
            local = f"{base_local}{suffix}"
            suffix += 1
        used_emails.add(local)
        people.append({
            "id": pid,
            "email": f"{local}@{domain}",
            "display_name": display,
            "team": team,
            "role": role,
            "job_title": rng.choice(TITLES[team]),
            "site": SITES[index % len(SITES)],
            "salary_band": BANDS[(index * 3) % len(BANDS)],
            "postcode": f"{rng.choice(['DN', 'WA', 'ML', 'BS', 'LS'])}{rng.randrange(10, 99)} "
                        f"{rng.randrange(1, 9)}{rng.choice('ABDEFGHJLNPQRSTUWXYZ')}"
                        f"{rng.choice('ABDEFGHJLNPQRSTUWXYZ')}",
            "extension": str(2000 + index),
            "started_on": _day(-rng.randrange(200, 3000)),
            "out_of_office": "",
            "password_hash": password_hash(password, f"{seed}:{pid}"),
            "password": password,
        })

    # Team approvers: the first person of each team who is not a generated junior, and
    # the depot approver is fixed because the payroll export names them.
    approvers: dict[str, int] = {}
    for person in people:
        if person["role"] in ("approver", "operations"):
            approvers.setdefault(person["team"], person["id"])
    for person in people:
        approvers.setdefault(person["team"], person["id"])
    for person in people:
        manager = approvers[person["team"]]
        person["manager_id"] = None if manager == person["id"] else manager
    return people


def build_assets(seed: str) -> list[dict]:
    rng = _rng(seed, "assets")
    assets = []
    for index, hostname in enumerate(NETWORK_HOSTS):
        category, models = ASSET_CATEGORIES[index % len(ASSET_CATEGORIES)]
        assets.append({
            "id": index + 1,
            "tag": f"LF-{4200 + index * 3}",
            "model": rng.choice(models),
            "category": category,
            "hostname": hostname,
            "site": SITES[index % len(SITES)],
            "label": rng.choice([
                "Dock bay spare", "Gatehouse unit", "Loaned to weekend shift",
                "Awaiting firmware", "Yard office", "Cab mount", "Returned from repair",
            ]),
            "holder_id": STAFF_IDS[(index * 5 + 2) % len(STAFF_IDS)],
            "status": rng.choice(["in service", "in service", "in service", "in repair", "in store"]),
            "serial": f"{rng.choice('CDFHJK')}{rng.randrange(10**7, 10**8)}",
            "acquired_on": _day(-rng.randrange(100, 1500)),
        })
    return assets


def build(conn: sqlite3.Connection, seed: str, domain: str) -> None:
    conn.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
    people = build_people(seed, domain)
    conn.executemany(
        "INSERT INTO people (id, email, display_name, job_title, team, role, manager_id, site,"
        " salary_band, postcode, extension, started_on, out_of_office, password_hash)"
        " VALUES (:id, :email, :display_name, :job_title, :team, :role, :manager_id, :site,"
        " :salary_band, :postcode, :extension, :started_on, :out_of_office, :password_hash)",
        [{k: v for k, v in p.items() if k != "password"} for p in people],
    )

    rng = _rng(seed, "leave")
    kinds = ["Annual leave", "Time off in lieu", "Unpaid leave", "Compassionate leave"]
    reasons = [
        "Family visit", "Half term", "Moving house", "Wedding", "Medical appointment",
        "Cover arranged with the depot", "Booked flights", "School holiday",
    ]
    leave_rows = []
    comment_rows = []
    # 4101 and 4102 belong to the two depot accounts, in that order, because the
    # induction pack walks a new starter through those two requests.
    owners = [1041, 1042, 1041, 1042, 1041, 1042] + [
        pid for pid in STAFF_IDS if pid not in (1041, 1042)]
    for index, owner in enumerate(owners[:34]):
        rid = 4101 + index
        start = 14 + index * 2
        leave_rows.append({
            "id": rid,
            "person_id": owner,
            "kind": kinds[index % len(kinds)],
            "start_date": _day(start),
            "end_date": _day(start + rng.randrange(1, 9)),
            "days": float(rng.randrange(2, 18)) / 2.0,
            "reason": reasons[index % len(reasons)],
            "status": ["submitted", "submitted", "approved", "refused", "withdrawn"][index % 5],
            "created_at": _stamp(index % 10, 8 + index % 8, (index * 7) % 60),
            "decided_by": None,
            "decided_at": None,
        })
        if index % 3 == 0:
            comment_rows.append({
                "request_id": rid,
                "person_id": 1016,
                "body": rng.choice([
                    "Cover arranged with the depot.",
                    "Please confirm the handover notes before you go.",
                    "Approved subject to the shift rota being updated.",
                    "Can you move this by a day? We are short on the Tuesday.",
                ]),
                "created_at": _stamp(index % 10, 11, (index * 13) % 60),
            })
    conn.executemany(
        "INSERT INTO leave_requests (id, person_id, kind, start_date, end_date, days, reason,"
        " status, created_at, decided_by, decided_at) VALUES (:id, :person_id, :kind, :start_date,"
        " :end_date, :days, :reason, :status, :created_at, :decided_by, :decided_at)", leave_rows)
    conn.executemany(
        "INSERT INTO leave_comments (request_id, person_id, body, created_at)"
        " VALUES (:request_id, :person_id, :body, :created_at)", comment_rows)

    rng = _rng(seed, "claims")
    titles = [
        "Depot visit mileage", "Overnight stay - Motherwell", "Client lunch - Avonmouth",
        "Replacement safety boots", "Rail fare to head office", "Parking - Immingham dock",
        "Training course materials", "Taxi after late shift", "Van cleaning",
        "Mobile data top-up",
    ]
    categories = ["Mileage", "Accommodation", "Subsistence", "Equipment", "Travel", "Other"]
    claim_rows, line_rows, stage_rows = [], [], []
    # 8815 is the depot account's open claim; 8821 and 8822 belong to a colleague and
    # are the ones finance uses in the wizard walkthrough.
    claim_owner = {8801: 1041, 8803: 1042, 8807: 1041, 8809: 1042,
                   8815: 1041, 8821: 1043, 8822: 1043}
    stages = ["draft", "itemised", "submitted", "reviewed", "reimbursed"]
    for index in range(30):
        cid = 8801 + index
        owner = claim_owner.get(cid, STAFF_IDS[(index * 11 + 5) % len(STAFF_IDS)])
        if cid == 8815:
            stage = "draft"
        elif cid == 8821:
            stage = "submitted"
        elif cid == 8822:
            stage = "itemised"
        else:
            stage = stages[index % len(stages)]
        closed = 1 if stage == "reimbursed" else 0
        claim_rows.append({
            "id": cid, "person_id": owner, "title": titles[index % len(titles)],
            "stage": stage, "closed": closed,
            "receipt_ref": f"RCT-{7000 + index * 13}",
            "created_at": _stamp(index % 12, 9 + index % 6, (index * 11) % 60),
        })
        for step in stages[: stages.index(stage) + 1]:
            stage_rows.append({
                "claim_id": cid, "stage": step,
                "at": _stamp(index % 12, 9 + stages.index(step), (index * 5) % 60),
                "by_person": owner,
            })
        for line_no in range(rng.randrange(1, 4)):
            line_rows.append({
                "claim_id": cid,
                "description": rng.choice([
                    "Diesel - depot run", "Hotel single room", "Sandwich and coffee",
                    "Toll charge", "Replacement gloves", "Return rail ticket",
                ]),
                "category": categories[(index + line_no) % len(categories)],
                "amount_cents": rng.randrange(450, 24000),
                "spent_on": _day(index % 12),
                "added_by": owner,
            })
    conn.executemany(
        "INSERT INTO claims (id, person_id, title, stage, closed, receipt_ref, created_at)"
        " VALUES (:id, :person_id, :title, :stage, :closed, :receipt_ref, :created_at)", claim_rows)
    conn.executemany(
        "INSERT INTO claim_lines (claim_id, description, category, amount_cents, spent_on, added_by)"
        " VALUES (:claim_id, :description, :category, :amount_cents, :spent_on, :added_by)", line_rows)
    conn.executemany(
        "INSERT INTO claim_stages (claim_id, stage, at, by_person)"
        " VALUES (:claim_id, :stage, :at, :by_person)", stage_rows)

    assets = build_assets(seed)
    conn.executemany(
        "INSERT INTO assets (id, tag, model, category, hostname, site, label, holder_id, status,"
        " serial, acquired_on) VALUES (:id, :tag, :model, :category, :hostname, :site, :label,"
        " :holder_id, :status, :serial, :acquired_on)", assets)

    rng = _rng(seed, "notes")
    note_rows = []
    for asset in assets:
        for _ in range(rng.randrange(0, 3)):
            note_rows.append({
                "asset_id": asset["id"],
                "person_id": STAFF_IDS[rng.randrange(0, len(STAFF_IDS))],
                "body": rng.choice([
                    "Battery replaced under warranty.",
                    "Screen cracked, still usable.",
                    "Moved to the north gate cabinet.",
                    "Firmware pinned until the next maintenance window.",
                    "Handed back by the weekend shift.",
                ]),
                "created_at": _stamp(rng.randrange(0, 12), 7 + rng.randrange(0, 9), rng.randrange(0, 60)),
            })
    conn.executemany(
        "INSERT INTO asset_notes (asset_id, person_id, body, created_at)"
        " VALUES (:asset_id, :person_id, :body, :created_at)", note_rows)

    rng = _rng(seed, "notices")
    notice_rows = []
    headlines = [
        ("Winter tyre changeover", "The workshop will fit winter tyres to the tractor units from Monday."),
        ("Payroll cut-off moved", "Expense claims must be submitted by the 18th this month."),
        ("New starter induction", "Induction runs every second Tuesday in the Immingham training room."),
        ("Gate barrier works", "The north gate barrier is out of service for two days."),
        ("Fire drill", "A full evacuation drill takes place on Thursday morning."),
        ("Handheld refresh", "IT Services will swap the older handhelds during the night shift."),
        ("Cycle to work scheme", "Applications for the cycle scheme close at the end of the month."),
    ]
    for index, (title, body) in enumerate(headlines):
        notice_rows.append({
            "title": title, "body": body,
            "at": _stamp(index, 8, 30), "author_id": 1007 if index % 2 else 1016,
        })
    conn.executemany(
        "INSERT INTO notices (title, body, at, author_id) VALUES (:title, :body, :at, :author_id)",
        notice_rows)

    handbook_rows = [
        ("leave-policy", "Booking leave", "Time off",
         "Requests go to your line manager and should be raised at least two weeks ahead. "
         "Depot shifts need cover agreed before the request is approved."),
        ("expenses-policy", "Claiming expenses", "Money",
         "Claims are itemised, submitted, reviewed by your manager and then reimbursed with "
         "the following payroll run. Receipts are required above ten pounds."),
        ("equipment", "Equipment and returns", "Equipment",
         "Every handheld, tablet and laptop carries an asset tag. Report a fault through the "
         "service desk rather than to the depot supervisor."),
        ("network-names", "Naming standard", "Equipment",
         "Network equipment is named by role and site, for example ap-yard-02 for the second "
         "yard access point."),
        ("security", "Keeping accounts safe", "Working here",
         "Sign in only through the staff services address. The service desk will never ask "
         "for your password."),
        ("home-working", "Working away from a depot", "Working here",
         "Agree a pattern with your manager and record it in the directory so the rota is right."),
    ]
    conn.executemany(
        "INSERT INTO handbook (slug, title, section, body) VALUES (?, ?, ?, ?)", handbook_rows)

    rng = _rng(seed, "tickets")
    ticket_rows = []
    subjects = [
        "Handheld will not pair with the dock", "Cannot sign in to the rota screen",
        "Printer jamming on labels", "Replacement charger needed",
        "Van tablet stuck on the splash screen", "Access to the finance folder",
    ]
    for index in range(18):
        ticket_rows.append({
            "person_id": STAFF_IDS[(index * 3) % len(STAFF_IDS)],
            "subject": subjects[index % len(subjects)],
            "body": rng.choice([
                "It worked yesterday and stopped this morning.",
                "Happens on the early shift only.",
                "Third time this month, please replace.",
                "Reported by the weekend supervisor.",
            ]),
            "queue": rng.choice(["Service desk", "Facilities", "Fleet"]),
            "status": ["open", "open", "waiting", "closed"][index % 4],
            "created_at": _stamp(index % 12, 7 + index % 10, (index * 17) % 60),
        })
    conn.executemany(
        "INSERT INTO tickets (person_id, subject, body, queue, status, created_at)"
        " VALUES (:person_id, :subject, :body, :queue, :status, :created_at)", ticket_rows)

    room_rows = [
        (1, "Kestrel", "Immingham", 8, "Screen, speakerphone"),
        (2, "Marram", "Immingham", 4, "Screen"),
        (3, "Gantry", "Warrington", 12, "Screen, camera"),
        (4, "Siding", "Motherwell", 6, "Speakerphone"),
        (5, "Quayside", "Avonmouth", 10, "Screen, camera"),
        (6, "Beacon", "Head Office", 20, "Screen, camera, hearing loop"),
    ]
    conn.executemany("INSERT INTO rooms (id, name, site, seats, kit) VALUES (?, ?, ?, ?, ?)", room_rows)
    rng = _rng(seed, "bookings")
    booking_rows = []
    for index in range(24):
        booking_rows.append({
            "room_id": room_rows[index % len(room_rows)][0],
            "person_id": STAFF_IDS[(index * 7) % len(STAFF_IDS)],
            "day": _day(index % 5),
            "slot": ["09:00", "10:30", "13:00", "14:30", "16:00"][index % 5],
            "purpose": rng.choice(["Shift handover", "Rota planning", "Supplier call",
                                   "Team catch-up", "Induction"]),
        })
    conn.executemany(
        "INSERT INTO room_bookings (room_id, person_id, day, slot, purpose)"
        " VALUES (:room_id, :person_id, :day, :slot, :purpose)", booking_rows)

    audit_rows = []
    for index, person in enumerate(people[:12]):
        audit_rows.append({
            "at": _stamp(index % 8, 8 + index % 9, (index * 19) % 60),
            "actor": people[(index + 3) % len(people)]["email"],
            "action": ["auth.signin", "leave.decision", "claim.review", "account.password"][index % 4],
            "subject": person["email"],
            "detail": "from the staff network",
        })
    conn.executemany(
        "INSERT INTO audit_trail (at, actor, action, subject, detail)"
        " VALUES (:at, :actor, :action, :subject, :detail)", audit_rows)

    conn.commit()


def rebuild(path: str, log_dir: str, seed: str, domain: str) -> None:
    """Write a fresh database beside the live one and move it into place."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_suffix(".rebuild")
    for leftover in (scratch, Path(str(scratch) + "-journal"), Path(str(scratch) + "-wal")):
        if leftover.exists():
            leftover.unlink()
    conn = sqlite3.connect(scratch)
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
        build(conn, seed, domain)
    finally:
        conn.close()
    os.replace(scratch, target)
    for leftover in (Path(str(target) + "-journal"), Path(str(target) + "-wal")):
        if leftover.exists():
            leftover.unlink()

    logs = Path(log_dir)
    logs.mkdir(parents=True, exist_ok=True)
    for name in ("signin.log", "approvals.log", "probe.log"):
        (logs / name).write_text("", encoding="utf-8")


def digest(path: str) -> tuple[str, int]:
    """A stable fingerprint of the seeded state, and how many rows it covers.

    Every table is dumped in primary-key order and hashed. The value changes if and
    only if a row changed, which is what the deployment check compares between
    releases.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " ORDER BY name")]
        mac = hashlib.sha256()
        total = 0
        for name in names:
            mac.update(f"\n#{name}\n".encode())
            for record in conn.execute(f"SELECT * FROM {name}"):
                total += 1
                mac.update(json.dumps({k: record[k] for k in record.keys()},
                                      sort_keys=True, default=str).encode())
                mac.update(b"\n")
        return mac.hexdigest(), total
    finally:
        conn.close()


def account_password(seed: str, person_id: int) -> str | None:
    """The password of a fixed account, for the deployment check's sign-in probe."""
    fixed = FIXED_ACCOUNTS.get(person_id)
    return fixed[1] if fixed else None


def verify_password(stored: str, password: str) -> bool:
    try:
        scheme, salt, expected = stored.split("$", 2)
        rounds = int(scheme.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), rounds).hex()
    return hmac.compare_digest(got, expected)
