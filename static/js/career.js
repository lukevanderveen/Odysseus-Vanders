// ============================================
// Career hub modal (docs/plans/career-hub-design.md)
// Tabs: Tracker (04), Applications (00/01/02), Reviewers (02), Posts (03), Setup (00).
// Later plans plug in with registerTab(name, renderFn) and onPanelClick(handler)
// instead of editing this file.
// ============================================
import uiModule from './ui.js';
import { placeholderHtml, TABS } from './careerLogic.js';
import { addFormHtml, detailHtml, listHtml } from './careerApplications.js';
import { readSettingsForm, setupHtml } from './careerSetup.js';
import { installCoverLetter } from './careerCoverLetter.js';

const _tabRenderers = {};
const _clickHandlers = [];

export const registerTab = (name, render) => { _tabRenderers[name] = render; };
export const onPanelClick = (handler) => { _clickHandlers.push(handler); };

const state = { tab: 'applications', apps: [], settings: {}, bootstrap: {}, examples: [], view: 'list', openId: null };
export const getState = () => state;

const _modal = () => document.getElementById('career-modal');
const _panel = () => document.getElementById('career-panel');

const _json = async (url, opts = {}) => {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail?.[0]?.msg || data.detail || `${url} failed (${res.status})`);
  return data;
};

const _fetchAll = async () => {
  const [boot, apps, examples] = await Promise.all([
    _json('/api/career/bootstrap'),
    _json('/api/career/applications'),
    _json('/api/career/examples').catch(() => ({ examples: [] })),
  ]);
  state.bootstrap = boot;
  state.settings = boot.settings || {};
  state.apps = apps.applications || [];
  state.examples = examples.examples || [];
};

const _renderApplications = () => {
  if (state.view === 'add') return addFormHtml();
  if (state.view === 'detail') {
    const app = state.apps.find((a) => a.id === state.openId);
    if (app) return detailHtml(app, state);
    state.view = 'list';
  }
  return listHtml(state);
};

export const render = () => {
  const panel = _panel();
  if (!panel) return;
  document.querySelectorAll('#career-tabs [data-career-tab]').forEach((b) => {
    b.classList.toggle('active', b.dataset.careerTab === state.tab);
  });
  const custom = _tabRenderers[state.tab];
  panel.innerHTML = state.tab === 'applications' ? _renderApplications()
    : state.tab === 'setup' ? setupHtml(state)
      : custom ? custom(state)
        : placeholderHtml(state.tab);
};

export const refresh = async () => {
  try {
    await _fetchAll();
    render();
  } catch (e) {
    uiModule.showError(`Career: ${e.message}`);
  }
};

// ── actions ──────────────────────────────────────────────────────────────────

const _saveApplication = async () => {
  const body = {
    company: document.getElementById('career-add-company')?.value || '',
    role: document.getElementById('career-add-role')?.value || '',
    jd_url: document.getElementById('career-add-url')?.value || null,
    jd_text: document.getElementById('career-add-jd')?.value || null,
  };
  try {
    const app = await _json('/api/career/applications', { method: 'POST', body: JSON.stringify(body) });
    state.view = 'detail';
    state.openId = app.id;
    await refresh();
    uiModule.showToast('Application saved');
  } catch (e) {
    uiModule.showError(`Save application: ${e.message}`);
  }
};

const _updateApplication = async (id, patch) => {
  try {
    await _json(`/api/career/applications/${id}`, { method: 'PUT', body: JSON.stringify(patch) });
    await refresh();
  } catch (e) {
    uiModule.showError(`Update application: ${e.message}`);
  }
};

const _archiveApplication = async (id) => {
  if (!(await uiModule.styledConfirm('Archive this application?'))) return;
  try {
    await _json(`/api/career/applications/${id}`, { method: 'DELETE' });
    if (state.openId === id) state.view = 'list';
    await refresh();
  } catch (e) {
    uiModule.showError(`Archive: ${e.message}`);
  }
};

const _saveSettings = async () => {
  try {
    await _json('/api/career/settings', { method: 'PUT', body: JSON.stringify(readSettingsForm()) });
    await refresh();
    uiModule.showToast('Career settings saved');
  } catch (e) {
    uiModule.showError(`Save settings: ${e.message}`);
  }
};

const _uploadCv = async () => {
  const input = document.getElementById('career-cv-file');
  const file = input?.files?.[0];
  if (!file) return uiModule.showError('Choose a CV file first');
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/career/cv', { method: 'POST', body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `upload failed (${res.status})`);
    await refresh();
    uiModule.showToast(`CV indexed (${data.chunks} chunks)`);
  } catch (e) {
    uiModule.showError(`Upload CV: ${e.message}`);
  }
};

const _addExample = async () => {
  const body = {
    title: document.getElementById('career-ex-title')?.value || '',
    jd_text: document.getElementById('career-ex-jd')?.value || '',
    letter_text: document.getElementById('career-ex-letter')?.value || '',
  };
  try {
    await _json('/api/career/examples', { method: 'POST', body: JSON.stringify(body) });
    await refresh();
    uiModule.showToast('Example added');
  } catch (e) {
    uiModule.showError(`Add example: ${e.message}`);
  }
};

const _deleteExample = async (filename) => {
  try {
    await _json(`/api/career/examples/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    await refresh();
  } catch (e) {
    uiModule.showError(`Delete example: ${e.message}`);
  }
};

// ── wiring ───────────────────────────────────────────────────────────────────

// Simple id → action map for the panel's own buttons; plugins run first.
const _idActions = {
  'career-add-btn': () => { state.view = 'add'; render(); },
  'career-add-cancel': () => { state.view = 'list'; render(); },
  'career-add-save': _saveApplication,
  'career-back': () => { state.view = 'list'; render(); },
  'career-settings-save': _saveSettings,
  'career-cv-upload': _uploadCv,
  'career-ex-save': _addExample,
  'career-detail-save-notes': () => _updateApplication(
    state.openId, { notes: document.getElementById('career-detail-notes')?.value || '' }),
};

const _onPanelClick = (ev) => {
  for (const handler of _clickHandlers) {
    if (handler(ev, state) === true) return;   // a plugin handled it
  }
  const idBtn = ev.target.closest('button[id]');
  if (idBtn && _idActions[idBtn.id]) return _idActions[idBtn.id]();
  const delEx = ev.target.closest('[data-action="delete-example"]');
  if (delEx) return _deleteExample(delEx.closest('[data-example]')?.dataset.example);
  const btn = ev.target.closest('button[data-action]');
  const id = btn?.closest('[data-application-id]')?.dataset.applicationId;
  if (!btn || !id) return;
  if (btn.dataset.action === 'open-application') { state.view = 'detail'; state.openId = id; return render(); }
  if (btn.dataset.action === 'archive-application') return _archiveApplication(id);
};

let _wired = false;
const _wire = () => {
  if (_wired) return;
  _wired = true;
  document.getElementById('close-career-modal')?.addEventListener('click', close);
  document.getElementById('career-tabs')?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-career-tab]');
    if (!btn || !TABS.includes(btn.dataset.careerTab)) return;
    state.tab = btn.dataset.careerTab;
    render();
  });
  _panel()?.addEventListener('click', _onPanelClick);
  _panel()?.addEventListener('change', (ev) => {
    if (ev.target.id === 'career-detail-status' && state.openId) {
      _updateApplication(state.openId, { status: ev.target.value });
    }
  });
};

export const open = () => {
  const modal = _modal();
  if (!modal) return;
  _wire();
  modal.classList.remove('hidden');
  refresh();
};

export const close = () => { _modal()?.classList.add('hidden'); };
export const isOpen = () => !!_modal() && !_modal().classList.contains('hidden');

const careerApi = { open, close, isOpen, refresh, render, registerTab, onPanelClick, getState };

// ── plugins ──────────────────────────────────────────────────────────────────
// Later plans add exactly two lines here per feature module: an import of its
// `install` function and a call `installX(careerApi);`. Plugin modules never
// import career.js (that would be an ES-module cycle: this file's consts are
// in the temporal dead zone while a dependency evaluates). Each `install(api)`
// calls api.registerTab / api.onPanelClick and may import `detailSections`
// from careerApplications.js; it must not fetch or render at load.
installCoverLetter(careerApi);

export default careerApi;
