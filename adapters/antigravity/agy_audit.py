#!/usr/bin/env python3
"""Antigravity hash-chained action audit & loop/injection scanner (3.0.0-agy.7).

Reuses frozen base `tools/_common.py` (`exit_code_from`, `gate_status`, `find_repo`),
`tools/audit_log.py`, `tools/loop_guard.py`, `tools/inject_scan.py`, and
`tools/secret_mask.py` without modifying any base files.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _load_common():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_agy_aud_common", HERE / "agy_common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_COMMON = _load_common()

REBUILD_VERSION = "3.0.0-agy.7"
READ_TOOLS = {"read_file", "view_file", "list_dir"}
SHELL_TOOL_NAMES = {"run_command", "bash", "powershell", "monitor"}
SUBAGENT_TOOLS = {"invoke_subagent", "swarm", "Agent", "Task"}
TERMINAL_OK_STATUSES = {"DONE", "OK", "SUCCEEDED", "SUCCESS", "COMPLETED"}
NONTERMINAL_STATUSES = {"RUNNING", "PENDING", "IN_PROGRESS", "QUEUED", "STARTED", "WAITING"}
ERROR_STATUSES = {"ERROR", "FAILED", "FAIL", "TIMED_OUT", "TIMEOUT"}
INTERRUPTED_STATUSES = {"CANCELED", "CANCELLED", "INTERRUPTED", "ABORTED"}
CREATED_RE = re.compile(r"^Created At:\s*(\S+)", re.M)
COMPLETED_RE = re.compile(r"^Completed At:\s*(\S+)", re.M)
OUTPUT_SECTION_RE = re.compile(r"(?:^|\r?\n)(?:Command output|Output):[ \t]*(?:\r?\n|$)")
STILL_RUNNING_RE = re.compile(r"\b(?:process still running|command (?:is )?still running|running in background)\b", re.I)
SUBAGENT_CID_RE = re.compile(r'"conversationId"\s*:\s*"([0-9a-f-]{8,})"', re.I)


def load_sibling(name):
    return _COMMON.load_sibling(HERE, name, prefix="_agy_aud_")


def default_brain_roots():
    gemini = pathlib.Path.home() / ".gemini"
    candidates = [
        gemini / "antigravity" / "brain",
        gemini / "antigravity-cli" / "brain",
        gemini / "antigravity-ide" / "brain",
    ]
    return [p for p in candidates if p.is_dir()]


def unwrap_arg_value(val):
    return _COMMON.unwrap_arg_value(val)


def normalize_tool_and_input(raw_name, raw_args):
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
        path = args.get("Path") or args.get("AbsolutePath") or args.get("file_path") or args.get("path")
        norm = dict(args)
        if path and "file_path" not in norm:
            norm["file_path"] = path
        return raw_name, raw_name, norm
    if low in ("write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content"):
        path = args.get("Path") or args.get("TargetFile") or args.get("file_path") or args.get("path")
        norm = dict(args)
        if path and "file_path" not in norm:
            norm["file_path"] = path
        return raw_name, raw_name, norm
    return raw_name or "?", raw_name or "?", args


def parse_iso(text):
    if not text or not isinstance(text, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except ValueError:
        return None


def _extract_shell_header(body):
    if not body:
        return ""
    if body.lstrip().startswith(("Output:", "Command output:")) and re.match(r"^[ \t]*(?:Command output|Output):[ \t]*(?:\r?\n|$)", body):
        return ""
    parts = OUTPUT_SECTION_RE.split(body, maxsplit=1)
    return parts[0]


def _extract_step_duration_ms(content):
    if not isinstance(content, str) or not content:
        return None
    m1, m2 = CREATED_RE.search(content), COMPLETED_RE.search(content)
    if m1 and m2:
        t1, t2 = parse_iso(m1.group(1)), parse_iso(m2.group(1))
        if t1 and t2:
            return max(0, int((t2 - t1).total_seconds() * 1000))
    return None


def classify_step_outcome(status_val, content, err=None, is_shell=True):
    """Classify terminal step outcome using frozen `tools/_common.py` G2 exit-code logic."""
    if err:
        return "failure", "error"
    st = str(status_val).strip().upper() if status_val is not None else ""
    body = content if isinstance(content, str) else ""
    if st in INTERRUPTED_STATUSES:
        return "failure", "interrupted"
    if st in ERROR_STATUSES:
        return "failure", "error"
    if is_shell:
        header = _extract_shell_header(body)
        code = _COMMON.exit_code_from(header) if header else None
        gstat = _COMMON.gate_status(code)
        if gstat == "FAIL":
            return "failure", "error"
        if st in NONTERMINAL_STATUSES or (header and STILL_RUNNING_RE.search(header) and gstat != "PASS"):
            return "result", "running"
        if st in TERMINAL_OK_STATUSES:
            return ("result", "unverified") if (header and code is None and "exited with code" in header.lower()) else ("result", "ok")
        if st == "":
            return ("result", "ok") if gstat == "PASS" else ("result", "unverified")
    elif st in NONTERMINAL_STATUSES:
        return "result", "running"
    if st in TERMINAL_OK_STATUSES:
        return "result", "ok"
    return "result", "unverified"


def parse_step_timing_and_error(content, status, is_shell=True):
    duration_ms = _extract_step_duration_ms(content)
    _, res_status = classify_step_outcome(status, content, is_shell=is_shell)
    return duration_ms, (res_status == "error"), (res_status == "interrupted")


def _extract_call_id(obj, keys):
    if not isinstance(obj, dict):
        return None
    for k in keys:
        val = obj.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def parse_raw_args(tc):
    return _COMMON.parse_raw_args(tc)


def _scan_tail_for_step(path, step_idx, require_generic=False, max_scan_bytes=2 * 1024 * 1024):
    return _COMMON.scan_tail_for_step(path, step_idx, require_generic=require_generic, max_scan_bytes=max_scan_bytes)


def find_step_info_in_transcript(transcript_path, step_idx):
    if not transcript_path or step_idx is None:
        return False, None, "", None
    tp = pathlib.Path(transcript_path)
    obj = _scan_tail_for_step(tp, step_idx, require_generic=True)
    if obj is None:
        return False, None, "", None
    if obj.get("truncated_fields"):
        full = tp.with_name("transcript_full.jsonl")
        if full.is_file() and full.resolve() != tp.resolve():
            full_obj = _scan_tail_for_step(full, step_idx, require_generic=True)
            if full_obj is not None:
                obj = full_obj
    raw_content = obj.get("content")
    body = raw_content if isinstance(raw_content, str) else ""
    return True, obj.get("status"), body, _extract_step_duration_ms(body)


def find_step_in_transcript(transcript_path, step_idx):
    found, status_val, body, _ = find_step_info_in_transcript(transcript_path, step_idx)
    return found, status_val, body


def load_transcript_steps_with_sources(transcript_path):
    tp = pathlib.Path(transcript_path)
    if not tp.is_file():
        raise FileNotFoundError(f"transcript not found: {tp}")
    raw_main = tp.read_bytes()
    sources = [{
        "path": tp.resolve(), "name": tp.name, "role": "primary",
        "sha256": hashlib.sha256(raw_main).hexdigest(), "bytes": len(raw_main),
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
                "path": full_path.resolve(), "name": full_path.name, "role": "full_companion",
                "sha256": hashlib.sha256(raw_full).hexdigest(), "bytes": len(raw_full),
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
    steps, tp, _ = load_transcript_steps_with_sources(transcript_path)
    return steps, tp


def _discover_child_cids_streaming(path):
    cids = []
    seen_cids = set()
    has_sub_turn = False
    trunc_generic_idxs = set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip() or ("PLANNER_RESPONSE" not in line and "GENERIC" not in line):
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                stype = obj.get("type")
                if stype == "PLANNER_RESPONSE":
                    has_sub_turn = any(isinstance(tc, dict) and tc.get("name") in SUBAGENT_TOOLS for tc in (obj.get("tool_calls") or []))
                elif stype == "GENERIC" and has_sub_turn:
                    if obj.get("truncated_fields") and obj.get("step_index") is not None:
                        trunc_generic_idxs.add(obj["step_index"])
                    body = obj.get("content") or ""
                    if isinstance(body, str) and "conversationId" in body:
                        for scid in SUBAGENT_CID_RE.findall(body):
                            k = scid.lower()
                            if k not in seen_cids:
                                seen_cids.add(k)
                                cids.append(scid)
    except OSError:
        return cids

    if trunc_generic_idxs:
        full_p = pathlib.Path(path).with_name("transcript_full.jsonl")
        if full_p.is_file() and _COMMON.norm_path_key(full_p) != _COMMON.norm_path_key(path):
            try:
                with open(full_p, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if "GENERIC" not in line or "conversationId" not in line:
                            continue
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(obj, dict) and obj.get("step_index") in trunc_generic_idxs:
                            body = obj.get("content") or ""
                            if isinstance(body, str):
                                for scid in SUBAGENT_CID_RE.findall(body):
                                    k = scid.lower()
                                    if k not in seen_cids:
                                        seen_cids.add(k)
                                        cids.append(scid)
            except OSError:
                pass
    return cids


def discover_transcripts(transcript_files, conversations, brain_roots, include_subagents):
    """Resolve list of `(conv_id, transcript_path, parent_conv_id)` in parent-before-child order."""
    roots = [pathlib.Path(r) for r in (brain_roots or [])] or default_brain_roots()
    nodes = {}
    seed_order = []
    parent_of = {}
    children_of = collections.defaultdict(list)
    queue = []

    def add_seed(cid, p_res):
        k = _COMMON.norm_path_key(p_res)
        if k not in nodes:
            nodes[k] = (cid, p_res)
            parent_of[k] = None
            seed_order.append(k)
            queue.append((k, cid, p_res))
        return k

    for tf in transcript_files or []:
        p = pathlib.Path(tf).resolve()
        cid = p.parent.parent.parent.name if p.parent.name == "logs" and p.parent.parent.name == ".system_generated" else p.stem
        add_seed(cid, p)

    for cid in conversations or []:
        found = None
        for root in roots:
            cand = root / cid / ".system_generated" / "logs" / "transcript.jsonl"
            if cand.is_file():
                found = cand.resolve()
                break
            for sub in sorted(root.glob(f"{cid}*/.system_generated/logs/transcript.jsonl")):
                found = sub.resolve()
                cid = found.parent.parent.parent.name
                break
            if found:
                break
        if found:
            add_seed(cid, found)

    if not transcript_files and not conversations:
        for root in roots:
            for cand in sorted(root.glob("*/.system_generated/logs/transcript.jsonl")):
                cid = cand.parent.parent.parent.name
                add_seed(cid, cand.resolve())

    def would_create_cycle(parent_k, child_k):
        cur = parent_k
        while cur is not None:
            if cur == child_k:
                return True
            par_cid = parent_of.get(cur)
            if par_cid is None:
                return False
            cur = next((nk for nk, (ncid, _) in nodes.items() if ncid == par_cid), None)
        return False

    q_idx = 0
    while include_subagents and q_idx < len(queue):
        key, cid, path = queue[q_idx]
        q_idx += 1
        if not path.is_file():
            continue
        for sub_cid in _discover_child_cids_streaming(path):
            for root in [path.parent.parent.parent.parent] + roots:
                sub_tp = root / sub_cid / ".system_generated" / "logs" / "transcript.jsonl"
                if sub_tp.is_file():
                    sp = sub_tp.resolve()
                    sk = _COMMON.norm_path_key(sp)
                    if sk not in nodes:
                        nodes[sk] = (sub_cid, sp)
                        parent_of[sk] = cid
                        children_of[key].append(sk)
                        queue.append((sk, sub_cid, sp))
                    elif sk != key and parent_of.get(sk) is None and not would_create_cycle(key, sk):
                        parent_of[sk] = cid
                        if sk not in children_of[key]:
                            children_of[key].append(sk)
                    break

    out = []
    emitted = set()

    def emit_subtree(k):
        if k in emitted:
            return
        emitted.add(k)
        cid, p = nodes[k]
        out.append((cid, p, parent_of.get(k)))
        for ck in children_of.get(k, ()):
            emit_subtree(ck)

    for k in seed_order:
        if parent_of.get(k) is None:
            emit_subtree(k)
    for k in seed_order:
        emit_subtree(k)
    return out


def _build_call_record(conv_id, parent_cid, model_seen, tc, tc_id, p_step, p_ts, st, orphan=False):
    if tc is not None:
        raw_name = tc.get("name") or "?"
        raw_args = parse_raw_args(tc)
        tool, tool_raw, norm_input = normalize_tool_and_input(raw_name, raw_args)
    else:
        raw_name, tool, tool_raw = "", "?", "unmatched_result"
        norm_input = {"unmatched_tool_call_id": tc_id} if tc_id else {"unmatched_step_index": st.get("step_index")}
    content = st.get("content") or ""
    status_val = st.get("status")
    hdr = _extract_shell_header(content)
    is_shell = (
        (raw_name.lower() in SHELL_TOOL_NAMES)
        or (tool.lower() in SHELL_TOOL_NAMES)
        or (orphan and bool(hdr) and _COMMON.exit_code_from(hdr) is not None)
    )
    dur, is_err, is_int = parse_step_timing_and_error(content, status_val, is_shell=is_shell)
    kind, res_status = classify_step_outcome(status_val, content, is_shell=is_shell)
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
    steps, tp, sources = load_transcript_steps_with_sources(transcript_path)
    calls, no_result, unmatched_results = [], 0, 0
    model_seen, batch_pending, batch_generics, batch_model = None, [], [], None

    def flush_batch():
        nonlocal no_result, unmatched_results
        if not batch_pending and not batch_generics:
            return
        used_p = [False] * len(batch_pending)
        used_g = [False] * len(batch_generics)
        emitted = []
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
        for pi, gi in zip([pi for pi, u in enumerate(used_p) if not u], [gi for gi, u in enumerate(used_g) if not u]):
            used_p[pi] = used_g[gi] = True
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
        elif stype == "GENERIC":
            step_cid = _extract_call_id(st, ("tool_call_id", "toolCallId", "call_id", "callId", "tool_use_id"))
            batch_generics.append((st, step_cid))
    flush_batch()
    return calls, no_result, unmatched_results, tp, sources


def extract_calls_from_transcript(conv_id, transcript_path, parent_cid=None):
    calls, no_result, _, tp, _ = extract_calls_detailed(conv_id, transcript_path, parent_cid=parent_cid)
    return calls, no_result, tp


def resolve_worktree_audit_path(log_template, payload, audit_mod, tool_cwd=None):
    tpl = str(log_template)
    if "{worktree}" in tpl and not os.environ.get("DW_WORKER_NAME"):
        ws = payload.get("workspacePaths") if isinstance(payload.get("workspacePaths"), list) else []
        ws_dir = next((w for w in ws if isinstance(w, str) and w.strip() and pathlib.Path(w).exists()), None)
        p_cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) and payload["cwd"].strip() and pathlib.Path(payload["cwd"]).exists() else None
        base_dir = ws_dir or p_cwd or os.getcwd()
        chosen_dir = None
        if base_dir:
            b_root, b_common, b_linked = _COMMON.find_git_root_and_common(base_dir)
            if isinstance(tool_cwd, str) and tool_cwd.strip() and pathlib.Path(tool_cwd).exists():
                t_root, t_common, t_linked = _COMMON.find_git_root_and_common(tool_cwd)
                if b_common is None or (t_common is not None and _COMMON.norm_path_key(t_common) == _COMMON.norm_path_key(b_common)):
                    chosen_dir = (t_root or pathlib.Path(tool_cwd).resolve(), t_linked)
                else:
                    chosen_dir = (b_root or pathlib.Path(base_dir).resolve(), b_linked)
            elif b_root is not None:
                chosen_dir = (b_root, b_linked)
            else:
                bp = pathlib.Path(base_dir).resolve()
                chosen_dir = (bp.parent if bp.name in (".agents", ".agent") and bp.parent.is_dir() else bp, False)
        elif isinstance(tool_cwd, str) and tool_cwd.strip() and pathlib.Path(tool_cwd).exists():
            t_root, _, t_linked = _COMMON.find_git_root_and_common(tool_cwd)
            if t_root is not None:
                chosen_dir = (t_root, t_linked)
            else:
                tp = pathlib.Path(tool_cwd).resolve()
                chosen_dir = (tp.parent if tp.name in (".agents", ".agent") and tp.parent.is_dir() else tp, False)
        if chosen_dir is not None:
            wt_root, is_linked = chosen_dir
            wt_base = audit_mod.safe_name(wt_root.resolve().name)
            if is_linked:
                h6 = hashlib.sha256(_COMMON.norm_path_key(wt_root).encode("utf-8")).hexdigest()[:6]
                wt_id = f"{wt_base}-{h6}"
            else:
                wt_id = wt_base
            tpl = tpl.replace("{worktree}", wt_id)
    folder_fallback = payload.get("cwd") or (payload.get("workspacePaths") or [None])[0]
    return audit_mod.expand(tpl, folder=folder_fallback)


def append_payload_entry(log_template, payload, step_info=None):
    audit = load_sibling("audit_log")
    if not isinstance(payload, dict):
        return 0
    tc = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    raw_name = tc.get("name") if isinstance(tc.get("name"), str) else "?"
    raw_args = parse_raw_args(tc)
    tool, tool_raw, norm_input = normalize_tool_and_input(raw_name, raw_args)
    is_shell = (raw_name.lower() in SHELL_TOOL_NAMES) or (tool.lower() in SHELL_TOOL_NAMES)
    step_idx = payload.get("stepIdx")
    conv_id = payload.get("conversationId") if isinstance(payload.get("conversationId"), str) else None
    err = payload.get("error")
    tp = payload.get("transcriptPath")
    inline_res = None
    for rk in ("toolResult", "toolResponse", "output", "response"):
        if rk in payload and payload[rk] is not None:
            inline_res = payload[rk] if isinstance(payload[rk], str) else json.dumps(payload[rk], ensure_ascii=False)
            break

    if step_info is not None:
        found_step, step_status, body, dur_ms = step_info
    elif tp and step_idx is not None:
        found_step, step_status, body, dur_ms = find_step_info_in_transcript(tp, step_idx)
    else:
        found_step, step_status, body, dur_ms = False, None, "", None

    if not found_step and inline_res is not None:
        found_step, body = True, inline_res
        if dur_ms is None:
            dur_ms = _extract_step_duration_ms(body)

    if err:
        kind, res = "failure", audit.output_record("error", err)
    elif found_step:
        kind, res_status = classify_step_outcome(step_status, body, is_shell=is_shell)
        res = audit.output_record(res_status, body)
    else:
        kind, res = "result", audit.output_record("unverified", "")

    fields = {
        "session_id": conv_id,
        "agent_id": None,
        "tool": tool,
        "tool_raw": tool_raw,
        "tool_use_id": tc.get("id") or (f"step-{step_idx}" if step_idx is not None else None),
        "input": audit.summary(norm_input),
        "kind": kind,
        "result": res,
        "duration_ms": dur_ms,
    }
    tool_cwd = unwrap_arg_value(raw_args.get("Cwd") or raw_args.get("cwd") or raw_args.get("workspace")) if isinstance(raw_args, dict) else None
    target = resolve_worktree_audit_path(log_template, payload, audit, tool_cwd=tool_cwd)
    audit.append_entry(target, fields)
    return 0


def cmd_hook_append(args):
    raw = sys.stdin.read() if sys.stdin is not None else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        print("{}")
        return 0
    if not isinstance(payload, dict):
        print("{}")
        return 0
    try:
        append_payload_entry(args.log, payload)
    except Exception as exc:
        print(f"agy_audit hook-append error: {exc}", file=sys.stderr)
        print("{}")
        return 1
    print("{}")
    return 0


def cmd_build(args):
    audit = load_sibling("audit_log")
    out_path = pathlib.Path(args.out)
    if out_path.exists() and not args.force:
        print(f"AGY_AUDIT: REFUSED {out_path} already exists (pass --force)", file=sys.stderr)
        return 1
    sources = discover_transcripts(args.transcript, args.conversation, args.brain_root, args.include_subagents)
    if not sources:
        print("AGY_AUDIT: CANNOT_RUN no Antigravity transcripts matched", file=sys.stderr)
        return 2

    all_calls, source_meta, total_no_result, total_unmatched = [], [], 0, 0
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
                "conversation_id": cid, "name": srec["name"], "role": srec["role"],
                "sha256": srec["sha256"], "bytes": srec["bytes"],
            })
        all_calls.extend(calls)

    if not all_calls:
        print("AGY_AUDIT: CANNOT_RUN no tool calls found in transcripts", file=sys.stderr)
        return 2
    all_calls.sort(key=lambda c: (c["ts_dt"] or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), c["step_idx"] or 0))

    rebuild_line = {
        "seq": 1, "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
        "session_id": sources[0][0], "agent_id": None, "tool": None, "tool_use_id": None, "input": "",
        "kind": "rebuild", "result": {"status": "rebuilt"}, "duration_ms": None,
        "source": {
            "by": "agy_audit.py", "version": REBUILD_VERSION, "transcripts": source_meta,
            "lines": len(all_calls), "no_result": total_no_result, "unmatched_results": total_unmatched,
        },
        "prev": audit.GENESIS,
    }
    rebuild_line["hash"] = audit.digest(rebuild_line)
    entries = [rebuild_line]
    prev_hash = rebuild_line["hash"]
    for seq, c in enumerate(all_calls, 2):
        input_str = audit.summary(c["input_obj"], limit=args.input_limit)
        input_cut = args.input_limit is not None and len(audit.summary(c["input_obj"], limit=None)) > args.input_limit
        entry = {
            "seq": seq,
            "ts": (c["ts_dt"] or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec="milliseconds"),
            "session_id": c["conv_id"], "agent_id": c["agent_id"], "tool": c["tool"], "tool_raw": c["tool_raw"],
            "tool_use_id": c.get("tc_id") or f"step-{c['step_idx']}",
            "input": input_str, "kind": c["kind"], "result": audit.output_record(c["result_status"], c["content"]),
            "duration_ms": c["duration_ms"], "prev": prev_hash,
        }
        if input_cut:
            entry["input_cut"] = True
        entry["hash"] = audit.digest(entry)
        prev_hash = entry["hash"]
        entries.append(entry)

    _COMMON.write_text_atomic(out_path, "".join(audit.canonical(e) + "\n" for e in entries))
    head_sha = entries[-1]["hash"]
    if args.json:
        print(json.dumps({
            "file": out_path.as_posix(), "lines": len(entries), "calls": len(all_calls),
            "conversations": len(sources), "no_result": total_no_result,
            "unmatched_results": total_unmatched, "head_seq": entries[-1]["seq"], "head_sha": head_sha,
        }, ensure_ascii=False))
    else:
        print(f"AGY_AUDIT file={out_path.as_posix()} lines={len(entries)} calls={len(all_calls)} conversations={len(sources)} no_result={total_no_result} unmatched={total_unmatched}")
        print(f"AUDIT_HEAD file={out_path.as_posix()} seq={entries[-1]['seq']} sha={head_sha}")
    return 0


def cmd_verify(args):
    audit = load_sibling("audit_log")
    argv = ["verify", "--log", args.log]
    for flag, val in (
        ("--from", args.from_seq), ("--to", args.to_seq), ("--anchor-log", args.anchor_log),
        ("--anchor-rev", args.anchor_rev), ("--anchor-path", args.anchor_path),
        ("--repo", args.repo), ("--cite", args.cite),
    ):
        if val is not None:
            argv += [flag, str(val)]
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
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        dt = datetime.datetime.fromisoformat(text).replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
        if end:
            dt = dt + datetime.timedelta(days=1)
        return dt.astimezone(datetime.timezone.utc)
    return parse_iso(text)


def read_path_from_call(tool_raw, input_obj):
    if (tool_raw or "").lower() in READ_TOOLS:
        p = input_obj.get("Path") or input_obj.get("AbsolutePath") or input_obj.get("file_path") or input_obj.get("DirectoryPath") or input_obj.get("path")
        return str(p) if p else None
    return None


def cmd_loops(args):
    loop = load_sibling("loop_guard")
    audit = load_sibling("audit_log")
    try:
        conf = loop.load_config(args.config)
    except loop.InputError as err:
        print(f"AGY_LOOPS: INPUT_ERROR {err}", file=sys.stderr)
        return 2
    for key in ("window", "repeat", "retry_errors", "poll", "ping_pong", "bad_args"):
        val = getattr(args, key, None)
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
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                ts_dt = parse_iso(entry.get("ts"))
                if (since_dt and ts_dt and ts_dt < since_dt) or (until_dt and ts_dt and ts_dt >= until_dt):
                    continue
                ev = loop.event_of(entry)
                if ev:
                    events.append(ev)

    if args.transcript or args.conversation or not args.audit:
        sources = discover_transcripts(args.transcript, args.conversation, args.brain_root, args.include_subagents)
        for cid, tp, parent_cid in sources:
            calls, _, _ = extract_calls_from_transcript(cid, tp, parent_cid)
            for c in calls:
                if (since_dt and c["ts_dt"] and c["ts_dt"] < since_dt) or (until_dt and c["ts_dt"] and c["ts_dt"] >= until_dt):
                    continue
                if args.reads:
                    rp = read_path_from_call(c["tool_raw"], c["input_obj"])
                    if rp:
                        read_counts[rp] += 1
                summary_text = loop.plain_summary(audit, c["input_obj"])
                status = "interrupted" if c["is_int"] else ("error" if c["is_err"] else "ok")
                err_cls = ("validation" if loop.VALIDATION.search(c["content"]) else "other") if status == "error" else None
                events.append({
                    "seq": c["step_idx"], "session": c["conv_id"], "agent": c["agent_id"],
                    "tool": c["tool"], "args": loop.short(summary_text), "input": summary_text,
                    "status": status, "result": loop.raw_hash(c["content"])[:12],
                    "error_class": err_cls, "error_text_known": True,
                })

    if not events:
        payload = {"findings_total": 0, "counts": {}, "not_verified": [], "events": 0, "streams": 0}
        if args.out:
            _COMMON.write_json_atomic(args.out, payload)
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
        "findings_total": len(findings), "counts": dict(counts), "not_verified": not_verified,
        "events": len(events), "streams": len({(e["session"], e["agent"]) for e in events}),
        "sessions": len({e["session"] for e in events}),
        "findings": findings[: args.show],
        "top_reads": [{"path": k, "count": v} for k, v in read_counts.most_common(10)] if args.reads else [],
    }
    if args.out:
        _COMMON.write_json_atomic(args.out, payload)
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
        print(f"LOOP_GUARD: {f'FOUND {len(findings)}' if findings else 'NONE'} events={payload['events']} streams={payload['streams']}")
    return 1 if findings else 0


def cmd_inject(args):
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

    s_ha = sub.add_parser("hook-append", help="PostToolUse hook: append Antigravity tool call to hash chain")
    s_ha.add_argument("--log", required=True)

    s_b = sub.add_parser("build", help="rebuild audit log from Antigravity transcript.jsonl")
    s_b.add_argument("--out", required=True)
    s_b.add_argument("--force", action="store_true")
    s_b.add_argument("--transcript", action="append", default=[])
    s_b.add_argument("--conversation", action="append", default=[])
    s_b.add_argument("--brain-root", action="append", default=[])
    s_b.add_argument("--include-subagents", action="store_true")
    s_b.add_argument("--input-limit", type=int)
    s_b.add_argument("--json", action="store_true")

    s_v = sub.add_parser("verify", help="verify audit hash chain via tools/audit_log.py")
    s_v.add_argument("--log", required=True)
    s_v.add_argument("--from", dest="from_seq", type=int)
    s_v.add_argument("--to", dest="to_seq", type=int)
    s_v.add_argument("--anchor-log")
    s_v.add_argument("--anchor-rev")
    s_v.add_argument("--anchor-path")
    s_v.add_argument("--repo")
    s_v.add_argument("--cite")

    s_l = sub.add_parser("loops", help="detect loops and re-read hot paths")
    s_l.add_argument("--audit", action="append", default=[])
    s_l.add_argument("--transcript", action="append", default=[])
    s_l.add_argument("--conversation", action="append", default=[])
    s_l.add_argument("--brain-root", action="append", default=[])
    s_l.add_argument("--include-subagents", action="store_true")
    s_l.add_argument("--since")
    s_l.add_argument("--until")
    s_l.add_argument("--config")
    s_l.add_argument("--window", type=int)
    s_l.add_argument("--repeat", type=int)
    s_l.add_argument("--retry-errors", type=int)
    s_l.add_argument("--poll", type=int)
    s_l.add_argument("--ping-pong", type=int)
    s_l.add_argument("--bad-args", type=int)
    s_l.add_argument("--reads", action="store_true")
    s_l.add_argument("--show", type=int, default=20)
    s_l.add_argument("--json", action="store_true")
    s_l.add_argument("--out")

    s_i = sub.add_parser("inject", help="scan Antigravity transcript tool outputs for prompt injection")
    s_i.add_argument("--transcript", action="append", default=[])
    s_i.add_argument("--conversation", action="append", default=[])
    s_i.add_argument("--brain-root", action="append", default=[])
    s_i.add_argument("--include-subagents", action="store_true")
    s_i.add_argument("--tool")
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
