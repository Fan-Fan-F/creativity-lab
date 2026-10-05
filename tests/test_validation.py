import copy
import unittest
from creativity_lab.engine import Engine, RunConfig
from creativity_lab.providers import DemoProvider
from creativity_lab.validation import attach_evidence, evidence_template


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.run = Engine(DemoProvider()).run(RunConfig("低成本图书交换", rounds=1, candidates_per_round=2))
        self.report = evidence_template(self.run)
        self.report["results"] = self.report["results"][:1]
        self.report["results"][0].update(candidate_value=3, control_value=2, constraints_passed=True,
                                        evaluator="trusted-test", evaluator_version="1", metric="success-rate",
                                        artifact="result.json", protocol="frozen-control-v1")

    def test_real_numbers_required_before_import(self):
        with self.assertRaises(ValueError):
            attach_evidence(self.run, evidence_template(self.run))

    def test_measured_improvement_does_not_rewrite_model_scores(self):
        output = attach_evidence(self.run, self.report)
        self.assertEqual(output["ideas"][0]["evidence"][0]["status"], "reported_pass")
        self.assertEqual(output["ideas"][0]["scores"], self.run["ideas"][0]["scores"])
        self.assertNotIn("evidence", self.run["ideas"][0])

    def test_edited_idea_cannot_receive_old_evidence(self):
        changed = copy.deepcopy(self.run)
        changed["ideas"][0]["description"] += "changed"
        with self.assertRaises(ValueError):
            attach_evidence(changed, self.report)

    def test_duplicate_unknown_or_nonfinite_evidence_rejected(self):
        for mutation in ("duplicate", "unknown", "nan", "bool"):
            report = copy.deepcopy(self.report)
            if mutation == "duplicate":
                report["results"] *= 2
            elif mutation == "unknown":
                report["results"][0]["idea_id"] = "wrong"
            elif mutation == "nan":
                report["results"][0]["candidate_value"] = float("nan")
            else:
                report["results"][0]["constraints_passed"] = "yes"
            with self.assertRaises(ValueError):
                attach_evidence(self.run, report)


if __name__ == "__main__":
    unittest.main()
