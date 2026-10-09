// ============================================
// Career hub — Tracker tab. Plugs in through installTracker(api) (plan 00
// plugin contract; never imports career.js) and polls the summary every 20 s
// while the tab is visible.
// ============================================
import uiModule from './ui.js';
import { detailSections } from './careerApplications.js';
import { daysLabel, statusChip } from './careerLogic.js';
import { eventRowHtml, nudgeDue, summaryChanged, tilesHtml } from './careerTrackerLogic.js';

const esc = uiModule.esc;
const POLL_MS = 20000;
let _api = null;
let _summary = null;
let _events = {};        // application id -> events (detail view)
let _pollTimer = null;
let _busy = false;

const _json = async (url, opts = {}) => {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${url} failed (${res.status})`);
  return data;
};

const _appRow = (a, settings, awaiting) => {
  const chip = statusChip(a.computed_status);
  return `
    <div class="report-row admin-card career-app" data-application-id="${esc(a.id)}">
      <div class="proj-card-head"><span class="proj-name">${esc(a.company)} — ${esc(a.role)}</span>
        <span class="proj-badge ${chip.cls}">${esc(chip.label)}</span>
        <span class="memory-desc">${esc(daysLabel(a.days_since_event))}</span></div>
      ${awaiting && nudgeDue(a, settings) ? '<div class="proj-actions"><button class="memory-toolbar-btn" data-action="suggest-nudge">Suggest nudge</button></div>' : ''}
    </div>`;
};

const _unsortedRow = (ev, apps) => {
  const opts = apps.map((a) => `<option value="${esc(a.id)}">${esc(a.company)} — ${esc(a.role)}</option>`).join('');
  return `
    <div class="report-row admin-card career-event" data-event-id="${esc(ev.id)}" data-uid="${esc(ev.uid || '')}" data-folder="${esc(ev.folder || 'INBOX')}">
      <div class="memory-desc report-scores">${esc(ev.subject || '(no subject)')} — ${esc(ev.from_address || '')}</div>
      ${ev.summary ? `<div class="memory-desc">${esc(ev.summary)}</div>` : ''}
      <div class="council-run-form">
        <select class="settings-input" data-role="assign-target"><option value="">Assign to…</option>${opts}</select>
        <button class="memory-toolbar-btn" data-action="assign-event">Assign</button>
        <button class="memory-toolbar-btn" data-action="dismiss-event">Dismiss</button>
        ${ev.uid ? '<button class="memory-toolbar-btn" data-action="open-email">Open email</button>' : ''}
      </div>
    </div>`;
};

const _trackerHtml = (state) => {
  if (!_summary) return '<p class="memory-desc">Loading tracker…</p>';
  const s = _summary;
  const settings = state.settings || {};
  return `
    ${tilesHtml(s.tiles, esc)}
    <div class="council-run-form">
      <button id="career-scan-btn" class="memory-toolbar-btn" ${_busy ? 'disabled' : ''}>${_busy ? 'Scanning…' : 'Scan last 30 days'}</button>
      <span class="memory-desc">Live: new inbox mail is classified automatically while Odysseus is open.</span>
    </div>
    <h3 class="cc-section">Recent responses</h3>
    ${s.recent.length ? s.recent.map((e) => eventRowHtml(e, esc, { showApp: true })).join('') : '<p class="memory-desc">No responses yet.</p>'}
    <h3 class="cc-section">Awaiting response (${s.awaiting.length})</h3>
    ${s.awaiting.length ? s.awaiting.map((a) => _appRow(a, settings, true)).join('') : '<p class="memory-desc">Nothing in flight.</p>'}
    ${s.unsorted.length ? `<h3 class="cc-section">Unsorted (${s.unsorted.length})</h3>${s.unsorted.map((e) => _unsortedRow(e, state.apps)).join('')}` : ''}
    <details class="proj-summary"><summary>Closed (${s.closed.length})</summary>${s.closed.map((a) => _appRow(a, settings, false)).join('')}</details>`;
};

const _loadSummary = async () => {
  const next = await _json('/api/career/tracker/summary');
  const changed = summaryChanged(_summary, next);
  _summary = next;
  return changed;
};

const _schedulePoll = () => {
  clearTimeout(_pollTimer);
  if (!_api.isOpen() || _api.getState().tab !== 'tracker') return;
  _pollTimer = setTimeout(async () => {
    try {
      if (await _loadSummary()) _api.render();
    } catch (e) {
      console.warn('tracker poll', e);
    } finally {
      _schedulePoll();
    }
  }, POLL_MS);
};

const renderTracker = (state) => {
  if (!_summary) {
    _loadSummary().then(() => _api.render()).catch((e) => uiModule.showError(`Tracker: ${e.message}`));
  }
  _schedulePoll();
  return _trackerHtml(state);
};

// Application detail: events timeline (loaded lazily, cached per application).
const eventsSection = (app) => {
  const evs = _events[app.id];
  if (!evs) {
    _json(`/api/career/tracker/events?application_id=${encodeURIComponent(app.id)}`)
      .then((d) => { _events[app.id] = d.events || []; _api.render(); })
      .catch(() => { _events[app.id] = []; });
    return '<h3 class="cc-section">Email events</h3><p class="memory-desc">Loading…</p>';
  }
  return `<h3 class="cc-section">Email events (${evs.length})</h3>${evs.length
    ? evs.map((e) => eventRowHtml(e, esc, { showApp: false })).join('')
    : '<p class="memory-desc">No emails linked yet.</p>'}`;
};

const _openEmail = (row) => {
  const uid = row?.dataset.uid;
  if (!uid) return;
  const folder = row.dataset.folder || 'INBOX';
  _api.close();
  window.location.hash = `#email=${encodeURIComponent(folder)}:${uid}`;
};

const _invalidate = async () => { _summary = null; _events = {}; await _api.refresh(); };

const handleClick = (ev, state) => {
  const row = ev.target.closest('[data-event-id]');
  if (ev.target.closest('#career-scan-btn')) {
    if (_busy) return true;
    _busy = true; _api.render();
    _json('/api/career/tracker/scan', { method: 'POST', body: JSON.stringify({ days: 30 }) })
      .then((r) => uiModule.showToast(`Scanned ${r.scanned} emails, ${r.events} tracker events`))
      .catch((e) => uiModule.showError(`Scan: ${e.message}`))
      .finally(async () => { _busy = false; await _invalidate(); });
    return true;
  }
  if (ev.target.closest('[data-action="open-email"]')) { _openEmail(row); return true; }
  if (ev.target.closest('[data-action="copy-reply"]')) {
    const text = row?.querySelector('.career-reply')?.textContent || '';
    uiModule.copyToClipboard(text);
    return true;
  }
  if (ev.target.closest('[data-action="assign-event"]')) {
    const target = row?.querySelector('[data-role="assign-target"]')?.value;
    if (!target) { uiModule.showError('Pick an application first'); return true; }
    _json(`/api/career/tracker/events/${row.dataset.eventId}/assign`, { method: 'POST', body: JSON.stringify({ application_id: target }) })
      .then(_invalidate).catch((e) => uiModule.showError(`Assign: ${e.message}`));
    return true;
  }
  if (ev.target.closest('[data-action="dismiss-event"]')) {
    _json(`/api/career/tracker/events/${row.dataset.eventId}/dismiss`, { method: 'POST' })
      .then(_invalidate).catch((e) => uiModule.showError(`Dismiss: ${e.message}`));
    return true;
  }
  if (ev.target.closest('[data-action="suggest-nudge"]')) {
    const id = ev.target.closest('[data-application-id]')?.dataset.applicationId;
    if (!id) return true;
    _json(`/api/career/tracker/applications/${id}/nudge`, { method: 'POST' })
      .then((r) => { uiModule.copyToClipboard(r.reply); uiModule.showToast('Nudge draft copied to clipboard'); })
      .catch((e) => uiModule.showError(`Nudge: ${e.message}`));
    return true;
  }
  return false;
};

export const installTracker = (api) => {
  _api = api;
  api.registerTab('tracker', renderTracker);
  detailSections.push(eventsSection);
  api.onPanelClick(handleClick);
};
