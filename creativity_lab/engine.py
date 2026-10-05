"""Divergent operators + quality/diversity archive + evidence-aware screening."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import random
import re
import uuid

from .providers import BudgetExhausted, CallBudget, ProviderError

MECHANISMS = ("subtract", "transfer", "combine", "separate", "replace", "adapt")
TEST_TYPES = ("prototype", "ab_test", "simulation", "expert_review")
OPERATORS = {
    "inversion": "Invert a hidden assumption. Remove a cause or constraint rather than adding another feature.",
    "analogy": "Transfer a causal mechanism from a distant domain. Explain the source mechanism, target mapping and where the analogy breaks.",
    "recombination": "Combine two mechanisms into an interaction neither has alone. Explain why their interaction matters.",
    "contradiction": "Identify two conflicting requirements and separate them in time, space, scale or actor roles.",
    "counterfactual": "Change a scarce resource or premise radically. Derive a feasible response and say which original constraints still hold.",
    "mutation": "Use archive candidates and critique to change the causal mechanism. Address a failure mode with a falsifiable improvement.",
    "direct": "Propose a useful, original idea for the task. Include the mechanism and a concrete test.",
}
PARTIAL_ERROR = "Model provider failed or returned invalid data; partial results preserved."


@dataclass
class RunConfig:
    task: str
    rounds: int = 2
    candidates_per_round: int = 4
    seed: int = 42
    max_calls: int = 30
    references: list[str] = field(default_factory=list)
    mode: str = "lab"
    # Optional hard cap is deliberately calls, not an invented dollar estimate.

    def validate(self):
        if not isinstance(self.task, str) or not self.task.strip() or len(self.task) > 12000:
            raise ValueError("Task must contain 1–12000 characters")
        for name, low, high in (("rounds", 1, 8), ("candidates_per_round", 2, 12), ("max_calls", 1, 200)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in [{low}, {high}]")
        if type(self.seed) is not int or not 0 <= self.seed < 2**32:
            raise ValueError("seed must be a nonnegative 32-bit integer")
        if self.mode not in ("lab", "baseline"):
            raise ValueError("mode must be lab or baseline")
        if not isinstance(self.references, list) or len(self.references) > 30 or any(not isinstance(r, str) or len(r) > 8000 for r in self.references):
            raise ValueError("references must be at most 30 strings of 8000 characters")


def tokens(text):
    """English word + Chinese character-bigram lexical proxy; no semantic claim."""
    english = re.findall(r"[a-z0-9]+", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    return set(english + [span[i:i+2] for span in chinese for i in range(max(1, len(span)-1))])


def similarity(left, right):
    a, b = tokens(left), tokens(right)
    if not a and not b:
        return 1.0
    return len(a & b) / max(1, len(a | b))


def idea_text(idea):
    return idea["description"]  # Titles/IDs cannot inflate novelty.


def novelty(text, corpus):
    return 1.0 - max((similarity(text, other) for other in corpus), default=0.0)


def normalize_idea(raw):
    if not isinstance(raw, dict):
        raise ProviderError("Idea must be an object")
    result = {}
    for key in ("title", "description", "test"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 6000:
            raise ProviderError(f"Idea requires bounded nonempty {key}")
        result[key] = value.strip()
    for key in ("mechanism", "test_type"):
        options = MECHANISMS if key == "mechanism" else TEST_TYPES
        if raw.get(key) not in options:
            raise ProviderError(f"Idea {key} must be one of the declared categories")
        result[key] = raw[key]
    for key in ("assumptions", "risks"):
        values = raw.get(key)
        if not isinstance(values, list) or not 1 <= len(values) <= 8 or any(not isinstance(x, str) or not x.strip() or len(x) > 2000 for x in values):
            raise ProviderError(f"Idea needs 1–8 bounded strings in {key}")
        result[key] = values
    return result


def normalize_review(raw):
    if not isinstance(raw, dict):
        raise ProviderError("Review must be an object")
    result = {}
    for key in ("novelty", "usefulness", "feasibility", "surprise", "testability"):
        value = raw.get(key)
        if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
            raise ProviderError(f"Review {key} must be a finite number between 0 and 1")
        result[key] = float(value)
    for key in ("rationale", "next_test"):
        if not isinstance(raw.get(key), str) or not raw[key].strip() or len(raw[key]) > 6000:
            raise ProviderError(f"Review needs {key}")
    failures = raw.get("failure_modes")
    if not isinstance(failures, list) or not failures or len(failures) > 8 or any(not isinstance(x, str) or not x.strip() or len(x) > 2000 for x in failures):
        raise ProviderError("Review needs bounded failure_modes")
    result["quality"] = .45*result["usefulness"] + .35*result["feasibility"] + .20*result["testability"]
    return result, {key: raw[key] for key in ("rationale", "failure_modes", "next_test")}


class Archive:
    """Discrete mechanism × test-type cells, inspired by MAP-Elites."""
    def __init__(self):
        self.cells = {}

    def update(self, ideas):
        for idea in ideas:
            if idea["status"] != "screened":
                continue
            # A minimum relevance/feasibility floor keeps nonsensical novelty out.
            if idea["scores"]["usefulness"] < .35 or idea["scores"]["feasibility"] < .25:
                continue
            cell = idea["mechanism"] + "|" + idea["test_type"]
            current = self.cells.get(cell)
            if current is None or idea["scores"]["quality"] > current["scores"]["quality"]:
                self.cells[cell] = idea

    def parents(self, rng, count=2):
        # Uniform niche selection resists convergence to one popular winner.
        candidates = sorted(self.cells.values(), key=lambda x: x["id"])
        if not candidates or count < 1:
            return []
        selected = [rng.choice(candidates)]
        while len(selected) < min(count, len(candidates)):
            remaining = [p for p in candidates if p not in selected]
            distinct = [p for p in remaining if p["mechanism"] not in {s["mechanism"] for s in selected}]
            selected.append(rng.choice(distinct or remaining))
        return selected

    def export(self):
        return [{"cell": cell, "idea_id": idea["id"], "quality": idea["scores"]["quality"]}
                for cell, idea in sorted(self.cells.items())]


class Engine:
    def __init__(self, provider, judge=None):
        self.provider = provider
        self.judge = judge or provider

    def run(self, config):
        config.validate()
        # Each run owns its checkpoint; an Engine can be reused without sharing state.
        state = {"budget": CallBudget(config.max_calls), "rng": random.Random(config.seed),
                 "archive": Archive(), "ideas": [], "trace": [], "active": {}}
        try:
            status = self._search(config, state)
        except ProviderError:
            # Provider exception text and response bodies may contain credentials.
            # Raise a fresh, fixed message and expose only validated accumulated data.
            state["budget"].usage_complete = False
            state["trace"].append({**state["active"], "event": "provider_error",
                                   "message": PARTIAL_ERROR})
            error = ProviderError(PARTIAL_ERROR)
            error.partial_result = self._finalize(config, state, "provider_error", PARTIAL_ERROR)
            raise error from None
        return self._finalize(config, state, status)

    def _search(self, config, state):
        budget, rng, archive = state["budget"], state["rng"], state["archive"]
        ideas, trace = state["ideas"], state["trace"]
        status = "complete"
        for round_index in range(config.rounds):
            batch = []
            for slot in range(config.candidates_per_round):
                if config.mode == "baseline":
                    operator = "direct"
                    parents = []
                else:
                    routes = list(OPERATORS)[:-1]
                    operator = routes[(round_index * config.candidates_per_round + slot) % len(routes)]
                    parents = archive.parents(rng) if round_index else []
                    if operator == "mutation" and not parents:
                        operator = "counterfactual"
                seed = rng.randrange(2**32)
                payload = {"purpose": "generate", "task": config.task, "operator": operator,
                           "instruction": OPERATORS[operator], "seed": seed,
                           "references": config.references,
                           "parents": [{"id": p["id"], "idea": idea_text(p), "critique": p.get("review", {})} for p in parents],
                           "avoid": [idea_text(i) for i in ideas[-12:]] if config.mode == "lab" else [],
                           "schema": {"ideas": [{"title": "string", "mechanism": list(MECHANISMS), "test_type": list(TEST_TYPES),
                               "description": "causal mechanism, task constraints, source analogy if used", "assumptions": ["string"],
                               "risks": ["string"], "test": "falsifiable prediction, control, measurement and success criterion"}]},
                           "count": 1}
                state["active"] = {"round": round_index+1, "phase": "generate",
                                   "operator": operator, "seed": seed}
                try:
                    response = self.provider.complete(payload, budget)
                    if not isinstance(response, dict):
                        raise ProviderError("Generator response must be an object")
                    raw = response.get("ideas")
                    if not isinstance(raw, list) or len(raw) != 1:
                        raise ProviderError("Generator must return exactly one idea")
                    item = normalize_idea(raw[0])
                except BudgetExhausted:
                    status = "budget_exhausted"
                    break
                if any(similarity(idea_text(item), idea_text(i)) >= .92 for i in ideas):
                    trace.append({"round": round_index+1, "operator": operator, "event": "duplicate_rejected"})
                    continue
                item.update(id=f"idea-{len(ideas)+1:03d}", round=round_index+1,
                            operator=operator, parents=[p["id"] for p in parents], status="proposed", scores={},
                            novelty_proxy=novelty(idea_text(item), config.references + [idea_text(i) for i in ideas]))
                ideas.append(item)
                batch.append(item)
                trace.append({"round": round_index+1, "operator": operator, "event": "generated", "idea_id": item["id"], "seed": seed})
            if batch:
                # Hide operator, lineage, archive position and novelty proxy from judge.
                shuffled = list(batch)
                rng.shuffle(shuffled)
                aliases = {f"candidate-{j+1}": item for j, item in enumerate(shuffled)}
                rows = [{"id": alias, **{k: item[k] for k in ("title", "description", "mechanism", "test_type", "assumptions", "risks", "test")}}
                        for alias, item in aliases.items()]
                payload = {"purpose": "review", "task": config.task, "references": config.references, "candidates": rows,
                           "instruction": "Evaluate task-specific usefulness, feasibility, testability, novelty relative to provided references, and surprise independently. Penalize unsupported claims and infeasible mechanisms. References are not exhaustive prior art. Scores 0..1. Provide one review for every supplied id; no experimental success can be inferred from text.",
                           "schema": {"reviews": [{"id": "supplied candidate ID", "novelty": "0..1", "usefulness": "0..1", "feasibility": "0..1", "surprise": "0..1", "testability": "0..1", "rationale": "string", "failure_modes": ["string"], "next_test": "string"}]}}
                state["active"] = {"round": round_index+1, "phase": "review"}
                try:
                    response = self.judge.complete(payload, budget)
                    if not isinstance(response, dict):
                        raise ProviderError("Judge response must be an object")
                    reviews = response.get("reviews")
                    if not isinstance(reviews, list) or len(reviews) != len(aliases):
                        raise ProviderError("Judge must review all candidates exactly once")
                    ids = [r.get("id") if isinstance(r, dict) else None for r in reviews]
                    if any(not isinstance(alias, str) for alias in ids):
                        raise ProviderError("Judge IDs must be strings")
                    if len(set(ids)) != len(aliases) or set(ids) != set(aliases):
                        raise ProviderError("Judge returned missing, duplicate or unknown IDs")
                    normalized = [(r["id"], *normalize_review(r)) for r in reviews]
                    for alias, scores, review in normalized:
                        aliases[alias].update(scores=scores, review=review, status="screened")
                    archive.update(batch)
                    trace.append({"round": round_index+1, "event": "screened", "count": len(batch), "occupied_cells": len(archive.cells)})
                except BudgetExhausted:
                    status = "budget_exhausted"
            if status != "complete":
                break
        return status

    def _finalize(self, config, state, status, error=None):
        budget, archive = state["budget"], state["archive"]
        ideas, trace = state["ideas"], state["trace"]
        # Final corpus-relative proxy is comparable across candidate ordering.
        for item in ideas:
            item["novelty_proxy"] = novelty(idea_text(item), config.references + [idea_text(i) for i in ideas if i is not item])
        result = {"schema_version": 1, "run_id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
                "task": config.task, "demo": bool(self.provider.demo or self.judge.demo), "status": status,
                "config": asdict(config), "budget": budget.as_dict(), "ideas": ideas,
                "archive": archive.export(), "trace": trace,
                "provenance": {"generator": self.provider.identity, "judge": self.judge.identity,
                               "different_judge_model": self.provider.identity.get("model") != self.judge.identity.get("model"),
                               "different_judge_configuration": self.provider.identity != self.judge.identity,
                               "seed_note": "controls scheduling and alias order; live model sampling is not deterministic",
                               "references_sha256": hashlib.sha256(json.dumps(config.references, ensure_ascii=False).encode()).hexdigest(),
                               "novelty_method": "lexical Jaccard proxy; no semantic or global prior-art claim"},
                "claim": "Fixture run" if self.provider.demo else "Text proposals and model screening; real-world effectiveness and human superiority untested"}
        if error:
            result["error"] = error
        return result
