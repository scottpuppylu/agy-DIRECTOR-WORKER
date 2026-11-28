#!/usr/bin/env python3
"""Token & context usage report for agy-DIRECTOR-WORKER (3.0.0-agy.7), reading Antigravity transcripts.

Uses single-pass streaming, G7 `workspacePaths` attribution, and frozen base
`tools/_common.py` D7 `usage_number` / `usage_partial` / `partial_line` checks
so invalid metric values become 0 + `partial: true` (never silent valid numbers).
"""
import argparse
import collections
import contextlib
import hashlib
import io
import json
import os
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent


def _load_common():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_agy_use_common", HERE / "agy_common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_COMMON = _load_common()
EFFORT_SUFFIX = re.compile(r"-(low|medium|high|xhigh|max)$", re.I)
WS_KEYS = ("workspacePaths", "workspace_paths", "workspace", "workspacePath", "cwd", "project_dir", "projectDir")


def load_sibling(name):
    return _COMMON.load_sibling(HERE, name, prefix="_agy_use_")


def parse_effort_from_model(model_name):
    if not model_name or not isinstance(model_name, str):
        return "UNKNOWN"
    m = EFFORT_SUFFIX.search(model_name.strip())
    return m.group(1).lower() if m else "fixed"


def to_local_iso(dt):
    return dt.astimezone().isoformat(timespec="seconds") if dt else "UNKNOWN"


def _case_norm(s):
    return _COMMON.case_norm(s)


def compile_project_patterns(project_norm_list):
    patterns = []
    for raw in project_norm_list or []:
        p = raw.rstrip("/")
        if p:
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
    return _COMMON.unwrap_arg_value(val)


def _step_session_workspaces(st):
    out, seen = [], set()

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

    for k in WS_KEYS:
        if k in st and st[k] is not None:
            add_val(st[k])
    meta = st.get("metadata")
    if isinstance(meta, dict):
        for k in WS_KEYS:
            if k in meta and meta[k] is not None:
                add_val(meta[k])
    return out


def _step_tool_cwds(st):
    out, seen = [], set()
    if st.get("type") != "PLANNER_RESPONSE":
        return out
    for tc in st.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        raw = _COMMON.parse_raw_args(tc)
        for k in ("Cwd", "cwd", "workspace"):
            if k in raw and raw[k] is not None:
                v = _unwrap_arg_val(raw[k])
                if isinstance(v, str):
                    for cnorm in _ws_path_variants(v):
                        if cnorm and cnorm not in seen:
                            seen.add(cnorm)
                            out.append(cnorm)
    return out


def extract_session_workspaces(steps):
    out, seen = [], set()
    for st in steps:
        if isinstance(st, dict):
            for cnorm in _step_session_workspaces(st):
                if cnorm not in seen:
                    seen.add(cnorm)
                    out.append(cnorm)
    return out


def extract_tool_cwds(steps):
    out, seen = [], set()
    for st in steps:
        if isinstance(st, dict):
            for cnorm in _step_tool_cwds(st):
                if cnorm not in seen:
                    seen.add(cnorm)
                    out.append(cnorm)
    return out


def extract_structured_workspaces(steps):
    out, seen = [], set()
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


def _path_within_target(candidate, targets):
    return any(candidate == tgt or candidate.startswith(tgt + "/") for tgt in targets)


def _match_attribution_from_collections(session_ws, tool_cwds, has_text_hit, project_norm_list, project_patterns):
    if not project_patterns and not project_norm_list:
        return True, "unfiltered"
    targets = [_case_norm(p.rstrip("/")) for p in (project_norm_list or []) if p.strip()]
    if session_ws:
        for wp in session_ws:
            if _path_within_target(wp, targets):
                return True, "structured_workspace"
        return False, "structured_workspace_mismatch"
    for cw in tool_cwds:
        if _path_within_target(cw, targets):
            return True, "structured_workspace"
    if has_text_hit:
        return True, "text_heuristic"
    return False, "no_match"


def classify_project_match(steps, project_norm_list, project_patterns):
    if not project_patterns and not project_norm_list:
        return True, "unfiltered"
    targets = [_case_norm(p.rstrip("/")) for p in (project_norm_list or []) if p.strip()]
    session_ws = extract_session_workspaces(steps)
    if session_ws:
        for wp in session_ws:
            if _path_within_target(wp, targets):
                return True, "structured_workspace"
        return False, "structured_workspace_mismatch"
    for cw in extract_tool_cwds(steps):
        if _path_within_target(cw, targets):
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
    return classify_project_match(steps, project_norm_list, project_patterns)[0]


def _project_step(st, agy_audit, proj_patterns):
    dt = agy_audit.parse_iso(st.get("created_at"))
    stype = st.get("type")
    step_ws = tuple(_step_session_workspaces(st))
    step_cwds = tuple(_step_tool_cwds(st)) if stype == "PLANNER_RESPONSE" else ()
    text_hit = False
    if proj_patterns:
        if stype == "USER_INPUT":
            txt = _case_norm((st.get("content") or "").replace("\\", "/"))
            text_hit = any(rx.search(txt) for rx in proj_patterns)
        elif stype == "PLANNER_RESPONSE":
            txt = _case_norm(_unwrapped_tool_calls_text(st.get("tool_calls") or []))
            text_hit = any(rx.search(txt) for rx in proj_patterns)

    if stype == "PLANNER_RESPONSE":
        inv = []
        m = st.get("model")
        model = m if (isinstance(m, str) and m) else None
        in_t, ca_t, out_t = _COMMON.usage_numbers(
            st, ("input_tokens", "cache_read_tokens", "output_tokens"), prefix="", invalid=inv
        )
        tnames = tuple(str(tc["name"]) for tc in (st.get("tool_calls") or []) if isinstance(tc, dict) and tc.get("name"))
        inv_pairs = tuple(inv)
    else:
        model, in_t, ca_t, out_t, tnames, inv_pairs = None, 0, 0, 0, (), ()

    return (dt, stype, model, in_t, ca_t, out_t, tnames, inv_pairs, step_ws, step_cwds, text_hit)


def summarize_conversation_streaming(conv_id, transcript_path, parent_cid, agy_audit, proj_norms=None, proj_patterns=None):
    tp = pathlib.Path(transcript_path)
    if not tp.is_file():
        raise FileNotFoundError(f"transcript not found: {tp}")
    resolved = tp.resolve()

    by_idx = {}
    anon_key = -1
    with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                st = json.loads(line)
            except ValueError:
                continue
            if not isinstance(st, dict):
                continue
            s_idx = st.get("step_index")
            key = s_idx if isinstance(s_idx, int) and not isinstance(s_idx, bool) else anon_key
            if key == anon_key:
                anon_key -= 1
            by_idx[key] = _project_step(st, agy_audit, proj_patterns)

    started_dt = last_dt = None
    models, tool_counts = collections.Counter(), collections.Counter()
    uncached_in = cache_read = output_tok = last_prompt = peak_prompt = turns = checkpoints = 0
    last_model = None
    invalid_list = []
    invalid_counts = collections.Counter()
    session_ws, ws_seen = [], set()
    tool_cwds, cwd_seen = [], set()
    has_text_hit = False

    for dt, stype, model, in_t, ca_t, out_t, tnames, inv_pairs, sws, scwds, t_hit in by_idx.values():
        if dt:
            if started_dt is None or dt < started_dt:
                started_dt = dt
            if last_dt is None or dt > last_dt:
                last_dt = dt
        for w in sws:
            if w not in ws_seen:
                ws_seen.add(w)
                session_ws.append(w)
        for c in scwds:
            if c not in cwd_seen:
                cwd_seen.add(c)
                tool_cwds.append(c)
        if t_hit:
            has_text_hit = True
        if stype == "CHECKPOINT":
            checkpoints += 1
        elif stype == "PLANNER_RESPONSE":
            turns += 1
            if model:
                models[model] += 1
                last_model = model
            uncached_in += in_t
            cache_read += ca_t
            output_tok += out_t
            for fk, fval in inv_pairs:
                invalid_list.append((fk, fval))
                invalid_counts[fk] += 1
            prompt_t = in_t + ca_t
            if prompt_t > 0:
                last_prompt = prompt_t
                if prompt_t > peak_prompt:
                    peak_prompt = prompt_t
            for tnm in tnames:
                tool_counts[tnm] += 1

    matched, attr_mode = _match_attribution_from_collections(
        session_ws, tool_cwds, has_text_hit, proj_norms, proj_patterns
    )
    started_local = to_local_iso(started_dt)
    model_name = last_model or (models.most_common(1)[0][0] if models else "UNKNOWN")
    total_tok = uncached_in + cache_read + output_tok
    denom = uncached_in + cache_read
    hit_pct = round((cache_read / denom) * 100.0, 2) if denom > 0 else 0.0
    p_info = _COMMON.usage_partial(invalid_list)

    return {
        "conversation_id": conv_id,
        "parent_id": parent_cid,
        "role": "subagent" if parent_cid else "session",
        "transcript": resolved.as_posix(),
        "started": started_local,
        "started_date": started_local[:10] if started_local != "UNKNOWN" else "UNKNOWN",
        "model": model_name,
        "effort": parse_effort_from_model(model_name),
        "models": dict(models),
        "steps": len(by_idx),
        "turns": turns,
        "checkpoints": checkpoints,
        "input_tokens": uncached_in,
        "cache_read_tokens": cache_read,
        "output_tokens": output_tok,
        "total_tokens": total_tok,
        "last_prompt_tokens": last_prompt,
        "peak_prompt_tokens": peak_prompt,
        "cache_hit_pct": hit_pct,
        "tool_calls": sum(tool_counts.values()),
        "tools": dict(tool_counts),
        "invalid_metric_fields": dict(invalid_counts),
        "partial": p_info["partial"],
        "invalid_count": p_info["invalid_count"],
        "invalid_fields": p_info["invalid_fields"],
        "_invalid_pairs": invalid_list,
        "_matched": matched,
        "_attr_mode": attr_mode,
        "_started_dt": started_dt,
    }


def summarize_conversation(conv_id, transcript_path, parent_cid, agy_audit):
    row = summarize_conversation_streaming(conv_id, transcript_path, parent_cid, agy_audit)
    row.pop("_matched", None)
    row.pop("_attr_mode", None)
    row.pop("_invalid_pairs", None)
    return row


def write_out(path, payload):
    _COMMON.write_json_atomic(path, payload)


def effort_check(order_path, sessions):
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
    ap.add_argument("--brain-root", action="append", default=[])
    ap.add_argument("--transcript", action="append", default=[])
    ap.add_argument("--conversation", action="append", default=[])
    ap.add_argument("--project-dir", action="append", default=[])
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--include-subagents", action="store_true")
    ap.add_argument("--effort-check", metavar="ORDER")
    ap.add_argument("--loops", action="store_true")
    ap.add_argument("--loops-config")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
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

    rows, seen_paths, assigned_cids, matched_raw_cids = [], set(), set(), set()
    all_invalid = []
    for cid, tp, parent_cid in sources:
        tp_key = _COMMON.norm_path_key(tp)
        if tp_key in seen_paths:
            continue
        seen_paths.add(tp_key)
        try:
            row = summarize_conversation_streaming(cid, tp, parent_cid, agy_audit, proj_norms=proj_norms, proj_patterns=proj_patterns)
        except OSError:
            continue
        matched = row.pop("_matched", True)
        attr_mode = row.pop("_attr_mode", "unfiltered")
        inv_pairs = row.pop("_invalid_pairs", [])
        row.pop("_started_dt", None)
        if a.project_dir:
            if not parent_cid and not matched:
                continue
            if parent_cid and parent_cid not in matched_raw_cids and not matched:
                continue
            row["attribution_mode"] = attr_mode if matched else "inherited_parent"
        else:
            row["attribution_mode"] = "unfiltered"
        matched_raw_cids.add(cid)
        sdate = row["started_date"]
        if (a.since and sdate != "UNKNOWN" and sdate < a.since) or (a.until and sdate != "UNKNOWN" and sdate > a.until):
            continue
        out_cid = cid
        if out_cid in assigned_cids:
            h8 = hashlib.sha256(tp_key.encode("utf-8")).hexdigest()[:8]
            out_cid = f"{cid}#{h8}"
        assigned_cids.add(out_cid)
        row["conversation_id"] = out_cid
        all_invalid.extend(inv_pairs)
        rows.append(row)

    if not rows:
        no_data = {
            "status": "NO_DATA", "reason": "no matching Antigravity transcripts found",
            "hints": [
                "check --brain-root (default ~/.gemini/antigravity/brain) or pass --transcript / --conversation",
                "note that --project-dir matches workspacePaths or paths in USER_INPUT / tool arguments",
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
    p_meta = _COMMON.usage_partial(all_invalid)
    totals = {
        "sessions": len(rows), "turns": 0, "steps": 0, "checkpoints": 0,
        "input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0,
        "total_tokens": 0, "tool_calls": 0,
        "invalid_metric_fields_total": p_meta["invalid_count"],
        "partial": p_meta["partial"],
        "invalid_count": p_meta["invalid_count"],
        "invalid_fields": p_meta["invalid_fields"],
    }
    for r in rows:
        d = daily[r["started_date"]]
        d["sessions"] += 1
        for k in ("turns", "input_tokens", "cache_read_tokens", "output_tokens", "total_tokens"):
            d[k] += r[k]
        for k in ("turns", "steps", "checkpoints", "input_tokens", "cache_read_tokens", "output_tokens", "total_tokens", "tool_calls"):
            totals[k] += r[k]
        for m, cnt in r["models"].items():
            model_mix[m] += cnt

    loops_summary = None
    if a.loops:
        with tempfile.TemporaryDirectory(prefix="agy_usage_loops_") as td:
            out_tmp = pathlib.Path(td) / "loops.json"
            loop_argv = ["loops", "--out", str(out_tmp)] + [x for r in rows for x in ("--transcript", r["transcript"])]
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
        "partial": p_meta["partial"],
        "invalid_count": p_meta["invalid_count"],
        "invalid_fields": p_meta["invalid_fields"],
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
          + (f" partial=1 invalid_fields={totals['invalid_count']}" if totals["partial"] else ""))
    pline = _COMMON.partial_line(all_invalid)
    if pline:
        print(pline)
    if loops_summary:
        counts_txt = " ".join(f"{k}={v}" for k, v in (loops_summary.get("counts") or {}).items() if v) or "none"
        print(f"LOOPS: findings={loops_summary.get('findings_total', 0)} ({counts_txt}) events={loops_summary.get('events', 0)}")
    if attr_modes.get("text_heuristic"):
        print("NOTE: project attribution used text_heuristic (transcript lacked structured workspacePaths); pass --conversation for exact cross-project comparisons")
    return 0


if __name__ == "__main__":
    sys.exit(main())
