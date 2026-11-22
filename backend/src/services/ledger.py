"""回传凭证台账。

每一条现场回传都先落成一张凭证（CaptureReceipt），再决定它是否计入：
- 计入：区段、轮次、座椅编号唯一，且标签已核；
- 重复：同一轮同一座椅已经登记，或整批重送；
- 待核：已登记但照片标签尚未确认；
- 无效：结构化字段不合法、区段未登记，或人工判为无效。

凭证只追加、不删除，因此原始回传总数始终能由四类数量相加核对。
"""
import re
from datetime import date, datetime
from hashlib import sha256
from json import dumps
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.constants.enums import CLASS_LABELS, CLASSES, COUNTED, DUPLICATE, INVALID, PENDING
from src.services.classification import category_of, tally
from src.models.entities import (
    Capture,
    CaptureEvent,
    CaptureReceipt,
    CaptureSubmission,
    Segment,
    User,
)

_SEGMENT = re.compile(r'^[A-Z0-9-]{2,32}$')
_ASSET = re.compile(r'^[A-Z0-9-]{2,40}$')
_ROUNDS = {'A', 'B'}
_STATES = {'verified', 'unverified'}


def now_text(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=' ', timespec='seconds')
    return value


def jsonable(value):
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return now_text(value)


def rows_record(obj, exclude=()):
    return {col.key: jsonable(getattr(obj, col.key)) for col in obj.__table__.columns if col.key not in exclude}


def _fingerprint(campaign, rows):
    payload = {
        'campaign': campaign,
        'rows': sorted(
            [(row.get('segment_code', ''), row.get('round_code', ''), row.get('asset_code', ''), row.get('state', ''))
             for row in rows]
        ),
    }
    return sha256(dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _clean(value):
    return value.strip() if isinstance(value, str) else value


def _validate(row, segments):
    row = {key: _clean(value) for key, value in row.items()}
    segment = row.get('segment_code') or ''
    cycle = row.get('round_code') or ''
    asset = row.get('asset_code') or ''
    state = row.get('state') or 'verified'
    if not _SEGMENT.fullmatch(segment):
        return row, f'区段编号格式不合法：{segment or "空"}'
    if segment not in segments:
        return row, f'区段未登记：{segment}'
    if cycle not in _ROUNDS:
        return row, f'轮次只能为 A 或 B：{cycle or "空"}'
    if not _ASSET.fullmatch(asset):
        return row, f'座椅编号格式不合法：{asset or "空"}'
    if state not in _STATES:
        return row, f'核对状态不合法：{state or "空"}'
    row['round_code'], row['asset_code'], row['state'] = cycle, asset, state
    return row, None


def classify(receipt, capture_state):
    """凭证分类；标签状态以当前台账为准，人工补核或判无效后即时反映。"""
    return category_of({
        'duplicate_of': receipt.duplicate_of,
        'capture_id': receipt.capture_id,
        'state': capture_state.get(receipt.capture_id),
    })


def _capture_states(db, campaign):
    return {row.id: row.state for row in db.scalars(select(Capture).where(Capture.campaign == campaign))}


def _first_receipt(db, capture_id):
    return db.scalar(select(CaptureReceipt).where(CaptureReceipt.capture_id == capture_id)
                     .order_by(CaptureReceipt.id).limit(1))


def _add_receipt(db, submission, segment_code, round_code, asset_code, state, capture_id,
                 duplicate_of=None, note=''):
    receipt = CaptureReceipt(segment_code=segment_code, campaign=submission.campaign,
                             round_code=round_code, asset_code=asset_code, state=state,
                             capture_id=capture_id, duplicate_of=duplicate_of,
                             submission_id=submission.id, note=note)
    db.add(receipt)
    db.flush()
    return receipt


def ensure_receipts(db, campaign):
    """为历史台账（先有 Capture、尚无凭证）补建回传凭证，保证原始条数不漏。"""
    missing = db.scalars(
        select(Capture).where(
            Capture.campaign == campaign,
            ~select(CaptureReceipt.id).where(CaptureReceipt.capture_id == Capture.id).exists(),
        ).order_by(Capture.id)
    ).all()
    if not missing:
        return 0
    actor_id = db.scalar(select(User.id).order_by(User.id).limit(1))
    if actor_id is None:
        return 0
    submission_id = f'legacy-{campaign}'
    submission = db.get(CaptureSubmission, submission_id)
    if submission is None:
        submission = CaptureSubmission(id=submission_id, campaign=campaign, client_submission_id=None,
                                       fingerprint=f'legacy-{campaign}', resend_of=None,
                                       actor_id=actor_id, item_count=0)
        db.add(submission)
        db.flush()
    for capture in missing:
        _add_receipt(db, submission, capture.segment_code, capture.round_code, capture.asset_code,
                     capture.state, capture.id, note='历史台账回溯补建凭证')
    submission.item_count = submission.item_count + len(missing)
    db.flush()
    return len(missing)


def ingest(db, user, campaign, rows, client_submission_id=None, resend_of=None):
    """登记一整批回传。逐条落凭证，任何一条都不丢弃。"""
    if client_submission_id:
        prior = db.scalar(select(CaptureSubmission).where(
            CaptureSubmission.campaign == campaign,
            CaptureSubmission.client_submission_id == client_submission_id,
        ).order_by(CaptureSubmission.received_at))
        if prior is not None:
            return submit_result(db, prior, idempotent=True)

    fingerprint = _fingerprint(campaign, rows)
    original = None
    if resend_of:
        original = db.get(CaptureSubmission, resend_of)
        if original is None or original.campaign != campaign:
            raise HTTPException(422, '重送指向的回传批次不存在')
    else:
        original = db.scalar(select(CaptureSubmission).where(
            CaptureSubmission.campaign == campaign,
            CaptureSubmission.fingerprint == fingerprint,
        ).order_by(CaptureSubmission.received_at, CaptureSubmission.id).limit(1))
    if original is not None and original.resend_of:
        original = db.get(CaptureSubmission, original.resend_of) or original

    submission = CaptureSubmission(id=uuid4().hex, campaign=campaign,
                                   client_submission_id=client_submission_id or None,
                                   fingerprint=fingerprint,
                                   resend_of=original.id if original else None,
                                   actor_id=user.id, item_count=len(rows))
    db.add(submission)
    db.flush()

    segments = set(db.scalars(select(Segment.code)))
    original_receipts = {}
    if original is not None:
        for receipt in db.scalars(select(CaptureReceipt).where(CaptureReceipt.submission_id == original.id)
                                  .order_by(CaptureReceipt.id)):
            original_receipts.setdefault(
                (receipt.segment_code, receipt.round_code, receipt.asset_code), receipt)

    for raw in rows:
        row, problem = _validate(raw, segments)
        segment_code = row.get('segment_code') or ''
        cycle = row.get('round_code') or ''
        asset = row.get('asset_code') or ''
        state = row.get('state') or 'verified'
        if problem:
            _add_receipt(db, submission, segment_code, cycle, asset, state, None, note=problem)
            continue
        key = (segment_code, cycle, asset)
        if original is not None and key in original_receipts:
            prior = original_receipts[key]
            reason = '整批重送，指向原回传凭证'
            if prior.note:
                reason += f'；原凭证说明：{prior.note}'
            _add_receipt(db, submission, segment_code, cycle, asset, state, prior.capture_id,
                         duplicate_of=prior.id, note=reason)
            continue
        existing = db.scalar(select(Capture).where(
            Capture.campaign == campaign, Capture.segment_code == segment_code,
            Capture.round_code == cycle, Capture.asset_code == asset))
        if existing is not None:
            prior = _first_receipt(db, existing.id)
            _add_receipt(db, submission, segment_code, cycle, asset, state, existing.id,
                         duplicate_of=prior.id if prior else None, note='同一轮同一座椅重复登记')
            continue
        capture = Capture(segment_code=segment_code, campaign=campaign, round_code=cycle,
                          asset_code=asset, state=state)
        try:
            with db.begin_nested():
                db.add(capture)
                db.flush()
        except IntegrityError:
            existing = db.scalar(select(Capture).where(
                Capture.campaign == campaign, Capture.segment_code == segment_code,
                Capture.round_code == cycle, Capture.asset_code == asset))
            prior = _first_receipt(db, existing.id) if existing else None
            _add_receipt(db, submission, segment_code, cycle, asset, state,
                         existing.id if existing else None,
                         duplicate_of=prior.id if prior else None, note='同一轮同一座椅重复登记')
            continue
        _add_receipt(db, submission, segment_code, cycle, asset, state, capture.id)

    db.flush()
    db.commit()
    return submit_result(db, submission, idempotent=False)


def submit_result(db, submission, idempotent=False):
    states = _capture_states(db, submission.campaign)
    receipts = db.scalars(select(CaptureReceipt).where(CaptureReceipt.submission_id == submission.id)
                          .order_by(CaptureReceipt.id)).all()
    rows = []
    for receipt in receipts:
        category = classify(receipt, states)
        rows.append({
            'receipt_id': receipt.id, 'segment_code': receipt.segment_code,
            'round_code': receipt.round_code, 'asset_code': receipt.asset_code,
            'state': receipt.state, 'capture_id': receipt.capture_id,
            'duplicate_of': receipt.duplicate_of, 'classification': category,
            'classification_label': CLASS_LABELS[category], 'reason': receipt.note,
        })
    counts = tally(rows)
    return {
        'submission': rows_record(submission),
        'idempotent': idempotent,
        'resend_of': submission.resend_of,
        'received': counts['received'],
        'counted': counts[COUNTED], 'duplicate': counts[DUPLICATE],
        'pending': counts[PENDING], 'invalid': counts[INVALID],
        'excluded': counts['excluded'], 'balanced': counts['balanced'],
        'rows': rows,
    }


def receipt_rows(db, campaign, segment_code=None):
    ensure_receipts(db, campaign)
    db.commit()
    query = select(CaptureReceipt).where(CaptureReceipt.campaign == campaign).order_by(CaptureReceipt.id)
    if segment_code:
        query = query.where(CaptureReceipt.segment_code == segment_code)
    states = _capture_states(db, campaign)
    out = []
    for receipt in db.scalars(query):
        category = classify(receipt, states)
        out.append({**rows_record(receipt), 'classification': category,
                    'classification_label': CLASS_LABELS[category]})
    return out


def counts(db, campaign, segment_code=None):
    rows = receipt_rows(db, campaign, segment_code)
    totals = tally(rows)
    if segment_code is None:
        grouped = {}
        for row in rows:
            grouped.setdefault(row['segment_code'], []).append(row)
        totals['segments'] = [{'segment_code': code, **tally(group)}
                              for code, group in sorted(grouped.items())]
    return totals


def submission_rows(db, campaign):
    """按凭证最早出现顺序排列回传批次，重送紧跟原批次。"""
    ensure_receipts(db, campaign)
    db.commit()
    states = _capture_states(db, campaign)
    submissions = {row.id: row for row in db.scalars(
        select(CaptureSubmission).where(CaptureSubmission.campaign == campaign))}
    receipts = db.scalars(select(CaptureReceipt).where(CaptureReceipt.campaign == campaign)
                          .order_by(CaptureReceipt.id)).all()
    buckets = {submission_id: {name: 0 for name in CLASSES} for submission_id in submissions}
    first_seen = {}
    for receipt in receipts:
        if receipt.submission_id in buckets:
            buckets[receipt.submission_id][classify(receipt, states)] += 1
            first_seen.setdefault(receipt.submission_id, receipt.id)
    out = []
    for submission_id in sorted(buckets, key=lambda key: first_seen.get(key, 0)):
        submission = submissions[submission_id]
        if submission is None:
            continue
        item = rows_record(submission)
        item.update({**buckets[submission_id], 'received': sum(buckets[submission_id].values()),
                     'excluded': sum(buckets[submission_id][name] for name in (DUPLICATE, PENDING, INVALID))})
        out.append(item)
    return out


def events(db, campaign):
    ids = [row.id for row in db.scalars(select(Capture).where(Capture.campaign == campaign))]
    if not ids:
        return []
    captures = {row.id: row for row in db.scalars(select(Capture).where(Capture.id.in_(ids)))}
    out = []
    for event in db.scalars(select(CaptureEvent).where(CaptureEvent.capture_id.in_(ids))
                            .order_by(CaptureEvent.recorded_at, CaptureEvent.id)):
        capture = captures.get(event.capture_id)
        item = rows_record(event)
        item.update({
            'segment_code': capture.segment_code if capture else '',
            'round_code': capture.round_code if capture else '',
            'asset_code': capture.asset_code if capture else '',
        })
        out.append(item)
    return out


def snapshot(db, campaign):
    """冻结提交时的凭证、标签状态与核对轨迹。"""
    ensure_receipts(db, campaign)
    db.flush()
    captures = [rows_record(row) for row in db.scalars(
        select(Capture).where(Capture.campaign == campaign).order_by(Capture.id))]
    states = {row['id']: row['state'] for row in captures}
    receipts = []
    for payload in receipt_rows(db, campaign):
        receipt_id = payload['id']
        category = payload['classification']
        receipts.append({
            'id': receipt_id, 'segment_code': payload['segment_code'],
            'round_code': payload['round_code'], 'asset_code': payload['asset_code'],
            'state': payload['state'], 'capture_id': payload['capture_id'],
            'duplicate_of': payload['duplicate_of'], 'submission_id': payload['submission_id'],
            'note': payload['note'], 'received_at': payload['received_at'],
            'classification': category, 'classification_label': CLASS_LABELS[category],
        })
    return {
        'captures': captures,
        'receipts': receipts,
        'submissions': submission_rows(db, campaign),
        'events': events(db, campaign),
        'capture_states': states,
    }


def verify(db, capture_id, user, state='verified', note=''):
    capture = db.scalar(select(Capture).where(Capture.id == capture_id).with_for_update())
    if capture is None:
        raise HTTPException(404, '找不到待核标签')
    if state not in _STATES and state != 'invalid':
        raise HTTPException(422, '核对结果只能是确认或无效')
    if capture.state == state:
        return capture, None
    previous = capture.state
    capture.state = state
    event = CaptureEvent(capture_id=capture.id, action='verify' if state == 'verified' else 'invalidate',
                         from_state=previous, to_state=state, actor_id=user.id,
                         note=note or ('确认同一实物编号' if state == 'verified' else '人工判为无效'))
    db.add(event)
    db.commit()
    return capture, event
