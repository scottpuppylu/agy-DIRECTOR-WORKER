"""Parallel dual-lens specification review workflow for Antigravity (`run_workflow`).

Launches two read-only `dw-blind-reviewer` agents (Contract correctness vs Failure/Boundary cases)
for an order / architecture change under `DIRECTOR/orders/` before dispatch.
"""


async def run():
    target = args["target"]
    brief = args.get("brief", "DIRECTOR/BRIEF.md")
    lenses = [
        {
            "Role": "Spec Contract Reviewer",
            "Prompt": (
                f"Review `{target}` against `{brief}` strictly for requirement coverage, AC measurability, "
                "SCOPE/FORBIDDEN completeness, TESTS_LOCKED accuracy, and TRACES_TO correctness. Read-only; report findings clearly."
            ),
        },
        {
            "Role": "Spec Edge Reviewer",
            "Prompt": (
                f"Review `{target}` against `{brief}` strictly for edge cases, security/privacy boundaries, "
                "Windows/POSIX path hazards, error fail-closed paths, and rollback safety. Read-only; report findings clearly."
            ),
        },
    ]
    results = await parallel(*[
        agent(prompt=item["Prompt"], type="dw-blind-reviewer", role=item["Role"])
        for item in lenses
    ])
    return {"target": target, "brief": brief, "reviews": results}
