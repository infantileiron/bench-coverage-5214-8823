import unittest

from src.services.classification import category_of, tally
from src.services.coverage import analyze


_UNSET = object()


def receipt(index, cycle='A', asset='S-1', state='verified', segment='BANK-01',
            classification=None, duplicate_of=None, capture_id=_UNSET):
    return {
        'id': index, 'segment_code': segment, 'round_code': cycle, 'asset_code': asset,
        'state': state, 'capture_id': index if capture_id is _UNSET else capture_id,
        'duplicate_of': duplicate_of, 'submission_id': 'sub-1', 'note': '',
        'classification': classification,
    }


CONFIG = {
    'campaign': 'CASE', 'min_overlap': 2, 'verified_independence': True,
    'segments': [{'code': 'BANK-01', 'title': '柳渡段', 'bank': '南岸', 'stable': True}],
}


class ClassificationTest(unittest.TestCase):
    def test_duplicate_wins_over_state(self):
        row = receipt(1, duplicate_of=0, state='verified')
        self.assertEqual(category_of(row), 'duplicate')

    def test_unverified_is_pending_and_invalid_state_is_invalid(self):
        self.assertEqual(category_of(receipt(1, state='unverified')), 'pending')
        self.assertEqual(category_of(receipt(1, state='invalid')), 'invalid')
        self.assertEqual(category_of(receipt(1, capture_id=None)), 'invalid')

    def test_tally_keeps_every_receipt(self):
        rows = [
            receipt(1), receipt(2, 'B', 'S-2'),
            receipt(3, duplicate_of=1),
            receipt(4, 'B', 'S-3', 'unverified'),
            receipt(5, 'A', 'bad', classification='invalid', capture_id=None),
        ]
        counts = tally(rows)
        self.assertEqual(counts['received'], 5)
        self.assertEqual((counts['counted'], counts['duplicate'], counts['pending'], counts['invalid']),
                         (2, 1, 1, 1))
        self.assertTrue(counts['balanced'])


class AnalyzeTest(unittest.TestCase):
    def build(self):
        rows = [
            # 计入：两轮各 3 个标签，重合 2 个
            receipt(1, 'A', 'S-1'), receipt(2, 'A', 'S-2'), receipt(3, 'A', 'S-3'),
            receipt(4, 'B', 'S-2'), receipt(5, 'B', 'S-3'), receipt(6, 'B', 'S-4'),
            # 排除：重复、待核、无效
            receipt(7, 'A', 'S-1', duplicate_of=1),
            receipt(8, 'B', 'S-9', 'unverified'),
            receipt(9, 'B', 'bad', classification='invalid', capture_id=None),
        ]
        return {'captures': [], 'receipts': rows, 'submissions': [], 'events': []}

    def test_four_classes_reconcile_to_raw_total(self):
        output = analyze(self.build(), CONFIG)
        metrics = output['metrics']
        self.assertEqual(metrics['原始回传总数'], 9)
        self.assertEqual(metrics['计入'], 6)
        self.assertEqual(metrics['重复'], 1)
        self.assertEqual(metrics['待核'], 1)
        self.assertEqual(metrics['无效'], 1)
        self.assertEqual(metrics['排除合计'], 3)
        self.assertEqual(metrics['四类对账'], '已对平')
        self.assertTrue(output['balanced'])

    def test_only_counted_labels_enter_estimate(self):
        output = analyze(self.build(), CONFIG)
        row = output['rows'][0]
        self.assertEqual((row['A 轮'], row['B 轮'], row['重合'], row['合并实见']), (3, 3, 2, 4))
        # 待核/无效让区段进入人工复核，但仍保留全部数字
        self.assertIsNone(row['估计漏查'])
        self.assertIn('待核', row['建议'])
        self.assertIn('无效', row['建议'])
        self.assertEqual((row['原始回传'], row['计入'], row['排除']), (9, 6, 3))

    def test_retry_does_not_inflate_counts(self):
        snapshot = self.build()
        once = analyze(snapshot, CONFIG)['metrics']
        retried = analyze(snapshot, CONFIG)['metrics']
        self.assertEqual(once, retried)

    def test_legacy_capture_list_still_classifies(self):
        legacy = [
            {'id': 1, 'segment_code': 'BANK-01', 'round_code': 'A', 'asset_code': 'S-1', 'state': 'verified'},
            {'id': 2, 'segment_code': 'BANK-01', 'round_code': 'B', 'asset_code': 'S-2', 'state': 'unverified'},
        ]
        output = analyze(legacy, CONFIG)
        metrics = output['metrics']
        self.assertEqual((metrics['原始回传总数'], metrics['计入'], metrics['待核']), (2, 1, 1))
        self.assertTrue(output['balanced'])


if __name__ == '__main__':
    unittest.main()
