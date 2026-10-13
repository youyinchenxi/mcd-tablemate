"""Synthetic official-response adapters; no credential or network access."""
from copy import deepcopy
import unittest

from maimai_team.official import build_snapshot


def success(data):
    return {"success": True, "code": 200, "data": data}


def choice(code, name, quantity=0, default=0, difference="+ ¥0", modification=None):
    result = {"code": code, "name": name, "quantity": quantity,
              "isDefault": default, "diffPrice": difference}
    if modification is not None:
        result["modification"] = modification
    return result


def fixture():
    menu = success({"meals": {"set": {"name": "测试套餐", "currentPrice": "19.90"},
                             "coffee": {"name": "测试咖啡", "currentPrice": "8.1"}}})
    details = {
        "set": success({"code": "set", "name": "测试套餐", "rounds": [
            {"id": 1, "quantity": 1, "minQuantity": 1, "maxQuantity": 1,
             "choices": [choice("main", "测试主食", 1, 1)]},
            {"id": 2, "quantity": 1, "minQuantity": 1, "maxQuantity": 1,
             "choices": [choice("drink", "测试饮料", 1, 1),
                         choice("coffee", "测试咖啡", 0, 1, "+ ¥1.5"),
                         choice("milk", "测试牛奶", 0, 1)]}]}),
        "coffee": success({"code": "coffee", "name": "测试咖啡", "rounds": [],
                            "modification": {"items": [{"values": [
                                {"code": "shot", "selectedQuantity": 1, "selectedKey": "0-0", "price": 0},
                                {"code": "shot", "selectedQuantity": 0, "selectedKey": "0-1", "price": 500},
                                {"code": "sugar", "selectedQuantity": 0, "selectedKey": "1-1", "unselectedKey": "1-0", "price": 0}]}]}}),
    }
    profile = {"offers": [{"code": "set"}, {"code": "coffee"}], "components": {
        "main": {"category": "main", "tags": ["chicken"]},
        "drink": {"category": "drink", "tags": []},
        "coffee": {"category": "drink", "tags": ["coffee"]},
        "milk": {"category": "drink", "tags": ["milk"]}}}
    return menu, details, profile, {"id": "MOCK-STORE", "name": "模拟门店"}


class OfficialAdapterTests(unittest.TestCase):
    def test_positive_quantity_beats_many_default_flags_and_preserves_components(self):
        catalog, template = build_snapshot(*fixture())
        self.assertEqual(catalog["source"]["mode"], "mcp")
        offer = catalog["offers"][0]
        self.assertEqual(offer["price_fen"], 1990)
        self.assertEqual([c["name"] for c in offer["components"]], ["测试主食", "测试饮料"])
        self.assertEqual(sum(c["quantity"] for c in offer["components"]), 2)
        arg = template["item_templates"][offer["id"]]["argument"]
        self.assertEqual(arg["roundList"][1]["comboItemList"], [{"code": "drink", "quantity": 1}])
        self.assertEqual(template["arguments"]["orderType"], 1)
        self.assertEqual(template["arguments"]["beType"], 1)
        self.assertEqual(template["total_path"], "/data/price")
        self.assertEqual(template["total_unit"], "fen")
        self.assertTrue(template["total_includes_fees"])

    def test_variant_diffprice_and_unique_item_ids_prevent_specification_merging(self):
        menu, details, profile, store = fixture()
        profile["offers"] = [{"code": "set"}, {"code": "set", "round_selections": {
            "2": [{"code": "coffee", "quantity": 1}]}}]
        catalog, template = build_snapshot(menu, details, profile, store)
        self.assertEqual([c["price_fen"] for c in catalog["offers"]], [1990, 2140])
        self.assertNotEqual(catalog["offers"][0]["items"][0]["id"], catalog["offers"][1]["items"][0]["id"])
        self.assertEqual(catalog["offers"][1]["components"][1]["tags"], ["coffee"])
        self.assertEqual(len(template["item_templates"]), 2)

    def test_customizations_preserve_selected_and_unselected_keys(self):
        catalog, template = build_snapshot(*fixture())
        coffee = catalog["offers"][1]
        values = template["item_templates"][coffee["id"]]["argument"]["modification"]["values"]
        self.assertEqual(values, [{"code": "shot", "key": "0-0", "quantity": 1},
                                  {"code": "sugar", "key": "1-0", "quantity": 0}])
        # Same-code values with distinct keys are retained, not deduplicated.
        args = list(fixture())
        args[1]["coffee"]["data"]["modification"]["items"][0]["values"][1]["unselectedKey"] = "other-0"
        catalog, template = build_snapshot(*args)
        values = template["item_templates"][catalog["offers"][1]["id"]]["argument"]["modification"]["values"]
        self.assertEqual([v["code"] for v in values], ["shot", "shot", "sugar"])

    def test_nested_round_customizations_are_preserved(self):
        args = list(fixture())
        args[1]["set"]["data"]["rounds"][0]["choices"][0]["modification"] = {
            "items": [{"values": [{"code": "sauce", "selectedQuantity": 1, "selectedKey": "yes", "unselectedKey": "no"}]}]}
        catalog, template = build_snapshot(*args)
        main = template["item_templates"][catalog["offers"][0]["id"]]["argument"]["roundList"][0]["comboItemList"][0]
        self.assertEqual(main["modification"]["values"], [{"code": "sauce", "key": "yes", "quantity": 1}])

    def test_unknown_components_fail_without_inventing_tags(self):
        args = list(fixture())
        del args[2]["components"]["drink"]
        with self.assertRaises(ValueError):
            build_snapshot(*args)

    def test_round_quantity_and_product_validation(self):
        for selection in ([{"code": "coffee", "quantity": 2}], [{"code": "unknown", "quantity": 1}],
                          [{"code": "coffee", "quantity": 0}], [],
                          [{"code": "coffee", "quantity": 1, "modification": {}}]):
            args = list(fixture())
            args[2]["offers"][0]["round_selections"] = {"2": selection}
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                build_snapshot(*args)

    def test_multiple_defaults_without_current_quantity_require_explicit_choice(self):
        args = list(fixture())
        choices = args[1]["set"]["data"]["rounds"][1]["choices"]
        for c in choices:
            c["quantity"] = 0
            c["diffPrice"] = "+ ¥0"
        with self.assertRaises(ValueError):
            build_snapshot(*args)
        args[2]["offers"][0]["round_selections"] = {"2": [{"code": "milk", "quantity": 1}]}
        catalog, template = build_snapshot(*args)
        self.assertEqual(catalog["offers"][0]["components"][1]["tags"], ["milk"])

    def test_default_quantity_mismatch_and_unknown_diffprice_fail(self):
        args = list(fixture())
        args[1]["set"]["data"]["rounds"][1]["choices"][1]["quantity"] = 1
        with self.assertRaises(ValueError):
            build_snapshot(*args)
        args = list(fixture())
        args[1]["set"]["data"]["rounds"][1]["choices"][1]["diffPrice"] = "价格待确认"
        args[2]["offers"][0]["round_selections"] = {"2": [{"code": "coffee", "quantity": 1}]}
        with self.assertRaises(ValueError):
            build_snapshot(*args)

    def test_business_failure_is_not_a_catalog(self):
        args = list(fixture())
        args[0]["success"] = False
        with self.assertRaises(ValueError):
            build_snapshot(*args)
        args = list(fixture())
        args[1]["coffee"]["code"] = 400
        with self.assertRaises(ValueError):
            build_snapshot(*args)

    def test_only_pickup_and_exact_fen_prices(self):
        args = list(fixture())
        args[2]["meal_mode"] = "麦乐送到家"
        with self.assertRaises(ValueError):
            build_snapshot(*args)
        args = list(fixture())
        args[0]["data"]["meals"]["set"]["currentPrice"] = "19.901"
        with self.assertRaises(ValueError):
            build_snapshot(*args)

    def test_mcp_envelopes_and_input_immutability(self):
        args = fixture()
        original = deepcopy(args)
        envelope = {"structuredContent": args[0]}
        catalog, template = build_snapshot(envelope, *args[1:])
        self.assertEqual(args, original)
        self.assertEqual(len(catalog["offers"]), 2)


if __name__ == "__main__":
    unittest.main()
