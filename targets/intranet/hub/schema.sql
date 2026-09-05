-- Staff services schema. Small enough to keep in one file; the deployment tooling
-- rebuilds from it rather than migrating, because every environment except production
-- is generated content.

CREATE TABLE people (
    id            INTEGER PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    job_title     TEXT NOT NULL,
    team          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'staff',
    manager_id    INTEGER REFERENCES people(id),
    site          TEXT NOT NULL,
    salary_band   TEXT NOT NULL,
    postcode      TEXT NOT NULL,
    extension     TEXT NOT NULL,
    started_on    TEXT NOT NULL,
    out_of_office TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL
);

CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES people(id),
    created_at  TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    signed_out  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE leave_requests (
    id          INTEGER PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES people(id),
    kind        TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    days        REAL NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'submitted',
    created_at  TEXT NOT NULL,
    decided_by  INTEGER REFERENCES people(id),
    decided_at  TEXT
);

CREATE TABLE leave_comments (
    id          INTEGER PRIMARY KEY,
    request_id  INTEGER NOT NULL REFERENCES leave_requests(id),
    person_id   INTEGER NOT NULL REFERENCES people(id),
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE delegations (
    id          INTEGER PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES people(id),
    delegate_to TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE claims (
    id          INTEGER PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES people(id),
    title       TEXT NOT NULL,
    stage       TEXT NOT NULL DEFAULT 'draft',
    closed      INTEGER NOT NULL DEFAULT 0,
    receipt_ref TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE claim_lines (
    id          INTEGER PRIMARY KEY,
    claim_id    INTEGER NOT NULL REFERENCES claims(id),
    description TEXT NOT NULL,
    category    TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    spent_on    TEXT NOT NULL,
    added_by    INTEGER NOT NULL REFERENCES people(id)
);

CREATE TABLE claim_stages (
    id          INTEGER PRIMARY KEY,
    claim_id    INTEGER NOT NULL REFERENCES claims(id),
    stage       TEXT NOT NULL,
    at          TEXT NOT NULL,
    by_person   INTEGER REFERENCES people(id)
);

CREATE TABLE assets (
    id          INTEGER PRIMARY KEY,
    tag         TEXT NOT NULL UNIQUE,
    model       TEXT NOT NULL,
    category    TEXT NOT NULL,
    hostname    TEXT NOT NULL,
    site        TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    holder_id   INTEGER REFERENCES people(id),
    status      TEXT NOT NULL DEFAULT 'in service',
    serial      TEXT NOT NULL,
    acquired_on TEXT NOT NULL
);

CREATE TABLE asset_notes (
    id          INTEGER PRIMARY KEY,
    asset_id    INTEGER NOT NULL REFERENCES assets(id),
    person_id   INTEGER NOT NULL REFERENCES people(id),
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE audit_trail (
    id      INTEGER PRIMARY KEY,
    at      TEXT NOT NULL,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    detail  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE reset_links (
    token      TEXT PRIMARY KEY,
    person_id  INTEGER NOT NULL REFERENCES people(id),
    issued_at  TEXT NOT NULL
);

-- De-duplication keys for the application's own anomaly counters.
CREATE TABLE counter_keys (
    key TEXT PRIMARY KEY,
    at  TEXT NOT NULL
);

CREATE TABLE notices (
    id        INTEGER PRIMARY KEY,
    title     TEXT NOT NULL,
    body      TEXT NOT NULL,
    at        TEXT NOT NULL,
    author_id INTEGER NOT NULL REFERENCES people(id)
);

CREATE TABLE handbook (
    slug    TEXT PRIMARY KEY,
    title   TEXT NOT NULL,
    section TEXT NOT NULL,
    body    TEXT NOT NULL
);

CREATE TABLE tickets (
    id         INTEGER PRIMARY KEY,
    person_id  INTEGER NOT NULL REFERENCES people(id),
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    queue      TEXT NOT NULL,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE rooms (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL,
    site  TEXT NOT NULL,
    seats INTEGER NOT NULL,
    kit   TEXT NOT NULL
);

CREATE TABLE room_bookings (
    id        INTEGER PRIMARY KEY,
    room_id   INTEGER NOT NULL REFERENCES rooms(id),
    person_id INTEGER NOT NULL REFERENCES people(id),
    day       TEXT NOT NULL,
    slot      TEXT NOT NULL,
    purpose   TEXT NOT NULL
);

CREATE INDEX idx_leave_person ON leave_requests(person_id);
CREATE INDEX idx_claim_person ON claims(person_id);
CREATE INDEX idx_lines_claim ON claim_lines(claim_id);
CREATE INDEX idx_notes_asset ON asset_notes(asset_id);
CREATE INDEX idx_sessions_person ON sessions(person_id);
