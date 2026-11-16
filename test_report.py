import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from report import generate, money

DATA = Path(__file__).parent / 'data'

class ReportTests(unittest.TestCase):
    def test_snapshot(self):
        works, contracts = generate(DATA, date(2026,11,10))
        c = {r['contract_id']:r for r in contracts}
        w = {r['work_id']:r for r in works}
        self.assertEqual(c['D-1']['paid_total'], '315000.00')
        self.assertEqual(c['D-1']['remaining_by_contract'], '285000.00')
        self.assertEqual(c['D-1']['contract_vs_active_works'], '100000.00')
        self.assertEqual(c['D-2']['paid_total'], '75000.00')
        self.assertEqual(c['D-2']['remaining_by_contract'], '15000.00')
        self.assertEqual(w['W-13']['remaining'], '0.00')
        self.assertEqual(w['W-14']['paid'], '100000.00')
        self.assertEqual(w['W-23']['status_as_of'], 'planned')
        self.assertEqual(w['W-23']['paid'], '25000.00')
    def test_before_refund_and_after_future_payment(self):
        _, c1 = generate(DATA, date(2026,9,5))
        self.assertEqual(c1[0]['paid_total'], '350000.00')
        _, c2 = generate(DATA, date(2026,11,12))
        self.assertEqual(c2[1]['paid_total'], '95000.00')
        self.assertEqual(c2[1]['overpaid_contract'], '5000.00')
    def test_money_rejects_fractional_kopecks(self):
        for value in ['1.001', '-1.00', 'NaN', 'Infinity']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                money(value)
    def test_no_double_count(self):
        for day in [date(2026,9,5), date(2026,11,10), date(2026,11,12)]:
            works, contracts = generate(DATA, day)
            for c in contracts:
                allocated = sum((Decimal(w['paid']) for w in works if w['contract_id'] == c['contract_id']), Decimal('0'))
                self.assertEqual(allocated + Decimal(c['paid_unallocated']), Decimal(c['paid_total']))

if __name__ == '__main__':
    unittest.main()
