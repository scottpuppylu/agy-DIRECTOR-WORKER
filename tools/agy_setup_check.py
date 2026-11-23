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
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
CORE_PRETOOL_TOOLS = ("run_command", "read_file", "write_file", "edit_file")
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


def _norm_key(p):
    try:
        return os.path.normcase(pathlib.Path(p).resolve().as_posix())
    except (OSError, ValueError):
        return os.path.normcase(str(p).replace("\\", "/"))


def _git_common_dir(repo):
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            p = pathlib.Path(proc.stdout.strip())
            return p.resolve() if p.is_absolute() else (pathlib.Path(repo) / p).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    dot = pathlib.Path(repo) / ".git"
    return dot.resolve() if dot.exists() else None


def _matcher_covers_core(matcher):
    """Verify matcher pattern compiles cleanly and matches all core Antigravity tools."""
    if not isinstance(matcher, str) or not matcher.strip():
        return False, "empty matcher"
    pat = ".*" if matcher.strip() == "*" else matcher
    try:
        rx = re.compile(pat)
    except re.error as exc:
        return False, f"invalid matcher regex {matcher!r}: {exc}"
    uncovered = [t for t in CORE_PRETOOL_TOOLS if not rx.search(t)]
    if uncovered:
        return False, f"matcher {matcher!r} does not cover core tool(s): {', '.join(uncovered)}"
    return True, None


def _extract_hook_entries(entries, need_matcher=False):
    """Extract enabled command strings and matcher coverage issues from a lifecycle event list."""
    cmds = []
    matcher_issues = []
    for blk in entries or []:
        if not isinstance(blk, dict) or blk.get("enabled") is False:
            continue
        if isinstance(blk.get("command"), str) and blk["command"].strip():
            if not need_matcher:
                cmds.append(blk["command"].strip())
            continue
        inner = blk.get("hooks")
        if not isinstance(inner, list):
            continue
        active_inner = [
            hk["command"].strip()
            for hk in inner
            if isinstance(hk, dict) and hk.get("enabled") is not False and isinstance(hk.get("command"), str) and hk["command"].strip()
        ]
        if not active_inner:
            continue
        if need_matcher:
            ok_m, m_err = _matcher_covers_core(blk.get("matcher"))
            if not ok_m:
                matcher_issues.append(m_err)
                continue
        cmds.extend(active_inner)
    return cmds, matcher_issues


def validate_hooks_structure(hdata):
    """Validate .agents/hooks.json groups, enabled flags, matchers, and required lifecycle hooks."""
    problems = []
    pretool_cmd = None
    if not isinstance(hdata, dict):
        return [".agents/hooks.json must be a JSON object"], None

    guard_grp = hdata.get("agydw-guard")
    bridge_grp = hdata.get("agydw-audit-and-bridge")

    if not isinstance(guard_grp, dict):
        problems.append(".agents/hooks.json missing 'agydw-guard' group (run agy_setup.py --apply)")
    elif guard_grp.get("enabled") is False:
        problems.append(".agents/hooks.json 'agydw-guard' group is disabled (enabled=false)")
    else:
        pre = guard_grp.get("PreToolUse")
        if not isinstance(pre, list) or not pre:
            problems.append(".agents/hooks.json 'agydw-guard' missing PreToolUse list")
        else:
            g_cmds, m_errs = _extract_hook_entries(pre, need_matcher=True)
            guard_matches = [c for c in g_cmds if "agy_guard.py" in c]
            if not guard_matches:
                msg = ".agents/hooks.json 'agydw-guard' has no enabled PreToolUse hook covering core tools"
                if m_errs:
                    msg += f" ({'; '.join(m_errs)})"
                problems.append(msg)
            else:
                pretool_cmd = guard_matches[0]

    if not isinstance(bridge_grp, dict):
        problems.append(".agents/hooks.json missing 'agydw-audit-and-bridge' group (run agy_setup.py --apply)")
    elif bridge_grp.get("enabled") is False:
        problems.append(".agents/hooks.json 'agydw-audit-and-bridge' group is disabled (enabled=false)")
    else:
        post = bridge_grp.get("PostToolUse")
        if not isinstance(post, list) or not post:
            problems.append(".agents/hooks.json 'agydw-audit-and-bridge' missing PostToolUse list")
        else:
            b_cmds, bm_errs = _extract_hook_entries(post, need_matcher=True)
            if not (any("agy_audit.py" in c for c in b_cmds) and any("agy_hook_bridge.py" in c for c in b_cmds)):
                msg = ".agents/hooks.json 'agydw-audit-and-bridge' PostToolUse missing active agy_audit.py / agy_hook_bridge.py"
                if bm_errs:
                    msg += f" ({'; '.join(bm_errs)})"
                problems.append(msg)
        for ev in ("SessionStart", "PreInvocation", "Stop"):
            ev_list = bridge_grp.get(ev)
            if not isinstance(ev_list, list) or not ev_list:
                problems.append(f".agents/hooks.json 'agydw-audit-and-bridge' missing required lifecycle hook {ev}")
            else:
                ev_cmds, _ = _extract_hook_entries(ev_list, need_matcher=False)
                if not any("agy_hook_bridge.py" in c for c in ev_cmds):
                    problems.append(f".agents/hooks.json 'agydw-audit-and-bridge' {ev} has no enabled agy_hook_bridge.py command")

    return problems, pretool_cmd


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
        rp = pathlib.Path(repo_path).resolve()
        repo_cd = _git_common_dir(rp)
        repo_primary = repo_cd.parent.resolve() if (repo_cd and repo_cd.name == ".git") else rp
        repo_keys = {_norm_key(rp), _norm_key(repo_primary)}

        if state_file.is_file():
            st_txt = state_file.read_text(encoding="utf-8-sig", errors="replace")
            m_pr = re.search(r"^[ \t]*PROJECT_REPO[ \t]*:[ \t]*(.+)$", st_txt, re.M)
            if m_pr and m_pr.group(1).strip():
                pr_val = m_pr.group(1).strip()
                pr_cd = _git_common_dir(pr_val) if pathlib.Path(pr_val).is_dir() else None
                same_cd = bool(pr_cd and repo_cd and _norm_key(pr_cd) == _norm_key(repo_cd))
                if _norm_key(pr_val) not in repo_keys and not same_cd:
                    problems.append(f"DIRECTOR_STATE.md PROJECT_REPO ({pr_val}) does not match --repo {rp.as_posix()}")

        if cfg_file.is_file():
            try:
                cfg_own = json.loads(cfg_file.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                cfg_own = None
            if isinstance(cfg_own, dict):
                cfg_cd = cfg_own.get("project_git_common_dir")
                if repo_cd:
                    if isinstance(cfg_cd, str) and cfg_cd.strip():
                        if _norm_key(cfg_cd.strip()) != _norm_key(repo_cd):
                            problems.append(f"director_work.json project_git_common_dir ({cfg_cd}) does not match --repo {rp.as_posix()}")
                    else:
                        shadows.append("director_work.json missing project_git_common_dir (legacy binding; re-run agy_setup.py --apply to migrate)")
                cfg_wt = cfg_own.get("director_worktree")
                if isinstance(cfg_wt, str) and cfg_wt.strip():
                    wt_cd = _git_common_dir(cfg_wt.strip()) if pathlib.Path(cfg_wt.strip()).is_dir() else None
                    same_wt_cd = bool(wt_cd and repo_cd and _norm_key(wt_cd) == _norm_key(repo_cd))
                    if _norm_key(cfg_wt.strip()) not in repo_keys and not same_wt_cd:
                        problems.append(f"director_work.json director_worktree ({cfg_wt}) does not match --repo {rp.as_posix()}")

        hooks_fp = rp / ".agents" / "hooks.json"
        if not hooks_fp.is_file():
            shadows.append("workspace missing .agents/hooks.json (run agy_setup.py --apply)")
        else:
            try:
                hdata = json.loads(hooks_fp.read_text(encoding="utf-8-sig"))
                h_probs, pre_cmd = validate_hooks_structure(hdata)
                problems.extend(h_probs)
                setup_state_fp = d / ".agydw_setup_state.json"
                if not h_probs and setup_state_fp.is_file() and pre_cmd:
                    try:
                        sstate = json.loads(setup_state_fp.read_text(encoding="utf-8-sig"))
                    except (OSError, ValueError):
                        sstate = None
                    if isinstance(sstate, dict):
                        want_mode = sstate.get("guard_mode")
                        if want_mode and f"--mode {want_mode}" not in pre_cmd:
                            problems.append(f".agents/hooks.json guard mode drifted from {setup_state_fp.name} (expected --mode {want_mode})")
                        if sstate.get("audit_log") and str(sstate["audit_log"]) not in pre_cmd:
                            problems.append(f".agents/hooks.json audit log drifted from {setup_state_fp.name} ({sstate['audit_log']})")
                        if sstate.get("enforce_resume"):
                            stop_cmds, _ = _extract_hook_entries((hdata.get("agydw-audit-and-bridge") or {}).get("Stop"), need_matcher=False)
                            if not any("--enforce-resume" in c for c in stop_cmds):
                                problems.append(f".agents/hooks.json Stop hook missing --enforce-resume expected by {setup_state_fp.name}")
            except (OSError, ValueError) as exc:
                problems.append(f".agents/hooks.json invalid JSON: {exc}")

        plug_fp = rp / ".agents" / "plugins.json"
        if not plug_fp.is_file():
            shadows.append("workspace missing .agents/plugins.json (run agy_setup.py --apply)")
        else:
            try:
                pdata = json.loads(plug_fp.read_text(encoding="utf-8-sig"))
                entries = pdata.get("entries") if isinstance(pdata, dict) else None
                if not isinstance(entries, list):
                    problems.append(".agents/plugins.json missing 'entries' array")
                else:
                    found_dw = False
                    for ent in entries:
                        if not isinstance(ent, dict) or not isinstance(ent.get("path"), str):
                            continue
                        ep = pathlib.Path(ent["path"])
                        if not ep.is_absolute():
                            ep = (rp / ep).resolve()
                        if (ep / "director-work" / "plugin.json").is_file() or (ep.name == "director-work" and (ep / "plugin.json").is_file()):
                            found_dw = True
                            break
                    if not found_dw:
                        shadows.append(".agents/plugins.json does not register 'director-work' plugin (run agy_setup.py --apply)")
            except (OSError, ValueError) as exc:
                problems.append(f".agents/plugins.json invalid JSON: {exc}")

    return problems, shadows, notes


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
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
