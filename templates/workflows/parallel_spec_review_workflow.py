"""Antigravity run_workflow template: parallel multi-angle SPEC_REVIEW for Strict work orders.

Invoke via Antigravity `run_workflow` tool with:
  Args = {
    "order": "<abs path to draft WO-xxx.md>",
    "repo": "<abs path to project repo>"
  }
"""
import json

order = args["order"]
repo = args["repo"]

SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "angle": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["P0", "P1", "P2"]},
                    "location": {"type": "string"},
                    "problem": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["severity", "location", "problem", "fix"],
            },
        },
        "ready_to_dispatch": {"type": "boolean"},
    },
    "required": ["angle", "issues", "ready_to_dispatch"],
}

ANGLES = [
    ("Boundary & Failure Modes", "Check Section E boundary conditions, invalid/empty inputs, crash safety, and hidden side effects."),
    ("Scope & File Isolation", "Verify SCOPE vs FORBIDDEN paths, Single Writer isolation across active worktrees/Swarm lines, and locked tests."),
    ("AC Testability & Completeness", "Verify every requirement has a verifiable AUTO/REVIEW/PO criterion and matches the project brief."),
]

phase("Parallel Spec Review")
reviews = await parallel(
    ANGLES,
    lambda item: agent(
        prompt=(
            f"Perform a read-only specification review of `{order}` in repo `{repo}` from the angle: {item[0]}.\n"
            f"Focus: {item[1]}\n"
            f"Cite exact sections or code paths. Classify issues strictly as P0 / P1 / P2."
        ),
        schema=SPEC_SCHEMA,
        role=f"Spec Review: {item[0]}",
        type_name="dw-blind-reviewer",
        workspace="inherit",
    ),
)

for r in reviews:
    log(f"[{r['angle']}] ready={r['ready_to_dispatch']} issues={len(r['issues'])}")
log(json.dumps(reviews, indent=2, ensure_ascii=False))
reviews
