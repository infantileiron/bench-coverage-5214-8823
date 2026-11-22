import {send} from './client.js';
export const api = {
  login: data => send('/auth/login', data),
  segments: input => send('/segments', input),
  captures: (code, campaign) => send(`/captures?${new URLSearchParams({segment_code:code, campaign})}`),
  ledger: (campaign, code = '') => send(`/captures?${new URLSearchParams({campaign, ...(code ? {segment_code:code} : {})})}`),
  summary: (campaign, code = '') => send(`/captures/summary?${new URLSearchParams({campaign, ...(code ? {segment_code:code} : {})})}`),
  submissions: campaign => send(`/captures/submissions?${new URLSearchParams({campaign})}`),
  events: campaign => send(`/captures/events?${new URLSearchParams({campaign})}`),
  addCapture: input => send('/captures', input),
  addBatch: input => send('/captures/batch', input),
  confirm: (id, state = 'verified', note = '') => send(`/captures/${id}/verify`, {state, note}),
  analyses: input => send('/analyses', input),
  analysis: id => send(`/analyses/${id}`),
  publish: (id, note) => send(`/analyses/${id}/publish`, {note}),
  export: id => send(`/analyses/${id}/export`, undefined, true),
};
