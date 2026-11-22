"""回传凭证的四类互斥归类。

规则只依赖凭证自身的字段，页面、导出、估算三处共用，避免口径漂移。
"""
from src.constants.enums import CLASSES, COUNTED, DUPLICATE, INVALID, PENDING

CAPTURE_STATES = {'verified', 'unverified', 'invalid'}


def category_of(row):
    """返回 counted / duplicate / pending / invalid 之一。"""
    category = row.get('classification')
    if category in CLASSES:
        return category
    if row.get('duplicate_of') is not None:
        return DUPLICATE
    if row.get('capture_id') is None:
        return INVALID
    state = row.get('state')
    if state not in CAPTURE_STATES or state == 'invalid':
        return INVALID
    if state == 'unverified':
        return PENDING
    return COUNTED


def tally(rows):
    counts = {name: 0 for name in CLASSES}
    for row in rows:
        counts[category_of(row)] += 1
    counts['received'] = len(rows)
    counts['excluded'] = counts[DUPLICATE] + counts[PENDING] + counts[INVALID]
    counts['balanced'] = counts['received'] == counts[COUNTED] + counts['excluded']
    return counts
