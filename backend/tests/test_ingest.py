import os
import tempfile
import unittest

_DB = tempfile.NamedTemporaryFile(suffix='.db', delete=False).name
os.environ.setdefault('DATABASE_URL', f'sqlite:///{_DB}')
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6399/0')
os.environ.setdefault('JWT_SECRET', 'test-secret-that-is-long-enough-000')

from src.config.database import Base, Session, engine  # noqa: E402
from src.models.entities import Capture, Segment, User  # noqa: E402
from src.services import ledger  # noqa: E402


def row(segment, cycle, asset, state='verified'):
    return {'segment_code': segment, 'round_code': cycle, 'asset_code': asset, 'state': state}


class IngestTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with Session() as db:
            db.add(User(email='analyst@test', role='analyst', password_hash='x'))
            db.add_all([Segment(code='BANK-01', title='柳渡段', bank='南岸', stable=True),
                        Segment(code='BANK-02', title='石桥段', bank='北岸', stable=True)])
            db.commit()
            self.user = db.query(User).first()

    def ingest(self, campaign, rows, **kwargs):
        with Session() as db:
            user = db.get(User, self.user.id)
            return ledger.ingest(db, user, campaign, rows, **kwargs)

    def test_duplicate_is_recorded_not_rejected(self):
        first = self.ingest('C1', [row('BANK-01', 'A', 'S-1')])
        self.assertEqual((first['received'], first['counted']), (1, 1))
        second = self.ingest('C1', [row('BANK-01', 'A', 'S-1')])
        self.assertEqual(second['received'], 1)
        self.assertEqual(second['duplicate'], 1)
        self.assertEqual(second['counted'], 0)
        self.assertEqual(second['rows'][0]['duplicate_of'], first['rows'][0]['receipt_id'])
        with Session() as db:
            self.assertEqual(db.query(Capture).count(), 1)

    def test_retry_with_same_client_id_adds_nothing(self):
        rows = [row('BANK-01', 'A', 'S-1'), row('BANK-01', 'B', 'S-1')]
        first = self.ingest('C1', rows, client_submission_id='dev-1')
        again = self.ingest('C1', rows, client_submission_id='dev-1')
        self.assertTrue(again['idempotent'])
        self.assertEqual(again['submission']['id'], first['submission']['id'])
        with Session() as db:
            self.assertEqual(len(ledger.receipt_rows(db, 'C1')), 2)

    def test_whole_batch_resend_is_linked_to_original(self):
        rows = [row('BANK-01', 'A', 'S-1'), row('BANK-01', 'B', 'S-1')]
        first = self.ingest('C1', rows)
        resent = self.ingest('C1', rows)
        self.assertEqual(resent['resend_of'], first['submission']['id'])
        self.assertEqual(resent['duplicate'], 2)
        self.assertEqual(resent['counted'], 0)
        self.assertEqual(resent['received'], 2)
        with Session() as db:
            counts = ledger.counts(db, 'C1')
        self.assertEqual(counts['received'], 4)
        self.assertTrue(counts['balanced'])

    def test_malformed_rows_become_invalid_receipts(self):
        result = self.ingest('C1', [
            row('BANK-01', 'A', 'S-1'),
            row('BANK-99', 'A', 'S-2'),
            row('BANK-01', 'Z', 'S-3'),
            row('BANK-01', 'A', 'has space'),
            row('BANK-01', 'A', 'S-4', 'weird'),
        ])
        self.assertEqual(result['received'], 5)
        self.assertEqual(result['counted'], 1)
        self.assertEqual(result['invalid'], 4)
        self.assertTrue(result['balanced'])

    def test_reverify_moves_receipt_between_classes(self):
        result = self.ingest('C1', [row('BANK-01', 'A', 'S-1', 'unverified')])
        self.assertEqual(result['pending'], 1)
        capture_id = result['rows'][0]['capture_id']
        with Session() as db:
            ledger.verify(db, capture_id, db.get(User, self.user.id), state='verified', note='照片确认')
            self.assertEqual(ledger.counts(db, 'C1')['counted'], 1)
            ledger.verify(db, capture_id, db.get(User, self.user.id), state='invalid', note='不是座椅')
            counts = ledger.counts(db, 'C1')
        self.assertEqual(counts['invalid'], 1)
        self.assertEqual(counts['received'], 1)
        with Session() as db:
            trace = ledger.events(db, 'C1')
        self.assertEqual([item['to_state'] for item in trace], ['verified', 'invalid'])

    def test_history_without_receipts_is_backfilled_once(self):
        with Session() as db:
            db.add(Capture(segment_code='BANK-01', campaign='C1', round_code='A',
                           asset_code='S-1', state='verified'))
            db.commit()
            self.assertEqual(ledger.ensure_receipts(db, 'C1'), 1)
            db.commit()
            self.assertEqual(ledger.ensure_receipts(db, 'C1'), 0)
            self.assertEqual(ledger.counts(db, 'C1')['counted'], 1)


if __name__ == '__main__':
    unittest.main()
