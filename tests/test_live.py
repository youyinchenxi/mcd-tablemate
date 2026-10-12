"""Mapping regression tests using synthetic schemas and in-memory responses."""
from copy import deepcopy
import unittest

from maimai_team.live import quote_callback


class FakeClient:
    def __init__(self, data=None, text=False):
        self.calls = []
        self.data = data if data is not None else {'code': 0, 'payment': {'all': 7311}}
        self.text = text

    def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if self.text:
            import json
            return {'content': [{'type': 'text', 'text': json.dumps(self.data)}]}
        return {'structuredContent': self.data}


class LiveMappingTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {'store': {'id': 'synthetic-store'}, 'source': {'mode': 'mcp'}}
        self.template = {'tool': 'calculate-price',
            'arguments': {'testStore': {'$ref': 'store_id'}, 'testBasket': {'$ref': 'cart'}},
            'item_templates': {
                'coffee-variant': {'argument': {'testCode': 'synthetic-coffee', 'spec': 'hot', 'amount': 1}, 'quantity_path': '/amount'},
                'burger-variant': {'argument': {'testCode': 'synthetic-burger', 'spec': 'single', 'amount': 1}, 'quantity_path': '/amount'}},
            'success': {'path': '/code', 'equals': 0}, 'total_path': '/payment/all',
            'total_unit': 'fen', 'total_includes_fees': True, 'meal_mode': 'synthetic-takeaway'}
        self.candidate = {'id': 'candidate-test', 'items': [
            {'id': 'coffee-variant', 'quantity': 2}, {'id': 'burger-variant', 'quantity': 3}]}

    def test_quotes_exact_full_cart_quantities_store_and_specs(self):
        client = FakeClient()
        value = quote_callback(client, self.catalog, self.template)(self.candidate)
        args = client.calls[0][1]
        self.assertEqual(args['testStore'], 'synthetic-store')
        self.assertEqual([i['amount'] for i in args['testBasket']], [2, 3])
        self.assertEqual([i['spec'] for i in args['testBasket']], ['hot', 'single'])
        self.assertEqual(value['total_fen'], 7311)
        self.assertEqual(value['evidence']['candidate_id'], 'candidate-test')
        self.assertEqual(len(value['evidence']['cart_digest']), 64)
        self.assertEqual(self.template['item_templates']['coffee-variant']['argument']['amount'], 1)

    def test_missing_duplicate_cart_or_store_reference_rejected_before_network(self):
        for bad_args in ({'testStore': {'$ref': 'store_id'}, 'testBasket': [{'testCode': 'unrelated'}]},
                         {'testStore': {'$ref': 'store_id'}, 'a': {'$ref': 'cart'}, 'b': {'$ref': 'cart'}},
                         {'testStore': 'different-store', 'testBasket': {'$ref': 'cart'}}):
            template = deepcopy(self.template)
            template['arguments'] = bad_args
            client = FakeClient()
            with self.assertRaises(ValueError):
                quote_callback(client, self.catalog, template)
            self.assertFalse(client.calls)

    def test_false_is_not_integer_business_success(self):
        for actual, expected in ((False, 0), (True, 1), (0, False), ('0', 0)):
            template = deepcopy(self.template)
            template['success']['equals'] = expected
            client = FakeClient({'code': actual, 'payment': {'all': 0}})
            with self.assertRaises(ValueError):
                quote_callback(client, self.catalog, template)(self.candidate)

    def test_yuan_conversion_exact_and_invalid_amounts_fail(self):
        template = deepcopy(self.template)
        template['total_unit'] = 'yuan'
        quote = quote_callback(FakeClient({'code': 0, 'payment': {'all': '73.11'}}, text=True), self.catalog, template)(self.candidate)
        self.assertEqual(quote['total_fen'], 7311)
        for amount in ('73.111', '-1', 'NaN', True, None):
            with self.assertRaises((ValueError, TypeError)):
                quote_callback(FakeClient({'code': 0, 'payment': {'all': amount}}), self.catalog, template)(self.candidate)

    def test_unconfirmed_fees_or_mock_source_rejected(self):
        template = deepcopy(self.template)
        template['total_includes_fees'] = False
        with self.assertRaises(ValueError):
            quote_callback(FakeClient(), self.catalog, template)
        with self.assertRaises(ValueError):
            quote_callback(FakeClient(), {'store': self.catalog['store'], 'source': {'mode': 'demo'}}, self.template)

    def test_unmapped_duplicate_or_invalid_quantity_never_sent(self):
        for items in ([], [{'id': 'missing', 'quantity': 1}],
                      [{'id': 'coffee-variant', 'quantity': 1}, {'id': 'coffee-variant', 'quantity': 1}],
                      [{'id': 'coffee-variant', 'quantity': True}]):
            client = FakeClient()
            with self.assertRaises(ValueError):
                quote_callback(client, self.catalog, self.template)({'id': 'test', 'items': items})
            self.assertFalse(client.calls)


if __name__ == '__main__':
    unittest.main()
