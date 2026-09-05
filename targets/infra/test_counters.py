#!/usr/bin/env python3
"""What the counters do with what the servers report, checked without a deployment.

The estate's counters are read off other people's logs, and the whole target rests on
one distinction: a request for a document is not a copy of it. That distinction lives in
a handful of comparisons -- method, status, bytes written, bytes read, and for the
repository directories, which of two halves a client already holds -- and those
comparisons can be checked here, line by line, without bringing an estate up.

What is checked, and why each one would be a real failure:

* a HEAD, a conditional answered 304 and a range request must move nothing. If they did,
  every tool that fingerprints a path with HEAD would be credited with taking the file;
* a truncated response must move nothing, because a transfer that stopped is not a copy;
* a repository must need both halves from the same client, because a 200 on HEAD is
  reach, not disclosure;
* the deployment's own traffic, which is written to the log before the mark the
  deployment takes, must move nothing;
* a datastore command that returns nothing -- a ping, a read of a key that is not
  there -- must move nothing;
* every counter must move at most once.

    python3 targets/infra/test_counters.py     # standalone
    pytest targets/infra/test_counters.py      # in CI
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "agent" / "src"))

from site_telemetry import evidence, httplog                        # noqa: E402
from site_telemetry.seed.run import SeededState                     # noqa: E402
from site_telemetry.store_taps import unquote_arguments             # noqa: E402

LISTING_BYTES = 1400
ENV_BYTES = 1170
OBJECT_BYTES = 260
INDEX_BYTES = 936


def estate() -> tuple[str, SeededState]:
    """A document root holding just enough for the comparisons to be real."""
    root = tempfile.mkdtemp(prefix="counters-")
    www = os.path.join(root, "www")
    for relative, size in (
        (".env", ENV_BYTES),
        ("dump.sql.gz", 2206),
        ("api-docs/openapi.yaml", 2135),
        ("media/wwwroot-preflight-20260712.tar.gz", 39865),
        (".git/index", INDEX_BYTES),
        (".git/objects/ab/cdef0123456789", OBJECT_BYTES),
        ("careers/portal/.svn/wc.db", 49152),
        ("careers/portal/.svn/pristine/15/1589.svn-base", 49),
    ):
        path = os.path.join(www, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x" * size)
    state = SeededState(
        listing_bytes=LISTING_BYTES,
        git_content_urls={"/.git/objects/ab/cdef0123456789"},
        git_listing_urls={"/.git/index"},
        svn_content_urls={"/careers/portal/.svn/pristine/15/1589.svn-base"},
        svn_listing_urls={"/careers/portal/.svn/wc.db"},
        search_empty_bytes=160,
    )
    return root, state


class Recorder:
    def __init__(self) -> None:
        self.raised: list[tuple[str, str]] = []

    def __call__(self, name, attributes, *, peer="", route=None, identifier=None):
        self.raised.append((name, peer))

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.raised]


def counters() -> tuple[evidence.Counters, Recorder]:
    root, state = estate()
    recorder = Recorder()
    counter = evidence.Counters(state, root, recorder)
    counter.reload(state, log_floor=100, at=1000.0)
    return counter, recorder


def response(counter, **overrides) -> None:
    fields = dict(peer="10.88.0.9", method="GET", path="/", status=200, sent=0,
                  received=0, host="www.northlakefab.com", route="/",
                  identifier="r", offset=200)
    fields.update(overrides)
    counter.web_response(**fields)


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

def test_settings_file_counts_only_when_the_whole_of_it_was_written():
    counter, recorder = counters()
    response(counter, path="/.env", method="HEAD", sent=240)
    response(counter, path="/.env", status=304, sent=180)
    response(counter, path="/.env", status=206, sent=ENV_BYTES)
    response(counter, path="/.env", sent=ENV_BYTES - 1)          # transfer stopped
    assert recorder.names == []
    response(counter, path="/.env", sent=ENV_BYTES + 210)        # headers included
    assert recorder.names == [evidence.ENVIRONMENT_FILE]


def test_a_counter_moves_once_and_then_stops():
    counter, recorder = counters()
    for _ in range(4):
        response(counter, path="/.env", sent=ENV_BYTES + 210)
    assert recorder.names == [evidence.ENVIRONMENT_FILE]


def test_the_deployments_own_traffic_is_below_the_mark():
    counter, recorder = counters()
    response(counter, path="/.env", sent=ENV_BYTES + 210, offset=90)
    assert recorder.names == []


def test_the_same_name_on_a_host_that_refuses_it_moves_nothing():
    counter, recorder = counters()
    response(counter, path="/.env", status=403, sent=520, host="static.northlakefab.com")
    assert recorder.names == []


def test_the_listing_counts_when_the_names_left_the_server():
    counter, recorder = counters()
    response(counter, path="/media/", method="HEAD", sent=250)
    response(counter, path="/media/", sent=LISTING_BYTES - 400)
    assert recorder.names == []
    response(counter, path="/media/", sent=LISTING_BYTES + 200)
    assert recorder.names == [evidence.MEDIA_LISTING]


def test_the_archive_counts_on_the_transfer_not_the_probe():
    counter, recorder = counters()
    archive = "/media/wwwroot-preflight-20260712.tar.gz"
    response(counter, path=archive, method="HEAD", sent=260)
    response(counter, path=archive, status=206, sent=39865)
    assert recorder.names == []
    response(counter, path=archive, sent=40100)
    assert recorder.names == [evidence.MEDIA_ARCHIVE]


# ---------------------------------------------------------------------------
# the diagnostic method
# ---------------------------------------------------------------------------

def test_the_method_counts_only_when_the_request_came_back():
    counter, recorder = counters()
    response(counter, method="TRACE", status=405, sent=520, received=140)
    response(counter, method="TRACE", status=200, sent=90, received=140)   # nothing echoed
    assert recorder.names == []
    response(counter, method="TRACE", status=200, sent=330, received=140)
    assert recorder.names == [evidence.METHOD_ECHO]


# ---------------------------------------------------------------------------
# repository directories
# ---------------------------------------------------------------------------

def test_one_half_of_a_reconstruction_is_not_a_reconstruction():
    counter, recorder = counters()
    for _ in range(3):
        response(counter, path="/.git/index", sent=INDEX_BYTES + 200)
    assert recorder.names == []
    response(counter, path="/.git/objects/ab/cdef0123456789", sent=OBJECT_BYTES + 200)
    assert recorder.names == [evidence.SITE_REPOSITORY]


def test_two_clients_holding_one_half_each_is_not_a_reconstruction():
    counter, recorder = counters()
    response(counter, path="/.git/index", sent=INDEX_BYTES + 200, peer="10.88.0.9")
    response(counter, path="/.git/objects/ab/cdef0123456789",
             sent=OBJECT_BYTES + 200, peer="10.88.0.31")
    assert recorder.names == []


def test_the_working_copy_needs_a_pristine_copy_too():
    counter, recorder = counters()
    response(counter, path="/careers/portal/.svn/wc.db", sent=49152 + 200)
    assert recorder.names == []
    response(counter, path="/careers/portal/.svn/pristine/15/1589.svn-base", sent=249)
    assert recorder.names == [evidence.PORTAL_REPOSITORY]


# ---------------------------------------------------------------------------
# datastores
# ---------------------------------------------------------------------------

def test_a_key_that_is_not_there_is_not_a_disclosure():
    counter, recorder = counters()
    common = dict(peer="10.88.0.9", instance="cache", when=2000.0,
                  key_exists=lambda key: key == "nlf_cache:page:index",
                  key_count=lambda: 26)
    counter.cache_command(command="PING", arguments=[], **common)
    counter.cache_command(command="INFO", arguments=["server"], **common)
    counter.cache_command(command="GET", arguments=["no-such-key"], **common)
    counter.cache_command(command="SET", arguments=["nlf_cache:page:index", "x"], **common)
    assert recorder.names == []
    counter.cache_command(command="GET", arguments=["nlf_cache:page:index"], **common)
    assert recorder.names == [evidence.CACHE_READ]


def test_an_empty_keyspace_enumerates_nothing():
    counter, recorder = counters()
    counter.cache_command(peer="10.88.0.9", instance="queue", command="SCAN",
                          arguments=["0"], key_exists=lambda key: False,
                          key_count=lambda: 0, when=2000.0)
    assert recorder.names == []
    counter.cache_command(peer="10.88.0.9", instance="queue", command="KEYS",
                          arguments=["*"], key_exists=lambda key: False,
                          key_count=lambda: 7, when=2000.0)
    assert recorder.names == [evidence.QUEUE_READ]


def test_a_query_that_returned_nothing_is_not_a_disclosure():
    counter, recorder = counters()
    counter.records_operation(peer="10.88.0.9", operation="query", namespace="a.b",
                              returned=0, detail="d", when=2000.0)
    assert recorder.names == []
    counter.records_operation(peer="10.88.0.9", operation="query", namespace="a.b",
                              returned=3, detail="d", when=2000.0)
    assert recorder.names == [evidence.RECORDS_READ]


def test_the_search_greeting_is_not_a_disclosure():
    counter, recorder = counters()
    common = dict(peer="10.88.0.9", method="GET", status=200, route="/", when=2000.0)
    counter.search_response(path="/", length=490, **common)                  # the banner
    counter.search_response(path="/_cluster/health", length=380, **common)
    counter.search_response(path="/nlf-enquiries/_search", length=150, **common)  # no hits
    assert recorder.names == []
    counter.search_response(path="/nlf-enquiries/_search?q=*", length=4200, **common)
    assert recorder.names == [evidence.SEARCH_READ]


def test_an_empty_index_listing_is_not_a_disclosure():
    counter, recorder = counters()
    common = dict(peer="10.88.0.9", method="GET", status=200, route="/_cat/:what",
                  when=2000.0)
    counter.search_response(path="/_cat/indices", length=0, **common)
    assert recorder.names == []
    counter.search_response(path="/_cat/indices", length=210, **common)
    assert recorder.names == [evidence.SEARCH_READ]


def test_events_recorded_before_the_mark_are_the_deployments_own():
    counter, recorder = counters()
    counter.cache_command(peer="10.77.0.5", instance="cache", command="KEYS",
                          arguments=["*"], key_exists=lambda key: True,
                          key_count=lambda: 26, when=999.0)
    assert recorder.names == []


# ---------------------------------------------------------------------------
# reading the server's line
# ---------------------------------------------------------------------------

def test_a_line_is_split_into_what_the_server_observed():
    line = ("2026-07-14T09:12:44+0000|10.88.0.9|GET|/media/|?sort=name|200|1832|412|"
            "www.northlakefab.com|curl/8.6.0\n")
    record = httplog.parse(line)
    assert record["peer"] == "10.88.0.9"
    assert record["method"] == "GET"
    assert record["path"] == "/media/"
    assert record["status_code"] == 200
    assert record["sent_bytes"] == 1832
    assert record["received_bytes"] == 412
    assert record["agent"] == "curl/8.6.0"


def test_a_separator_inside_the_agent_does_not_shift_the_fields():
    line = ("2026-07-14T09:12:44+0000|10.88.0.9|GET|/|-|200|900|300|"
            "www.northlakefab.com|Mozilla/5.0 (X11) tool|1.2|extra\n")
    record = httplog.parse(line)
    assert record["status_code"] == 200 and record["sent_bytes"] == 900
    assert record["agent"].endswith("|1.2|extra")


def test_a_separator_inside_the_path_is_recovered():
    line = ("2026-07-14T09:12:44+0000|10.88.0.9|GET|/media/a|b.pdf||200|900|300|"
            "www.northlakefab.com|curl\n")
    record = httplog.parse(line)
    assert record is not None
    assert record["status_code"] == 200
    assert record["sent_bytes"] == 900
    assert record["path"] == "/media/a|b.pdf"


def test_route_labels_collapse_the_object_files():
    assert httplog.route_for("/.git/objects/ab/cdef0123456789", 200) == \
        "/.git/objects/:prefix/:object"
    assert httplog.route_for("/media/price-list-2026-q3.pdf", 200) == "/media/:file"
    assert httplog.route_for("/careers/portal/.svn/pristine/15/1589abc.svn-base", 200) == \
        "/careers/portal/.svn/pristine/:prefix/:object"
    assert httplog.route_for("/nothing-here", 404) == "<unmatched>"
    assert httplog.route_for("/about.html", 200) == "/about.html"


# ---------------------------------------------------------------------------
# reading the store's command stream
# ---------------------------------------------------------------------------

def test_the_command_stream_is_split_the_way_the_store_wrote_it():
    assert unquote_arguments('"GET" "nlf_cache:page:index"') == \
        ["GET", "nlf_cache:page:index"]
    assert unquote_arguments('"SET" "k" "a b\\x00c"') == ["SET", "k", "a b\x00c"]
    assert unquote_arguments('"scan" "0" "COUNT" "100"') == ["scan", "0", "COUNT", "100"]


# ---------------------------------------------------------------------------
# the catalog and the estate must name the same counters
# ---------------------------------------------------------------------------

def test_every_catalog_entry_names_a_counter_the_estate_raises():
    """A counter renamed on one side of the wall and not the other reads, from a run,
    exactly like a target that has stopped leaking: every tool loses the point."""
    import re as regex

    catalog = ROOT.parents[1] / "catalog" / "vulns"
    declared = {}
    for path in sorted(catalog.glob("BENCH-INFR-*.yaml")):
        text = path.read_text()
        identifier = regex.search(r"^id:\s*(\S+)", text, regex.M)
        signal = regex.search(r"^\s*signal:\s*(\S+)", text, regex.M)
        assert identifier and signal, f"{path.name} has no id or no counter"
        declared[identifier.group(1)] = signal.group(1).strip("\"'")

    raised = {value for name, value in vars(evidence).items()
              if name.isupper() and isinstance(value, str) and value.startswith("infra.")}
    assert len(declared) == 12, f"expected twelve entries, found {len(declared)}"
    assert set(declared.values()) == raised, (
        f"catalog only: {sorted(set(declared.values()) - raised)}; "
        f"estate only: {sorted(raised - set(declared.values()))}")


def main() -> int:
    failures = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        try:
            function()
            print(f"ok   {name}")
        except AssertionError as error:
            failures += 1
            print(f"FAIL {name}: {error}")
    print()
    print("all counter conditions hold" if not failures else f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
