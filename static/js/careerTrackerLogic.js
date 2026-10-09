// ============================================
// Career tracker — pure logic (no DOM, Node-importable for tests)
// ============================================

const KIND_LABELS = {
  confirmation: 'Confirmation',
  rejection: 'Rejection',
  interview_invite: 'Interview invite',
  offer: 'Offer',
  info_request: 'Info request',
  recruiter_outreach: 'Recruiter outreach',
  other: 'Other',
};

export const sentimentChip = (kind, sentiment) => {
  const label = KIND_LABELS[kind] || 'Other';
  if (kind === 'other') return { label, cls: 'career-chip-muted' };
  const cls = sentiment === 'positive' ? 'career-chip-positive'
    : sentiment === 'negative' ? 'career-chip-negative'
      : 'career-chip-waiting';
  return { label, cls };
};

const TILES = [
  ['active', 'Active'], ['awaiting', 'Awaiting response'], ['interviews', 'Interviews'],
  ['offers', 'Offers'], ['rejected', 'Rejected'], ['response_rate', 'Response rate'],
];

export const tilesHtml = (tiles, esc) => `<div class="career-tiles">${TILES.map(([key, label]) => {
  const raw = (tiles || {})[key] ?? 0;
  const value = key === 'response_rate' ? `${raw}%` : String(raw);
  return `<div class="career-tile admin-card"><span class="career-tile-num">${esc(value)}</span><span class="career-tile-label">${esc(label)}</span></div>`;
}).join('')}</div>`;

export const eventRowHtml = (ev, esc, { showApp = true } = {}) => {
  const chip = sentimentChip(ev.kind, ev.sentiment);
  const who = showApp && ev.company ? `<span class="proj-name">${esc(ev.company)} — ${esc(ev.role || '')}</span>` : '';
  const reply = ev.suggested_reply
    ? `<button class="memory-toolbar-btn" data-action="copy-reply">Copy reply</button>`
    : ev.reply_blocked_reason ? `<span class="memory-desc">No reply: ${esc(ev.reply_blocked_reason)}</span>` : '';
  return `
    <div class="report-row admin-card career-event" data-event-id="${esc(ev.id)}" data-uid="${esc(ev.uid || '')}" data-folder="${esc(ev.folder || 'INBOX')}">
      <div class="proj-card-head">${who}<span class="proj-badge ${chip.cls}">${esc(chip.label)}</span>
        <span class="memory-desc">${esc((ev.received_at || '').slice(0, 10))}</span></div>
      <div class="memory-desc report-scores">${esc(ev.subject || '(no subject)')}</div>
      ${ev.summary ? `<div class="memory-desc">${esc(ev.summary)}</div>` : ''}
      ${ev.suggested_reply ? `<pre class="career-reply">${esc(ev.suggested_reply)}</pre>` : ''}
      <div class="proj-actions">
        ${ev.uid ? '<button class="memory-toolbar-btn" data-action="open-email">Open email</button>' : ''}
        ${reply}
      </div>
    </div>`;
};

const AWAITING = new Set(['applied', 'acknowledged', 'screening']);

export const nudgeDue = (app, settings) => {
  if (!AWAITING.has(app.computed_status)) return false;
  const days = app.days_since_event;
  return Number.isFinite(days) && days >= Number((settings || {}).nudge_after_days ?? 10);
};

export const summaryChanged = (prev, next) => (prev || {}).updated_at !== (next || {}).updated_at;
