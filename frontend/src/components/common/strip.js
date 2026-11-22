import {h} from '../../utils/html.js';
export function strip(counts) {
  const values = [
    ['原始回传', counts.received], ['计入', counts.counted], ['重复', counts.duplicate],
    ['待核', counts.pending], ['无效', counts.invalid],
  ];
  return `<div class="pair-strip">${values.map(([label, value]) => `<span>${h(label)} <b>${h(value ?? 0)}</b></span>`).join('')}</div>`;
}
