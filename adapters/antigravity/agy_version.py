#!/usr/bin/env python3
"""Verify monotonic `-agy.N` variant version numbering across base upgrades (3.0.0-agy.7).

Enforces the variant serial rule:
  - `VERSION` matches `<BASE_VERSION>-agy.<N>` (positive integer N).
  - `N` strictly never resets to 1 when `BASE_VERSION` bumps (N > max prior agy.K seen in
    `CHANGELOG.md` headings and optional git tags).
  - The top version heading in `CHANGELOG.md` matches `VERSION`.
"""
import argparse
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parent.parent
AGY_VER_RE = re.compile(r"^([0-9]+\.[0-9]+\.[0-9]+)-agy\.([1-9][0-9]*)$")
HIST_AGY_RE = re.compile(r"\b[0-9]+\.[0-9]+\.[0-9]+-agy\.([1-9][0-9]*)\b")


def check_version(root, check_git_tags=True):
    root = pathlib.Path(root).resolve()
    problems = []

    vf = root / "VERSION"
    bf = root / "BASE_VERSION"
    cf = root / "CHANGELOG.md"
    if not vf.is_file():
        return ["missing VERSION file"], None
    if not bf.is_file():
        return ["missing BASE_VERSION file"], None
    if not cf.is_file():
        return ["missing CHANGELOG.md file"], None

    ver = vf.read_text(encoding="utf-8-sig").strip()
    base_ver = bf.read_text(encoding="utf-8-sig").strip()
    m = AGY_VER_RE.fullmatch(ver)
    if not m:
        problems.append(f"VERSION `{ver}` must match `<BASE_VERSION>-agy.<N>` (e.g. `3.0.0-agy.7`)")
        return problems, {"version": ver, "base_version": base_ver, "serial": None, "history_nums": []}

    ver_base, serial = m.group(1), int(m.group(2))
    if ver_base != base_ver:
        problems.append(f"VERSION prefix `{ver_base}` differs from BASE_VERSION `{base_ver}`")

    changelog_txt = cf.read_text(encoding="utf-8-sig", errors="replace")
    top_heads = re.findall(r"^##[ \t]+([^\r\n]+)", changelog_txt, re.M)
    if not top_heads or not top_heads[0].startswith(ver):
        got_top = top_heads[0] if top_heads else "<none>"
        problems.append(f"top CHANGELOG.md heading `{got_top}` does not start with `{ver}`")

    # Collect prior `-agy.K` serials from section headings in CHANGELOG.md and plugin.json
    hist_nums = []
    for head in top_heads[1:]:
        hm = HIST_AGY_RE.search(head)
        if hm:
            hist_nums.append(int(hm.group(1)))

    if check_git_tags and (root / ".git").exists():
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), "tag", "-l", "*-agy.*"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    tm = AGY_VER_RE.fullmatch(line.strip().lstrip("v"))
                    if tm:
                        k = int(tm.group(1 + 1))
                        if tm.group(0) != ver:
                            hist_nums.append(k)
        except (OSError, subprocess.SubprocessError):
            pass

    if hist_nums:
        prev_max = max(hist_nums)
        if serial <= prev_max:
            problems.append(
                f"non-monotonic agy serial: {serial} <= historical max {prev_max} "
                "(must continue from prior -agy.N, never reset to 1)"
            )

    plug_json = HERE / "plugin" / "director-work" / "plugin.json"
    if plug_json.is_file() and root == DEFAULT_ROOT:
        try:
            import json
            pv = str(json.loads(plug_json.read_text(encoding="utf-8-sig")).get("version") or "")
            if pv != ver:
                problems.append(f"plugin.json version `{pv}` != VERSION `{ver}`")
        except (OSError, ValueError) as exc:
            problems.append(f"cannot read plugin.json version: {exc}")

    return problems, {"version": ver, "base_version": base_ver, "serial": serial, "history_nums": sorted(set(hist_nums))}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="variant checkout root")
    ap.add_argument("--no-git-tags", action="store_true", help="skip checking git tags")
    try:
        a = ap.parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 2

    probs, info = check_version(a.root, check_git_tags=not a.no_git_tags)
    if probs:
        for p in probs:
            print(f"FAIL {p}")
        print(f"AGY_VERSION: FAIL version={(info or {}).get('version', '?')} problems={len(probs)}")
        return 1
    hist = f" prev_max={max(info['history_nums'])}" if info["history_nums"] else ""
    print(f"AGY_VERSION: PASS version={info['version']} base={info['base_version']} serial={info['serial']}{hist}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
