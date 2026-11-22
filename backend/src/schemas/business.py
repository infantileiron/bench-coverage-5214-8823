from typing import Literal
from pydantic import BaseModel, Field


class CleanInput(BaseModel, str_strip_whitespace=True, extra='forbid'):
    pass


class Login(CleanInput):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class SegmentInput(CleanInput):
    code: str = Field(pattern=r'^[A-Z0-9-]{2,32}$')
    title: str = Field(min_length=2, max_length=100)
    bank: str = Field(min_length=2, max_length=40)
    stable: bool


class CaptureInput(CleanInput):
    """单条登记也走凭证台账：字段不合法时落为“无效”凭证，而不是整条丢失。"""
    segment_code: str = Field(max_length=64)
    campaign: str = Field(pattern=r'^[A-Z0-9-]{2,32}$')
    round_code: str = Field(default='', max_length=8)
    asset_code: str = Field(default='', max_length=80)
    state: str = Field(default='verified', max_length=16)


class CaptureRow(CleanInput):
    """整批回传的原始行。字段只做宽松收口，不合格的行落为“无效”凭证，不整批丢弃。"""
    segment_code: str = Field(default='', max_length=64)
    round_code: str = Field(default='', max_length=8)
    asset_code: str = Field(default='', max_length=80)
    state: str = Field(default='verified', max_length=16)


class CaptureBatchInput(CleanInput):
    campaign: str = Field(pattern=r'^[A-Z0-9-]{2,32}$')
    rows: list[CaptureRow] = Field(min_length=1, max_length=1000)
    client_submission_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9._:-]{1,64}$')
    resend_of: str | None = Field(default=None, max_length=64)


class CaptureDecision(CleanInput):
    state: Literal['verified', 'invalid'] = 'verified'
    note: str = Field(default='', max_length=300)


class AnalysisInput(CleanInput):
    title: str = Field(min_length=2, max_length=100)
    campaign: str = Field(pattern=r'^[A-Z0-9-]{2,32}$')
    min_overlap: int = Field(ge=3, le=200, default=5)
    verified_independence: bool


class Publication(CleanInput):
    note: str = Field(min_length=3, max_length=400)
