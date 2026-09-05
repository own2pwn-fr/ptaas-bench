"""Catalog loading, class-default resolution and integrity checks."""

from __future__ import annotations

import pytest
import yaml

from benchctl.catalog import coverage_stats, find_repo_root, load_catalog
from conftest import REPO_ROOT, vuln_entry


def codes(catalog, level=None):
    return sorted(i.code for i in catalog.issues if level is None or i.level == level)


def test_real_catalog_is_clean():
    # The shipped catalog is a contract for the rest of the platform; if it stops
    # validating, everything downstream is scoring against nonsense.
    catalog = load_catalog(REPO_ROOT)
    assert catalog.errors == ()
    assert len(catalog) >= 3
    assert catalog.digest()


def test_class_defaults_are_resolved(make_catalog):
    catalog = load_catalog(make_catalog([vuln_entry()]))
    v = catalog.by_id["BENCH-SHOP-0001"]
    assert v.family == "injection"
    assert v.cwe == (89,)
    assert v.owasp == {"2017": "A01", "2021": "A03", "2025": "A05"}
    assert v.label == "SQL injection (UNION)"


def test_explicit_fields_beat_class_defaults(make_catalog):
    entry = vuln_entry(cwe=[564], severity="low", owasp={"2025": "A06"})
    catalog = load_catalog(make_catalog([entry]))
    v = catalog.by_id["BENCH-SHOP-0001"]
    assert v.cwe == (564,)
    assert v.severity == "low"
    # Per-edition merge: only 2025 was overridden, the others keep class defaults.
    assert v.owasp == {"2017": "A01", "2021": "A03", "2025": "A06"}


def test_schema_violation_is_an_error(make_catalog):
    entry = vuln_entry(severity="catastrophic")
    catalog = load_catalog(make_catalog([entry]))
    assert "schema" in codes(catalog, "error")
    assert len(catalog) == 0  # the entry is dropped, never half-loaded


def test_unknown_class_is_an_error(make_catalog):
    catalog = load_catalog(make_catalog([vuln_entry(**{"class": "sqli_telepathic"})]))
    assert "unknown-class" in codes(catalog, "error")


def test_duplicate_id_is_an_error(tmp_path, make_catalog):
    root = make_catalog([vuln_entry()])
    (root / "catalog" / "vulns" / "copy.yaml").write_text(
        yaml.safe_dump(vuln_entry()), encoding="utf-8"
    )
    catalog = load_catalog(root)
    assert "duplicate-id" in codes(catalog, "error")
    assert len(catalog) == 1


def test_id_prefix_must_agree_with_app(make_catalog):
    catalog = load_catalog(make_catalog([vuln_entry(id="BENCH-BANK-0001")]))
    assert "id-app-mismatch" in codes(catalog, "error")


def test_id_prefix_may_abbreviate_the_app(make_catalog):
    # BENCH-SHOP-* living in app "shopfront" is the shipped convention.
    catalog = load_catalog(make_catalog([vuln_entry(app="shopfront")]))
    assert catalog.errors == ()


def test_one_prefix_cannot_serve_two_apps(make_catalog):
    entries = [
        vuln_entry(id="BENCH-SHOP-0001", app="shopfront"),
        vuln_entry(id="BENCH-SHOP-0002", app="shopback"),
    ]
    catalog = load_catalog(make_catalog(entries))
    assert "id-prefix-collision" in codes(catalog, "error")


def test_shared_entrypoint_is_a_warning_not_an_error(make_catalog):
    entries = [
        vuln_entry(id="BENCH-SHOP-0001"),
        vuln_entry(id="BENCH-SHOP-0002", **{"class": "xss_reflected"}, severity="medium"),
    ]
    catalog = load_catalog(make_catalog(entries))
    assert catalog.errors == ()
    assert "shared-entrypoint" in codes(catalog, "warning")


def test_route_dialect_difference_still_counts_as_a_shared_entrypoint(make_catalog):
    entries = [
        vuln_entry(id="BENCH-SHOP-0001", entrypoint={"path": "/api/orders/:id"}),
        vuln_entry(id="BENCH-SHOP-0002", entrypoint={"path": "/api/orders/{id}"},
                   **{"class": "idor_read"}, severity="high"),
    ]
    catalog = load_catalog(make_catalog(entries))
    assert "shared-entrypoint" in codes(catalog, "warning")


def test_oob_oracle_with_neither_signal_nor_token_warns(make_catalog):
    # Nothing to correlate on: the sinkhole could only guess by container and time
    # window, which never counts towards headline trigger recall.
    entry = vuln_entry(
        **{"class": "ssrf_blind"},
        severity="high",
        oracle={"kind": "oob", "signal": None,
                "condition": "The sinkhole observed a callback for this import job."},
    )
    catalog = load_catalog(make_catalog([entry]))
    assert catalog.errors == ()
    assert "oob-unattributable" in codes(catalog, "warning")


def test_non_oob_oracle_without_a_signal_warns(make_catalog):
    # A target must never emit a catalog id, so a sink with no signal is currently
    # unscoreable. A warning rather than an error: entries are written before the
    # targets that emit their signal, and one gap must not block validation.
    catalog = load_catalog(make_catalog([vuln_entry(oracle={"signal": None})]))
    assert "signal-missing" in codes(catalog, "warning")
    assert catalog.errors == ()


def test_duplicate_signal_is_an_error(make_catalog):
    entries = [
        vuln_entry(id="BENCH-SHOP-0001", oracle={"signal": "shop.catalog.query.plan_anomaly"}),
        vuln_entry(id="BENCH-SHOP-0002", oracle={"signal": "shop.catalog.query.plan_anomaly"}),
    ]
    catalog = load_catalog(make_catalog(entries))
    assert "duplicate-signal" in codes(catalog, "error")


def test_signal_index_maps_back_to_the_entry(make_catalog):
    catalog = load_catalog(make_catalog([vuln_entry(
        oracle={"signal": "shop.catalog.query.plan_anomaly"})]))
    assert catalog.by_signal["shop.catalog.query.plan_anomaly"].id == "BENCH-SHOP-0001"


def test_shipped_signals_do_not_leak_the_benchmark():
    # Deception mandate: the signal is the one catalog string that also exists
    # inside a target, so it must never name the benchmark or the entry.
    catalog = load_catalog(REPO_ROOT)
    for v in catalog.vulns:
        signal = (v.oracle.signal or "").lower()
        assert "bench" not in signal
        assert v.id.lower() not in signal


def test_duplicate_canary_token_is_an_error(make_catalog):
    oracle = {"kind": "oob", "condition": "The canary service received a callback token.",
              "canary_token": "shop0031"}
    entries = [
        vuln_entry(id="BENCH-SHOP-0001", oracle=oracle),
        vuln_entry(id="BENCH-SHOP-0002", oracle=oracle),
    ]
    catalog = load_catalog(make_catalog(entries))
    assert "duplicate-canary-token" in codes(catalog, "error")


def test_unknown_prereq_and_cycles_are_errors(make_catalog):
    entries = [
        vuln_entry(id="BENCH-SHOP-0001", requires_prereq=["BENCH-SHOP-0009"]),
        vuln_entry(id="BENCH-SHOP-0002", requires_prereq=["BENCH-SHOP-0003"]),
        vuln_entry(id="BENCH-SHOP-0003", requires_prereq=["BENCH-SHOP-0002"]),
    ]
    catalog = load_catalog(make_catalog(entries))
    assert "unknown-prereq" in codes(catalog, "error")
    assert "prereq-cycle" in codes(catalog, "error")


def test_prereq_depth_and_transitive_closure(make_catalog):
    entries = [
        vuln_entry(id="BENCH-SHOP-0001"),
        vuln_entry(id="BENCH-SHOP-0002", requires_prereq=["BENCH-SHOP-0001"]),
        vuln_entry(id="BENCH-SHOP-0003", requires_prereq=["BENCH-SHOP-0002"]),
    ]
    catalog = load_catalog(make_catalog(entries))
    assert catalog.prereq_depth("BENCH-SHOP-0001") == 0
    assert catalog.prereq_depth("BENCH-SHOP-0003") == 2
    assert catalog.transitive_prereqs("BENCH-SHOP-0003") == ("BENCH-SHOP-0001", "BENCH-SHOP-0002")


def test_taxonomy_self_consistency_is_checked(make_catalog):
    broken = {
        "editions": {"2021": {"A01": "Broken Access Control"}},
        "families": ["injection"],
        "classes": {"weird": {"family": "telepathy", "cwe": [1], "severity": "low",
                              "owasp": {"2021": "A09"}}},
    }
    catalog = load_catalog(make_catalog([], taxonomy=broken))
    assert "taxonomy-unknown-family" in codes(catalog, "error")
    assert "taxonomy-unknown-category" in codes(catalog, "error")


def test_digest_tracks_scored_fields(make_catalog):
    a = load_catalog(make_catalog([vuln_entry()]))
    b = load_catalog(make_catalog([vuln_entry(title="A different title entirely")]))
    c = load_catalog(make_catalog([vuln_entry(entrypoint={"param": "search"})]))
    assert a.digest() == b.digest()  # prose changes must not invalidate archived scores
    assert a.digest() != c.digest()  # a scored field change must


def test_coverage_stats_expose_the_backlog(make_catalog):
    stats = coverage_stats(load_catalog(make_catalog([vuln_entry()])))
    assert stats["total_vulns"] == 1
    assert stats["classes_covered"] == 1
    assert "A02" in stats["owasp"]["2021"]["empty_cells"]
    assert "xss_stored" in stats["empty_classes"]
    assert stats["by_render"] == {"static-html": 1}
    assert stats["params"] == {"with_param": 1, "without_param": 0}


def test_find_repo_root_walks_up():
    assert find_repo_root(REPO_ROOT / "catalog" / "vulns") == REPO_ROOT


def test_missing_root_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        find_repo_root(tmp_path)
