// ============================================
// Projects tab — auto-discovered local code projects (Agent Council Phases 1–2)
// Grid of projects with stack chips, staleness badge, Index / Deep Index /
// Developer Review / Archive actions, plus the council-report approval queue.
// Modal shell lives in index.html (#projects-modal); this module renders into it.
// ============================================
import uiModule from './ui.js';
import { newlyDrafted, projectStatusBadge, reportChip, sortProjects } from './projectsLogic.js';
import { scoreBarsHtml } from './councilLogic.js';
import { openCommandCentre, wireCommandCentre } from './councilOffice.js';

const esc = uiModule.esc;
let _projects = [];
let _reports = [];
let _busy = new Set();
let _deepBusy = new Set();
let _reviewBusy = new Set();   // optimistic — covers the gap until the running row arrives
let _openBodies = new Set();
let _reportPollTimer = null;

const _modal = () => document.getElementById('projects-modal');
const _grid = () => document.getElementById('projects-grid');
const _reportsBox = () => document.getElementById('council-reports');

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
  const reviewRunning = _reviewBusy.has(p.id)
    || _reports.some((r) => r.project_id === p.id && r.status === 'running');
  const summary = p.deep_summary
    ? `<details class="proj-summary"><summary>Summary</summary><div class="proj-summary-body">${esc(p.deep_summary)}</div></details>`
    : '';
  return `
    <div class="proj-card admin-card" data-project-id="${esc(p.id)}">
      <div class="proj-card-head">
        <button class="proj-name proj-name-btn" data-action="command-centre" title="Open Command Centre — ${esc(p.path)}">${esc(p.name)}</button>
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
        <button class="memory-toolbar-btn" data-action="review" ${reviewRunning || !p.exists ? 'disabled' : ''}>
          ${reviewRunning ? 'Reviewing…' : 'Developer Review'}
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

const _reportRow = (r) => {
  const chip = reportChip(r);
  const isDraft = r.status === 'draft';
  const open = _openBodies.has(r.id);
  return `
    <div class="report-row admin-card" data-report-id="${esc(r.id)}">
      <div class="proj-card-head">
        <span class="proj-name">${esc(r.title || 'Report')}</span>
        <span class="proj-badge ${chip.cls}${r.status === 'running' ? ' report-chip-running' : ''}">${esc(chip.label)}</span>
      </div>
      ${scoreBarsHtml(r.scores, esc)}
      ${r.status === 'error' ? `<div class="memory-desc report-scores">${esc(r.error || 'unknown error')}</div>` : ''}
      <div class="proj-actions">
        ${r.status !== 'running' && r.status !== 'error'
          ? `<button class="memory-toolbar-btn" data-action="toggle-body">${open ? 'Hide' : 'View'}</button>` : ''}
        ${isDraft ? `<button class="memory-toolbar-btn" data-action="approve">Approve</button>` : ''}
        ${isDraft ? `<button class="memory-toolbar-btn" data-action="dismiss">Dismiss</button>` : ''}
      </div>
      ${open ? `<div class="report-body" data-body-for="${esc(r.id)}">Loading…</div>` : ''}
    </div>`;
};

const _renderReports = () => {
  const box = _reportsBox();
  if (!box) return;
  if (!_reports.length) {
    box.innerHTML = '<p class="memory-desc">No reports yet — run a Developer Review on a project above.</p>';
    return;
  }
  box.innerHTML = _reports.map(_reportRow).join('');
  _openBodies.forEach((rid) => _loadBody(rid));
};

const _loadBody = async (rid) => {
  const el = document.querySelector(`[data-body-for="${rid}"]`);
  if (!el) return;
  try {
    const res = await fetch(`/api/council/reports/${rid}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `report fetch failed (${res.status})`);
    el.textContent = data.body || '(empty report)';
  } catch (e) {
    el.textContent = `Failed to load report: ${e.message}`;
  }
};

const _fetchReports = async () => {
  const res = await fetch('/api/council/reports');
  if (!res.ok) throw new Error(`reports list failed (${res.status})`);
  const prevRunning = _reports.filter((r) => r.status === 'running').map((r) => r.id);
  _reports = (await res.json()).reports || [];
  newlyDrafted(prevRunning, _reports).forEach(() =>
    uiModule.showToast('Developer Review ready — awaiting your approval below'));
};

// While a report is generating, keep the queue fresh (only while modal open).
const _scheduleReportPoll = () => {
  clearTimeout(_reportPollTimer);
  if (!isOpen() || !_reports.some((r) => r.status === 'running')) return;
  _reportPollTimer = setTimeout(async () => {
    try {
      await _fetchReports();
      _render();
      _renderReports();
    } catch (_) { /* transient — next poll retries */ }
    _scheduleReportPoll();
  }, 5000);
};

const _refresh = async () => {
  try {
    await _fetchProjects();
    await _fetchReports();
    _render();
    _renderReports();
  } catch (e) {
    uiModule.showError(`Projects: ${e.message}`);
  } finally {
    _scheduleReportPoll();   // a transient error must never kill the poll loop
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

const _review = async (pid) => {
  _reviewBusy.add(pid);
  _render();
  try {
    const res = await fetch('/api/council/reports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: pid }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `review failed (${res.status})`);
    uiModule.showToast('Developer Review generating — it will land below as a draft');
    await _refresh();
  } catch (e) {
    uiModule.showError(`Review: ${e.message}`);
  } finally {
    _reviewBusy.delete(pid);
    _render();
  }
};

const _reportAction = async (rid, action) => {
  try {
    const res = await fetch(`/api/council/reports/${rid}/${action}`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `${action} failed (${res.status})`);
    uiModule.showToast(`Report ${data.status}`);
    await _refresh();
  } catch (e) {
    uiModule.showError(`Report: ${e.message}`);
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
    if (btn.dataset.action === 'review') _review(pid);
    if (btn.dataset.action === 'archive') _archive(pid);
    if (btn.dataset.action === 'command-centre') {
      const project = _projects.find((p) => p.id === pid);
      if (project) {
        wireCommandCentre();
        openCommandCentre(project, _reports);
      }
    }
  });
  // Delegated handler for the approval queue (CSP-safe, survives re-renders).
  _reportsBox()?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('button[data-action]');
    if (!btn) return;
    const rid = btn.closest('[data-report-id]')?.dataset.reportId;
    if (!rid) return;
    if (btn.dataset.action === 'approve') _reportAction(rid, 'approve');
    if (btn.dataset.action === 'dismiss') _reportAction(rid, 'dismiss');
    if (btn.dataset.action === 'toggle-body') {
      if (_openBodies.has(rid)) _openBodies.delete(rid); else _openBodies.add(rid);
      _renderReports();
    }
  });
};

export const open = () => {
  const modal = _modal();
  if (!modal) return;
  _wire();
  modal.classList.remove('hidden');
  _refresh();
};

export const close = () => {
  clearTimeout(_reportPollTimer);
  _modal()?.classList.add('hidden');
};
export const isOpen = () => !!_modal() && !_modal().classList.contains('hidden');

export default { open, close, isOpen };
