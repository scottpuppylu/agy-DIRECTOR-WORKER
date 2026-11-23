#!/usr/bin/env python3
"""Token & context usage report for agy-DIRECTOR-WORKER (agydw), reading Antigravity transcripts.

Reads Antigravity's documented transcript.jsonl files across App, CLI, and IDE roots and
reports counts only (never prints prompt text or tool contents).

Empirically verified Antigravity PLANNER_RESPONSE token fields:
  input_tokens       uncached input tokens for that turn
  cache_read_tokens  prompt-cache hit tokens for that turn
  output_tokens      generated output tokens (including thinking)
  prompt_size        input_tokens + cache_read_tokens (active context size at that turn)

Usage:
  python -I -X utf8 agy_usage.py [--project-dir PATH] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
                                 [--conversation ID|PREFIX ...] [--transcript FILE ...]
                                 [--brain-root DIR ...] [--include-subagents] [--loops]
                                 [--effort-check ORDER] [--json] [--out FILE]
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
EFFORT_SUFFIX = re.compile(r"-(low|medium|high|xhigh|max)$", re.I)


def load_sibling(name):
    """Load a sibling Python module cleanly."""
    spec = importlib.util.spec_from_file_location(f"_agy_use_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_effort_from_model(model_name):
    """Extract reasoning effort tier from Antigravity model string if present."""
    if not model_name or not isinstance(model_name, str):
        return "UNKNOWN"
    m = EFFORT_SUFFIX.search(model_name.strip())
    return m.group(1).lower() if m else "fixed"


def to_local_iso(dt):
    """Format UTC datetime in local timezone as YYYY-MM-DDTHH:MM:SS+ZZ:ZZ."""
    if not dt:
        return "UNKNOWN"
    return dt.astimezone().isoformat(timespec="seconds")


def _case_norm(s):
    """Preserve exact casing on POSIX filesystems while staying case-insensitive on Windows."""
    return s.lower() if os.name == "nt" else s


def compile_project_patterns(project_norm_list):
    """Compile boundary-safe path regexes so '/tmp/app' never matches '/tmp/app-long'."""
    patterns = []
    for raw in project_norm_list or []:
        p = raw.rstrip("/")
        if not p:
            continue
        patterns.append(re.compile(rf"(?<![A-Za-z0-9._:/-]){re.escape(p)}(?=$|[/\\\"'\s,;:)\]}}])"))
    return patterns


def _norm_ws_path(val):
    if not isinstance(val, str) or not val.strip():
        return None
    s = val.strip().replace("\\", "/")
    if os.name == "nt" and len(s) >= 2 and s[1] == ":":
        s = s[0].lower() + s[1:]
    return s.rstrip("/") or "/"


def _ws_path_variants(val):
    """Return normalized path variants (raw + resolved when absolute) preserving POSIX case."""
    nv = _norm_ws_path(val)
    if not nv:
        return []
    out = [_case_norm(nv)]
    try:
        p = pathlib.Path(val.strip())
        if p.is_absolute():
            rv = _norm_ws_path(str(p.resolve()))
            if rv:
                rc = _case_norm(rv)
                if rc not in out:
                    out.append(rc)
    except (OSError, RuntimeError, ValueError):
        pass
    return out


def _unwrap_arg_val(val):
    if isinstance(val, str):
        s = val.strip()
        if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or s[0] in "[{"):
            try:
                return json.loads(s)
            except ValueError:
                return val
    return val


def extract_session_workspaces(steps):
    """Extract normalized session/step-level workspace paths (excluding per-call tool Cwd)."""
    out = []
    seen = set()

    def add_val(v):
        v = _unwrap_arg_val(v)
        if isinstance(v, str):
            for cnorm in _ws_path_variants(v):
                if cnorm and cnorm not in seen:
                    seen.add(cnorm)
                    out.append(cnorm)
        elif isinstance(v, list):
            for item in v:
                add_val(item)

    ws_keys = ("workspacePaths", "workspace_paths", "workspace", "workspacePath", "cwd", "project_dir", "projectDir")
    for st in steps:
        if not isinstance(st, dict):
            continue
        for k in ws_keys:
            if k in st and st[k] is not None:
                add_val(st[k])
        meta = st.get("metadata")
        if isinstance(meta, dict):
            for k in ws_keys:
                if k in meta and meta[k] is not None:
                    add_val(meta[k])
    return out


def extract_tool_cwds(steps):
    """Extract normalized per-tool Cwd/workspace fields from PLANNER_RESPONSE tool_calls (unwrapped)."""
    out = []
    seen = set()

    def add_val(v):
        v = _unwrap_arg_val(v)
        if isinstance(v, str):
            for cnorm in _ws_path_variants(v):
                if cnorm and cnorm not in seen:
                    seen.add(cnorm)
                    out.append(cnorm)

    for st in steps:
        if not isinstance(st, dict) or st.get("type") != "PLANNER_RESPONSE":
            continue
        for tc in st.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            raw = tc.get("args") if tc.get("args") is not None else tc.get("arguments")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raw = {}
            if isinstance(raw, dict):
                for k in ("Cwd", "cwd", "workspace"):
                    if k in raw and raw[k] is not None:
                        add_val(raw[k])
    return out


def extract_structured_workspaces(steps):
    """Extract normalized workspace/cwd paths from both session metadata and unwrapped tool calls."""
    out = []
    seen = set()
    for path in extract_session_workspaces(steps) + extract_tool_cwds(steps):
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _unwrapped_tool_calls_text(tool_calls):
    cleaned = []
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        item = dict(tc)
        for ak in ("args", "arguments"):
            raw = item.get(ak)
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    pass
            if isinstance(raw, dict):
                item[ak] = {k: _unwrap_arg_val(v) for k, v in raw.items()}
        cleaned.append(item)
    return json.dumps(cleaned, ensure_ascii=False).replace("\\", "/")


def classify_project_match(steps, project_norm_list, project_patterns):
    """Strictly trust session-level workspacePaths; keep tool Cwd non-exclusive against project files."""
    if not project_patterns and not project_norm_list:
        return True, "unfiltered"

    targets = [_case_norm(p.rstrip("/")) for p in (project_norm_list or []) if p.strip()]
    session_ws = extract_session_workspaces(steps)
    if session_ws:
        for wp in session_ws:
            for tgt in targets:
                if wp == tgt or wp.startswith(tgt + "/") or tgt.startswith(wp + "/"):
                    return True, "structured_workspace"
        return False, "structured_workspace_mismatch"

    for cw in extract_tool_cwds(steps):
        for tgt in targets:
            if cw == tgt or cw.startswith(tgt + "/") or tgt.startswith(cw + "/"):
                return True, "structured_workspace"

    for st in steps:
        stype = st.get("type")
        if stype == "USER_INPUT":
            txt = _case_norm((st.get("content") or "").replace("\\", "/"))
            if any(rx.search(txt) for rx in project_patterns):
                return True, "text_heuristic"
        elif stype == "PLANNER_RESPONSE":
            txt = _case_norm(_unwrapped_tool_calls_text(st.get("tool_calls") or []))
            if any(rx.search(txt) for rx in project_patterns):
                return True, "text_heuristic"
    return False, "no_match"


def matches_project(steps, project_patterns, project_norm_list=None):
    """Backwards-compatible boolean project attribution helper."""
    ok, _ = classify_project_match(steps, project_norm_list, project_patterns)
    return ok


def safe_int_metric(val, invalid_counts, field_name):
    """Parse non-negative integer token counters safely, counting malformed values without crashing."""
    if val is None:
        return 0
    if isinstance(val, bool):
        invalid_counts[field_name] += 1
        return 0
    if isinstance(val, int):
        if val >= 0:
            return val
        invalid_counts[field_name] += 1
        return 0
    if isinstance(val, str):
        s = val.strip()
        if re.fullmatch(r"\d+", s):
            return int(s)
    invalid_counts[field_name] += 1
    return 0


def summarize_conversation(conv_id, transcript_path, parent_cid, agy_audit):
    """Aggregate exact token, model, checkpoint, and tool metrics for one conversation."""
    steps, resolved = agy_audit.load_transcript_steps(transcript_path)
    started_dt = None
    last_dt = None
    models = collections.Counter()
    tool_counts = collections.Counter()
    uncached_in = 0
    cache_read = 0
    output_tok = 0
    last_prompt = 0
    peak_prompt = 0
    turns = 0
    checkpoints = 0
    last_model = None
    invalid_counts = collections.Counter()

    for st in steps:
        dt = agy_audit.parse_iso(st.get("created_at"))
        if dt:
            if started_dt is None or dt < started_dt:
                started_dt = dt
            if last_dt is None or dt > last_dt:
                last_dt = dt
        stype = st.get("type")
        if stype == "CHECKPOINT":
            checkpoints += 1
        elif stype == "PLANNER_RESPONSE":
            turns += 1
            m = st.get("model")
            if isinstance(m, str) and m:
                models[m] += 1
                last_model = m
            in_t = safe_int_metric(st.get("input_tokens"), invalid_counts, "input_tokens")
            ca_t = safe_int_metric(st.get("cache_read_tokens"), invalid_counts, "cache_read_tokens")
            out_t = safe_int_metric(st.get("output_tokens"), invalid_counts, "output_tokens")
            uncached_in += in_t
            cache_read += ca_t
            output_tok += out_t
            prompt_t = in_t + ca_t
            if prompt_t > 0:
                last_prompt = prompt_t
                if prompt_t > peak_prompt:
                    peak_prompt = prompt_t
            for tc in st.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("name"):
                    tool_counts[str(tc["name"])] += 1

    total_prompt = uncached_in + cache_read
    cache_pct = round(100.0 * cache_read / total_prompt, 1) if total_prompt > 0 else 0.0
    local_dt = started_dt.astimezone() if started_dt else None

    return {
        "conversation_id": conv_id,
        "parent_id": parent_cid,
        "role": "subagent" if parent_cid else "session",
        "transcript": resolved.as_posix(),
        "started": to_local_iso(started_dt),
        "started_date": local_dt.strftime("%Y-%m-%d") if local_dt else "UNKNOWN",
        "ended": to_local_iso(last_dt),
        "model": last_model or "UNKNOWN",
        "models": dict(models),
        "effort": parse_effort_from_model(last_model),
        "turns": turns,
        "steps": len(steps),
        "checkpoints": checkpoints,
        "input_tokens": uncached_in,
        "cache_read_tokens": cache_read,
        "output_tokens": output_tok,
        "total_tokens": uncached_in + cache_read + output_tok,
        "last_prompt_tokens": last_prompt,
        "peak_prompt_tokens": peak_prompt,
        "cache_hit_pct": cache_pct,
        "tool_calls": sum(tool_counts.values()),
        "tools": dict(tool_counts),
        "invalid_metric_fields": dict(invalid_counts),
        "_steps": steps,
        "_started_dt": started_dt,
    }


def write_out(path, payload):
    """Write JSON report to path atomically enough for local use."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def effort_check(order_path, sessions):
    """Print EFFORT_ACTUAL lines comparing order MODEL / EFFORT with observed conversation models."""
    p = pathlib.Path(order_path)
    if not p.is_file():
        print(f"EFFORT_CHECK: MISSING order {p}", file=sys.stderr)
        return 2
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    m = re.search(r"^[ \t]*MODEL[ \t]*/[ \t]*EFFORT[ \t]*=[ \t]*([^/\n]+)/\s*([A-Za-z]+)", text, re.M)
    order_effort = m.group(2).strip().lower() if m else "UNKNOWN"
    title = re.search(r"^#\s+(\S+)", text, re.M)
    wo = title.group(1) if title else p.stem
    for s in sessions:
        eff = s["effort"]
        warn = "  # WARN mismatch" if (order_effort != "UNKNOWN" and eff not in ("UNKNOWN", "fixed", order_effort)) else ""
        print(f"EFFORT_ACTUAL {wo} conv={s['conversation_id'][:8]} model={s['model']} effort={eff} order={order_effort}{warn}")
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brain-root", action="append", default=[], help="override Antigravity brain directory (repeatable)")
    ap.add_argument("--transcript", action="append", default=[], help="transcript.jsonl file (repeatable)")
    ap.add_argument("--conversation", action="append", default=[], help="conversationId UUID or prefix (repeatable)")
    ap.add_argument("--project-dir", action="append", default=[], help="match conversations mentioning this folder path")
    ap.add_argument("--since", help="local start date YYYY-MM-DD (inclusive)")
    ap.add_argument("--until", help="local start date YYYY-MM-DD (inclusive)")
    ap.add_argument("--include-subagents", action="store_true", help="include child conversations launched via invoke_subagent")
    ap.add_argument("--effort-check", metavar="ORDER", help="compare order MODEL / EFFORT against matching conversations")
    ap.add_argument("--loops", action="store_true", help="include loop summary via agy_audit.py loops")
    ap.add_argument("--loops-config", help="optional loop_guard config JSON for --loops")
    ap.add_argument("--json", action="store_true", help="print JSON summary to stdout")
    ap.add_argument("--out", help="write JSON report to FILE")
    try:
        a = ap.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    agy_audit = load_sibling("agy_audit")
    sources = agy_audit.discover_transcripts(a.transcript, a.conversation, a.brain_root, a.include_subagents)
    proj_norms = []
    for p in a.project_dir:
        for cand in _ws_path_variants(p) or [_case_norm(str(pathlib.Path(p).resolve()).replace("\\", "/"))]:
            if cand not in proj_norms:
                proj_norms.append(cand)
    proj_patterns = compile_project_patterns(proj_norms)

    rows = []
    seen_paths = set()
    assigned_cids = set()
    matched_raw_cids = set()
    for cid, tp, parent_cid in sources:
        tp_key = os.path.normcase(pathlib.Path(tp).resolve().as_posix())
        if tp_key in seen_paths:
            continue
        seen_paths.add(tp_key)
        try:
            row = summarize_conversation(cid, tp, parent_cid, agy_audit)
        except OSError:
            continue
        steps_tmp = row.pop("_steps", [])
        row.pop("_started_dt", None)
        if a.project_dir:
            matched, attr_mode = classify_project_match(steps_tmp, proj_norms, proj_patterns)
            del steps_tmp
            if not parent_cid and not matched:
                continue
            if parent_cid and parent_cid not in matched_raw_cids and not matched:
                continue
            row["attribution_mode"] = attr_mode if matched else "inherited_parent"
        else:
            del steps_tmp
            row["attribution_mode"] = "unfiltered"
        sdate = row["started_date"]
        if a.since and sdate != "UNKNOWN" and sdate < a.since:
            continue
        if a.until and sdate != "UNKNOWN" and sdate > a.until:
            continue
        matched_raw_cids.add(cid)
        out_cid = cid
        if out_cid in assigned_cids:
            h8 = hashlib.sha256(tp_key.encode("utf-8")).hexdigest()[:8]
            out_cid = f"{cid}#{h8}"
        assigned_cids.add(out_cid)
        row["conversation_id"] = out_cid
        rows.append(row)

    if not rows:
        no_data = {
            "status": "NO_DATA",
            "reason": "no matching Antigravity transcripts found",
            "hints": [
                "check --brain-root (default ~/.gemini/antigravity/brain) or pass --transcript / --conversation",
                "note that --project-dir matches paths referenced in USER_INPUT or tool arguments",
            ],
        }
        if a.out:
            write_out(a.out, no_data)
        if a.json:
            print(json.dumps(no_data, indent=2, ensure_ascii=False))
        else:
            print("NO_DATA: no matching Antigravity transcripts found")
        return 2

    rows.sort(key=lambda r: r["started"])

    if a.effort_check:
        return effort_check(a.effort_check, rows)

    daily = collections.defaultdict(lambda: {"sessions": 0, "turns": 0, "input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    model_mix = collections.Counter()
    totals = {"sessions": len(rows), "turns": 0, "steps": 0, "checkpoints": 0, "input_tokens": 0,
              "cache_read_tokens": 0, "output_tokens": 0, "total_tokens": 0, "tool_calls": 0,
              "invalid_metric_fields_total": 0}
    for r in rows:
        d = daily[r["started_date"]]
        d["sessions"] += 1
        for k in ("turns", "input_tokens", "cache_read_tokens", "output_tokens", "total_tokens"):
            d[k] += r[k]
        for k in ("turns", "steps", "checkpoints", "input_tokens", "cache_read_tokens", "output_tokens", "total_tokens", "tool_calls"):
            totals[k] += r[k]
        totals["invalid_metric_fields_total"] += sum((r.get("invalid_metric_fields") or {}).values())
        for m, cnt in r["models"].items():
            model_mix[m] += cnt

    loops_summary = None
    if a.loops:
        import contextlib, io, tempfile
        with tempfile.TemporaryDirectory(prefix="agy_usage_loops_") as td:
            out_tmp = pathlib.Path(td) / "loops.json"
            loop_argv = ["loops", "--out", str(out_tmp)]
            for r in rows:
                loop_argv += ["--transcript", r["transcript"]]
            if a.loops_config:
                loop_argv += ["--config", a.loops_config]
            if a.since:
                loop_argv += ["--since", a.since]
            if a.until:
                loop_argv += ["--until", a.until]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                agy_audit.main(loop_argv)
            if out_tmp.is_file():
                loops_summary = json.loads(out_tmp.read_text(encoding="utf-8"))

    attr_modes = collections.Counter(r.get("attribution_mode", "unfiltered") for r in rows)
    report = {
        "status": "OK",
        "attribution_modes": dict(attr_modes),
        "totals": totals,
        "model_turns": dict(model_mix),
        "daily": dict(sorted(daily.items())),
        "sessions": rows,
        "loops": loops_summary,
    }
    if a.out:
        write_out(a.out, report)

    if a.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print("CONV_ID   ROLE      STARTED              MODEL                     EFFORT  TURNS  IN_UNCACHED  CACHE_READ   OUTPUT    PEAK_CTX  HIT%")
    for r in rows:
        print(f"{r['conversation_id'][:8]}  {r['role']:8s}  {r['started'][:19]:19s}  {r['model'][:24]:24s}  "
              f"{r['effort']:6s}  {r['turns']:5d}  {r['input_tokens']:11d}  {r['cache_read_tokens']:10d}  "
              f"{r['output_tokens']:8d}  {r['peak_prompt_tokens']:10d}  {r['cache_hit_pct']:4.1f}%")
    print("\nDAILY:")
    for day, d in sorted(daily.items()):
        print(f"  {day}  sessions={d['sessions']} turns={d['turns']} in={d['input_tokens']} cache={d['cache_read_tokens']} out={d['output_tokens']} total={d['total_tokens']}")
    print(f"\nTOTALS: sessions={totals['sessions']} turns={totals['turns']} checkpoints={totals['checkpoints']} "
          f"in={totals['input_tokens']} cache={totals['cache_read_tokens']} out={totals['output_tokens']} total={totals['total_tokens']}"
          + (f" invalid_fields={totals['invalid_metric_fields_total']}" if totals["invalid_metric_fields_total"] else ""))
    if totals["invalid_metric_fields_total"]:
        print(f"WARN: ignored {totals['invalid_metric_fields_total']} non-numeric token metric field(s) across transcript(s)")
    if loops_summary:
        counts_txt = " ".join(f"{k}={v}" for k, v in (loops_summary.get("counts") or {}).items() if v) or "none"
        print(f"LOOPS: findings={loops_summary.get('findings_total', 0)} ({counts_txt}) events={loops_summary.get('events', 0)}")
    if attr_modes.get("text_heuristic"):
        print("NOTE: project attribution used text_heuristic (transcript lacked structured workspacePaths); pass --conversation for exact cross-project comparisons")
    return 0


if __name__ == "__main__":
    sys.exit(main())
