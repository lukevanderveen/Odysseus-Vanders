// ============================================
// Career hub — Reviewers tab: four persona cards + editor (name, personality,
// model/endpoint override). Plugs into career.js through installReviewers(api).
// ============================================
import uiModule from './ui.js';
import { memberStatusChip, memberEditPayload } from './councilLogic.js';
import { sortModelIds } from './modelSort.js';
import { REVIEWER_COLORS } from './careerLogic.js';
import { reviewerLabel } from './careerReviewLogic.js';

const esc = uiModule.esc;

let _api = null;
let _reviewers = null;     // null = not loaded yet
let _status = {};          // department -> Busy|Ready
let _editing = null;

const _json = async (url, opts = {}) => {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${url} failed (${res.status})`);
  return data;
};

const _load = async () => {
  try {
    const [list, status] = await Promise.all([_json('/api/career/reviewers'), _json('/api/career/reviewers/status')]);
    _reviewers = list.reviewers || [];
    _status = Object.fromEntries((status.reviewers || []).map((r) => [r.department, r.status]));
  } catch (e) {
    _reviewers = [];
    uiModule.showError(`Reviewers: ${e.message}`);
  }
  _api.render();
};

const _card = (m) => {
  const chip = memberStatusChip(_status[m.department] || 'Ready');
  const color = REVIEWER_COLORS[m.department] || 'var(--fg)';
  return `
    <div class="proj-card admin-card council-member" data-reviewer-id="${esc(m.id)}" style="--dept-color:${color}">
      <div class="proj-card-head">
        <span class="council-avatar">${esc((m.name || '?')[0].toUpperCase())}</span>
        <span class="proj-name">${esc(m.name)}</span>
        <span class="proj-badge ${chip.cls}${chip.label === 'Busy' ? ' report-chip-running' : ''}">${esc(chip.label)}</span>
      </div>
      <div class="council-dept">${esc(reviewerLabel(m.department))}</div>
      <div class="memory-desc report-scores">${esc((m.personality || '').slice(0, 140))}${(m.personality || '').length > 140 ? '…' : ''}</div>
      <div class="memory-desc report-scores">${m.model ? `Model: ${esc(m.model)}` : 'Model: task default'}</div>
      <div class="proj-actions"><button class="memory-toolbar-btn" data-action="edit-reviewer">Edit persona</button></div>
    </div>`;
};

const _form = (m) => `
  <div class="council-edit-form" data-reviewer-id="${esc(m.id)}" style="--dept-color:${REVIEWER_COLORS[m.department] || 'var(--fg)'}">
    <h2 class="council-reports-title">Edit ${esc(reviewerLabel(m.department))}</h2>
    <label class="assistant-field"><span>Name</span>
      <input type="text" id="career-rv-name" class="settings-input" value="${esc(m.name || '')}"></label>
    <label class="assistant-field"><span>Personality (system prompt)</span>
      <textarea id="career-rv-personality" class="settings-input" rows="8">${esc(m.personality || '')}</textarea></label>
    <div class="assistant-field-row">
      <label class="assistant-field" style="flex:1;"><span>Model endpoint</span>
        <select id="career-rv-endpoint" class="settings-input"></select></label>
      <label class="assistant-field" style="flex:1;"><span>Model</span>
        <select id="career-rv-model" class="settings-input"></select></label>
    </div>
    <div class="council-run-form">
      <button id="career-rv-save" class="memory-toolbar-btn">Save</button>
      <button id="career-rv-cancel" class="memory-toolbar-btn">Cancel</button>
    </div>
  </div>`;

// Same endpoint/model population as the council member editor.
const _populateEndpointModel = (member) => {
  const epSelect = document.getElementById('career-rv-endpoint');
  const modelSelect = document.getElementById('career-rv-model');
  if (!epSelect || !modelSelect) return;
  fetch('/api/model-endpoints').then((r) => (r.ok ? r.json() : [])).then((eps) => {
    const endpoints = Array.isArray(eps) ? eps : [];
    let html = '<option value="">(use task default)</option>';
    for (const ep of endpoints) {
      if (!ep.is_enabled) continue;
      const url = ep.base_url || '';
      const sel = member.endpoint_url && url
        && member.endpoint_url.includes(url.replace('/v1', '').replace(/\/$/, '')) ? ' selected' : '';
      html += `<option value="${esc(url)}"${sel}>${esc(ep.name || url)}</option>`;
    }
    epSelect.innerHTML = html;
    epSelect.onchange = async () => {
      const url = epSelect.value;
      if (!url) { modelSelect.innerHTML = '<option value="">(default)</option>'; return; }
      const ep = endpoints.find((e) => e.base_url === url);
      if (!ep) return;
      modelSelect.innerHTML = '<option value="">loading…</option>';
      try {
        const res = await fetch(`/api/model-endpoints/${ep.id}/models`);
        const models = await res.json();
        const ids = (models.models || models || [])
          .map((m) => (typeof m === 'string' ? m : (m.id || m.name || ''))).filter(Boolean);
        let mh = '<option value="">(default)</option>';
        for (const mid of sortModelIds(ids)) {
          mh += `<option value="${esc(mid)}"${mid === member.model ? ' selected' : ''}>${esc(mid.split('/').pop())}</option>`;
        }
        modelSelect.innerHTML = mh;
      } catch { modelSelect.innerHTML = '<option value="">(failed)</option>'; }
    };
    if (epSelect.value) epSelect.onchange();
  });
};

const _save = async () => {
  if (!_editing) return;
  const payload = memberEditPayload({
    name: document.getElementById('career-rv-name')?.value,
    personality: document.getElementById('career-rv-personality')?.value,
    model: document.getElementById('career-rv-model')?.value,
    endpoint: document.getElementById('career-rv-endpoint')?.value,
  });
  try {
    await _json(`/api/career/reviewers/${_editing.id}`, { method: 'PUT', body: JSON.stringify(payload) });
    uiModule.showToast('Reviewer updated');
    _editing = null;
    await _load();
  } catch (e) {
    uiModule.showError(`Save reviewer: ${e.message}`);
  }
};

export const renderReviewersTab = () => {
  if (_reviewers === null) { _load(); return '<p class="memory-desc">Loading reviewers…</p>'; }
  if (_editing) return _form(_editing);
  if (!_reviewers.length) return '<p class="memory-desc">No reviewers yet — open the Applications tab once to seed them.</p>';
  return `
    <div class="projects-grid">${_reviewers.map(_card).join('')}</div>
    <p class="memory-desc doclib-desc">Each reviewer reads the job description, the cover letter and your CV excerpt in its own voice. Edit a persona to change what it looks for, or pin it to a specific model.</p>`;
};

// Returns true when it handled the click (career.js stops there).
export const handleReviewersClick = (ev) => {
  if (ev.target.closest('#career-rv-save')) { _save(); return true; }
  if (ev.target.closest('#career-rv-cancel')) { _editing = null; _api.render(); return true; }
  const btn = ev.target.closest('[data-action="edit-reviewer"]');
  if (!btn) return false;
  const id = btn.closest('[data-reviewer-id]')?.dataset.reviewerId;
  _editing = (_reviewers || []).find((m) => m.id === id) || null;
  _api.render();
  if (_editing) _populateEndpointModel(_editing);
  return true;
};

export const installReviewers = (api) => {
  _api = api;
  api.registerTab('reviewers', renderReviewersTab);
  api.onPanelClick(handleReviewersClick);
};
