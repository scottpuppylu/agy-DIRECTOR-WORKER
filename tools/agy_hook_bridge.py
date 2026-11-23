#!/usr/bin/env python3
"""Antigravity lifecycle hook bridge (SessionStart / PostToolUse / PreInvocation / Stop).

Antigravity's PostToolUse hook expects '{}' on stdout and cannot inject additionalContext
directly into the model turn like Claude Code. This bridge restores every DIRECTOR-WORK
guardrail cleanly using Antigravity's documented hook contracts:

  session-start   Reads SessionStart stdin; renders RESUME_STATE header + open items via
                  upstream resume_inject.py and returns injectSteps (systemMessage).
  post-tool       Reads PostToolUse stdin; detects repeated log polling (poll_hint logic)
                  and scans tool output in transcriptPath via upstream inject_scan.py. Queues
                  any warning to --state-dir and outputs '{}' on stdout.
  pre-invocation  Reads PreInvocation stdin; drains queued warnings from --state-dir AND
                  checks transcriptPath for new SYSTEM/CHECKPOINT compaction steps since the
                  last seen invocation; emits injectSteps (systemMessage) or '{}'.
  stop-check      Reads Stop stdin; immediately returns {"decision": "allow"} whenever
                  continuationActive is true or terminationReason != "NO_TOOL_CALL" (prevents
                  loops). With --enforce-resume, blocks once when tracked git files changed
                  more recently than RESUME_STATE.md (or RESUME_STATE header is missing).

Standard library only. Safe on Windows cmd /c and POSIX sh -c.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOG_EXT = (".log", ".out", ".err", ".txt")
LOG_READ_CMD = re.compile(r"(?:^|[;&|]\s*)(?:tail|head|less|cat|type|get-content|gc)\b", re.I)


def load_sibling(name):
    """Load a sibling Python module by file name."""
    spec = importlib.util.spec_from_file_location(f"_agy_bridge_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_stdin_json():
    """Parse JSON object from stdin, returning {} on empty or malformed input."""
    raw = sys.stdin.read() if sys.stdin is not None else ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def safe_cid(cid):
    """Sanitize conversationId for use in state filenames."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", cid or "default") or "default"


def queue_alert(state_dir, conv_id, message):
    """Append an alert message to pending_<conv>.jsonl under state_dir."""
    folder = pathlib.Path(state_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"pending_{safe_cid(conv_id)}.jsonl"
    entry = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "message": message}
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def drain_alerts(state_dir, conv_id):
    """Atomically read and clear queued alerts for conv_id."""
    folder = pathlib.Path(state_dir)
    path = folder / f"pending_{safe_cid(conv_id)}.jsonl"
    if not path.is_file():
        return []
    tmp = folder / f"draining_{safe_cid(conv_id)}_{os.getpid()}.jsonl"
    try:
        os.replace(path, tmp)
    except OSError:
        return []
    messages = []
    try:
        for line in tmp.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict) and isinstance(item.get("message"), str):
                    messages.append(item["message"])
            except ValueError:
                continue
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return messages


def render_resume_text(resume_path, max_bytes=2048):
    """Render RESUME_STATE header + open sections using upstream resume_inject.render."""
    ri = load_sibling("resume_inject")
    path = pathlib.Path(resume_path)
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as err:
        return f"[agy_hook_bridge] no RESUME_STATE read from {path.as_posix()} ({type(err).__name__}); inspect project state manually."
    return ri.render(text, path.as_posix(), max_bytes, ri.DEFAULT_SECTIONS).rstrip("\n")


def _scan_tail_step(path, step_idx, max_scan_bytes=2 * 1024 * 1024):
    """Scan JSONL backwards in bounded non-overlapping windows using a byte prefilter before JSON parsing."""
    if not path.is_file() or step_idx is None:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0:
        return None
    needle = str(step_idx).encode("ascii")
    windows = (65536, 196608, 786432, 1048576)
    pos = size
    scanned = 0
    carry = b""
    idx = 0
    try:
        with open(path, "rb") as fh:
            while pos > 0 and scanned < max_scan_bytes:
                win = windows[idx] if idx < len(windows) else windows[-1]
                idx += 1
                win = min(win, pos, max_scan_bytes - scanned)
                if win <= 0:
                    break
                start = pos - win
                fh.seek(start)
                buf = fh.read(win) + carry
                pos = start
                scanned += win
                if start > 0:
                    nl = buf.find(b"\n")
                    if nl >= 0:
                        carry = buf[:nl + 1]
                        complete = buf[nl + 1:]
                    else:
                        carry = buf
                        continue
                else:
                    carry = b""
                    complete = buf
                for line in reversed(complete.splitlines()):
                    if not line.strip() or needle not in line:
                        continue
                    try:
                        obj = json.loads(line.decode("utf-8", errors="replace"))
                    except ValueError:
                        continue
                    if isinstance(obj, dict) and obj.get("step_index") == step_idx:
                        return obj
    except OSError:
        return None
    return None


def find_step_text(transcript_path, step_idx):
    """Extract step content for step_idx from transcript.jsonl tail, falling back to transcript_full.jsonl only if truncated."""
    if not transcript_path or step_idx is None:
        return ""
    tp = pathlib.Path(transcript_path)
    obj = _scan_tail_step(tp, step_idx)
    if obj is None:
        return ""
    if obj.get("truncated_fields"):
        full = tp.with_name("transcript_full.jsonl")
        if full.is_file() and full.resolve() != tp.resolve():
            full_obj = _scan_tail_step(full, step_idx)
            if full_obj is not None:
                obj = full_obj
    content = obj.get("content")
    return content if isinstance(content, str) else ""


def file_head_sha(path, nbytes=512):
    """Return SHA-256 hex digest of the leading bytes of path to detect rewrite/rotation."""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read(nbytes)).hexdigest()
    except OSError:
        return None


def track_checkpoints_incremental(transcript_path, prev_count=0, prev_offset=0, prev_head_sha=None):
    """Incrementally count CHECKPOINT steps with file-identity rotation/truncation detection."""
    if not transcript_path:
        return prev_count, prev_offset, prev_head_sha, False
    tp = pathlib.Path(transcript_path)
    if not tp.is_file():
        return 0, 0, None, False
    try:
        size = tp.stat().st_size
    except OSError:
        return prev_count, prev_offset, prev_head_sha, False
    cur_head_sha = file_head_sha(tp)
    rotated = bool(prev_offset > 0 and (size < prev_offset or (prev_head_sha and cur_head_sha and cur_head_sha != prev_head_sha)))
    if size == prev_offset and not rotated:
        return prev_count, prev_offset, cur_head_sha, False
    if rotated:
        prev_count, prev_offset = 0, 0
    added = 0
    new_offset = prev_offset
    try:
        with open(tp, "rb") as fh:
            fh.seek(prev_offset)
            raw = fh.read()
        last_nl = raw.rfind(b"\n")
        if last_nl >= 0:
            complete = raw[:last_nl + 1]
            new_offset = prev_offset + len(complete)
            for line in complete.splitlines():
                if b'"CHECKPOINT"' not in line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("type") == "CHECKPOINT":
                    added += 1
    except OSError:
        return prev_count, prev_offset, cur_head_sha, False
    return prev_count + added, new_offset, cur_head_sha, rotated


def count_checkpoints_incremental(transcript_path, prev_count=0, prev_offset=0):
    """Backwards-compatible incremental checkpoint counter returning (count, new_offset)."""
    cur, off, _, _ = track_checkpoints_incremental(transcript_path, prev_count, prev_offset, None)
    return cur, off


def count_checkpoints(transcript_path):
    """Backwards-compatible wrapper returning total CHECKPOINT step count."""
    return count_checkpoints_incremental(transcript_path, 0, 0)[0]


def log_target_from_call(tool_name, args):
    """Identify a repeated log-file path from run_command or read_file/view_file, else None."""
    low = (tool_name or "").lower()
    if low in ("read_file", "view_file"):
        path = args.get("Path") or args.get("AbsolutePath") or ""
        if isinstance(path, str) and path.lower().endswith(LOG_EXT):
            return path
        return None
    if low == "run_command":
        cmd = args.get("CommandLine") or ""
        if not isinstance(cmd, str) or not LOG_READ_CMD.search(cmd):
            return None
        for token in re.findall(r'"([^"]+)"|\'([^\']+)\'|(\S+)', cmd):
            word = next((t for t in token if t), "")
            if word.lower().endswith(LOG_EXT):
                return word
    return None


def cmd_session_start(args):
    """Handle Antigravity SessionStart hook."""
    payload = read_stdin_json()
    steps = []
    if args.resume:
        text = render_resume_text(args.resume, args.max_bytes)
        steps.append({"systemMessage": {"systemMessage": text}})
    if args.banner:
        ws = payload.get("workspacePaths") or []
        ws_label = pathlib.Path(ws[0]).name if ws else "workspace"
        steps.append({
            "systemMessage": {
                "systemMessage": "",
                "metadata": {"user_markdown": f"> **agydw** 已載入（`{ws_label}`）— RESUME_STATE 已注入。"},
            }
        })
    # Record initial checkpoint count + byte offset + head hash for incremental & rotation tracking
    if args.state_dir and payload.get("transcriptPath"):
        folder = pathlib.Path(args.state_dir)
        folder.mkdir(parents=True, exist_ok=True)
        meta_file = folder / f"compaction_{safe_cid(payload.get('conversationId'))}.json"
        cur, off, hsha, _ = track_checkpoints_incremental(payload.get("transcriptPath"), 0, 0, None)
        meta_file.write_text(json.dumps({"checkpoints": cur, "offset": off, "head_sha": hsha}), encoding="utf-8")
    print(json.dumps({"injectSteps": steps} if steps else {}, ensure_ascii=False))
    return 0


def cmd_post_tool(args):
    """Handle Antigravity PostToolUse hook: detect log polling & prompt injection, queue alerts, print {}."""
    payload = read_stdin_json()
    tc = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    tool_name = tc.get("name") if isinstance(tc.get("name"), str) else ""
    raw_args = tc.get("args") if tc.get("args") is not None else tc.get("arguments")
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except ValueError:
            raw_args = {}
    tool_args = raw_args if isinstance(raw_args, dict) else {}
    conv_id = payload.get("conversationId")

    if args.state_dir:
        folder = pathlib.Path(args.state_dir)
        folder.mkdir(parents=True, exist_ok=True)

        # 1. Poll-hint detector
        target = log_target_from_call(tool_name, tool_args)
        if target:
            now = datetime.datetime.now(datetime.timezone.utc).timestamp()
            poll_path = folder / f"polls_{safe_cid(conv_id)}.json"
            try:
                state = json.loads(poll_path.read_text(encoding="utf-8")) if poll_path.is_file() else {}
            except (OSError, ValueError):
                state = {}
            key = hashlib.sha256(target.lower().encode("utf-8")).hexdigest()[:12]
            stamps = [t for t in state.get(key, []) if now - t <= args.poll_window]
            stamps.append(now)
            if len(stamps) >= args.poll_times:
                queue_alert(
                    args.state_dir,
                    conv_id,
                    f"poll_hint: 你已短時間連續讀取 `{pathlib.Path(target).name}` {len(stamps)} 次；"
                    "若是在等背景任務或測試，請結束本輪等系統喚醒，或改看最後結論，不要輪詢 (docs/04)。",
                )
                stamps = []
            state[key] = stamps
            poll_path.write_text(json.dumps(state), encoding="utf-8")

        # 2. Prompt-injection scanner on tool output from transcriptPath
        if args.inject_scan and payload.get("transcriptPath"):
            body = find_step_text(payload.get("transcriptPath"), payload.get("stepIdx"))
            if body:
                scanner = load_sibling("inject_scan")
                findings = scanner.scan_text(body)
                if findings:
                    queue_alert(args.state_dir, conv_id, scanner.warning(tool_name or "a tool", findings))

    print("{}")
    return 0


def cmd_pre_invocation(args):
    """Handle Antigravity PreInvocation hook: drain queued alerts + detect compaction checkpoints."""
    payload = read_stdin_json()
    conv_id = payload.get("conversationId")
    messages = drain_alerts(args.state_dir, conv_id) if args.state_dir else []

    if args.resume and args.state_dir and payload.get("transcriptPath"):
        folder = pathlib.Path(args.state_dir)
        folder.mkdir(parents=True, exist_ok=True)
        meta_file = folder / f"compaction_{safe_cid(conv_id)}.json"
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.is_file() else {}
            prev = int(meta.get("checkpoints", 0))
            prev_off = int(meta.get("offset", 0))
            prev_head = meta.get("head_sha") if isinstance(meta.get("head_sha"), str) else None
        except (OSError, ValueError, TypeError):
            prev, prev_off, prev_head = 0, 0, None
        cur, new_off, cur_head, rotated = track_checkpoints_incremental(payload.get("transcriptPath"), prev, prev_off, prev_head)
        if cur > prev or (rotated and cur > 0):
            meta_file.write_text(json.dumps({"checkpoints": cur, "offset": new_off, "head_sha": cur_head}), encoding="utf-8")
            messages.insert(0, "[compaction detected] " + render_resume_text(args.resume, args.max_bytes))
        elif new_off != prev_off or cur_head != prev_head or not meta_file.is_file():
            meta_file.write_text(json.dumps({"checkpoints": cur, "offset": new_off, "head_sha": cur_head}), encoding="utf-8")

    steps = [{"systemMessage": {"systemMessage": "\n\n".join(messages)}}] if messages else []
    print(json.dumps({"injectSteps": steps} if steps else {}, ensure_ascii=False))
    return 0


STOP_TERMINATION_REASONS = {"NO_TOOL_CALL", "MODEL_STOP", "END_TURN"}


def is_stop_check_eligible(payload):
    """Support documented Antigravity Stop event schemas while avoiding infinite continuation loops."""
    if payload.get("continuationActive") is True:
        return False
    reason = str(payload.get("terminationReason") or "").strip().upper()
    if reason in STOP_TERMINATION_REASONS:
        return True
    if not reason and payload.get("fullyIdle") is True:
        return True
    return False


def _repo_head_info(repo):
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%H%x09%ct", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and "\t" in proc.stdout:
            sha, ts_s = proc.stdout.strip().split("\t", 1)
            return sha.strip(), float(ts_s.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None, None


def _check_resume_freshness(repo, resume_path):
    """Check whether uncommitted workspace files OR committed HEAD are newer than resume_path."""
    if not resume_path.is_file():
        return True, "RESUME_STATE.md does not exist yet", "missing"
    resume_mtime = resume_path.stat().st_mtime
    resume_txt = resume_path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        proc = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, timeout=5)
        dirty = [line[3:].strip().split(" -> ")[-1] for line in proc.stdout.splitlines() if line.strip()] if proc.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        dirty = []
    for rel in dirty:
        fp = pathlib.Path(repo) / rel
        if fp.is_file() and fp.resolve() != resume_path.resolve() and fp.stat().st_mtime > resume_mtime + 2:
            return True, f"workspace file `{rel}` was modified after {resume_path.name}", f"dirty:{rel}:{int(fp.stat().st_mtime)}"

    head_sha, head_ts = _repo_head_info(repo)
    if head_sha:
        m_vh = re.search(r"^[ \t]*(?:VERIFIED_HEAD|HEAD)[ \t]*:[ \t]*([0-9a-f]{7,40})\b", resume_txt, re.M)
        if m_vh:
            recorded = m_vh.group(1).lower()
            if not head_sha.lower().startswith(recorded):
                return True, f"repo HEAD ({head_sha[:10]}) differs from {resume_path.name} VERIFIED_HEAD ({recorded})", f"head:{head_sha}"
        elif head_ts is not None and head_ts > resume_mtime + 2:
            return True, f"repo HEAD ({head_sha[:10]}) was committed after {resume_path.name}", f"head:{head_sha}"
    return False, None, None


def repo_has_newer_changes(repo, resume_path):
    """True when tracked files in repo have uncommitted edits or repo HEAD is newer than resume_path."""
    stale, why, _ = _check_resume_freshness(repo, pathlib.Path(resume_path))
    return stale, why


def _stop_already_blocked(state_dir, conv_id, fp):
    if not state_dir or not fp:
        return False
    folder = pathlib.Path(state_dir)
    folder.mkdir(parents=True, exist_ok=True)
    rec = folder / f"stop_guard_{safe_cid(conv_id)}.json"
    try:
        old = json.loads(rec.read_text(encoding="utf-8")) if rec.is_file() else {}
    except (OSError, ValueError):
        old = {}
    if old.get("fingerprint") == fp:
        return True
    rec.write_text(json.dumps({"fingerprint": fp}), encoding="utf-8")
    return False


def cmd_stop_check(args):
    """Handle Antigravity Stop hook: prevent stopping without updating RESUME_STATE (anti-loop safe)."""
    payload = read_stdin_json()
    if not is_stop_check_eligible(payload):
        print(json.dumps({"decision": "allow"}))
        return 0
    if not args.enforce_resume or not args.resume:
        print(json.dumps({"decision": "allow"}))
        return 0

    resume_path = pathlib.Path(args.resume)
    repo = args.repo or payload.get("cwd") or ((payload.get("workspacePaths") or [None])[0]) or "."
    conv_id = payload.get("conversationId")
    # Validate RESUME_STATE header if file exists
    if resume_path.is_file():
        text = resume_path.read_text(encoding="utf-8-sig", errors="replace")
        head_ok = all(re.search(rf"^{k}\s*:", text, re.M) for k in ("PROGRESS", "DOING", "NEXT_STEP", "COMMS"))
        if not head_ok:
            if not _stop_already_blocked(args.state_dir, conv_id, "missing_header"):
                print(json.dumps({
                    "decision": "continue",
                    "reason": f"agydw Stop guard: `{resume_path.as_posix()}` is missing the 4-line header (PROGRESS / DOING / NEXT_STEP / COMMS). Update it before ending.",
                }, ensure_ascii=False))
                return 0
            print(json.dumps({"decision": "allow"}))
            return 0
    stale, why, fp = _check_resume_freshness(repo, resume_path)
    if stale and not _stop_already_blocked(args.state_dir, conv_id, fp):
        print(json.dumps({
            "decision": "continue",
            "reason": f"agydw Stop guard: {why}. Update `{resume_path.as_posix()}` (PROGRESS / DOING / NEXT_STEP / COMMS) before finishing.",
        }, ensure_ascii=False))
        return 0
    print(json.dumps({"decision": "allow"}))
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("session-start", help="SessionStart hook: inject RESUME_STATE header + open items")
    s1.add_argument("--resume", help="path to RESUME_STATE.md")
    s1.add_argument("--max-bytes", type=int, default=2048, help="max injected UTF-8 bytes (default 2048)")
    s1.add_argument("--state-dir", help="bridge state directory for compaction tracking")
    s1.add_argument("--banner", action="store_true", help="include user-only markdown badge (0 model tokens)")

    s2 = sub.add_parser("post-tool", help="PostToolUse hook: queue poll_hint & inject_scan alerts, print {}")
    s2.add_argument("--state-dir", required=True, help="bridge state directory")
    s2.add_argument("--poll-window", type=int, default=600, help="poll window seconds (default 600)")
    s2.add_argument("--poll-times", type=int, default=3, help="repeated reads before warning (default 3)")
    s2.add_argument("--inject-scan", action="store_true", help="scan matching step output in transcriptPath")

    s3 = sub.add_parser("pre-invocation", help="PreInvocation hook: drain queued alerts & check compaction")
    s3.add_argument("--state-dir", required=True, help="bridge state directory")
    s3.add_argument("--resume", help="RESUME_STATE.md to re-inject when a new CHECKPOINT is detected")
    s3.add_argument("--max-bytes", type=int, default=2048, help="max injected UTF-8 bytes (default 2048)")

    s4 = sub.add_parser("stop-check", help="Stop hook: ensure RESUME_STATE updated (never loops)")
    s4.add_argument("--resume", help="path to RESUME_STATE.md")
    s4.add_argument("--repo", help="git checkout to compare mtime and HEAD against")
    s4.add_argument("--state-dir", help="optional bridge state directory for 1-shot anti-loop tracking")
    s4.add_argument("--enforce-resume", action="store_true", help="continue once if code/HEAD changed after RESUME_STATE")

    try:
        a = ap.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    if a.cmd == "session-start":
        return cmd_session_start(a)
    if a.cmd == "post-tool":
        return cmd_post_tool(a)
    if a.cmd == "pre-invocation":
        return cmd_pre_invocation(a)
    if a.cmd == "stop-check":
        return cmd_stop_check(a)
    return 2


if __name__ == "__main__":
    sys.exit(main())
