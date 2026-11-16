#!/usr/bin/env python3
"""Deterministic as-of-date contractor payment report. Standard library only."""
import argparse
import csv
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

ZERO = Decimal('0.00')


def money(value):
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f'Invalid money amount: {value!r}') from exc
    if not result.is_finite() or result < 0 or result.as_tuple().exponent < -2:
        raise ValueError(f'Invalid nonnegative kopeck amount: {value!r}')
    return result.quantize(Decimal('0.01'))


def read_csv(path, fields):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not set(fields).issubset(reader.fieldnames):
            raise ValueError(f'Invalid columns in {path}')
        return list(reader)


def parse_date(value, field, required=False):
    if not value:
        if required:
            raise ValueError(f'Missing {field}')
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f'Invalid {field}: {value}') from exc


def generate(data_dir, as_of):
    contracts = read_csv(data_dir / 'contracts.csv', ['contract_id','contractor','amount','signed_at'])
    works = read_csv(data_dir / 'works.csv', ['work_id','contract_id','title','price','status','completed_at','cancelled_at'])
    payments = read_csv(data_dir / 'payments.csv', ['payment_id','contract_id','work_id','purpose','amount','status','created_at','paid_at','refunded_at'])
    ids = set()
    contract_by_id = {}
    for c in contracts:
        cid = c['contract_id']
        if not cid or cid in ids:
            raise ValueError(f'Duplicate/empty contract ID: {cid}')
        ids.add(cid)
        c['amount_value'] = money(c['amount'])
        c['signed_date'] = parse_date(c['signed_at'], 'signed_at', True)
        contract_by_id[cid] = c
    work_by_id = {}
    by_contract = defaultdict(list)
    for w in works:
        wid, cid = w['work_id'], w['contract_id']
        if not wid or wid in work_by_id or cid not in contract_by_id:
            raise ValueError(f'Invalid work ID or contract: {wid}, {cid}')
        if w['status'] not in ('planned','done','cancelled'):
            raise ValueError(f'Unknown work status: {w["status"]}')
        w['price_value'] = money(w['price'])
        w['completed_date'] = parse_date(w['completed_at'], 'completed_at')
        w['cancelled_date'] = parse_date(w['cancelled_at'], 'cancelled_at')
        if w['status'] == 'done' and not w['completed_date']:
            raise ValueError(f'Missing completion date: {wid}')
        if w['status'] == 'cancelled' and not w['cancelled_date']:
            raise ValueError(f'Missing cancellation date: {wid}')
        # A snapshot cannot establish when a currently planned work was created.
        w['asof_status'] = ('cancelled' if w['cancelled_date'] and w['cancelled_date'] <= as_of
                           else 'done' if w['completed_date'] and w['completed_date'] <= as_of
                           else 'planned')
        work_by_id[wid] = w
        by_contract[cid].append(w)
    allocated = defaultdict(lambda: ZERO)
    unassigned = defaultdict(lambda: ZERO)
    payment_ids = set()
    for p in payments:
        pid, cid, wid = p['payment_id'], p['contract_id'], p['work_id']
        if not pid or pid in payment_ids or cid not in contract_by_id:
            raise ValueError(f'Invalid payment ID or contract: {pid}, {cid}')
        payment_ids.add(pid)
        if wid and (wid not in work_by_id or work_by_id[wid]['contract_id'] != cid):
            raise ValueError(f'Payment {pid} references unrelated work {wid}')
        if p['status'] not in ('paid','queued','refunded'):
            raise ValueError(f'Unknown payment status: {p["status"]}')
        amount = money(p['amount'])
        paid = parse_date(p['paid_at'], 'paid_at')
        refunded = parse_date(p['refunded_at'], 'refunded_at')
        if p['status'] in ('paid','refunded') and not paid:
            raise ValueError(f'Missing paid_at: {pid}')
        if p['status'] == 'refunded' and not refunded:
            raise ValueError(f'Missing refunded_at: {pid}')
        if paid and refunded and refunded < paid:
            raise ValueError(f'Refund precedes payment: {pid}')
        if not paid or paid > as_of or (refunded and refunded <= as_of):
            continue
        if wid:
            allocated[wid] += amount
        else:
            unassigned[cid] += amount
    work_rows = []
    contract_rows = []
    for c in contracts:
        cid = c['contract_id']
        items = by_contract[cid]
        # Direct payments stay linked, even if the work was cancelled.
        # Unassigned payments fill completed works first, then planned works.
        eligible = sorted((w for w in items if w['asof_status'] != 'cancelled'),
                          key=lambda w: (w['asof_status'] != 'done', w['completed_date'] or date.max, w['work_id']))
        pool = unassigned[cid]
        for w in eligible:
            capacity = max(ZERO, w['price_value'] - allocated[w['work_id']])
            portion = min(pool, capacity)
            allocated[w['work_id']] += portion
            pool -= portion
        total_paid = sum((allocated[w['work_id']] for w in items), ZERO) + pool
        active_price = sum((w['price_value'] for w in items if w['asof_status'] != 'cancelled'), ZERO)
        for w in items:
            paid_amount = allocated[w['work_id']]
            payable = ZERO if w['asof_status'] == 'cancelled' else max(ZERO, w['price_value'] - paid_amount)
            work_rows.append({'contract_id':cid, 'work_id':w['work_id'], 'title':w['title'],
                              'status_as_of':w['asof_status'], 'price':str(w['price_value']),
                              'paid':str(paid_amount), 'remaining':str(payable),
                              'overpaid':str(max(ZERO, paid_amount - w['price_value']))})
        contract_rows.append({'contract_id':cid, 'contractor':c['contractor'],
                              'contract_amount':str(c['amount_value']), 'active_works_price':str(active_price),
                              'paid_total':str(total_paid), 'paid_unallocated':str(pool),
                              'remaining_by_contract':str(max(ZERO, c['amount_value'] - total_paid)),
                              'remaining_by_active_works':str(sum((Decimal(w['remaining']) for w in work_rows if w['contract_id'] == cid), ZERO)),
                              'contract_vs_active_works':str(c['amount_value'] - active_price),
                              'overpaid_contract':str(max(ZERO, total_paid - c['amount_value']))})
    return work_rows, contract_rows


def write_csv(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description='Contractor payments report as of a date (inclusive)')
    parser.add_argument('--date', required=True, help='YYYY-MM-DD')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parent / 'data')
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parent / 'reports')
    args = parser.parse_args()
    as_of = parse_date(args.date, 'date', True)
    works, contracts = generate(args.data_dir, as_of)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / 'works.csv', works, ['contract_id','work_id','title','status_as_of','price','paid','remaining','overpaid'])
    write_csv(args.output_dir / 'contracts.csv', contracts, ['contract_id','contractor','contract_amount','active_works_price','paid_total','paid_unallocated','remaining_by_contract','remaining_by_active_works','contract_vs_active_works','overpaid_contract'])
    print(f'Generated {args.output_dir / "works.csv"} and {args.output_dir / "contracts.csv"} as of {as_of}')


if __name__ == '__main__':
    main()
