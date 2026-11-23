#!/usr/bin/env python3
"""Antigravity hash-chained action audit & loop/injection scanner (2.6.0-agy.4).

Reuses upstream tools/audit_log.py (hash chain, locking, verification), tools/loop_guard.py
(loop detection), tools/inject_scan.py (prompt injection scanning), and tools/secret_mask.py
without modifying them.

Subcommands:
  hook-append --log FILE              PostToolUse hook: reads Antigravity JSON on stdin, maps
                                      run_command -> PowerShell/Bash + input.command, appends to
                                      hash-chained FILE, and outputs '{}' to stdout.
  build --out FILE [--force] ...      Rebuilds a verifiable audit log (kind=rebuild at seq 1) from
                                      Antigravity transcript.jsonl / transcript_full.jsonl files.
                                      Supports --include-subagents (discovers child UUIDs from
                                      invoke_subagent outputs).
  verify --log FILE [...]             Delegates directly to upstream audit_log.py verify.
  loops [...]                         Detects repeat/retry/ping_pong/poll/bad_args/child_error
                                      plus --reads (most re-read paths) over audit logs or
                                      Antigravity transcripts, supporting --since/--until/--json/--out.
  inject [...]                        Scans tool outputs in Antigravity transcripts with inject_scan.py.
"""
import argparse
import collections
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REBUILD_VERSION = "2.6.0-agy.4"
READ_TOOLS = {"read_file", "view_file", "list_dir"}
SUBAGENT_TOOLS = {"invoke_subagent", "swarm", "Agent", "Task"}
TERMINAL_OK_STATUSES = {"DONE", "OK", "SUCCEEDED", "SUCCESS", "COMPLETED"}
NONTERMINAL_STATUSES = {"RUNNING", "PENDING", "IN_PROGRESS", "QUEUED", "STARTED", "WAITING"}
ERROR_STATUSES = {"ERROR", "FAILED", "FAIL", "TIMED_OUT", "TIMEOUT"}
INTERRUPTED_STATUSES = {"CANCELED", "CANCELLED", "INTERRUPTED", "ABORTED"}
CREATED_RE = re.compile(r"^Created At:\s*(\S+)", re.M)
COMPLETED_RE = re.compile(r"^Completed At:\s*(\S+)", re.M)
EXIT_NONZERO_RE = re.compile(r"Command exited with code (-?[1-9]\d*)\.")
EXIT_ZERO_RE = re.compile(r"Command exited with code 0\.")
STILL_RUNNING_RE = re.compile(r"\b(?:process still running|command is still running|running in background)\b", re.I)
SUBAGENT_CID_RE = re.compile(r'"conversationId"\s*:\s*"([0-9a-f-]{8,})"')


def load_sibling(name):
    """Load a sibling module cleanly without altering sys.path."""
    spec = importlib.util.spec_from_file_location(f"_agy_aud_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def default_brain_roots():
    """Return existing default Antigravity brain directories (App, CLI, IDE)."""
    gemini = pathlib.Path.home() / ".gemini"
    candidates = [
        gemini / "antigravity" / "brain",
        gemini / "antigravity-cli" / "brain",
        gemini / "antigravity-ide" / "brain",
    ]
    return [p for p in candidates if p.is_dir()]


def unwrap_arg_value(val):
    """Unwrap Antigravity PLANNER_RESPONSE JSON-quoted string args (e.g. '\"git status\"' -> 'git status')."""
    if isinstance(val, str):
        s = val.strip()
        if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or s[0] in "[{"):
            try:
                return json.loads(s)
            except ValueError:
                return val
    return val


def normalize_tool_and_input(raw_name, raw_args):
    """Normalize Antigravity tool names/args to report_vs_audit-compatible schema."""
    args = {k: unwrap_arg_value(v) for k, v in (raw_args or {}).items()} if isinstance(raw_args, dict) else {}
    low = (raw_name or "").lower()
    if low == "run_command":
        shell_tool = "PowerShell" if os.name == "nt" else "Bash"
        cmd = args.get("CommandLine") if isinstance(args.get("CommandLine"), str) else args.get("command", "")
        norm_input = {"command": cmd}
        for k in ("Cwd", "WaitMsBeforeAsync"):
            if k in args:
                norm_input[k] = args[k]
        return shell_tool, raw_name, norm_input
    if low in ("read_file", "view_file"):
        path = args.get("Path") or args.get("AbsolutePath") or args.get("file_path")
        norm = dict(args)
        if path and "file_path" not in norm:
            norm["file_path"] = path
        return raw_name, raw_name, norm
    if low in ("write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content"):
        path = args.get("Path") or args.get("TargetFile") or args.get("file_path")
        norm = dict(args)
        if path and "file_path" not in norm:
            norm["file_path"] = path
        return raw_name, raw_name, norm
    return raw_name or "?", raw_name or "?", args


def parse_iso(text):
    """Parse ISO-8601 timestamp safely to UTC datetime, or None."""
    if not text or not isinstance(text, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except ValueError:
        return None


def classify_step_outcome(status_val, content, err=None):
    """Classify terminal outcomes explicitly; keep RUNNING/non-terminal distinct from ok."""
    if err:
        return "failure", "error"
    st = str(status_val).strip().upper() if status_val is not None else ""
    body = content if isinstance(content, str) else ""
    if st in INTERRUPTED_STATUSES:
        return "failure", "interrupted"
    if st in ERROR_STATUSES or (body and EXIT_NONZERO_RE.search(body)):
        return "failure", "error"
    if st in NONTERMINAL_STATUSES or (body and STILL_RUNNING_RE.search(body) and not EXIT_ZERO_RE.search(body)):
        return "result", "running"
    if st in TERMINAL_OK_STATUSES:
        return "result", "ok"
    if st == "":
        return ("result", "ok") if (body and EXIT_ZERO_RE.search(body)) else ("result", "unverified")
    return "result", "unverified"


def parse_step_timing_and_error(content, status):
    """Extract duration_ms and error status from an Antigravity GENERIC step body."""
    duration_ms = None
    m1, m2 = CREATED_RE.search(content), COMPLETED_RE.search(content)
    if m1 and m2:
        t1, t2 = parse_iso(m1.group(1)), parse_iso(m2.group(1))
        if t1 and t2:
            duration_ms = max(0, int((t2 - t1).total_seconds() * 1000))
    _, res_status = classify_step_outcome(status, content)
    is_err = (res_status == "error")
    is_int = (res_status == "interrupted")
    return duration_ms, is_err, is_int


def _extract_call_id(obj, keys):
    """Return first non-empty correlation ID string among keys in obj dict."""
    if not isinstance(obj, dict):
        return None
    for k in keys:
        val = obj.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def parse_raw_args(tc):
    """Extract tool call args dict supporting both Antigravity `args` and `arguments` (dict or JSON string)."""
    if not isinstance(tc, dict):
        return {}
    raw = tc.get("args")
    if raw is None:
        raw = tc.get("arguments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _scan_tail_for_step(path, step_idx, require_generic=False, max_scan_bytes=2 * 1024 * 1024):
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
                        if require_generic and obj.get("type") != "GENERIC":
                            continue
                        return obj
    except OSError:
        return None
    return None


def find_step_in_transcript(transcript_path, step_idx):
    """Locate step_idx quickly from transcript.jsonl (tail-first), falling back to transcript_full.jsonl only if truncated."""
    if not transcript_path or step_idx is None:
        return False, None, ""
    tp = pathlib.Path(transcript_path)
    obj = _scan_tail_for_step(tp, step_idx, require_generic=True) or _scan_tail_for_step(tp, step_idx, require_generic=False)
    if obj is None:
        return False, None, ""
    if obj.get("truncated_fields"):
        full = tp.with_name("transcript_full.jsonl")
        if full.is_file() and full.resolve() != tp.resolve():
            full_obj = _scan_tail_for_step(full, step_idx, require_generic=(obj.get("type") == "GENERIC")) or _scan_tail_for_step(full, step_idx, require_generic=False)
            if full_obj is not None:
                obj = full_obj
    content = obj.get("content")
    return True, obj.get("status"), (content if isinstance(content, str) else "")


def load_transcript_steps_with_sources(transcript_path):
    """Load steps from single snapshots of transcript.jsonl and (if read) companion transcript_full.jsonl."""
    tp = pathlib.Path(transcript_path)
    if not tp.is_file():
        raise FileNotFoundError(f"transcript not found: {tp}")

    raw_main = tp.read_bytes()
    sources = [{
        "path": tp.resolve(),
        "name": tp.name,
        "role": "primary",
        "sha256": hashlib.sha256(raw_main).hexdigest(),
        "bytes": len(raw_main),
    }]

    steps = []
    truncated_idxs = set()
    for line in raw_main.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            if obj.get("truncated_fields") and "step_index" in obj:
                truncated_idxs.add(obj["step_index"])
            steps.append(obj)

    if truncated_idxs:
        full_path = tp.with_name("transcript_full.jsonl")
        if full_path.is_file() and full_path.resolve() != tp.resolve():
            raw_full = full_path.read_bytes()
            sources.append({
                "path": full_path.resolve(),
                "name": full_path.name,
                "role": "full_companion",
                "sha256": hashlib.sha256(raw_full).hexdigest(),
                "bytes": len(raw_full),
            })
            full_by_idx = {}
            for line in raw_full.decode("utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("step_index") in truncated_idxs:
                        full_by_idx[obj["step_index"]] = obj
                except ValueError:
                    continue
            if full_by_idx:
                steps = [
                    full_by_idx[s["step_index"]] if (s.get("truncated_fields") and s.get("step_index") in full_by_idx) else s
                    for s in steps
                ]
    return steps, tp, sources


def load_transcript_steps(transcript_path):
    """Load steps from transcript.jsonl, resolving truncated steps from transcript_full.jsonl only when needed."""
    steps, tp, _ = load_transcript_steps_with_sources(transcript_path)
    return steps, tp


def discover_transcripts(transcript_files, conversations, brain_roots, include_subagents):
    """Resolve list of (conv_id, transcript_path, parent_conv_id) in deterministic order."""
    roots = [pathlib.Path(r) for r in (brain_roots or [])] or default_brain_roots()
    seen = set()
    queue = []

    for tf in transcript_files or []:
        p = pathlib.Path(tf).resolve()
        cid = p.parent.parent.parent.name if p.parent.name == "logs" and p.parent.parent.name == ".system_generated" else p.stem
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            queue.append((cid, p, None))

    for cid in conversations or []:
        found = None
        for root in roots:
            cand = root / cid / ".system_generated" / "logs" / "transcript.jsonl"
            if cand.is_file():
                found = cand.resolve()
                break
            # Allow prefix match
            for sub in sorted(root.glob(f"{cid}*/.system_generated/logs/transcript.jsonl")):
                found = sub.resolve()
                cid = found.parent.parent.parent.name
                break
            if found:
                break
        if found and str(found).lower() not in seen:
            seen.add(str(found).lower())
            queue.append((cid, found, None))

    if not transcript_files and not conversations:
        for root in roots:
            for cand in sorted(root.glob("*/.system_generated/logs/transcript.jsonl")):
                cid = cand.parent.parent.parent.name
                p = cand.resolve()
                if str(p).lower() not in seen:
                    seen.add(str(p).lower())
                    queue.append((cid, p, None))

    out = []
    idx = 0
    while idx < len(queue):
        cid, path, parent = queue[idx]
        idx += 1
        out.append((cid, path, parent))
        if include_subagents and path.is_file():
            calls, _, _ = extract_calls_from_transcript(cid, path, parent_cid=parent)
            for c in calls:
                if c["tool_raw"] not in SUBAGENT_TOOLS:
                    continue
                for sub_cid in SUBAGENT_CID_RE.findall(c.get("content") or ""):
                    for root in [path.parent.parent.parent.parent] + roots:
                        sub_tp = root / sub_cid / ".system_generated" / "logs" / "transcript.jsonl"
                        if sub_tp.is_file():
                            sp = sub_tp.resolve()
                            if str(sp).lower() not in seen:
                                seen.add(str(sp).lower())
                                queue.append((sub_cid, sp, cid))
                            break
    return out


def _build_call_record(conv_id, parent_cid, model_seen, tc, tc_id, p_step, p_ts, st, orphan=False):
    """Build normalized audit call entry without defaulting missing status to DONE."""
    if tc is not None:
        raw_name = tc.get("name") or "?"
        raw_args = parse_raw_args(tc)
        tool, tool_raw, norm_input = normalize_tool_and_input(raw_name, raw_args)
    else:
        tool = "?"
        tool_raw = "unmatched_result"
        norm_input = {"unmatched_tool_call_id": tc_id} if tc_id else {"unmatched_step_index": st.get("step_index")}
    content = st.get("content") or ""
    status_val = st.get("status")
    dur, is_err, is_int = parse_step_timing_and_error(content, status_val)
    kind, res_status = classify_step_outcome(status_val, content)
    ts_str = st.get("created_at") or p_ts
    return {
        "conv_id": conv_id,
        "agent_id": conv_id if parent_cid else None,
        "parent_id": parent_cid,
        "step_idx": st.get("step_index"),
        "planner_step": p_step,
        "tc_id": tc_id,
        "orphan": orphan,
        "tool": tool,
        "tool_raw": tool_raw,
        "input_obj": norm_input,
        "content": content,
        "duration_ms": dur,
        "is_err": is_err,
        "is_int": is_int,
        "kind": kind,
        "result_status": res_status,
        "ts_str": ts_str,
        "ts_dt": parse_iso(ts_str),
        "model": model_seen,
    }


def extract_calls_detailed(conv_id, transcript_path, parent_cid=None):
    """Two-pass per-turn pairing: correlation ID first across the batch, then remaining anonymous slots."""
    steps, tp, sources = load_transcript_steps_with_sources(transcript_path)
    calls = []
    no_result = 0
    unmatched_results = 0
    model_seen = None

    batch_pending = []
    batch_generics = []
    batch_model = None

    def flush_batch():
        nonlocal no_result, unmatched_results
        if not batch_pending and not batch_generics:
            return
        used_p = [False] * len(batch_pending)
        used_g = [False] * len(batch_generics)
        emitted = []

        # Pass 1: correlation ID matches first across the whole turn batch
        for gi, (st, step_cid) in enumerate(batch_generics):
            if not step_cid:
                continue
            match_pi = next((pi for pi, item in enumerate(batch_pending) if not used_p[pi] and item[1] == step_cid), None)
            used_g[gi] = True
            if match_pi is not None:
                used_p[match_pi] = True
                tc, tc_id, p_step, p_ts = batch_pending[match_pi]
                emitted.append(_build_call_record(conv_id, parent_cid, batch_model, tc, tc_id or step_cid, p_step, p_ts, st))
            else:
                unmatched_results += 1
                rec = _build_call_record(conv_id, parent_cid, batch_model, None, step_cid, None, st.get("created_at"), st, orphan=True)
                if rec["result_status"] in ("error", "interrupted"):
                    emitted.append(rec)

        # Pass 2: pair remaining anonymous results strictly with remaining unused pending calls
        rem_p = [pi for pi, u in enumerate(used_p) if not u]
        rem_g = [gi for gi, u in enumerate(used_g) if not u]
        for pi, gi in zip(rem_p, rem_g):
            used_p[pi] = True
            used_g[gi] = True
            tc, tc_id, p_step, p_ts = batch_pending[pi]
            st, _ = batch_generics[gi]
            emitted.append(_build_call_record(conv_id, parent_cid, batch_model, tc, tc_id, p_step, p_ts, st))

        for gi, u in enumerate(used_g):
            if not u:
                unmatched_results += 1
                st, _ = batch_generics[gi]
                rec = _build_call_record(conv_id, parent_cid, batch_model, None, None, None, st.get("created_at"), st, orphan=True)
                if rec["result_status"] in ("error", "interrupted"):
                    emitted.append(rec)

        no_result += sum(1 for u in used_p if not u)
        emitted.sort(key=lambda c: c["step_idx"] if c["step_idx"] is not None else 0)
        calls.extend(emitted)
        batch_pending.clear()
        batch_generics.clear()

    for st in steps:
        stype = st.get("type")
        if stype == "PLANNER_RESPONSE":
            flush_batch()
            if st.get("model"):
                model_seen = st.get("model")
            batch_model = model_seen
            for tc in st.get("tool_calls") or []:
                if isinstance(tc, dict):
                    tc_id = _extract_call_id(tc, ("id", "call_id", "tool_call_id", "toolCallId", "tool_use_id"))
                    batch_pending.append((tc, tc_id, st.get("step_index"), st.get("created_at")))
        elif stype == "GENERIC" and batch_pending:
            step_cid = _extract_call_id(st, ("tool_call_id", "toolCallId", "call_id", "callId", "tool_use_id"))
            batch_generics.append((st, step_cid))

    flush_batch()
    return calls, no_result, unmatched_results, tp, sources


def extract_calls_from_transcript(conv_id, transcript_path, parent_cid=None):
    """Pair PLANNER_RESPONSE.tool_calls with subsequent GENERIC steps (two-pass ID-first)."""
    calls, no_result, _, tp, _ = extract_calls_detailed(conv_id, transcript_path, parent_cid=parent_cid)
    return calls, no_result, tp


def cmd_hook_append(args):
    """Handle Antigravity PostToolUse hook: append hash-chained entry and output '{}'."""
    audit = load_sibling("audit_log")
    raw = sys.stdin.read() if sys.stdin is not None else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        print("{}")
        return 0
    if not isinstance(payload, dict):
        print("{}")
        return 0

    tc = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    raw_name = tc.get("name") if isinstance(tc.get("name"), str) else "?"
    raw_args = parse_raw_args(tc)
    tool, tool_raw, norm_input = normalize_tool_and_input(raw_name, raw_args)

    step_idx = payload.get("stepIdx")
    conv_id = payload.get("conversationId") if isinstance(payload.get("conversationId"), str) else None
    err = payload.get("error")
    tp = payload.get("transcriptPath")
    inline_res = None
    for rk in ("toolResult", "toolResponse", "output", "response"):
        if rk in payload and payload[rk] is not None:
            inline_res = payload[rk] if isinstance(payload[rk], str) else json.dumps(payload[rk], ensure_ascii=False)
            break

    found_step, step_status, body = find_step_in_transcript(tp, step_idx) if (tp and step_idx is not None) else (False, None, "")
    if not found_step and inline_res is not None:
        found_step, body = True, inline_res

    if err:
        kind = "failure"
        res = audit.output_record("error", err)
    elif found_step:
        kind, res_status = classify_step_outcome(step_status, body)
        res = audit.output_record(res_status, body)
    else:
        # Honest reporting: never claim status=ok when neither error nor matching tool output was observed
        kind = "result"
        res = audit.output_record("unverified", "")

    fields = {
        "session_id": conv_id,
        "agent_id": None,
        "tool": tool,
        "tool_raw": tool_raw,
        "tool_use_id": tc.get("id") or (f"step-{step_idx}" if step_idx is not None else None),
        "input": audit.summary(norm_input),
        "kind": kind,
        "result": res,
        "duration_ms": None,
    }
    try:
        target = audit.expand(args.log, folder=(payload.get("cwd") or (payload.get("workspacePaths") or [None])[0]))
        audit.append_entry(target, fields)
    except (audit.AuditError, ValueError, OSError) as exc:
        print(f"agy_audit hook-append error: {exc}", file=sys.stderr)
        print("{}")
        return 1
    print("{}")
    return 0


def cmd_build(args):
    """Rebuild an audit_log.py-compatible hash-chained JSONL log from Antigravity transcripts."""
    audit = load_sibling("audit_log")
    out_path = pathlib.Path(args.out)
    if out_path.exists() and not args.force:
        print(f"AGY_AUDIT: REFUSED {out_path} already exists (pass --force)", file=sys.stderr)
        return 1

    sources = discover_transcripts(args.transcript, args.conversation, args.brain_root, args.include_subagents)
    if not sources:
        print("AGY_AUDIT: CANNOT_RUN no Antigravity transcripts matched", file=sys.stderr)
        return 2

    all_calls = []
    source_meta = []
    total_no_result = 0
    total_unmatched = 0
    for cid, tp, parent_cid in sources:
        try:
            calls, no_res, unm_res, _, src_recs = extract_calls_detailed(cid, tp, parent_cid=parent_cid)
        except OSError as exc:
            print(f"AGY_AUDIT: CANNOT_RUN {tp}: {exc}", file=sys.stderr)
            return 2
        total_no_result += no_res
        total_unmatched += unm_res
        for srec in src_recs:
            source_meta.append({
                "conversation_id": cid,
                "name": srec["name"],
                "role": srec["role"],
                "sha256": srec["sha256"],
                "bytes": srec["bytes"],
            })
        all_calls.extend(calls)

    if not all_calls:
        print("AGY_AUDIT: CANNOT_RUN no tool calls found in transcripts", file=sys.stderr)
        return 2

    all_calls.sort(key=lambda c: (c["ts_dt"] or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), c["step_idx"] or 0))

    rebuild_line = {
        "seq": 1,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
        "session_id": sources[0][0],
        "agent_id": None,
        "tool": None,
        "tool_use_id": None,
        "input": "",
        "kind": "rebuild",
        "result": {"status": "rebuilt"},
        "duration_ms": None,
        "source": {
            "by": "agy_audit.py",
            "version": REBUILD_VERSION,
            "transcripts": source_meta,
            "lines": len(all_calls),
            "no_result": total_no_result,
            "unmatched_results": total_unmatched,
        },
        "prev": audit.GENESIS,
    }
    rebuild_line["hash"] = audit.digest(rebuild_line)

    entries = [rebuild_line]
    prev_hash = rebuild_line["hash"]
    for seq, c in enumerate(all_calls, 2):
        input_str = audit.summary(c["input_obj"], limit=args.input_limit)
        input_cut = args.input_limit is not None and len(audit.summary(c["input_obj"], limit=None)) > args.input_limit
        kind = c["kind"]
        res = audit.output_record(c["result_status"], c["content"])
        ts_out = (c["ts_dt"] or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec="milliseconds")
        entry = {
            "seq": seq,
            "ts": ts_out,
            "session_id": c["conv_id"],
            "agent_id": c["agent_id"],
            "tool": c["tool"],
            "tool_raw": c["tool_raw"],
            "tool_use_id": c.get("tc_id") or f"step-{c['step_idx']}",
            "input": input_str,
            "kind": kind,
            "result": res,
            "duration_ms": c["duration_ms"],
            "prev": prev_hash,
        }
        if input_cut:
            entry["input_cut"] = True
        entry["hash"] = audit.digest(entry)
        prev_hash = entry["hash"]
        entries.append(entry)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(f"{out_path.name}.tmp-{os.getpid()}")
    tmp_path.write_text("".join(audit.canonical(e) + "\n" for e in entries), encoding="utf-8", newline="\n")
    os.replace(tmp_path, out_path)

    head_sha = entries[-1]["hash"]
    if args.json:
        print(json.dumps({
            "file": out_path.as_posix(),
            "lines": len(entries),
            "calls": len(all_calls),
            "conversations": len(sources),
            "no_result": total_no_result,
            "unmatched_results": total_unmatched,
            "head_seq": entries[-1]["seq"],
            "head_sha": head_sha,
        }, ensure_ascii=False))
    else:
        print(f"AGY_AUDIT file={out_path.as_posix()} lines={len(entries)} calls={len(all_calls)} conversations={len(sources)} no_result={total_no_result} unmatched={total_unmatched}")
        print(f"AUDIT_HEAD file={out_path.as_posix()} seq={entries[-1]['seq']} sha={head_sha}")
    return 0


def cmd_verify(args):
    """Delegate chain verification to audit_log.py, then summarize result verification status."""
    audit = load_sibling("audit_log")
    argv = ["verify", "--log", args.log]
    if args.from_seq is not None:
        argv += ["--from", str(args.from_seq)]
    if args.to_seq is not None:
        argv += ["--to", str(args.to_seq)]
    if args.anchor_log:
        argv += ["--anchor-log", args.anchor_log]
    if args.anchor_rev:
        argv += ["--anchor-rev", args.anchor_rev]
    if args.anchor_path:
        argv += ["--anchor-path", args.anchor_path]
    if args.repo:
        argv += ["--repo", args.repo]
    if args.cite:
        argv += ["--cite", args.cite]
    old_argv = sys.argv
    sys.argv = ["audit_log.py"] + argv
    try:
        rc = audit.main()
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 0
    finally:
        sys.argv = old_argv
    if rc == 0 and pathlib.Path(args.log).is_file():
        c_ok = c_err = c_unv = c_run = 0
        try:
            for line in pathlib.Path(args.log).read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                obj = json.loads(line)
                if obj.get("kind") in ("result", "failure") and isinstance(obj.get("result"), dict):
                    st = obj["result"].get("status")
                    if st == "ok":
                        c_ok += 1
                    elif st in ("error", "interrupted"):
                        c_err += 1
                    elif st == "running":
                        c_run += 1
                    elif st == "unverified":
                        c_unv += 1
            print(f"AGY_AUDIT_STATUS: chain=OK confirmed_ok={c_ok} confirmed_error={c_err} unverified={c_unv} running={c_run}")
        except (OSError, ValueError):
            pass
    return rc


def parse_day(text, end=False):
    """Parse YYYY-MM-DD (or ISO timestamp) into UTC boundary."""
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        dt = datetime.datetime.fromisoformat(text).replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
        if end:
            dt = dt + datetime.timedelta(days=1)
        return dt.astimezone(datetime.timezone.utc)
    return parse_iso(text)


def read_path_from_call(tool_raw, input_obj):
    """Extract read file path from read_file/view_file/shell read calls for --reads."""
    if (tool_raw or "").lower() in READ_TOOLS:
        p = input_obj.get("Path") or input_obj.get("AbsolutePath") or input_obj.get("file_path") or input_obj.get("DirectoryPath")
        return str(p) if p else None
    return None


def cmd_loops(args):
    """Detect loops and optional most re-read paths over Antigravity transcripts or audit logs."""
    loop = load_sibling("loop_guard")
    audit = load_sibling("audit_log")
    try:
        conf = loop.load_config(args.config)
    except loop.InputError as err:
        print(f"AGY_LOOPS: INPUT_ERROR {err}", file=sys.stderr)
        return 2
    for key, attr in (("window", "window"), ("repeat", "repeat"), ("retry_errors", "retry_errors"),
                      ("poll", "poll"), ("ping_pong", "ping_pong"), ("bad_args", "bad_args")):
        val = getattr(args, attr, None)
        if val is not None:
            conf[key] = val
    conf["child_tools"] = list(dict.fromkeys(conf.get("child_tools", []) + ["invoke_subagent", "swarm"]))

    since_dt = parse_day(args.since, end=False)
    until_dt = parse_day(args.until, end=True)

    events = []
    read_counts = collections.Counter()
    if args.audit:
        for log_file in args.audit:
            p = pathlib.Path(log_file)
            if not p.is_file():
                print(f"AGY_LOOPS: INPUT_ERROR missing audit {p}", file=sys.stderr)
                return 2
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                ts_dt = parse_iso(entry.get("ts"))
                if since_dt and ts_dt and ts_dt < since_dt:
                    continue
                if until_dt and ts_dt and ts_dt >= until_dt:
                    continue
                ev = loop.event_of(entry)
                if ev:
                    events.append(ev)

    if args.transcript or args.conversation or not args.audit:
        sources = discover_transcripts(args.transcript, args.conversation, args.brain_root, args.include_subagents)
        for cid, tp, parent_cid in sources:
            calls, _, _ = extract_calls_from_transcript(cid, tp, parent_cid)
            for c in calls:
                if since_dt and c["ts_dt"] and c["ts_dt"] < since_dt:
                    continue
                if until_dt and c["ts_dt"] and c["ts_dt"] >= until_dt:
                    continue
                if args.reads:
                    rp = read_path_from_call(c["tool_raw"], c["input_obj"])
                    if rp:
                        read_counts[rp] += 1
                summary_text = loop.plain_summary(audit, c["input_obj"])
                status = "interrupted" if c["is_int"] else ("error" if c["is_err"] else "ok")
                err_cls = None
                if status == "error":
                    err_cls = "validation" if loop.VALIDATION.search(c["content"]) else "other"
                events.append({
                    "seq": c["step_idx"],
                    "session": c["conv_id"],
                    "agent": c["agent_id"],
                    "tool": c["tool"],
                    "args": loop.short(summary_text),
                    "input": summary_text,
                    "status": status,
                    "result": loop.raw_hash(c["content"])[:12],
                    "error_class": err_cls,
                    "error_text_known": True,
                })

    if not events:
        payload = {"findings_total": 0, "counts": {}, "not_verified": [], "events": 0, "streams": 0}
        if args.out:
            pathlib.Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print("AGY_LOOPS: NO_DATA no matching tool events", file=sys.stderr)
        return 2

    findings = loop.detect(events, conf)
    cov = loop.coverage(events, conf)
    counts = collections.Counter(f["category"] for f in findings)
    not_verified = []
    if not cov["error_text"]:
        not_verified.append("bad_args")
    if not cov["child_results"] and not any(e["tool"] in conf["child_tools"] for e in events):
        not_verified.append("child_error_unhandled")

    payload = {
        "findings_total": len(findings),
        "counts": dict(counts),
        "not_verified": not_verified,
        "events": len(events),
        "streams": len({(e["session"], e["agent"]) for e in events}),
        "sessions": len({e["session"] for e in events}),
        "findings": findings[: args.show],
        "top_reads": [{"path": k, "count": v} for k, v in read_counts.most_common(10)] if args.reads else [],
    }
    if args.out:
        pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for f in findings[: args.show]:
            print(f"LOOP {f['category']} tool={','.join(f['tools'])} args={','.join(f['args'])} "
                  f"count={f['count']} seq={f['first']}..{f['last']} session={(f['session'] or '')[:8]}")
        if args.reads and read_counts:
            print("TOP_READS:")
            for p, cnt in read_counts.most_common(10):
                print(f"  {cnt:3d}  {p}")
        tag = f"FOUND {len(findings)}" if findings else "NONE"
        print(f"LOOP_GUARD: {tag} events={payload['events']} streams={payload['streams']}")
    return 1 if findings else 0


def cmd_inject(args):
    """Scan tool outputs in Antigravity transcripts for prompt-injection patterns."""
    scanner = load_sibling("inject_scan")
    sources = discover_transcripts(args.transcript, args.conversation, args.brain_root, args.include_subagents)
    if not sources:
        print("AGY_INJECT: NO_DATA no transcripts matched", file=sys.stderr)
        return 2
    findings = []
    for cid, tp, _ in sources:
        calls, _, _ = extract_calls_from_transcript(cid, tp)
        for c in calls:
            if args.tool and c["tool"].lower() != args.tool.lower() and c["tool_raw"].lower() != args.tool.lower():
                continue
            for cat, line in scanner.scan_text(c["content"]):
                findings.append({"category": cat, "tool": c["tool_raw"], "step": c["step_idx"], "line": line, "conversation": cid})

    if args.json:
        print(json.dumps(findings[: args.show], indent=2, ensure_ascii=False))
    else:
        for f in findings[: args.show]:
            print(f"INJECT {f['category']} tool={f['tool']} step={f['step']} line={f['line']} conv={f['conversation'][:8]}")
        print(f"INJECT_SCAN: FOUND {len(findings)}" if findings else "INJECT_SCAN: NONE")
    return 1 if findings else 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s_ha = sub.add_parser("hook-append", help="PostToolUse hook: append Antigravity tool call to hash chain, print {}")
    s_ha.add_argument("--log", required=True, help="audit log path (supports {worktree} and {container})")

    s_b = sub.add_parser("build", help="rebuild audit log from Antigravity transcript.jsonl")
    s_b.add_argument("--out", required=True, help="output JSONL file")
    s_b.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    s_b.add_argument("--transcript", action="append", default=[], help="transcript.jsonl path (repeatable)")
    s_b.add_argument("--conversation", action="append", default=[], help="conversationId UUID or prefix (repeatable)")
    s_b.add_argument("--brain-root", action="append", default=[], help="override Antigravity brain directory")
    s_b.add_argument("--include-subagents", action="store_true", help="follow invoke_subagent conversationIds")
    s_b.add_argument("--input-limit", type=int, help="optional input truncation limit (default: full)")
    s_b.add_argument("--json", action="store_true", help="print summary as JSON")

    s_v = sub.add_parser("verify", help="verify audit hash chain (delegates to audit_log.py)")
    s_v.add_argument("--log", required=True)
    s_v.add_argument("--from", dest="from_seq", type=int)
    s_v.add_argument("--to", dest="to_seq", type=int)
    s_v.add_argument("--anchor-log")
    s_v.add_argument("--anchor-rev")
    s_v.add_argument("--anchor-path")
    s_v.add_argument("--repo")
    s_v.add_argument("--cite")

    s_l = sub.add_parser("loops", help="detect loops and re-read hot paths across logs/transcripts")
    s_l.add_argument("--audit", action="append", default=[], help="audit JSONL log (repeatable)")
    s_l.add_argument("--transcript", action="append", default=[], help="transcript.jsonl (repeatable)")
    s_l.add_argument("--conversation", action="append", default=[], help="conversationId or prefix (repeatable)")
    s_l.add_argument("--brain-root", action="append", default=[])
    s_l.add_argument("--include-subagents", action="store_true")
    s_l.add_argument("--since", help="YYYY-MM-DD or ISO timestamp (inclusive)")
    s_l.add_argument("--until", help="YYYY-MM-DD or ISO timestamp (exclusive for date)")
    s_l.add_argument("--config", help="loop_guard JSON config")
    s_l.add_argument("--window", type=int)
    s_l.add_argument("--repeat", type=int)
    s_l.add_argument("--retry-errors", type=int)
    s_l.add_argument("--poll", type=int)
    s_l.add_argument("--ping-pong", type=int)
    s_l.add_argument("--bad-args", type=int)
    s_l.add_argument("--reads", action="store_true", help="report most frequently re-read file paths")
    s_l.add_argument("--show", type=int, default=20)
    s_l.add_argument("--json", action="store_true")
    s_l.add_argument("--out", help="write JSON summary for status_gen.py")

    s_i = sub.add_parser("inject", help="scan Antigravity transcript tool outputs for prompt injection")
    s_i.add_argument("--transcript", action="append", default=[])
    s_i.add_argument("--conversation", action="append", default=[])
    s_i.add_argument("--brain-root", action="append", default=[])
    s_i.add_argument("--include-subagents", action="store_true")
    s_i.add_argument("--tool", help="filter by tool name")
    s_i.add_argument("--show", type=int, default=40)
    s_i.add_argument("--json", action="store_true")

    try:
        a = ap.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    if a.cmd == "hook-append":
        return cmd_hook_append(a)
    if a.cmd == "build":
        return cmd_build(a)
    if a.cmd == "verify":
        return cmd_verify(a)
    if a.cmd == "loops":
        return cmd_loops(a)
    if a.cmd == "inject":
        return cmd_inject(a)
    return 2


if __name__ == "__main__":
    sys.exit(main())
