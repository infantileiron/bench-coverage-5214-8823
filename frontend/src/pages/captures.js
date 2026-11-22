import {api} from '../api/survey.js';
import {heading} from '../components/common/layout.js';
import {table} from '../components/common/table.js';
import {strip} from '../components/common/strip.js';
import {actions, classes, states} from '../constants/enums.js';
import {onForm} from '../hooks/forms.js';
import {canWrite} from '../stores/session.js';
import {h} from '../utils/html.js';

let selected = 'BANK-01';
let campaign = 'NOV21-BENCH';
let board = null;

const sample = `BANK-01,A,BANK-01-S001,verified
BANK-01,B,BANK-01-S001,verified
BANK-01,B,BANK-01-S017,unverified`;

/** 把粘贴的“区段,轮次,座椅编号,状态”文本拆成回传原始行，坏行也保留。 */
export function parseBatch(text) {
  return text.split('\n').map(line => line.trim()).filter(Boolean).map(line => {
    const [segment_code = '', round_code = '', asset_code = '', state = 'verified'] = line.split(/[,，\t]/).map(cell => cell.trim());
    return {segment_code, round_code, asset_code, state};
  });
}

function trace(receipt) {
  if (receipt.duplicate_of) return `${receipt.note || '重复登记'} · 原凭证 #${receipt.duplicate_of}`;
  return receipt.note || '—';
}

export async function captures() {
  const segments = await api.segments();
  const chosen = segments.find(x => x.code === selected) || segments[0];
  board = board || {
    text: sample,
    clientId: `dev-${Date.now().toString(36)}`,
    resendOf: '',
    message: '',
  };
  if (!chosen) return {html:heading('双轮发现清单','先登记区段，再记录两轮独立发现。')};
  selected = chosen.code;
  const [rows, summary, submissions, events] = await Promise.all([
    api.ledger(campaign), api.summary(campaign), api.submissions(campaign), api.events(campaign),
  ]);
  const pending = rows.filter(x => x.classification === 'pending');
  const perSegment = summary.segments || [];
  const form = `<section class="sheet tools"><h2>记录座椅标签</h2><p>${h(campaign)} · 每条回传都入账，重复或不合格不会被丢掉。</p>
    <form id="capture-form"><label>区段<select name="segment_code">${segments.map(x => `<option value="${h(x.code)}" ${chosen.code === x.code ? 'selected' : ''}>${h(x.title)}</option>`).join('')}</select></label>
    <label>巡视轮次<select name="round_code"><option>A</option><option>B</option></select></label><label>座椅编号<input name="asset_code" required placeholder="${h(chosen.code)}-S099"></label>
    <label>核对状态<select name="state"><option value="verified">标签已核</option><option value="unverified">待核标签</option></select></label>
    <button type="submit">登记发现</button><p role="alert"></p></form>
    ${pending.length ? `<form id="confirm-form"><h3>待核照片</h3><p>补核后计入，判无效后排除；两种处理都留核对轨迹。</p><label>座椅标签<select name="id">${pending.map(x => `<option value="${x.capture_id}">${h(x.segment_code)} · ${h(x.round_code)} · ${h(x.asset_code)}</option>`).join('')}</select></label>
    <label>处理<select name="state"><option value="verified">确认同一实物，计入</option><option value="invalid">照片无效，排除</option></select></label>
    <label>说明<input name="note" maxlength="300" placeholder="例如：同一实物，编号已擦除"></label><button type="submit">提交核验</button><p role="alert"></p></form>` : ''}
    ${canWrite() ? `<form id="batch-form"><h3>整批回传</h3><p>每行“区段,轮次,座椅编号,状态”。同一批重试不会新增计数，整批重送按指纹自动挂到原批次。</p>
      <label>回传批次编号<input name="client_submission_id" value="${h(board.clientId)}" pattern="[A-Za-z0-9._:-]{1,64}"></label>
      <label>整批回送原文<textarea name="text" rows="5" required>${h(board.text)}</textarea></label>
      <label>挂到原批次（可选）<select name="resend_of"><option value="">自动识别</option>${submissions.submissions.map(x => `<option value="${h(x.id)}">${h(x.id.slice(0, 8))} · ${h(x.received)} 条${x.resend_of ? ' · 重送' : ''}</option>`).join('')}</select></label>
      <button type="submit">登记整批回传</button><p role="alert"></p></form>` : ''}</section>`;
  const traceRows = events.slice().reverse().map(x => [
    x.recorded_at, `${x.segment_code} · ${x.round_code}`, x.asset_code,
    actions[x.action] || x.action, states[x.from_state] || x.from_state, states[x.to_state] || x.to_state, x.note,
  ]);
  const submissionRows = submissions.submissions.map(x => [
    x.id.slice(0, 8), x.received_at, x.client_submission_id || '—', x.resend_of ? x.resend_of.slice(0, 8) : '—',
    x.received, x.counted, x.duplicate, x.pending, x.invalid, x.excluded,
  ]);
  return {html:heading('双轮发现清单','独立记录同一物理标签。每条回传按区段归入计入、重复、待核、无效四类，四类相加等于原始回传数。') +
    `<div class="workarea"><section class="sheet"><h2>发现台账 · 批次回传</h2><form id="filter-form" class="filters">
    <label>巡查批次<input name="campaign" required pattern="[A-Z0-9-]+" value="${h(campaign)}"></label><button type="submit" class="secondary">查询</button><p role="alert"></p></form>
    ${board.message ? `<p class="decision">${h(board.message)}</p>` : ''}
    ${strip(summary)}
    <h3>按区段四类对账</h3>${table(['区段', '原始回传', '计入', '重复', '待核', '无效', '排除'],
      perSegment.map(x => [x.segment_code, x.received, x.counted, x.duplicate, x.pending, x.invalid, x.excluded]))}
    <h3>回传批次 · 重送可追溯</h3>${table(['批次', '收到时间', '提交编号', '挂到原批次', '原始回传', '计入', '重复', '待核', '无效', '排除'], submissionRows)}
    <h3>逐条回传 · 含排除原因</h3><div class="tall">${table(['区段', '轮次', '座椅编号', '归类', '原因 / 原凭证'], rows.map(x => [
      x.segment_code, x.round_code, x.asset_code, classes[x.classification] || x.classification, trace(x)]))}</div>
    <h3>核对轨迹</h3>${table(['时间', '区段 · 轮次', '座椅编号', '动作', '原状态', '新状态', '说明'], traceRows, '暂无补核或判无效记录。')}
    </section>${canWrite() ? form : ''}</div>`,
    bind(root, refresh) {
      onForm(root.querySelector('#filter-form'), async data => {campaign = data.campaign; refresh();});
      onForm(root.querySelector('#capture-form'), async data => {
        const result = await api.addCapture({...data, campaign});
        board.message = `单条登记：${classes[result.classification] || result.classification}（批次累计 ${result.received} 条回传）`;
        refresh();
      });
      onForm(root.querySelector('#confirm-form'), async data => {
        await api.confirm(data.id, data.state, data.note);
        board.message = data.state === 'invalid' ? '已判为无效并排除。' : '已补核，标签计入双轮集合。';
        refresh();
      });
      onForm(root.querySelector('#batch-form'), async data => {
        const rows = parseBatch(data.text);
        board.text = data.text;
        const result = await api.addBatch({
          campaign, rows, client_submission_id: data.client_submission_id || null,
          resend_of: data.resend_of || null,
        });
        const head = result.idempotent
          ? `同一批重试已忽略，未新增计数（仍为 ${result.received} 条回传）`
          : `整批 ${result.received} 条：计入 ${result.counted}、重复 ${result.duplicate}、待核 ${result.pending}、无效 ${result.invalid}`;
        board.message = result.resend_of ? `${head}；已挂到原批次 ${result.resend_of.slice(0, 8)}` : head;
        board.clientId = `dev-${Date.now().toString(36)}`;
        board.resendOf = '';
        refresh();
      });
    }
  };
}
