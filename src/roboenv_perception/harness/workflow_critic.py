"""Normalize existing review results; never treat generated images as observations."""


def finding(stage, status, report, *, report_ref, candidate, observation_refs):
    if stage == "structure_audit":
        issues = report.get("issues", [])
        scope = "structure" if any(r["repair_target"] == "blueprint" for r in issues) else "unsupported"
    elif stage == "geometry_audit":
        issues = report.get("feedback", [])
        targets = {row.get("repair_target") for row in issues}
        if not issues or "verifier" in targets:
            scope = "unsupported"
        elif status == "failed" and targets == {"geometry"}:
            scope = "geometry"
        else:
            scope = "structure"
    else:
        field = {"blueprint": "issues", "blueprint_review": "findings"}.get(stage, "issues")
        issues = report.get(field, [])
        scope = "geometry" if stage == "build_hypothesis_audit" else "structure_or_evidence" if stage in {
            "blueprint", "blueprint_review", "geometry_review",
        } else "structure" if stage == "assembly_audit" else "physical_diagnosis"
    return {
        "stage": stage, "status": status, "report_ref": report_ref,
        "candidate": candidate, "observation_refs": observation_refs,
        "issues": issues[:16], "suggested_scope": scope,
        "candidate_role": "diagnostic_only",
    }


def feedback(entry):
    rows = entry["issues"]
    return [
        (str(row.get("message") or row.get("detail") or row) if isinstance(row, dict) else str(row))[:800]
        for row in rows
    ] or [f'{entry["stage"]}: {entry["status"]}; inspect the bound report.']
