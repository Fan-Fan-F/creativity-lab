"""Checks comparison math, blinding, resource provenance and invalid data."""
import copy
import json
from pathlib import Path
import unittest

from creativity_lab.evaluation import (build_blind_pack, make_pack, run_benchmark,
                                       summarize_votes, wilson_interval)
from creativity_lab.providers import DemoProvider, ProviderError


def idea(idea_id, quality=.6, novelty=.5):
    return {"id": idea_id, "title": "方案 " + idea_id, "description": "通过可检验的反馈机制改善任务。",
            "mechanism": "combine", "test_type": "prototype", "assumptions": ["资源可获得"],
            "risks": ["效果需要验证"], "test": "使用对照原型验证预注册指标。",
            "status": "screened", "operator": "private-operator", "parents": ["private-parent"],
            "scores": {"quality": quality, "novelty": novelty}, "novelty_proxy": novelty}


def run(mode, ideas=None, demo=False, calls=3, input_tokens=100, output_tokens=100):
    return {"schema_version": 1, "run_id": mode + "-private-run", "task": "同一个可检验的任务", "demo": demo,
            "status": "complete", "config": {"task": "同一个可检验的任务", "rounds": 1,
            "candidates_per_round": 2, "seed": 42, "max_calls": 10, "references": [], "mode": mode},
            "ideas": ideas or [idea(mode + "-one")], "budget": {"calls": calls, "max_calls": 10,
            "input_tokens": input_tokens, "output_tokens": output_tokens, "usage_complete": True},
            "provenance": {"generator": {"kind": "chat", "model": "secret-generator"},
                           "judge": {"kind": "chat", "model": "secret-judge"}}}


def human_run():
    candidate = idea("human-one")
    del candidate["scores"]
    return {"schema_version": 1, "comparison_kind": "human", "task": "同一个可检验的任务",
            "demo": False, "provenance": {"kind": "human", "collection_protocol": "实际收集，限时与工具预注册"},
            "resource_limits": {"time_minutes": 30, "tools": ["paper"]}, "ideas": [candidate]}


def vote_for_source(mapping, source="lab"):
    return [{"item_id": item["item_id"], "choice": next(side for side in ("A", "B") if item["sides"][side]["source"] == source)}
            for item in mapping["items"]]


class EvaluationTests(unittest.TestCase):
    def test_wilson_known_symmetric_case(self):
        low, high = wilson_interval(5, 10)
        self.assertAlmostEqual(low, .2365930905, places=8)
        self.assertAlmostEqual(high, .7634069095, places=8)
        self.assertIsNone(wilson_interval(0, 0))
        self.assertGreater(wilson_interval(20, 20)[0], .5)

    def test_wilson_rejects_invalid_counts(self):
        for wins, total in ((True, 3), (-1, 3), (4, 3), (1, -1), (1.0, 3)):
            with self.subTest(wins=wins, total=total), self.assertRaises(ValueError):
                wilson_interval(wins, total)
        with self.assertRaises(ValueError):
            wilson_interval(1, 2, float("nan"))

    def test_pack_removes_source_and_scores(self):
        pack, mapping = build_blind_pack(run("lab"), run("baseline"))
        rendered = json.dumps(pack)
        for private_text in ("secret-generator", "secret-judge", "private-operator", "private-parent", "private-run", "novelty_proxy", '"scores"', '"budget"'):
            self.assertNotIn(private_text, rendered)
        self.assertEqual({entry["source"] for entry in mapping["items"][0]["sides"].values()}, {"lab", "baseline"})
        self.assertEqual(set(pack["items"][0]["A"]), {"title", "description", "mechanism", "test_type", "assumptions", "risks", "test"})

    def test_selects_quality_without_novelty_and_is_deterministic(self):
        lab = run("lab", [idea("novel-but-low-quality", .2, 1), idea("high-quality", .8, 0)])
        first = build_blind_pack(lab, run("baseline"), seed=7)
        second = build_blind_pack(lab, run("baseline"), seed=7)
        self.assertEqual(first, second)
        selected = next(row for row in first[1]["items"][0]["sides"].values() if row["source"] == "lab")
        self.assertEqual(selected["idea_id"], "high-quality")

    def test_inputs_are_not_mutated(self):
        lab, baseline = run("lab"), run("baseline")
        before = copy.deepcopy((lab, baseline))
        build_blind_pack(lab, baseline)
        self.assertEqual((lab, baseline), before)

    def test_multiple_candidates_require_equal_available_count(self):
        pairs = [{"task_id": "task", "lab": run("lab", [idea("one"), idea("two")]), "baseline": run("baseline")}]
        with self.assertRaisesRegex(ValueError, "equal number"):
            make_pack(pairs, selection_count=2)

    def test_wrong_task_duplicate_ids_invalid_score_rejected(self):
        mismatched = run("baseline")
        mismatched["task"] = "其他任务"
        mismatched["config"]["task"] = "其他任务"
        invalids = [mismatched, run("baseline", [idea("dup"), idea("dup")]),
                    run("baseline", [idea("bad", float("nan"))]), run("baseline", [idea("huge", 10**1000)])]
        for baseline in invalids:
            with self.subTest(baseline=baseline), self.assertRaises(ValueError):
                build_blind_pack(run("lab"), baseline)

    def test_boolean_metadata_rejected(self):
        for field in ("schema_version", "demo"):
            baseline = run("baseline")
            baseline[field] = True if field == "schema_version" else "false"
            with self.assertRaises(ValueError):
                build_blind_pack(run("lab"), baseline)
        baseline = run("baseline")
        baseline["budget"]["calls"] = True
        with self.assertRaises(ValueError):
            build_blind_pack(run("lab"), baseline)

    def test_votes_real_win_loss_tie_and_missing(self):
        pairs = [{"task_id": f"t-{i}", "lab": run("lab"), "baseline": run("baseline")} for i in range(4)]
        pack, mapping = make_pack(pairs)
        votes = vote_for_source(mapping)[:1] + vote_for_source(mapping, "baseline")[1:2]
        votes.append({"item_id": mapping["items"][2]["item_id"], "choice": "tie"})
        result = summarize_votes(pack, mapping, votes)
        self.assertEqual((result["wins"], result["losses"], result["ties"], result["missing"]), (1, 1, 1, 1))
        self.assertEqual(result["win_fraction_decisive"], .5)
        self.assertEqual(result["superiority_claim"], "not_established")

    def test_empty_votes_not_imputed(self):
        pack, mapping = build_blind_pack(run("lab"), run("baseline"))
        result = summarize_votes(pack, mapping, [])
        self.assertEqual(result["missing"], 1)
        self.assertIsNone(result["wilson_95_decisive"])
        self.assertIsNone(result["win_fraction_decisive"])

    def test_invalid_votes_rejected(self):
        pack, mapping = build_blind_pack(run("lab"), run("baseline"))
        valid = vote_for_source(mapping)[0]
        invalids = [[valid, valid], [{"item_id": "unknown", "choice": "A"}],
                    [{"item_id": valid["item_id"], "choice": "maybe"}],
                    [{**valid, "score": 100}], [{**valid, "reason": 1}]]
        for votes in invalids:
            with self.subTest(votes=votes), self.assertRaises(ValueError):
                summarize_votes(pack, mapping, votes)

    def test_tampered_pack_or_mapping_rejected(self):
        pack, mapping = build_blind_pack(run("lab"), run("baseline"))
        altered = copy.deepcopy(pack)
        altered["items"][0]["A"]["description"] = "Changed after voting"
        with self.assertRaisesRegex(ValueError, "integrity"):
            summarize_votes(altered, mapping, [])
        altered_map = copy.deepcopy(mapping)
        altered_map["items"][0]["task_id"] = "changed"
        with self.assertRaisesRegex(ValueError, "integrity"):
            summarize_votes(pack, altered_map, [])

    def test_fixture_low_n_no_human_prevent_claims(self):
        pack, mapping = build_blind_pack(run("lab", demo=True), run("baseline", demo=True))
        result = summarize_votes(pack, mapping, vote_for_source(mapping))
        limitations = " ".join(result["limitations"])
        self.assertIn("Fixture/demo", limitations)
        self.assertIn("20 decisive", limitations)
        self.assertIn("No human-created", limitations)
        self.assertEqual(result["superiority_claim"], "not_established")

    def test_equal_caps_do_not_hide_unequal_usage(self):
        for comparator in (run("baseline", calls=2), run("baseline", output_tokens=200)):
            pack, mapping = build_blind_pack(run("lab"), comparator)
            result = summarize_votes(pack, mapping, vote_for_source(mapping))
            self.assertFalse(mapping["items"][0]["metadata"]["matching"]["resource_matched"])
            self.assertTrue(any("unequal or unverified" in issue for issue in result["limitations"]))

    def test_repeated_tasks_grouped_and_flagged(self):
        pairs = [{"task_id": "repeated", "lab": run("lab"), "baseline": run("baseline")} for _ in range(2)]
        pack, mapping = make_pack(pairs)
        result = summarize_votes(pack, mapping, vote_for_source(mapping))
        self.assertEqual(result["distinct_tasks"], 1)
        self.assertEqual(result["by_task"]["repeated"]["wins"], 2)
        self.assertTrue(any("clustered" in issue for issue in result["limitations"]))

    def test_task_aliases_cannot_inflate_independent_task_count(self):
        pairs = [{"task_id": f"different-id-{i}", "lab": run("lab"), "baseline": run("baseline")} for i in range(2)]
        pack, mapping = make_pack(pairs)
        result = summarize_votes(pack, mapping, vote_for_source(mapping))
        self.assertEqual(result["distinct_tasks"], 1)
        self.assertTrue(any("clustered" in issue for issue in result["limitations"]))
        pairs[1]["task_id"] = pairs[0]["task_id"]
        pairs[1]["lab"]["task"] = pairs[1]["lab"]["config"]["task"] = "different task"
        pairs[1]["baseline"]["task"] = pairs[1]["baseline"]["config"]["task"] = "different task"
        with self.assertRaisesRegex(ValueError, "same task text"):
            make_pack(pairs)

    def test_human_comparator_needs_no_fabricated_scores(self):
        pack, mapping = build_blind_pack(run("lab"), human_run())
        result = summarize_votes(pack, mapping, vote_for_source(mapping))
        self.assertEqual(result["by_comparator"]["human"]["wins"], 1)
        self.assertFalse(any("No human-created" in issue for issue in result["limitations"]))
        self.assertTrue(any("authorship" in issue for issue in result["limitations"]))
        self.assertEqual(mapping["items"][0]["metadata"]["human_resource_limits"]["time_minutes"], 30)

    def test_human_authorship_declaration_required(self):
        comparator = human_run()
        del comparator["provenance"]["collection_protocol"]
        with self.assertRaises(ValueError):
            build_blind_pack(run("lab"), comparator)
        comparator = human_run()
        comparator["demo"] = True
        with self.assertRaisesRegex(ValueError, "fixture"):
            build_blind_pack(run("lab"), comparator)

    def test_benchmark_fresh_factories_and_actual_budget_records(self):
        factory_calls = []
        def factory():
            factory_calls.append(True)
            return DemoProvider()
        tasks = [{"id": "t", "domain": "science", "task": "设计一个对照实验"}]
        result = run_benchmark(factory, factory, tasks, rounds=1, candidates=2, max_calls=4)
        self.assertEqual(len(factory_calls), 4)
        self.assertEqual(result["protocol"]["scheduled_calls_per_mode_upper_bound"], 3)
        self.assertEqual(result["pairs"][0]["lab"]["config"]["max_calls"], 4)
        self.assertEqual(result["pairs"][0]["baseline"]["config"]["max_calls"], 4)
        self.assertTrue(result["summary"]["demo"])
        for mode in ("lab", "baseline"):
            self.assertEqual(result["summary"]["total_usage"][mode]["calls"], result["pairs"][0][mode]["budget"]["calls"])
            self.assertFalse(result["summary"]["total_usage"][mode]["usage_complete"])

    def test_benchmark_preserves_partial_failure_and_continues(self):
        class FailingProvider(DemoProvider):
            def complete(self, payload, budget):
                budget.reserve()
                budget.usage_complete = False
                raise ProviderError("Simulated provider outage")
        providers = iter([FailingProvider(), DemoProvider()])
        result = run_benchmark(lambda: next(providers), None, [{"id": "t", "task": "实际任务"}], rounds=1, candidates=2)
        self.assertFalse(result["summary"]["complete"])
        self.assertEqual(result["pairs"][0]["lab"]["status"], "provider_error")
        self.assertEqual(result["pairs"][0]["lab"]["budget"]["calls"], 1)
        self.assertEqual(result["pairs"][0]["baseline"]["status"], "complete")
        self.assertEqual(result["summary"]["incomplete_runs"][0]["mode"], "lab")
        self.assertTrue(result["protocol"]["issues"])
        with self.assertRaises(ValueError):
            make_pack(result["pairs"])

    def test_unstructured_provider_failure_is_not_fabricated(self):
        def broken_factory():
            raise ProviderError("No provider")
        with self.assertRaises(ProviderError):
            run_benchmark(broken_factory, None, [{"id": "t", "task": "实际任务"}])

    def test_task_suite_and_invalid_task_metadata(self):
        path = Path(__file__).resolve().parents[1] / "creativity_lab" / "data" / "tasks.json"
        tasks = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(tasks["tasks"]), 12)
        self.assertEqual({task["domain"] for task in tasks["tasks"]}, {"science", "product", "writing"})
        duplicate = [{"id": "x", "task": "one"}, {"id": "x", "task": "two"}]
        with self.assertRaises(ValueError):
            run_benchmark(DemoProvider, None, duplicate)
        with self.assertRaises(ValueError):
            run_benchmark(DemoProvider, None, [{"id": "x", "task": "one"}], rounds=True)
        with self.assertRaises(ValueError):
            run_benchmark(DemoProvider, None, {"schema_version": True, "tasks": [{"id": "x", "task": "one"}]})


if __name__ == "__main__":
    unittest.main()
