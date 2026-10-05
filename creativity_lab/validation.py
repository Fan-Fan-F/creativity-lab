"""Import external measurements without executing model-generated code."""
import copy
import hashlib
import json
import math


def subject_hash(idea):
    fields = {key: idea[key] for key in ("title", "description", "mechanism", "test")}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def attach_evidence(run, report):
    """Results remain externally reported, not cryptographically certified truth."""
    if not isinstance(report, dict) or report.get("run_id") != run.get("run_id"):
        raise ValueError("Evidence run_id must match the run")
    rows = report.get("results")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Evidence requires a nonempty results list")
    result = copy.deepcopy(run)
    ideas = {idea["id"]: idea for idea in result["ideas"]}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("idea_id"), str):
            raise ValueError("Invalid evidence row")
        idea_id = row["idea_id"]
        if idea_id not in ideas or idea_id in seen:
            raise ValueError("Unknown or duplicate evidence idea_id")
        seen.add(idea_id)
        if row.get("subject_sha256") != subject_hash(ideas[idea_id]):
            raise ValueError("Evidence must refer to the exact current idea")
        for key in ("evaluator", "evaluator_version", "metric", "artifact", "protocol"):
            if not isinstance(row.get(key), str) or not row[key].strip() or len(row[key]) > 8000:
                raise ValueError(f"Evidence requires {key}")
        for key in ("candidate_value", "control_value"):
            value = row.get(key)
            if type(value) not in (float, int):
                raise ValueError("Measurements must be finite numbers")
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError("Measurements must be finite numbers")
        if row.get("direction") not in ("higher", "lower"):
            raise ValueError("direction must be higher or lower")
        if type(row.get("constraints_passed")) is not bool:
            raise ValueError("constraints_passed must be boolean")
        delta = row["candidate_value"] - row["control_value"]
        improved = delta > 0 if row["direction"] == "higher" else delta < 0
        evidence = copy.deepcopy(row)
        evidence.update(status="reported_pass" if improved and row["constraints_passed"] else "reported_fail",
                        delta=delta, provenance="external report; content and artifact not independently verified")
        ideas[idea_id].setdefault("evidence", []).append(evidence)
    return result


def evidence_template(run):
    return {"run_id": run["run_id"], "results": [
        {"idea_id": idea["id"], "subject_sha256": subject_hash(idea), "evaluator": "replace-with-trusted-evaluator",
         "evaluator_version": "replace-with-version", "metric": "replace-with-preregistered-metric",
         "candidate_value": None, "control_value": None, "direction": "higher", "constraints_passed": False,
         "artifact": "replace-with-result-file-or-URL", "protocol": "replace-with-frozen-test-protocol"}
        for idea in run["ideas"]]}
