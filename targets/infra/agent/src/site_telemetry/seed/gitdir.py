"""Write the repository metadata a checkout leaves behind, without a client installed.

The web tier is a plain file server and the deployment host has no version control
client on it, so the setup routine writes the administrative directory itself: object
files, refs, the reflog and the index, in the on-disk formats the tools read. That is
enough for a colleague to run `git log` against a copy of the directory, which is the
only reason it is reproduced faithfully rather than approximated.

Formats, all long-stable:

* an object is ``zlib(<type> <length>\\0<payload>)`` stored under
  ``objects/<first two hex of the sha1>/<the rest>``;
* a tree is a concatenation of ``<octal mode> <name>\\0<20 raw sha1 bytes>`` sorted by
  name, with directory names sorted as though they ended in a slash;
* the index is ``DIRC`` + version + count, then one fixed 62-byte record per path
  followed by the path and NUL padding to a multiple of eight, then a trailing checksum.

Nothing here shells out and nothing needs the network.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass, field

REG_MODE = 0o100644
DIR_MODE = 0o40000


@dataclass
class Layout:
    """What the setup routine wrote, and what each file is."""

    files: dict[str, bytes] = field(default_factory=dict)
    # Object paths (relative to the repository directory) holding file content.
    blob_paths: set[str] = field(default_factory=set)
    # Files from which the set of tracked paths can be recovered: the index, the
    # reflog, the packed refs, and the commit and tree objects.
    listing_paths: set[str] = field(default_factory=set)
    head_commit: str = ""


class _Writer:
    def __init__(self) -> None:
        self.layout = Layout()

    def object(self, kind: str, payload: bytes, *, listing: bool = False) -> str:
        body = f"{kind} {len(payload)}".encode() + b"\0" + payload
        oid = hashlib.sha1(body).hexdigest()
        path = f"objects/{oid[:2]}/{oid[2:]}"
        self.layout.files[path] = zlib.compress(body, 1)
        if kind == "blob":
            self.layout.blob_paths.add(path)
        if listing:
            self.layout.listing_paths.add(path)
        return oid

    def blob(self, payload: bytes) -> str:
        return self.object("blob", payload)

    def tree(self, entries: dict[str, tuple[int, str]]) -> str:
        """``entries`` maps a name to (mode, object id)."""

        def sort_key(name: str) -> bytes:
            mode = entries[name][0]
            return (name + "/").encode() if mode == DIR_MODE else name.encode()

        payload = b""
        for name in sorted(entries, key=sort_key):
            mode, oid = entries[name]
            payload += f"{mode:o} {name}".encode() + b"\0" + bytes.fromhex(oid)
        return self.object("tree", payload, listing=True)

    def commit(self, tree: str, parents: list[str], author: str, email: str,
               when: int, offset: str, message: str) -> str:
        lines = [f"tree {tree}"]
        lines += [f"parent {p}" for p in parents]
        stamp = f"{author} <{email}> {when} {offset}"
        lines.append(f"author {stamp}")
        lines.append(f"committer {stamp}")
        payload = ("\n".join(lines) + "\n\n" + message.strip() + "\n").encode()
        return self.object("commit", payload, listing=True)


def _snapshot(writer: _Writer, files: dict[str, bytes]) -> str:
    """Write every tree needed for one snapshot and return the root tree id."""
    # group by first path segment
    here: dict[str, tuple[int, str]] = {}
    subdirs: dict[str, dict[str, bytes]] = {}
    for path, content in files.items():
        head, _, rest = path.partition("/")
        if rest:
            subdirs.setdefault(head, {})[rest] = content
        else:
            here[head] = (REG_MODE, writer.blob(content))
    for name, contents in subdirs.items():
        here[name] = (DIR_MODE, _snapshot(writer, contents))
    return writer.tree(here)


def _index(entries: list[tuple[str, str, int]], when: int) -> bytes:
    """Build the index from (path, object id, size), which is what a checkout leaves."""
    body = b"DIRC" + struct.pack(">II", 2, len(entries))
    for path, oid, size in sorted(entries):
        raw = path.encode()
        record = struct.pack(
            ">10I",
            when, 0,            # created
            when, 0,            # modified
            0x0001, 0,          # device, inode: zero on a checkout made elsewhere
            REG_MODE,
            0, 0,               # owner, group
            size,
        )
        record += bytes.fromhex(oid)
        record += struct.pack(">H", min(len(raw), 0xFFF))
        record += raw
        # One to eight NUL bytes, so the record is a multiple of eight and the name
        # stays NUL-terminated.
        record += b"\0" * (8 - (len(record) % 8))
        body += record
    return body + hashlib.sha1(body).digest()


def build(ctx, history: list[dict], *, remote: str, description: str,
          branch: str = "main") -> Layout:
    """Write a whole administrative directory.

    ``history`` is a list of snapshots, oldest first, each
    ``{"message": str, "author": int (index into the staff list), "when": int (epoch),
      "files": {path: bytes}}``. The last snapshot is what the working directory holds.
    """
    writer = _Writer()
    layout = writer.layout
    parents: list[str] = []
    reflog: list[str] = []
    previous = "0" * 40
    last_files: dict[str, bytes] = {}
    last_when = 0
    for step in history:
        person = ctx.person(step["author"])
        tree = _snapshot(writer, step["files"])
        commit = writer.commit(tree, parents, person.name, person.email,
                               step["when"], "+0100", step["message"])
        action = "commit (initial)" if not parents else "commit"
        reflog.append(
            f"{previous} {commit} {person.name} <{person.email}> {step['when']} +0100"
            f"\t{action}: {step['message'].splitlines()[0]}\n"
        )
        previous = commit
        parents = [commit]
        last_files = step["files"]
        last_when = step["when"]

    head = previous
    layout.head_commit = head

    entries = []
    for path, content in last_files.items():
        entries.append((path, hashlib.sha1(f"blob {len(content)}".encode() + b"\0" + content).hexdigest(),
                        len(content)))
    layout.files["index"] = _index(entries, last_when)
    layout.listing_paths.add("index")

    layout.files["HEAD"] = f"ref: refs/heads/{branch}\n".encode()
    layout.files[f"refs/heads/{branch}"] = f"{head}\n".encode()
    layout.files["packed-refs"] = (
        "# pack-refs with: peeled fully-peeled sorted \n"
        f"{head} refs/remotes/origin/{branch}\n"
    ).encode()
    layout.listing_paths.add("packed-refs")
    layout.files["logs/HEAD"] = "".join(reflog).encode()
    layout.files[f"logs/refs/heads/{branch}"] = "".join(reflog).encode()
    layout.listing_paths.add("logs/HEAD")
    layout.files["ORIG_HEAD"] = f"{head}\n".encode()
    layout.files["COMMIT_EDITMSG"] = (history[-1]["message"].strip() + "\n").encode()
    layout.files["description"] = (description + "\n").encode()
    layout.files["config"] = (
        "[core]\n"
        "\trepositoryformatversion = 0\n"
        "\tfilemode = true\n"
        "\tbare = false\n"
        "\tlogallrefupdates = true\n"
        f'[remote "origin"]\n'
        f"\turl = {remote}\n"
        "\tfetch = +refs/heads/*:refs/remotes/origin/*\n"
        f'[branch "{branch}"]\n'
        "\tremote = origin\n"
        f"\tmerge = refs/heads/{branch}\n"
    ).encode()
    layout.files["info/exclude"] = (
        "# Paths ignored in every working copy of this repository.\n"
        ".DS_Store\n"
        "*.swp\n"
        "node_modules/\n"
    ).encode()
    layout.files["hooks/pre-commit.sample"] = (
        "#!/bin/sh\n"
        "# Refuse a commit that would add a non-ASCII file name, which the deployment\n"
        "# host cannot serve reliably. Rename it and commit again.\n"
        "exec git diff-index --check --cached HEAD --\n"
    ).encode()
    layout.files["hooks/post-update.sample"] = (
        "#!/bin/sh\n"
        "exec git update-server-info\n"
    ).encode()
    layout.files["info/refs"] = f"{head}\trefs/heads/{branch}\n".encode()
    layout.files["objects/info/packs"] = b"\n"
    return layout
