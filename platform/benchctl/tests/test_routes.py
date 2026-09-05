"""Exhaustive coverage of the route normaliser across the five framework dialects."""

from __future__ import annotations

import itertools

import pytest

from benchctl.routes import (
    GREEDY,
    PLACEHOLDER,
    UNMATCHED,
    normalize_route,
    path_from_url,
    route_matches_path,
    routes_equal,
)

# The five dialects named in the benchmark specification, all denoting the same route.
FIVE_DIALECTS = [
    "/api/orders/:id",
    "/api/orders/{id}",
    "/api/orders/<int:id>",
    "/api/orders/{id:int}",
    "/api/orders/*",
]


@pytest.mark.parametrize("route", FIVE_DIALECTS)
def test_five_dialects_normalise_identically(route):
    assert normalize_route(route) == "/api/orders/{}"


@pytest.mark.parametrize("a,b", itertools.combinations(FIVE_DIALECTS, 2))
def test_five_dialects_compare_equal(a, b):
    assert routes_equal(a, b)
    assert routes_equal(b, a)


@pytest.mark.parametrize(
    "route,expected",
    [
        ("/", "/"),
        ("", "/"),
        ("/api/products", "/api/products"),
        ("/api/products/", "/api/products"),
        ("//api//products//", "/api/products"),
        ("  /api/products  ", "/api/products"),
        ("/api/products?q=1", "/api/products"),
        ("/api/products#frag", "/api/products"),
        ("http://shopfront:8080/api/products", "/api/products"),
        ("/API/Products", "/api/products"),
        # per-dialect parameter forms
        ("/u/:name", "/u/{}"),
        ("/u/:name?", "/u/{}"),
        ("/u/{name}", "/u/{}"),
        ("/u/{name?}", "/u/{}"),
        ("/u/{name:alpha}", "/u/{}"),
        ("/u/{id:[0-9]+}", "/u/{}"),
        ("/u/<name>", "/u/{}"),
        ("/u/<string:name>", "/u/{}"),
        ("/u/<uuid:name>", "/u/{}"),
        ("/u/[name]", "/u/{}"),
        ("/u/*", "/u/{}"),
        # greedy forms stay distinct from single-segment ones
        ("/files/<path:p>", "/files/{**}"),
        ("/files/{*p}", "/files/{**}"),
        ("/files/{**p}", "/files/{**}"),
        ("/files/{p...}", "/files/{**}"),
        ("/files/[...slug]", "/files/{**}"),
        ("/files/*p", "/files/{**}"),
        ("/files/**", "/files/{**}"),
        # multiple parameters, and a parameter embedded in a literal segment
        ("/a/:x/b/{y}/c/<int:z>", "/a/{}/b/{}/c/{}"),
        ("/report-{id}.csv", "/report-{}.csv"),
    ],
)
def test_normalisation_table(route, expected):
    assert normalize_route(route) == expected


def test_placeholder_never_equals_a_literal_in_strict_mode():
    # A static sibling route must not steal reach credit from a parameterised one.
    assert not routes_equal("/api/orders/:id", "/api/orders/export")
    assert not routes_equal("/api/orders/{id}", "/api/orders")


def test_greedy_is_not_the_same_route_as_single():
    assert not routes_equal("/files/{**}", "/files")
    assert routes_equal("/files/<path:p>", "/files/:a/:b")


def test_unmatched_sentinel_is_never_a_route():
    # `<unmatched>` looks exactly like a Flask parameter; if it normalised to {} then
    # every 404 would credit reach on every one-segment route in the catalog.
    assert normalize_route(UNMATCHED) == UNMATCHED
    assert not routes_equal(UNMATCHED, "/anything")
    assert not routes_equal(UNMATCHED, "/:id")
    assert routes_equal(UNMATCHED, UNMATCHED)
    assert not route_matches_path("/{id}", UNMATCHED)


@pytest.mark.parametrize("template", FIVE_DIALECTS)
def test_concrete_paths_match_templates_leniently(template):
    assert route_matches_path(template, "/api/orders/1002")
    assert route_matches_path(template, "http://shopfront:8080/api/orders/1002?x=1")
    assert not route_matches_path(template, "/api/orders/1002/items")
    assert not route_matches_path(template, "/api/invoices/1002")


def test_greedy_template_matches_a_deep_path():
    assert route_matches_path("/files/<path:p>", "/files/a/b/c.txt")
    assert not route_matches_path("/files/<path:p>", "/files")


def test_path_from_url():
    assert path_from_url("https://h/a/b?x=1#f") == "/a/b"
    assert path_from_url("/a/b?x=1") == "/a/b"
    assert path_from_url("https://h") == "/"


def test_constants_do_not_collide():
    assert PLACEHOLDER != GREEDY
