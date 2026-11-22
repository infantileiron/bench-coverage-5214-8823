"""按区段汇总一个批次的回传凭证，并把可估算区段的漏查算出来。

口径：
- 原始回传总数 = 计入 + 重复 + 待核 + 无效，四类互斥且覆盖每一条凭证；
- 只有“计入”的凭证进入双轮标签集合；重复、待核、无效全部排除，但保留在按区段明细里；
- 同一轮同一座椅只计一次，因此重试、整批重送不会抬高计数。
"""
from collections import defaultdict
from math import ceil

from src.constants.enums import CLASSES, CLASS_LABELS, COUNTED, DUPLICATE, INVALID, PENDING
from src.services.classification import category_of, tally

UNKNOWN_SEGMENT = '未识别区段'


def chapman(n_a, n_b, overlap):
    population = (n_a + 1) * (n_b + 1) / (overlap + 1) - 1
    variance = (n_a + 1) * (n_b + 1) * (n_a - overlap) * (n_b - overlap) / ((overlap + 1)**2 * (overlap + 2))
    return population, variance


def legacy_receipt(row):
    state = row.get('state', 'verified')
    return {
        'id': None, 'segment_code': row.get('segment_code', ''),
        'round_code': row.get('round_code', ''), 'asset_code': row.get('asset_code', ''),
        'state': state, 'capture_id': row.get('id'),
        'duplicate_of': None, 'submission_id': None,
        'note': '旧快照无回传凭证，按标签状态归类',
        'classification': COUNTED if state == 'verified' else PENDING if state == 'unverified' else INVALID,
    }


def normalise_snapshot(snapshot):
    """兼容早期只冻结 Capture 列表的快照。"""
    if isinstance(snapshot, dict):
        return {'captures': snapshot.get('captures') or [], 'receipts': snapshot.get('receipts') or [],
                'submissions': snapshot.get('submissions') or [], 'events': snapshot.get('events') or []}
    return {'captures': snapshot or [],
            'receipts': [legacy_receipt(row) for row in snapshot or []],
            'submissions': [], 'events': []}


def receipt_counts(receipts):
    counts = tally(receipts)
    counts['balanced'] = counts['received'] == counts[COUNTED] + counts[PENDING] + counts[DUPLICATE] + counts[INVALID]
    return counts


def _segments(receipts, config):
    segments = {row['code']: {'code': row['code'], 'title': row['title'],
                              'stable': bool(row['stable']), 'unconfigured': False}
                for row in config['segments']}
    for row in receipts:
        code = row.get('segment_code') or ''
        if code not in segments:
            segments.setdefault(UNKNOWN_SEGMENT, {'code': '—', 'title': UNKNOWN_SEGMENT,
                                                  'stable': False, 'unconfigured': True})
    return segments


def _label_sets(counted):
    a = {row['asset_code'] for row in counted if row['round_code'] == 'A'}
    b = {row['asset_code'] for row in counted if row['round_code'] == 'B'}
    return len(a), len(b), len(a & b), len(a | b)


def analyze(snapshot, config):
    ledger = normalise_snapshot(snapshot)
    receipts = ledger['receipts']
    segments = _segments(receipts, config)
    grouped = defaultdict(list)
    for row in receipts:
        code = row.get('segment_code') or ''
        if code in segments and not segments[code]['unconfigured']:
            grouped[code].append(row)
        else:
            grouped[UNKNOWN_SEGMENT].append(row)
    rows = []
    for code, segment in segments.items():
        group = grouped.get(code, [])
        counts = receipt_counts(group)
        counted = [row for row in group if category_of(row) == COUNTED]
        n_a, n_b, overlap, seen = _label_sets(counted)
        reasons = []
        if not config['verified_independence']:
            reasons.append('两轮独立性待确认')
        if segment['unconfigured']:
            reasons.append('区段不在本次估算范围')
        elif not segment['stable']:
            reasons.append('观察期有资产变更或围挡')
        if overlap < config['min_overlap']:
            reasons.append('重合数量低于门槛')
        if counts[PENDING]:
            reasons.append(f'存在 {counts[PENDING]} 条待核标签')
        if counts[INVALID]:
            reasons.append(f'存在 {counts[INVALID]} 条无效回传')
        population = missed = var = None
        if not reasons:
            population, var = chapman(n_a, n_b, overlap)
            missed = max(0., population - seen)
        rows.append({
            '区段编号': segment['code'],
            '区段': segment['title'],
            '原始回传': counts['received'],
            '计入': counts[COUNTED],
            '重复': counts[DUPLICATE],
            '待核': counts[PENDING],
            '无效': counts[INVALID],
            '排除': counts['excluded'],
            'A 轮': n_a,
            'B 轮': n_b,
            '重合': overlap,
            '合并实见': seen,
            '估计总量': round(population, 2) if population is not None else None,
            '估计漏查': round(missed, 2) if missed is not None else None,
            '估计方差': round(var, 2) if var is not None else None,
            '建议': '；'.join(reasons) if reasons else f'按约 {ceil(missed)} 处漏查安排补巡',
        })

    totals = receipt_counts(receipts)
    eligible = [row for row in rows if row['估计漏查'] is not None]
    pending = sorted((row for row in rows if row['估计漏查'] is None), key=lambda row: row['区段'])
    eligible.sort(key=lambda row: (-row['估计漏查'], row['区段']))
    summary = (f'先补巡 {eligible[0]["区段"]}，该区段估计漏查 {eligible[0]["估计漏查"]:.2f} 处。'
               if eligible and eligible[0]['估计漏查'] > 0 else '合格区段当前没有由估算触发的补巡。')
    if not eligible:
        summary = '当前所有区段均需先人工核对，暂缓按估算派单。'
    elif pending:
        summary += f' 另有 {len(pending)} 个区段需人工核对。'
    ordered = eligible + pending
    balanced = (totals['received'] == sum(row['原始回传'] for row in rows)
                == sum(row['计入'] + row['排除'] for row in rows))
    return {
        'metrics': {
            '原始回传总数': totals['received'],
            '计入': totals[COUNTED],
            '重复': totals[DUPLICATE],
            '待核': totals[PENDING],
            '无效': totals[INVALID],
            '排除合计': totals['excluded'],
            '四类对账': '已对平' if balanced else '不一致',
            '已确认实见': sum(row['合并实见'] for row in rows),
            '可估算区段': len(eligible),
            '人工复核区段': len(pending),
            '可估区段漏查合计': round(sum(row['估计漏查'] for row in eligible), 2),
        },
        'totals': totals,
        'balanced': balanced,
        'classes': [{'key': name, 'label': CLASS_LABELS[name], 'count': totals[name]} for name in CLASSES],
        'summary': summary,
        'rows': ordered,
        'submissions': ledger['submissions'],
        'events': ledger['events'],
        'notes': [
            '原始回传总数 = 计入 + 重复 + 待核 + 无效，每条回传凭证只归一类。',
            '同一轮同一座椅只计一次；重复登记与整批重送不新增计数，凭证仍保留可追溯。',
            '只有计入的标签进入双轮集合；待核照片补核或判无效后，重新锁定批次再估算。',
            '合计仅覆盖有可比观察且重合达标的区段，未估算区段保留在按区段明细中。',
        ],
    }
