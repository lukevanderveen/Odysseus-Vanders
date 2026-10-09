// ============================================
// Career hub — cover letter plugin for the Applications detail view (plan 01)
// Plugs into career.js through installCoverLetter(api); never imports career.js.
// ============================================
import uiModule from './ui.js';
import { detailSections } from './careerApplications.js';
import { letterSectionHtml } from './careerCoverLetterLogic.js';

const esc = uiModule.esc;
let _api = null;
const _letters = new Map();   // application id -> last payload
const _busy = new Set();

export const loadLetter = async (appId) => {
  const res = await fetch(`/api/career/applications/${appId}/cover-letter`);
  const data = await res.json().catch(() => null);
  if (res.ok && data) _letters.set(appId, data);
  return _letters.get(appId) || null;
};

const _draft = async (appId) => {
  if (_busy.has(appId)) return;
  _busy.add(appId);
  _api.render();
  try {
    const res = await fetch(`/api/career/applications/${appId}/cover-letter`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `draft failed (${res.status})`);
    _letters.set(appId, data);
    uiModule.showToast(data.version > 1 ? `Cover letter regenerated (v${data.version})` : 'Cover letter drafted');
    await _api.refresh();
  } catch (e) {
    uiModule.showError(`Cover letter: ${e.message}`);
  } finally {
    _busy.delete(appId);
    _api.render();
  }
};

const sectionHtml = (app) => {
  if (!_letters.has(app.id) && app.cover_letter_doc_id) {
    loadLetter(app.id).then((p) => { if (p) _api.render(); });
  }
  return letterSectionHtml(app, _letters.get(app.id) || null, _busy.has(app.id), esc);
};

const handleClick = (ev) => {
  const btn = ev.target.closest('[data-action="draft-cover-letter"]');
  if (!btn) return false;
  const id = btn.closest('[data-application-id]')?.dataset.applicationId;
  if (id) _draft(id);
  return true;
};

export const installCoverLetter = (api) => {
  _api = api;
  detailSections.push(sectionHtml);
  api.onPanelClick(handleClick);
};
