#!/usr/bin/env python3
"""Workspace bootstrap helper for agy-DIRECTOR-WORKER (agydw). Dry-run by default; --apply writes.

Idempotently wires a project repository for Antigravity Director/Worker governance WITHOUT
modifying user-global ~/.gemini/config/config.json:
  1. Writes <REPO>/.agents/hooks.json (PreToolUse -> agy_guard.py, PostToolUse -> agy_audit.py
     + agy_hook_bridge.py, SessionStart -> agy_hook_bridge.py, PreInvocation -> agy_hook_bridge.py,
     Stop -> agy_hook_bridge.py).
  2. Writes <REPO>/.agents/plugins.json referencing <RULES>/plugin so Antigravity auto-loads
     the `director-work` plugin for this project.
  3. Adds local-only paths (`AGENTS.local.md`, `.agents/hooks.local.json`, `_work/`) into
     <REPO>/.git/info/exclude (does not edit .gitignore without PO approval).
  4. Optional --init-director: scaffolds <DIRECTOR>/ files from <RULES>/templates/ and copies
     tools/ (unless --no-copy-tools).

Usage:
  python -I -X utf8 agy_setup.py --repo <REPO> [--director <WS>/DIRECTOR] [--rules <RULES>] [--apply]
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCAL_EXCLUDES = ("AGENTS.local.md", ".agents/hooks.local.json", "_work/")


def git_dir(repo):
    """Resolve <repo>/.git directory (handles linked git worktrees)."""
    dot = pathlib.Path(repo) / ".git"
    if dot.is_dir():
        return dot
    if dot.is_file():
        m = re.match(r"gitdir:\s*(.+)", dot.read_text(encoding="utf-8", errors="replace").strip())
        if m:
            p = pathlib.Path(m.group(1).strip())
            return p if p.is_absolute() else (pathlib.Path(repo) / p).resolve()
    return None


def q(path_like):
    """Format an absolute forward-slash path cleanly; quote only when whitespace or shell metachars exist."""
    s = pathlib.Path(path_like).resolve().as_posix()
    return s if re.fullmatch(r"[A-Za-z0-9_./:{}-]+", s) else f'"{s}"'


def build_hooks_config(args, rules_root, director_dir):
    """Build Antigravity .agents/hooks.json dictionary with absolute Python & script paths."""
    py = q(sys.executable)
    if os.name == "nt" and py.startswith('"'):
        py = f"call {py}"
    tools_dir = (director_dir / "tools") if (director_dir and (director_dir / "tools" / "agy_guard.py").is_file()) else (rules_root / "tools")
    guard = q(tools_dir / "agy_guard.py")
    rules_json = q(tools_dir / "guard_rules.json")
    audit = q(tools_dir / "agy_audit.py")
    bridge = q(tools_dir / "agy_hook_bridge.py")

    state_dir = q(args.bridge_state or (director_dir / ".bridge_state" if director_dir else pathlib.Path(args.repo) / ".agents" / ".bridge_state"))
    resume_path = q(args.resume_file or (director_dir / "RESUME_STATE.md" if director_dir else pathlib.Path(args.repo) / "RESUME_STATE.md"))
    audit_log = q(args.audit_log or (director_dir / "audit" / "{worktree}.jsonl" if director_dir else pathlib.Path(args.repo) / ".agents" / "audit_{worktree}.jsonl"))

    guard_parts = [f"{py} -B -I -X utf8 {guard} --rules {rules_json} --profile {args.profile} --mode {args.guard_mode}"]
    if director_dir:
        guard_parts.append(f"--protect {q(director_dir)}")
    guard_parts.append(f"--audit {audit_log}")
    guard_parts.append(f"--bridge-state {state_dir}")
    if args.overwrite_utf8:
        guard_parts.append("--overwrite-utf8")
    guard_cmd = " ".join(guard_parts)

    post_audit_cmd = f"{py} -B -I -X utf8 {audit} hook-append --log {audit_log}"
    post_bridge_cmd = f"{py} -B -I -X utf8 {bridge} post-tool --state-dir {state_dir} --inject-scan"
    session_cmd = f"{py} -B -I -X utf8 {bridge} session-start --resume {resume_path} --state-dir {state_dir} --banner"
    pre_inv_cmd = f"{py} -B -I -X utf8 {bridge} pre-invocation --state-dir {state_dir} --resume {resume_path}"
    stop_cmd = f"{py} -B -I -X utf8 {bridge} stop-check --resume {resume_path} --repo {q(args.repo)}" + (" --enforce-resume" if args.enforce_resume else "")

    return {
        "agydw-guard": {
            "PreToolUse": [
                {
                    "matcher": "run_command|read_file|view_file|write_file|write_to_file|edit_file|replace_file_content|multi_replace_file_content|grep_search|find_by_name|list_dir|send_message|post_message",
                    "hooks": [{"type": "command", "command": guard_cmd, "timeout": 15}],
                }
            ]
        },
        "agydw-audit-and-bridge": {
            "SessionStart": [{"type": "command", "command": session_cmd, "timeout": 10}],
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {"type": "command", "command": post_audit_cmd, "timeout": 15},
                        {"type": "command", "command": post_bridge_cmd, "timeout": 15},
                    ],
                }
            ],
            "PreInvocation": [{"type": "command", "command": pre_inv_cmd, "timeout": 10}],
            "Stop": [{"type": "command", "command": stop_cmd, "timeout": 10}],
        },
    }


def build_plugins_config(plugin_parent_dir):
    """Build Antigravity .agents/plugins.json pointing to <RULES>/plugin."""
    return {
        "entries": [
            {"path": pathlib.Path(plugin_parent_dir).resolve().as_posix()}
        ]
    }


def fill_placeholders(text, rules_root, repo, director_dir):
    """Replace common scaffold placeholders lightly without clobbering user fields."""
    out = text.replace("<RULES>", rules_root.as_posix())
    out = out.replace("<REPO>", repo.as_posix())
    if director_dir:
        out = out.replace("<DIRECTOR>", director_dir.as_posix())
    return out


def init_director(director_dir, rules_root, repo, apply, copy_tools):
    """Scaffold DIRECTOR/ folder from templates/."""
    actions = []
    subdirs = ("orders", "reviews", "research", "evidence", "audit")
    for sd in subdirs:
        p = director_dir / sd
        if not p.is_dir():
            actions.append(f"CREATE dir {p.as_posix()}")
            if apply:
                p.mkdir(parents=True, exist_ok=True)

    tpl_dir = rules_root / "templates"
    files_tpl = tpl_dir / "DIRECTOR_FILES.md"
    # Map standard files from individual templates
    file_map = {
        "RESUME_STATE.md": tpl_dir / "RESUME_STATE.md",
        "PO_PENDING.md": tpl_dir / "PO_PENDING.md",
        "DECISION_REGISTER.md": tpl_dir / "DECISION_REGISTER.md",
    }
    for fname, src in file_map.items():
        dst = director_dir / fname
        if not dst.exists() and src.is_file():
            actions.append(f"WRITE {dst.as_posix()} (from {src.name})")
            if apply:
                dst.parent.mkdir(parents=True, exist_ok=True)
                content = fill_placeholders(src.read_text(encoding="utf-8-sig"), rules_root, repo, director_dir)
                dst.write_text(content, encoding="utf-8", newline="\n")

    # Minimal valid defaults for remaining director files if absent
    static_defaults = {
        "DIRECTOR_OPERATING_RULES.md": (
            f"# 本專案總監作業規則\n\n"
            f"- 規則包版本：2.6.0-agy.2（位置：`{rules_root.as_posix()}`）\n"
            f"- 本專案（`{repo.as_posix()}`）既有 `AGENTS.md` / `GEMINI.md` 與 PO 指示永遠優先。\n"
            f"- 其餘未列事項照 `{rules_root.as_posix()}/docs/` 01 至 13 執行。\n"
        ),
        "DIRECTOR_STATE.md": (
            f"# DIRECTOR_STATE\n\n"
            f"PROJECT_REPO: {repo.as_posix()}\n"
            f"RULES_PATH: {rules_root.as_posix()}\n"
            f"PENDING_METHOD: ask_question + PO_PENDING.md（圖片或成品對照可選 Artifacts / generative_ui；worker 經總監回報）\n"
            f"STAGE: BOOTSTRAP\n"
        ),
        "ACTIVE_WORKSTREAMS.md": (
            "# ACTIVE_WORKSTREAMS\n\n"
            "| 名稱 | 模式 | 狀態 | 工單 | RESUME_STATE | 模型 / Effort | 備註 |\n"
            "|---|---|---|---|---|---|---|\n"
        ),
        "RISK_REGISTER.md": (
            "# RISK_REGISTER\n\n"
            "| ID | 風險 | 影響 | 對策 | 負責人 |\n"
            "|---|---|---|---|---|\n"
        ),
        "DIRECTOR_LOG.md": "# DIRECTOR_LOG\n\n",
        "GUARDS.md": (
            "# GUARDS\n\n"
            "| 機制 | 擁有者 | 中斷影響 | 檢查指令 | 重建指令 |\n"
            "|---|---|---|---|---|\n"
            "| Hooks (`agy_guard` + `agy_audit` + `agy_hook_bridge`) | 總監 / PO | 失去工具層自動攔截與提醒 | `python -I -X utf8 tools/agy_doctor.py --repo <REPO>` | `python -I -X utf8 tools/agy_setup.py --repo <REPO> --apply` |\n"
        ),
        "BASELINE_METRICS.md": "# BASELINE_METRICS\n\n基準尚未量測：請跑 `agy_usage.py` 與 `memory_size_check.py --kind rules`。\n",
    }
    for fname, content in static_defaults.items():
        dst = director_dir / fname
        if not dst.exists():
            actions.append(f"WRITE {dst.as_posix()}")
            if apply:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(content, encoding="utf-8", newline="\n")

    cfg_dst = director_dir / "director_work.json"
    cfg_src = rules_root / "director_work.example.json"
    if not cfg_dst.exists() and cfg_src.is_file():
        actions.append(f"WRITE {cfg_dst.as_posix()}")
        if apply:
            cfg = json.loads(cfg_src.read_text(encoding="utf-8-sig"))
            cfg["protect_director"] = director_dir.as_posix()
            cfg["director_worktree"] = repo.as_posix()
            cfg_dst.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    if copy_tools:
        dst_tools = director_dir / "tools"
        if not dst_tools.exists():
            actions.append(f"COPY tools/ -> {dst_tools.as_posix()}")
            if apply:
                shutil.copytree(rules_root / "tools", dst_tools, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "fixtures"))
    return actions


def _norm_path_key(path_str):
    try:
        return os.path.normcase(pathlib.Path(path_str).resolve().as_posix())
    except (OSError, ValueError):
        return os.path.normcase(str(path_str).replace("\\", "/"))


def merge_hooks_config(hooks_file, hooks_obj):
    """Non-destructively merge agydw hook groups into existing .agents/hooks.json."""
    try:
        existing = json.loads(hooks_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot parse existing {hooks_file.as_posix()}: {exc}") from exc
    if not isinstance(existing, dict):
        raise ValueError(f"existing {hooks_file.as_posix()} must be a JSON object, got {type(existing).__name__}")
    if "hooks" in existing and isinstance(existing.get("hooks"), dict) and "agydw-guard" not in existing:
        raise ValueError(f"existing {hooks_file.as_posix()} uses Claude-style 'hooks' wrapper; convert or pass --force")
    preserved = [k for k in existing if k not in hooks_obj]
    merged = dict(existing)
    for k, v in hooks_obj.items():
        merged[k] = v
    changed = (merged != existing)
    return merged, len(preserved), changed


def merge_plugins_config(plugins_file, plugin_parent):
    """Non-destructively append <RULES>/plugin entry to existing .agents/plugins.json."""
    try:
        existing = json.loads(plugins_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot parse existing {plugins_file.as_posix()}: {exc}") from exc
    if not isinstance(existing, dict):
        raise ValueError(f"existing {plugins_file.as_posix()} must be a JSON object, got {type(existing).__name__}")
    entries = existing.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError(f"existing {plugins_file.as_posix()} 'entries' must be a JSON array")

    target_str = pathlib.Path(plugin_parent).resolve().as_posix()
    want_keys = {_norm_path_key(target_str), _norm_path_key(pathlib.Path(plugin_parent) / "director-work")}
    already = any(
        isinstance(e, dict) and isinstance(e.get("path"), str) and _norm_path_key(e["path"]) in want_keys
        for e in entries
    )
    merged = dict(existing)
    merged_entries = list(entries)
    if not already:
        merged_entries.append({"path": target_str})
    merged["entries"] = merged_entries
    return merged, len(entries), (not already)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="target project git repository")
    ap.add_argument("--director", help="DIRECTOR folder path (default: <REPO>/../DIRECTOR)")
    ap.add_argument("--rules", default=str(HERE.parent), help="path to agy-DIRECTOR-WORKER repo (default: parent of tools/)")
    ap.add_argument("--plugin", help="plugin parent folder for .agents/plugins.json (default: <RULES>/plugin)")
    ap.add_argument("--guard-mode", choices=("remind", "ask", "block"), default="remind")
    ap.add_argument("--profile", choices=("worker", "director"), default="worker")
    ap.add_argument("--audit-log", help="override action audit log path")
    ap.add_argument("--resume-file", help="override RESUME_STATE.md path")
    ap.add_argument("--bridge-state", help="override hook bridge state directory")
    ap.add_argument("--enforce-resume", action="store_true", help="enable Stop hook RESUME_STATE freshness guard")
    ap.add_argument("--overwrite-utf8", action="store_true", help="opt-in PreToolUse overwrite for python -I -X utf8")
    ap.add_argument("--init-director", action="store_true", help="scaffold missing DIRECTOR/ files from templates")
    ap.add_argument("--no-copy-tools", action="store_true", help="do not copy tools/ into DIRECTOR/ during --init-director")
    ap.add_argument("--force", action="store_true", help="replace whole .agents/hooks.json / plugins.json instead of merging")
    ap.add_argument("--apply", action="store_true", help="write files (default: dry-run)")
    try:
        a = ap.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    repo = pathlib.Path(a.repo).resolve()
    rules_root = pathlib.Path(a.rules).resolve()
    director_dir = pathlib.Path(a.director).resolve() if a.director else (repo.parent / "DIRECTOR")
    plugin_parent = pathlib.Path(a.plugin).resolve() if a.plugin else (rules_root / "plugin")

    if not repo.is_dir():
        print(f"AGY_SETUP: FAIL repo directory not found: {repo}", file=sys.stderr)
        return 2

    gd = git_dir(repo)
    actions = []

    # 1. Git exclude check
    if gd is not None:
        excl = gd / "info" / "exclude"
        existing = excl.read_text(encoding="utf-8", errors="replace") if excl.is_file() else ""
        have = {line.strip() for line in existing.splitlines()}
        missing = [item for item in LOCAL_EXCLUDES if item not in have and item.rstrip("/") not in have]
        if missing:
            actions.append(f"APPEND {excl.as_posix()}: {', '.join(missing)}")
            if a.apply:
                excl.parent.mkdir(parents=True, exist_ok=True)
                prefix = "" if (not existing or existing.endswith("\n")) else "\n"
                with open(excl, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(prefix + "\n".join(missing) + "\n")
    else:
        actions.append(f"NOTE {repo.as_posix()} is not a git repo; skipped .git/info/exclude")

    # 2. Scaffold DIRECTOR/ if requested
    if a.init_director:
        actions += init_director(director_dir, rules_root, repo, a.apply, not a.no_copy_tools)

    # 3. .agents/hooks.json (non-destructive merge when existing unless --force)
    agents_dir = repo / ".agents"
    hooks_file = agents_dir / "hooks.json"
    hooks_obj = build_hooks_config(a, rules_root, director_dir if (a.director or a.init_director or director_dir.is_dir()) else None)
    if not hooks_file.exists() or a.force:
        actions.append(f"WRITE {hooks_file.as_posix()}")
        if a.apply:
            agents_dir.mkdir(parents=True, exist_ok=True)
            hooks_file.write_text(json.dumps(hooks_obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    else:
        try:
            merged_hooks, n_preserved, changed = merge_hooks_config(hooks_file, hooks_obj)
        except ValueError as exc:
            print(f"AGY_SETUP: FAIL {exc}", file=sys.stderr)
            return 1
        if changed:
            actions.append(f"MERGE {hooks_file.as_posix()} (preserved {n_preserved} existing group(s))")
            if a.apply:
                hooks_file.write_text(json.dumps(merged_hooks, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        else:
            actions.append(f"OK {hooks_file.as_posix()} (already up to date; preserved {n_preserved} existing group(s))")

    # 4. .agents/plugins.json (non-destructive merge when existing unless --force)
    plugins_file = agents_dir / "plugins.json"
    if plugin_parent.is_dir():
        plugins_obj = build_plugins_config(plugin_parent)
        if not plugins_file.exists() or a.force:
            actions.append(f"WRITE {plugins_file.as_posix()} -> {plugin_parent.as_posix()}")
            if a.apply:
                agents_dir.mkdir(parents=True, exist_ok=True)
                plugins_file.write_text(json.dumps(plugins_obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        else:
            try:
                merged_plugins, n_entries, changed = merge_plugins_config(plugins_file, plugin_parent)
            except ValueError as exc:
                print(f"AGY_SETUP: FAIL {exc}", file=sys.stderr)
                return 1
            if changed:
                actions.append(f"MERGE {plugins_file.as_posix()} -> {plugin_parent.as_posix()} (preserved {n_entries} existing entry/entries)")
                if a.apply:
                    plugins_file.write_text(json.dumps(merged_plugins, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
            else:
                actions.append(f"OK {plugins_file.as_posix()} (already registered; preserved {n_entries} existing entry/entries)")

    for act in actions:
        print(("APPLY " if a.apply else "PLAN  ") + act)
    print(f"AGY_SETUP: {'DONE' if a.apply else 'DRY_RUN'} steps={len(actions)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
