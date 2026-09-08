# Changelog

Released by tag. A consumer pins `tag@sha256` and folds it into its harness revision,
so every entry here is a harness change by definition — a recording made before it and
one made after it are not comparable without saying so out loud.

## 0.3.8

- **`text_list_growth` reads a PIPE-SEPARATED list, and proves growth as a SET rather than a
  prefix.** Two changes to one check, both found the same way: busbar's three limit-validation
  cells (`boot.refusal|BOOT-P20|validate`, `|BOOT-P29|`, `|BOOT-P30|`) all say the same thing — a
  limit's metric enum gained the four token metrics 1.6.0 added (`tokens_input`, `tokens_output`,
  `tokens_cache_read`, `tokens_cache_write`) — and all three stayed red under 0.3.7 for reasons
  about PUNCTUATION and POSITION rather than about anything the message stopped saying.
  (1) A list spelled the way a grammar is written out — `(requests | tokens | budget | concurrent)`,
  or unparenthesised mid-sentence, `<metric> is one of requests|tokens|budget|concurrent and
  <window> one of minute|hour|day|month|total` — is now found beside the backtick-quoted spelling,
  by one rule: an item is a bare word, at least two of them joined by `|`, so a run ends at the
  first token no pipe follows and cannot swallow the sentence around it. The parentheses are PROSE
  AROUND the run, not part of it, and stay under the byte-identical-surroundings requirement like
  every other character of the template. Runs of the two spellings never overlap, and a list that
  changed WHICH spelling it uses is a template change, refused by name.
  (2) The changed list's relation is now membership, not position: every golden item must appear
  somewhere in the candidate's list (multiplicity respected), and whatever is left over is the
  growth. Through 0.3.7 golden's items had to be an ordered PREFIX, so growth was only ever
  forgiven at the END of a list — and real enums grow next to the item they refine. An item DROPPED
  is still the first thing refused, named by its position in the golden's list.
  DELIBERATE WIDENING, stated out loud: a candidate that merely REORDERS golden's items, adding
  nothing, is a set superset and is now ACCEPTED, where 0.3.7 refused it as "added no new items".
  A reordered operator-visible list is still a change nobody announced (busbar's own register says
  so: F-013's BOOT-020 narrowing ruled exactly that "neither additive nor better" and fixed it
  rather than forgiving it) — but catching it is the register's job, not this check's: an entry
  naming the cell, with a changelog line and a declared width, still has to be written by a person
  before any of this runs. Such a row says `additive: the declared list was REORDERED, no items
  added or dropped` rather than falling through to a raw before/after that reads like a forgiven
  rewrite. Everything else is byte-identical in behaviour: the splice proof (golden's own raw list
  text put back into the candidate must reproduce golden BYTE FOR BYTE), the one-changed-list
  limit, the every-other-list-byte-identical rule, the JSON-leaf path and its one-differing-leaf
  slot, and every load-time refusal are untouched. In the self-test, the ONLY verdicts that move
  are the two mid-list-insertion cases (`rr4`, `ss4`), which were red for the new item's position
  alone and are now accepted naming it.

## 0.3.7

- **`additive` gains `new_route`, for a route that did not exist in 1.5.5 now answering.** Every
  additive relation so far (superset, `text_list_growth`, `description_corrections`) compares two
  REAL responses; there is no relation between 1.5.5's not-found stub and whatever a new route
  answers with now, so none of them apply. An entry may declare `new_route: true`; on a cell where
  the golden's status is 404 AND the golden body is shaped like 1.5.5's not-found envelope (a JSON
  object whose only top-level key is `error`, itself carrying a string `message`) AND the
  candidate's status is not 5xx, the entry may take `status`, `headers` and `body` for that cell
  wholesale, no relation checked between the two bodies at all — reported on the accepted row as
  `new route: golden 404 -> candidate <status>`. Any other golden status, or a candidate 5xx,
  leaves the flag inert and the ordinary additive rules decide exactly as if it were absent.
  `status` joins the classes `additive` may take, refused at load unless `new_route: true` is set;
  the money guard gains a narrow exception for `new_route` + `kind: additive` + a changelog line +
  only `status` among the money classes named — there is no previous behavior to declare `breaking`
  against, but it is still money and still needs its own changelog line.

## 0.3.6

- **`additive` gains `description_corrections`, for a named JSON string leaf that may differ
  outright.** Not every body difference is growth — sometimes 1.5.5's prose was simply wrong, and
  1.6.0's replacement is a registered factual correction rather than an addition.
  `description_corrections: ["<json pointer>", ...]` forgives a STRING leaf's mismatch at exactly
  the listed pointer(s), no growth relation checked, reported on the accepted row with both texts;
  every other leaf still follows the ordinary superset rules unchanged, and an entry may correct
  one leaf while separately proving `text_list_growth` on a different leaf in the same body (a
  corrected leaf never competes for `text_list_growth`'s one-differing-leaf slot — checked first).
  Refused at load: a pointer that does not resolve to a string in the golden body of any cell the
  entry matches — a typo'd path would otherwise silently forgive nothing while the cell stays red
  for an unrelated-looking reason.

## 0.3.5

- **`text_list_growth` reaches a string LEAF inside a JSON body, not only a plain-text body.**
  `admin.ops|DeleteOverlaySection|not-found`'s enum lives at `/error/message` inside a JSON
  envelope, not in a plain-text body. `additive_superset()`'s walk now defers a STRING leaf
  mismatch (collecting the JSON pointer, golden value and candidate value) instead of failing on
  it immediately; every other mismatch (a missing key, a short array, a non-string scalar, an
  uncovered `null`) still fails the walk immediately, unchanged. After the walk: zero deferred
  leaves is a plain superset as before; exactly one is fed to the existing
  `text_list_growth_check()` proof, naming the leaf's JSON pointer on both the accepted row and a
  refusal; more than one differing leaf is refused by name, naming every path — a second leaf
  moving is not "one list grew" under either leaf's own story. Verified against busbar's real
  Oxford-comma shape on both sides (golden "...`root`, or `plugin_versions`"; a grown candidate
  "...`plugin_versions`, ..., or `agents`"): list membership is defined by extracting
  backtick-quoted tokens via a run regex whose separator alternation treats "or" purely as prose
  glue wherever it falls, never as an item of its own, so the Oxford "or" moving to before the new
  last item as the list grows needs no special case.

## 0.3.4

- **`additive` gains `text_list_growth: true`, the same superset proof for a key-list named in
  prose instead of JSON.** A body/`effects.stderr` string can grow an enum inside a sentence
  ("expected `groups`, `hooks`, `root`, or `plugin_versions`") the same way a JSON array grows a
  key — but there is no JSON structure to walk, and the surrounding template is exactly the part
  that must not move for free. The check finds every backtick-quoted comma list in the golden and
  candidate text (paired by order; a mismatched count, or growth touching more than one list, is
  refused — one declared list per entry keeps the check narrow); the one list that changed must
  hold golden's items as a PREFIX; every other list and everything outside the lists must be
  byte-identical; and the actual proof is a splice — golden's own raw list text spliced back into
  the candidate at the changed list's position must reproduce golden byte for byte. That splice is
  what catches a reworded template beside real growth: `admin.ops|DeleteOverlaySection|not-found`
  changed "expected" to "expected one of" alongside adding four items, and the splice fails at the
  first byte the wording differs, so the cell stays red. `effects.stderr` is now a valid `additive`
  class, but refused at load unless `text_list_growth: true` is also set — a raw string has no
  other growth proof this file knows. Accepted rows report the items proved new via
  `detail["additive.removed"]`.

## 0.3.3

- **A third register kind, `additive`, for growth the tool proves rather than an owner asserts.**
  `improvement` can never take `body`/`headers` on a BODY_IS_CONTRACT family (rated 10 there), and
  `breaking` would misdescribe a response that dropped nothing and changed no existing value —
  neither fits a body that grew a key. `additive` may take `body` and `headers` ONLY when the tool
  itself proves the candidate a superset of the golden at every path the golden defines: for
  `body`, both sides must parse as JSON, every golden key/value must be present in the candidate
  (recursively; extra keys allowed; arrays are a golden-prefix-of-candidate under the same rule
  per element; a golden `null` growing into a value needs the path listed under `null_to_value`);
  for `headers`, every golden header must be present with an equal value (extra headers allowed;
  `content-length` exempt only when this same entry's body check passed). `status`, usage,
  `effects.readback` and every `missing.*` class are never taken by `additive` — refused at load,
  same as any class outside `{body, headers}` — and an `additive` entry must carry a `changelog`
  line, exactly as `breaking` does. A failed proof leaves the cell red and the row names where:
  `additive: not a superset at <json path / header>`. Fixes the second half of the rated-weight
  problem: F-011's admin.ops views (`GetHooks`, `PostHooks`, `GetOpenapiJson`, ...) are
  BODY_IS_CONTRACT and were owner-ruled additive growth, not breaking, with no register kind able
  to say so without either being refused (`improvement`) or overclaiming (`breaking`).

## 0.3.2

- **A fired transform may take a class it rendered identical, whatever that class is rated on
  the cell's family.** 0.3.1's fired-transform branch subtracted the FAMILY-rated money set
  (`money_at_cell`) from an entry's own `allowed` set even when the rewritten pair was
  byte-identical, so an `improvement`-kind transform (e.g. stripping a `diag=BUSBAR-NNNN`
  diagnostic suffix) could no longer be credited for `body` on any BODY_IS_CONTRACT family
  (`boot.warning`, `boot.refusal`, `cli`, `config.migrate`, `admin.ops`, `ops.scrape`) — 18 real
  `boot.warning` cells in busbar's own corpus went from accepted to a phantom divergence purely
  because their family rates `body` 10 and the entry is not `kind: breaking`. The fired-transform
  branch only ever runs once the rewritten pair has been PROVEN byte-identical (a transform
  touches only `effects.stderr` and `body.text`, so every other field is a verbatim copy and
  would still show up in the comparison if it moved); nothing is being forgiven at that point,
  so the entry's own `allowed` set — already held to the GLOBAL money guard at load — is what
  decides, not a second family-specific subtraction. A class the rewrite does not touch (a
  changed `status`, a real body difference outside the rewritten pattern) still fails to reach
  this branch at all and stays red, exactly as before.

- **A transform pattern that can match the empty string, or names no literal text at all, is
  refused at load** (`.*`, `\s*`, `(.|\n)*`, `\d+`, ...). "Byte-identical after the rewrite" only
  proves the rewrite named the right token when the pattern IS a token; a pattern this broad can
  swallow arbitrary surrounding content and would make that proof vacuous.

## 0.3.1

- **A `transform` in `accepted-differences.json` is applied symmetrically, not just to the
  candidate.** `diff-cells.py`'s transform branch rewrote only the candidate side before
  comparing, on the assumption that the pattern a transform strips (a diagnostic code, a
  new metering series) exists on the candidate and never on the golden. That assumption
  fails the moment the golden is *also* shaped like the candidate — two recordings of the
  same binary taken to prove determinism, or a candidate-vs-candidate A/B — and stripping
  the pattern from one side only manufactured a phantom divergence out of an
  already-identical pair. The golden is now rewritten by the same rules before either side
  is compared: when the golden lacks the pattern (the real golden-vs-candidate case) this
  is a no-op, so the transform still only ever removes a difference, never invents one.
  `replay-selftest.sh` proves all three properties this fix must hold: a self-diff (or any
  candidate-vs-candidate pair) under a transform-bearing register now reports 0 diverging
  (RED before this fix), the real asymmetric case is forgiven exactly as before, and a
  genuine body divergence planted under a transform-bearing entry still reports RED.

## 0.3.0

Four external audits of the tool at `v0.2.3` are the source of this release. Every
change below closes a path by which the oracle could report **GREEN over something it
had not compared**, and every one ships with a self-test case that was red before it
and is green after — in `busbar-oracle replay-selftest`, `cells --selftest`,
`fixture-gate-selftest`, `rigs-ledger --selftest` or `oracle-config.sh --selftest`,
never a new test runner.

### The corpus can no longer shrink unseen

- **A corrupt `cells.json` is refused as a floor.** `enumerate-cells.py` swallowed
  `json.JSONDecodeError` on the committed corpus, skipped the per-family floor
  entirely, and `--write` then committed the shrunken corpus **as the new reference**
  in the same command. A reference that cannot be read is not a reference that says
  "anything goes". Also refused: a `counts.by_family` that is missing, or that gives a
  count which is not a non-negative integer (that family had silently had no floor),
  and an `--accept-family-shrink` naming a family the corpus does not have. `--write`
  is now atomic, and importing the product's cell module no longer writes
  `__pycache__` into the tree the gate asserts is clean.

- **A baselined cell deleted from `cells.json` is RED.** `replay.sh`'s owed-baseline
  ratchet names that case first in its own header and could not see it: `owed` and the
  gap list are both derived *from* `cells.json`, so an id removed from the corpus was
  in neither, and `if cid not in scope: continue` dropped it in silence. The default
  reason string on that branch was provably unreachable. `diff-cells.py` now writes
  `corpus-ids.txt` and `selected-ids.txt`, and the ratchet distinguishes "this id left
  the corpus" (red, unless `accepted-gaps.json` names it) from "this run used
  `--family` and never looked at it" (still skipped).

- **`extra.candidate`: a cell the candidate recorded that nothing compared.** Every
  loop in the differ walks the OWED set, which comes from the golden, so a cell file
  the candidate wrote that the golden does not owe was invisible — no row, no class,
  no line. That covers a gap the candidate has closed, the *other half of a rename*,
  and a stale file from an earlier recording into the same `--out`. Reported on its
  own ledger row, in `extra-candidate.txt`, in `report.json` and in `report.md`. Not
  red by default (nothing was compared); `--refuse-extra-candidate` makes it red, and
  the ids reach `EXPECTED_IDS` so the verdict actually reads them.

### The provenance guard covers the whole recording

- **Harness skew is a set, not a scalar.** The guard was one equality on
  `meta.json.harness_rev` — a field `merge-recordings.py`, `renormalize.sh` and a text
  editor all overwrite. A golden merged from a part recorded under revision A and a
  part recorded under B is stamped `B`, so a candidate recorded today under B satisfied
  it exactly and the differ compared A's cells against B's with nothing said. The
  provenance is now `harness_rev` ∪ `harness_rev_history` ∪ `merged_from[].harness_rev`
  ∪ `harness_rev_recorded`, and the two sides must name the same set.
  `harness_rev_recorded` — a field no file in this repository wrote or read, while
  sitting in a shipped golden carrying a real digest — is now written by both
  re-stampers (once, so the original recording revision survives every later re-stamp)
  and read here. `report.json` gains `golden_harness_revs` / `candidate_harness_revs`.

  **Consuming products should expect this to bite.** A golden that was merged or
  re-normalized is mixed-revision by construction, and a fresh candidate is not: such
  a pair is now refused unless `--allow-harness-skew` is passed deliberately, or the
  golden is re-normalized to a single revision.

### Money classes are armed where they were not

- **Concurrent cells capture their real egress.** `capture-concurrent.py` hardcoded
  `"effects": {… "egress": []}` — which is `capture.py`'s own spelling of "this cell
  must never reach upstream" — on cells that bill for eight upstream requests.
  `effects.egress` is rated 10 and is a MONEY class, so on every concurrency/queue cell
  a candidate that sent a mangled system prompt, dropped the tool list, leaked a client
  header upstream or doubled its upstream attempts diverged on nothing: `[] == []`,
  `PASS identical`. The one other witness (`busbar_upstream_attempts_total`) is masked
  on this very driver. The recorder now snapshots egress around the burst, settles it,
  and names the files, exactly as the single-request path does.

- **The "an `improvement` may not forgive money" rule is keyed on the RATED weight.**
  `MONEY_CLASSES` is keyed on the class and asserted against `CLASS_WEIGHT`; the weight
  a divergence is actually scored with is keyed on the FAMILY. On the six
  `BODY_IS_CONTRACT` families the `body` class is rated 10 while `CLASS_WEIGHT` rates
  it 3 and `MONEY_CLASSES` does not name it — so a four-line `improvement` entry with
  no `kind: breaking` and no changelog line could waive the entire stdout and stderr of
  a boot refusal, the only thing such a cell records, weighted 10 in the D/W ratio. The
  file's own assertion could not catch it because it never looked at the family path.
  One function (`rated_weight`) now answers both questions; entries that lose reach are
  named on stderr when the register loads. Also fixed here: a declared `"weight"` could
  price a money divergence as a cosmetic one, and `"weight": 0` silently became 10.

- **Egress ordering no longer depends on the recording host's locale.** The order of
  the egress filenames *is* the recorded order of the upstream requests; `sort` and
  `comm` are pinned `LC_ALL=C`, as `harness-rev.sh` already pins its own glob.

### A recording is what a driver that succeeded produced

- **A script cell passes when its driver exited 0, into a directory this run emptied.**
  PASS meant "a non-empty `captured.json` exists, its status is not -1, and it carries
  no `effects.harness_error`" — the exit status of the process that wrote the file was
  discarded at the invocation, and three drivers in the consuming product define no
  `fail()` and no `harness_error` at all, so for those the file test was the whole gate.
  Nothing cleared the output directory either, so on a re-record into an existing
  `--out` that file could be *the previous run's*. The give-up ordering was also wrong:
  `status == -1` was tested before `harness_error` and `continue`d, so a driver that
  gave up **and marked it correctly** was filed as a named gap — and a SKIP row leaves
  the owed set entirely. One rule now, in one order, in `script_cell_verdict()`, which
  the self-test drives directly.

- **The fixture gate refuses a `needs_fixture` it cannot read.** `${!1}` requires a
  valid shell identifier; on anything else bash prints `invalid variable name` and
  **aborts the enclosing compound command**, so the recorder's
  `if oracle_fixture_missing …; then SKIP; continue; fi` ran neither branch, fell
  through, and **recorded the cell with its fixture absent** — freezing whatever a
  product with no backend answers into the golden, with a PASS row, reproduced by every
  candidate. (`needs_fixture: 1` was the same hole by indirecting onto `$1`.) The gate
  now answers three ways — gap, record, or *this is not a fixture gate* — and the third
  is a red row. `fixture-gate-selftest` checks every value in the product's own corpus
  against it.

- **`oracle_write_config` and `oracle_env` cannot name different directories.** One
  wrote to `$1`, the other booted from `$WORK`; the pair was correct only because every
  shipped driver happens to export the same value. It is now bound, and a caller whose
  `WORK` names somewhere else is refused rather than served a config it did not write.

### The rig ledger fails closed

`rigs-ledger.sh` had four ways to report green having compared nothing, and all four
are now rows:

- a **corrupt baseline** made the fold throw, produced no rows, and `[ -n "$rows" ]`
  read that as "no regressions" — every signed-off row unchecked. The exit status is
  captured (as the file's four other folds already did) and a fold that threw is
  `baseline|_fold_failed`, red and owed;
- a **missing baseline** was an advisory line. It is `baseline|_missing`, red and owed
  — except under `--rebaseline`, which is the run that creates the first one;
- **`--rebaseline` skipped the diff**, so it refused to sign off a FAIL and happily
  signed off an *absence*: a scenario that stopped executing became the new floor.
  The comparison now runs under it too;
- **`--check` was parsed and never read**, so the one caller that asked for the
  baseline comparison out loud got the same run as one that did not. It now refuses a
  run with no baseline, and refuses to be combined with `--rebaseline`.

New: `--accept-baseline-loss <row-id>` (repeatable), so a reviewed removal is named in
the command line of the commit that makes it rather than accepted by the absence of a
check.

### The self-tests can no longer pass vacuously

- case (y), the owed ratchet, was **skipped in silence** when `owed-baseline.txt` was
  empty or absent — the one state in which every ratchet the file provides is gone. An
  empty baseline beside a golden that owes cells is now the finding;
- the `harness_error` guard triggered only on a **literal digit** after `fail`, so
  `fail "$rc" …` was skipped entirely, and it asked the whole FILE for the string
  rather than the give-up path. Both widened, plus a new check for a give-up written
  inline rather than through `fail()`;
- a case that **could not run** now reports `SKIP` and is counted in the summary
  instead of reporting `PASS` (the `SUPERVISOR_MARKERS` check and the owed ratchet);
- the driver-contract case's "beside itself" arm resolved against an empty temp
  directory, so it would have passed with the extraction reverted. It resolves against
  the product's own `scripts/` directory now, and says so when the layout cannot
  discriminate;
- two independent cases shared `$W/renorm`, so one measured the other's leftovers;
- `renormalize.sh`'s faithfulness table cited `record.sh` **line numbers** that had all
  rotted, and a self-test asserted on one of them — so correcting the comment turned
  the case red for the wrong reason. Call sites are named by function now.

### Documentation

- the README claimed "Nothing in this repository knows anything about busbar", which is
  not true of this tool and never was: the release URL, the directory layout it sources
  from, the plane vocabulary, the dialect table and several transcribed source
  behaviours are all the product's. Corrected to the claim that is true and that the
  code actually enforces — a *product* file is never resolved against the tool's own
  directory — which the self-tests check as a class.

## 0.2.3 and earlier

See the tag history. `0.2.x` was the extraction of the oracle out of the product's tree:
the tool/data seam, the driver contract (`BUSBAR_ORACLE_TOOL_DIR`), the harness revision
covering the tool by digest and the data by hash, and the self-tests for each.
