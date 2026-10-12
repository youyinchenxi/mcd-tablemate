import copy
import json
from collections import Counter
from pathlib import Path
import unittest
from unittest.mock import patch

from maimai_team.planner import plan


ROOT = Path(__file__).resolve().parents[1]


def meal(oid, price, components, max_quantity=60, items=None):
    return {"id": oid, "name": oid, "price_fen": price, "components": components,
            "items": items or [{"id": oid, "quantity": 1}], "max_quantity": max_quantity}


def component(name, category, tags=(), quantity=1):
    return {"name": name, "category": category, "tags": list(tags), "quantity": quantity}


def person(pid, **overrides):
    return {"id": pid, "name": pid, "needs": {"main": 1, "drink": 1, "side": 0},
            "exclude_tags": [], "must_tags": [], "prefer_tags": [], **overrides}


def catalog(offers):
    return {"store": {"id": "synthetic", "name": "Synthetic demo"}, "offers": offers}


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT / "examples" / "demo_catalog.json").read_text(encoding="utf-8"))
        self.request = json.loads((ROOT / "examples" / "demo_request.json").read_text(encoding="utf-8"))

    def test_demo_all_candidates_respect_hard_tags_and_needs(self):
        result = plan(self.catalog, self.request)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["candidates"])
        for candidate in result["candidates"]:
            self.assertEqual(candidate["unmet"], [])
            for p, assignment in zip(self.request["people"], candidate["assignments"]):
                tags = {tag for c in assignment["components"] for tag in c["tags"]}
                self.assertFalse(tags.intersection(p["exclude_tags"]))
                self.assertTrue(set(p["must_tags"]).issubset(tags))
                counts = Counter()
                for c in assignment["components"]:
                    counts[c["category"]] += c["quantity"]
                for category, wanted in p["needs"].items():
                    self.assertGreaterEqual(counts[category], wanted)

    def test_purchase_quantities_and_package_components_conserved(self):
        offers = self.catalog["offers"]
        by_id = {o["id"]: o for o in offers}
        for candidate in plan(self.catalog, self.request)["candidates"]:
            expected_items, expected_price = Counter(), 0
            for assignment in candidate["assignments"]:
                expected_components = Counter()
                for owned in assignment["offers"]:
                    offer = by_id[owned["id"]]
                    expected_price += offer["price_fen"] * owned["quantity"]
                    for item in offer["items"]:
                        expected_items[item["id"]] += item["quantity"] * owned["quantity"]
                    for c in offer["components"]:
                        expected_components[(c["name"], c["category"])] += c["quantity"] * owned["quantity"]
                actual = Counter()
                for c in assignment["components"]:
                    actual[(c["name"], c["category"])] += c["quantity"]
                self.assertEqual(actual, expected_components)
            self.assertEqual({i["id"]: i["quantity"] for i in candidate["items"]}, dict(expected_items))
            self.assertEqual(candidate["estimated_total_fen"], expected_price)

    def test_full_package_extra_fries_remain_owned_and_visible(self):
        offer = meal("set", 2000, [component("burger", "main"), component("cola", "drink"),
                                    component("fries", "side", quantity=2)])
        result = plan(catalog([offer]), {"people": [person("a"), person("b")], "budget_fen": 5000})
        candidate = result["candidates"][0]
        self.assertEqual(candidate["items"], [{"id": "set", "quantity": 2}])
        self.assertEqual(sum(c["quantity"] for c in candidate["extras"]), 4)
        self.assertEqual({c["person_id"] for c in candidate["extras"]}, {"a", "b"})

    def test_excluded_tag_in_package_side_blocks_entire_package(self):
        offer = meal("set", 1000, [component("burger", "main"), component("cola", "drink"),
                                    component("beef-side", "side", ["牛肉"])])
        result = plan(catalog([offer]), {"people": [person("a", exclude_tags=["牛肉"]), person("b")],
                                       "budget_fen": 5000})
        self.assertEqual(result["status"], "no_solution")
        self.assertEqual(result["candidates"], [])

    def test_budget_shortfall_keeps_complete_over_budget_candidates_for_quotes(self):
        self.request["budget_fen"] = 1
        result = plan(self.catalog, self.request)
        self.assertEqual(result["status"], "estimated_over_budget")
        self.assertTrue(result["candidates"])
        self.assertTrue(all(not c["estimated_within_budget"] for c in result["candidates"]))
        self.assertTrue(all(c["unmet"] == [] for c in result["candidates"]))
        self.assertEqual(result["price_source"], "catalog_estimate")

    def test_estimated_over_budget_candidates_reserved_alongside_affordable_ones(self):
        self.request["budget_fen"] = 7600
        result = plan(self.catalog, self.request)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(any(c["estimated_within_budget"] for c in result["candidates"]))
        self.assertTrue(any(not c["estimated_within_budget"] for c in result["candidates"]))

    def test_change_order_to_coffee_changes_assignment(self):
        self.request["people"][0]["must_tags"] = []
        before = plan(self.catalog, self.request)["candidates"][0]
        self.request["people"][0]["must_tags"] = ["咖啡"]
        after = plan(self.catalog, self.request)["candidates"][0]
        self.assertNotEqual(before["assignments"][0]["offers"], after["assignments"][0]["offers"])
        self.assertIn("咖啡", {t for c in after["assignments"][0]["components"] for t in c["tags"]})

    def test_quantity_cap_applies_across_people(self):
        offer = meal("only", 1000, [component("burger", "main"), component("cola", "drink")], 1)
        result = plan(catalog([offer]), {"people": [person("a"), person("b")], "budget_fen": 5000})
        self.assertEqual(result["status"], "no_solution")
        offer["max_quantity"] = 2
        result = plan(catalog([offer]), {"people": [person("a"), person("b")], "budget_fen": 5000})
        self.assertEqual(result["candidates"][0]["items"], [{"id": "only", "quantity": 2}])

    def test_non_splittable_two_person_package_not_reused_as_two_meals(self):
        offer = meal("double", 2000, [component("burger", "main", quantity=2),
                                      component("cola", "drink", quantity=2)], 1)
        result = plan(catalog([offer]), {"people": [person("a"), person("b")], "budget_fen": 5000})
        self.assertEqual(result["status"], "no_solution")

    def test_single_items_and_shared_item_ids_aggregate_purchase_counts(self):
        offers = [meal("burger", 1000, [component("burger", "main")], items=[{"id": "sku", "quantity": 2}]),
                  meal("coffee", 500, [component("coffee", "drink", ["咖啡"])])]
        result = plan(catalog(offers), {"people": [person("a", must_tags=["咖啡"]), person("b")],
                                      "budget_fen": 5000})
        self.assertEqual(result["candidates"][0]["items"], [{"id": "coffee", "quantity": 2}, {"id": "sku", "quantity": 4}])

    def test_budget_strategy_returns_cheapest_estimate_first(self):
        self.request["strategy"] = "budget"
        result = plan(self.catalog, self.request)
        prices = [c["estimated_total_fen"] for c in result["candidates"]]
        self.assertEqual(prices, sorted(prices))

    def test_six_people_receive_distinct_complete_meals_with_quantity_cap(self):
        offer = meal("set", 2000, [component("burger", "main"), component("cola", "drink")], 6)
        request = {"people": [person(str(i)) for i in range(6)], "budget_fen": 12000}
        result = plan(catalog([offer]), request)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["items"], [{"id": "set", "quantity": 6}])
        self.assertEqual(len(candidate["assignments"]), 6)
        self.assertEqual(sum(a["estimated_cost_fen"] for a in candidate["assignments"]), 12000)

    def test_soft_preference_can_be_unmet_without_relaxing_hard_requirements(self):
        offer = meal("set", 2000, [component("burger", "main", ["鸡肉"]), component("cola", "drink")])
        request = {"people": [person("a", prefer_tags=["咖啡"], exclude_tags=["牛肉"]), person("b")],
                   "budget_fen": 4000}
        result = plan(catalog([offer]), request)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["assignments"][0]["preference_hits"], [])

    def test_constrained_person_keeps_scarce_coffee_and_output_order_is_preserved(self):
        offers = [meal("coffee-set", 1000, [component("burger", "main"), component("coffee", "drink", ["咖啡"])], 1),
                  meal("cola-set", 2000, [component("burger", "main"), component("cola", "drink")])]
        request = {"people": [person("a"), person("b", must_tags=["咖啡"])], "budget_fen": 5000}
        result = plan(catalog(offers), request)
        first = result["candidates"][0]["assignments"]
        self.assertEqual([a["person_id"] for a in first], ["a", "b"])
        self.assertEqual(first[1]["offers"][0]["id"], "coffee-set")
        self.assertEqual(first[0]["offers"][0]["id"], "cola-set")

    def test_search_truncation_is_explicit_when_no_solution_was_exhaustively_proven(self):
        with patch("maimai_team.planner.MAX_NODES_PER_PERSON", 1):
            result = plan(self.catalog, self.request)
        self.assertTrue(result["search"]["truncated"])
        self.assertTrue(any("exhaustive" in warning for warning in result["warnings"]))

    def test_candidate_limit_one_keeps_an_affordable_candidate(self):
        self.request.update(budget_fen=7600, quote_limit=1)
        result = plan(self.catalog, self.request)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertTrue(result["candidates"][0]["estimated_within_budget"])

    def test_input_is_not_mutated_and_results_are_deterministic(self):
        before_catalog, before_request = copy.deepcopy(self.catalog), copy.deepcopy(self.request)
        self.assertEqual(plan(self.catalog, self.request), plan(self.catalog, self.request))
        self.assertEqual(self.catalog, before_catalog)
        self.assertEqual(self.request, before_request)

    def test_invalid_values_and_duplicates_raise_value_error(self):
        mutations = [
            lambda c, r: r["people"].append(copy.deepcopy(r["people"][0])),
            lambda c, r: c["offers"].append(copy.deepcopy(c["offers"][0])),
            lambda c, r: c["offers"][0]["items"].append(copy.deepcopy(c["offers"][0]["items"][0])),
            lambda c, r: c["offers"][0].update(price_fen=-1),
            lambda c, r: c["offers"][0].update(price_fen=True),
            lambda c, r: c["offers"][0]["components"][0].update(quantity=0),
            lambda c, r: c["offers"][0]["components"][0].update(category="other"),
            lambda c, r: r.update(budget_fen=-1),
            lambda c, r: r.update(quote_limit=9),
            lambda c, r: r.update(strategy="unknown"),
            lambda c, r: r.update(people=[r["people"][0]]),
            lambda c, r: r["people"][0].update(needs={"main": -1}),
            lambda c, r: r["people"][0].update(exclude_tags=["牛肉", "牛肉"]),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                c, r = copy.deepcopy(self.catalog), copy.deepcopy(self.request)
                mutate(c, r)
                with self.assertRaises(ValueError):
                    plan(c, r)


if __name__ == "__main__":
    unittest.main()
