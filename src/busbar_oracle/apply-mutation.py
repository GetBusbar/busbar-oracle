#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Busbar Inc and contributors
"""Apply ONE boot-mutation (fixtures/boot-mutations.json) to the oracle's baseline config.

  apply-mutation.py --baseline <config.yaml> --providers <providers.yaml> --mutation <BOOT-id> --out <dir>

Writes <out>/config.yaml (and <out>/providers.yaml when the mutation targets it), prints extra
KEY=VALUE env lines on stdout (for `env` ops) and writes <out>/mutation-args.json (for `args` ops).
Exit 3 with a one-line reason on stderr when the mutation is `op: null` (needs a fixture) — the
recorder records that cell as a named gap, never a pass.

Mutation ops (applied in order to the PARSED YAML document, so a typo key or a shape change lands
exactly where the inventory row says):
  {"set": "a.b.c", "value": <json>}     set a dotted path (creating maps as needed; list index as int)
  {"delete": "a.b.c"}                   remove a key
  {"raw_yaml": "text"}                  append raw text to the document (for `${VAR}` cases etc.)
  {"replace_yaml": "text"}              replace the whole document with this text
  {"providers_set": "p.k", "value": v}  same as set, on providers.yaml
  {"providers_delete": "p.k"}
  {"env": {"VAR": "value"}}             environment for the process
  {"args": ["--flag", ...]}             extra CLI arguments
  {"plugin_dir": ["<repo-name>", ...]}  stage a REAL, digest-pinned, published plugin tarball (see
                                        fetch-plugin.sh / plugin-digests.tsv) per named repo into a
                                        fresh <out>/plugins/ directory and point `plugins.dir` at it
                                        (merged into any `plugins:` map a prior `set` op already
                                        wrote — dir is the only key this op touches). Lets a boot
                                        mutation put a REAL signed artifact of a KNOWN kind (hook /
                                        auth / store / secret) in front of a config reference that
                                        expects a different kind, without needing a purpose-built
                                        broken plugin.
  {"plugin_dir_corrupt": {"name": "<repo-name>", "truncate_bytes": N}}
                                        same staging as `plugin_dir`, but truncates the copied
                                        tarball to N bytes first — a real signed artifact whose
                                        archive/manifest is now unreadable (BOOT-135's `plugin
                                        validation failed` family), as opposed to a wrong-KIND but
                                        otherwise-intact one.
  {"plugin_dir_unsigned": ["<repo-name>", ...]}
                                        same staging as `plugin_dir`, but flips one hex nibble in
                                        the copied manifest's `signature` field (same length, still
                                        valid hex, no longer the real ed25519 signature) before
                                        repacking. The archive and manifest stay well-formed — the
                                        published dylib runs unmodified — but `busbar-plugin-sign`
                                        now sees a TAMPERED signature over a `publisher: busbar`
                                        (or third-party) manifest, i.e. "unsigned" in trust-policy
                                        terms: REJECTED under `allow_unsigned: false` (the
                                        `plugin present but NOT loaded (trust policy)` family) or
                                        `Verdict::Allowed` (UNVERIFIED) under `allow_unsigned: true`
                                        — without needing a from-source unsigned plugin build.
  {"overlay": {...}}                   write the given JSON object verbatim as
                                        <out>/busbar-overlay.json (or <out>/boot-overlay.json when
                                        the mutation also sets mode: boot — see NOTE below), i.e. the
                                        durable overlay busbar reads next to config.yaml by default.
                                        The op vocabulary otherwise writes only config.yaml /
                                        providers.yaml; this is the one escape hatch for mutations
                                        that are only reachable through the overlay layer (a runtime
                                        `HookCfg` key config.yaml's `HookDefCfg` never exposes, an
                                        overlay schema version newer than this binary understands,
                                        a named-map patch this binary's typed parse rejects, etc).
                                        NOTE: record.sh's `boot` mode copies the mutated config.yaml
                                        to `<out>/boot.yaml` and rewrites BUSBAR_CONFIG to point at
                                        it, but the config's DIRECTORY is unchanged (`<out>`), so a
                                        `busbar-overlay.json` written here lands in the same
                                        directory `boot.yaml` resolves its default overlay from
                                        either way — one code path serves both modes.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover
    print("apply-mutation: PyYAML is required (pip3 install pyyaml)", file=sys.stderr)
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))

# THE FIXTURE IS THE PRODUCT'S DATA. THIS FILE IS THE TOOL.
#
# `--fixture` used to default to `<tool>/fixtures/boot-mutations.json`, which was right for exactly
# as long as the tool lived inside the product it judges. It does not any more: the PACKAGE ships
# only `fixtures/selftest-recording/**` (see pyproject's package-data), and boot-mutations.json is a
# statement about the PRODUCT's boot surface that travels with the product's data directory. So the
# default resolved to a path that does not exist in an installed tool, `record.sh` passed no
# `--fixture`, and every `mutation:` cell died with FileNotFoundError. Measured on a full linux
# re-record against v0.2.0: 232 FAIL rows (195 boot.refusal, 31 boot.warning, 7 neutrality,
# 4 documented) — a whole plane of the corpus, reported as a product regression.
#
# The default is now the DATA dir's copy, i.e. the same directory cells.json, the registers, the
# digest pins and the cell drivers come out of, and record.sh passes it EXPLICITLY as well so the
# two cannot disagree about which fixture a recording was made against. The HERE fallback stays for
# the in-tree layout every existing golden was recorded under: with BUSBAR_ORACLE_DATA unset, the
# tool is sitting inside the product and the old path is the right one.
DATA = os.environ.get("BUSBAR_ORACLE_DATA") or HERE
BOOT_MUTATIONS = os.path.join(DATA, "fixtures", "boot-mutations.json")


def _fetch_plugin(repo: str) -> str:
    """Resolve the cached, digest-verified tarball path for a plugin-digests.tsv repo name, fetching
    it (network) on a cache miss — the exact same path oracle-config.sh / record.sh already use, so a
    mutation's plugin is provably the same artifact the golden's other plugin cells were proven against."""
    out = subprocess.run(["bash", os.path.join(HERE, "fetch-plugin.sh"), repo],
                          capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _stage_plugin_dir(out_dir: str, repos: list, corrupt: dict | None = None) -> str:
    """Copy each named published plugin's tarball into <out_dir>/plugins/ (optionally truncating one
    of them per `corrupt`), returning the absolute plugins dir path."""
    plugins_dir = os.path.join(out_dir, "plugins")
    os.makedirs(plugins_dir, exist_ok=True)
    for repo in repos:
        src = _fetch_plugin(repo)
        dst = os.path.join(plugins_dir, os.path.basename(src))
        shutil.copyfile(src, dst)
        if corrupt and corrupt.get("name") == repo:
            n = int(corrupt["truncate_bytes"])
            with open(dst, "r+b") as f:
                f.truncate(n)
    return plugins_dir


def _stage_plugin_dir_unsigned(out_dir: str, repos: list) -> str:
    """Copy each named published plugin's tarball into <out_dir>/plugins/, tampering the copy's
    manifest `signature` field (one hex nibble flipped, same length) so the artifact no longer
    verifies — the archive stays well-formed and the dylib is untouched, only the ed25519 signature
    is now wrong. Returns the absolute plugins dir path. tarfile round-trip preserves member order
    and names; only manifest.json's bytes change (and therefore the .tar.gz's compressed bytes, but
    never its members)."""
    import io
    import tarfile

    plugins_dir = os.path.join(out_dir, "plugins")
    os.makedirs(plugins_dir, exist_ok=True)
    for repo in repos:
        src = _fetch_plugin(repo)
        dst = os.path.join(plugins_dir, os.path.basename(src))
        with tarfile.open(src, "r:gz") as tin:
            members = []
            datas = {}
            for m in tin.getmembers():
                members.append(m)
                if m.isfile():
                    datas[m.name] = tin.extractfile(m).read()
        manifest_name = next((n for n in datas if n.endswith("manifest.json")), None)
        if manifest_name is None:
            raise RuntimeError(f"plugin_dir_unsigned: {repo} tarball carries no manifest.json")
        manifest = json.loads(datas[manifest_name])
        sig = manifest.get("signature", "")
        if not sig:
            raise RuntimeError(f"plugin_dir_unsigned: {repo} manifest carries no signature to tamper")
        # flip one hex nibble in the middle of the signature — same length, still valid hex, no
        # longer the real ed25519 signature over this artifact's sha256.
        mid = len(sig) // 2
        flipped = "0" if sig[mid] != "0" else "1"
        manifest["signature"] = sig[:mid] + flipped + sig[mid + 1:]
        datas[manifest_name] = json.dumps(manifest).encode("utf-8")
        with tarfile.open(dst, "w:gz") as tout:
            for m in members:
                if m.name in datas:
                    payload = datas[m.name]
                    m2 = tarfile.TarInfo(m.name)
                    m2.size = len(payload)
                    m2.mode = m.mode
                    m2.mtime = m.mtime
                    tout.addfile(m2, io.BytesIO(payload))
                else:
                    tout.addfile(m)
    return plugins_dir


def walk_set(doc, path: str, value):
    parts = path.split(".")
    cur = doc
    for i, k in enumerate(parts):
        last = i == len(parts) - 1
        if isinstance(cur, list):
            k = int(k)
            if last:
                if k == len(cur):
                    cur.append(value)
                else:
                    cur[k] = value
                return
            cur = cur[k]
            continue
        if last:
            cur[k] = value
            return
        if k not in cur or cur[k] is None:
            cur[k] = {}
        cur = cur[k]


def walk_delete(doc, path: str) -> list:
    """Remove a dotted path. Returns [] when something was actually removed, or a one-element list
    naming the miss when the path is not in the document.

    A delete that MATCHES NOTHING is the silent-no-op failure mode of this whole file. `cur.pop(k,
    None)` (and the bare `return` on a missing intermediate key) turned "the baseline does not have
    the key this mutation exists to remove" — a renamed config key, a reshaped block, a typo in the
    fixture — into a config written back UNMUTATED. busbar then boots on it happily and the
    boot-refusal cell that ordered the deletion records that happy boot as its golden: a PASS that
    proves the opposite of what the cell claims, and one nothing downstream can ever distinguish
    from a real one, because "busbar accepted the mutated config" and "the mutation never landed"
    produce byte-identical recordings. Here is the only place that can tell them apart."""
    parts = path.split(".")
    cur = doc
    for i, k in enumerate(parts[:-1]):
        if isinstance(cur, list):
            cur = cur[int(k)]
        elif isinstance(cur, dict):
            if k not in cur:
                return [f"delete {path!r} matched nothing: no {'.'.join(parts[:i + 1])!r} in the baseline"]
            cur = cur[k]
        else:
            return [f"delete {path!r} matched nothing: {'.'.join(parts[:i])!r} is a "
                    f"{type(cur).__name__}, not a map"]
    k = parts[-1]
    if isinstance(cur, list):
        del cur[int(k)]
        return []
    if isinstance(cur, dict):
        if k not in cur:
            return [f"delete {path!r} matched nothing: the baseline has no {path!r}"]
        del cur[k]
        return []
    return [f"delete {path!r} matched nothing: its parent is a {type(cur).__name__}, not a map"]


def selftest() -> int:
    """Prove that a mutation which changes NOTHING is refused rather than written out, and that
    every `delete` in the shipped fixture still finds its key in the shipped baseline shape. No
    busbar, no network, no ports.
    """
    import tempfile
    fails = 0

    def say(ok, what):
        nonlocal fails
        print(f"{'PASS' if ok else 'FAIL'}  {what}")
        if not ok:
            fails += 1

    base = {"auth": {"chain": ["keys"], "signing_key": {"file": "/w/signing.key"},
                     "admin_auth": ["admin-tokens"]},
            "identity-providers": {"admin-tokens": {"module": "admin-tokens"}},
            "pools": [{"name": "p"}]}

    d = json.loads(json.dumps(base))
    say(walk_delete(d, "auth.signing_key") == [] and "signing_key" not in d["auth"],
        "delete of a present key removes it and reports no miss")
    d = json.loads(json.dumps(base))
    # THE BUG: the baseline spells it `signing_key`; a fixture (or a renamed config key) says
    # `signingKey`. The old code popped nothing and returned an untouched config.
    say(walk_delete(d, "auth.signingKey") and d == base,
        "delete of an absent LEAF is reported as a miss, not a silent no-op")
    say(walk_delete(d, "auth.tls.cert") and d == base,
        "delete under an absent INTERMEDIATE is reported as a miss")
    say(walk_delete(d, "identity-providers.admin-tokens.module.deeper") and d == base,
        "delete through a non-map is reported as a miss")
    say(walk_delete(d, "pools.0") == [] and d["pools"] == [],
        "delete of a list index still works")

    # end to end: a mutation whose only op is a delete that matches nothing must exit 2 and must not
    # leave a config.yaml behind for the recorder to boot.
    w = tempfile.mkdtemp(prefix="apply-mutation-selftest.")
    fx = os.path.join(w, "muts.json")
    json.dump({"mutations": [{"id": "SELFTEST-MISS", "op": [{"delete": "auth.signingKey"}]},
                             {"id": "SELFTEST-HIT", "op": [{"delete": "auth.signing_key"}]}]},
              open(fx, "w"))
    cfgp, provp = os.path.join(w, "config.yaml"), os.path.join(w, "providers.yaml")
    open(cfgp, "w").write(yaml.safe_dump(base))
    open(provp, "w").write("providers: {}\n")
    argv, out = sys.argv, os.path.join(w, "out")
    try:
        sys.argv = ["apply-mutation.py", "--baseline", cfgp, "--providers", provp,
                    "--mutation", "SELFTEST-MISS", "--out", out, "--fixture", fx]
        rc = main()
        say(rc == 2 and not os.path.exists(os.path.join(out, "config.yaml")),
            "a mutation that changed nothing exits 2 and writes no config.yaml")
        sys.argv[sys.argv.index("SELFTEST-MISS")] = "SELFTEST-HIT"
        rc = main()
        wrote = os.path.exists(os.path.join(out, "config.yaml")) and \
            "signing_key" not in (yaml.safe_load(open(os.path.join(out, "config.yaml"))) or {}).get("auth", {})
        say(rc == 0 and wrote, "a mutation that DID change something still writes the mutated config")
    finally:
        sys.argv = argv
        shutil.rmtree(w, ignore_errors=True)

    # every shipped delete must still find its key in the shipped baseline: this is the regression
    # that would otherwise only surface as a green cell proving nothing.
    fxp = BOOT_MUTATIONS
    if os.path.exists(fxp):
        shipped = json.load(open(fxp, encoding="utf-8"))
        dels = [(m["id"], op) for m in shipped["mutations"] if m.get("op")
                for op in m["op"] if "delete" in op]
        for mid, op in dels:
            d = json.loads(json.dumps(base))
            say(walk_delete(d, op["delete"]) == [],
                f"{mid}: delete {op['delete']!r} still matches the baseline shape")

    # THE FIXTURE COMES OUT OF THE DATA DIRECTORY, NOT OUT OF THE TOOL. A subprocess, because the
    # default is computed from the environment at import and the point of the case is what a
    # FRESHLY LAUNCHED apply-mutation.py resolves — which is how record.sh runs it.
    #
    # The planted mutation id exists in NO packaged fixture, so an arm that passes could not have
    # read one: if this file ever goes back to defaulting at its own directory, the first assertion
    # is red rather than accidentally satisfied by a same-named mutation shipped beside the tool.
    w2 = tempfile.mkdtemp(prefix="apply-mutation-datadir.")
    try:
        os.makedirs(os.path.join(w2, "fixtures"))
        planted = "SELFTEST-DATA-DIR-ONLY-b3f1"
        with open(os.path.join(w2, "fixtures", "boot-mutations.json"), "w", encoding="utf-8") as f:
            json.dump({"mutations": [{"id": planted, "op": [{"delete": "auth.signing_key"}]}]}, f)
        cfg2, prov2 = os.path.join(w2, "config.yaml"), os.path.join(w2, "providers.yaml")
        open(cfg2, "w").write(yaml.safe_dump(base))
        open(prov2, "w").write("providers: {}\n")

        def run_planted(env_data):
            env = dict(os.environ)
            if env_data is None:
                env.pop("BUSBAR_ORACLE_DATA", None)
            else:
                env["BUSBAR_ORACLE_DATA"] = env_data
            out = os.path.join(w2, "out-" + ("data" if env_data else "here"))
            shutil.rmtree(out, ignore_errors=True)
            r = subprocess.run([sys.executable, os.path.abspath(__file__),
                                "--baseline", cfg2, "--providers", prov2,
                                "--mutation", planted, "--out", out],
                               capture_output=True, text=True, env=env)
            return r, out

        r, out = run_planted(w2)
        wrote = os.path.exists(os.path.join(out, "config.yaml")) and "signing_key" not in \
            (yaml.safe_load(open(os.path.join(out, "config.yaml"), encoding="utf-8")) or {}).get("auth", {})
        say(r.returncode == 0 and wrote,
            "with BUSBAR_ORACLE_DATA set, a mutation with no --fixture is read out of the DATA dir's "
            f"fixtures/boot-mutations.json (rc={r.returncode}, stderr={r.stderr.strip()[:160]!r})")

        # …and the same call with the variable unset must NOT find it. Without this the case above
        # would still pass if the default were BOTH paths tried in turn — which is the shape that
        # lets a stale fixture beside the tool silently decide what a cell records.
        r2, _ = run_planted(None)
        say(r2.returncode != 0,
            "with BUSBAR_ORACLE_DATA unset, the same mutation is NOT found beside the tool "
            f"(rc={r2.returncode}) — the default moved, it was not widened")
    finally:
        shutil.rmtree(w2, ignore_errors=True)

    print(f"\napply-mutation selftest: {'GREEN' if not fails else f'RED ({fails} failing)'}")
    return 1 if fails else 0


def main() -> int:
    if sys.argv[1:2] == ["--selftest"]:
        return selftest()
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--providers", required=True)
    ap.add_argument("--mutation", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixture", default=BOOT_MUTATIONS,
                    help="the product's boot-mutation inventory. Default: "
                         "$BUSBAR_ORACLE_DATA/fixtures/boot-mutations.json (the tool's own directory "
                         "only when that variable is unset, i.e. the in-tree layout)")
    a = ap.parse_args()
    try:
        fx = json.load(open(a.fixture, encoding="utf-8"))
    except FileNotFoundError:
        # NAMED, NOT A TRACEBACK. record.sh reads exit 2 as "the tool failed" and exit 3 as "this
        # mutation declares itself unavailable"; a FileNotFoundError traceback landed in the cell's
        # stderr and every mutation cell became a FAIL whose detail was a Python stack. Say which
        # path was tried and where the default came from, so the next person reads the seam.
        print(f"apply-mutation: no mutation fixture at {a.fixture} "
              f"(BUSBAR_ORACLE_DATA={os.environ.get('BUSBAR_ORACLE_DATA') or '<unset>'}); "
              f"boot-mutations.json is the PRODUCT's data, not the tool's — pass --fixture or set "
              f"BUSBAR_ORACLE_DATA to the directory that holds it", file=sys.stderr)
        return 2
    mut = next((m for m in fx["mutations"] if m["id"] == a.mutation), None)
    if mut is None:
        print(f"apply-mutation: no mutation {a.mutation}", file=sys.stderr); return 2
    if mut.get("op") is None:
        print(f"{a.mutation}: {mut.get('notes', 'needs a fixture')}", file=sys.stderr); return 3

    cfg_text = open(a.baseline, encoding="utf-8").read()
    prov_text = open(a.providers, encoding="utf-8").read()
    cfg = yaml.safe_load(cfg_text) or {}
    prov = yaml.safe_load(prov_text) or {}
    raw_tail, replace_all, prov_touched = [], None, False
    env_lines, args, overlay_doc = [], [], None
    # A `delete` op that matched nothing is the silent-no-op failure mode of this file (see
    # MutationMissed): the config goes back out UNMUTATED and the cell records a happy boot as the
    # golden for a refusal. Refuse the run instead — exit 2 (harness error), never 3 (a named gap):
    # a gap is a cell we know we cannot record, this is a cell we THINK we recorded and did not.
    missed = []
    for op in mut["op"]:
        if "set" in op:
            walk_set(cfg, op["set"], op.get("value"))
        elif "delete" in op:
            missed += walk_delete(cfg, op["delete"])
        elif "raw_yaml" in op:
            raw_tail.append(op["raw_yaml"])
        elif "replace_yaml" in op:
            replace_all = op["replace_yaml"]
        elif "providers_set" in op:
            walk_set(prov, op["providers_set"], op.get("value")); prov_touched = True
        elif "providers_delete" in op:
            missed += walk_delete(prov, op["providers_delete"]); prov_touched = True
        elif "env" in op:
            env_lines += [f"{k}={v}" for k, v in op["env"].items()]
        elif "args" in op:
            args += list(op["args"])
        elif "plugin_dir" in op:
            os.makedirs(a.out, exist_ok=True)
            plugins_dir = _stage_plugin_dir(a.out, op["plugin_dir"])
            cfg.setdefault("plugins", {})["dir"] = plugins_dir
        elif "plugin_dir_corrupt" in op:
            os.makedirs(a.out, exist_ok=True)
            spec = op["plugin_dir_corrupt"]
            plugins_dir = _stage_plugin_dir(a.out, [spec["name"]], corrupt=spec)
            cfg.setdefault("plugins", {})["dir"] = plugins_dir
        elif "plugin_dir_unsigned" in op:
            os.makedirs(a.out, exist_ok=True)
            plugins_dir = _stage_plugin_dir_unsigned(a.out, op["plugin_dir_unsigned"])
            cfg.setdefault("plugins", {})["dir"] = plugins_dir
        elif "overlay" in op:
            overlay_doc = op["overlay"]
        else:
            print(f"apply-mutation: unknown op {op}", file=sys.stderr); return 2
    if missed:
        for m in missed:
            print(f"apply-mutation: {a.mutation}: {m}", file=sys.stderr)
        print(f"apply-mutation: {a.mutation} changed NOTHING — refusing to write an unmutated config "
              f"(a cell recorded against it would prove the opposite of what it claims)", file=sys.stderr)
        return 2

    os.makedirs(a.out, exist_ok=True)
    if replace_all is not None:
        out_text = replace_all
    else:
        out_text = yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False, allow_unicode=True)
        if raw_tail:
            out_text += "\n" + "\n".join(raw_tail) + "\n"
    open(os.path.join(a.out, "config.yaml"), "w", encoding="utf-8").write(out_text)
    if prov_touched:
        open(os.path.join(a.out, "providers.yaml"), "w", encoding="utf-8").write(
            yaml.safe_dump(prov, sort_keys=False, default_flow_style=False, allow_unicode=True))
    if overlay_doc is not None:
        open(os.path.join(a.out, "busbar-overlay.json"), "w", encoding="utf-8").write(
            json.dumps(overlay_doc))
    json.dump({"args": args}, open(os.path.join(a.out, "mutation-args.json"), "w"))
    for ln in env_lines:
        print(ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
