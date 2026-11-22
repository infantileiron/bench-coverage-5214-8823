from fastapi import HTTPException
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.config.settings import REDIS_URL
from src.constants.enums import Status
from src.models.entities import Analysis, Audit, Segment

queue = Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
QUEUE = 'bench-coverage:notifications'


def record(obj, exclude=()):
    from src.services.ledger import rows_record
    return rows_record(obj, exclude)


def audit(db, user, action, subject):
    db.add(Audit(user_id=user.id, action=action, subject=str(subject)))


def save_input(db, model, data, user):
    """单条登记：区段直接写入；座椅回传走凭证台账，重复不再报错而是留痕。"""
    if model is Segment:
        item = Segment(**data.model_dump())
        db.add(item)
        audit(db, user, 'create', data.code)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, '区段编号已经登记') from exc
        return record(item)
    from src.services import ledger
    payload = data.model_dump() if hasattr(data, 'model_dump') else dict(data)
    campaign = payload.pop('campaign')
    result = ledger.ingest(db, user, campaign, [payload])
    receipt = result['rows'][0]
    return {**payload, 'campaign': campaign, 'id': receipt['receipt_id'],
            'capture_id': receipt['capture_id'], 'classification': receipt['classification'],
            'classification_label': receipt['classification_label'],
            'duplicate_of': receipt['duplicate_of'], 'reason': receipt['reason'],
            **{key: result[key] for key in ('received', 'counted', 'duplicate', 'pending', 'invalid', 'excluded')}}


def verify_capture(db, id, user, state='verified', note=''):
    from src.services import ledger
    capture, event = ledger.verify(db, id, user, state=state, note=note)
    audit(db, user, 'verify_capture' if state == 'verified' else 'invalidate_capture',
          f'{capture.segment_code}/{capture.asset_code}')
    db.commit()
    return {**record(capture), 'event': record(event) if event else None}


def submit(db, data, user):
    from src.services import ledger
    segments = [record(row) for row in db.scalars(select(Segment).order_by(Segment.code))]
    if not segments:
        raise HTTPException(422, '请先登记巡查区段')
    snapshot = ledger.snapshot(db, data.campaign)
    if not snapshot['receipts']:
        raise HTTPException(422, '该巡查批次还没有记录')
    parameters = {**data.model_dump(exclude={'title'}), 'segments': segments}
    item = Analysis(title=data.title, status=Status.QUEUED, parameters=parameters,
                    snapshot=snapshot, author_id=user.id, result=None)
    db.add(item)
    db.flush()
    audit(db, user, 'queue', item.id)
    db.commit()
    try:
        queue.lpush(QUEUE, item.id)
    except RedisError:
        pass
    return record(item, ('snapshot',))


def get_analysis(db, id):
    item = db.get(Analysis, id)
    if not item:
        raise HTTPException(404, '找不到补巡单')
    return item


def publish(db, id, note, user):
    item = db.scalar(select(Analysis).where(Analysis.id == id).with_for_update())
    if not item:
        raise HTTPException(404, '找不到补巡单')
    if item.status != Status.COMPLETED:
        raise HTTPException(409, '只有已完成的补巡单可签核')
    item.status = Status.PUBLISHED
    item.note = note
    audit(db, user, 'publish', id)
    db.commit()
    return record(item, ('snapshot',))
