#!/usr/bin/env python3
"""Workspace bootstrap & upgrade helper for agy-DIRECTOR-WORKER (3.0.0-agy.7). Dry-run by default; --apply writes.

Places the project's `DIRECTOR/` folder INSIDE `<PRIMARY_REPO>/DIRECTOR/` (item R3),
git-ignores it via shared `.git/info/exclude` + `DIRECTOR/.gitignore`, deploys
`<REPO>/.agent/rules/director-work.md`, installs frozen base `tools/` via `tools/install.py`
into `DIRECTOR/tools/`, copies & SHA-256 verifies `adapters/antigravity/` into
`DIRECTOR/adapters/antigravity/`, and wires `.agents/hooks.json` + `.agents/plugins.json`.

In BOTH general mode and `--beginner` mode, installs `agydw-spec-gate` (`spec_gate_hook.py`, R6)
on PreToolUse (`matcher: "*"`) without caching. General mode also wires `agydw-guard` and
the unified `agydw-audit-and-bridge`.
"""
import argparse
import contextlib
import datetime
import hashlib
import json
import os
import pathlib
import random
import re
import shlex
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent


def _load_common():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_agy_setup_common", HERE / "agy_common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_COMMON = _load_common()

BASE_LOCAL_EXCLUDES = ("AGENTS.local.md", ".agents/hooks.local.json", "_work/", ".claude/settings.local.json", "DIRECTOR/")
AGENTS_LOCAL_EXCLUDES = (".agents/hooks.json", ".agents/plugins.json")
ADAPTER_MANIFEST_NAME = "AGYDW_ADAPTER_MANIFEST.json"
SETUP_STATE_NAME = ".agydw_setup_state.json"


def read_version(rules_root):
    vf = pathlib.Path(rules_root) / "VERSION"
    if vf.is_file():
        txt = vf.read_text(encoding="utf-8-sig", errors="replace").strip()
        if txt:
            return txt
    return "3.0.0-agy.7"


def _norm_path_key(path_str):
    return _COMMON.norm_path_key(path_str)


@contextlib.contextmanager
def file_lock(lock_path, wait_s=15.0, stale_s=30.0):
    lp = pathlib.Path(lock_path)
    lp.parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}:{time.time_ns()}:{random.randint(100000, 999999)}\n"
    end = time.monotonic() + wait_s
    while True:
        try:
            fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, token.encode("ascii"))
            finally:
                os.close(fd)
            break
        except (FileExistsError, PermissionError):
            try:
                if lp.exists() and time.time() - lp.stat().st_mtime > stale_s:
                    lp.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= end:
                raise RuntimeError(
                    f"timed out waiting for lock {lp.as_posix()} after {wait_s:g}s; "
                    "verify no other agy_setup process is running, then re-run"
                )
            time.sleep(0.015 + random.random() * 0.025)
    try:
        yield
    finally:
        try:
            if lp.is_file() and lp.read_text(encoding="ascii", errors="replace") == token:
                lp.unlink(missing_ok=True)
        except OSError:
            pass


def git_dir(repo):
    return _COMMON.git_dir(repo)


def git_common_dir(repo):
    info = _COMMON.find_repo(repo)
    return info.common_dir if info is not None else None


def git_exclude_file(repo):
    return _COMMON.exclude_file(repo)


def primary_repo_root(repo):
    rp = pathlib.Path(repo).resolve()
    cd = git_common_dir(rp)
    if cd is not None and cd.name == ".git":
        return cd.parent.resolve()
    return rp


def q(path_like):
    s = pathlib.Path(path_like).resolve().as_posix()
    if re.fullmatch(r"[A-Za-z0-9_./:{}-]+", s):
        return s
    return f'"{s}"' if os.name == "nt" else shlex.quote(s)


def _is_valid_rules_root(root):
    p = pathlib.Path(root)
    return (
        (p / "templates" / "RESUME_STATE.md").is_file()
        and (p / "tools" / "install.py").is_file()
        and (p / "adapters" / "antigravity" / "plugin" / "director-work" / "plugin.json").is_file()
    )


def resolve_rules_root(rules_arg, explicit_rules):
    if explicit_rules:
        cand = pathlib.Path(rules_arg).resolve()
        if _is_valid_rules_root(cand):
            return cand, None
        return None, f"--rules {cand.as_posix()} is missing templates/RESUME_STATE.md, tools/install.py, or adapters/antigravity/plugin/"

    candidates = [pathlib.Path(rules_arg).resolve(), HERE.parent.parent]
    manifest = HERE / ADAPTER_MANIFEST_NAME
    if manifest.is_file():
        try:
            mdata = json.loads(manifest.read_text(encoding="utf-8-sig"))
            if isinstance(mdata, dict) and isinstance(mdata.get("rules_root"), str):
                candidates.append(pathlib.Path(mdata["rules_root"]))
        except (OSError, ValueError):
            pass
    for probe_dir in (HERE.parent, HERE.parent.parent):
        st_f = probe_dir / "DIRECTOR_STATE.md"
        if st_f.is_file():
            m = re.search(r"^[ \t]*RULES_PATH[ \t]*:[ \t]*(.+)$", st_f.read_text(encoding="utf-8-sig", errors="replace"), re.M)
            if m and m.group(1).strip():
                candidates.append(pathlib.Path(m.group(1).strip()))

    seen = set()
    for c in candidates:
        key = _norm_path_key(c)
        if key in seen:
            continue
        seen.add(key)
        if _is_valid_rules_root(c):
            return c.resolve(), None
    return None, (
        f"cannot locate agy-DIRECTOR-WORKER rules root from {HERE.as_posix()}; pass --rules <AGYDW_REPO>"
    )


def director_owner_info(director_dir):
    d = pathlib.Path(director_dir)
    info = {"project_repo": None, "project_git_common_dir": None, "rules_path": None}
    cfg_p = d / "director_work.json"
    if cfg_p.is_file():
        try:
            cfg = json.loads(cfg_p.read_text(encoding="utf-8-sig"))
            if isinstance(cfg, dict):
                if isinstance(cfg.get("project_git_common_dir"), str) and cfg["project_git_common_dir"].strip():
                    info["project_git_common_dir"] = cfg["project_git_common_dir"].strip()
                if isinstance(cfg.get("director_worktree"), str) and cfg["director_worktree"].strip():
                    info["project_repo"] = cfg["director_worktree"].strip()
        except (OSError, ValueError):
            pass
    st_p = d / "DIRECTOR_STATE.md"
    if st_p.is_file():
        st = st_p.read_text(encoding="utf-8-sig", errors="replace")
        m_repo = re.search(r"^[ \t]*PROJECT_REPO[ \t]*:[ \t]*(.+)$", st, re.M)
        if m_repo and m_repo.group(1).strip() and not info["project_repo"]:
            info["project_repo"] = m_repo.group(1).strip()
        m_rules = re.search(r"^[ \t]*RULES_PATH[ \t]*:[ \t]*(.+)$", st, re.M)
        if m_rules and m_rules.group(1).strip():
            info["rules_path"] = m_rules.group(1).strip()
    return info


def director_matches_repo(director_dir, repo):
    info = director_owner_info(director_dir)
    owner_cd, owner_repo = info["project_git_common_dir"], info["project_repo"]
    if not owner_cd and not owner_repo:
        return True, None
    rp = pathlib.Path(repo).resolve()
    repo_key = _norm_path_key(rp)
    primary_key = _norm_path_key(primary_repo_root(rp))
    repo_cd = git_common_dir(rp)
    repo_cd_key = _norm_path_key(repo_cd) if repo_cd is not None else None
    if owner_cd and repo_cd_key:
        if _norm_path_key(owner_cd) == repo_cd_key:
            return True, None
        return False, owner_repo or owner_cd
    if owner_repo:
        if _norm_path_key(owner_repo) in (repo_key, primary_key):
            return True, None
        owner_p = pathlib.Path(owner_repo)
        if owner_p.is_dir() and repo_cd_key:
            owner_live_cd = git_common_dir(owner_p)
            if owner_live_cd is not None and _norm_path_key(owner_live_cd) == repo_cd_key:
                return True, None
        return False, owner_repo
    return True, None


def default_director_dir(repo):
    """R3: Per-project DIRECTOR/ lives directly inside `<PRIMARY_REPO>/DIRECTOR`."""
    return primary_repo_root(repo) / "DIRECTOR"


def compute_dir_hashes(src_dir):
    root = pathlib.Path(src_dir)
    hashes = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if "__pycache__" in rel.parts or "fixtures" in rel.parts or rel.name in (ADAPTER_MANIFEST_NAME, "INSTALL_MANIFEST.json") or p.suffix == ".pyc":
            continue
        hashes[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


def adapter_matches_manifest(folder, version_str, expected_hashes):
    d = pathlib.Path(folder)
    mp = d / ADAPTER_MANIFEST_NAME
    if not d.is_dir() or not mp.is_file():
        return False, "missing"
    try:
        mdata = json.loads(mp.read_text(encoding="utf-8-sig"))
        old_ver = str(mdata.get("version") or "unknown")
    except (OSError, ValueError):
        return False, "corrupt"
    if old_ver != version_str:
        return False, old_ver
    for rel_name, want_sha in expected_hashes.items():
        fp = d / rel_name
        if not fp.is_file() or hashlib.sha256(fp.read_bytes()).hexdigest() != want_sha:
            return False, old_ver
    return True, old_ver


def deploy_adapter_dir(target_dir, src_dir, rules_root, expected_hashes, ver):
    """Install/upgrade `DIRECTOR/adapters/antigravity/` via staging + backup swap + SHA-256 verify."""
    dst = pathlib.Path(target_dir)
    src = pathlib.Path(src_dir)
    dst.parent.mkdir(parents=True, exist_ok=True)
    staging = dst.parent / f".adapter_staging_{os.getpid()}_{random.randint(1000, 9999)}"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(src, staging, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "fixtures", ADAPTER_MANIFEST_NAME))
    for rel_name, want_sha in expected_hashes.items():
        sf = staging / rel_name
        if not sf.is_file() or hashlib.sha256(sf.read_bytes()).hexdigest() != want_sha:
            shutil.rmtree(staging, ignore_errors=True)
            raise RuntimeError(f"staged adapter integrity check failed for {rel_name}")
    manifest = {
        "schema_version": 1,
        "version": ver,
        "rules_root": pathlib.Path(rules_root).resolve().as_posix(),
        "installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "files": expected_hashes,
    }
    _COMMON.write_json_atomic(staging / ADAPTER_MANIFEST_NAME, manifest)
    backup = None
    if dst.exists():
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = dst.parent / f".adapter_backup_{ts}_{os.getpid()}"
        os.replace(dst, backup)
    try:
        os.replace(staging, dst)
    except OSError:
        if backup and backup.exists() and not dst.exists():
            os.replace(backup, dst)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if backup and backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def run_base_install(rules_root, tools_target, execute=False):
    """Delegate base `tools/` installation to frozen `tools/install.py`."""
    inst = pathlib.Path(rules_root) / "tools" / "install.py"
    cmd = [sys.executable, "-B", "-I", "-X", "utf8", str(inst), "install", "--to", str(tools_target), "--rules", str(rules_root)]
    if execute:
        cmd.append("--execute")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def build_sessions_config(sessions_path, register_directors, revoke_sessions, protected_dirs=()):
    if sessions_path.exists():
        try:
            data = json.loads(sessions_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot parse existing {sessions_path.as_posix()}: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError(f"existing {sessions_path.as_posix()} must be schema_version=1 JSON object")
        merged = dict(data)
        merged.setdefault("default", "worker")
        sess_map = dict(data.get("sessions") or {})
        changed = False
    else:
        merged = {
            "schema_version": 1,
            "about": "agydw per-session role map (managed by agy_setup.py)",
            "default": "worker",
            "sessions": {},
            "protected": [],
        }
        sess_map = {}
        changed = True

    prot_list = list(merged.get("protected") or [])
    existing_keys = {_norm_path_key(g) for g in prot_list if isinstance(g, str)}
    for pd in protected_dirs or ():
        if not pd:
            continue
        p_glob = pathlib.Path(pd).resolve().as_posix().rstrip("/") + "/**"
        if _norm_path_key(p_glob) not in existing_keys:
            prot_list.append(p_glob)
            existing_keys.add(_norm_path_key(p_glob))
            changed = True
    if merged.get("protected") != prot_list:
        merged["protected"] = prot_list

    for cid in register_directors or []:
        cid_s = cid.strip()
        if not cid_s:
            continue
        want = {"profile": "director", "name": "Director"}
        if sess_map.get(cid_s) != want and sess_map.get(cid_s) != "director":
            sess_map[cid_s] = want
            changed = True
    for cid in revoke_sessions or []:
        cid_s = cid.strip()
        if cid_s in sess_map:
            del sess_map[cid_s]
            changed = True
    merged["sessions"] = sess_map
    return merged, changed


def _flag_passed(argv_list, *names):
    for arg in argv_list:
        for nm in names:
            if arg == nm or arg.startswith(nm + "="):
                return True
    return False


def _read_agydw_section(director_dir):
    cfg_p = pathlib.Path(director_dir) / "director_work.json"
    if not cfg_p.is_file():
        return {}
    try:
        cfg = json.loads(cfg_p.read_text(encoding="utf-8-sig"))
        sec = cfg.get("agydw") if isinstance(cfg, dict) else None
        return dict(sec) if isinstance(sec, dict) else {}
    except (OSError, ValueError):
        return {}


def load_persisted_options(director_dir, hooks_file, repo, dir_active):
    state_p = pathlib.Path(director_dir) / SETUP_STATE_NAME
    if state_p.is_file():
        try:
            data = json.loads(state_p.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    sec = _read_agydw_section(director_dir)
    out = {}
    if sec.get("guard_mode") in ("remind", "ask", "block"):
        out["guard_mode"] = sec["guard_mode"]
    for bk in ("enforce_resume", "overwrite_utf8", "beginner"):
        if isinstance(sec.get(bk), bool):
            out[bk] = sec[bk]
    return out


def resolve_effective_args(a, argv_list, saved):
    eff = argparse.Namespace(**vars(a))
    if not saved:
        return eff
    if not _flag_passed(argv_list, "--guard-mode") and saved.get("guard_mode") in ("remind", "ask", "block"):
        eff.guard_mode = saved["guard_mode"]
    if not _flag_passed(argv_list, "--profile") and saved.get("profile") in ("worker", "director"):
        eff.profile = saved["profile"]
    if not _flag_passed(argv_list, "--enforce-resume", "--no-enforce-resume") and "enforce_resume" in saved:
        eff.enforce_resume = bool(saved["enforce_resume"])
    if not _flag_passed(argv_list, "--overwrite-utf8", "--no-overwrite-utf8") and "overwrite_utf8" in saved:
        eff.overwrite_utf8 = bool(saved["overwrite_utf8"])
    if not _flag_passed(argv_list, "--beginner", "--general") and "beginner" in saved:
        eff.beginner = bool(saved["beginner"])
    if not _flag_passed(argv_list, "--no-copy-tools", "--copy-tools") and "no_copy_tools" in saved:
        eff.no_copy_tools = bool(saved["no_copy_tools"])
    if not _flag_passed(argv_list, "--exclude-agents", "--no-exclude-agents") and "exclude_agents" in saved:
        eff.exclude_agents = bool(saved["exclude_agents"])
    if not _flag_passed(argv_list, "--rules-file", "--no-rules") and "install_rules" in saved:
        eff.install_rules = bool(saved["install_rules"])
    for attr, flag in (
        ("audit_log", "--audit-log"), ("resume_file", "--resume-file"),
        ("bridge_state", "--bridge-state"), ("sessions", "--sessions"),
    ):
        if not _flag_passed(argv_list, flag) and saved.get(attr):
            setattr(eff, attr, saved[attr])
    if not _flag_passed(argv_list, "--protect") and isinstance(saved.get("protect"), list):
        eff.protect = list(saved["protect"])
    return eff


def build_hooks_config(args, adapter_dir, tools_dir, director_dir):
    py = q(sys.executable)
    if os.name == "nt" and py.startswith('"'):
        py = f"call {py}"
    adir = pathlib.Path(adapter_dir)
    tdir = pathlib.Path(tools_dir)

    spec_hook = q(adir / "spec_gate_hook.py")
    guard = q(adir / "agy_guard.py")
    rules_json = q(tdir / "guard_rules.json")
    bridge = q(adir / "agy_hook_bridge.py")

    brief_path = q(director_dir / "BRIEF.md") if director_dir else q(pathlib.Path(args.repo) / "DIRECTOR" / "BRIEF.md")
    dir_q = q(director_dir) if director_dir else q(pathlib.Path(args.repo) / "DIRECTOR")
    tools_q = q(tdir)

    state_dir = q(args.bridge_state or (director_dir / ".bridge_state" if director_dir else pathlib.Path(args.repo) / ".agents" / ".bridge_state"))
    resume_path = q(args.resume_file or (director_dir / "RESUME_STATE.md" if director_dir else pathlib.Path(args.repo) / "RESUME_STATE.md"))
    audit_log = q(args.audit_log or (director_dir / "audit" / "{worktree}.jsonl" if director_dir else pathlib.Path(args.repo) / ".agents" / "audit_{worktree}.jsonl"))
    sess_arg = args.sessions or (director_dir / ".sessions.json" if director_dir else None)

    spec_cmd = f"{py} -B -I -X utf8 {spec_hook} --brief {brief_path} --director {dir_q} --tools {tools_q}"

    cfg = {
        "agydw-spec-gate": {
            "PreToolUse": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": spec_cmd, "timeout": 15}],
                }
            ]
        }
    }

    guard_parts = [f"{py} -B -I -X utf8 {guard} --rules {rules_json} --profile {args.profile} --mode {args.guard_mode}"]
    if getattr(args, "beginner", False):
        guard_parts.append("--hard-only")
    protect_dirs = []
    if director_dir:
        protect_dirs.append(pathlib.Path(director_dir).resolve())
    for extra_p in getattr(args, "protect", None) or []:
        ep = pathlib.Path(extra_p).resolve()
        if all(_norm_path_key(ep) != _norm_path_key(x) for x in protect_dirs):
            protect_dirs.append(ep)
    for pd in protect_dirs:
        guard_parts.append(f"--protect {q(pd)}")
    if director_dir:
        guard_parts.append(f"--pause-flag {q(pathlib.Path(director_dir) / 'PAUSE.flag')}")
    if sess_arg:
        guard_parts.append(f"--sessions {q(sess_arg)}")
    guard_parts.append(f"--audit {audit_log}")
    guard_parts.append(f"--bridge-state {state_dir}")
    if args.overwrite_utf8:
        guard_parts.append("--overwrite-utf8")
    guard_cmd = " ".join(guard_parts)
    cfg["agydw-guard"] = {
        "PreToolUse": [
            {
                "matcher": "run_command|read_file|view_file|view_file_outline|view_code_item|list_dir|grep_search|find_by_name|write_file|write_to_file|edit_file|replace_file_content|multi_replace_file_content|send_message|post_message|call_mcp_tool|mcp_.*|run_workflow|manage_task|invoke_subagent|swarm",
                "hooks": [{"type": "command", "command": guard_cmd, "timeout": 15}],
            }
        ]
    }

    post_unified_cmd = f"{py} -B -I -X utf8 {bridge} post-tool --audit-log {audit_log} --state-dir {state_dir} --inject-scan"
    session_cmd = f"{py} -B -I -X utf8 {bridge} session-start --resume {resume_path} --state-dir {state_dir} --banner"
    pre_inv_cmd = f"{py} -B -I -X utf8 {bridge} pre-invocation --state-dir {state_dir} --resume {resume_path}"
    stop_parts = [f"{py} -B -I -X utf8 {bridge} stop-check --resume {resume_path} --repo {q(args.repo)} --state-dir {state_dir}"]
    if sess_arg:
        stop_parts.append(f"--sessions {q(sess_arg)}")
    if args.enforce_resume:
        stop_parts.append("--enforce-resume")
    stop_cmd = " ".join(stop_parts)

    cfg["agydw-audit-and-bridge"] = {
        "SessionStart": [{"type": "command", "command": session_cmd, "timeout": 10}],
        "PostToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": post_unified_cmd, "timeout": 15}]}
        ],
        "PreInvocation": [{"type": "command", "command": pre_inv_cmd, "timeout": 10}],
        "Stop": [{"type": "command", "command": stop_cmd, "timeout": 10}],
    }
    return cfg


def build_plugins_config(plugin_parent_dir):
    return {"entries": [{"path": pathlib.Path(plugin_parent_dir).resolve().as_posix()}]}


def fill_placeholders(text, rules_root, repo, director_dir):
    out = text.replace("<RULES>", rules_root.as_posix()).replace("<REPO>", repo.as_posix())
    if director_dir:
        out = out.replace("<DIRECTOR>", director_dir.as_posix())
    return out


def plan_director_scaffold(director_dir, rules_root, repo, eff=None):
    d = pathlib.Path(director_dir)
    subdirs = [d / sd for sd in ("orders", "reviews", "research", "evidence", "audit")]
    writes = []
    tpl_dir = rules_root / "templates"
    for fname in ("RESUME_STATE.md", "PO_PENDING.md", "DECISION_REGISTER.md", "BRIEF.md"):
        src, dst = tpl_dir / fname, d / fname
        if not dst.exists() and src.is_file():
            content = fill_placeholders(src.read_text(encoding="utf-8-sig"), rules_root, repo, d)
            writes.append((dst, content, f"WRITE {dst.as_posix()} (from {src.name})"))

    ver = read_version(rules_root)
    primary = primary_repo_root(repo)
    static_defaults = {
        ".gitignore": "*\n",
        "INDEX.md": (
            f"# DIRECTOR INDEX\n\n"
            f"- 專案：`{primary.name}`\n"
            "- 接手一句話：讀 `DIRECTOR/INDEX.md`，照它接手，不要重問已確認的需求。\n\n"
            "## 核心紀錄\n"
            "- 初衷卡（需求／規格與確認原文）：`BRIEF.md`\n"
            "- 決策與驗收記錄：`DECISION_REGISTER.md`\n"
            "- 目前進度與下一步：`RESUME_STATE.md`\n"
            "- 使用者待決清單：`PO_PENDING.md`\n"
            "- 派工與交接紀錄：`DIRECTOR_LOG.md`\n"
            "- 總監環境狀態：`DIRECTOR_STATE.md`\n"
            "- 防呆與 Hook 清單：`GUARDS.md`\n"
            "- 進行中工作線：`ACTIVE_WORKSTREAMS.md`\n"
        ),
        "DIRECTOR_OPERATING_RULES.md": (
            f"# 本專案總監作業規則\n\n"
            f"- 規則包版本：{ver}（位置：`{rules_root.as_posix()}`）\n"
            f"- 本專案（`{primary.as_posix()}`）既有 `AGENTS.md` / `GEMINI.md` / `.agent/rules/` 與 PO 指示永遠優先。\n"
            f"- 其餘未列事項照 `{rules_root.as_posix()}/docs/` 01 至 13 執行。\n"
        ),
        "DIRECTOR_STATE.md": (
            f"# DIRECTOR_STATE\n\n"
            f"PROJECT_REPO: {primary.as_posix()}\n"
            f"RULES_PATH: {rules_root.as_posix()}\n"
            f"PENDING_METHOD: ask_question + PO_PENDING.md（圖片或成品對照可選 Artifacts / generative_ui；worker 經總監回報）\n"
            f"STAGE: BOOTSTRAP\n"
        ),
        "ACTIVE_WORKSTREAMS.md": "# ACTIVE_WORKSTREAMS\n\n| 名稱 | 模式 | 狀態 | 工單 | RESUME_STATE | 模型 / Effort | 備註 |\n|---|---|---|---|---|---|---|\n",
        "RISK_REGISTER.md": "# RISK_REGISTER\n\n| ID | 風險 | 影響 | 對策 | 負責人 |\n|---|---|---|---|---|\n",
        "DIRECTOR_LOG.md": "# DIRECTOR_LOG\n\n",
        "GUARDS.md": (
            "# GUARDS\n\n| 機制 | 擁有者 | 中斷影響 | 檢查指令 | 重建指令 |\n|---|---|---|---|---|\n"
            "| Spec Gate + Guard (`spec_gate_hook` + `agy_guard` + `agy_hook_bridge`) | 總監 / PO | 失去初衷卡前阻擋與工具層自動防護 | `python -B -I -X utf8 DIRECTOR/adapters/antigravity/agy_doctor.py --repo <REPO> --probe-hooks` | `python -B -I -X utf8 adapters/antigravity/agy_setup.py --repo <REPO> --apply` |\n"
        ),
        "BASELINE_METRICS.md": "# BASELINE_METRICS\n\n基準尚未量測：請跑 `DIRECTOR/adapters/antigravity/agy_usage.py` 與 `DIRECTOR/tools/memory_size_check.py --kind rules`。\n",
    }
    for fname, content in static_defaults.items():
        dst = d / fname
        if not dst.exists():
            writes.append((dst, content, f"WRITE {dst.as_posix()}"))

    cfg_dst = d / "director_work.json"
    cfg_src = rules_root / "director_work.example.json"
    common_d = git_common_dir(repo)
    if not cfg_dst.exists() and cfg_src.is_file():
        cfg = json.loads(cfg_src.read_text(encoding="utf-8-sig"))
        cfg["protect_director"] = d.as_posix()
        cfg["director_worktree"] = primary.as_posix()
        if common_d is not None:
            cfg["project_git_common_dir"] = common_d.as_posix()
        if eff is not None:
            cfg["mode"] = "beginner" if getattr(eff, "beginner", False) else "general"
            cfg["agydw"] = {
                "guard_mode": eff.guard_mode,
                "enforce_resume": bool(eff.enforce_resume),
                "overwrite_utf8": bool(eff.overwrite_utf8),
                "beginner": bool(getattr(eff, "beginner", False)),
            }
        writes.append((cfg_dst, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", f"WRITE {cfg_dst.as_posix()}"))
    return subdirs, writes


def plan_director_config_migration(director_dir, repo, eff=None):
    cfg_dst = pathlib.Path(director_dir) / "director_work.json"
    if not cfg_dst.is_file():
        return None
    common_d = git_common_dir(repo)
    try:
        cfg = json.loads(cfg_dst.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict):
        return None
    changed_fields = []
    if common_d is not None and (not isinstance(cfg.get("project_git_common_dir"), str) or not cfg["project_git_common_dir"].strip()):
        cfg["project_git_common_dir"] = common_d.as_posix()
        changed_fields.append("project_git_common_dir")
    if not isinstance(cfg.get("director_worktree"), str) or not cfg["director_worktree"].strip():
        cfg["director_worktree"] = primary_repo_root(repo).as_posix()
        changed_fields.append("director_worktree")
    if eff is not None:
        want_mode = "beginner" if getattr(eff, "beginner", False) else "general"
        if cfg.get("mode") != want_mode:
            cfg["mode"] = want_mode
            changed_fields.append("mode")
        agydw_cur = cfg.get("agydw") if isinstance(cfg.get("agydw"), dict) else {}
        agydw_next = dict(agydw_cur)
        agydw_next.update({
            "guard_mode": eff.guard_mode,
            "enforce_resume": bool(eff.enforce_resume),
            "overwrite_utf8": bool(eff.overwrite_utf8),
            "beginner": bool(getattr(eff, "beginner", False)),
        })
        if agydw_next != cfg.get("agydw"):
            cfg["agydw"] = agydw_next
            changed_fields.append("agydw")
    if not changed_fields:
        return None
    return cfg_dst, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", f"MIGRATE {cfg_dst.as_posix()} (+{', '.join(changed_fields)})"


def plan_director_state_migration(director_dir, rules_root):
    st_dst = pathlib.Path(director_dir) / "DIRECTOR_STATE.md"
    if not st_dst.is_file():
        return None
    txt = st_dst.read_text(encoding="utf-8-sig", errors="replace")
    want = pathlib.Path(rules_root).resolve().as_posix()
    m = re.search(r"^([ \t]*RULES_PATH[ \t]*:[ \t]*)(.+)$", txt, re.M)
    if not m or m.group(2).strip() == want:
        return None
    new_txt = txt[:m.start(2)] + want + txt[m.end(2):]
    return st_dst, new_txt, f"MIGRATE {st_dst.as_posix()} (RULES_PATH)"


def merge_hooks_config(hooks_file, hooks_obj, re_enable=False, drop_groups=()):
    try:
        existing = json.loads(hooks_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot parse existing {hooks_file.as_posix()}: {exc}") from exc
    if not isinstance(existing, dict):
        raise ValueError(f"existing {hooks_file.as_posix()} must be a JSON object")
    if "hooks" in existing and isinstance(existing.get("hooks"), dict) and "agydw-guard" not in existing and "agydw-spec-gate" not in existing:
        raise ValueError(f"existing {hooks_file.as_posix()} uses Claude-style 'hooks' wrapper; pass --force")
    managed = set(hooks_obj.keys()) | set(drop_groups)
    preserved = [k for k in existing if k not in managed]
    merged = dict(existing)
    for dk in drop_groups:
        if dk not in hooks_obj:
            merged.pop(dk, None)
    for k, v in hooks_obj.items():
        old_grp = existing.get(k)
        grp = dict(old_grp) if isinstance(old_grp, dict) else {}
        if re_enable:
            grp.pop("enabled", None)
        elif isinstance(old_grp, dict) and old_grp.get("enabled") is False:
            grp["enabled"] = False
        for hk_k, hk_v in v.items():
            grp[hk_k] = json.loads(json.dumps(hk_v))
        merged[k] = grp
    return merged, len(preserved), (merged != existing)


def _is_dw_plugin_entry(ep, old_rules_roots):
    for cand in (ep / "director-work" / "plugin.json", ep / "plugin.json"):
        if cand.is_file():
            try:
                if json.loads(cand.read_text(encoding="utf-8-sig")).get("name") == "director-work":
                    return True
            except (OSError, ValueError):
                pass
    ep_key = _norm_path_key(ep)
    for old_r in old_rules_roots:
        if not old_r:
            continue
        op = pathlib.Path(old_r)
        for psub in (op / "plugin", op / "plugin" / "director-work", op / "adapters" / "antigravity" / "plugin"):
            if ep_key == _norm_path_key(psub):
                return True
    return ep.name == "director-work"


def merge_plugins_config(plugins_file, plugin_parent, repo_path, old_rules_roots=()):
    try:
        existing = json.loads(plugins_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot parse existing {plugins_file.as_posix()}: {exc}") from exc
    if not isinstance(existing, dict) or not isinstance(existing.get("entries", []), list):
        raise ValueError(f"existing {plugins_file.as_posix()} must have an 'entries' array")
    target_str = pathlib.Path(plugin_parent).resolve().as_posix()
    new_entries, preserved, replaced_dw = [], 0, False
    for ent in existing.get("entries", []):
        if isinstance(ent, dict) and isinstance(ent.get("path"), str):
            ep = pathlib.Path(ent["path"])
            if not ep.is_absolute():
                ep = (pathlib.Path(repo_path) / ep).resolve()
            excl = ent.get("exclude") if isinstance(ent.get("exclude"), list) else []
            if "director-work" not in excl and _is_dw_plugin_entry(ep, old_rules_roots):
                if not replaced_dw:
                    upd = dict(ent)
                    upd["path"] = target_str
                    new_entries.append(upd)
                    replaced_dw = True
                continue
        new_entries.append(ent)
        preserved += 1
    if not replaced_dw:
        new_entries.append({"path": target_str})
    merged = dict(existing, entries=new_entries)
    return merged, preserved, (merged != existing), replaced_dw


def validate_target_path_types(repo, director_dir, dir_active, sessions_path, excl, plugin_parent, install_rules):
    rp = pathlib.Path(repo)
    agents_dir = rp / ".agents"
    if agents_dir.exists() and not agents_dir.is_dir():
        return f"path conflict: {agents_dir.as_posix()} exists and is not a directory"
    for fp in (agents_dir / "hooks.json", agents_dir / "plugins.json" if plugin_parent.is_dir() else None):
        if fp is not None and fp.exists() and fp.is_dir():
            return f"path conflict: {fp.as_posix()} exists as a directory"
    if install_rules:
        ag_dir = rp / ".agent"
        ar_dir = ag_dir / "rules"
        rf = ar_dir / "director-work.md"
        for dp in (ag_dir, ar_dir):
            if dp.exists() and not dp.is_dir():
                return f"path conflict: {dp.as_posix()} exists and is not a directory"
        if rf.exists() and rf.is_dir():
            return f"path conflict: {rf.as_posix()} exists as a directory"
    if excl is not None:
        if excl.parent.exists() and not excl.parent.is_dir():
            return f"path conflict: {excl.parent.as_posix()} is not a directory"
        if excl.exists() and excl.is_dir():
            return f"path conflict: {excl.as_posix()} is a directory"
    if dir_active:
        d = pathlib.Path(director_dir)
        if d.exists() and not d.is_dir():
            return f"path conflict: {d.as_posix()} is not a directory"
        for sd in ("orders", "reviews", "research", "evidence", "audit", "tools", "adapters"):
            sub = d / sd
            if sub.exists() and not sub.is_dir():
                return f"path conflict: {sub.as_posix()} is not a directory"
        for fn in (SETUP_STATE_NAME, "director_work.json", "DIRECTOR_STATE.md", "RESUME_STATE.md", "INDEX.md"):
            fp = d / fn
            if fp.exists() and fp.is_dir():
                return f"path conflict: {fp.as_posix()} is a directory"
    if sessions_path:
        sp = pathlib.Path(sessions_path)
        if sp.parent.exists() and not sp.parent.is_dir():
            return f"path conflict: {sp.parent.as_posix()} is not a directory"
        if sp.exists() and sp.is_dir():
            return f"path conflict: {sp.as_posix()} is a directory"
    return None


def _norm_opt_path(val, base_dir=None):
    if not val:
        return None
    s = os.path.expanduser(str(val).strip())
    p = pathlib.Path(s)
    if not p.is_absolute() and base_dir is not None:
        p = pathlib.Path(base_dir) / p
    try:
        return p.resolve().as_posix()
    except (OSError, ValueError):
        return p.as_posix() if hasattr(p, "as_posix") else s.replace("\\", "/")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="target project git repository")
    ap.add_argument("--director", help="DIRECTOR folder path (default: <PRIMARY_REPO>/DIRECTOR)")
    ap.add_argument("--rules", default=str(HERE.parent.parent), help="path to agy-DIRECTOR-WORKER rules repo")
    ap.add_argument("--plugin", help="override plugin parent directory")
    ap.add_argument("--guard-mode", choices=("remind", "ask", "block"), default="remind")
    ap.add_argument("--profile", choices=("worker", "director"), default="worker")
    ap.add_argument("--beginner", action="store_true", help="beginner mode: keep agydw-spec-gate and agydw-guard --hard-only (relax remind/ask)")
    ap.add_argument("--general", dest="beginner", action="store_false", help="general mode: restore full agydw-guard reminders/asks after --beginner")
    ap.add_argument("--register-director", "--director-session", dest="register_director", action="append", default=[])
    ap.add_argument("--revoke-session", action="append", default=[])
    ap.add_argument("--sessions")
    ap.add_argument("--protect", action="append", default=[])
    ap.add_argument("--audit-log")
    ap.add_argument("--resume-file")
    ap.add_argument("--bridge-state")
    ap.add_argument("--enforce-resume", action=argparse.BooleanOptionalAction, default=False)
    ap.add_argument("--overwrite-utf8", action=argparse.BooleanOptionalAction, default=False)
    ap.add_argument("--init-director", action="store_true")
    ap.add_argument("--no-copy-tools", dest="no_copy_tools", action="store_true", default=False)
    ap.add_argument("--copy-tools", dest="no_copy_tools", action="store_false")
    ap.add_argument("--exclude-agents", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--rules-file", dest="install_rules", action="store_true", default=True)
    ap.add_argument("--no-rules", dest="install_rules", action="store_false")
    ap.add_argument("--enable", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--apply", action="store_true")
    try:
        a = ap.parse_args(argv_list)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    explicit_rules = _flag_passed(argv_list, "--rules")
    repo = pathlib.Path(a.repo).resolve()
    if not repo.is_dir():
        print(f"AGY_SETUP: FAIL repo directory not found: {repo}", file=sys.stderr)
        return 2
    rules_root, rules_err = resolve_rules_root(a.rules, explicit_rules)
    if rules_err:
        print(f"AGY_SETUP: FAIL {rules_err}", file=sys.stderr)
        return 1

    director_dir = pathlib.Path(_norm_opt_path(a.director, base_dir=repo) if a.director else default_director_dir(repo)).resolve()
    own_ok, owner_desc = director_matches_repo(director_dir, repo)
    if not own_ok:
        print(f"AGY_SETUP: FAIL director {director_dir.as_posix()} belongs to {owner_desc}; pass project-specific --director", file=sys.stderr)
        return 1
    if director_dir.parent.exists() and not director_dir.parent.is_dir():
        print(f"AGY_SETUP: FAIL path conflict: {director_dir.parent.as_posix()} is not a directory", file=sys.stderr)
        return 1

    def run_setup_pipeline():
        dir_active = bool(a.director or a.init_director or director_dir.is_dir())
        agents_dir = repo / ".agents"
        hooks_file = agents_dir / "hooks.json"
        plugins_file = agents_dir / "plugins.json"

        saved_opts = load_persisted_options(director_dir, hooks_file, repo, dir_active)
        eff = resolve_effective_args(a, argv_list, saved_opts)
        eff.audit_log = _norm_opt_path(eff.audit_log, base_dir=repo)
        eff.resume_file = _norm_opt_path(eff.resume_file, base_dir=repo)
        eff.bridge_state = _norm_opt_path(eff.bridge_state, base_dir=repo)
        eff.sessions = _norm_opt_path(eff.sessions, base_dir=repo)
        eff.protect = [p for p in (_norm_opt_path(x, base_dir=repo) for x in (eff.protect or [])) if p]

        old_info = director_owner_info(director_dir)
        old_rules_roots = [old_info.get("rules_path")]
        src_adapter = rules_root / "adapters" / "antigravity"
        has_existing_copy = (director_dir / "tools").is_dir() or (director_dir / "adapters" / "antigravity").is_dir()
        explicit_copy = _flag_passed(argv_list, "--copy-tools")
        should_sync_tools = (not eff.no_copy_tools) and dir_active and (a.init_director or has_existing_copy or explicit_copy) and (
            _norm_path_key(rules_root / "tools") != _norm_path_key(director_dir / "tools")
        )
        use_copied_tools = (not eff.no_copy_tools) and (should_sync_tools or has_existing_copy)
        active_tools_dir = (director_dir / "tools") if use_copied_tools else (rules_root / "tools")
        active_adapter_dir = (director_dir / "adapters" / "antigravity") if use_copied_tools else src_adapter
        plugin_parent = pathlib.Path(_norm_opt_path(a.plugin, base_dir=repo)).resolve() if a.plugin else (active_adapter_dir / "plugin")

        sessions_path = pathlib.Path(eff.sessions).resolve() if eff.sessions else (director_dir / ".sessions.json" if dir_active else None)
        protected_globs_dirs = ([director_dir] if dir_active else []) + [pathlib.Path(x) for x in (eff.protect or [])]
        excl = git_exclude_file(repo)

        path_err = validate_target_path_types(repo, director_dir, dir_active, sessions_path, excl, plugin_parent, eff.install_rules)
        if path_err:
            print(f"AGY_SETUP: FAIL {path_err}", file=sys.stderr)
            return 1

        sessions_obj, sessions_changed = (None, False)
        if sessions_path:
            try:
                sessions_obj, sessions_changed = build_sessions_config(
                    sessions_path, a.register_director, a.revoke_session, protected_globs_dirs
                )
            except ValueError as exc:
                print(f"AGY_SETUP: FAIL {exc}", file=sys.stderr)
                return 1

        want_excludes = BASE_LOCAL_EXCLUDES + (AGENTS_LOCAL_EXCLUDES if eff.exclude_agents else ())
        excl_missing, excl_existing = [], ""
        if excl is not None:
            excl_existing = excl.read_text(encoding="utf-8", errors="replace") if excl.is_file() else ""
            have = {line.strip() for line in excl_existing.splitlines()}
            excl_missing = [item for item in want_excludes if item not in have and item.rstrip("/") not in have]

        scaffold_dirs, scaffold_writes = ([], [])
        if a.init_director:
            scaffold_dirs, scaffold_writes = plan_director_scaffold(director_dir, rules_root, repo, eff=eff)
        migration_plan = plan_director_config_migration(director_dir, repo, eff=eff) if dir_active else None
        state_mig_plan = plan_director_state_migration(director_dir, rules_root) if dir_active else None

        hooks_obj = build_hooks_config(eff, active_adapter_dir, active_tools_dir, director_dir if dir_active else None)
        drop_grps = ()
        if not hooks_file.exists() or a.force:
            hooks_write_data, hooks_should_write, hooks_action = hooks_obj, True, f"WRITE {hooks_file.as_posix()}"
        else:
            try:
                hooks_write_data, n_pres, chg = merge_hooks_config(hooks_file, hooks_obj, re_enable=bool(a.enable), drop_groups=drop_grps)
            except ValueError as exc:
                print(f"AGY_SETUP: FAIL {exc}", file=sys.stderr)
                return 1
            hooks_should_write = chg
            hooks_action = f"{'MERGE' if chg else 'OK'} {hooks_file.as_posix()} (preserved {n_pres} group(s))"

        plugins_obj = build_plugins_config(plugin_parent)
        if not plugins_file.exists() or a.force:
            plugins_write_data, plugins_should_write, plugins_action = plugins_obj, True, f"WRITE {plugins_file.as_posix()} -> {plugin_parent.as_posix()}"
        else:
            try:
                plugins_write_data, n_ent, p_chg, rep_dw = merge_plugins_config(plugins_file, plugin_parent, repo, old_rules_roots)
            except ValueError as exc:
                print(f"AGY_SETUP: FAIL {exc}", file=sys.stderr)
                return 1
            plugins_should_write = p_chg
            verb = "UPGRADE" if rep_dw else ("MERGE" if p_chg else "OK")
            plugins_action = f"{verb} {plugins_file.as_posix()} -> {plugin_parent.as_posix()} (preserved {n_ent} entry/entries)"

        actions, notes = [], []
        if not a.director:
            primary = primary_repo_root(repo)
            old_outside = list(primary.parent.glob(f"DIRECTOR-{primary.name}*"))
            if old_outside and not director_dir.is_dir():
                notes.append(f"NOTE legacy outside folder found ({old_outside[0].as_posix()}); 3.0.0 uses in-repo {director_dir.as_posix()} (copy/archive old records if continuing)")

        if excl is not None and excl_missing:
            actions.append(f"APPEND {excl.as_posix()}: {', '.join(excl_missing)}")
            if a.apply:
                excl.parent.mkdir(parents=True, exist_ok=True)
                prefix = "" if (not excl_existing or excl_existing.endswith("\n")) else "\n"
                with open(excl, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(prefix + "\n".join(excl_missing) + "\n")
        elif excl is None:
            actions.append(f"NOTE {repo.as_posix()} is not a git repo; skipped .git/info/exclude")

        for sd in scaffold_dirs:
            if not sd.is_dir():
                actions.append(f"CREATE dir {sd.as_posix()}")
                if a.apply:
                    sd.mkdir(parents=True, exist_ok=True)
        for dst, content, label in scaffold_writes:
            actions.append(label)
            if a.apply:
                _COMMON.write_text_atomic(dst, content)
        for plan in (migration_plan, state_mig_plan):
            if plan is not None:
                m_dst, m_txt, m_lbl = plan
                actions.append(m_lbl)
                if a.apply:
                    _COMMON.write_text_atomic(m_dst, m_txt)

        if eff.install_rules:
            rules_src = src_adapter / "rules" / "director-work.md"
            rules_dst = repo / ".agent" / "rules" / "director-work.md"
            if rules_src.is_file():
                want_rules = fill_placeholders(rules_src.read_text(encoding="utf-8-sig"), rules_root, repo, director_dir)
                cur_rules = rules_dst.read_text(encoding="utf-8-sig", errors="replace") if rules_dst.is_file() else None
                if cur_rules != want_rules:
                    actions.append(f"{'UPDATE' if cur_rules is not None else 'WRITE'} {rules_dst.as_posix()}")
                    if a.apply:
                        _COMMON.write_text_atomic(rules_dst, want_rules)
                else:
                    actions.append(f"OK {rules_dst.as_posix()}")

        ver = read_version(rules_root)
        if should_sync_tools:
            actions.append(f"INSTALL base tools -> {active_tools_dir.as_posix()}")
            if a.apply:
                director_dir.mkdir(parents=True, exist_ok=True)
                rc_inst, out_inst, err_inst = run_base_install(rules_root, active_tools_dir, execute=True)
                if rc_inst != 0:
                    print(f"AGY_SETUP: FAIL tools/install.py exit {rc_inst}: {out_inst.strip()} {err_inst.strip()}", file=sys.stderr)
                    return 1
            exp_adapter = compute_dir_hashes(src_adapter)
            ad_ok, ad_old = adapter_matches_manifest(active_adapter_dir, ver, exp_adapter)
            if not ad_ok:
                actions.append(f"{'COPY' if ad_old == 'missing' else f'UPGRADE ({ad_old}->{ver})'} adapter -> {active_adapter_dir.as_posix()}")
                if a.apply:
                    deploy_adapter_dir(active_adapter_dir, src_adapter, rules_root, exp_adapter, ver)
            else:
                actions.append(f"OK adapter ({ver}) at {active_adapter_dir.as_posix()}")

        if sessions_path:
            if a.apply:
                with file_lock(sessions_path.with_name(sessions_path.name + ".lock")):
                    live_sess, live_chg = build_sessions_config(
                        sessions_path, a.register_director, a.revoke_session, protected_globs_dirs
                    )
                    if live_chg:
                        existed = sessions_path.exists()
                        _COMMON.write_json_atomic(sessions_path, live_sess)
                        actions.append(f"{'MERGE' if existed else 'WRITE'} {sessions_path.as_posix()} (director_sessions={len(live_sess['sessions'])})")
                    else:
                        actions.append(f"OK {sessions_path.as_posix()}")
            else:
                actions.append(f"{'MERGE' if (sessions_changed and sessions_path.exists()) else ('WRITE' if sessions_changed else 'OK')} {sessions_path.as_posix()}")

        actions.append(hooks_action)
        if a.apply and hooks_should_write:
            _COMMON.write_json_atomic(hooks_file, hooks_write_data)
        actions.append(plugins_action)
        if a.apply and plugins_should_write:
            _COMMON.write_json_atomic(plugins_file, plugins_write_data)

        setup_state = {
            "schema_version": 1,
            "guard_mode": eff.guard_mode,
            "profile": eff.profile,
            "beginner": bool(getattr(eff, "beginner", False)),
            "enforce_resume": bool(eff.enforce_resume),
            "overwrite_utf8": bool(eff.overwrite_utf8),
            "no_copy_tools": bool(eff.no_copy_tools),
            "exclude_agents": bool(eff.exclude_agents),
            "install_rules": bool(eff.install_rules),
            "audit_log": eff.audit_log,
            "resume_file": eff.resume_file,
            "bridge_state": eff.bridge_state,
            "sessions": eff.sessions,
            "protect": list(eff.protect or []),
            "active_tools_dir": active_tools_dir.resolve().as_posix(),
            "active_adapter_dir": active_adapter_dir.resolve().as_posix(),
            "rules_root": rules_root.as_posix(),
            "version": ver,
        }
        if dir_active and a.apply:
            _COMMON.write_json_atomic(director_dir / SETUP_STATE_NAME, setup_state)

        for act in actions:
            print(("APPLY " if a.apply else "PLAN  ") + act)
        for n in notes:
            print(n)
        mode_desc = "beginner(hard-only)" if getattr(eff, "beginner", False) else f"general({eff.guard_mode})"
        print(f"AGY_SETUP: {'DONE' if a.apply else 'DRY_RUN'} director={director_dir.as_posix()} mode={mode_desc} steps={len(actions)}")
        return 0

    if a.apply:
        proj_hash = hashlib.sha256(_norm_path_key(director_dir).encode("utf-8")).hexdigest()[:10]
        with file_lock(director_dir.parent / f".agydw_setup_{proj_hash}.lock"):
            return run_setup_pipeline()
    return run_setup_pipeline()


if __name__ == "__main__":
    sys.exit(main())
