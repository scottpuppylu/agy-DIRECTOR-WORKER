#!/usr/bin/env python3
"""Verify that a DIRECTOR folder (and optional project .agents/ setup) was built correctly for agydw.

Checks:
  - Required files exist: DIRECTOR_STATE.md, ACTIVE_WORKSTREAMS.md, DECISION_REGISTER.md,
    PO_PENDING.md, RESUME_STATE.md, DIRECTOR_OPERATING_RULES.md, director_work.json
  - DIRECTOR_STATE.md has a filled PENDING_METHOD line (ask_question + PO_PENDING.md; Artifacts/
    Generative UI optional); leftover PO_PAGE_URL from Claude edition is noted
  - PO_PENDING.md entries (ID: OD-xx / ACT-xx) have required fields, no placeholders on answered/closed
  - RESUME_STATE.md 4-line header (PROGRESS, DOING, NEXT_STEP, COMMS) + size check (shadow; --strict fails)
  - director_work.json schema_version 1, regexes, resume / base_ref / release settings (shadow; --strict fails)
  - With --repo: checks .agents/hooks.json and .agents/plugins.json existence/JSON (shadow; --strict fails)

Usage:
  python -I -X utf8 agy_setup_check.py DIRECTOR [--repo REPO] [--strict] [--json]
"""
import argparse
import importlib.util
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REQUIRED_FILES = (
    "DIRECTOR_STATE.md",
    "ACTIVE_WORKSTREAMS.md",
    "DECISION_REGISTER.md",
    "PO_PENDING.md",
    "RESUME_STATE.md",
    "DIRECTOR_OPERATING_RULES.md",
    "director_work.json",
)
RESUME_KEYS = ("PROGRESS", "DOING", "NEXT_STEP", "COMMS")
PO_START = re.compile(r"^[ \t]*ID[ \t]*:[ \t]*((?:OD|ACT)-\S+)", re.M)
PLACEHOLDER = re.compile(r"^<[^<>]*>$|^TBD$|^待\S*$")


def load_sibling(name):
    """Load sibling module cleanly."""
    spec = importlib.util.spec_from_file_location(f"_agy_chk_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_director(dpath, repo_path=None):
    """Return (problems, shadows, notes) for one DIRECTOR folder."""
    problems, shadows, notes = [], [], []
    d = pathlib.Path(dpath)
    if d.is_file() and d.name == "DIRECTOR_STATE.md":
        d = d.parent
    if not d.is_dir():
        return [f"missing DIRECTOR folder: {d}"], [], []

    for fname in REQUIRED_FILES:
        if not (d / fname).is_file():
            problems.append(f"missing required file: {fname}")

    state_file = d / "DIRECTOR_STATE.md"
    if state_file.is_file():
        st = state_file.read_text(encoding="utf-8-sig", errors="replace")
        pm = re.search(r"^[ \t]*PENDING_METHOD[ \t]*:[ \t]*(.*)$", st, re.M)
        if not pm or not pm.group(1).strip() or PLACEHOLDER.match(pm.group(1).strip()):
            problems.append("DIRECTOR_STATE.md missing filled PENDING_METHOD line (e.g. ask_question + PO_PENDING.md)")
        if re.search(r"^[ \t]*PO_PAGE_URL[ \t]*:", st, re.M):
            notes.append("DIRECTOR_STATE.md contains Claude-era PO_PAGE_URL (ignored in agydw)")

    po_file = d / "PO_PENDING.md"
    if po_file.is_file():
        pt = po_file.read_text(encoding="utf-8-sig", errors="replace")
        starts = list(PO_START.finditer(pt))
        seen_ids = set()
        for idx, m in enumerate(starts):
            pid = m.group(1)
            if pid in seen_ids:
                shadows.append(f"PO_PENDING.md duplicate ID: {pid}")
            seen_ids.add(pid)
            end = starts[idx + 1].start() if idx + 1 < len(starts) else len(pt)
            blk = pt[m.start():end]
            for req in ("STATUS", "QUESTION", "OPTIONS", "RECOMMENDED", "SOURCE"):
                fm = re.search(rf"^[ \t]*{req}[ \t]*:[ \t]*(.*)$", blk, re.M)
                if not fm or not fm.group(1).strip():
                    problems.append(f"PO_PENDING.md {pid}: missing {req}")
            st_m = re.search(r"^[ \t]*STATUS[ \t]*:[ \t]*(\S+)", blk, re.M)
            status_val = st_m.group(1).lower() if st_m else ""
            if status_val not in ("open", "answered", "closed"):
                problems.append(f"PO_PENDING.md {pid}: invalid STATUS {status_val!r}")
            ans_m = re.search(r"^[ \t]*ANSWER[ \t]*:[ \t]*(.*)$", blk, re.M)
            ans_val = ans_m.group(1).strip() if ans_m else ""
            if status_val in ("answered", "closed") and (not ans_val or PLACEHOLDER.match(ans_val)):
                problems.append(f"PO_PENDING.md {pid}: STATUS={status_val} requires non-empty ANSWER")
            if not re.search(r"^[ \t]*CREATED[ \t]*:", blk, re.M):
                shadows.append(f"PO_PENDING.md {pid}: missing CREATED date")

    res_file = d / "RESUME_STATE.md"
    if res_file.is_file():
        rtext = res_file.read_text(encoding="utf-8-sig", errors="replace")
        rlines = [l for l in rtext.splitlines() if l.strip()]
        if len(rlines) < 4 or not all(rlines[i].startswith(f"{k}:") for i, k in enumerate(RESUME_KEYS)):
            problems.append("RESUME_STATE.md must start with PROGRESS:, DOING:, NEXT_STEP:, COMMS: in order")
        else:
            for i, k in enumerate(RESUME_KEYS):
                val = rlines[i].split(":", 1)[1].strip()
                if not val or PLACEHOLDER.match(val):
                    shadows.append(f"RESUME_STATE.md {k} is empty or placeholder")
        if not re.search(r"^[ \t]*UPDATED[ \t]*:[ \t]*\d{4}-\d{2}-\d{2}", rtext, re.M):
            shadows.append("RESUME_STATE.md UPDATED missing YYYY-MM-DD date")

    cfg_file = d / "director_work.json"
    if cfg_file.is_file():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            problems.append(f"director_work.json invalid JSON: {exc}")
            cfg = None
        if isinstance(cfg, dict):
            if cfg.get("schema_version") != 1:
                shadows.append(f"director_work.json schema_version={cfg.get('schema_version')!r}, expected 1")
            if cfg.get("mode") not in (None, "general", "beginner"):
                shadows.append(f"director_work.json mode={cfg.get('mode')!r} (expected general or beginner)")
            for rx_key in ("trace_id_pattern", "po_item_pattern", "po_open_pattern"):
                if rx_key in cfg and isinstance(cfg[rx_key], str):
                    try:
                        re.compile(cfg[rx_key])
                    except re.error as err:
                        shadows.append(f"director_work.json {rx_key} invalid regex: {err}")
            if res_file.is_file():
                ms = load_sibling("memory_size_check")
                settings = ms.resume_settings(cfg, notes)
                facts = ms.measure(res_file.read_bytes())
                over = ms.over_limits(facts, settings["max_bytes"], settings["max_line_bytes"])
                if over:
                    shadows.append(f"RESUME_SIZE over ({', '.join(over)}): {facts['bytes']} bytes, longest {facts['longest_bytes']}")

    if repo_path:
        rp = pathlib.Path(repo_path)
        for rel in (".agents/hooks.json", ".agents/plugins.json"):
            fp = rp / rel
            if not fp.is_file():
                shadows.append(f"workspace missing {rel} (run agy_setup.py --apply)")
            else:
                try:
                    json.loads(fp.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError) as exc:
                    problems.append(f"{rel} invalid JSON: {exc}")

    return problems, shadows, notes


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directors", nargs="+", metavar="DIRECTOR", help="DIRECTOR folder(s) to check")
    ap.add_argument("--repo", help="optional workspace repo to check .agents/hooks.json and plugins.json")
    ap.add_argument("--strict", action="store_true", help="treat shadow findings as failures (exit 1)")
    ap.add_argument("--json", action="store_true", help="output JSON summary")
    try:
        a = ap.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    results = []
    any_fail = False
    for d in a.directors:
        probs, shadows, notes = check_director(d, a.repo)
        failed = bool(probs) or (a.strict and bool(shadows))
        any_fail = any_fail or failed
        results.append({"director": str(d), "ok": not failed, "problems": probs, "shadows": shadows, "notes": notes})

    if a.json:
        print(json.dumps({"ok": not any_fail, "strict": a.strict, "results": results}, indent=2, ensure_ascii=False))
    else:
        for r in results:
            tag = "OK  " if r["ok"] else "FAIL"
            print(f"{tag} {r['director']}")
            for p in r["problems"]:
                print(f"  - problem: {p}")
            for s in r["shadows"]:
                prefix = "  - problem:" if a.strict else "  ~ shadow:"
                print(f"{prefix} {s}")
            for n in r["notes"]:
                print(f"  ~ note: {n}")
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
