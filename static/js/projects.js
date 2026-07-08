// ============================================
// Projects tab — auto-discovered local code projects (Agent Council Phases 1–2)
// A clean grid of project cards: stack chips, staleness badge, an at-a-glance
// health score + last-commit line, and Index / Deep Index / Developer Review /
// Run full council / Archive actions. Each card opens the Command Centre — the
// one place a project's council reviews live (score, radar, tabbed bodies,
// approve/dismiss). Modal shell lives in index.html (#projects-modal).
// ============================================
import uiModule from './ui.js';
import {
  newlyDrafted, projectStatusBadge, sortProjects,
} from './projectsLogic.js';
import { commitAgeLabel, projectHealth } from './councilLogic.js';
import {
  openCommandCentre, refreshCommandCentre, wireCommandCentre,
} from './councilOffice.js';

const esc = uiModule.esc;
let _projects = [];
let _reports = [];
let _busy = new Set();
let _deepBusy = new Set();
let _reviewBusy = new Set();   // optimistic — covers the gap until the running row arrives
let _fullBusy = new Set();     // optimistic — full-council run kicked off
let _reportPollTimer = null;

const _modal = () => document.getElementById('projects-modal');
const _grid = () => document.getElementById('projects-grid');

const _fetchProjects = async () => {
  const res = await fetch('/api/projects');
  if (!res.ok) throw new Error(`projects list failed (${res.status})`);
  _projects = (await res.json()).projects || [];
};

// At-a-glance health + a link into the Command Centre, where the reviews live.
const _cardMeta = (p) => {
  const health = projectHealth(_reports.filter((r) => r.project_id === p.id));
  const commit = commitAgeLabel(health.lastCommitDays);
  const reviews = health.reviewCount
    ? `<button class="proj-reviews-link" data-action="command-centre">${health.reviewCount} review${health.reviewCount === 1 ? '' : 's'}${health.draftCount ? ` · ${health.draftCount} to approve` : ''} ›</button>`
    : '<span class="memory-desc proj-no-reviews">No reviews yet</span>';
  const activity = commit ? `<span class="proj-activity">Last commit ${esc(commit)}</span>` : '';
  return { score: health.score, metaHtml: `${reviews}${activity}` };
};

const _card = (p) => {
  const badge = projectStatusBadge(p);
  const chips = (p.stack || [])
    .map((s) => `<span class="proj-chip">${esc(s)}</span>`)
    .join('');
  const busy = _busy.has(p.id);
  const deepBusy = _deepBusy.has(p.id);
  const fullBusy = _fullBusy.has(p.id);
  const reviewRunning = _reviewBusy.has(p.id) || fullBusy
    || _reports.some((r) => r.project_id === p.id && r.status === 'running');
  const { score, metaHtml } = _cardMeta(p);
  const scoreChip = score === null ? ''
    : `<span class="proj-score" title="Overall score from the latest report">${esc(String(score))}<span class="proj-score-denom">/100</span></span>`;
  const summary = p.deep_summary
    ? `<details class="proj-summary"><summary>Summary</summary><div class="proj-summary-body">${esc(p.deep_summary)}</div></details>`
    : '';
  return `
    <div class="proj-card admin-card" data-project-id="${esc(p.id)}">
      <div class="proj-card-head">
        <button class="proj-name proj-name-btn" data-action="command-centre" title="Open Command Centre — ${esc(p.path)}">${esc(p.name)}</button>
        ${scoreChip}
        <span class="proj-badge ${badge.cls}">${esc(badge.label)}</span>
      </div>
      <div class="proj-chips">${chips}</div>
      <div class="proj-meta">${metaHtml}</div>
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
        <button class="memory-toolbar-btn proj-council-btn" data-action="run-full" ${reviewRunning || !p.exists ? 'disabled' : ''}>
          ${fullBusy ? 'Convening…' : 'Run full council'}
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

const _fetchReports = async () => {
  const res = await fetch('/api/council/reports');
  if (!res.ok) throw new Error(`reports list failed (${res.status})`);
  const prevRunning = _reports.filter((r) => r.status === 'running').map((r) => r.id);
  _reports = (await res.json()).reports || [];
  newlyDrafted(prevRunning, _reports).forEach(() =>
    uiModule.showToast('A council report is ready — open the project to approve it'));
};

// While a report is generating, keep cards + the open Command Centre fresh.
const _scheduleReportPoll = () => {
  clearTimeout(_reportPollTimer);
  if (!isOpen() || !_reports.some((r) => r.status === 'running')) return;
  _reportPollTimer = setTimeout(async () => {
    try {
      await _fetchReports();
      _render();
      refreshCommandCentre();
    } catch (_) { /* transient — next poll retries */ }
    _scheduleReportPoll();
  }, 5000);
};

const _refresh = async () => {
  try {
    await _fetchProjects();
    await _fetchReports();
    _render();
    refreshCommandCentre();
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
    uiModule.showToast('Developer Review generating — open the project to watch it land');
    await _refresh();
  } catch (e) {
    uiModule.showError(`Review: ${e.message}`);
  } finally {
    _reviewBusy.delete(pid);
    _render();
  }
};

const _runFull = async (pid) => {
  _fullBusy.add(pid);
  _render();
  try {
    const res = await fetch('/api/council/run-full', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: pid }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `run failed (${res.status})`);
    uiModule.showToast('Full council convened — departments report in turn, then a Council Review draft');
    await _refresh();
  } catch (e) {
    uiModule.showError(`Council run: ${e.message}`);
  } finally {
    _fullBusy.delete(pid);
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

const _openCentre = (pid) => {
  const project = _projects.find((p) => p.id === pid);
  if (!project) return;
  wireCommandCentre();
  openCommandCentre(project, { getReports: () => _reports, onAction: _reportAction });
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
    if (btn.dataset.action === 'run-full') _runFull(pid);
    if (btn.dataset.action === 'archive') _archive(pid);
    if (btn.dataset.action === 'command-centre') _openCentre(pid);
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
