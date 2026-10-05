"""Budget-transparent baselines and blinded, externally supplied human votes.

Screening scores select proposals; they never stand in for human observations.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
import random
from typing import Callable

from .engine import Engine, MECHANISMS, TEST_TYPES, RunConfig, normalize_idea
from .providers import ProviderError

IDEA_FIELDS = ("title", "description", "mechanism", "test_type", "assumptions", "risks", "test")
SELECTION_RULE = "Model runs: highest screening quality (usefulness/feasibility/testability), ties by submission order; human submissions: precommitted submission order. No novelty score in selection."


def _integer(value, name, low=0, high=None):
    if type(value) is not int or value < low or (high is not None and value > high):
        raise ValueError(f"{name} must be an integer in the allowed range")
    return value


def _text(value, name, max_length=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"{name} must be a bounded nonempty string")
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _task_key(task):
    return _hash(" ".join(task.split()))


def _tasks(tasks):
    if isinstance(tasks, dict):
        if type(tasks.get("schema_version", 1)) is not int or tasks.get("schema_version", 1) != 1:
            raise ValueError("Unsupported task schema_version")
        tasks = tasks.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 100:
        raise ValueError("tasks must contain 1–100 task objects")
    result, ids = [], set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("Each task must be an object")
        task_id = _text(task.get("id"), "Task id", 100)
        if task_id in ids:
            raise ValueError("Task ids must be unique")
        ids.add(task_id)
        references = task.get("references", [])
        config = RunConfig(task=_text(task.get("task"), "Task"), references=references)
        config.validate()
        result.append({"id": task_id, "domain": _text(task.get("domain", "unspecified"), "Domain", 100),
                       "task": config.task, "references": list(references)})
    return result


def _run_metrics(run):
    screened = [idea for idea in run["ideas"] if idea["status"] == "screened"]
    quality = [idea["scores"]["quality"] for idea in screened]
    return {"proposals": len(run["ideas"]), "screened": len(screened),
            "mean_screening_quality": sum(quality) / len(quality) if quality else None,
            "occupied_cells": len(run["archive"]), "possible_cells": len(MECHANISMS) * len(TEST_TYPES),
            "archive_coverage": len(run["archive"]) / (len(MECHANISMS) * len(TEST_TYPES)),
            "duplicates_rejected": sum(event["event"] == "duplicate_rejected" for event in run["trace"]),
            "budget": dict(run["budget"]), "status": run["status"]}


def run_benchmark(provider_factory: Callable, judge_factory: Callable | None, tasks,
                  rounds=2, candidates=4, max_calls=30, seed=42):
    """Run lab and best-of-N baseline with identical call caps and task inputs.

    Factories take no arguments and create fresh instances per mode. The cap is
    per task/mode, includes generation and review, and does not match tokens.
    """
    if not callable(provider_factory) or (judge_factory is not None and not callable(judge_factory)):
        raise ValueError("Provider/judge factories must be callable")
    checked = _tasks(tasks)
    RunConfig(task=checked[0]["task"], rounds=rounds, candidates_per_round=candidates,
              max_calls=max_calls, seed=seed).validate()
    pairs, metrics, incomplete = [], [], []
    for index, task in enumerate(checked):
        task_seed = (seed + index) % 2**32
        pair = {"task_id": task["id"], "domain": task["domain"], "seed": task_seed}
        row = {"task_id": task["id"]}
        for mode, key in (("lab", "lab"), ("baseline", "baseline")):
            provider = provider_factory()
            judge = judge_factory() if judge_factory is not None else None
            config = RunConfig(task=task["task"], rounds=rounds, candidates_per_round=candidates,
                               max_calls=max_calls, seed=task_seed, mode=mode, references=task["references"])
            try:
                pair[key] = Engine(provider, judge).run(config)
            except ProviderError as exc:
                partial = getattr(exc, "partial_result", None)
                if not isinstance(partial, dict):
                    raise
                try:
                    _validate_run(partial, mode, allow_partial=True)
                    if not isinstance(partial.get("archive"), list) or not isinstance(partial.get("trace"), list):
                        raise ValueError("Partial run lacks archive/trace")
                except (TypeError, ValueError):
                    raise exc
                pair[key] = partial
            row[key] = _run_metrics(pair[key])
            if pair[key]["status"] != "complete":
                incomplete.append({"task_id": task["id"], "mode": mode, "status": pair[key]["status"]})
        pairs.append(pair)
        metrics.append(row)
    totals = {}
    for mode in ("lab", "baseline"):
        budgets = [row[mode]["budget"] for row in metrics]
        totals[mode] = {key: sum(budget[key] for budget in budgets)
                        for key in ("calls", "input_tokens", "output_tokens")}
        totals[mode]["usage_complete"] = all(budget["usage_complete"] for budget in budgets)
    return {"schema_version": 1, "kind": "creativity_benchmark", "pairs": pairs,
            "summary": {"tasks": len(checked), "complete": not incomplete, "incomplete_runs": incomplete,
                        "demo": any(pair[key]["demo"] for pair in pairs for key in ("lab", "baseline")),
                        "per_task": metrics, "total_usage": totals,
                        "claim": "Exploratory model screening only; no independent human improvement or superiority evidence."},
            "protocol": {"baseline": "Independent direct proposals with no archive feedback; same screening and duplicate policy.",
                         "rounds": rounds, "candidates_per_round": candidates, "call_cap_per_task_mode": max_calls,
                         "scheduled_calls_per_mode_upper_bound": rounds * (candidates + 1),
                         "seed": seed, "task_suite_sha256": _hash(checked),
                         "issues": ["At least one run was incomplete; provider errors and exhausted budgets are not completed experiments."] if incomplete else [],
                         "resource_matching": "Equal task, references, scheduling settings and call caps. Actual calls and tokens are reported; tokens, time and dollars are not matched.",
                         "limits": ["Duplicate rejection and exhausted budgets can reduce actual calls or screened candidates.",
                                    "LLM screening and lexical novelty are exploratory proxies.",
                                    "A task-specific blinded human assessment is required for improvement claims."]}}


def _validate_run(run, label, allow_partial=False):
    if not isinstance(run, dict) or type(run.get("schema_version")) is not int or run["schema_version"] != 1:
        raise ValueError("Each comparison source must be a schema_version 1 run")
    _text(run.get("task"), "Run task")
    if type(run.get("demo")) is not bool:
        raise ValueError("Each run must explicitly declare demo true/false")
    if not isinstance(run.get("provenance"), dict):
        raise ValueError("Each run requires provenance")
    ideas = run.get("ideas")
    minimum = 0 if allow_partial else 1
    if not isinstance(ideas, list) or not minimum <= len(ideas) <= 1000:
        raise ValueError("Each run requires bounded proposals; blind comparison sources must be nonempty")
    ids = set()
    for idea in ideas:
        if not isinstance(idea, dict):
            raise ValueError("Proposal must be an object")
        idea_id = _text(idea.get("id"), "Proposal id", 200)
        if idea_id in ids:
            raise ValueError("Proposal ids must be unique within a run")
        ids.add(idea_id)
        try:
            normalize_idea(idea)
        except ProviderError as exc:
            raise ValueError(str(exc)) from exc
    if label == "human":
        if run.get("comparison_kind") != "human" or run["provenance"].get("kind") != "human":
            raise ValueError("Human comparators must explicitly declare human provenance")
        _text(run["provenance"].get("collection_protocol"), "Human collection protocol", 6000)
        if run["demo"]:
            raise ValueError("A fixture cannot be represented as a human comparator")
        return
    config = run.get("config")
    if not isinstance(config, dict) or config.get("mode") != label:
        raise ValueError(f"Expected config mode {label}")
    try:
        RunConfig(**config).validate()
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid run configuration") from exc
    if config["task"] != run["task"]:
        raise ValueError("Run/config task mismatch")
    budget = run.get("budget")
    if not isinstance(budget, dict) or type(budget.get("usage_complete")) is not bool:
        raise ValueError("Model run requires explicit usage metadata")
    _integer(budget.get("max_calls"), "max_calls", 1, 200)
    _integer(budget.get("calls"), "calls", 0, budget["max_calls"])
    for key in ("input_tokens", "output_tokens"):
        _integer(budget.get(key), key)
    if budget["max_calls"] != config["max_calls"]:
        raise ValueError("Budget cap differs from run config")
    for key in ("generator", "judge"):
        identity = run["provenance"].get(key)
        if not isinstance(identity, dict) or not identity:
            raise ValueError("Model runs require generator and judge identities")
    statuses = ("complete", "budget_exhausted", "provider_error") if allow_partial else ("complete", "budget_exhausted")
    if run.get("status") not in statuses:
        raise ValueError("Unrecognized run status")


def _selected(run, label, count):
    if label == "human":
        selected = run["ideas"][:count]
    else:
        scored = []
        for index, idea in enumerate(run["ideas"]):
            if idea.get("status") != "screened":
                continue
            scores = idea.get("scores")
            quality = scores.get("quality") if isinstance(scores, dict) else None
            if type(quality) not in (int, float) or not 0 <= quality <= 1 or not math.isfinite(quality):
                raise ValueError("Screened proposals require finite quality scores in [0, 1]")
            scored.append((-float(quality), index, idea))
        selected = [idea for _, _, idea in sorted(scored, key=lambda row: (row[0], row[1]))[:count]]
    if len(selected) != count:
        raise ValueError("Both sources must contain the requested equal number of eligible proposals")
    return selected


def _pair_metadata(lab, comparator, label):
    meta = {"demo": lab["demo"] or comparator["demo"], "comparator": label,
            "lab_provenance": lab["provenance"], "comparator_provenance": comparator["provenance"],
            "lab_budget": lab.get("budget"), "comparator_budget": comparator.get("budget"),
            "lab_status": lab.get("status"), "comparator_status": comparator.get("status"),
            "human_resource_limits": comparator.get("resource_limits") if label == "human" else None}
    if label == "human":
        meta["matching"] = {"same_task": True, "resource_matched": False,
                            "note": "Human source is externally supplied; time/tools/candidate selection need a precommitted protocol."}
    else:
        keys = ("rounds", "candidates_per_round", "max_calls", "seed", "references")
        same_config = all(lab["config"][key] == comparator["config"][key] for key in keys)
        same_generator = lab["provenance"]["generator"] == comparator["provenance"]["generator"]
        same_judge = lab["provenance"]["judge"] == comparator["provenance"]["judge"]
        same_calls = lab["budget"]["calls"] == comparator["budget"]["calls"]
        complete_usage = lab["budget"]["usage_complete"] and comparator["budget"]["usage_complete"]
        same_tokens = complete_usage and all(lab["budget"][key] == comparator["budget"][key]
                                             for key in ("input_tokens", "output_tokens"))
        meta["matching"] = {"same_task": True, "same_config_ceiling": same_config,
                            "same_generator": same_generator, "same_judge": same_judge,
                            "same_actual_calls": same_calls, "token_counts_equal": same_tokens,
                            "token_usage_complete": complete_usage,
                            "resource_matched": same_config and same_generator and same_judge and same_calls and same_tokens,
                            "note": "Equal call caps alone do not establish equal token, wall-time or dollar cost."}
    return meta


def make_pack(pairs, seed=42, selection_count=1):
    """Return (blinded reviewer JSON, private mapping JSON).

    pairs: [{'task_id': str, 'lab': run, 'baseline': run,
             'comparator_label': 'baseline' | 'human'}]. Repeated tasks are kept
    for transparency and later flagged as clustered observations.
    """
    _integer(seed, "seed", 0, 2**32 - 1)
    _integer(selection_count, "selection_count", 1, 20)
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 1000:
        raise ValueError("pairs must contain 1–1000 comparison objects")
    rng = random.Random(seed)
    public_items, private_items, task_aliases = [], [], {}
    for pair_index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise ValueError("Each pair must be an object")
        task_id = _text(pair.get("task_id", f"task-{pair_index+1:03d}"), "Task id", 100)
        lab, comparator = pair.get("lab"), pair.get("baseline")
        label = pair.get("comparator_label", "human" if isinstance(comparator, dict) and comparator.get("comparison_kind") == "human" else "baseline")
        if label not in ("baseline", "human"):
            raise ValueError("Comparator label must be baseline or human")
        _validate_run(lab, "lab")
        _validate_run(comparator, label)
        if lab["task"] != comparator["task"]:
            raise ValueError("A blind comparison must use exactly the same task")
        task_key = _task_key(lab["task"])
        if task_id in task_aliases and task_aliases[task_id] != task_key:
            raise ValueError("A repeated task id must refer to the same task text")
        task_aliases[task_id] = task_key
        selected_lab = _selected(lab, "lab", selection_count)
        selected_comparator = _selected(comparator, label, selection_count)
        meta = _pair_metadata(lab, comparator, label)
        for rank, (left, right) in enumerate(zip(selected_lab, selected_comparator), 1):
            sources = [("lab", left), (label, right)]
            rng.shuffle(sources)
            item_id = f"item-{len(public_items)+1:04d}"
            item = {"item_id": item_id, "task_id": task_id, "task": lab["task"],
                    **{side: normalize_idea(idea) for side, (_, idea) in zip(("A", "B"), sources)}}
            public_items.append(item)
            private_items.append({"item_id": item_id, "task_id": task_id, "task_sha256": task_key, "pair_index": pair_index,
                                  "selection_rank": rank, "public_item_sha256": _hash(item),
                                  "sides": {side: {"source": source, "idea_id": idea["id"]}
                                            for side, (source, idea) in zip(("A", "B"), sources)},
                                  "metadata": meta})
    rng.shuffle(public_items)
    pack_id = _hash(public_items)[:24]
    pack = {"schema_version": 1, "kind": "blinded_creativity_review", "pack_id": pack_id,
            "demo": any(item["metadata"]["demo"] for item in private_items),
            "selection_rule": SELECTION_RULE, "selection_count_per_source": selection_count,
            "instructions": "Choose A, B or tie for each task using novelty relative to your knowledge, usefulness, feasibility and testability. Judge the mechanism, not length or polish. Do not search for the source or infer experimental success from proposal text.",
            "vote_schema": {"item_id": "supplied item ID", "choice": "A | B | tie", "reason": "optional rationale"},
            "items": public_items}
    mapping = {"schema_version": 1, "kind": "private_blind_mapping", "pack_id": pack_id,
               "public_items_sha256": _hash(public_items), "seed": seed, "items": private_items,
               "warning": "Keep this file from reviewers until votes and analysis rules are frozen."}
    return pack, mapping


def build_blind_pack(lab_run, baseline_run, seed=42):
    """Single-task convenience wrapper; supports explicitly labeled human runs."""
    return make_pack([{"task_id": "task-001", "lab": lab_run, "baseline": baseline_run}], seed=seed)


def wilson_interval(wins, decisive, z=1.959963984540054):
    """Two-sided 95% Wilson interval by default; no interval for zero decisions."""
    _integer(decisive, "decisive")
    _integer(wins, "wins", 0, decisive)
    if type(z) not in (int, float) or not 0 < z < 100 or not math.isfinite(z):
        raise ValueError("z must be finite and in (0, 100)")
    if decisive == 0:
        return None
    p, z2 = wins / decisive, z * z
    divisor = 1 + z2 / decisive
    center = (p + z2 / (2 * decisive)) / divisor
    half = z * math.sqrt(p * (1-p) / decisive + z2 / (4 * decisive**2)) / divisor
    return [max(0.0, center-half), min(1.0, center+half)]


def _counts(outcomes):
    counts = Counter(outcomes)
    wins, losses, ties = counts["win"], counts["loss"], counts["tie"]
    decisive = wins + losses
    return {"wins": wins, "ties": ties, "losses": losses, "decisive": decisive,
            "win_fraction_decisive": wins / decisive if decisive else None,
            "wilson_95_decisive": wilson_interval(wins, decisive)}


def summarize_votes(pack, mapping, votes):
    """Analyze externally supplied votes. Never generate or impute human votes.

    One adjudicated vote per item is allowed. Missing votes stay missing. Wilson
    intervals treat decisive items as independent, so clustering is flagged.
    """
    for obj, kind in ((pack, "blinded_creativity_review"), (mapping, "private_blind_mapping")):
        if not isinstance(obj, dict) or type(obj.get("schema_version")) is not int or obj["schema_version"] != 1 or obj.get("kind") != kind:
            raise ValueError("Unsupported pack or mapping schema")
        if not isinstance(obj.get("items"), list) or not obj["items"]:
            raise ValueError("Pack/mapping items must be nonempty lists")
    if pack.get("pack_id") != mapping.get("pack_id") or mapping.get("public_items_sha256") != _hash(pack["items"]):
        raise ValueError("Pack/mapping integrity mismatch")
    public = {}
    private = {}
    for collection, target in ((pack["items"], public), (mapping["items"], private)):
        for item in collection:
            if not isinstance(item, dict):
                raise ValueError("Pack item must be an object")
            item_id = _text(item.get("item_id"), "Item id", 100)
            if item_id in target:
                raise ValueError("Pack/mapping item ids must be unique")
            target[item_id] = item
    if set(public) != set(private):
        raise ValueError("Mapping must contain exactly every public item")
    for item_id, item in private.items():
        sides = item.get("sides")
        if item.get("public_item_sha256") != _hash(public[item_id]) or item.get("task_id") != public[item_id].get("task_id"):
            raise ValueError("Item/mapping integrity mismatch")
        if item.get("task_sha256") != _task_key(_text(public[item_id].get("task"), "Task")):
            raise ValueError("Task fingerprint integrity mismatch")
        if not isinstance(sides, dict) or set(sides) != {"A", "B"}:
            raise ValueError("Mapping must define exactly sides A and B")
        labels = []
        for side in ("A", "B"):
            if not isinstance(sides[side], dict) or sides[side].get("source") not in ("lab", "baseline", "human"):
                raise ValueError("Invalid mapped source")
            labels.append(sides[side]["source"])
        if labels.count("lab") != 1 or not isinstance(item.get("metadata"), dict):
            raise ValueError("Each comparison must have one lab and one comparator")
        meta = item["metadata"]
        if type(meta.get("demo")) is not bool or not isinstance(meta.get("matching"), dict):
            raise ValueError("Private mapping requires demo and matching provenance")
        comparator = next(label for label in labels if label != "lab")
        if meta.get("comparator") != comparator:
            raise ValueError("Comparator metadata mismatch")
    if not isinstance(votes, list) or len(votes) > len(public):
        raise ValueError("votes must be a list no longer than the pack")
    seen, outcomes, grouped, kinds = set(), [], defaultdict(list), defaultdict(list)
    for vote in votes:
        if not isinstance(vote, dict) or set(vote) - {"item_id", "choice", "reason"}:
            raise ValueError("Each vote must use item_id, choice and optional reason")
        item_id = vote.get("item_id")
        if not isinstance(item_id, str) or item_id not in public:
            raise ValueError("Vote references an unknown item")
        if item_id in seen:
            raise ValueError("Duplicate votes are not allowed")
        seen.add(item_id)
        choice = vote.get("choice")
        if choice not in ("A", "B", "tie"):
            raise ValueError("Vote choice must be A, B or tie")
        if "reason" in vote and (not isinstance(vote["reason"], str) or len(vote["reason"]) > 6000):
            raise ValueError("Vote reason must be a bounded string")
        item = private[item_id]
        outcome = "tie" if choice == "tie" else "win" if item["sides"][choice]["source"] == "lab" else "loss"
        outcomes.append(outcome)
        grouped[item["task_id"]].append(outcome)
        kinds[item["metadata"]["comparator"]].append(outcome)
    counts = _counts(outcomes)
    issues = []
    if any(item["metadata"]["demo"] for item in private.values()):
        issues.append("Fixture/demo proposals cannot establish creative improvement.")
    if len(votes) < len(public):
        issues.append("Incomplete voting; missing items were not imputed.")
    if counts["decisive"] < 20:
        issues.append("Fewer than 20 decisive comparisons; too little evidence for a superiority claim.")
    task_counts = Counter(item["task_sha256"] for item in private.values())
    if any(count > 1 for count in task_counts.values()):
        issues.append("Repeated candidates/seeds from a task are clustered, not independent human creativity samples; pooled Wilson interval is descriptive only.")
    if len(task_counts) < 12:
        issues.append("Fewer than 12 distinct tasks; this does not establish broad cross-task generalization.")
    if any(not item["metadata"]["matching"].get("resource_matched", False)
           for item in private.values() if item["metadata"]["comparator"] == "baseline"):
        issues.append("Model comparisons have unequal or unverified actual resource usage; equal call caps are not token matching.")
    if any(item["metadata"]["lab_status"] != "complete" or
           (item["metadata"]["comparator"] == "baseline" and item["metadata"]["comparator_status"] != "complete")
           for item in private.values()):
        issues.append("Incomplete runs were compared; their shortlist is not a completed search under the declared protocol.")
    human_items = [item for item in private.values() if item["metadata"]["comparator"] == "human"]
    if not human_items:
        issues.append("No human-created comparator was supplied; human superiority cannot be assessed.")
    else:
        issues.append("Externally supplied human proposals require verified authorship, time/tool limits and participant sampling; JSON declarations alone do not verify them.")
    if counts["wilson_95_decisive"] is None or counts["wilson_95_decisive"][0] <= .5:
        issues.append("The descriptive decisive-vote interval does not establish a lab preference above one half.")
    return {"schema_version": 1, "kind": "blind_vote_summary", "pack_id": pack["pack_id"],
            "items": len(public), "voted": len(votes), "missing": len(public)-len(votes),
            **counts, "by_task": {key: _counts(value) for key, value in sorted(grouped.items())},
            "by_comparator": {key: _counts(value) for key, value in sorted(kinds.items())},
            "distinct_tasks": len(task_counts), "limitations": issues,
            "superiority_claim": "not_established",
            "conclusion": "Observed supplied-reviewer preferences only. This summary does not certify superhuman creativity or real-world effectiveness.",
            "interval_note": "Wilson 95% interval excludes ties and assumes independent decisive comparisons; task-level repeated observations need a clustered analysis."}
