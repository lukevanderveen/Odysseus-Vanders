// ============================================
// Projects tab — auto-discovered local code projects (Agent Council Phase 1)
// Grid of projects with stack chips, staleness badge, Index / Archive actions.
// Modal shell lives in index.html (#projects-modal); this module renders into it.
// ============================================
import uiModule from './ui.js';
import { projectStatusBadge, sortProjects } from './projectsLogic.js';

const esc = uiModule.esc;
let _projects = [];
let _busy = new Set();
let _deepBusy = new Set();

const _modal = () => document.getElementById('projects-modal');
const _grid = () => document.getElementById('projects-grid');

const _fetchProjects = async () => {
  const res = await fetch('/api/projects');
  if (!res.ok) throw new Error(`projects list failed (${res.status})`);
  _projects = (await res.json()).projects || [];
};

const _card = (p) => {
  const badge = projectStatusBadge(p);
  const chips = (p.stack || [])
    .map((s) => `<span class="proj-chip">${esc(s)}</span>`)
    .join('');
  const busy = _busy.has(p.id);
  const deepBusy = _deepBusy.has(p.id);
  const summary = p.deep_summary
    ? `<details class="proj-summary"><summary>Summary</summary><div class="proj-summary-body">${esc(p.deep_summary)}</div></details>`
    : '';
  return `
    <div class="proj-card admin-card" data-project-id="${esc(p.id)}">
      <div class="proj-card-head">
        <span class="proj-name" title="${esc(p.path)}">${esc(p.name)}</span>
        <span class="proj-badge ${badge.cls}">${esc(badge.label)}</span>
      </div>
      <div class="proj-chips">${chips}</div>
      ${summary}
      <div class="proj-actions">
        <button class="memory-toolbar-btn" data-action="index" ${busy || !p.exists ? 'disabled' : ''}>
          ${busy ? 'Indexing…' : 'Index'}
        </button>
        <button class="memory-toolbar-btn" data-action="deep-index" ${deepBusy || !p.exists ? 'disabled' : ''}>
          ${deepBusy ? 'Summarising…' : 'Deep Index'}
        </button>
        <button class="memory-toolbar-btn" data-action="archive">Archive</button>
      </div>
    </div>`;
};

const _render = () => {
  const grid = _grid();
  if (!grid) return;
  if (!_projects.length) {
    grid.innerHTML =
      '<p class="memory-desc">No projects yet — set <code>projects_workspace_roots</code> in settings, then Scan.</p>';
    return;
  }
  grid.innerHTML = sortProjects(_projects).map(_card).join('');
};

const _refresh = async () => {
  try {
    await _fetchProjects();
    _render();
  } catch (e) {
    uiModule.showError(`Projects: ${e.message}`);
  }
};

const _scan = async () => {
  const btn = document.getElementById('projects-scan-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Scanning…'; }
  try {
    const res = await fetch('/api/projects/scan', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `scan failed (${res.status})`);
    _projects = data.projects || [];
    _render();
    uiModule.showToast(`Found ${_projects.length} project${_projects.length === 1 ? '' : 's'}`);
  } catch (e) {
    uiModule.showError(`Scan: ${e.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Scan workspace'; }
  }
};

const _index = async (pid) => {
  _busy.add(pid);
  _render();
  try {
    const res = await fetch(`/api/projects/${pid}/index`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `index failed (${res.status})`);
    uiModule.showToast(`Indexed ${data.files} files (${data.chunks} chunks)`);
  } catch (e) {
    uiModule.showError(`Index: ${e.message}`);
  } finally {
    _busy.delete(pid);
    await _refresh();
  }
};

const _pollDeepIndex = (pid) => new Promise((resolve) => {
  const started = Date.now();
  const tick = async () => {
    if (Date.now() - started > 15 * 60 * 1000) return resolve({ status: 'error', error: 'timed out' });
    try {
      const res = await fetch(`/api/projects/${pid}/deep-index/status`);
      const s = await res.json();
      if (s.status === 'done' || s.status === 'error') return resolve(s);
    } catch (_) { /* transient — keep polling */ }
    setTimeout(tick, 3000);
  };
  tick();
});

const _deepIndex = async (pid) => {
  _deepBusy.add(pid);
  _render();
  try {
    const res = await fetch(`/api/projects/${pid}/deep-index`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `deep index failed (${res.status})`);
    const final = await _pollDeepIndex(pid);
    if (final.status === 'error') throw new Error(final.error || 'unknown');
    uiModule.showToast('Deep index complete');
  } catch (e) {
    uiModule.showError(`Deep index: ${e.message}`);
  } finally {
    _deepBusy.delete(pid);
    await _refresh();
  }
};

const _archive = async (pid) => {
  try {
    const res = await fetch(`/api/projects/${pid}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`archive failed (${res.status})`);
    await _refresh();
  } catch (e) {
    uiModule.showError(`Archive: ${e.message}`);
  }
};

let _wired = false;
const _wire = () => {
  if (_wired) return;
  _wired = true;
  document.getElementById('close-projects-modal')?.addEventListener('click', close);
  document.getElementById('projects-scan-btn')?.addEventListener('click', _scan);
  // One delegated handler for all card actions (CSP-safe, survives re-renders).
  _grid()?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('button[data-action]');
    if (!btn) return;
    const pid = btn.closest('[data-project-id]')?.dataset.projectId;
    if (!pid) return;
    if (btn.dataset.action === 'index') _index(pid);
    if (btn.dataset.action === 'deep-index') _deepIndex(pid);
    if (btn.dataset.action === 'archive') _archive(pid);
  });
};

export const open = () => {
  const modal = _modal();
  if (!modal) return;
  _wire();
  modal.classList.remove('hidden');
  _refresh();
};

export const close = () => _modal()?.classList.add('hidden');
export const isOpen = () => !!_modal() && !_modal().classList.contains('hidden');

export default { open, close, isOpen };
