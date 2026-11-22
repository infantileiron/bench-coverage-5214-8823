import csv
import io
from fastapi import HTTPException, Response

from src.constants.enums import CLASSES, CLASS_LABELS

COLUMNS = ['区段编号', '区段', '原始回传', '计入', '重复', '待核', '无效', '排除',
           'A 轮', 'B 轮', '重合', '合并实见', '估计总量', '估计漏查', '估计方差', '建议']


def safe(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def export(item):
    if not item.result:
        raise HTTPException(409, '估算结果尚未完成')
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    metrics = item.result['metrics']
    writer.writerow(['补巡单', '状态', '结论'])
    writer.writerow([safe(item.title), item.status, safe(item.result['summary'])])
    writer.writerow([])
    writer.writerow(['回传四类', '数量'])
    for name in CLASSES:
        writer.writerow([CLASS_LABELS[name], metrics.get(CLASS_LABELS[name], 0)])
    writer.writerow(['原始回传总数', metrics.get('原始回传总数', 0)])
    writer.writerow(['排除合计', metrics.get('排除合计', 0)])
    writer.writerow(['四类对账', metrics.get('四类对账', '')])
    writer.writerow([])
    writer.writerow(['估算指标', '数值'])
    for key, value in metrics.items():
        if key in {CLASS_LABELS[name] for name in CLASSES} or key in ('原始回传总数', '排除合计', '四类对账'):
            continue
        writer.writerow([safe(key), safe(value)])
    rows = item.result['rows']
    if rows:
        writer.writerow([])
        writer.writerow(COLUMNS)
        writer.writerows([[safe(row.get(key, '')) for key in COLUMNS] for row in rows])
    submissions = item.result.get('submissions') or []
    if submissions:
        writer.writerow([])
        writer.writerow(['回传批次', '提交编号', '重送来源', '收到时间', '原始回传', '计入', '重复', '待核', '无效', '排除'])
        writer.writerows([[safe(row.get('id', '')), safe(row.get('client_submission_id') or ''),
                           safe(row.get('resend_of') or ''), safe(row.get('received_at', '')),
                           row.get('received', 0), row.get('counted', 0), row.get('duplicate', 0),
                           row.get('pending', 0), row.get('invalid', 0), row.get('excluded', 0)]
                          for row in submissions])
    trace = item.result.get('events') or []
    if trace:
        writer.writerow([])
        writer.writerow(['核对轨迹', '区段', '轮次', '座椅编号', '动作', '原状态', '新状态', '说明', '记录时间'])
        writer.writerows([[row.get('id'), safe(row.get('segment_code', '')), row.get('round_code', ''),
                           safe(row.get('asset_code', '')), row.get('action', ''),
                           row.get('from_state', ''), row.get('to_state', ''), safe(row.get('note', '')),
                           safe(row.get('recorded_at', ''))] for row in trace])
    return Response(content='﻿' + stream.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="bench-plan-{item.id}.csv"'})
