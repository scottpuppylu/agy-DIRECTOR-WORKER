#!/usr/bin/env python3
"""Verify that an Antigravity DIRECTOR folder, `.agent/rules/`, and `.agents/` setup are valid (3.0.0-agy.7)."""
import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _load_common():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_agy_chk_common", HERE / "agy_common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_COMMON = _load_common()

CORE_PRETOOL_TOOLS = (
    "run_command", "read_file", "view_file", "write_file", "write_to_file",
    "edit_file", "replace_file_content", "grep_search", "find_by_name",
    "call_mcp_tool", "mcp_files_trash_file",
)
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
    return _COMMON.load_sibling(HERE, name, prefix="_agy_chk_")


def _norm_key(p):
    return _COMMON.norm_path_key(p)


def _git_common_dir(repo):
    info = _COMMON.find_repo(repo)
    return info.common_dir if info is not None else None


def _matcher_covers_core(matcher, require_all_unknown=False):
    if not isinstance(matcher, str) or not matcher.strip():
        return False, "empty matcher"
    if require_all_unknown and matcher.strip() not in ("*", ".*"):
        return False, f"spec gate requires wildcard matcher '*', got {matcher!r}"
    pat = ".*" if matcher.strip() == "*" else matcher
    try:
        rx = re.compile(pat)
    except re.error as exc:
        return False, f"invalid matcher regex {matcher!r}: {exc}"
    uncovered = [t for t in CORE_PRETOOL_TOOLS if not rx.search(t)]
    if uncovered:
        return False, f"matcher {matcher!r} misses core tool(s): {', '.join(uncovered)}"
    return True, None


def _extract_hook_entries(entries, need_matcher=False, require_wildcard=False):
    cmds, matcher_issues = [], []
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
        active = [hk["command"].strip() for hk in inner if isinstance(hk, dict) and hk.get("enabled") is not False and isinstance(hk.get("command"), str) and hk["command"].strip()]
        if not active:
            continue
        if need_matcher:
            ok_m, m_err = _matcher_covers_core(blk.get("matcher"), require_all_unknown=require_wildcard)
            if not ok_m:
                matcher_issues.append(m_err)
                continue
        cmds.extend(active)
    return cmds, matcher_issues


def validate_hooks_structure(hdata, beginner=False):
    problems = []
    pretool_cmd = None
    spec_gate_cmd = None
    if not isinstance(hdata, dict):
        return [".agents/hooks.json must be a JSON object"], None, None

    sg_grp = hdata.get("agydw-spec-gate")
    if not isinstance(sg_grp, dict):
        problems.append(".agents/hooks.json missing 'agydw-spec-gate' group (R6)")
    elif sg_grp.get("enabled") is False:
        problems.append(".agents/hooks.json 'agydw-spec-gate' group is disabled")
    else:
        sg_pre = sg_grp.get("PreToolUse")
        if not isinstance(sg_pre, list) or not sg_pre:
            problems.append(".agents/hooks.json 'agydw-spec-gate' missing PreToolUse")
        else:
            s_cmds, sm_errs = _extract_hook_entries(sg_pre, need_matcher=True, require_wildcard=True)
            matches = [c for c in s_cmds if "spec_gate_hook.py" in c]
            if not matches:
                msg = ".agents/hooks.json 'agydw-spec-gate' missing active wildcard PreToolUse hook running spec_gate_hook.py"
                if sm_errs:
                    msg += f" ({'; '.join(sm_errs)})"
                problems.append(msg)
            else:
                spec_gate_cmd = matches[0]

    guard_grp = hdata.get("agydw-guard")
    if not isinstance(guard_grp, dict):
        problems.append(".agents/hooks.json missing 'agydw-guard' group")
    elif guard_grp.get("enabled") is False:
        problems.append(".agents/hooks.json 'agydw-guard' group is disabled")
    else:
        pre = guard_grp.get("PreToolUse")
        if not isinstance(pre, list) or not pre:
            problems.append(".agents/hooks.json 'agydw-guard' missing PreToolUse list")
        else:
            g_cmds, m_errs = _extract_hook_entries(pre, need_matcher=True)
            matches = [c for c in g_cmds if "agy_guard.py" in c]
            if not matches:
                msg = ".agents/hooks.json 'agydw-guard' missing active PreToolUse hook"
                if m_errs:
                    msg += f" ({'; '.join(m_errs)})"
                problems.append(msg)
            else:
                pretool_cmd = matches[0]
                has_hard_only = bool(re.search(r"(?:^|\s)--hard-only(?:\s|$)", pretool_cmd))
                if beginner and not has_hard_only:
                    problems.append(".agents/hooks.json 'agydw-guard' in --beginner mode must include --hard-only")
                elif not beginner and has_hard_only:
                    problems.append(".agents/hooks.json 'agydw-guard' in general mode must not pass --hard-only")

    bridge_grp = hdata.get("agydw-audit-and-bridge")
    if not isinstance(bridge_grp, dict):
        problems.append(".agents/hooks.json missing 'agydw-audit-and-bridge' group")
    elif bridge_grp.get("enabled") is False:
        problems.append(".agents/hooks.json 'agydw-audit-and-bridge' group is disabled")
    else:
        post = bridge_grp.get("PostToolUse")
        if not isinstance(post, list) or not post:
            problems.append(".agents/hooks.json 'agydw-audit-and-bridge' missing PostToolUse list")
        else:
            b_cmds, bm_errs = _extract_hook_entries(post, need_matcher=True)
            if not any("agy_hook_bridge.py" in c and "--audit-log" in c for c in b_cmds):
                msg = ".agents/hooks.json PostToolUse missing unified agy_hook_bridge.py --audit-log"
                if bm_errs:
                    msg += f" ({'; '.join(bm_errs)})"
                problems.append(msg)
        for ev in ("SessionStart", "PreInvocation", "Stop"):
            ev_list = bridge_grp.get(ev)
            ev_cmds, _ = _extract_hook_entries(ev_list, need_matcher=False) if isinstance(ev_list, list) else ([], [])
            if not any("agy_hook_bridge.py" in c for c in ev_cmds):
                problems.append(f".agents/hooks.json 'agydw-audit-and-bridge' missing active {ev} hook")

    return problems, pretool_cmd, spec_gate_cmd


def _split_cmd_tokens(cmd_str):
    return _COMMON.split_cmd_tokens(cmd_str)


def _extract_opt(tokens, flag):
    return _COMMON.extract_opt(tokens, flag)


def check_director(dpath, repo_path=None, beginner=None):
    problems, shadows, notes = [], [], []
    d = pathlib.Path(dpath)
    if d.is_file() and d.name == "DIRECTOR_STATE.md":
        d = d.parent
    if not d.is_dir():
        return [f"missing DIRECTOR folder: {d}"], [], []

    for fname in REQUIRED_FILES:
        if not (d / fname).is_file():
            problems.append(f"missing required file: {fname}")
    if not (d / "INDEX.md").is_file():
        shadows.append("missing INDEX.md (needed for handoff.py cross-variant resume)")

    state_file = d / "DIRECTOR_STATE.md"
    if state_file.is_file():
        st = state_file.read_text(encoding="utf-8-sig", errors="replace")
        pm = re.search(r"^[ \t]*PENDING_METHOD[ \t]*:[ \t]*(.*)$", st, re.M)
        if not pm or not pm.group(1).strip() or PLACEHOLDER.match(pm.group(1).strip()):
            problems.append("DIRECTOR_STATE.md missing filled PENDING_METHOD line")
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
                shadows.append(f"director_work.json mode={cfg.get('mode')!r}")
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
                    shadows.append(f"RESUME_SIZE over ({', '.join(over)}): {facts['bytes']} bytes")

    setup_state_fp = d / ".agydw_setup_state.json"
    sstate = None
    if setup_state_fp.is_file():
        try:
            sstate = json.loads(setup_state_fp.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            sstate = None
    is_beginner = bool(beginner) if beginner is not None else bool((sstate or {}).get("beginner", False))

    if (d / "tools" / "INSTALL_MANIFEST.json").is_file():
        try:
            inst = load_sibling("install")
            rec = inst.read_record(d / "tools")
            if not rec:
                shadows.append("DIRECTOR/tools/INSTALL_MANIFEST.json unreadable")
            else:
                for entry in rec.get("files", []):
                    fp = d / "tools" / entry["path"]
                    if not fp.is_file() or hashlib.sha256(fp.read_bytes()).hexdigest() != entry["sha256"]:
                        problems.append(f"DIRECTOR/tools/{entry['path']} drifted from INSTALL_MANIFEST.json")
        except Exception as exc:
            shadows.append(f"could not check DIRECTOR/tools manifest: {exc}")

    ad_man = d / "adapters" / "antigravity" / "AGYDW_ADAPTER_MANIFEST.json"
    if ad_man.is_file():
        try:
            adata = json.loads(ad_man.read_text(encoding="utf-8-sig"))
            for rel, want_sha in (adata.get("files") or {}).items():
                fp = ad_man.parent / rel
                if not fp.is_file() or hashlib.sha256(fp.read_bytes()).hexdigest() != want_sha:
                    problems.append(f"DIRECTOR/adapters/antigravity/{rel} drifted from manifest")
        except (OSError, ValueError) as exc:
            shadows.append(f"corrupt adapter manifest: {exc}")

    if repo_path:
        rp = pathlib.Path(repo_path).resolve()
        repo_cd = _git_common_dir(rp)
        repo_primary = repo_cd.parent.resolve() if (repo_cd and repo_cd.name == ".git") else rp
        repo_keys = {_norm_key(rp), _norm_key(repo_primary)}

        if repo_cd and _norm_key(d).startswith(_norm_key(repo_primary) + "/"):
            chk = subprocess.run(["git", "-C", str(rp), "check-ignore", "-q", d.relative_to(repo_primary).as_posix()], capture_output=True)
            if chk.returncode == 1:
                shadows.append(f"in-project {d.name}/ is not git-ignored yet (run agy_setup.py --apply or bootstrap_repo.py)")

        rules_md = rp / ".agent" / "rules" / "director-work.md"
        if not rules_md.is_file():
            shadows.append("workspace missing .agent/rules/director-work.md (run agy_setup.py --apply)")
        elif rules_md.stat().st_size > 24000:
            problems.append(f".agent/rules/director-work.md exceeds 24,000 bytes ({rules_md.stat().st_size})")

        if state_file.is_file():
            st_txt = state_file.read_text(encoding="utf-8-sig", errors="replace")
            m_pr = re.search(r"^[ \t]*PROJECT_REPO[ \t]*:[ \t]*(.+)$", st_txt, re.M)
            if m_pr and m_pr.group(1).strip():
                pr_val = m_pr.group(1).strip()
                pr_cd = _git_common_dir(pr_val) if pathlib.Path(pr_val).is_dir() else None
                same_cd = bool(pr_cd and repo_cd and _norm_key(pr_cd) == _norm_key(repo_cd))
                if _norm_key(pr_val) not in repo_keys and not same_cd:
                    problems.append(f"DIRECTOR_STATE.md PROJECT_REPO ({pr_val}) does not match --repo {rp.as_posix()}")

        hooks_fp = rp / ".agents" / "hooks.json"
        if not hooks_fp.is_file():
            shadows.append("workspace missing .agents/hooks.json (run agy_setup.py --apply)")
        else:
            try:
                hdata = json.loads(hooks_fp.read_text(encoding="utf-8-sig"))
                h_probs, pre_cmd, sg_cmd = validate_hooks_structure(hdata, beginner=is_beginner)
                problems.extend(h_probs)
                if not h_probs and isinstance(sstate, dict) and pre_cmd:
                    want_mode = sstate.get("guard_mode")
                    if want_mode and f"--mode {want_mode}" not in pre_cmd:
                        problems.append(f".agents/hooks.json guard mode drifted from {setup_state_fp.name} (expected --mode {want_mode})")
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
                        shadows.append(".agents/plugins.json does not register 'director-work' plugin")
            except (OSError, ValueError) as exc:
                problems.append(f".agents/plugins.json invalid JSON: {exc}")

    return problems, shadows, notes


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directors", nargs="+", metavar="DIRECTOR")
    ap.add_argument("--repo")
    ap.add_argument("--beginner", action="store_true", default=None)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    results, any_fail = [], False
    for d in a.directors:
        probs, shadows, notes = check_director(d, a.repo, beginner=a.beginner)
        failed = bool(probs) or (a.strict and bool(shadows))
        any_fail = any_fail or failed
        results.append({"director": str(d), "ok": not failed, "problems": probs, "shadows": shadows, "notes": notes})

    if a.json:
        print(json.dumps({"ok": not any_fail, "strict": a.strict, "results": results}, indent=2, ensure_ascii=False))
    else:
        for r in results:
            print(f"{'OK  ' if r['ok'] else 'FAIL'} {r['director']}")
            for p in r["problems"]:
                print(f"  - problem: {p}")
            for s in r["shadows"]:
                print(f"  {'- problem:' if a.strict else '~ shadow:'} {s}")
            for n in r["notes"]:
                print(f"  ~ note: {n}")
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
