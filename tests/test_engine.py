"""Behavioral checks for search diversity, provenance and judge boundaries."""
from copy import deepcopy
import unittest

from creativity_lab.engine import (Archive, Engine, MECHANISMS, RunConfig, TEST_TYPES,
                                  normalize_idea, normalize_review, similarity)
from creativity_lab.providers import DemoProvider, ProviderError


def idea(index=0, description=None):
    return {"title": f"Candidate {index}",
            "description": description or f"cause{index} action{index} outcome{index}",
            "mechanism": MECHANISMS[index % len(MECHANISMS)],
            "test_type": TEST_TYPES[index % len(TEST_TYPES)],
            "assumptions": ["The causal premise needs testing."],
            "risks": ["The intervention may not meet the goal."],
            "test": "Compare the intervention with a control using a predefined outcome."}


def review(alias, **changes):
    row = {"id": alias, "novelty": .7, "usefulness": .8, "feasibility": .7,
           "surprise": .6, "testability": .9,
           "rationale": "A plausible proposal requiring an experiment.",
           "failure_modes": ["The expected causal effect may be absent."],
           "next_test": "Run a controlled prototype and measure the specified outcome."}
    row.update(changes)
    return row


class RecordingProvider:
    demo = False
    identity = {"kind": "test", "model": "scripted-boundary-provider"}

    def __init__(self, same_description=False, bad_reviews=None, bad_response=None):
        self.requests = []
        self.next_index = 0
        self.same_description = same_description
        self.bad_reviews = bad_reviews
        self.bad_response = bad_response

    def complete(self, payload, budget):
        budget.reserve()
        budget.record({"prompt_tokens": 3, "completion_tokens": 4})
        self.requests.append(deepcopy(payload))
        if payload["purpose"] == "generate":
            if self.bad_response is not None:
                return self.bad_response
            item = idea(self.next_index, "one mechanism with one unchanged causal effect"
                        if self.same_description else None)
            self.next_index += 1
            return {"ideas": [item]}
        rows = [review(candidate["id"]) for candidate in payload["candidates"]]
        if self.bad_reviews:
            rows = self.bad_reviews(rows)
        return {"reviews": rows}


def screened(index, quality, mechanism, test_type):
    item = idea(index)
    item.update(id=f"idea-{index}", status="screened", mechanism=mechanism,
                test_type=test_type,
                scores={"quality": quality, "usefulness": .8, "feasibility": .7})
    return item


class ArchiveBehaviorTests(unittest.TestCase):
    def test_global_champion_does_not_erase_a_different_niche(self):
        archive = Archive()
        champion = screened(1, .95, "subtract", "prototype")
        stepping_stone = screened(2, .55, "transfer", "simulation")
        replacement = screened(3, .97, "subtract", "prototype")
        worse = screened(4, .8, "subtract", "prototype")
        archive.update([champion, stepping_stone, replacement, worse])
        self.assertEqual({row["idea_id"] for row in archive.export()},
                         {replacement["id"], stepping_stone["id"]})

    def test_high_novelty_cannot_admit_an_infeasible_or_irrelevant_candidate(self):
        archive = Archive()
        infeasible = screened(1, .99, "combine", "simulation")
        infeasible["scores"]["feasibility"] = .1
        irrelevant = screened(2, .99, "subtract", "prototype")
        irrelevant["scores"]["usefulness"] = .1
        unscreened = screened(3, .99, "transfer", "ab_test")
        unscreened["status"] = "proposed"
        archive.update([infeasible, irrelevant, unscreened])
        self.assertEqual(archive.export(), [])


class BoundaryTests(unittest.TestCase):
    def test_scores_reject_bool_non_numeric_nonfinite_and_out_of_range(self):
        for bad in (True, None, "0.8", float("nan"), float("inf"), -0.1, 1.1,
                    10**400):
            with self.subTest(value=repr(bad)[:60]):
                with self.assertRaises(ProviderError):
                    normalize_review(review("candidate-1", usefulness=bad))

    def test_review_explanations_are_required_and_bounded(self):
        for key, value in (("rationale", ""), ("next_test", 42),
                           ("failure_modes", []), ("failure_modes", [False]),
                           ("failure_modes", ["x"] * 9)):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ProviderError):
                    normalize_review(review("candidate-1", **{key: value}))

    def test_model_cannot_invent_archive_categories(self):
        for key in ("mechanism", "test_type"):
            item = idea()
            item[key] = "world-changing-unverified-category"
            with self.subTest(field=key), self.assertRaises(ProviderError):
                normalize_idea(item)

    def test_unknown_model_fields_cannot_become_experimental_evidence(self):
        item = idea()
        item.update(experiment_passed=True, globally_novel=True,
                    human_superiority="proved", api_key="untrusted-output")
        normalized = normalize_idea(item)
        self.assertEqual(set(normalized),
                         {"title", "description", "test", "mechanism", "test_type",
                          "assumptions", "risks"})

    def test_duplicate_unknown_and_unhashable_judge_ids_are_controlled_errors(self):
        transformations = {
            "duplicate": lambda rows: [rows[0], deepcopy(rows[0])],
            "unknown": lambda rows: [rows[0], review("not-supplied")],
            "unhashable": lambda rows: [rows[0], review(["candidate-2"])],
        }
        for name, transform in transformations.items():
            with self.subTest(case=name), self.assertRaises(ProviderError):
                Engine(RecordingProvider(bad_reviews=transform)).run(
                    RunConfig("Design a prototype", rounds=1, candidates_per_round=2))

    def test_generator_outer_schema_errors_are_controlled(self):
        for response in ([], "wrong", 7):
            with self.subTest(response=response), self.assertRaises(ProviderError):
                Engine(RecordingProvider(bad_response=response)).run(
                    RunConfig("Design a prototype", rounds=1, candidates_per_round=2))

    def test_judge_outer_schema_errors_are_controlled(self):
        class BadJudge(RecordingProvider):
            def complete(self, payload, budget):
                budget.reserve()
                return ["not a review object"]
        with self.assertRaises(ProviderError):
            Engine(RecordingProvider(), judge=BadJudge()).run(
                RunConfig("Design a prototype", rounds=1, candidates_per_round=2))

    def test_lexical_distance_does_not_claim_semantic_equivalence(self):
        self.assertEqual(similarity("rapid automobile", "fast car"), 0)
        result = Engine(DemoProvider()).run(
            RunConfig("Reduce waste", rounds=1, candidates_per_round=2))
        self.assertIn("lexical", result["provenance"]["novelty_method"].lower())
        self.assertIn("no semantic", result["provenance"]["novelty_method"].lower())
        self.assertIn("global", result["provenance"]["novelty_method"].lower())


class SearchBehaviorTests(unittest.TestCase):
    def test_paid_generation_failure_preserves_candidate_budget_and_safe_trace(self):
        secret = "fake-upstream-key-do-not-export"

        class FailsSecondGeneration(RecordingProvider):
            def complete(self, payload, budget):
                if payload["purpose"] == "generate" and self.next_index == 1:
                    budget.reserve()
                    raise ProviderError("upstream echoed " + secret)
                return super().complete(payload, budget)

        with self.assertRaises(ProviderError) as caught:
            Engine(FailsSecondGeneration()).run(
                RunConfig("Design a prototype", rounds=2, candidates_per_round=2))
        partial = caught.exception.partial_result
        self.assertEqual(partial["status"], "provider_error")
        self.assertEqual(partial["budget"]["calls"], 2)
        self.assertFalse(partial["budget"]["usage_complete"])
        self.assertEqual(len(partial["ideas"]), 1)
        self.assertEqual(partial["ideas"][0]["status"], "proposed")
        self.assertEqual(partial["archive"], [])
        self.assertEqual(partial["trace"][-1]["event"], "provider_error")
        self.assertEqual(partial["trace"][-1]["phase"], "generate")
        self.assertNotIn(secret, str(caught.exception))
        import json
        self.assertNotIn(secret, json.dumps(partial))

    def test_review_failure_preserves_proposals_and_existing_archive(self):
        class FailsSecondReview(RecordingProvider):
            def __init__(self):
                super().__init__()
                self.review_calls = 0

            def complete(self, payload, budget):
                if payload["purpose"] == "review":
                    self.review_calls += 1
                    if self.review_calls == 2:
                        budget.reserve()
                        raise ProviderError("private upstream response body")
                return super().complete(payload, budget)

        with self.assertRaises(ProviderError) as caught:
            Engine(FailsSecondReview()).run(
                RunConfig("Design a prototype", rounds=2, candidates_per_round=2))
        partial = caught.exception.partial_result
        self.assertEqual(partial["budget"]["calls"], 6)
        self.assertEqual(len(partial["ideas"]), 4)
        self.assertEqual([row["status"] for row in partial["ideas"]],
                         ["screened", "screened", "proposed", "proposed"])
        self.assertEqual({row["idea_id"] for row in partial["archive"]},
                         {row["id"] for row in partial["ideas"][:2]})
        self.assertTrue(all(row["scores"] == {} for row in partial["ideas"][2:]))
        self.assertEqual(partial["trace"][-1]["phase"], "review")
        self.assertEqual(partial["status"], "provider_error")

    def test_malformed_review_never_partially_applies_good_rows(self):
        def corrupt_last(rows):
            rows[-1]["usefulness"] = "high"
            return rows

        with self.assertRaises(ProviderError) as caught:
            Engine(RecordingProvider(bad_reviews=corrupt_last)).run(
                RunConfig("Design a prototype", rounds=1, candidates_per_round=2))
        partial = caught.exception.partial_result
        self.assertEqual(len(partial["ideas"]), 2)
        self.assertTrue(all(row["status"] == "proposed" and row["scores"] == {}
                            for row in partial["ideas"]))
        self.assertEqual(partial["archive"], [])

    def test_changed_titles_and_categories_do_not_disguise_duplicate_mechanisms(self):
        provider = RecordingProvider(same_description=True)
        result = Engine(provider).run(
            RunConfig("Reduce waste", rounds=2, candidates_per_round=3, max_calls=20))
        self.assertEqual(len(result["ideas"]), 1)
        self.assertEqual(len(result["archive"]), 1)
        self.assertEqual(sum(row["event"] == "duplicate_rejected"
                             for row in result["trace"]), 5)
        self.assertEqual(result["budget"]["calls"], 7)

    def test_fixture_replay_is_stable_except_explicit_run_metadata(self):
        config = RunConfig("Reduce waste", rounds=3, candidates_per_round=6, seed=73)
        one, two = Engine(DemoProvider()).run(config), Engine(DemoProvider()).run(config)
        for result in (one, two):
            result.pop("run_id")
            result.pop("created_at")
        self.assertEqual(one, two)
        self.assertTrue(one["demo"])
        self.assertIn("Fixture", one["claim"])

    def test_every_parent_exists_and_precedes_its_child(self):
        result = Engine(RecordingProvider()).run(
            RunConfig("Improve mobility", rounds=3, candidates_per_round=6, seed=4))
        by_id = {item["id"]: item for item in result["ideas"]}
        self.assertEqual(len(by_id), len(result["ideas"]))
        for child in result["ideas"]:
            if child["round"] > 1:
                self.assertTrue(child["parents"])
            for parent_id in child["parents"]:
                self.assertIn(parent_id, by_id)
                self.assertLess(by_id[parent_id]["round"], child["round"])

    def test_baseline_never_receives_archive_or_avoidance_steering(self):
        provider = RecordingProvider()
        Engine(provider).run(RunConfig("Improve mobility", rounds=3,
                                       candidates_per_round=3, mode="baseline"))
        generations = [row for row in provider.requests if row["purpose"] == "generate"]
        self.assertEqual(len(generations), 9)
        for request in generations:
            self.assertEqual(request["operator"], "direct")
            self.assertEqual(request["parents"], [])
            self.assertEqual(request["avoid"], [])

    def test_judge_cannot_see_search_source_or_internal_proxy(self):
        provider = RecordingProvider()
        Engine(provider).run(RunConfig("Improve mobility", rounds=2,
                                       candidates_per_round=3))
        for request in provider.requests:
            if request["purpose"] != "review":
                continue
            for candidate in request["candidates"]:
                self.assertTrue(candidate["id"].startswith("candidate-"))
                self.assertFalse({"operator", "parents", "novelty_proxy", "scores", "round"}
                                 & set(candidate))

    def test_exact_budget_leaves_unreviewed_candidates_visible(self):
        result = Engine(RecordingProvider()).run(
            RunConfig("Reduce waste", rounds=3, candidates_per_round=2, max_calls=2))
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertEqual(result["budget"]["calls"], 2)
        self.assertEqual(len(result["ideas"]), 2)
        self.assertEqual({item["status"] for item in result["ideas"]}, {"proposed"})
        self.assertEqual(result["archive"], [])


if __name__ == "__main__":
    unittest.main()
