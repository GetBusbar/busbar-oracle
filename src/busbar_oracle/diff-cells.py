#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""Diff two shadow-oracle recordings cell by cell — the REPLAY half of the oracle.

  diff-cells.py --golden <dir> --candidate <dir> --out <dir> [--cells cells.json] [--family <regex>]

Reads <dir>/cells/<safe>.json (normalize.py output) from both recordings plus each side's
ledger.tsv. The OWED set is every cell id in cells.json (filtered) whose GOLDEN ledger row is PASS —
a cell the golden itself could not record is a named gap (owed-gaps.txt), never owed and never
green. For each owed id the candidate must have an identical normalized cell.

Divergence classes (a cell may carry several; the first names the earliest divergent layer):
  missing.golden      owed but the golden cell file is absent (recorder bug: red)
  missing.candidate   owed, golden present, candidate absent (the build did not serve it: red)
  status              HTTP status / exit code
  headers             header key set or value
  body                JSON: list of JSON-pointer paths with old/new; text/SSE: first differing line
  effects.usage       ledger delta differs (money)
  effects.usage_after_restart  the usage a script cell read back after a restart differs (money the
                      store was supposed to keep: a wrong-shaped store call loses it while every
                      request still answers 200)
  effects.store_errors  the number of `store error` lines a script cell's boots logged differs
  effects.files       the EXACT file set a script cell found in the directories it watched (the
                      process's working directory and the config's own directory). The contract is
                      an ABSENCE — a binary that writes a WAL, a keyset or a probe file where 1.5.5
                      wrote nothing serves every request identically while doing it, so nothing else
                      in the cell moves and only this class says so
  effects.metrics     metric delta differs
  effects.audit       audit delta differs
  effects.stderr      exec cells: the process's stderr (boot refusals, warnings, CLI errors)
  effects.script      EVERY OTHER effects key. A script cell writes the evidence its cell is about
                      straight into `effects` under a name of its own choosing — `survived`,
                      `key_after_restart`, `validate_exit`, `hazard_lines`, `usage_before`/`usage_after`,
                      `list_plugins` — and the classes above name only nine FIXED keys, so none of
                      that was compared by anything. 14 golden cells carry such a key that no other
                      class mirrors, so a build that lost the store across a restart
                      (plugins.store-persist|store-sqlite's `survived` / `key_after_restart`), leaked a
                      hazard line (hazard|no-data-dir's `hazard_lines`) or moved the usage view a
                      refusal was measured against (teller|admit-refusal's `usage_before`/`usage_after`)
                      diverged on nothing the differ computed and its row printed `PASS  identical`.
                      Rated MONEY: only a `breaking` entry naming its changelog line may forgive it
  norm.rules          the set of normalizer rules that fired differs (a rule firing on ONE side is
                      itself a finding: something non-deterministic appeared or disappeared)

Writes <out>/report.json, <out>/report.md, <out>/owed.txt, <out>/owed-gaps.txt, <out>/diverging.txt
and prints one TSV row per owed id on stdout: <id> <TAB> PASS|FAIL <TAB> <classes> <TAB> <first-diff>
(the driver turns those into ledger rows via fleet-fixtures/lib.sh `record`).
Exit 0 always — the VERDICT is verdict.sh's job, not this file's.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

# THE CELL LIST AND THE REGISTER ARE THE PRODUCT'S DATA, NOT THE TOOL'S.
#
# `--cells` and `--accepted` defaulted to `<tool>/cells.json` and `<tool>/accepted-differences.json`,
# which was right only while the tool sat inside the product it judges. An installed tool ships
# neither file, so `busbar-oracle diff` without `--cells` read a path that does not exist. replay.sh
# always passes both, which is exactly why this went unnoticed: the seam is only visible to someone
# running the verdict half directly, and what they got was a traceback rather than the verdict.
#
# They now default to the DATA directory — the one place cells.json, the registers, the owed
# baseline, the digest pins and the drivers all live — with the tool's own directory as the fallback
# for the in-tree layout (BUSBAR_ORACLE_DATA unset).
_HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("BUSBAR_ORACLE_DATA") or _HERE

CLASS_ORDER = ["missing.golden", "missing.candidate", "status", "headers", "body", "effects.stderr",
               "effects.usage", "effects.usage_after_restart", "effects.store_errors",
               "effects.metrics", "effects.audit", "norm.rules", "effects.egress", "effects.readback",
               "effects.files", "effects.script"]
# The effects keys that have a class of their own above. EVERY OTHER key a driver writes into
# `effects` is compared under `effects.script` — the differ used to walk this tuple and nothing else,
# so a script cell's own evidence (whatever key it chose) was compared by no class at all.
# `exec_rules` is excluded because it is the normalizer's rule ledger and is compared as norm.rules.
NAMED_EFFECT_KEYS = ("usage", "usage_after_restart", "store_errors", "metrics", "audit", "stderr",
                     "egress", "readback", "files")
EFFECT_KEYS_WITH_OWN_CLASS = set(NAMED_EFFECT_KEYS) | {"exec_rules"}
# Weight per class; a cell's weight is its family's max class weight over the classes it diverged in.
# Money and refusal semantics dominate; cosmetics count but cannot outvote them.
CLASS_WEIGHT = {"missing.golden": 10, "missing.candidate": 10, "status": 10, "effects.usage": 10,
                "effects.usage_after_restart": 10, "effects.store_errors": 10,
                "body": 3, "effects.stderr": 3, "effects.audit": 3, "headers": 1, "effects.metrics": 1, "norm.rules": 1, "effects.egress": 10, "effects.readback": 10, "effects.files": 10, "effects.script": 10}
# The classes that are MONEY: an accepted difference may only carry one of these if it is a declared
# breaking change with a changelog line. Usage that a restart did not preserve, and a store the
# binary could not write to, are both money — the request statuses look fine either way. This set is
# kept in lockstep with CLASS_WEIGHT: every class this file rates 10 is a class no `improvement`
# entry may forgive. effects.egress (what busbar SENT upstream — a changed egress body is a changed
# bill and a changed prompt), effects.readback (whether the write the cell made actually persisted,
# and as what) and effects.files (a binary writing a WAL/keyset/probe file where 1.5.5 wrote nothing,
# invisible in every other class) were rated 10 but omitted here, so a plain `improvement` entry
# could waive them.
MONEY_CLASSES = {"status", "effects.usage", "effects.usage_after_restart", "effects.store_errors",
                 "missing.candidate", "effects.egress", "effects.readback", "effects.files",
                 "effects.script"}
assert MONEY_CLASSES <= set(CLASS_ORDER)
assert {k for k, w in CLASS_WEIGHT.items() if w == 10} == MONEY_CLASSES | {"missing.golden"}, \
    "MONEY_CLASSES must name every class CLASS_WEIGHT rates 10 (missing.golden is a recorder bug, never acceptable at all)"
# Families where BODY bytes are the contract itself (admin responses, boot messages, CLI output).
BODY_IS_CONTRACT = {"admin.ops", "boot.refusal", "boot.warning", "config.migrate", "cli", "ops.scrape"}
# On those families these classes are rated 10 whatever CLASS_WEIGHT says, because on a boot refusal
# or a CLI verb the bytes ARE the product's answer.
BODY_CONTRACT_TEN = ("status", "body", "missing.candidate", "missing.golden")


def rated_weight(fam: str, k: str) -> int:
    """What this cell's divergence in class `k` is actually WORTH — the number the report uses.

    THE MONEY GUARD AND THE WEIGHT USED TO ANSWER TO DIFFERENT AUTHORITIES, which is the whole of
    this finding. `MONEY_CLASSES` is keyed on the CLASS and the assertion above holds it to
    `CLASS_WEIGHT`; but the weight a cell's divergence is scored with is keyed on the FAMILY, and on
    the 604 cells of BODY_IS_CONTRACT the `body` class is rated 10 while CLASS_WEIGHT rates it 3 and
    MONEY_CLASSES does not name it at all. The assertion passed because it never looked at the
    family path. So a four-line `improvement` entry — no `kind: breaking`, no changelog line the
    loader demands — could forgive the ENTIRE stdout and stderr of a boot refusal: a cell whose only
    content is the refusal text an operator reads, weighted 10 in the D/W ratio and waived by an
    entry the money guard never inspected.

    One function now answers both questions, so they cannot disagree again: `money_at_cell()` asks
    it which classes are money HERE, and the scoring below asks it what the divergence is worth."""
    if fam in BODY_IS_CONTRACT and k in BODY_CONTRACT_TEN:
        return 10
    return CLASS_WEIGHT[k]


def money_at_cell(fam: str) -> set:
    """The classes no `improvement` entry may forgive ON A CELL OF THIS FAMILY: every class this
    file rates 10 for it. Equal to MONEY_CLASSES everywhere except the BODY_IS_CONTRACT families,
    where it also holds `body`."""
    return {k for k in CLASS_ORDER if rated_weight(fam, k) == 10}


# ── `additive` — A THIRD REGISTER KIND, FOR GROWTH THE TOOL CAN VERIFY IS GROWTH ──────────────────
# `improvement` may never take a class `money_at_cell()` rates 10 for the cell's family (the boot-
# refusal-stdout hole this file was written against); `breaking` may take anything, but only by
# declaring the change and eating the blast radius. Neither fits a body that grew a KEY: F-011's
# admin.ops views (`GetHooks`, `PostHooks`, `GetOpenapiJson`, ...) are BODY_IS_CONTRACT — `body` is
# rated 10 there — so `improvement` cannot cover them, and `breaking` would be a lie: nothing broke,
# a value that existed is still there with the SAME value, and only new keys were added beside it.
#
# `additive` is authorized to take `body` and `headers` ONLY where the tool itself has PROVEN the
# candidate is a superset of the golden at every path the golden defines — never on the strength of
# an owner's say-so alone, which is what `classes: [body, headers]` on an `improvement`/`breaking`
# entry would otherwise be. See `additive_superset()`/`additive_headers_superset()` below for the
# proof, and the fired-additive branch in `main()` for where a failed proof leaves the cell red
# rather than silently falling back to an ordinary acceptance.
ADDITIVE_CLASSES = {"body", "headers", "effects.stderr", "status"}
# The classes `additive` may take WITHOUT declaring `text_list_growth`. `effects.stderr` has no
# JSON-superset relation at all — it is a raw string — so it is refused at load unless the entry
# opts into the text_list_growth check specifically (see below).
ADDITIVE_JSON_CLASSES = {"body", "headers"}
# `status` is the one class `additive` may EVER take that is also in MONEY_CLASSES, and only under
# `new_route` (see below): a route that did not exist in 1.5.5 (golden 404, the 1.5.5 not-found
# envelope) now answers is not a superset of anything — there is no relation between a stub refusal
# and a real response — so it is its own gate, not a widening of additive_superset().
ADDITIVE_STATUS_CLASSES = {"status"}


def body_as_json(body) -> tuple:
    """(parsed, ok). `ok` is True only when `body` is JSON — already-structured (`body["json"]`) or
    text that parses (`body["text"]` via `json.loads`). An SSE/plain-text body, or one side that
    fails to parse, makes `ok` False: additive body forgiveness is JSON-superset ONLY, by the spec's
    own words ("if either side is not JSON, no body forgiveness") — there is no text-superset
    relation to fall back to."""
    if not isinstance(body, dict):
        return None, False
    if "json" in body:
        return body["json"], True
    text = body.get("text")
    if isinstance(text, str):
        try:
            return json.loads(text), True
        except (ValueError, TypeError):
            return None, False
    return None, False


def resolve_json_pointer(doc, pointer: str) -> tuple:
    """(found, value) for a `/a/b/0`-style JSON pointer against `doc`, in the SAME path convention
    `additive_superset` builds (`""`/`"/"` is the root; a list index is its decimal string). Used to
    validate a `description_corrections` entry AT LOAD, against the golden's own recorded body —
    the register may only name a path that actually IS a string somewhere real, never a hypothetical
    one a typo could silently mean nothing."""
    if pointer in ("", "/"):
        return True, doc
    cur = doc
    for part in pointer.lstrip("/").split("/"):
        if isinstance(cur, dict):
            if part not in cur:
                return False, None
            cur = cur[part]
        elif isinstance(cur, list):
            if not part.lstrip("-").isdigit():
                return False, None
            i = int(part)
            if i < 0 or i >= len(cur):
                return False, None
            cur = cur[i]
        else:
            return False, None
    return True, cur


def is_1_5_5_not_found_envelope(body_json) -> bool:
    """Whether `body_json` is shaped like 1.5.5's not-found response: a JSON object whose ONLY
    top-level key is `error`, itself an object carrying a string `message` (busbar's real 404
    bodies, e.g. `{"error": {"code": "not_found", "message": "hook `x` not found"}}` —
    `code`/`param`/`type` vary or are absent, `message` never is). This is `new_route`'s gate: a
    route 1.5.5 did not have answered with exactly this stub, never a real payload, so a candidate
    that now answers for real cannot be compared against it by ANY relation — not a superset, not a
    growth, not a correction. There is nothing to walk; the golden body's SHAPE is the only thing
    that says "this route was a stub", so that shape is all this checks."""
    return (isinstance(body_json, dict) and set(body_json.keys()) == {"error"}
            and isinstance(body_json["error"], dict) and isinstance(body_json["error"].get("message"), str))


def additive_superset(golden, cand, path: str, null_to_value: set, string_diffs: list | None = None,
                       description_corrections: set | None = None, corrections_report: list | None = None) -> str | None:
    """The first JSON-pointer path where `cand` is NOT a superset of `golden`, or None if it is one
    everywhere. The relation, applied recursively:

      dict     every golden key present in cand with an equal (recursively-superset) value; cand
               may carry extra keys the golden does not name.
      list     cand is at least as long as golden, and golden is a PREFIX of cand under this same
               relation applied per element (golden == cand — same length, every element equal — is
               the length-equal case of the same rule, not a separate one). Extra trailing elements
               are additive growth; an element INSERTED before the end, or one reordered, moves
               every following golden element's paired candidate index and fails the recursive
               check at the first element that no longer matches, which is the right answer for
               "was every original array member kept, in place, with everything after it new".
      scalar   equal values, UNLESS golden is None and cand is not: a null growing into a real value
               is not free — it names a path the register did not always populate, and the entry
               must list it under `null_to_value` to claim it (never a blanket default, or `null`
               would stop being a signal that the field is genuinely absent).

    `string_diffs`, when passed a list, turns a STRING leaf's mismatch from an immediate failure
    into a deferred one: `(path, golden_str, cand_str)` is appended and the walk continues, instead
    of stopping at the first string that differs. This is what lets the caller apply
    `text_list_growth_check()` to a leaf embedded inside an otherwise-superset JSON body (an error
    message field that grew its own backtick list) while still catching a SECOND differing leaf as
    a real divergence — two strings that changed is not "one list grew", whatever either one says
    on its own. Every other mismatch (a missing key, a short array, a non-string scalar, a `null`
    not covered by `null_to_value`) is unaffected and fails immediately, exactly as before: this
    parameter only ever WIDENS what continues walking, never what ultimately passes.

    `description_corrections`, when it names `path`, forgives a STRING leaf's mismatch OUTRIGHT —
    no growth proof, no relation to check, just a registered claim that 1.5.5's prose was wrong and
    this is the correction. Checked BEFORE `string_diffs`, so a corrected leaf never competes for
    the one slot `text_list_growth` allows: an entry may correct a description AND separately prove
    growth on a different leaf in the same body. `corrections_report`, when passed a list, records
    `(path, golden_str, cand_str)` for every leaf forgiven this way, for the accepted row to name."""
    if golden is None and cand is not None:
        return None if path in null_to_value else (path or "/")
    if isinstance(golden, dict):
        if not isinstance(cand, dict):
            return path or "/"
        for k, gv in golden.items():
            if k not in cand:
                return f"{path}/{k}"
            bad = additive_superset(gv, cand[k], f"{path}/{k}", null_to_value, string_diffs,
                                     description_corrections, corrections_report)
            if bad is not None:
                return bad
        return None
    if isinstance(golden, list):
        if not isinstance(cand, list) or len(cand) < len(golden):
            return path or "/"
        for i, gv in enumerate(golden):
            bad = additive_superset(gv, cand[i], f"{path}/{i}", null_to_value, string_diffs,
                                     description_corrections, corrections_report)
            if bad is not None:
                return bad
        return None
    if golden != cand:
        p = path or "/"
        if description_corrections and p in description_corrections and isinstance(golden, str) and isinstance(cand, str):
            if corrections_report is not None:
                corrections_report.append((p, golden, cand))
            return None
        if string_diffs is not None and isinstance(golden, str) and isinstance(cand, str):
            string_diffs.append((p, golden, cand))
            return None
        return p
    return None


def additive_headers_superset(gh: dict, ch: dict, body_ok: bool) -> str | None:
    """The first header name where `ch` is NOT a superset of `gh`, or None if it is one everywhere.
    Every golden header must be present in the candidate with an EQUAL value; extra candidate
    headers are additive growth, same as an extra JSON key. `content-length` is exempted only when
    `body_ok` — this SAME entry's body check already proved the candidate a superset there, so the
    length moving is that growth's own shadow, not a second, unrelated divergence; if this entry
    does not also cover `body` (or the body check failed), `content-length` is held to the ordinary
    equality rule like any other header."""
    gh2, ch2 = dict(gh or {}), dict(ch or {})
    if body_ok:
        gh2.pop("content-length", None); ch2.pop("content-length", None)
    for k, v in gh2.items():
        if k not in ch2 or ch2[k] != v:
            return k
    return None


# ── `text_list_growth` — THE SAME PROOF, FOR A LIST NAMED IN PROSE INSTEAD OF JSON ────────────────
# A body/stderr that says "expected `groups`, `hooks`, `root`, or `plugin_versions`" carries the
# same kind of growth as a JSON array: a NEW key-list entry (`identity-providers`) appended to an
# enum. But there is no JSON structure to walk — the list lives inside one sentence, and the rest
# of that sentence (the template, "expected", the trailing punctuation) is exactly the part that
# must NOT be allowed to move for free, or additive would launder a genuinely reworded message
# (`admin.ops|DeleteOverlaySection|not-found` changed "expected" to "expected one of" IN ADDITION
# TO growing the list — that rewording must still be red).
_LIST_ITEM_RX = re.compile(r"`([^`]*)`")
# One item, then one-or-more more items joined by ", or ", " or ", or ", " — tried longest-first so
# ", or " is not swallowed as a bare ", ". At least two items: a single `token` in prose is not "a
# list", it is just a quoted word.
_LIST_RUN_RX = re.compile(r"`[^`]*`(?:(?:, or | or |, )`[^`]*`)+")


def find_backtick_lists(text: str) -> list:
    """[(start, end, [item, ...]), ...] for every maximal backtick-quoted list run in `text`, in
    the order they appear."""
    out = []
    for m in _LIST_RUN_RX.finditer(text):
        out.append((m.start(), m.end(), _LIST_ITEM_RX.findall(m.group(0))))
    return out


def _first_diff_byte(a: str, b: str) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def _sans_additive_prefix(note: str) -> str:
    """`note` with its leading "additive: " stripped, for splicing into a wider message that
    already says "additive:" once (e.g. naming the JSON path a leaf-level check failed at)."""
    return note[len("additive: "):] if note.startswith("additive: ") else note


def text_list_growth_check(golden: str, cand: str) -> tuple:
    """(removed, note). `removed` is the list of items present in `cand`'s list but not `golden`'s
    — reported on the accepted row — and is None on failure, where `note` names why.

    The relation: find every backtick-list run in each text (same count, same order, or refused —
    "growth in two lists" below); every run except AT MOST ONE must be byte-identical between the
    two texts; the ONE run that differs must hold golden's items as a PREFIX, in order (an item
    removed, reordered, or inserted before the end breaks this); and — the actual proof — splicing
    GOLDEN's own raw list text back into `cand` at that run's position must reproduce `golden`
    BYTE FOR BYTE. That last step is what catches a reworded template: the surrounding sentence is
    never inspected on its own terms, only through whether putting golden's list back closes the
    gap completely. A `\\d+`-shaped hole around the list — different wording before or after it —
    fails this splice exactly where the wording starts to differ."""
    if golden == cand:
        return [], None
    g_lists, c_lists = find_backtick_lists(golden), find_backtick_lists(cand)
    if len(g_lists) != len(c_lists):
        return None, (f"additive: text_list_growth could not pair the golden and candidate lists "
                       f"(golden has {len(g_lists)}, candidate has {len(c_lists)})")
    if not g_lists:
        i = _first_diff_byte(golden, cand)
        return None, f"additive: not a superset at text byte {i} (no backtick list found)"
    changed = []
    for i, (g_l, c_l) in enumerate(zip(g_lists, c_lists)):
        if g_l[2] != c_l[2]:
            changed.append(i)
    if len(changed) > 1:
        return None, ("additive: text_list_growth touched more than one backtick list — one "
                       "declared list per entry keeps the check narrow; split it into two entries")
    if not changed:
        # every list is byte-identical item-for-item; the texts still differ, so the difference is
        # in the surrounding prose, not in any list at all.
        i = _first_diff_byte(golden, cand)
        return None, f"additive: not a superset at text byte {i}"
    k = changed[0]
    gs, ge, gitems = g_lists[k]
    cs, ce, citems = c_lists[k]
    mismatch = next((i for i in range(len(gitems)) if i >= len(citems) or citems[i] != gitems[i]), None)
    if mismatch is not None:
        got = repr(citems[mismatch]) if mismatch < len(citems) else "<missing>"
        return None, (f"additive: not a superset at list item {mismatch} ({got} != {gitems[mismatch]!r}, "
                       f"golden's items are not a prefix of the candidate's)")
    if len(citems) <= len(gitems):
        return None, "additive: not a superset (candidate's list added no new items)"
    # every OTHER list must be byte-identical, not merely item-identical — a re-ordered "or"/comma
    # around unrelated, unchanged items is still a template change this check must not launder.
    for i, (g_l, c_l) in enumerate(zip(g_lists, c_lists)):
        if i == k:
            continue
        if golden[g_l[0]:g_l[1]] != cand[c_l[0]:c_l[1]]:
            return None, f"additive: not a superset at text byte {_first_diff_byte(golden, cand)}"
    # THE PROOF: splice golden's own raw list text back into the candidate at the grown run's
    # position. If what remains is not byte-identical to golden, the difference is not the list.
    reconciled = cand[:cs] + golden[gs:ge] + cand[ce:]
    if reconciled != golden:
        return None, f"additive: not a superset at text byte {_first_diff_byte(golden, reconciled)}"
    return citems[len(gitems):], None

# ── THE ONE EXEMPTION FROM `norm.rules`, AND THE ONLY KIND OF RULE ALLOWED INTO IT ───────────────
# `norm.rules` exists because a normalizer rule that fires on ONE side is itself a finding: content
# was rewritten on the candidate that was not rewritten on the golden, and the rewrite is exactly
# where a real divergence goes to hide. ORDER_RULES is the single exemption — a RE-SORT changes no
# content at all, so whether a map happened to come out sorted on one run is not a contract.
#
# It was a bare set literal INSIDE compare(), hand-copied from normalize.py's rule list, and it had
# already drifted: normalize.py grew `boot.exhaustion-order` (sort_pool_lines, a third sort_runs
# call) and this set was never told, so that rule counted as one-sided. That drift was the harmless
# direction. The other direction is not: the membership test here is a plain name match, so putting
# a rule that DROPS or BLANKS content into this set — metrics.timing (drops a key), metrics.shape
# (drops lines), body.keep-lines (keeps only matching lines), hdr.retry-after (blanks a value) —
# would make its one-sided firing invisible, and a one-sided firing of a rule that removes content
# is precisely how a money figure leaves a cell without a class saying so.
#
# So the two kinds are named separately and asserted disjoint at import. A re-sort rule may be
# exempted; a rule that removes or rewrites content may never be, whatever it is called. Adding a
# CONTENT_RULES name to ORDER_RULES stops this file from loading rather than quietly widening the
# exemption. replay-selftest.sh holds the set to normalize.py's ACTUAL re-sort rules as well, so
# drift in either direction is red rather than merely lucky.
ORDER_RULES = {"boot.pool-order", "boot.error-order", "boot.exhaustion-order", "boot.pair-order",
               "keys.order"}
# Every normalize.py rule whose effect is to DROP, BLANK or REWRITE content rather than reorder it.
CONTENT_RULES = {"hdr.date", "hdr.retry-after", "hdr.etag", "hdr.length", "id.wire", "audit.hash",
                 "ts.unix", "ts.usage-window", "info.uptime", "ver.string", "key.id",
                 "metrics.timing", "metrics.cooldown", "metrics.shape", "metrics.absolute",
                 "body.keep-lines", "keep.header", "keep.header-min", "keep.json_key",
                 "keep.text_regex", "egress.cred", "egress.host", "egress.body", "text.port",
                 "stderr.platform-capability", "egress.elapsed", "metrics.concurrent-attempts"}
assert not (ORDER_RULES & CONTENT_RULES), \
    ("a rule that drops/blanks/rewrites content may never be exempted from norm.rules: "
     f"{sorted(ORDER_RULES & CONTENT_RULES)}")

# ── A RULE ABOUT THE HOST IS NOT A RULE ABOUT BUSBAR, AND IS NOT SILENT EITHER ───────────────────
# `stderr.platform-capability` drops lines that report what the RECORDING MACHINE can do (see
# normalize.py). Such a rule is ASYMMETRIC BY CONSTRUCTION: the golden is recorded on darwin, where
# jemalloc cannot start its purge thread, and the CI candidate runs on linux, where the same binary
# never prints the line. The rule therefore fires on the golden side and not the candidate side on
# every cell that carries a boot log — which is the correct outcome, and which `norm.rules` would
# report as 56 divergences about the difference between two laptops.
#
# It is NOT put in ORDER_RULES. ORDER_RULES means "changes no content", and this rule removes a line;
# the disjointness assert above exists precisely so that a stripping rule cannot be smuggled into the
# exemption, and quietly reclassifying this one to buy silence is the move that assert is there to
# stop. So it gets its own category, with a different and weaker promise: its one-sided firing does
# not make a cell RED, and is REPORTED on the cell's row regardless — including on a cell that is
# otherwise green, which is the case the ordinary `detail` channel drops on the floor.
#
# The bar for adding a name here is not "this is noisy". It is: the line is emitted by the host's
# capabilities and not in response to anything a request did, so 1.5.5 and 1.6.0 CANNOT disagree
# about it. Anything a request can influence belongs in norm.rules where it can be red.
HOST_RULES = {"stderr.platform-capability"}
assert HOST_RULES <= CONTENT_RULES, \
    f"a host rule still removes content and must be declared as such: {sorted(HOST_RULES - CONTENT_RULES)}"
assert not (HOST_RULES & ORDER_RULES), \
    f"a host rule is not a re-sort and may not take the norm.rules exemption: {sorted(HOST_RULES & ORDER_RULES)}"


def host_rule_skew(g, c) -> dict | None:
    """Which HOST_RULES fired on one side only. Reported on the row whether or not the cell diverges,
    so "this cell is green because a host-capability line was dropped from the golden" is a sentence
    the ledger actually contains rather than one a reader has to infer.

    Either side may be None: a cell missing from the golden or the candidate has its own class, and
    asking which host rules it applied is not a question. Say nothing rather than raising — this is a
    reporting nicety, and it must never be the reason the differ cannot render a row."""
    if not isinstance(g, dict) or not isinstance(c, dict):
        return None
    ga = {r for r in g.get("applied", [])} & HOST_RULES
    ca = {r for r in c.get("applied", [])} & HOST_RULES
    if ga == ca:
        return None
    return {"only_golden": sorted(ga - ca), "only_candidate": sorted(ca - ga)}


def provenance_revs(m: dict) -> set:
    """Every harness revision a recording's cells could have been produced under.

    `harness_rev` is the rev the recording CLAIMS, and on a recording that was merged or
    re-normalized it is the rev of only some of its cells. The other names are where the rest went:

      harness_rev_history       merge-recordings.py and renormalize.sh push every superseded rev here
      merged_from[].harness_rev the rev each merged PART was recorded under, per part
      harness_rev_recorded      the rev the cells were RECORDED under, before any re-stamp

    That last field was a ghost: no file in this tool wrote it and no file read it, while it sat in
    the shipped golden's meta.json carrying a real digest. A field that is neither written nor read
    is a claim nothing can check — so it is written now (merge-recordings.py, renormalize.sh, both
    `setdefault`, so the ORIGINAL recording rev survives every later re-stamp) and it is read here.

    Non-string and empty entries are ignored rather than raising: this set decides whether a
    comparison may happen at all, and a malformed history entry must not be the reason the differ
    cannot render its refusal."""
    revs = set()

    def add(v):
        if isinstance(v, str) and v.strip():
            revs.add(v.strip())

    if not isinstance(m, dict):
        return revs
    add(m.get("harness_rev"))
    add(m.get("harness_rev_recorded"))
    for v in m.get("harness_rev_history") or []:
        add(v)
    for part in m.get("merged_from") or []:
        if isinstance(part, dict):
            add(part.get("harness_rev"))
    return revs


def entry_may_take(e: dict, money_here: set) -> set:
    """The classes this register entry may forgive ON THIS CELL.

    `e["allowed"]` is what the entry says (checked once, at load, against MONEY_CLASSES). This is
    the same question asked where the answer can depend on the cell: a class that is rated 10 for
    this cell's family is money HERE, and only a `breaking` entry with a changelog line may take it,
    exactly as the loader's guard demands for the class-keyed money set. An entry that is not so
    entitled simply does not cover the class — the class stays in `need`, the cell stays red, and
    the row names the divergence instead of an acceptance."""
    if e["money_ok"]:
        return e["allowed"]
    return e["allowed"] - money_here


def transform_pattern_too_broad(pattern: str) -> str | None:
    """Why this transform pattern may not ship, or None if it is fine.

    A transform is credited for a class the moment the rewritten pair is byte-identical (see the
    fired-transform branch below): the rewrite is trusted to name EXACTLY the token it erases, and
    nothing else. A pattern that can match the EMPTY STRING (`.*`, `\\s*`, `(.|\\n)*`, `x?`, `a{0,3}`)
    or that names no literal text at all (`.`, `\\d+`, `.+`) is not a token — it is a shape that can
    swallow arbitrary surrounding content, so "byte-identical after the rewrite" stops proving the
    erased text was the accepted one and starts proving only that the pattern was wide enough. This
    is checked at LOAD, over the pattern's source text, so a transform this broad refuses to load
    rather than quietly widening what a rewrite may erase.

    Matching-empty is checked directly: compile the pattern and ask whether it matches "". Having
    "no literal text" is checked by stripping every regex construct that is not a literal character
    — escapes (`\\d`, `\\s`, `\\n`, ...), character classes (`[...]`), groups and lookaround markers,
    quantifiers (`* + ? {m,n}`), alternation (`|`) and anchors/dot (`^ $ .`) — and refusing if nothing
    remains. Escaped literal punctuation (`\\.` meaning a literal dot, `\\-` meaning a literal hyphen)
    is conservatively treated as non-literal too: a pattern with real intent to strip fixed text
    reads as PLAIN characters somewhere in it (D-1's `diag=BUSBAR-\\d{4}` has "diag=BUSBAR-"), so
    refusing on an escape-only pattern costs nothing a real acceptance needed."""
    try:
        rx = re.compile(pattern, re.M)
    except re.error as e:
        return f"does not compile: {e}"
    if rx.match("") is not None:
        return "can match the empty string (matches '')"
    stripped = re.sub(r"\\.|\[[^\]]*\]|\((?:\?[:=!<]?)?|\)|[.^$*+?{}|]", "", pattern)
    if not stripped:
        return "names no literal text (only regex syntax/escapes/character classes)"
    return None


def allowed_classes(kind: str, classes: set) -> set:
    """The classes an accepted-differences entry may forgive — ONE definition, shared by the
    loader's money guard and the matcher below.

    They used to be two. The loader tested `e["classes"]` (what the entry SAYS) while the matcher
    widened an entry that names no classes to a whole default set (what the entry DOES). An entry
    with `kind: breaking` and no `classes` key therefore had an empty intersection with
    MONEY_CLASSES — the guard saw nothing to refuse, no changelog line was demanded — and then
    matched against every class there is. One four-line entry with `cells: "."` silently forgave a
    200 -> 500 and a changed bill on every cell in the corpus and the oracle reported GREEN.
    Deriving the effective set here and validating THAT closes it by construction.

    `missing.golden` is excluded from BOTH defaults and refused outright when named: it is the
    golden's own ledger claiming PASS for a cell it did not write, i.e. a recorder bug, and
    CLASS_WEIGHT's assertion above already calls it "never acceptable at all"."""
    if classes:
        return set(classes)
    if kind == "breaking":
        return set(CLASS_ORDER) - {"missing.golden"}
    if kind == "additive":
        # `effects.stderr` is NEVER a default — it has no JSON-superset relation (a raw string) and
        # is only ever reachable by naming it explicitly alongside `text_list_growth: true`.
        return set(ADDITIVE_JSON_CLASSES)
    return set(CLASS_ORDER) - MONEY_CLASSES - {"missing.golden"}


# A cell's `compare: [classes]` is a per-cell waiver with none of the register's ceremony: no owner,
# no kind, no changelog line, no rationale, and — until this guard — no limit on what it threw away.
# It is a WHITELIST, so everything it does not name is dropped, which makes it the widest instrument
# in the oracle and the easiest one to write by accident. `cli|--generate-signing-key` carried
# `compare: ["status"]` for a random key that normalize.py already hashes to `<HASH>`: the exit code
# was compared and the whole rest of the cell — the operator guidance block on stderr, the file set
# the run left behind (effects.files), the script evidence, the egress, the readback, the normalizer
# rules that fired — was discarded, on a verb whose entire job is to MINT A SECRET.
#
# So the DROPPED set is policed the same way the register's is. `compare` may only give up classes
# that are cheap to be wrong about; it may never give up money, and never `effects.files` or
# `effects.script` (a binary writing a keyset file where 1.5.5 wrote nothing is invisible in every
# other class, which is exactly why those two are rated 10). And it must say WHY in the cell's own
# `why`, because "part of this output is random" is a claim about the recording that a reader has to
# be able to check.
COMPARE_MAY_NEVER_DROP = MONEY_CLASSES | {"effects.files", "effects.script"}
# missing.* is never dropped by a `compare` list (the differ keeps it unconditionally): an owed cell
# the candidate did not serve is not a narrowing question.
COMPARE_ALWAYS_KEPT = {"missing.golden", "missing.candidate"}


def compare_narrowed(only) -> list:
    """The classes a cell's `compare` list DROPS, in CLASS_ORDER. Printed on the cell's report row."""
    keep = set(only) | COMPARE_ALWAYS_KEPT
    return [k for k in CLASS_ORDER if k not in keep]


def check_compare_policy(cells: list, path: str) -> None:
    """Refuse the whole run if any cell's `compare` list drops a class it may not, or carries no
    `why`. Checked at LOAD, over every cell in the corpus, whether or not the run selects it — a
    policy that only bites on the cells a filtered run happens to touch is not a policy."""
    for c in cells:
        only = c.get("compare")
        if only is None:
            continue
        if not isinstance(only, list) or not only:
            sys.exit(f"{os.path.basename(path)}: cell {c['id']!r} has a `compare` that is not a non-empty list of classes")
        unknown = sorted(set(only) - set(CLASS_ORDER))
        if unknown:
            sys.exit(f"{os.path.basename(path)}: cell {c['id']!r} names unknown compare class(es) {unknown}; "
                     f"a typo here silently drops the class it meant to keep")
        if not (c.get("why") or "").strip():
            sys.exit(f"{os.path.basename(path)}: cell {c['id']!r} narrows `compare` to {sorted(only)} but carries no `why`. "
                     f"A per-cell waiver with no stated reason is the one kind this oracle does not accept.")
        forbidden = sorted(set(compare_narrowed(only)) & COMPARE_MAY_NEVER_DROP)
        if forbidden:
            sys.exit(f"{os.path.basename(path)}: cell {c['id']!r} narrows `compare` to {sorted(only)}, which DROPS "
                     f"{forbidden}. `compare` may only give up classes outside MONEY_CLASSES, and never "
                     f"effects.files/effects.script. If the output really is non-deterministic, normalize it in "
                     f"normalize.py so the class can still be compared; if it is a deliberate behavioural change, "
                     f"it belongs in accepted-differences.json where it needs an owner and a changelog line.")


def safe_name(cell_id: str) -> str:
    return cell_id.replace("|", "__")


def load_ledger(d: str) -> dict:
    """id -> (status, detail), THE LAST ROW WINNING — the same rule fleet-fixtures/verdict.sh uses on
    the very same file.

    `record` APPENDS, so an id can legitimately carry more than one row: a driver that retried, a
    later step that revised its own verdict. This read used to keep the FIRST row, so the two halves
    of one gate disagreed about what the golden said. The dangerous direction is not the loud one: a
    golden whose first row is SKIP and whose corrected row is PASS drops out of the OWED set
    entirely, so its cell is never compared, never reaches diverging.txt, and a divergence on it
    cannot be seen anywhere. Last-row-wins keeps the correction, exactly as the verdict does."""
    out = {}
    p = os.path.join(d, "ledger.tsv")
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8", errors="replace") as f:
        for ln in f:
            parts = ln.rstrip("\n").split("\t")
            if len(parts) >= 2:
                out[parts[0]] = (parts[1], parts[3] if len(parts) > 3 else "")
    return out


def load_cell(d: str, cell_id: str):
    p = os.path.join(d, "cells", safe_name(cell_id) + ".json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # a corrupt cell is a divergence, not a crash
        return {"__corrupt__": str(e)}


def json_paths_diff(a, b, path="", out=None, limit=50):
    """List JSON-pointer paths where a != b (first `limit`)."""
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append({"path": f"{path}/{k}", "golden": None, "candidate": b[k]})
            elif k not in b:
                out.append({"path": f"{path}/{k}", "golden": a[k], "candidate": None})
            else:
                json_paths_diff(a[k], b[k], f"{path}/{k}", out, limit)
            if len(out) >= limit:
                break
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append({"path": f"{path}/len", "golden": len(a), "candidate": len(b)})
        for i, (x, y) in enumerate(zip(a, b)):
            json_paths_diff(x, y, f"{path}/{i}", out, limit)
            if len(out) >= limit:
                break
        return out
    if a != b:
        out.append({"path": path or "/", "golden": a, "candidate": b})
    return out


def text_diff(a: str, b: str):
    al, bl = a.split("\n"), b.split("\n")
    for i, (x, y) in enumerate(zip(al, bl)):
        if x != y:
            return {"line": i + 1, "golden": x[:300], "candidate": y[:300], "golden_lines": len(al), "candidate_lines": len(bl)}
    if len(al) != len(bl):
        i = min(len(al), len(bl))
        return {"line": i + 1, "golden": (al[i] if i < len(al) else "<EOF>")[:300],
                "candidate": (bl[i] if i < len(bl) else "<EOF>")[:300], "golden_lines": len(al), "candidate_lines": len(bl)}
    return None


def body_diff(g, c):
    if g == c:
        return None
    if isinstance(g, dict) and isinstance(c, dict):
        if "eventstream" in g and "eventstream" in c:
            # normalize.py's `eventstream.frames` representation: an ordered [[event-type, payload]]
            # list. It diffs as JSON so the report names the frame INDEX and the path inside its
            # payload that moved ("0.1.role"), instead of the useless "the bodies differ" a text
            # diff over binary framing used to give.
            return {"kind": "json", "paths": json_paths_diff(g["eventstream"], c["eventstream"])}
        if "json" in g and "json" in c:
            return {"kind": "json", "paths": json_paths_diff(g["json"], c["json"])}
        if "text" in g and "text" in c:
            return {"kind": "text", **(text_diff(g["text"], c["text"]) or {})}
        return {"kind": "shape", "golden": sorted(g), "candidate": sorted(c)}
    return {"kind": "shape", "golden": type(g).__name__, "candidate": type(c).__name__}


def compare(g: dict, c: dict) -> tuple[list, dict]:
    classes, detail = [], {}
    if "__corrupt__" in g:
        return ["missing.golden"], {"missing.golden": g["__corrupt__"]}
    if "__corrupt__" in c:
        return ["missing.candidate"], {"missing.candidate": c["__corrupt__"]}
    if g.get("status") != c.get("status"):
        classes.append("status"); detail["status"] = {"golden": g.get("status"), "candidate": c.get("status")}
    gh, ch = g.get("headers", {}), c.get("headers", {})
    if gh != ch:
        classes.append("headers")
        detail["headers"] = {"only_golden": sorted(set(gh) - set(ch)), "only_candidate": sorted(set(ch) - set(gh)),
                             "changed": {k: {"golden": gh[k], "candidate": ch[k]} for k in sorted(set(gh) & set(ch)) if gh[k] != ch[k]}}
    bd = body_diff(g.get("body"), c.get("body"))
    if bd is not None:
        classes.append("body"); detail["body"] = bd
    ge, ce = g.get("effects", {}), c.get("effects", {})
    for k in NAMED_EFFECT_KEYS:
        if ge.get(k) != ce.get(k):
            classes.append(f"effects.{k}")
            if k == "stderr" and isinstance(ge.get(k), str) and isinstance(ce.get(k), str):
                detail["effects.stderr"] = {"kind": "text", **(text_diff(ge[k], ce[k]) or {})}
            else:
                detail[f"effects.{k}"] = {"paths": json_paths_diff(ge.get(k), ce.get(k))}
    # EVERY OTHER EFFECTS KEY, or a script cell's whole contract is compared by nobody. The loop
    # above walks a FIXED list, and a script cell writes its evidence under a name of its own
    # (`survived`, `key_after_restart`, `validate_exit`, `hazard_lines`, `usage_before`) — 14 golden
    # cells carry such a key that neither `status` nor `body` mirrors, so a candidate could flip
    # `survived` from "yes" to "no" and the row still printed `PASS  identical`. A key present on
    # one side only counts too: an effect that stopped being reported is exactly as much a
    # divergence as one whose value moved.
    moved = sorted(k for k in (set(ge) | set(ce)) - EFFECT_KEYS_WITH_OWN_CLASS if ge.get(k) != ce.get(k))
    if moved:
        classes.append("effects.script")
        detail["effects.script"] = {"keys": moved,
                                    "paths": json_paths_diff({k: ge.get(k) for k in moved},
                                                             {k: ce.get(k) for k in moved})}
    _exempt = ORDER_RULES | HOST_RULES
    ga = [r for r in g.get("applied", []) + (g.get("effects") or {}).get("exec_rules", []) if r not in _exempt]
    ca = [r for r in c.get("applied", []) + (c.get("effects") or {}).get("exec_rules", []) if r not in _exempt]
    if sorted(ga) != sorted(ca):
        classes.append("norm.rules")
        detail["norm.rules"] = {"only_golden": sorted(set(ga) - set(ca)), "only_candidate": sorted(set(ca) - set(ga))}
    classes.sort(key=CLASS_ORDER.index)
    return classes, detail


def first_diff_text(classes, detail) -> str:
    if not classes:
        return ""
    k = classes[0]
    d = detail.get(k)
    if d is None and detail.get("accepted.transform"):
        return f"{k}: identical after the accepted rewrite {detail['accepted.transform']}"
    if k in ADDITIVE_CLASSES and detail.get("additive.removed"):
        parts = []
        for entry in detail["additive.removed"]:
            items = entry.get("removed") or []
            if items:
                where = f" at {entry['path']}" if entry.get("path") else ""
                parts.append(f"added {', '.join(dict.fromkeys(items))}{where}")
            for corr_path, gtext, ctext in entry.get("corrections") or []:
                parts.append(f"corrected {corr_path}: {gtext!r} -> {ctext!r}")
            if entry.get("note"):
                parts.append(entry["note"])
        return ("additive: " + "; ".join(parts)) if parts else "additive: superset (no new items)"
    if k in ADDITIVE_CLASSES and detail.get("additive.rejected"):
        return detail["additive.rejected"]
    if k == "status":
        return f"status {d['golden']} -> {d['candidate']}"
    if k == "headers":
        parts = []
        if d["only_golden"]: parts.append("missing " + ",".join(d["only_golden"][:3]))
        if d["only_candidate"]: parts.append("added " + ",".join(d["only_candidate"][:3]))
        for hk, hv in list(d["changed"].items())[:2]:
            parts.append(f"{hk}: {hv['golden']!r} -> {hv['candidate']!r}")
        return "headers " + "; ".join(parts)
    if k == "body":
        if d.get("kind") == "json" and d.get("paths"):
            p = d["paths"][0]
            return f"body {p['path']}: {json.dumps(p['golden'])[:80]} -> {json.dumps(p['candidate'])[:80]}"
        if d.get("kind") == "text":
            return f"body line {d.get('line')}: {d.get('golden','')[:80]!r} -> {d.get('candidate','')[:80]!r}"
        return "body shape differs"
    if k == "effects.stderr":
        return f"stderr line {d.get('line')}: {d.get('golden','')[:80]!r} -> {d.get('candidate','')[:80]!r}"
    if k.startswith("effects."):
        ps = d.get("paths") or []
        if ps:
            p = ps[0]
            return f"{k} {p['path']}: {json.dumps(p['golden'])[:60]} -> {json.dumps(p['candidate'])[:60]}"
        return k
    if k == "norm.rules":
        return f"norm.rules only_golden={d['only_golden']} only_candidate={d['only_candidate']}"
    return k


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cells", default=os.path.join(DATA, "cells.json"),
                    help="the product's cell list. Default: $BUSBAR_ORACLE_DATA/cells.json")
    ap.add_argument("--family", default="")
    ap.add_argument("--id-filter", default="",
                    help="regex over cell IDs (the same domain as record.sh --filter); only matching cells are owed and compared")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when any owed cell diverges without an accepted entry or is missing from the candidate")
    ap.add_argument("--accepted", default=os.path.join(DATA, "accepted-differences.json"),
                    help="the product's register of forgiven divergences. "
                         "Default: $BUSBAR_ORACLE_DATA/accepted-differences.json")
    ap.add_argument("--refuse-extra-candidate", action="store_true",
                    help="make `extra.candidate` rows FAIL instead of PASS. A cell the candidate "
                         "recorded that the golden does not owe is not a divergence — nothing was "
                         "compared — so it is VISIBLE by default and red only when a caller says so")
    ap.add_argument("--allow-harness-skew", action="store_true",
                     help="proceed even if golden and candidate were produced by different (or unrecorded) "
                          "testing/shadow-oracle revisions; without this the differ refuses to compare them")
    a = ap.parse_args()

    def meta(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}

    gmeta = meta(os.path.join(a.golden, "meta.json"))
    cmeta = meta(os.path.join(a.candidate, "meta.json"))
    grev, crev = gmeta.get("harness_rev"), cmeta.get("harness_rev")
    grevs, crevs = provenance_revs(gmeta), provenance_revs(cmeta)
    # A diff only means "this is busbar's behavior" if the same recorder, normalizer and cell set
    # produced both sides. Either side missing its provenance is exactly as unproven as the two
    # sides disagreeing — a golden with no harness_rev cannot be trusted to match anything.
    #
    # AND `harness_rev` ALONE IS NOT THE PROVENANCE. It is ONE SCALAR that three shipped writers
    # overwrite: merge-recordings.py stamps the rev of the part recorded LAST and pushes the others
    # onto `harness_rev_history`; renormalize.sh re-stamps it and does the same; a re-stamp by hand
    # leaves a note and nothing else. So a golden whose 913 cells were recorded under A and whose 2
    # re-recorded cells were made under B carries `harness_rev: B` — and a candidate recorded today
    # under B satisfied `grev == crev` exactly, this guard said nothing, and the differ compared 913
    # cells whose normalization predates the change against a candidate that postdates it. That is
    # the situation this refusal's own message describes, reached by laundering the field upstream
    # of the flag that is supposed to authorise it. The provenance is therefore the WHOLE SET —
    # harness_rev, harness_rev_history, merged_from[].harness_rev and harness_rev_recorded — and the
    # two sides must name the same one. See provenance_revs().
    if grev is None or crev is None or grevs != crevs:
        if not a.allow_harness_skew:
            if grev is None or crev is None:
                why = f"golden harness_rev={grev!r} candidate harness_rev={crev!r} (one or both meta.json predate this field)"
            elif grev != crev:
                why = f"golden harness_rev={grev} candidate harness_rev={crev}"
            else:
                why = (f"golden and candidate both stamp harness_rev={grev}, but the recordings were "
                       f"not made under the same set of harness revisions: golden "
                       f"{sorted(grevs)} vs candidate {sorted(crevs)} (from harness_rev, "
                       f"harness_rev_history, harness_rev_recorded and merged_from[].harness_rev — "
                       f"a merged or re-normalized recording keeps the revs its cells really came "
                       f"from, and one scalar cannot speak for all of them)")
            sys.stderr.write(
                "diff-cells: refusing to compare — golden and candidate were not proven to come from the "
                f"same shadow-oracle harness revision ({why}). A diff between them may be explained by a "
                "change to normalize.py/capture.py/cells.json/etc, not by busbar's behavior. Re-record both "
                "with the current testing/shadow-oracle tree, or pass --allow-harness-skew to compare anyway.\n")
            return 2
    # THE CORPUS IS READ BEFORE THE REGISTER, because the register's width guard is measured against
    # it: an entry's `cells` regex is a claim about HOW MANY cells it forgives, and that claim can
    # only be checked against the full cell list (never the --family/--id-filter subset, or a
    # narrower run would make a wide entry look narrow).
    with open(a.cells, encoding="utf-8") as f:
        cells_doc = json.load(f)
    all_cell_ids = [c["id"] for c in cells_doc["cells"]]
    check_compare_policy(cells_doc["cells"], a.cells)

    accepted, transforms, additive = [], [], []
    if os.path.exists(a.accepted):
        for e in json.load(open(a.accepted, encoding="utf-8")).get("accepted", []):
            base = {"rx": re.compile(e.get("cells", ".")), "classes": set(e.get("classes", [])), "kind": e.get("kind", "improvement"),
                    "id": e.get("id", e.get("cells", "?")), "rationale": e.get("rationale", ""), "by": e.get("by", "")}
            # The register may never quietly forgive a status or a billing figure: only a `breaking` entry
            # that names its CHANGELOG line may accept those classes, and no entry may be a total blanket.
            # Tested on the EFFECTIVE set (what the entry will forgive), never on `classes` alone (what
            # it says) — an entry that omits `classes` forgives a whole default set, and testing the
            # empty literal let a `breaking` entry with no `classes` and no changelog take every money
            # class there is. See allowed_classes().
            base["allowed"] = allowed_classes(base["kind"], base["classes"])
            # Whether this entry is entitled to forgive a MONEY class at all — the same test the
            # loader applies below, kept on the entry so the per-cell matcher can apply it against
            # the classes THIS cell rates 10 (see rated_weight/money_at_cell).
            base["money_ok"] = base["kind"] == "breaking" and bool(e.get("changelog"))
            if "missing.golden" in base["classes"]:
                sys.exit(f"accepted-differences: entry {base['id']!r} accepts 'missing.golden' — a golden ledger row that says PASS for a cell the golden did not write is a recorder bug, never an acceptable difference; re-record the golden")
            money = base["allowed"] & MONEY_CLASSES
            # `additive`'s ONE exception to "only `breaking` may touch money": `new_route`, and only
            # for `status`, and only with its own changelog line. A route that did not exist in
            # 1.5.5 (golden 404, the 1.5.5 not-found envelope) now answering for real is not a
            # behavioural CHANGE to declare `breaking` over — there is no previous behavior to
            # compare the new one against — but it is still money (a status code), so it still needs
            # a changelog line naming it, exactly as `breaking` would.
            new_route_money_ok = (base["kind"] == "additive" and bool(e.get("new_route"))
                                  and money <= ADDITIVE_STATUS_CLASSES and bool(e.get("changelog")))
            if money and not (base["kind"] == "breaking" and e.get("changelog")) and not new_route_money_ok:
                sys.exit(f"accepted-differences: entry {base['id']!r} accepts {sorted(money)} but is not kind=breaking with a changelog line")
            # `additive` IS NEVER A BLANK CHEQUE FOR MONEY, `breaking`'S CHANGELOG LINE DOES NOT
            # EXTEND TO IT. It is defined for exactly two classes — `body` and `headers` — and ONLY
            # when the tool itself proves the candidate a superset of the golden at every path the
            # golden names (see additive_superset()); status, usage, readback and every missing.*
            # class are refused here regardless of `classes`, because there is no superset relation
            # for "the request succeeded" or "the money moved" — those are equal or they are not.
            if base["kind"] == "additive":
                if "transform" in e:
                    sys.exit(f"accepted-differences: entry {base['id']!r} is kind=additive and also carries a "
                             f"`transform` — additive proves growth by inspecting the recorded pair, never by "
                             f"rewriting it first. Use one register kind or the other.")
                extra = base["allowed"] - ADDITIVE_CLASSES
                if extra:
                    sys.exit(f"accepted-differences: entry {base['id']!r} is kind=additive but names {sorted(extra)} — "
                             f"additive may only ever take {sorted(ADDITIVE_CLASSES)}. There is no superset relation "
                             f"for a status code, a usage figure, a readback or a missing cell: those are either "
                             f"equal or they are a real divergence.")
                if not e.get("changelog"):
                    sys.exit(f"accepted-differences: entry {base['id']!r} is kind=additive but carries no `changelog` "
                             f"line — additive proves growth mechanically, but the growth itself is still a product "
                             f"change an owner must document, exactly as `breaking` requires.")
                # `effects.stderr` (and a text `body`, under this flag) has NO JSON structure to walk
                # — it is a raw string, and the only growth proof this file knows for a raw string is
                # `text_list_growth`: one backtick-quoted comma list in the candidate holds golden's
                # items as a prefix, everything else byte-identical. Naming `effects.stderr` without
                # opting into that check would otherwise fall through to `body_as_json()`, which
                # would just report "not JSON on both sides" for every cell forever — a silent no-op
                # acceptance that never fires is worse than a refusal at load.
                if "effects.stderr" in base["allowed"] and not e.get("text_list_growth"):
                    sys.exit(f"accepted-differences: entry {base['id']!r} is kind=additive and names "
                             f"'effects.stderr', which has no JSON-superset relation. Set "
                             f"`text_list_growth: true` to use the backtick-list-growth proof instead.")
                # `status` is refused unless `new_route: true` is set — there is no superset relation
                # for a status code (it is equal or it is a real divergence) OUTSIDE the one
                # carve-out `new_route` names: a route 1.5.5 did not have (golden 404, the 1.5.5
                # not-found envelope) now answering for real.
                if "status" in base["allowed"] and not e.get("new_route"):
                    sys.exit(f"accepted-differences: entry {base['id']!r} is kind=additive and names "
                             f"'status', which additive may only take when `new_route: true` is also "
                             f"set (a route that did not exist in 1.5.5 now answers).")
                # `description_corrections` NAMES A REAL STRING, NEVER A HYPOTHETICAL ONE. A pointer
                # that does not resolve to a string leaf in ANY golden cell this entry matches is
                # refused here — a typo'd path would otherwise silently forgive nothing (the leaf it
                # meant to correct stays uncovered and the cell stays red for an unrelated reason,
                # which is a confusing way to fail) or, worse, later resolve against a DIFFERENT
                # field a future recording happens to add at that path. Checked against every
                # matching cell that actually has a golden recording; a cell this entry's `cells`
                # regex matches but the golden never recorded is not evidence either way.
                dc = e.get("description_corrections") or []
                if dc:
                    if not isinstance(dc, list) or not all(isinstance(p, str) for p in dc):
                        sys.exit(f"accepted-differences: entry {base['id']!r}'s description_corrections "
                                 f"must be a list of JSON-pointer strings")
                    matched = [cid for cid in all_cell_ids if base["rx"].search(cid)]
                    for ptr in dc:
                        checked_any, is_string = False, False
                        for cid in matched:
                            gp = os.path.join(a.golden, "cells", safe_name(cid) + ".json")
                            if not os.path.exists(gp):
                                continue
                            try:
                                gcell = json.load(open(gp, encoding="utf-8"))
                            except Exception:
                                continue
                            gj, gok = body_as_json(gcell.get("body"))
                            if not gok:
                                continue
                            checked_any = True
                            found, val = resolve_json_pointer(gj, ptr)
                            if found and isinstance(val, str):
                                is_string = True
                                break
                        if checked_any and not is_string:
                            sys.exit(f"accepted-differences: entry {base['id']!r}'s description_corrections "
                                     f"names {ptr!r}, which is not a string leaf in the golden body of any "
                                     f"matching cell.")
            if "cells" not in e and not base["classes"] and "transform" not in e:
                sys.exit(f"accepted-differences: entry {base['id']!r} has neither cells nor classes (a total blanket)")
            # A TRANSFORM IS NOT EXEMPT FROM HAVING A SCOPE. `cells` defaulted to "." for every entry,
            # and the two `transform` entries that carry no `cells` (D-1's diagnostic codes, D-2's
            # jemalloc line) therefore ran their rewrites over all 2299 cells. That was defended as
            # harmless because a transform is line-precise — it only fires where its regex matches —
            # but the match itself is not the acceptance: what a fired transform does is hand the
            # cell's WHOLE raw class list to the accepted column, and until the next guard below that
            # list was never checked against the entry's `allowed` set. A cell where D-1's stderr
            # rewrite happened to fire and whose STATUS had also moved reported
            # `PASS ACCEPTED improvement (D-1 …): status` — a money class forgiven by an entry that
            # is not `breaking` and names no changelog line for it, on a cell D-1 never claimed.
            if "transform" in e and not e.get("cells"):
                sys.exit(f"accepted-differences: transform entry {base['id']!r} declares no `cells`. A rewrite with no "
                         f"scope runs over the whole corpus, and a cell it fires on has every one of its divergences "
                         f"handed to this entry. Name the cells it is about.")
            # ── AN ENTRY DECLARES ITS OWN WIDTH, AND MAY NEVER EXCEED IT ─────────────────────────
            # A `cells` regex is prose that runs. `^llm\|[a-z_]+\|cohere\|request\|ok(_stream)?$` is
            # a scope an owner can read; the same string with `|^billing\|` glued on the end reads
            # almost identically and forgives twelve more cells in a different family — which is
            # exactly what M-3 did: an entry whose id, rationale and changelog line all speak only
            # about Cohere `billed_units` held effects.usage (MONEY) over every billing cell in the
            # corpus. Nothing refused it, because the money guard asks WHICH classes an entry takes
            # and never HOW MANY cells it takes them over.
            #
            # So the entry must say the number out loud. `expected_cells` is the count the author
            # saw when they wrote the regex; matching MORE than that is refused. An alternation
            # bolted onto a live entry, or a new family whose ids happen to fall under an old
            # pattern, now stops the run and names the drift instead of widening in silence.
            # Matching FEWER is not refused (a cell can be renamed away or not yet recorded) but is
            # reported, so a stale entry is visible rather than merely harmless.
            #
            # `transform` entries are in this guard too: they now carry a required `cells`, so their
            # scope is a claim like any other and is held to the same number.
            n_match = sum(1 for cid in all_cell_ids if base["rx"].search(cid))
            exp = e.get("expected_cells")
            if not isinstance(exp, int) or isinstance(exp, bool) or exp < 0:
                sys.exit(f"accepted-differences: entry {base['id']!r} declares no `expected_cells` "
                         f"(a non-negative integer: how many cells in {os.path.basename(a.cells)} its `cells` regex "
                         f"is meant to cover). It currently matches {n_match}. Without it the regex can widen silently.")
            if n_match > exp:
                over = [cid for cid in all_cell_ids if base["rx"].search(cid)][:12]
                sys.exit(f"accepted-differences: entry {base['id']!r} declares expected_cells={exp} but its `cells` "
                         f"regex matches {n_match} cells — the waiver is wider than the entry says it is. "
                         f"Narrow the regex, or raise expected_cells DELIBERATELY and say why in the rationale. "
                         f"Matched (first {len(over)}): {', '.join(over)}")
            if n_match < exp:
                sys.stderr.write(f"accepted-differences: note: entry {base['id']!r} declares expected_cells={exp} "
                                 f"but matches {n_match} — cells renamed away, or not recorded yet.\n")
            if "transform" in e:
                # a LINE-PRECISE acceptance: the candidate's text is rewritten by these regexes before the
                # diff, so ONLY the accepted token (a diagnostic code, a renamed line) is forgiven and any
                # other change on the same line / cell still shows. Fires visibly: an identical-after-rewrite
                # cell reports ACCEPTED with this id, never PASS.
                for rx, _repl in e["transform"]["candidate"]:
                    why = transform_pattern_too_broad(rx)
                    if why:
                        sys.exit(f"accepted-differences: entry {base['id']!r} transform pattern {rx!r} is refused: "
                                 f"{why}. A transform is credited for a class the moment the rewritten pair is "
                                 f"byte-identical, so its pattern must name a specific token, never a shape wide "
                                 f"enough to swallow arbitrary content.")
                base["transform"] = [(re.compile(rx, re.M), repl) for rx, repl in e["transform"]["candidate"]]
                transforms.append(base)
            elif base["kind"] == "additive":
                # JSON-pointer-style paths (`/pools/0/name`) where a golden `null` is allowed to grow
                # into a real value. Not a default: `null` staying `null` is a claim the field is
                # genuinely absent, and letting it grow ANYWHERE for free would let additive quietly
                # cover a value that was never populated instead of one that is provably unchanged.
                base["null_to_value"] = set(e.get("null_to_value", []))
                base["text_list_growth"] = bool(e.get("text_list_growth", False))
                base["description_corrections"] = set(e.get("description_corrections", []) or [])
                base["new_route"] = bool(e.get("new_route", False))
                additive.append(base)
            else:
                accepted.append(base)
    # ── WHICH ENTRIES CAN NO LONGER FORGIVE WHAT THEY NAME ──────────────────────────────────────
    # Said at LOAD, over the whole corpus, so the register's owners learn it from a run rather than
    # from a cell going red months later. Not fatal: nothing has diverged yet, and refusing to run
    # would make the differ unusable against a register that is merely stale. The refusal itself
    # happens per cell, where the question is real (see entry_may_take).
    fam_by_id = {c["id"]: (c.get("family") or c.get("plane", "unknown")) for c in cells_doc["cells"]}
    narrowed_entries = []
    for e in accepted + transforms:
        if e["money_ok"]:
            continue
        lost = set()
        for cid, fam in fam_by_id.items():
            if e["rx"].search(cid):
                lost |= (e["allowed"] & money_at_cell(fam)) - MONEY_CLASSES
        if lost:
            narrowed_entries.append((e["id"], sorted(lost)))
    if narrowed_entries:
        sys.stderr.write(
            "accepted-differences: the following entries name class(es) that are rated 10 on some "
            "cell they match (a family where the BODY is the contract), and are not kind=breaking "
            "with a changelog line. Those classes are NOT forgiven on those cells:\n")
        for eid, lost in narrowed_entries:
            sys.stderr.write(f"  - {eid!r}: {', '.join(lost)}\n")

    os.makedirs(a.out, exist_ok=True)

    fam_rx = re.compile(a.family) if a.family else None
    cells = [c for c in cells_doc["cells"] if not fam_rx or fam_rx.search(c.get("family", c.get("plane", "")))]
    id_rx = re.compile(a.id_filter) if a.id_filter else None
    cells = [c for c in cells if not id_rx or id_rx.search(c["id"])]
    by_id = {c["id"]: c for c in cells}
    gl, cl = load_ledger(a.golden), load_ledger(a.candidate)

    def family_of(c):  # legacy llm cells carry no `family`; treat plane as the family
        return c.get("family") or c.get("plane", "unknown")

    owed, gaps = [], []
    for c in cells:
        st = gl.get(c["id"], ("MISSING", "no golden ledger row"))
        if st[0] == "PASS":
            owed.append(c["id"])
        else:
            gaps.append((c["id"], st[0], st[1]))

    results, fam_stats, class_counts = [], defaultdict(lambda: Counter()), Counter()
    W = D = 0
    for cid in owed:
        c = by_id[cid]
        fam = family_of(c)
        g = load_cell(a.golden, cid)
        cc = load_cell(a.candidate, cid)
        pre_acc = None
        if g is None:
            classes, detail = ["missing.golden"], {"missing.golden": "golden ledger PASS but cell file absent"}
        elif cc is None:
            classes, detail = ["missing.candidate"], {"missing.candidate": cl.get(cid, ("MISSING", "no candidate ledger row"))[1]}
        else:
            fired = []
            if transforms and isinstance(cc, dict) and "__corrupt__" not in cc:
                # THE TRANSFORM IS A NORMALIZATION, NOT A ONE-SIDED CANDIDATE REWRITE. Applying it
                # only to `cc` assumes the golden never carries the pattern being stripped — true
                # for a real 1.5.5-golden vs 1.6.0-candidate diff (a diagnostic code or a new
                # metering series exists only on the candidate side there), but false the moment the
                # golden is itself 1.6.0-shaped: e.g. two independent recordings of the SAME 1.6.0
                # binary taken to prove determinism, or a candidate-vs-candidate A/B. There the
                # golden line keeps the pattern while the transformed candidate loses it, so an
                # already-identical pair reports a phantom body/effects.stderr divergence. `g_t` is
                # rewritten by the same rules so the transform applies uniformly: when the golden
                # lacks the pattern (the real 1.5.5-vs-1.6.0 case) `g_t == g` and this is a no-op, so
                # it only ever REMOVES a difference, never manufactures one.
                g_t = json.loads(json.dumps(g)) if isinstance(g, dict) else g
                cc_t = json.loads(json.dumps(cc))
                body_changed = False
                for t in transforms:
                    if not t["rx"].search(cid):
                        continue
                    hit = False
                    if isinstance(g_t, dict):
                        geff = g_t.get("effects", {})
                        if isinstance(geff.get("stderr"), str):
                            for rx, repl in t["transform"]:
                                geff["stderr"] = rx.sub(repl, geff["stderr"])
                        gbody = g_t.get("body")
                        if isinstance(gbody, dict) and isinstance(gbody.get("text"), str):
                            for rx, repl in t["transform"]:
                                gbody["text"] = rx.sub(repl, gbody["text"])
                    eff = cc_t.get("effects", {})
                    if isinstance(eff.get("stderr"), str):
                        for rx, repl in t["transform"]:
                            new = rx.sub(repl, eff["stderr"])
                            if new != eff["stderr"]:
                                hit = True; eff["stderr"] = new
                    body = cc_t.get("body")
                    if isinstance(body, dict) and isinstance(body.get("text"), str):
                        for rx, repl in t["transform"]:
                            new = rx.sub(repl, body["text"])
                            if new != body["text"]:
                                hit = True; body_changed = True; body["text"] = new
                    if hit:
                        fired.append(t)
                if fired:
                    if body_changed:
                        # a rewritten body cannot keep 1.5.5's byte length; the length header is the
                        # accepted change's shadow, not a second divergence. It has to come off ALL
                        # FOUR sides: `cc` (the untransformed candidate) is what classes_raw is
                        # computed from, and those are the classes the row REPORTS and the classes
                        # the accepted-entry match is tested against — leaving content-length on cc
                        # alone gave every accepted-transform row a phantom `headers` class, which
                        # both mis-described the row and made a correctly narrow `classes: [body]`
                        # entry fail to match.
                        for side in (g, g_t, cc, cc_t):
                            side.get("headers", {}).pop("content-length", None)
                    classes_raw, detail_raw = compare(g, cc)
                    classes, detail = compare(g_t, cc_t)
                    # THE FIRED ENTRIES' `allowed` SET DECIDES, NOT THE FACT THAT THEY FIRED. A
                    # transform firing on a cell only means its regex matched some text there; it
                    # says nothing about the OTHER classes that cell diverged in. This branch handed
                    # `classes_raw` — every class the untransformed candidate differed in — straight
                    # to the accepted column on the strength of that match, so a cell where D-1's
                    # `[error] BUSBAR-1234: ` rewrite fired AND whose status had moved 200 -> 500
                    # reported PASS/ACCEPTED naming `status`, a MONEY class, under an `improvement`
                    # entry that could never have been allowed to name it in `classes`. Every other
                    # path through this loop tests the divergence against `allowed`; this one did not.
                    # Now it does, jointly across the entries that fired (same rule as the non-
                    # transform matcher below): anything left over stays a divergence and is red.
                    if not classes and classes_raw:
                        # `entry["allowed"]` decides, NOT `entry_may_take`'s FAMILY money subtraction.
                        # This branch only runs when the TRANSFORMED pair is fully equal — `classes`
                        # (compare(g_t, cc_t)) is empty — and a transform touches exactly two fields,
                        # `effects.stderr` and `body.text` (plus the content-length shadow of the
                        # latter). Every other field of `g_t`/`cc_t` is a verbatim copy of `g`/`cc`,
                        # so compare() computed the SAME value for every class outside
                        # {headers, body, effects.stderr} whether or not the transform ran. Full
                        # equality post-transform therefore PROVES `classes_raw` cannot contain a
                        # class the transform did not touch — `status`, `effects.usage`,
                        # `effects.files`, and every other real money field would still show up in
                        # `classes` (non-empty) if they had moved, and this branch would never run.
                        # `money_at_cell()` exists to stop an `improvement` entry from being credited
                        # for a class that is STILL DIFFERENT (the boot-refusal-stdout hole this file
                        # was written against): that hole needs the family subtraction because the
                        # content in question never became equal. Here it did, by construction of
                        # what a transform is allowed to touch, so the ordinary global money guard —
                        # already enforced at load, where an entry's `allowed` can never contain a
                        # class in MONEY_CLASSES without `kind: breaking` and a changelog line — is
                        # the whole of what's needed. Subtracting the family-rated set on top of that
                        # (as before) refused a diagnostic-code-stripping `improvement` transform
                        # credit for `body` on every BODY_IS_CONTRACT family (boot.warning,
                        # boot.refusal, cli, config.migrate, admin.ops, ops.scrape) even though the
                        # rewritten pair is byte-identical — nothing was forgiven, because nothing
                        # was left to forgive.
                        cover_t = set().union(*(t["allowed"] for t in fired))
                        unclaimed = [k for k in classes_raw if k not in cover_t]
                        if not unclaimed:
                            classes, detail = classes_raw, {"accepted.transform": [t["id"] for t in fired]}
                            pre_acc = fired[0]
                        else:
                            # keep the REAL detail for the classes that are still divergent, so the
                            # row reads as the divergence it is; the unclaimed list is named in the
                            # message the caller sees on the ledger row.
                            classes, detail = classes_raw, {k: v for k, v in detail_raw.items() if k in unclaimed}
                else:
                    classes, detail = compare(g, cc)
            else:
                classes, detail = compare(g, cc)
            only = c.get("compare")  # a cell with inherently random output names the classes that ARE its contract
            if only:
                classes = [k for k in classes if k in only or k.startswith("missing.")]
                detail = {k: v for k, v in detail.items() if k in classes}
        # owner-accepted differences: the cell reports ACCEPTED (its own column), never a silent pass
        acc = pre_acc if classes and detail.get("accepted.transform") else None
        if classes and acc is None:
            # A cell can sit at the intersection of TWO separately-named improvements: F-011 names the
            # additive hook VIEW (body/headers) and F-011r names the readback's `at` -> `fires_at`
            # (effects.readback), and admin.ops|PostHooks|ok carries both at once. Demanding that ONE
            # entry cover every class made that cell red with no honest way to express it — the only
            # escape was to widen a correctly-narrow entry onto classes it does not own, which is
            # exactly the blanket this register exists to prevent. So the entries that MATCH THIS CELL
            # are allowed to cover the classes JOINTLY: every class must still be named by some entry
            # with an owner and a rationale (and the per-entry money guard above is untouched, since a
            # money class can only ever be taken by a `breaking` entry that names its changelog line).
            # The row then reports every entry that contributed, never just the first.
            need, cover = set(classes), []
            money_here = money_at_cell(fam)
            for e in accepted:
                if not e["rx"].search(cid):
                    continue
                take = need & entry_may_take(e, money_here)
                if not take:
                    continue
                cover.append(e); need -= take
                if not need:
                    break
            # `additive` CLAIMS A CLASS BY PROVING IT, NEVER BY DECLARING IT. Unlike every other
            # entry above, matching this cell is not enough: `body`/`headers` only leave `need` when
            # additive_superset()/additive_headers_superset() find the candidate a superset of the
            # golden at every path the golden defines. A failed proof leaves the class in `need` (the
            # cell stays red) and records WHERE it failed, so the row never has to guess whether
            # "additive" fired or simply matched.
            additive_note = None
            additive_removed = []
            if need & ADDITIVE_CLASSES:
                for e in additive:
                    if not e["rx"].search(cid):
                        continue
                    want = need & e["allowed"]
                    if not want:
                        continue
                    claimed, body_ok = set(), False
                    # `new_route` IS ITS OWN GATE, CHECKED BEFORE ANY ORDINARY CLASS LOGIC — a route
                    # that did not exist in 1.5.5 (golden 404, the 1.5.5 not-found envelope) now
                    # answering for real bears no relation to the stub it replaced: not a superset,
                    # not growth, not a correction. When the gate holds, whatever of
                    # {status, headers, body} this entry is asked for is claimed WHOLESALE and taken
                    # out of `want` before the ordinary per-class checks below ever see it. When it
                    # does not hold — golden status is not 404, the golden body is not the stub
                    # shape, or the candidate is 5xx — `want` is untouched and the ordinary rules
                    # decide, exactly as if `new_route` had not been declared.
                    if e["new_route"]:
                        gj0, gok0 = body_as_json(g.get("body"))
                        cstatus = cc.get("status")
                        is_5xx = isinstance(cstatus, int) and not isinstance(cstatus, bool) and 500 <= cstatus < 600
                        if g.get("status") == 404 and gok0 and is_1_5_5_not_found_envelope(gj0) and not is_5xx:
                            new_claim = want & (ADDITIVE_STATUS_CLASSES | {"headers", "body"})
                            if new_claim:
                                claimed |= new_claim
                                body_ok = "body" in new_claim
                                additive_removed.append({"entry": e["id"], "class": "new_route",
                                                         "note": f"new route: golden 404 -> candidate {cstatus}"})
                                want = want - new_claim
                    if "body" in want:
                        gtext = g.get("body", {}).get("text") if isinstance(g.get("body"), dict) else None
                        ctext = cc.get("body", {}).get("text") if isinstance(cc.get("body"), dict) else None
                        if e["text_list_growth"] and isinstance(gtext, str) and isinstance(ctext, str):
                            # a plain-text body (SSE, CLI stdout): the whole body IS the one string
                            # to prove growth on, same as effects.stderr below.
                            removed, note = text_list_growth_check(gtext, ctext)
                            if removed is not None:
                                claimed.add("body"); body_ok = True
                                if removed:
                                    additive_removed.append({"entry": e["id"], "class": "body", "removed": removed})
                            else:
                                additive_note = note
                        else:
                            gj, gok = body_as_json(g.get("body"))
                            cj, cok = body_as_json(cc.get("body"))
                            if not (gok and cok):
                                additive_note = ("additive: body is not JSON on both sides" if not e["text_list_growth"]
                                                 else "additive: body has no text or JSON on both sides for text_list_growth")
                            else:
                                # `string_diffs` (only under `text_list_growth`) defers a STRING
                                # leaf mismatch instead of failing on it immediately, so an
                                # error-message field that grew its own backtick list can still pass
                                # — but only when it is the ONLY leaf that differs; two differing
                                # leaves is not "one list grew" under either leaf's own story.
                                # `description_corrections` forgives a NAMED leaf's mismatch
                                # outright (checked first inside additive_superset, so a corrected
                                # leaf never competes for text_list_growth's one slot).
                                string_diffs = [] if e["text_list_growth"] else None
                                corrections_report = []
                                bad = additive_superset(gj, cj, "", e["null_to_value"], string_diffs,
                                                        e["description_corrections"], corrections_report)
                                if bad is not None:
                                    additive_note = f"additive: not a superset at {bad}"
                                elif string_diffs and len(string_diffs) > 1:
                                    paths = ", ".join(p for p, _, _ in string_diffs)
                                    additive_note = f"additive: text_list_growth found more than one differing string leaf: {paths}"
                                elif string_diffs:
                                    leaf_path, gstr, cstr = string_diffs[0]
                                    removed, note = text_list_growth_check(gstr, cstr)
                                    if removed is not None:
                                        claimed.add("body"); body_ok = True
                                        if removed:
                                            additive_removed.append({"entry": e["id"], "class": "body",
                                                                     "path": leaf_path, "removed": removed})
                                        if corrections_report:
                                            additive_removed.append({"entry": e["id"], "class": "body",
                                                                     "corrections": corrections_report})
                                    else:
                                        additive_note = f"additive: not a superset at {leaf_path} ({_sans_additive_prefix(note)})"
                                else:
                                    claimed.add("body"); body_ok = True
                                    if corrections_report:
                                        additive_removed.append({"entry": e["id"], "class": "body",
                                                                 "corrections": corrections_report})
                    if "headers" in want:
                        bad = additive_headers_superset(g.get("headers", {}), cc.get("headers", {}), body_ok)
                        if bad is None:
                            claimed.add("headers")
                        else:
                            additive_note = f"additive: not a superset at header {bad!r}"
                    if "effects.stderr" in want:
                        gtext = (g.get("effects") or {}).get("stderr")
                        ctext = (cc.get("effects") or {}).get("stderr")
                        if isinstance(gtext, str) and isinstance(ctext, str):
                            removed, note = text_list_growth_check(gtext, ctext)
                            if removed is not None:
                                claimed.add("effects.stderr")
                                if removed:
                                    additive_removed.append({"entry": e["id"], "class": "effects.stderr", "removed": removed})
                            else:
                                additive_note = note
                        else:
                            additive_note = "additive: effects.stderr missing on one side"
                    if claimed:
                        cover.append(e); need -= claimed
                    if not need:
                        break
            if cover and not need:
                acc = cover[0] if len(cover) == 1 else {
                    "id": " + ".join(e["id"] for e in cover),
                    "kind": "breaking" if any(e["kind"] == "breaking" for e in cover) else cover[0]["kind"],
                    "rationale": " | ".join(e["rationale"] for e in cover),
                    "by": ", ".join(dict.fromkeys(e["by"] for e in cover)),
                }
                if additive_removed:
                    # THE ROW SAYS WHAT GREW, NOT JUST THAT SOMETHING DID. additive proves a
                    # superset mechanically; the items it proved were new are exactly the fact an
                    # owner reading the ledger needs, so they ride on the row rather than living
                    # only in the register entry's rationale.
                    detail = {**detail, "additive.removed": additive_removed}
            elif additive_note:
                # THE CELL STAYS RED, BUT NOT SILENTLY: an additive entry matched and was tried, and
                # this is exactly where it stopped being a superset. Attached rather than replacing
                # `detail[k]` so the real diff (the golden/candidate values at the failing path) is
                # still there for whoever opens report.json; only the ROW's headline message changes.
                detail = {**detail, "additive.rejected": additive_note}
        # ONE AUTHORITY FOR WHAT A DIVERGENCE IS WORTH. This was three expressions that had to agree
        # with MONEY_CLASSES by hand and did not; rated_weight() is now the only place that knows.
        rated = max([rated_weight(fam, k) for k in classes] or [0])
        wt = c.get("weight")
        if wt is None:
            wt = rated if classes else (10 if fam in BODY_IS_CONTRACT else 0)
        # A DECLARED WEIGHT MAY NOT UNDERCUT A MONEY CLASS. `cell_w` took the cell's own `weight` in
        # preference to its classes', so `"weight": 1` on a cell would have capped a weight-10
        # `status` divergence's contribution to the D/W ratio at 1 — a money divergence priced as a
        # cosmetic one. Dormant in busbar's corpus (all 22 cells that declare a weight declare 10),
        # but nothing refused a low weight on a money class, so the floor is the rating.
        cell_w = max(wt, rated, 1) if classes else 0
        # `or` made a declared `"weight": 0` silently become 10, and both arms of the old
        # conditional were 10 — so the D/W "ratio" was a cell count. Kept at 10 (every owed cell
        # weighs the same today), said once, with a declared weight honoured only when it is a
        # usable positive integer.
        decl = c.get("weight")
        owed_w = decl if isinstance(decl, int) and not isinstance(decl, bool) and decl > 0 else 10
        W += owed_w
        fam_stats[fam]["owed"] += 1
        fam_stats[fam]["owed_w"] += owed_w
        if classes and acc is None:
            D += min(cell_w, owed_w)
            fam_stats[fam]["diverging"] += 1
            fam_stats[fam]["div_w"] += min(cell_w, owed_w)
            for k in classes:
                class_counts[k] += 1
        elif classes:
            fam_stats[fam]["accepted"] += 1
        results.append({"id": cid, "family": fam, "plane": c.get("plane"), "weight": owed_w,
                        "classes": classes, "first_diff": first_diff_text(classes, detail), "detail": detail if classes else {},
                        # A narrowed cell says so ON ITS OWN ROW. `PASS  identical` on a cell that
                        # compared four of sixteen classes is a true sentence that reads as a
                        # different, larger one; the reader of the ledger is the person who has to
                        # know the row is narrow.
                        **({"narrowed": compare_narrowed(c["compare"])} if c.get("compare") else {}),
                        # A host-capability rule that fired on one side only is stated on the row even
                        # when the cell is green — that is the whole point of it having its own
                        # category rather than sitting in the norm.rules exemption unremarked.
                        **({"platform": _skew} if (_skew := host_rule_skew(g, cc)) else {}),
                        **({"accepted": {"id": acc["id"], "kind": acc["kind"], "rationale": acc["rationale"], "by": acc["by"]}} if acc else {})})

    # ── WHAT THE CANDIDATE RECORDED THAT NOTHING HERE COMPARED ──────────────────────────────────
    # Every loop above walks the OWED set, which is derived from the golden. A cell file the
    # candidate wrote that the golden does not owe is therefore invisible to all of it: no row, no
    # class, no line anywhere. Three real things wear that shape and all three matter —
    #
    #   * a cell that exists in cells.json but which the GOLDEN could not record (a named gap). The
    #     candidate can record it fine, which is precisely the evidence that the gap has closed, and
    #     the gap register is where that belongs. Today it is simply not mentioned.
    #   * a cell id that is in NEITHER the golden's owed set nor cells.json — a RENAME. The old id
    #     leaves the corpus (see the owed-baseline check in replay.sh) and the new one lands here.
    #     Reporting only the disappearance names half of a rename and makes it look like a deletion.
    #   * a stale file from an EARLIER recording into the same --out (record.sh does not clear the
    #     directory per run for cells it did not select), which is a candidate carrying cells no
    #     binary in this run produced.
    #
    # It is its own class and NOT a member of CLASS_ORDER: CLASS_ORDER names ways an owed cell can
    # DIVERGE, each with a weight, a money rating and a `compare` policy, and nothing was compared
    # here at all. So it is reported — on its own row, in its own file, in report.json and report.md
    # — and it is not red unless a caller asks (--refuse-extra-candidate). Silence was the bug; red
    # would be a false one, because a candidate recording more than the golden owes is usually the
    # gate's own coverage growing.
    extra = []
    cand_cells_dir = os.path.join(a.candidate, "cells")
    filtered = bool(fam_rx or id_rx)
    if os.path.isdir(cand_cells_dir):
        safe_to_id = {safe_name(cid): cid for cid in all_cell_ids}
        owed_set, selected = set(owed), set(by_id)
        unknown_suppressed = 0
        for fn in sorted(os.listdir(cand_cells_dir)):
            if not fn.endswith(".json"):
                continue
            cid = safe_to_id.get(fn[:-5])
            if cid is None:
                # A file whose id is not in cells.json cannot be tested against --family/--id-filter
                # (it has no family and no row to match), so under a filtered run it is counted and
                # named in aggregate rather than reported as if the filter were not there.
                if filtered:
                    unknown_suppressed += 1
                    continue
                extra.append((fn[:-5], "no cell of this id is in cells.json — a rename, a removed "
                                       "cell, or a file left by an earlier recording into this --out"))
            elif cid in owed_set or cid not in selected:
                continue
            else:
                gs, gd = gl.get(cid, ("MISSING", "no golden ledger row"))
                extra.append((cid, f"the candidate recorded it; the golden does not owe it ({gs}: {gd})"))
        if unknown_suppressed:
            sys.stderr.write(f"diff-cells: {unknown_suppressed} candidate cell file(s) name ids that are "
                             f"not in {os.path.basename(a.cells)}; not reported because this run is "
                             f"filtered and they cannot be tested against the filter\n")

    fam_table = {}
    for fam, s in sorted(fam_stats.items()):
        fam_table[fam] = {"owed": s["owed"], "diverging": s["diverging"], "accepted": s["accepted"], "owed_w": s["owed_w"], "div_w": s["div_w"],
                          "ratio": (s["div_w"] / s["owed_w"]) if s["owed_w"] else 0.0}
    report = {
        "meta": {"golden": a.golden, "candidate": a.candidate, "golden_version": gmeta.get("version"),
                 "candidate_version": cmeta.get("version"), "cells_json": a.cells, "family_filter": a.family,
                 "golden_binary_sha256": gmeta.get("binary_sha256"), "candidate_binary_sha256": cmeta.get("binary_sha256"),
                 "golden_harness_rev": grev, "candidate_harness_rev": crev,
                 # The whole provenance, not just the scalar, so a reader of the report can see a
                 # mixed-rev recording without opening meta.json — and so `harness_skew_allowed`
                 # is true for a history skew exactly as it is for a scalar one.
                 "golden_harness_revs": sorted(grevs), "candidate_harness_revs": sorted(crevs),
                 "harness_skew_allowed": bool(a.allow_harness_skew and (grev is None or crev is None or grevs != crevs))},
        "totals": {"cells_in_scope": len(cells), "owed": len(owed), "gaps": len(gaps),
                   "diverging": sum(1 for r in results if r["classes"] and "accepted" not in r),
                   "accepted": sum(1 for r in results if "accepted" in r), "W": W, "D": D,
                   "ratio": (D / W) if W else 0.0, "extra_candidate": len(extra)},
        "by_family": fam_table, "by_class": dict(class_counts),
        "gaps": [{"id": i, "golden_status": s, "detail": d} for i, s, d in gaps],
        "extra_candidate": [{"id": i, "why": w} for i, w in extra],
        "cells": results,
    }
    with open(os.path.join(a.out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, sort_keys=True)
    with open(os.path.join(a.out, "owed.txt"), "w") as f:
        f.write("\n".join(owed) + ("\n" if owed else ""))
    with open(os.path.join(a.out, "owed-gaps.txt"), "w") as f:
        for i, s, d in gaps:
            f.write(f"{i}\t{s}\t{d}\n")
    with open(os.path.join(a.out, "extra-candidate.txt"), "w") as f:
        for i, w in extra:
            f.write(f"{i}\t{w}\n")
    # THE CORPUS AND THE SELECTION, AS FILES, because replay.sh's owed-baseline check cannot tell
    # "this id was dropped from cells.json" from "this run used --family and never looked at it"
    # without them — and that is the exact case the ratchet's own header names first. Both are
    # written on every run; a caller that ignores them is unchanged.
    with open(os.path.join(a.out, "corpus-ids.txt"), "w") as f:
        f.write("".join(cid + "\n" for cid in all_cell_ids))
    with open(os.path.join(a.out, "selected-ids.txt"), "w") as f:
        f.write("".join(c["id"] + "\n" for c in cells))
    with open(os.path.join(a.out, "diverging.txt"), "w") as f:
        for r in results:
            if r["classes"]:
                f.write(f"{r['id']}\t{','.join(r['classes'])}\t{r['first_diff']}\n")

    # report.md
    lines = [f"# Shadow-oracle replay: {report['meta'].get('golden_version')} (golden) vs {report['meta'].get('candidate_version')} (candidate)", "",
             f"golden binary sha256: `{report['meta'].get('golden_binary_sha256') or 'unknown'}` · "
             f"candidate binary sha256: `{report['meta'].get('candidate_binary_sha256') or 'unknown'}`", "",
             f"owed {len(owed)} · diverging {report['totals']['diverging']} · accepted {report['totals']['accepted']} · gaps {len(gaps)} · weighted D/W = {report['totals']['ratio']:.4f}", ""]
    if report["meta"]["harness_skew_allowed"]:
        lines += [f"**--allow-harness-skew was used**: golden harness_rev `{grev}`, candidate harness_rev `{crev}`. "
                  "A divergence below may be explained by a harness change, not busbar's behavior.", ""]
    lines += ["",
             "| family | owed | diverging | accepted | D/W |", "|---|---|---|---|---|"]
    for fam, s in fam_table.items():
        lines.append(f"| {fam} | {s['owed']} | {s['diverging']} | {s['accepted']} | {s['ratio']:.3f} |")
    lines += ["", "| class | cells |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(class_counts.items(), key=lambda kv: -kv[1])]
    top = sorted((r for r in results if r["classes"] and "accepted" not in r), key=lambda r: (-r["weight"], r["id"]))[:25]
    if top:
        lines += ["", "## Top divergences", ""]
        for r in top:
            lines.append(f"- `{r['id']}` [{','.join(r['classes'])}] {r['first_diff']}")
    if gaps:
        lines += ["", "## Golden gaps (not owed)", ""] + [f"- `{i}` {s}: {d}" for i, s, d in gaps[:50]]
        if len(gaps) > 50:
            lines.append(f"- … {len(gaps) - 50} more in owed-gaps.txt")
    if extra:
        lines += ["", "## Recorded by the candidate, compared by nothing (`extra.candidate`)", "",
                  "Not divergences — these cells were never compared, because the golden does not owe "
                  "them. Named here because silence about them is how a rename reads as a deletion and "
                  "how a stale file from an earlier recording survives into a report.", ""] + \
                 [f"- `{i}` {w}" for i, w in extra[:50]]
        if len(extra) > 50:
            lines.append(f"- … {len(extra) - 50} more in extra-candidate.txt")
    with open(os.path.join(a.out, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    narrowed_rows = [r for r in results if "narrowed" in r]
    if narrowed_rows:
        lines += ["", "## Cells compared narrowly (`compare`)", ""] + \
                 [f"- `{r['id']}` narrowed: [{', '.join(r['narrowed'])}]" for r in narrowed_rows]
        with open(os.path.join(a.out, "report.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    for r in results:
        # `narrowed: [...]` rides on the row itself. The ledger is what a human reads and what
        # verdict.sh folds, and a narrowed cell's verdict covers less than the row's shape implies.
        narrow = f" narrowed: [{','.join(r['narrowed'])}]" if "narrowed" in r else ""
        if "accepted" in r:
            sys.stdout.write(f"{r['id']}\tPASS\tACCEPTED {r['accepted']['kind']} ({r['accepted']['id']}): {','.join(r['classes'])}\t{r['first_diff']}{narrow}\n")
            continue
        st = "FAIL" if r["classes"] else "PASS"
        sys.stdout.write(f"{r['id']}\t{st}\t{','.join(r['classes']) or 'identical'}\t{r['first_diff']}{narrow}\n")
    # `extra.candidate` rides on the ledger like every other verdict. The ledger is what a human
    # reads and what verdict.sh folds; a channel that exists only in report.json is a channel nobody
    # sees (the same lesson `narrowed` above already cost).
    for cid, why in extra:
        st = "FAIL" if a.refuse_extra_candidate else "PASS"
        sys.stdout.write(f"{cid}\t{st}\textra.candidate\t{why}\n")
    if a.strict:
        # The strict exit is for callers that use this file as a gate on a subset (land.sh); the full
        # verdict over every owed cell is still verdict.sh's. Zero owed cells is red: a filter that
        # selects nothing has proven nothing.
        if not owed:
            sys.stderr.write("diff-cells: strict: no owed cells matched the filter — nothing was compared\n")
            return 1
        if report["totals"]["diverging"] > 0:
            sys.stderr.write(f"diff-cells: strict: {report['totals']['diverging']} unaccepted divergence(s)\n")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
