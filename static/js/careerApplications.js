// ============================================
// Career hub — Applications tab (list, add form, detail)
// Later plans extend the detail view: cover letter (01), review panel (02),
// email events (04) through the `detailSections` registry below.
// ============================================
import uiModule from './ui.js';
import { applicationBuckets, daysLabel, statusChip, STATUS_LABELS } from './careerLogic.js';

const esc = uiModule.esc;

// Later plans push `(app, state) => html` here; sections render in push order.
export const detailSections = [];

const _card = (a) => {
  const chip = statusChip(a.computed_status);
  const when = a.days_since_event === null || a.days_since_event === undefined
    ? '' : ` · ${daysLabel(a.days_since_event)}`;
  return `
    <div class="proj-card admin-card career-app" data-application-id="${esc(a.id)}">
      <div class="proj-card-head">
        <span class="proj-name">${esc(a.company)} — ${esc(a.role)}</span>
        <span class="proj-badge ${chip.cls}">${esc(chip.label)}</span>
      </div>
      <div class="memory-desc report-scores">${esc(a.source)}${when}</div>
      <div class="proj-actions">
        <button class="memory-toolbar-btn" data-action="open-application">Open</button>
        <button class="memory-toolbar-btn" data-action="archive-application">Archive</button>
      </div>
    </div>`;
};

const _group = (title, apps) => apps.length
  ? `<h3 class="cc-section">${esc(title)} (${apps.length})</h3><div class="projects-grid">${apps.map(_card).join('')}</div>`
  : '';

export const addFormHtml = () => `
  <div class="council-edit-form career-add-form">
    <h2 class="council-reports-title">New application</h2>
    <div class="assistant-field-row">
      <label class="assistant-field" style="flex:1;"><span>Company</span><input id="career-add-company" class="settings-input" type="text"></label>
      <label class="assistant-field" style="flex:1;"><span>Role</span><input id="career-add-role" class="settings-input" type="text"></label>
    </div>
    <label class="assistant-field"><span>Posting URL (fetched for the job description if you leave the text empty)</span>
      <input id="career-add-url" class="settings-input" type="url"></label>
    <label class="assistant-field"><span>Job description (paste)</span>
      <textarea id="career-add-jd" class="settings-input" rows="8"></textarea></label>
    <div class="council-run-form">
      <button id="career-add-save" class="memory-toolbar-btn">Save application</button>
      <button id="career-add-cancel" class="memory-toolbar-btn">Cancel</button>
    </div>
  </div>`;

export const listHtml = (state) => {
  const b = applicationBuckets(state.apps);
  const body = state.apps.length
    ? _group('Drafting', b.drafting) + _group('Active', b.active) + _group('Closed', b.closed)
    : '<p class="memory-desc">No applications yet. Add one, or let the tracker create them from confirmation emails.</p>';
  return `
    <div class="council-run-form">
      <button id="career-add-btn" class="memory-toolbar-btn">Add application</button>
    </div>${body}`;
};

const _statusOptions = (current) => Object.entries(STATUS_LABELS)
  .filter(([v]) => v !== 'ghosted')
  .map(([v, label]) => `<option value="${esc(v)}"${v === current ? ' selected' : ''}>${esc(label)}</option>`).join('');

export const detailHtml = (app, state) => `
  <div class="career-detail" data-application-id="${esc(app.id)}">
    <div class="council-run-form">
      <button id="career-back" class="memory-toolbar-btn">← All applications</button>
      <select id="career-detail-status" class="settings-input">${_statusOptions(app.status)}</select>
    </div>
    <h2 class="cc-title">${esc(app.company)} — ${esc(app.role)}</h2>
    <div class="memory-desc report-scores">${app.url ? `<a href="${esc(app.url)}" target="_blank" rel="noopener">${esc(app.url)}</a>` : 'No posting URL'}</div>
    ${detailSections.map((fn) => fn(app, state)).join('')}
    <details class="proj-summary"><summary>Job description</summary>
      <div class="proj-summary-body">${esc(app.jd_text || '(none)')}</div></details>
    <label class="assistant-field"><span>Notes</span>
      <textarea id="career-detail-notes" class="settings-input" rows="4">${esc(app.notes || '')}</textarea></label>
    <div class="council-run-form"><button id="career-detail-save-notes" class="memory-toolbar-btn">Save notes</button></div>
  </div>`;
