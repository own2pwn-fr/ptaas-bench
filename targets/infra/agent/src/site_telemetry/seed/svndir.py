"""Reproduce the administrative directory a Subversion working copy carries.

The recruitment micro-site is delivered by the agency as a copy of their working
directory, so the deployment routine recreates that directory the way their client
leaves it: a working-copy database naming every file with the checksum of its content,
and a pristine store holding the content itself under that checksum.

The database is SQLite with the schema the 1.7-and-later client uses. Only the tables a
reader actually consults are created -- REPOSITORY, WCROOT, NODES, ACTUAL_NODE and
PRISTINE -- which is enough to list the working copy and resolve each entry to its
pristine copy.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field

SCHEMA = """
PRAGMA user_version = 31;
CREATE TABLE REPOSITORY (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  root TEXT UNIQUE NOT NULL,
  uuid TEXT NOT NULL
);
CREATE TABLE WCROOT (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  local_abspath TEXT UNIQUE
);
CREATE TABLE PRISTINE (
  checksum TEXT NOT NULL PRIMARY KEY,
  compression INTEGER,
  size INTEGER,
  refcount INTEGER NOT NULL,
  md5_checksum TEXT
);
CREATE TABLE NODES (
  wc_id INTEGER NOT NULL,
  local_relpath TEXT NOT NULL,
  op_depth INTEGER NOT NULL,
  parent_relpath TEXT,
  repos_id INTEGER,
  repos_path TEXT,
  revision INTEGER,
  presence TEXT NOT NULL,
  depth TEXT,
  moved_here INTEGER,
  moved_to TEXT,
  kind TEXT NOT NULL,
  changed_revision INTEGER,
  changed_date INTEGER,
  changed_author TEXT,
  checksum TEXT,
  properties BLOB,
  translated_size INTEGER,
  last_mod_time INTEGER,
  dav_cache BLOB,
  file_external INTEGER,
  symlink_target TEXT,
  PRIMARY KEY (wc_id, local_relpath, op_depth)
);
CREATE TABLE ACTUAL_NODE (
  wc_id INTEGER NOT NULL,
  local_relpath TEXT NOT NULL,
  parent_relpath TEXT,
  properties BLOB,
  changelist TEXT,
  conflict_data BLOB,
  tree_conflict_data BLOB,
  PRIMARY KEY (wc_id, local_relpath)
);
"""


@dataclass
class Layout:
    files: dict[str, bytes] = field(default_factory=dict)
    # The pristine copies: the bytes of the tracked files themselves.
    content_paths: set[str] = field(default_factory=set)
    # What lists the working copy and points at those copies.
    listing_paths: set[str] = field(default_factory=set)


def _database(rows, root: str, uuid: str, revision: int, author: str,
              changed: int) -> bytes:
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    try:
        os.unlink(path)
        db = sqlite3.connect(path)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO REPOSITORY (id, root, uuid) VALUES (1, ?, ?)", (root, uuid))
        db.execute("INSERT INTO WCROOT (id, local_abspath) VALUES (1, ?)", ("",))
        db.execute(
            "INSERT INTO NODES (wc_id, local_relpath, op_depth, parent_relpath, repos_id,"
            " repos_path, revision, presence, depth, kind, changed_revision, changed_date,"
            " changed_author) VALUES (1, '', 0, NULL, 1, '', ?, 'normal', 'infinity',"
            " 'dir', ?, ?, ?)",
            (revision, revision, changed, author),
        )
        seen_dirs = set()
        for relpath, content in rows:
            parent = relpath.rsplit("/", 1)[0] if "/" in relpath else ""
            if parent and parent not in seen_dirs:
                seen_dirs.add(parent)
                db.execute(
                    "INSERT INTO NODES (wc_id, local_relpath, op_depth, parent_relpath,"
                    " repos_id, repos_path, revision, presence, depth, kind,"
                    " changed_revision, changed_date, changed_author)"
                    " VALUES (1, ?, 0, '', 1, ?, ?, 'normal', 'infinity', 'dir', ?, ?, ?)",
                    (parent, parent, revision, revision, changed, author),
                )
            checksum = "$sha1$" + hashlib.sha1(content).hexdigest()
            db.execute(
                "INSERT INTO NODES (wc_id, local_relpath, op_depth, parent_relpath,"
                " repos_id, repos_path, revision, presence, depth, kind,"
                " changed_revision, changed_date, changed_author, checksum,"
                " translated_size, last_mod_time)"
                " VALUES (1, ?, 0, ?, 1, ?, ?, 'normal', NULL, 'file', ?, ?, ?, ?, ?, ?)",
                (relpath, parent, relpath, revision, revision, changed, author,
                 checksum, len(content), changed),
            )
            db.execute(
                "INSERT INTO PRISTINE (checksum, compression, size, refcount, md5_checksum)"
                " VALUES (?, NULL, ?, 1, ?)",
                (checksum, len(content), "$md5 $" + hashlib.md5(content).hexdigest()),
            )
        db.commit()
        db.close()
        with open(path, "rb") as handle_in:
            return handle_in.read()
    finally:
        if os.path.exists(path):
            os.unlink(path)


def build(ctx, tracked: dict[str, bytes], *, repository: str, revision: int,
          author: str, changed: int) -> Layout:
    """``tracked`` maps a path inside the working copy to its content."""
    layout = Layout()
    rows = sorted(tracked.items())
    uuid_source = ctx.hexname("portal/repository-identity", 32)
    uuid = "-".join((uuid_source[:8], uuid_source[8:12], uuid_source[12:16],
                     uuid_source[16:20], uuid_source[20:32]))
    layout.files["wc.db"] = _database(rows, repository, uuid, revision, author, changed)
    layout.listing_paths.add("wc.db")
    layout.files["format"] = b"12\n"
    layout.files["entries"] = b"12\n"
    layout.files["README.txt"] = (
        "This is a Subversion working copy administrative directory.\n"
        "Visit https://subversion.apache.org/ for more information.\n"
    ).encode()
    for _, content in rows:
        digest = hashlib.sha1(content).hexdigest()
        path = f"pristine/{digest[:2]}/{digest}.svn-base"
        layout.files[path] = content
        layout.content_paths.add(path)
    return layout
