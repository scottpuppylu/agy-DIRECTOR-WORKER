#!/usr/bin/env python3
"""Self-test suite for agy-DIRECTOR-WORKER (agydw).

Verifies:
  1. Upstream byte-identity (39 DW v2.6.0 files) when reference checkout is present
  2. CLI --help smoke checks across all tools/*.py
  3. agy_guard.py (Antigravity + Claude, App 2.0 + IDE/CLI names, remind/ask/block,
     G00/G01/G02/G43/G50/G53, --protect, --pause-flag, --overwrite-utf8,
     plus Windows cmd /c execution on plugin hooks and spaced-path generated hooks)
  4. agy_hook_bridge.py (session-start, 2-stage post-tool -> pre-invocation,
     CHECKPOINT compaction re-inject, stop-check with continuationActive)
  5. agy_audit.py (hook-append, build + --include-subagents, verify, report_vs_audit,
     loops --reads, inject) & agy_usage.py (exact token math, --json, --out, NO_DATA)
  6. Core governance roundtrip (agy_setup, agy_setup_check --strict, agy_doctor,
     safety_gate, gate_check --order/--hash/--report-skeleton/--match/--verdict/--sequence/--final,
     scope_check, evidence_record, memory_size_check --kind rules, status_gen)
  7. Plugin schema, subagent YAML tools: guard, byte budgets, vocabulary alignment,
     workflow AST parse, LF line endings, and repo-wide PII/secret leak scan.

Usage:
  python -B -I -X utf8 tools/selftest_agy.py [--verbose] [--upstream DIR]
"""
import argparse
import ast
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

TOOLS_DIR = pathlib.Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parent
FIXTURES_DIR = TOOLS_DIR / "fixtures"
PYTHON = sys.executable

UPSTREAM_PURE_FILES = [
    ("LICENSE", "LICENSE"),
    ("PURPOSE.md", "PURPOSE.md"),
    ("CHANGELOG-upstream.md", "CHANGELOG.md"),
    ("templates/.gitleaks.toml", "templates/.gitleaks.toml"),
    ("templates/.pre-commit-config.yaml", "templates/.pre-commit-config.yaml"),
    ("templates/CPU_REPRO.md", "templates/CPU_REPRO.md"),
    ("templates/GATE_FEASIBILITY.md", "templates/GATE_FEASIBILITY.md"),
    ("templates/HANDOFF.md", "templates/HANDOFF.md"),
    ("templates/SETTINGS_MANIFEST.md", "templates/SETTINGS_MANIFEST.md"),
    ("templates/pre_tr.json", "templates/pre_tr.json"),
    ("templates/run_closing.py", "templates/run_closing.py"),
    ("templates/weird_inputs.py", "templates/weird_inputs.py"),
]
UPSTREAM_PURE_TOOLS = [
    "audit_log.py", "completion.py", "delete_guard.py", "dispatch_check.py",
    "evidence_record.py", "flag_check.py", "guard_rules.json", "hash_check.py",
    "hook_guard.py", "inject_scan.py", "line_brief.py", "loop_guard.py",
    "pack_check.py", "pause_ctl.py", "pre_tr.py", "ran_line.py",
    "report_vs_audit.py", "resume_archive.py", "resume_inject.py", "round_check.py",
    "ruler_check.py", "run_plan_check.py", "secret_mask.py", "sim_check.py",
    "trace_report.py", "vocabulary.json", "waiver.py",
]


class Runner:
    """Collect pass / skip / fail outcomes with concise reporting."""

    def __init__(self, verbose=False):
        self.verbose = verbose
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def check(self, name, fn):
        try:
            detail = fn()
            self.passed += 1
            if self.verbose:
                msg = f"  [PASS] {name}" + (f" ({detail})" if detail else "")
                print(msg)
        except _Skip as s:
            self.skipped += 1
            print(f"  [SKIP] {name}: {s}")
        except Exception as exc:
            self.failed += 1
            print(f"  [FAIL] {name}: {exc}")


class _Skip(Exception):
    pass


def run_py(script, args, cwd=None, stdin_obj=None, check_zero=False):
    cmd = [PYTHON, "-B", "-I", "-X", "utf8", str(script)] + list(args)
    inp = json.dumps(stdin_obj) if isinstance(stdin_obj, dict) else (stdin_obj or "")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT_DIR),
        input=inp,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
    )
    if check_zero and proc.returncode != 0:
        raise AssertionError(
            f"{pathlib.Path(script).name} {' '.join(args)} exited {proc.returncode}\n"
            f"stdout={proc.stdout.strip()}\nstderr={proc.stderr.strip()}"
        )
    return proc


def run_cmd(cmd, cwd=None, stdin_obj=None, check_zero=False):
    inp = json.dumps(stdin_obj) if isinstance(stdin_obj, dict) else (stdin_obj or "")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT_DIR),
        input=inp,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
    )
    if check_zero and proc.returncode != 0:
        raise AssertionError(f"cmd {cmd} exited {proc.returncode}: {proc.stderr.strip()} {proc.stdout.strip()}")
    return proc


def run_hook_shell(cmd_line, hook_dir, payload_dict):
    """Execute a hook command exactly the way Antigravity runs it on this OS."""
    inp = json.dumps(payload_dict, ensure_ascii=False)
    if os.name == "nt":
        proc = subprocess.run(
            "cmd /d /c " + cmd_line,
            cwd=str(hook_dir),
            input=inp,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
        )
    else:
        proc = subprocess.run(
            ["sh", "-c", cmd_line],
            cwd=str(hook_dir),
            input=inp,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
        )
    return proc


def test_upstream_identity(upstream_dir):
    up = pathlib.Path(upstream_dir)
    if not (up / ".git").exists():
        raise _Skip(f"upstream git repo not found at {up}")
    pairs = list(UPSTREAM_PURE_FILES) + [(f"tools/{t}", f"tools/{t}") for t in UPSTREAM_PURE_TOOLS]
    checked = 0
    for dst_rel, src_rel in pairs:
        local_path = ROOT_DIR / dst_rel
        assert local_path.is_file(), f"missing {dst_rel}"
        proc = subprocess.run(
            ["git", "-C", str(up), "show", f"v2.6.0:{src_rel}"],
            capture_output=True,
            timeout=15,
        )
        assert proc.returncode == 0, f"git show v2.6.0:{src_rel} failed"
        src_sha = hashlib.sha256(proc.stdout).hexdigest()
        dst_sha = hashlib.sha256(local_path.read_bytes()).hexdigest()
        assert src_sha == dst_sha, f"SHA mismatch on {dst_rel}: {dst_sha[:12]} != {src_sha[:12]}"
        checked += 1
    return f"{checked}/{len(pairs)} SHA-256 exact match"


def test_tools_help():
    scripts = sorted(p for p in TOOLS_DIR.glob("*.py") if p.name != "__init__.py")
    assert len(scripts) >= 39, f"expected >= 39 tools/*.py, got {len(scripts)}"
    for sp in scripts:
        proc = run_py(sp, ["--help"])
        assert proc.returncode == 0, f"{sp.name} --help failed rc={proc.returncode}: {proc.stderr}"
    return f"{len(scripts)} tools --help rc=0"


def test_agy_guard_matrix():
    payloads = json.loads((FIXTURES_DIR / "agy_hook_payloads.json").read_text("utf-8"))
    ag = payloads["antigravity"]
    cl = payloads["claude"]
    rules = TOOLS_DIR / "guard_rules.json"

    with tempfile.TemporaryDirectory(prefix="agydw_guard_") as td:
        tmp = pathlib.Path(td)
        audit_file = tmp / "guard_audit.jsonl"
        bridge_dir = tmp / "bridge_state"
        pause_flag = tmp / "pause.flag"

        # 1. Clean Antigravity calls -> allow
        for key in ("clean_cmd", "clean_read"):
            p = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--mode", "remind"], stdin_obj=ag[key], check_zero=True)
            out = json.loads(p.stdout)
            assert out.get("decision") == "allow", f"{key} got {out}"

        # 2. Hard-denied rules regardless of --mode remind (G00 actual guard script, G50, G53)
        g00_payload = {
            "hookEventName": "PreToolUse",
            "conversationId": "conv-demo-001",
            "toolCall": {"name": "edit_file", "args": {"Path": str(TOOLS_DIR / "agy_guard.py")}},
        }
        p00 = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--mode", "remind", "--audit", str(audit_file)], stdin_obj=g00_payload, check_zero=True)
        assert json.loads(p00.stdout).get("decision") == "deny", f"g00 real path should deny, got {p00.stdout}"

        for key in ("g50_secret_read_app2", "g50_secret_read_ide", "g53_oauth_read"):
            p = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--mode", "remind", "--audit", str(audit_file)], stdin_obj=ag[key], check_zero=True)
            out = json.loads(p.stdout)
            assert out.get("decision") == "deny", f"{key} should deny in remind mode, got {out}"

        # 3. G43 Antigravity config edit by worker (follows --mode like G40); G01/G02 seat-belt remind / ask / block
        p43_rem = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--profile", "worker", "--mode", "remind"], stdin_obj=ag["g43_hooks_edit"], check_zero=True)
        j43_rem = json.loads(p43_rem.stdout)
        assert j43_rem.get("decision") == "allow" and "[G43]" in j43_rem.get("reason", ""), f"g43 remind got {j43_rem}"
        p43_ask = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--profile", "worker", "--mode", "ask"], stdin_obj=ag["g43_hooks_edit"], check_zero=True)
        assert json.loads(p43_ask.stdout).get("decision") == "force_ask", f"g43 ask got {p43_ask.stdout}"
        p43_blk = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--profile", "worker", "--mode", "block"], stdin_obj=ag["g43_hooks_edit"], check_zero=True)
        assert json.loads(p43_blk.stdout).get("decision") == "deny", f"g43 block got {p43_blk.stdout}"

        p01_rem = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--mode", "remind"], stdin_obj=ag["g01_force_push"], check_zero=True)
        j01_rem = json.loads(p01_rem.stdout)
        assert j01_rem.get("decision") == "allow" and "[G01]" in j01_rem.get("reason", ""), f"g01 remind got {j01_rem}"
        p01_blk = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--mode", "block"], stdin_obj=ag["g01_force_push"], check_zero=True)
        assert json.loads(p01_blk.stdout).get("decision") == "deny", f"g01 block got {p01_blk.stdout}"

        p02_rem = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--mode", "remind", "--bridge-state", str(bridge_dir), "--audit", str(audit_file)], stdin_obj=ag["g02_reset_hard"], check_zero=True)
        j02_rem = json.loads(p02_rem.stdout)
        assert j02_rem.get("decision") == "allow" and "reason" in j02_rem, f"g02 remind got {j02_rem}"
        assert any(bridge_dir.glob("pending_*.jsonl")), "remind should queue bridge alert"

        p02_ask = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--mode", "ask"], stdin_obj=ag["g02_reset_hard"], check_zero=True)
        assert json.loads(p02_ask.stdout).get("decision") == "force_ask", f"g02 ask got {p02_ask.stdout}"

        p02_blk = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--mode", "block"], stdin_obj=ag["g02_reset_hard"], check_zero=True)
        assert json.loads(p02_blk.stdout).get("decision") == "deny", f"g02 block got {p02_blk.stdout}"

        # 4. --protect DIRECTOR
        prot_dir = tmp / "DIRECTOR"
        for key, tool_nm, arg_key, rel in (
            ("protected_order_write_app2", "write_file", "Path", "orders/WO-001.md"),
            ("protected_log_edit_ide", "replace_file_content", "TargetFile", "DIRECTOR_LOG.md"),
        ):
            payload_p = {
                "hookEventName": "PreToolUse",
                "conversationId": "conv-demo-001",
                "toolCall": {"name": tool_nm, "args": {arg_key: (prot_dir / rel).as_posix()}},
            }
            pp = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--mode", "remind", "--protect", str(prot_dir)], stdin_obj=payload_p, check_zero=True)
            assert json.loads(pp.stdout).get("decision") == "deny", f"--protect failed for {key}: {pp.stdout}"

        # 5. --pause-flag via pause_ctl.py (read-only clean_cmd stays allowed; writes denied until unpaused)
        write_payload = {
            "hookEventName": "PreToolUse",
            "conversationId": "conv-demo-001",
            "toolCall": {"name": "write_file", "args": {"Path": (tmp / "repo" / "src" / "app.py").as_posix()}},
        }
        run_py(TOOLS_DIR / "pause_ctl.py", ["on", "--flag", str(pause_flag), "--by", "director", "--reason", "e2e pause test"], check_zero=True)
        p_paused_ro = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--pause-flag", str(pause_flag)], stdin_obj=ag["clean_cmd"], check_zero=True)
        assert json.loads(p_paused_ro.stdout).get("decision") == "allow", f"pause should allow read-only clean_cmd: {p_paused_ro.stdout}"
        p_paused_wr = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--pause-flag", str(pause_flag)], stdin_obj=write_payload, check_zero=True)
        assert json.loads(p_paused_wr.stdout).get("decision") == "deny", f"pause should deny writes: {p_paused_wr.stdout}"
        run_py(TOOLS_DIR / "pause_ctl.py", ["off", "--flag", str(pause_flag)], check_zero=True)
        p_unpaused = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--pause-flag", str(pause_flag)], stdin_obj=write_payload, check_zero=True)
        assert json.loads(p_unpaused.stdout).get("decision") == "allow", f"unpaused write should allow: {p_unpaused.stdout}"

        # 6. Claude protocol compatibility
        p_cl = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--mode", "block", "--format", "claude"], stdin_obj=cl["g02_reset_hard"])
        assert p_cl.returncode == 2, f"claude deny should exit 2, got {p_cl.returncode}"

        # 7. --overwrite-utf8
        p_ov = run_py(TOOLS_DIR / "agy_guard.py", ["--rules", str(rules), "--overwrite-utf8"], stdin_obj=ag["bare_python_cmd"], check_zero=True)
        j_ov = json.loads(p_ov.stdout)
        assert "-I -X utf8" in ((j_ov.get("overwrite") or {}).get("CommandLine") or ""), f"overwrite failed: {j_ov}"

        # 8. Verify audit log written by guard is hash-valid
        run_py(TOOLS_DIR / "audit_log.py", ["verify", "--log", str(audit_file)], check_zero=True)

    return "Antigravity+Claude, modes, G00..G53, protect, pause, overwrite, audit OK"


def test_hooks_cmd_quoting_and_spaces():
    payloads = json.loads((FIXTURES_DIR / "agy_hook_payloads.json").read_text("utf-8"))
    ag = payloads["antigravity"]

    # 1. Live execute plugin/director-work/hooks.json via OS shell
    plug_dir = ROOT_DIR / "plugin" / "director-work"
    plug_hooks = json.loads((plug_dir / "hooks.json").read_text("utf-8"))
    plug_cmd = plug_hooks["agydw-plugin-seatbelt"]["PreToolUse"][0]["hooks"][0]["command"]
    p1 = run_hook_shell(plug_cmd, plug_dir, ag["g50_secret_read_app2"])
    assert p1.returncode == 0 and json.loads(p1.stdout).get("decision") == "deny", f"plugin hook deny failed: rc={p1.returncode} out={p1.stdout} err={p1.stderr}"
    p1r = run_hook_shell(plug_cmd, plug_dir, ag["g01_force_push"])
    assert p1r.returncode == 0 and "[G01]" in json.loads(p1r.stdout).get("reason", ""), f"plugin hook G01 remind failed: {p1r.stdout}"
    p2 = run_hook_shell(plug_cmd, plug_dir, ag["clean_cmd"])
    assert p2.returncode == 0 and json.loads(p2.stdout).get("decision") == "allow", f"plugin hook allow failed: {p2.stdout}"

    # 2. Spaced workspace + agy_setup.py generated .agents/hooks.json
    with tempfile.TemporaryDirectory(prefix="agydw space test ") as td:
        base = pathlib.Path(td)
        repo = base / "my repo"
        director = base / "my director"
        repo.mkdir()
        run_cmd(["git", "init", "-b", "main", str(repo)], check_zero=True)
        run_py(TOOLS_DIR / "agy_setup.py", [
            "--repo", str(repo), "--director", str(director),
            "--rules", str(ROOT_DIR), "--init-director", "--apply",
        ], check_zero=True)
        gen_hooks = json.loads((repo / ".agents" / "hooks.json").read_text("utf-8"))
        pre_cmd = gen_hooks["agydw-guard"]["PreToolUse"][0]["hooks"][0]["command"]
        sess_cmd = gen_hooks["agydw-audit-and-bridge"]["SessionStart"][0]["command"]
        post_audit_cmd = gen_hooks["agydw-audit-and-bridge"]["PostToolUse"][0]["hooks"][0]["command"]
        post_bridge_cmd = gen_hooks["agydw-audit-and-bridge"]["PostToolUse"][0]["hooks"][1]["command"]

        agents_dir = repo / ".agents"
        p_sec = run_hook_shell(pre_cmd, agents_dir, ag["g50_secret_read_app2"])
        assert p_sec.returncode == 0 and json.loads(p_sec.stdout).get("decision") == "deny", f"spaced pre_cmd deny failed: {p_sec.stdout} {p_sec.stderr}"
        p_g01 = run_hook_shell(pre_cmd, agents_dir, ag["g01_force_push"])
        assert p_g01.returncode == 0 and "[G01]" in json.loads(p_g01.stdout).get("reason", ""), f"spaced pre_cmd G01 remind failed: {p_g01.stdout} {p_g01.stderr}"
        p_sess = run_hook_shell(sess_cmd, agents_dir, {"hookEventName": "SessionStart", "conversationId": "c1"})
        assert p_sess.returncode == 0 and "injectSteps" in json.loads(p_sess.stdout), f"spaced sess_cmd failed: {p_sess.stdout} {p_sess.stderr}"
        p_pa = run_hook_shell(post_audit_cmd, agents_dir, {"hookEventName": "PostToolUse", "conversationId": "c1", "toolCall": ag["clean_cmd"]["toolCall"]})
        assert p_pa.returncode == 0 and json.loads(p_pa.stdout) == {}, f"spaced post_audit failed: {p_pa.stdout} {p_pa.stderr}"
        p_pb = run_hook_shell(post_bridge_cmd, agents_dir, {"hookEventName": "PostToolUse", "conversationId": "c1", "toolCall": ag["clean_read"]["toolCall"]})
        assert p_pb.returncode == 0 and json.loads(p_pb.stdout) == {}, f"spaced post_bridge failed: {p_pb.stdout} {p_pb.stderr}"

    return "plugin hooks + spaced-path generated hooks OK under OS shell"


def test_agy_hook_bridge_flow():
    payloads = json.loads((FIXTURES_DIR / "agy_hook_payloads.json").read_text("utf-8"))
    ag = payloads["antigravity"]

    with tempfile.TemporaryDirectory(prefix="agydw_bridge_") as td:
        tmp = pathlib.Path(td)
        state_dir = tmp / "state"
        repo = tmp / "repo"
        repo.mkdir()
        run_cmd(["git", "init", "-b", "main", str(repo)], check_zero=True)
        run_cmd(["git", "-C", str(repo), "config", "user.name", "Demo"], check_zero=True)
        run_cmd(["git", "-C", str(repo), "config", "user.email", "demo@example.invalid"], check_zero=True)
        src = repo / "app.py"
        src.write_text("x = 1\n", encoding="utf-8")
        run_cmd(["git", "-C", str(repo), "add", "-A"], check_zero=True)
        run_cmd(["git", "-C", str(repo), "commit", "-m", "init"], check_zero=True)

        resume = tmp / "RESUME_STATE.md"
        resume.write_text(
            "PROGRESS: init done\nDOING: bridge test\nNEXT_STEP: finish step\nCOMMS: NONE\n\n# RESUME_STATE\nOPEN_DECISIONS: NONE\n",
            encoding="utf-8",
            newline="\n",
        )

        # 1. session-start
        ps = run_py(TOOLS_DIR / "agy_hook_bridge.py", [
            "session-start", "--resume", str(resume), "--state-dir", str(state_dir), "--banner",
        ], stdin_obj={"conversationId": "cid-1"}, check_zero=True)
        js = json.loads(ps.stdout)
        steps_text = json.dumps(js.get("injectSteps", []), ensure_ascii=False)
        assert "PROGRESS: init done" in steps_text, f"session-start missing PROGRESS: {js}"

        # 2. post-tool 3x repeated log reads + prompt-injection transcript scan -> {}
        read_call = {
            "conversationId": "cid-1",
            "toolCall": {"name": "read_file", "args": {"Path": "D:/demo/run.log"}},
        }
        for _ in range(3):
            pp = run_py(TOOLS_DIR / "agy_hook_bridge.py", ["post-tool", "--state-dir", str(state_dir), "--poll-times", "3"], stdin_obj=read_call, check_zero=True)
            assert json.loads(pp.stdout) == {}

        inject_payload = {
            "conversationId": "cid-1",
            "stepIdx": 4,
            "transcriptPath": str(FIXTURES_DIR / "agy_inject_sample.jsonl"),
            "toolCall": {"name": "read_file", "args": {"Path": "D:/demo/repo/untrusted.html"}},
        }
        pi = run_py(TOOLS_DIR / "agy_hook_bridge.py", ["post-tool", "--state-dir", str(state_dir), "--inject-scan"], stdin_obj=inject_payload, check_zero=True)
        assert json.loads(pi.stdout) == {}

        # 3. pre-invocation drains queued alerts AND detects new CHECKPOINT compaction
        tr_compacted = tmp / "transcript.jsonl"
        tr_compacted.write_text((FIXTURES_DIR / "agy_transcript_sample.jsonl").read_text("utf-8"), encoding="utf-8")
        pre = run_py(TOOLS_DIR / "agy_hook_bridge.py", [
            "pre-invocation", "--state-dir", str(state_dir), "--resume", str(resume),
        ], stdin_obj={"conversationId": "cid-1", "transcriptPath": str(tr_compacted)}, check_zero=True)
        jpre = json.loads(pre.stdout)
        msg_blob = json.dumps(jpre.get("injectSteps", []), ensure_ascii=False)
        assert "poll_hint" in msg_blob and "compaction detected" in msg_blob, f"pre-invocation missing alerts: {jpre}"

        # Second pre-invocation should be clean {}
        pre2 = run_py(TOOLS_DIR / "agy_hook_bridge.py", [
            "pre-invocation", "--state-dir", str(state_dir), "--resume", str(resume),
        ], stdin_obj={"conversationId": "cid-1", "transcriptPath": str(tr_compacted)}, check_zero=True)
        assert json.loads(pre2.stdout) == {}, f"second pre-invocation not drained: {pre2.stdout}"

        # 4. stop-check: continuationActive=True -> allow; modified code > resume mtime + 2s -> deny once
        pst1 = run_py(TOOLS_DIR / "agy_hook_bridge.py", ["stop-check", "--resume", str(resume), "--repo", str(repo), "--enforce-resume"], stdin_obj=ag["stop_continuation_active"], check_zero=True)
        assert json.loads(pst1.stdout).get("decision") == "allow"

        old_mtime = time.time() - 30
        os.utime(resume, (old_mtime, old_mtime))
        src.write_text("x = 2\n", encoding="utf-8")
        new_mtime = time.time() + 5
        os.utime(src, (new_mtime, new_mtime))
        pst2 = run_py(TOOLS_DIR / "agy_hook_bridge.py", ["stop-check", "--resume", str(resume), "--repo", str(repo), "--enforce-resume"], stdin_obj=ag["stop_no_tool_call"], check_zero=True)
        assert json.loads(pst2.stdout).get("decision") == "continue", f"stop-check should return continue when code newer than RESUME_STATE: {pst2.stdout}"

    return "session-start, 2-stage post->pre bridge, compaction re-inject, stop anti-loop OK"


def test_agy_audit_and_usage():
    with tempfile.TemporaryDirectory(prefix="agydw_aud_") as td:
        tmp = pathlib.Path(td)
        brain = tmp / "brain"
        p_dir = brain / "00000000-0000-0000-0000-000000000001" / ".system_generated" / "logs"
        c_dir = brain / "11111111-2222-3333-4444-555555555555" / ".system_generated" / "logs"
        p_dir.mkdir(parents=True)
        c_dir.mkdir(parents=True)
        now_utc = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        raw_parent = (FIXTURES_DIR / "agy_transcript_sample.jsonl").read_text("utf-8")
        # Rewrite fixture date/hour prefix to current UTC minute so ran_line.py aligns with rebuilt log
        now_prefix = now_utc.strftime("%Y-%m-%dT%H:%M:")
        raw_parent = raw_parent.replace("2026-11-24T01:00:", now_prefix)
        parent_tr = p_dir / "transcript.jsonl"
        parent_tr.write_text(raw_parent, encoding="utf-8")
        child_tr = c_dir / "transcript.jsonl"
        child_tr.write_text(
            json.dumps({
                "step_index": 0, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
                "created_at": f"{now_prefix}09Z", "model": "gemini-3-flash-low",
                "input_tokens": 200, "cache_read_tokens": 100, "output_tokens": 30,
                "tool_calls": [{"name": "read_file", "args": {"Path": "\"D:/demo/sample-repo/logs/app.log\""}}]
            }) + "\n" +
            json.dumps({
                "step_index": 1, "source": "SYSTEM", "type": "GENERIC", "status": "DONE",
                "created_at": f"{now_prefix}10Z", "content": "OK log"
            }) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        # 1. hook-append -> verify
        hook_log = tmp / "hook_audit.jsonl"
        ha = run_py(TOOLS_DIR / "agy_audit.py", ["hook-append", "--log", str(hook_log)], stdin_obj={
            "conversationId": "c1",
            "toolCall": {"name": "run_command", "args": {"CommandLine": "python -B -I -X utf8 tools/check.py"}}
        }, check_zero=True)
        assert json.loads(ha.stdout) == {}
        run_py(TOOLS_DIR / "audit_log.py", ["verify", "--log", str(hook_log)], check_zero=True)

        # 2. build (--include-subagents) + verify + report_vs_audit
        rebuilt = tmp / "rebuilt_audit.jsonl"
        run_py(TOOLS_DIR / "agy_audit.py", [
            "build", "--transcript", str(parent_tr), "--brain-root", str(brain), "--include-subagents", "--out", str(rebuilt), "--force",
        ], check_zero=True)
        run_py(TOOLS_DIR / "audit_log.py", ["verify", "--log", str(rebuilt)], check_zero=True)

        rep = tmp / "TASK_REPORT.md"
        rep.write_text("# TASK REPORT\nSESSION_ID: 00000000-0000-0000-0000-000000000001\nRAN:\n", encoding="utf-8")
        run_py(TOOLS_DIR / "ran_line.py", ["--to", str(rep), "--exit", "0", "--cmd", "python -B -I -X utf8 tools/check.py"], check_zero=True)
        rva = run_py(TOOLS_DIR / "report_vs_audit.py", ["--report", str(rep), "--log", str(rebuilt), "--session", "00000000-0000-0000-0000-000000000001"], check_zero=True)
        assert "VERIFIED" in rva.stdout or "OK" in rva.stdout or rva.returncode == 0

        # 3. loops --reads
        lp = run_py(TOOLS_DIR / "agy_audit.py", ["loops", "--transcript", str(FIXTURES_DIR / "agy_loop_sample.jsonl"), "--reads", "--json"])
        assert lp.returncode == 1, f"loops fixture should exit 1, got rc={lp.returncode} out={lp.stdout}"
        lp_data = json.loads(lp.stdout)
        assert len(lp_data.get("findings", [])) >= 1 and len(lp_data.get("top_reads", [])) >= 1, f"missing loops/top_reads in {lp_data}"

        # 4. inject
        inj = run_py(TOOLS_DIR / "agy_audit.py", ["inject", "--transcript", str(FIXTURES_DIR / "agy_inject_sample.jsonl")])
        assert "INJECT" in inj.stdout, f"inject fixture should report INJECT, got: {inj.stdout}"

        # 5. agy_usage exact math (parent only & parent + subagent) + NO_DATA
        u_json = tmp / "usage.json"
        up = run_py(TOOLS_DIR / "agy_usage.py", ["--transcript", str(parent_tr), "--json", "--out", str(u_json)], check_zero=True)
        ud = json.loads(up.stdout)
        tots = ud["totals"]
        assert tots["input_tokens"] == 4700 and tots["cache_read_tokens"] == 3100 and tots["output_tokens"] == 400 and tots["total_tokens"] == 8200, f"bad totals: {tots}"
        sess0 = ud["sessions"][0]
        assert sess0["last_prompt_tokens"] == 800 and sess0["peak_prompt_tokens"] == 2700 and tots["checkpoints"] == 1, f"bad prompt/cp: {sess0} {tots}"

        u_sub = run_py(TOOLS_DIR / "agy_usage.py", [
            "--brain-root", str(brain), "--conversation", "00000000-0000-0000-0000-000000000001", "--include-subagents", "--loops", "--json",
        ], check_zero=True)
        us = json.loads(u_sub.stdout)
        assert us["totals"]["total_tokens"] == 8200 + 330 and len(us["sessions"]) == 2, f"subagent usage mismatch: {us['totals']}"

        empty_brain = tmp / "empty_brain"
        empty_brain.mkdir()
        u_empty = run_py(TOOLS_DIR / "agy_usage.py", ["--brain-root", str(empty_brain)])
        assert u_empty.returncode == 2 and "NO_DATA" in u_empty.stdout, f"empty usage should print NO_DATA rc=2, got rc={u_empty.returncode} {u_empty.stdout}"

    return "hook-append, build+subagents, verify, report_vs_audit, loops+reads, inject, exact token math OK"


def test_governance_tools_roundtrip():
    with tempfile.TemporaryDirectory(prefix="agydw_gov_") as td:
        tmp = pathlib.Path(td)
        repo = tmp / "proj"
        director = tmp / "DIRECTOR"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "tests").mkdir()
        (repo / "src" / "__init__.py").write_text("", encoding="utf-8", newline="\n")
        (repo / "tests" / "__init__.py").write_text("", encoding="utf-8", newline="\n")
        (repo / "src" / "demo.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8", newline="\n")
        (repo / "tests" / "test_demo.py").write_text(
            "import unittest\nfrom src.demo import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n",
            encoding="utf-8",
            newline="\n",
        )
        run_cmd(["git", "init", "-b", "main", str(repo)], check_zero=True)
        run_cmd(["git", "-C", str(repo), "config", "user.name", "Demo"], check_zero=True)
        run_cmd(["git", "-C", str(repo), "config", "user.email", "demo@example.invalid"], check_zero=True)
        run_cmd(["git", "-C", str(repo), "add", "-A"], check_zero=True)
        run_cmd(["git", "-C", str(repo), "commit", "-m", "init"], check_zero=True)

        run_py(TOOLS_DIR / "agy_setup.py", [
            "--repo", str(repo), "--director", str(director), "--rules", str(ROOT_DIR), "--init-director", "--apply",
        ], check_zero=True)

        # Configure BASE_REF check locally without remote
        dw_cfg = json.loads((director / "director_work.json").read_text("utf-8"))
        dw_cfg["base_ref"] = {"check": False, "fetch": False, "strict": False}
        (director / "director_work.json").write_text(json.dumps(dw_cfg, indent=2), encoding="utf-8", newline="\n")

        # Prepare DEC_REG DD with verification case and RESUME_STATE
        (director / "DECISION_REGISTER.md").write_text(
            "# Decision Register\n\n## A. PO 決定\n| ID | 日期時間 | 決定 | 為什麼 | 來源 | 狀態 |\n|---|---|---|---|---|---|\n| PO-01 | 2026-11-24 01:00 +08:00 | ok | ok | chat | ACTIVE |\n\n"
            "## B. 總監決定\n| ID | 日期時間 | 決定 | 理由 | 影響範圍 | 回退方式 | 驗收案例 | 狀態 |\n|---|---|---|---|---|---|---|---|\n"
            "| DD-01 | 2026-11-24 01:00 +08:00 | demo | demo | src/ | git | `python -B -m unittest` -> OK | ACTIVE |\n\n"
            "## C. 待 PO 決定\n| ID | 開立時間 | 問題 | 建議 | 狀態 |\n|---|---|---|---|---|\n",
            encoding="utf-8",
            newline="\n",
        )
        (director / "RESUME_STATE.md").write_text(
            "PROGRESS: setup ready\nDOING: WO-001 test\nNEXT_STEP: verify gate\nCOMMS: NONE\n\n# RESUME_STATE — WO-001\nUPDATED: 2026-11-24 01:05 +08:00\n",
            encoding="utf-8",
            newline="\n",
        )

        # Lock test and commit BASE_REF
        (repo / "tests" / "test_demo.py").write_text(
            "import unittest\nfrom src.demo import add, sub\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n    def test_sub(self):\n        self.assertEqual(sub(5, 2), 3)\n",
            encoding="utf-8",
            newline="\n",
        )
        run_cmd(["git", "-C", str(repo), "add", "-A"], check_zero=True)
        run_cmd(["git", "-C", str(repo), "commit", "-m", "lock test for WO-001"], check_zero=True)
        base_sha = run_cmd(["git", "-C", str(repo), "rev-parse", "HEAD"], check_zero=True).stdout.strip()

        lock_out = run_py(TOOLS_DIR / "safety_gate.py", ["--lock-tests", "tests/test_demo.py", "--repo", str(repo)], cwd=repo, check_zero=True).stdout.strip()
        assert "TESTS_LOCKED:" in lock_out and "sha256=" in lock_out, f"bad lock_tests: {lock_out}"

        work_dir = tmp / "work"
        wo_work = work_dir / "WO-001"
        wo_work.mkdir(parents=True)
        wo = director / "orders" / "WO-001-sub.md"
        wo.write_text(
            f"""# WO-001 — sub
**Add sub(a, b) to src/demo.py**

```text
ISSUED_BY      = 總監 2026-11-24 01:10 +08:00
REVISION       = 1
TRACES_TO      = DD-01
PRIMARY_WORKER = 子代理「w1」
MODEL / EFFORT = gemini-3-flash / high
FALLBACK       = 開接班子代理
GOVERNANCE     = Standard
BASE_REF       = main @ {base_sha}
NEW_BRANCH     = w1/sub
WORKTREE       = {repo.as_posix()}
WORK_DIR       = {work_dir.as_posix()}
BUDGET         = US$0
HEAVY_RESOURCE = 無
```

## A. 背景
新增 `sub(a, b)`（VERIFIED_LOCAL）。

## B. 目標
- MUST HAVE：實作 `sub(a, b)`
- OPTIONAL：無
- 不做（OUT OF SCOPE）：不改 `add`

```text
SCOPE:
- src/demo.py
FORBIDDEN:
- tests/test_demo.py
```

## C. 執行前檢查
檢查分支與現有函式。

## D. 實作要求
最小完整實作。

## E. 驗收條件
| ID | 類型 | 要求 | 驗證方法 | 預期 |
|---|---|---|---|---|
| AC-1 | AUTO | 單元測試通過 | `python -B -m unittest tests.test_demo` | exit 0 |

```text
CHECKS:
- AC-1: ["python", "-B", "-m", "unittest", "tests.test_demo"]
```

```text
{lock_out}
```

## F. 安全與版控
照 docs/08。禁止：修改 locked test。

## G. 交付與停止規則
- DELIVERABLES: src/demo.py 與 TASK REPORT
- STOP_RULES: 同一根因 1 輪沒進展就停
""",
            encoding="utf-8",
            newline="\n",
        )
        run_py(TOOLS_DIR / "gate_check.py", ["--order", str(wo), "--repo", str(repo)], check_zero=True)
        h_out = run_py(TOOLS_DIR / "gate_check.py", ["--hash", str(wo)], check_zero=True).stdout
        wo_sha = re.search(r"([0-9a-f]{64})", h_out).group(1)

        plan_file = wo_work / "PLAN.md"
        plan_file.write_text("# PLAN\n- [ ] 1.1 Implement `sub(a, b)` in `src/demo.py`\n", encoding="utf-8", newline="\n")
        plan_line = run_py(TOOLS_DIR / "safety_gate.py", ["--plan-hash", str(plan_file), "--order", str(wo)], check_zero=True).stdout.strip()

        dlog = director / "DIRECTOR_LOG.md"
        now_t = "2026-11-24 01:12 +08:00"
        dlog.write_text(
            f"# DIRECTOR_LOG\n"
            f"{now_t} | DISPATCH WO-001 rev=1 sha={wo_sha} | session=coord-1\n"
            f"2026-11-24 01:13 +08:00 | SPEC_REVIEW WO-001 tokens=500 issues=0/0/0 adopted=0 session=rev-1\n"
            f"2026-11-24 01:14 +08:00 | {plan_line} session=coord-1\n",
            encoding="utf-8",
            newline="\n",
        )

        time.sleep(1.1)
        run_cmd(["git", "-C", str(repo), "checkout", "-b", "w1/sub"], check_zero=True)
        (repo / "src" / "demo.py").write_text(
            "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n",
            encoding="utf-8",
            newline="\n",
        )
        plan_file.write_text("# PLAN\n- [x] 1.1 Implement `sub(a, b)` in `src/demo.py`\n", encoding="utf-8", newline="\n")
        run_cmd(["git", "-C", str(repo), "add", "src/demo.py"], check_zero=True)
        run_cmd(["git", "-C", str(repo), "commit", "--date", "2026-11-24T01:16:00+08:00", "-m", "WO-001: implement sub"], check_zero=True)
        cand = run_cmd(["git", "-C", str(repo), "rev-parse", "HEAD"], check_zero=True).stdout.strip()

        sc_out = run_py(TOOLS_DIR / "scope_check.py", ["--order", str(wo), "--repo", str(repo), "--base", base_sha, "--candidate", cand], check_zero=True).stdout
        sc_last = [ln for ln in sc_out.splitlines() if ln.strip()][-1]
        wt_line = [ln for ln in sc_out.splitlines() if ln.startswith("WORKTREE:")][0]
        pc_line = [ln for ln in sc_out.splitlines() if ln.startswith("PROTECTED_CHANGED:")][0]

        evid_dir = director / "evidence"
        run_py(TOOLS_DIR / "evidence_record.py", [
            "run", "--label", "AC-1", "--order", str(wo), "--cwd", str(repo), "--out-dir", str(evid_dir),
            "--", "python", "-B", "-m", "unittest", "tests.test_demo",
        ], cwd=repo, check_zero=True)
        ev_json = next(evid_dir.glob("*-AC-1.json"))

        run_py(TOOLS_DIR / "evidence_record.py", [
            "verify", "--order", str(wo), "--dispatch-log", str(dlog), "--repo", str(repo), "--candidate", cand, "--rerun", str(ev_json),
        ], check_zero=True)

        skel_path = wo_work / "skel.md"
        run_py(TOOLS_DIR / "gate_check.py", ["--report-skeleton", str(wo), "--repo", str(repo), "--out", str(skel_path)], check_zero=True)
        assert skel_path.is_file()

        report = wo_work / "WO-001_TASK_REPORT.md"
        report.write_text(
            f"""# TASK REPORT — WO-001
Implemented sub(a, b) with unit tests passing.
TASK_ID / WORKER / MODEL / BRANCH / BASE / HEAD / WORKTREE_CLEAN(yes|no): WO-001 / w1 / gemini-3-flash / w1/sub / {base_sha} / {cand} / yes
ORDER: WO-001-sub.md REVISION 1
ORDER_SHA256: {wo_sha}
{wt_line}
RESULT: PASS
  ENGINEERING: PASS
  PRODUCT（成品）: PASS
  PERFORMANCE: NOT_RUN
  STABILITY: PASS
DONE: src/demo.py 新增 sub(a, b)
TESTS: python -B -m unittest tests.test_demo (OK)
{sc_last}
{pc_line}
EVIDENCE: {ev_json.name}
AGENT_NAME: w1
SESSION_ID: worker-1
RAN:
ACCEPTANCE:
| ID | 實際 | 證據 | 狀態 |
|---|---|---|---|
| AC-1 | 單元測試通過 | {ev_json.name} | PASS |
COMMON_SENSE_GATE: 不適用（純程式庫函式）
UNVERIFIED: NONE
KNOWN_ISSUES: NONE
FAILED_ATTEMPTS: NONE
PACKS: NONE
DECISIONS_NEEDED: NONE
NEXT: 送總監驗收
""",
            encoding="utf-8",
            newline="\n",
        )
        run_py(TOOLS_DIR / "ran_line.py", ["--to", str(report), "--exit", "0", "--cmd", "python -B -m unittest tests.test_demo"], check_zero=True)

        run_py(TOOLS_DIR / "gate_check.py", [
            "--match", str(report), str(wo),
            "--evidence-dir", str(evid_dir),
            "--dispatch-log", str(dlog),
            "--repo", str(repo),
            "--candidate", cand,
        ], check_zero=True)

        verdict = director / "reviews" / "WO-001_GATE_VERDICT.md"
        verdict.write_text(
            f"""# GATE VERDICT — WO-001 sub
2026-11-24 01:18 +08:00 總監
ORDER: WO-001-sub.md REVISION 1
CANDIDATE_HEAD: {cand}
SESSION_ID: coord-1

## A. 判定
PASS
分面向：工程 PASS / 成品 PASS / 效能 NOT_RUN / 穩定性 PASS / 整合 PASS / PO 驗收：未做（保留給 PO）
下一步：ACCEPT_AND_CLOSE
- `python -B -m unittest tests.test_demo` exit=0 at=01:18

## B. 已證實成果
| 項目 | 證據路徑 | 證據等級 | 依 worker 報告？ |
|---|---|---|---|
| sub(a, b) | {ev_json.name} | VERIFIED_LOCAL | 否（已重跑） |

## C. 問題
- P0：無
- P1：無
- P2：無

## D. 取捨
最小實作。

## E. 下一張工單
無

## F. 停止狀態
NO MERGE / NO PUSH / NO FINAL ACCEPTANCE
""",
            encoding="utf-8",
            newline="\n",
        )
        run_py(TOOLS_DIR / "gate_check.py", [
            "--verdict", str(verdict), "--match", str(report), str(wo),
            "--evidence-dir", str(evid_dir), "--dispatch-log", str(dlog), "--repo", str(repo),
            "--candidate", cand, "--config", str(director / "director_work.json"),
        ], check_zero=True)
        run_py(TOOLS_DIR / "gate_check.py", ["--sequence", str(wo), "--dispatch-log", str(dlog), "--repo", str(repo)], check_zero=True)

        e2e_dir = director / "evidence" / "E2E"
        e2e_dir.mkdir(parents=True, exist_ok=True)
        (e2e_dir / "e2e_sub.txt").write_text("ENTRY_POINT: src.demo.sub\nsub(5, 2) == 3\n", encoding="utf-8", newline="\n")
        (director / "usage.json").write_text(json.dumps({"totals": {"total_tokens": 1000}}), encoding="utf-8", newline="\n")
        run_py(TOOLS_DIR / "gate_check.py", ["--final", str(director), "--usage", str(director / "usage.json")], check_zero=True)

        # memory_size_check --kind rules
        run_py(TOOLS_DIR / "memory_size_check.py", ["--kind", "rules", "--file", str(ROOT_DIR / "plugin" / "director-work" / "rules" / "AGENTS.md")], check_zero=True)

        # status_gen
        st_cfg = director / "status.json"
        st_cfg.write_text(json.dumps({
            "schema_version": 1,
            "name": "demo",
            "po_pending": {"path": (director / "PO_PENDING.md").as_posix(), "counts": [{"label": "open", "regex": "^STATUS:\\s*open\\b"}]},
            "orders": {"dir": (director / "orders").as_posix(), "glob": "WO-*.md", "show": 5},
            "disk": repo.drive + "/",
            "disk_min_gib": 0.1,
            "max_lines": 40,
        }), encoding="utf-8", newline="\n")
        st_py = director / "status.py"
        run_py(TOOLS_DIR / "status_gen.py", ["--config", str(st_cfg), "--out", str(st_py), "--force"], check_zero=True)
        run_py(st_py, [], check_zero=True)

        # agy_setup_check --strict & agy_doctor --probe-hooks
        run_py(TOOLS_DIR / "agy_setup_check.py", ["--strict", str(director), "--repo", str(repo)], check_zero=True)
        doc = run_py(TOOLS_DIR / "agy_doctor.py", ["--repo", str(repo), "--plugin", str(ROOT_DIR / "plugin" / "director-work"), "--probe-hooks", "--json"], check_zero=True)
        assert json.loads(doc.stdout)["counts"]["FAIL"] == 0

    return "full WO->PLAN->commit->evidence->match->verdict->sequence->final->status->doctor roundtrip OK"


def test_plugin_budgets_vocab_and_hygiene():
    # 1. plugin.json strict official Antigravity schema keys & types
    pjson = json.loads((ROOT_DIR / "plugin" / "director-work" / "plugin.json").read_text("utf-8"))
    allowed = {
        "$schema", "name", "displayName", "display_name", "description", "version", "disabled",
        "logo", "suggestedPrompts", "suggested_prompts", "skills", "author", "homepage",
        "repository", "license", "keywords", "extensions", "source", "sourcePath", "source_path",
    }
    extra = set(pjson.keys()) - allowed
    assert not extra, f"disallowed plugin.json keys: {extra}"
    assert isinstance(pjson.get("repository"), str), f"plugin.json repository must be string URL, got {type(pjson.get('repository')).__name__}"
    assert len(pjson.get("suggestedPrompts", [])) <= 3

    # 2. Subagent tools guard (prevent IDE unmapped tool hang bug)
    bad_tools = {"view_file", "grep_search", "find_by_name", "list_dir", "write_to_file", "replace_file_content", "multi_replace_file_content"}
    for agent_md in sorted((ROOT_DIR / "plugin" / "director-work" / "agents").glob("*.md")):
        txt = agent_md.read_text("utf-8")
        m = re.search(r"(?ms)^---\n(.*?)\n---", txt)
        assert m, f"missing YAML frontmatter in {agent_md.name}"
        tline = [ln for ln in m.group(1).splitlines() if ln.strip().startswith("tools:")]
        assert tline, f"missing tools: in {agent_md.name}"
        listed = re.findall(r"[A-Za-z0-9_]+", tline[0].split(":", 1)[1])
        bad_found = bad_tools & set(listed)
        assert not bad_found, f"unmapped hang-risk tool(s) in {agent_md.name}: {bad_found}"
        if agent_md.name == "dw-blind-reviewer.md":
            assert listed == ["read_file"], f"dw-blind-reviewer tools must be [read_file], got {listed}"

    # 3. Budgets
    rules_size = (ROOT_DIR / "plugin" / "director-work" / "rules" / "AGENTS.md").stat().st_size
    assert rules_size < 8500, f"plugin rules/AGENTS.md too large: {rules_size} >= 8500"
    must_read = ["docs/01_roles_authority.md", "docs/03_communication.md", "docs/06_resume_lossless.md", "docs/08_safety_rules.md"]
    mr_sum = sum((ROOT_DIR / f).stat().st_size for f in must_read)
    assert mr_sum <= 9550, f"must-read docs over budget: {mr_sum} > 9550"

    # 4. Hooks schema check on plugin + template
    for hpath in (ROOT_DIR / "plugin" / "director-work" / "hooks.json", ROOT_DIR / "templates" / "hooks.antigravity.json"):
        hdata = json.loads(hpath.read_text("utf-8"))
        assert "hooks" not in hdata, f"{hpath.name} uses Claude wrapper instead of Antigravity named group"

    # 5. Workflows AST parse
    for wf in sorted((ROOT_DIR / "templates" / "workflows").glob("*.py")):
        ast.parse(wf.read_text("utf-8"), filename=str(wf))

    # 6. Vocabulary alignment spot-check across core templates
    vocab = json.loads((TOOLS_DIR / "vocabulary.json").read_text("utf-8"))
    rs_tpl = (ROOT_DIR / "templates" / "RESUME_STATE.md").read_text("utf-8")
    for tok in vocab["resume_header"]:
        assert tok in rs_tpl, f"missing {tok} in templates/RESUME_STATE.md"
    gv_tpl = (ROOT_DIR / "templates" / "GATE_VERDICT.md").read_text("utf-8")
    for tok in vocab["verdicts"] + vocab["next_steps"]:
        assert tok in gv_tpl, f"missing {tok} in templates/GATE_VERDICT.md"

    # 7. Repo-wide LF, JSON validity, PII / user-path / secret scan
    secret_pat = re.compile(r"(?:ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{35}|-----BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY-----)")
    user_path_pat = re.compile(r"C:[\\/]Users[\\/]User\b", re.I)
    for p in sorted(ROOT_DIR.rglob("*")):
        if not p.is_file() or ".git" in p.parts:
            continue
        assert "__pycache__" not in p.parts and p.suffix != ".pyc", f"bytecode artifact in repo: {p}"
        raw = p.read_bytes()
        assert b"\r\n" not in raw, f"CRLF detected in {p.relative_to(ROOT_DIR)}"
        if p.suffix == ".json":
            json.loads(raw.decode("utf-8"))
        if p.name != "selftest_agy.py":
            txt = raw.decode("utf-8", errors="replace")
            assert not user_path_pat.search(txt), f"real user path leaked in {p.relative_to(ROOT_DIR)}"
            assert not secret_pat.search(txt), f"secret-like token leaked in {p.relative_to(ROOT_DIR)}"

    return f"plugin strict, rules={rules_size}B, must-read={mr_sum}B, LF/JSON/PII clean"


def test_director_protect_permission():
    rules = TOOLS_DIR / "guard_rules.json"
    with tempfile.TemporaryDirectory(prefix="agydw_prot_") as td:
        tmp = pathlib.Path(td)
        director = tmp / "DIRECTOR"
        (director / "orders").mkdir(parents=True)
        order_payload = {
            "hookEventName": "PreToolUse",
            "conversationId": "conv-prot-1",
            "toolCall": {"name": "write_file", "args": {"Path": (director / "orders" / "WO-001.md").as_posix()}},
        }
        log_payload = {
            "hookEventName": "PreToolUse",
            "conversationId": "conv-prot-1",
            "toolCall": {"name": "replace_file_content", "args": {"TargetFile": (director / "DIRECTOR_LOG.md").as_posix()}},
        }
        for label, payload in (("order", order_payload), ("log", log_payload)):
            for mode in ("remind", "block"):
                pw = run_py(TOOLS_DIR / "agy_guard.py", [
                    "--rules", str(rules), "--profile", "worker", "--mode", mode, "--protect", str(director),
                ], stdin_obj=payload, check_zero=True)
                assert json.loads(pw.stdout).get("decision") == "deny", f"worker must deny {label} in {mode}: {pw.stdout}"

                pd = run_py(TOOLS_DIR / "agy_guard.py", [
                    "--rules", str(rules), "--profile", "director", "--mode", mode, "--protect", str(director),
                ], stdin_obj=payload, check_zero=True)
                assert json.loads(pd.stdout).get("decision") == "allow", f"director must allow {label} in {mode}: {pd.stdout}"
    return "worker denied vs director allowed on orders/ and DIRECTOR_LOG.md"


def test_merge_setup_and_honest_doctor():
    with tempfile.TemporaryDirectory(prefix="agydw_merge_") as td:
        tmp = pathlib.Path(td)
        repo = tmp / "proj"
        director = tmp / "DIRECTOR"
        repo.mkdir()
        run_cmd(["git", "init", "-b", "main", str(repo)], check_zero=True)
        (repo / ".agents").mkdir()
        custom_hooks = {
            "user-custom-lint": {
                "PreToolUse": [{"matcher": "run_command", "hooks": [{"type": "command", "command": "echo ok"}]}]
            }
        }
        custom_plugins = {"entries": [{"path": (tmp / "other-plugin").as_posix()}]}
        (repo / ".agents" / "hooks.json").write_text(json.dumps(custom_hooks, indent=2), encoding="utf-8", newline="\n")
        (repo / ".agents" / "plugins.json").write_text(json.dumps(custom_plugins, indent=2), encoding="utf-8", newline="\n")

        # Pre-populate valid DIRECTOR files manually so only missing .agents agydw wiring triggers failure
        run_py(TOOLS_DIR / "agy_setup.py", [
            "--repo", str(repo), "--director", str(director), "--rules", str(ROOT_DIR), "--init-director", "--apply",
        ], check_zero=False)
        # First test BEFORE agydw merge by restoring custom-only .agents files
        (repo / ".agents" / "hooks.json").write_text(json.dumps(custom_hooks, indent=2), encoding="utf-8", newline="\n")
        (repo / ".agents" / "plugins.json").write_text(json.dumps(custom_plugins, indent=2), encoding="utf-8", newline="\n")
        (director / "DIRECTOR_STATE.md").write_text("# DIRECTOR_STATE\nPENDING_METHOD: ask_question + PO_PENDING.md\n", encoding="utf-8", newline="\n")
        (director / "RESUME_STATE.md").write_text(
            "PROGRESS: init\nDOING: merge test\nNEXT_STEP: verify\nCOMMS: NONE\n\nUPDATED: 2026-11-24 02:00 +08:00\n",
            encoding="utf-8",
            newline="\n",
        )

        # Before merge: --strict and doctor MUST fail and doctor must NOT say ENFORCED
        sc_before = run_py(TOOLS_DIR / "agy_setup_check.py", ["--strict", str(director), "--repo", str(repo)])
        assert sc_before.returncode == 1, f"setup_check --strict must fail without agydw hooks/plugins: {sc_before.stdout}"
        doc_before = run_py(TOOLS_DIR / "agy_doctor.py", ["--repo", str(repo), "--json"])
        db = json.loads(doc_before.stdout)
        assert doc_before.returncode == 1 and db["counts"]["FAIL"] == 2 and "ENFORCED" not in db["counts"], (
            f"doctor must fail without agydw wiring & never report ENFORCED: {doc_before.stdout}"
        )

        # Corrupted hooks JSON must cause agy_setup --apply to exit 1 (never falsely say DONE)
        (repo / ".agents" / "hooks.json").write_text("{broken json", encoding="utf-8", newline="\n")
        bad_setup = run_py(TOOLS_DIR / "agy_setup.py", ["--repo", str(repo), "--director", str(director), "--rules", str(ROOT_DIR), "--apply"])
        assert bad_setup.returncode == 1 and "DONE" not in bad_setup.stdout, f"broken hooks must fail setup: {bad_setup.stdout}"

        # Restore custom hooks and run non-destructive merge (NO --force)
        (repo / ".agents" / "hooks.json").write_text(json.dumps(custom_hooks, indent=2), encoding="utf-8", newline="\n")
        good_setup = run_py(TOOLS_DIR / "agy_setup.py", [
            "--repo", str(repo), "--director", str(director), "--rules", str(ROOT_DIR), "--apply",
        ], check_zero=True)
        assert "MERGE " in good_setup.stdout, f"expected MERGE lines: {good_setup.stdout}"

        merged_h = json.loads((repo / ".agents" / "hooks.json").read_text("utf-8"))
        assert "user-custom-lint" in merged_h and "agydw-guard" in merged_h and "agydw-audit-and-bridge" in merged_h
        merged_p = json.loads((repo / ".agents" / "plugins.json").read_text("utf-8"))
        assert len(merged_p["entries"]) == 2 and merged_p["entries"][0]["path"].endswith("other-plugin")

        # After merge: --strict passes & doctor reports CONFIGURED + HOOK_EXEC_OK with zero FAIL
        run_py(TOOLS_DIR / "agy_setup_check.py", ["--strict", str(director), "--repo", str(repo)], check_zero=True)
        doc_after = json.loads(run_py(TOOLS_DIR / "agy_doctor.py", [
            "--repo", str(repo), "--plugin", str(ROOT_DIR / "plugin" / "director-work"), "--json",
        ], check_zero=True).stdout)
        assert doc_after["counts"]["FAIL"] == 0 and doc_after["counts"]["HOOK_EXEC_OK"] >= 1 and doc_after["counts"]["CONFIGURED"] == 2
        assert "ENFORCED" not in doc_after["counts"]

    return "non-destructive merge preserves custom .agents configs; doctor & setup_check honest"


def test_standalone_plugin_and_official_schema():
    payloads = json.loads((FIXTURES_DIR / "agy_hook_payloads.json").read_text("utf-8"))
    ag = payloads["antigravity"]

    with tempfile.TemporaryDirectory(prefix="agydw_iso_plugin_") as td:
        tmp = pathlib.Path(td)
        iso_plug = tmp / "standalone-plugin"
        shutil.copytree(ROOT_DIR / "plugin" / "director-work", iso_plug)

        # Ensure ../../tools does NOT exist around iso_plug
        assert not (iso_plug.parent.parent / "tools").exists()

        # Execute hooks.json command directly from isolated plugin folder via OS shell
        ph_obj = json.loads((iso_plug / "hooks.json").read_text("utf-8"))
        cmd_line = ph_obj["agydw-plugin-seatbelt"]["PreToolUse"][0]["hooks"][0]["command"]
        p_deny = run_hook_shell(cmd_line, iso_plug, ag["g50_secret_read_app2"])
        assert p_deny.returncode == 0 and json.loads(p_deny.stdout).get("decision") == "deny", f"standalone deny failed: {p_deny.stdout} {p_deny.stderr}"
        p_g01 = run_hook_shell(cmd_line, iso_plug, ag["g01_force_push"])
        assert p_g01.returncode == 0 and "[G01]" in json.loads(p_g01.stdout).get("reason", ""), f"standalone G01 failed: {p_g01.stdout}"
        p_ok = run_hook_shell(cmd_line, iso_plug, ag["clean_cmd"])
        assert p_ok.returncode == 0 and json.loads(p_ok.stdout).get("decision") == "allow", f"standalone allow failed: {p_ok.stdout}"

        # Doctor validates official schema & executes standalone seatbelt hook
        doc = json.loads(run_py(TOOLS_DIR / "agy_doctor.py", ["--plugin", str(iso_plug), "--json"], check_zero=True).stdout)
        assert doc["counts"]["FAIL"] == 0 and doc["counts"]["HOOK_EXEC_OK"] == 1

        # Mutate repository to dict (invalid per official schema) -> doctor MUST fail
        pj = json.loads((iso_plug / "plugin.json").read_text("utf-8"))
        pj["repository"] = {"type": "git", "url": "https://example.invalid/repo"}
        (iso_plug / "plugin.json").write_text(json.dumps(pj), encoding="utf-8", newline="\n")
        doc_bad = run_py(TOOLS_DIR / "agy_doctor.py", ["--plugin", str(iso_plug), "--json"])
        assert doc_bad.returncode == 1 and "repository must be string" in doc_bad.stdout

    return "standalone plugin seatbelt works without ../../tools + official JSON Schema type guard OK"


def test_audit_unverified_and_arguments_compat():
    with tempfile.TemporaryDirectory(prefix="agydw_honest_aud_") as td:
        tmp = pathlib.Path(td)
        audit_log = tmp / "hook.jsonl"
        now_utc = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        ts_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. PostToolUse without error & without transcript match -> MUST record status=unverified (never ok)
        ha = run_py(TOOLS_DIR / "agy_audit.py", ["hook-append", "--log", str(audit_log)], stdin_obj={
            "conversationId": "conv-unv",
            "stepIdx": 999,
            "toolCall": {"name": "run_command", "arguments": "{\"CommandLine\": \"git status\"}"},
        }, check_zero=True)
        assert json.loads(ha.stdout) == {}
        entries = [json.loads(ln) for ln in audit_log.read_text("utf-8").splitlines() if ln.strip()]
        assert entries[0]["result"]["status"] == "unverified", f"expected unverified, got {entries[0]}"
        assert "git status" in entries[0]["input"], f"arguments JSON string lost: {entries[0]['input']}"

        ver = run_py(TOOLS_DIR / "agy_audit.py", ["verify", "--log", str(audit_log)], check_zero=True)
        assert "AGY_AUDIT_STATUS: chain=OK confirmed_ok=0 confirmed_error=0 unverified=1" in ver.stdout, ver.stdout

        rep = tmp / "TASK_REPORT.md"
        rep.write_text("# TASK REPORT\nSESSION_ID: conv-unv\nRAN:\n", encoding="utf-8")
        run_py(TOOLS_DIR / "ran_line.py", ["--to", str(rep), "--exit", "0", "--cmd", "git status"], check_zero=True)
        rva = run_py(TOOLS_DIR / "report_vs_audit.py", ["--report", str(rep), "--log", str(audit_log), "--session", "conv-unv"])
        assert rva.returncode == 1 and "unverified" in rva.stdout, f"report_vs_audit must reject unverified log as PASS: rc={rva.returncode} {rva.stdout}"

        # 2. agy_audit build on transcript using `arguments` JSON string field
        tr = tmp / "tr_args.jsonl"
        tr.write_text(
            json.dumps({
                "step_index": 0, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
                "created_at": ts_str, "model": "gemini-3-flash",
                "tool_calls": [{"name": "run_command", "arguments": "{\"CommandLine\": \"git status\"}"}],
            }) + "\n" +
            json.dumps({
                "step_index": 1, "source": "SYSTEM", "type": "GENERIC", "status": "DONE",
                "created_at": ts_str, "content": "Command exited with code 0.\nnothing to commit",
            }) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        rebuilt = tmp / "rebuilt.jsonl"
        run_py(TOOLS_DIR / "agy_audit.py", ["build", "--transcript", str(tr), "--out", str(rebuilt), "--force"], check_zero=True)
        r_entries = [json.loads(ln) for ln in rebuilt.read_text("utf-8").splitlines() if ln.strip()]
        assert any("git status" in e.get("input", "") for e in r_entries), f"build missed arguments JSON string: {r_entries}"
        v_rebuilt = run_py(TOOLS_DIR / "agy_audit.py", ["verify", "--log", str(rebuilt)], check_zero=True)
        assert "confirmed_ok=1 confirmed_error=0 unverified=0" in v_rebuilt.stdout, v_rebuilt.stdout

    return "unverified missing-output audit + arguments JSON-string rebuild OK"


def test_docs_cli_and_test_request_template():
    for rel in ("README.md", "BOOTSTRAP.md", "plugin/director-work/skills/dw-review-gate/SKILL.md"):
        txt = (ROOT_DIR / rel).read_text("utf-8")
        assert not re.search(r"evidence_record\.py\s+verify[^\n]*--evidence-dir", txt), f"broken --evidence-dir flag in {rel}"
        assert "evidence_record.py verify" in txt and "--dispatch-log" in txt and "--candidate" in txt and "--rerun" in txt, (
            f"missing full verify --rerun flags in {rel}"
        )

    # Validate TEST_REQUEST.md template fields directly with upstream gate_check.py --test-request
    tr_raw = (ROOT_DIR / "templates" / "TEST_REQUEST.md").read_text("utf-8")
    replacements = {
        "<FUNC|BENCH> <內容> <上限分鐘>": "FUNC calc-suite 30",
        "<worker 名稱與 conversationId>": "w1 (conv-1234)",
        "<這次測試要回答哪一個決定或驗收項>": "Verify AC-1 after divide feature",
        "<新產物或暫存資料夾的 GiB，沒有寫 0>": "0.5",
        "<組數> x <每組張數> x <單張 MiB> = <GiB>（沒有擷取寫 NONE）": "NONE",
        "<快取或重匯入餘量 GiB，沒有寫 0>": "0.5",
        "<預計寫入的 GiB，至少 BUILD_GB + CAPTURE + CACHE_GB>": "1.0",
        "<組數> x <單組實測分鐘（含逾時）> = <分鐘>": "1 x 10 = 10",
    }
    tr_filled = tr_raw
    for k, v in replacements.items():
        tr_filled = tr_filled.replace(k, v)
    tr_filled = tr_filled.replace("TIER: 1 批次（預設） | 2 單項（寫理由：P0 / PO 在等 / 下一步依賴 / 耦合無法用開關分 / 單一根因驗證）", "TIER: 1 批次")
    for placeholder, val in (
        ("這次測試要回答哪一個決定或驗收項", "Verify AC-1"),
        ("最小範圍，以及為什麼不能更小", "calc module only"),
        ("這次修改包含的每一項變更，以及對應的開關", "add divide switch"),
        ("要跑的開 / 關組合", "divide=on"),
        ("每個開關、視角、輸出項的程式位置（檔案:行號，在要跑的 commit 裡）；計畫檔可交 run_plan_check.py 核對（2.6.0）", "calc.py:8"),
        ("需要獨佔重資源／安靜時段的分鐘數（沒有寫 0）", "0"),
        ("一般時段的分鐘數", "10"),
        ("第 0 級（離線、純邏輯）檢查已做完的項目", "unittest passed"),
    ):
        tr_filled = tr_filled.replace(placeholder, val)

    with tempfile.TemporaryDirectory(prefix="agydw_tr_") as td:
        tr_path = pathlib.Path(td) / "TR-001.md"
        tr_path.write_text(tr_filled, encoding="utf-8", newline="\n")
        run_py(TOOLS_DIR / "gate_check.py", ["--test-request", str(tr_path)], check_zero=True)

    return "README/BOOTSTRAP/SKILL verify --rerun flags + TEST_REQUEST.md gate_check OK"


def test_incremental_transcript_perf():
    import importlib.util
    spec_b = importlib.util.spec_from_file_location("_test_bridge", TOOLS_DIR / "agy_hook_bridge.py")
    mod_b = importlib.util.module_from_spec(spec_b)
    spec_b.loader.exec_module(mod_b)
    spec_a = importlib.util.spec_from_file_location("_test_aud", TOOLS_DIR / "agy_audit.py")
    mod_a = importlib.util.module_from_spec(spec_a)
    spec_a.loader.exec_module(mod_a)

    with tempfile.TemporaryDirectory(prefix="agydw_perf_") as td:
        tmp = pathlib.Path(td)
        tp = tmp / "transcript.jsonl"
        pad = "x" * 900
        lines = []
        for i in range(5200):
            if i == 2500:
                lines.append(json.dumps({"step_index": i, "type": "CHECKPOINT", "status": "DONE", "content": "compact"}))
            else:
                lines.append(json.dumps({"step_index": i, "type": "GENERIC", "status": "DONE", "content": pad}))
        lines.append(json.dumps({
            "step_index": 5200, "type": "GENERIC", "status": "DONE", "content": "tail sentinel step output ok",
        }))
        tp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        size_mb = round(tp.stat().st_size / (1024 * 1024), 2)

        t0 = time.perf_counter()
        txt = mod_b.find_step_text(str(tp), 5200)
        t_find_b = (time.perf_counter() - t0) * 1000.0
        assert "tail sentinel step output ok" in txt

        t1 = time.perf_counter()
        cnt1, off1 = mod_b.count_checkpoints_incremental(str(tp), 0, 0)
        t_cp1 = (time.perf_counter() - t1) * 1000.0
        assert cnt1 == 1 and off1 == tp.stat().st_size

        # Append one new CHECKPOINT and measure incremental delta scan
        with open(tp, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"step_index": 5201, "type": "CHECKPOINT", "status": "DONE", "content": "compact2"}) + "\n")
        t2 = time.perf_counter()
        cnt2, off2 = mod_b.count_checkpoints_incremental(str(tp), cnt1, off1)
        t_cp2 = (time.perf_counter() - t2) * 1000.0
        assert cnt2 == 2 and off2 == tp.stat().st_size

        t3 = time.perf_counter()
        found, st_val, body = mod_a.find_step_in_transcript(str(tp), 5200)
        t_find_a = (time.perf_counter() - t3) * 1000.0
        assert found and st_val == "DONE" and "tail sentinel step output ok" in body

    return f"{size_mb}MB transcript: tail_find={t_find_b:.1f}+{t_find_a:.1f}ms, cp_init={t_cp1:.1f}ms, cp_inc={t_cp2:.2f}ms"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", "-v", action="store_true", help="print every passing check")
    ap.add_argument("--upstream", default="D:/DIRECTOR-WORK", help="optional reference DW checkout for v2.6.0 SHA-256 check")
    args = ap.parse_args(argv)

    r = Runner(verbose=args.verbose)
    print(f"SELFTEST_AGY root={ROOT_DIR.as_posix()}")
    r.check("upstream_v2.6.0_byte_identity", lambda: test_upstream_identity(args.upstream))
    r.check("tools_help_smoke", test_tools_help)
    r.check("agy_guard_matrix", test_agy_guard_matrix)
    r.check("director_protect_permission", test_director_protect_permission)
    r.check("hooks_cmd_quoting_and_spaces", test_hooks_cmd_quoting_and_spaces)
    r.check("merge_setup_and_honest_doctor", test_merge_setup_and_honest_doctor)
    r.check("standalone_plugin_and_official_schema", test_standalone_plugin_and_official_schema)
    r.check("agy_hook_bridge_flow", test_agy_hook_bridge_flow)
    r.check("agy_audit_and_usage", test_agy_audit_and_usage)
    r.check("audit_unverified_and_arguments_compat", test_audit_unverified_and_arguments_compat)
    r.check("docs_cli_and_test_request_template", test_docs_cli_and_test_request_template)
    r.check("incremental_transcript_perf", test_incremental_transcript_perf)
    r.check("governance_tools_roundtrip", test_governance_tools_roundtrip)
    r.check("plugin_budgets_vocab_and_hygiene", test_plugin_budgets_vocab_and_hygiene)

    print(f"SELFTEST_AGY_SUMMARY: pass={r.passed} skip={r.skipped} fail={r.failed}")
    return 1 if r.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
