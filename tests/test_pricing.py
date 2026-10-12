"""Synthetic pricing fixtures. No test here proves a real MCP integration."""
import unittest
from copy import deepcopy

from maimai_team.pricing import allocate_shares, verify_candidates


def candidate(cid, estimated=2000, score=100):
    return {"id": cid, "estimated_total_fen": estimated, "score": score, "items": [],
            "assignments": [{"person_id": "a", "person_name": "甲", "estimated_cost_fen": 1100},
                            {"person_id": "b", "person_name": "乙", "estimated_cost_fen": 900}], "extras": []}


def mock_quote(amount):
    # Evidence is deliberately labelled as a test fixture; production only
    # obtains this structure from the application's trusted live MCP adapter.
    return {"total_fen": amount, "evidence": {"tool": "calculate-price", "source": "mcp",
            "store_id": "LOCAL-MOCK-STORE", "verified_at": "MOCK-TIME",
            "total_includes_fees": True, "test_fixture": True}}


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.request = {"budget_fen": 2500, "strategy": "balanced", "people": [{"id": "a"}, {"id": "b"}]}
        self.result = {"source": "demo", "verified": False, "candidates": [candidate("a"), candidate("b", score=90)], "warnings": []}

    def test_fees_make_previously_estimated_budget_candidate_over_budget(self):
        output = verify_candidates(self.result, self.request, lambda c: mock_quote(2600))
        self.assertEqual(output["status"], "over_budget")
        self.assertFalse(output["verified"])
        self.assertEqual(output["candidates"], [])
        self.assertEqual(output["over_budget_candidate_ids"], ["a", "b"])

    def test_quote_failure_removed_and_errors_not_echoed(self):
        def callback(c):
            if c["id"] == "b":
                raise ValueError("SECRET-TOKEN")
            return mock_quote(2201)
        output = verify_candidates(self.result, self.request, callback)
        self.assertEqual(output["status"], "ok")
        self.assertEqual([c["id"] for c in output["candidates"]], ["a"])
        self.assertEqual(output["failed_candidate_ids"], ["b"])
        self.assertNotIn("SECRET-TOKEN", str(output))
        verified = output["candidates"][0]
        self.assertEqual(sum(s["share_fen"] for s in verified["shares"]), 2201)
        self.assertEqual(verified["total_fen"], 2201)
        self.assertTrue(verified["verified"])

    def test_incomplete_evidence_fails_closed(self):
        for bad in ({"total_fen": 0}, {"total_fen": True, "evidence": mock_quote(0)["evidence"]},
                    {"total_fen": -1, "evidence": mock_quote(0)["evidence"]}):
            output = verify_candidates(self.result, self.request, lambda c: bad)
            self.assertEqual(output["status"], "quote_failed")
        for key, bad_value in (("tool", "create-order"), ("source", "demo"),
                               ("total_includes_fees", False), ("verified_at", ""), ("store_id", "")):
            quote = mock_quote(1200)
            quote["evidence"][key] = bad_value
            output = verify_candidates(self.result, self.request, lambda c: quote)
            self.assertEqual(output["status"], "quote_failed")
            self.assertFalse(output["verified"])

    def test_budget_strategy_reorders_using_real_total(self):
        self.request["strategy"] = "budget"
        output = verify_candidates(self.result, self.request, lambda c: mock_quote(2400 if c["id"] == "a" else 1800))
        self.assertEqual([c["id"] for c in output["candidates"]], ["b", "a"])

    def test_balanced_strategy_keeps_preference_then_actual_price(self):
        output = verify_candidates(self.result, self.request, lambda c: mock_quote(2400 if c["id"] == "a" else 1800))
        self.assertEqual([c["id"] for c in output["candidates"]], ["a", "b"])
        self.result["candidates"][1]["score"] = 100
        output = verify_candidates(self.result, self.request, lambda c: mock_quote(2400 if c["id"] == "a" else 1800))
        self.assertEqual([c["id"] for c in output["candidates"]], ["b", "a"])

    def test_no_candidates_distinct_from_failed_quotes(self):
        self.result["candidates"] = []
        output = verify_candidates(self.result, self.request, lambda c: self.fail("must not quote"))
        self.assertEqual(output["status"], "no_solution")
        self.assertEqual(output["quote_status"], "no_candidates")

    def test_input_is_not_mutated_even_by_callback(self):
        original = deepcopy(self.result)
        def callback(c):
            c["items"].append({"id": "injected"})
            return mock_quote(2000)
        verify_candidates(self.result, self.request, callback)
        self.assertEqual(self.result, original)

    def test_largest_remainder_and_zero_weights(self):
        assignments = [{"person_id": str(i), "estimated_cost_fen": 1} for i in range(3)]
        shares = allocate_shares(assignments, 100)
        self.assertEqual([s["share_fen"] for s in shares], [34, 33, 33])
        for a in assignments:
            a["estimated_cost_fen"] = 0
        self.assertEqual(sum(s["share_fen"] for s in allocate_shares(assignments, 7)), 7)
        self.assertEqual([s["share_fen"] for s in allocate_shares(assignments, 7)], [3, 2, 2])

    def test_allocation_conserves_total_for_various_fees_and_weights(self):
        for weights in ((0, 1), (19, 23, 29), (1, 1, 1, 1), (1000000, 1)):
            assignments = [{"person_id": str(i), "estimated_cost_fen": w} for i, w in enumerate(weights)]
            for total in (0, 1, 2, 99, 1234, 99999999):
                shares = allocate_shares(assignments, total)
                self.assertEqual(sum(s["share_fen"] for s in shares), total)
                self.assertTrue(all(s["share_fen"] >= 0 for s in shares))


if __name__ == "__main__":
    unittest.main()
