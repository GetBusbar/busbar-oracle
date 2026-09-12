# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""RFC 6901 reference-token escaping, on every path-builder in `diff-cells.py` (OR-BASE-1, 0.3.20).

0.3.10 fixed `additive_superset`/`resolve_json_pointer`: a `paths` key that is itself a URL (an
OpenAPI document's own key shape) is now BUILT escaped and RESOLVED unescaped, so
`description_corrections` can address a leaf under it. `json_paths_diff` — the walker that names
the FIRST divergent leaf of a JSON body or an `effects.*` value for the ledger's own diff text — was
not part of that fix: it still joined a raw, unescaped key onto `path`, so the very row a maintainer
would copy into `accepted-differences.json` named a pointer `resolve_json_pointer` could not
resolve. `_pointer_set` (the `derived_from_body` helper that reads `json_paths_diff`'s own `paths`)
split on the same unescaped convention, so the two stayed internally consistent with each other and
both wrong the same way.

These tests pin the escaped-and-round-tripping form for `json_paths_diff` and `_pointer_set`,
alongside `resolve_json_pointer`/`ptr_escape`/`ptr_unescape` themselves — the one standard, checked
on every side that reads or writes a pointer."""
from __future__ import annotations


# ── ptr_escape / ptr_unescape: the reference-token grammar itself ─────────────────────────────────
def test_escape_then_unescape_round_trips_a_slash_bearing_key(diff_cells):
    key = "/api/v1/admin/overlay/{section}"
    esc = diff_cells.ptr_escape(key)
    assert esc == "~1api~1v1~1admin~1overlay~1{section}"
    assert "/" not in esc, "an escaped token must not be able to forge a path separator"
    assert diff_cells.ptr_unescape(esc) == key


def test_a_literal_tilde_escapes_to_tilde_zero(diff_cells):
    assert diff_cells.ptr_escape("~") == "~0"
    assert diff_cells.ptr_unescape("~0") == "~"


def test_a_key_with_both_slash_and_tilde_round_trips(diff_cells):
    # order matters: escaping does ~ -> ~0 FIRST, so a literal "~1" in a key is not later misread
    # as an escaped "/". Unescaping undoes it in the opposite order: ~1 -> / FIRST, then ~0 -> ~.
    key = "a~1b/c~d"
    esc = diff_cells.ptr_escape(key)
    assert diff_cells.ptr_unescape(esc) == key
    assert esc != key  # the key actually needed escaping, this is not a vacuous round-trip


def test_a_plain_key_with_neither_character_escapes_to_itself(diff_cells):
    assert diff_cells.ptr_escape("plain") == "plain"
    assert diff_cells.ptr_unescape("plain") == "plain"


# ── resolve_json_pointer: escaped resolves, unescaped-with-a-real-slash still refuses ─────────────
def test_a_pointer_previously_unresolvable_now_resolves(diff_cells):
    """The exact shape OR-BASE-1 named: an `accepted-differences` leaf under a `paths` key that
    itself contains `/` used to split into segments naming nothing (`paths`, ``, `api`, ...)."""
    url = "/v1/overlay/{section}"
    doc = {"paths": {url: {"delete": {"summary": "S", "responses": {"400": {"description": "D"}}}}}}
    ptr = f"/paths/{diff_cells.ptr_escape(url)}/delete/summary"
    assert ptr == "/paths/~1v1~1overlay~1{section}/delete/summary"
    found, val = diff_cells.resolve_json_pointer(doc, ptr)
    assert (found, val) == (True, "S")
    ptr2 = f"/paths/{diff_cells.ptr_escape(url)}/delete/responses/400/description"
    assert diff_cells.resolve_json_pointer(doc, ptr2) == (True, "D")


def test_an_unescaped_slash_inside_a_key_still_fails_as_before(diff_cells):
    """The pre-0.3.10 convention (a raw `/`-join) must stay dead: it is not a second accepted
    spelling that happens to also work, or two conventions would be live for the same document."""
    url = "/v1/overlay/{section}"
    doc = {"paths": {url: {"delete": {"summary": "S"}}}}
    unescaped_ptr = "/paths" + url + "/delete/summary"  # the old, wrong spelling
    assert diff_cells.resolve_json_pointer(doc, unescaped_ptr) == (False, None)


def test_ptr_unescape_order_a_literal_tilde1_is_not_misread_as_a_slash(diff_cells):
    """Unescaping must do ~1 -> / BEFORE ~0 -> ~, or the escaped form of the literal text `~1`
    (`~01`) would come back as `/1` instead of `~1`."""
    assert diff_cells.ptr_unescape("~01") == "~1"


# ── json_paths_diff: the reporter must EMIT escaped pointers ──────────────────────────────────────
def test_json_paths_diff_escapes_a_slash_bearing_key(diff_cells):
    url = "/v1/overlay/{section}"
    golden = {"paths": {url: {"summary": "old"}}}
    candidate = {"paths": {url: {"summary": "new"}}}
    diffs = diff_cells.json_paths_diff(golden, candidate)
    assert len(diffs) == 1
    d = diffs[0]
    assert d["path"] == "/paths/~1v1~1overlay~1{section}/summary"
    assert d["golden"] == "old" and d["candidate"] == "new"
    # and the emitted pointer is one a maintainer can paste straight into accepted-differences:
    # resolve_json_pointer reaches the SAME leaf it names.
    assert diff_cells.resolve_json_pointer(golden, d["path"]) == (True, "old")
    assert diff_cells.resolve_json_pointer(candidate, d["path"]) == (True, "new")


def test_json_paths_diff_still_byte_identical_for_a_plain_key(diff_cells):
    """A key with neither `/` nor `~` — every key this walker has ever been handed outside an
    OpenAPI `paths` document — must escape to itself: this fix widens what can be addressed, it
    does not change a single byte of any path already printed for such a document."""
    diffs = diff_cells.json_paths_diff({"a": {"b": 1}}, {"a": {"b": 2}})
    assert diffs == [{"path": "/a/b", "golden": 1, "candidate": 2}]


def test_json_paths_diff_escapes_a_missing_and_extra_key_too(diff_cells):
    url = "/v1/overlay/{section}"
    diffs = diff_cells.json_paths_diff({}, {url: 1})
    assert diffs == [{"path": f"/{diff_cells.ptr_escape(url)}", "golden": None, "candidate": 1}]
    diffs = diff_cells.json_paths_diff({url: 1}, {})
    assert diffs == [{"path": f"/{diff_cells.ptr_escape(url)}", "golden": 1, "candidate": None}]


# ── _pointer_set: derived_from_body's own reader must unescape to match ───────────────────────────
def test_pointer_set_round_trips_json_paths_diff_output_through_a_slash_bearing_key(diff_cells):
    url = "/v1/overlay/{section}"
    golden = {url: {"body_bytes": 100}}
    candidate = {url: {"body_bytes": 140}}
    diffs = diff_cells.json_paths_diff(golden, candidate)
    assert len(diffs) == 1
    patched = {url: {"body_bytes": 140}}
    assert diff_cells._pointer_set(patched, diffs[0]["path"], diffs[0]["golden"]) is True
    assert patched == golden


def test_pointer_set_still_fails_closed_on_a_path_naming_nothing(diff_cells):
    assert diff_cells._pointer_set({"a": {"b": 1}}, "/a/nope/c", 9) is False
