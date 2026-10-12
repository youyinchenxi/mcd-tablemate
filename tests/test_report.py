from copy import deepcopy
import unittest

from maimai_team.pricing import verify_candidates
from maimai_team.report import markdown
from maimai_team.server import demo_data, demo_plan


class ReportTests(unittest.TestCase):
    def test_estimate_is_never_described_as_verified_price(self):
        result = demo_plan(demo_data()['request'])
        report = markdown(result)
        self.assertIn('非实时门店价格', report)
        self.assertNotIn('整单核算实付', report)
        self.assertIn('预算余量', report)
        self.assertNotIn('require MCP verification', report)

    def test_verified_report_has_context_shares_and_no_pending_quote_warning(self):
        request = demo_data()['request']
        result = demo_plan(request)
        result['candidates'] = result['candidates'][:1]
        # Synthetic callback for output regression; no real MCP request.
        quote = lambda _c: {'total_fen': 7001, 'evidence': {
            'source': 'mcp', 'tool': 'calculate-price', 'store_id': 'synthetic-store',
            'verified_at': '2026-10-13T00:00:00Z', 'total_includes_fees': True,
            'meal_mode': 'synthetic-takeaway'}}
        checked = verify_candidates(result, request, quote)
        report = markdown(checked)
        self.assertIn('整单核算实付：¥70.01', report)
        self.assertIn('预算余量：¥9.99', report)
        self.assertIn('synthetic-store', report)
        self.assertIn('2026-10-13T00:00:00Z', report)
        self.assertIn('synthetic-takeaway', report)
        self.assertIn('分摊建议', report)
        self.assertNotIn('整单实付须由 MCP 核验', report)
        self.assertEqual(sum(s['share_fen'] for s in checked['candidates'][0]['shares']), 7001)

    def test_failed_quote_report_cannot_show_estimated_candidate_as_actual(self):
        request = demo_data()['request']
        checked = verify_candidates(demo_plan(request), request, lambda _c: {'total_fen': 1})
        report = markdown(checked)
        self.assertIn('暂无', report)
        self.assertNotIn('## 方案', report)
        self.assertNotIn('整单核算实付', report)


if __name__ == '__main__':
    unittest.main()
