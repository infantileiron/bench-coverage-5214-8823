from fastapi import APIRouter, Depends
from sqlalchemy import select

from src.config.database import get_db
from src.controllers.auth import current, login, manager, writable
from src.controllers.export import export
from src.models.entities import Analysis, Audit, Capture, Segment
from src.schemas.business import (
    AnalysisInput,
    CaptureBatchInput,
    CaptureDecision,
    CaptureInput,
    Login,
    Publication,
    SegmentInput,
)
from src.services import business as svc
from src.services import ledger

router = APIRouter(prefix='/api')


@router.post('/auth/login')
def sign_in(data: Login, db=Depends(get_db)):
    return login(data, db)


@router.get('/auth/me')
def me(user=Depends(current)):
    return {'email': user.email, 'role': user.role}


@router.get('/segments')
def segments(db=Depends(get_db), user=Depends(current)):
    return [svc.record(item) for item in db.scalars(select(Segment).order_by(Segment.code))]


@router.post('/segments', status_code=201)
def create_segment(data: SegmentInput, db=Depends(get_db), user=Depends(writable)):
    return svc.save_input(db, Segment, data, user)


@router.get('/captures')
def captures(campaign: str | None = None, segment_code: str | None = None,
             classification: str | None = None, db=Depends(get_db), user=Depends(current)):
    if not campaign:
        return [svc.record(item) for item in db.scalars(select(Capture).order_by(Capture.id.desc()))]
    rows = ledger.receipt_rows(db, campaign, segment_code)
    if classification:
        rows = [row for row in rows if row['classification'] == classification]
    return rows


@router.get('/captures/summary')
def summary(campaign: str, segment_code: str | None = None, db=Depends(get_db), user=Depends(current)):
    return ledger.counts(db, campaign, segment_code)


@router.get('/captures/submissions')
def submissions(campaign: str, db=Depends(get_db), user=Depends(current)):
    return {'submissions': ledger.submission_rows(db, campaign),
            'totals': ledger.counts(db, campaign)}


@router.get('/captures/events')
def capture_events(campaign: str, db=Depends(get_db), user=Depends(current)):
    return ledger.events(db, campaign)


@router.post('/captures', status_code=201)
def create_capture(data: CaptureInput, db=Depends(get_db), user=Depends(writable)):
    return svc.save_input(db, 'capture', data, user)


@router.post('/captures/batch', status_code=201)
def create_capture_batch(data: CaptureBatchInput, db=Depends(get_db), user=Depends(writable)):
    return ledger.ingest(db, user, data.campaign, [row.model_dump() for row in data.rows],
                         client_submission_id=data.client_submission_id, resend_of=data.resend_of)


@router.post('/captures/{id}/verify')
def verify_capture(id: int, data: CaptureDecision | None = None, db=Depends(get_db), user=Depends(writable)):
    decision = data or CaptureDecision()
    return svc.verify_capture(db, id, user, state=decision.state, note=decision.note)


@router.get('/analyses')
def analyses(db=Depends(get_db), user=Depends(current)):
    return [svc.record(item, ('snapshot', 'result')) for item in db.scalars(select(Analysis).order_by(Analysis.id.desc()))]


@router.post('/analyses', status_code=202)
def enqueue(data: AnalysisInput, db=Depends(get_db), user=Depends(writable)):
    return svc.submit(db, data, user)


@router.get('/analyses/{id}')
def analysis(id: int, db=Depends(get_db), user=Depends(current)):
    return svc.record(svc.get_analysis(db, id), ('snapshot',))


@router.get('/analyses/{id}/export')
def download(id: int, db=Depends(get_db), user=Depends(current)):
    return export(svc.get_analysis(db, id))


@router.post('/analyses/{id}/publish')
def publish(id: int, data: Publication, db=Depends(get_db), user=Depends(manager)):
    return svc.publish(db, id, data.note, user)


@router.get('/audits')
def audit_log(db=Depends(get_db), user=Depends(manager)):
    return [svc.record(item) for item in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(100))]
