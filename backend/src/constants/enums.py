from enum import StrEnum


class Role(StrEnum):
    MANAGER = 'manager'
    ANALYST = 'analyst'
    VIEWER = 'viewer'


class Status(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    PUBLISHED = 'published'


class CaptureClass(StrEnum):
    COUNTED = 'counted'
    DUPLICATE = 'duplicate'
    PENDING = 'pending'
    INVALID = 'invalid'


CLASSES = tuple(item.value for item in CaptureClass)
COUNTED, DUPLICATE, PENDING, INVALID = CLASSES
CLASS_LABELS = {COUNTED: '计入', DUPLICATE: '重复', PENDING: '待核', INVALID: '无效'}
