// ============================================
// Council dashboard (Agent Council Phase 3)
// Tabs: Members (department status chips + run-a-report), Approvals (draft
// inbox), History (settled reports, filterable). Modal shell lives in
// index.html (#council-modal); this module renders into it.
// ============================================
import uiModule from './ui.js';
import { DEPT_COLORS, filterHistory, memberStatusChip, scoreBarsHtml, splitReports } from './councilLogic.js';
import { reportChip } from './projectsLogic.js';
import { officeHtml } from './councilOffice.js';

const esc = uiModule.esc;

const REPORT_TYPE_LABELS = {
  developer_review: 'Developer Review',
  market_opportunity: 'Market Opportunity Report',
  launch_strategy: 'Launch Strategy',
  architecture_review: 'Architecture Review',
  marketing_audit: 'Marketing Audit',
};

let _members = [];
let _reports = [];
let _projects = [];
let _tab = 'office';
let _historyDept = '';
let _runProject = '';   // run-form selections survive re-renders
let _runType = '';
let _runBusy = false;
let _openBodies = new Set();
let _pollTimer = null;

const _modal = () => document.getElementById('council-modal');
const _panel = () => document.getElementById('council-panel');

const _fetchAll = async () => {
  const [status, reports, projects] = await Promise.all([
    fetch('/api/council/status'),
    fetch('/api/council/reports'),
    fetch('/api/projects'),
  ]);
  if (!status.ok) throw new Error(`council status failed (${status.status})`);
  if (!reports.ok) throw new Error(`reports list failed (${reports.status})`);
  _members = (await status.json()).members || [];
  _reports = (await reports.json()).reports || [];
  _projects = projects.ok ? (await projects.json()).projects || [] : [];
};

const _projectName = (pid) => (_projects.find((p) => p.id === pid) || {}).name || pid;

// ── tab renderers ────────────────────────────────────────────────────────────

const _memberCard = (m) => {
  const chip = memberStatusChip(m.status);
  const color = DEPT_COLORS[m.department] || 'var(--fg)';
  const report = _reports.find((r) => r.id === m.current_report_id);
  const detail = m.status === 'Busy'
    ? `Generating: ${report ? report.title : 'a report'}`
    : m.status === 'Waiting'
      ? `Queued (position ${m.queue_position})`
      : 'Idle — ready for a report';
  return `
    <div class="proj-card admin-card council-member" style="--dept-color:${color}">
      <div class="proj-card-head">
        <span class="council-avatar">${esc((m.name || '?')[0].toUpperCase())}</span>
        <span class="proj-name">${esc(m.name)}</span>
        <span class="proj-badge ${chip.cls}${m.status === 'Busy' ? ' report-chip-running' : ''}">${esc(chip.label)}</span>
      </div>
      <div class="council-dept">${esc(m.department)} department</div>
      <div class="memory-desc report-scores">${esc(detail)}</div>
    </div>`;
};

const _runForm = () => {
  const projects = _projects.filter((p) => p.exists !== false);
  if (!projects.length) {
    return '<p class="memory-desc">No projects yet — scan a workspace in the Projects tool first.</p>';
  }
  const projOpts = projects
    .map((p) => `<option value="${esc(p.id)}"${p.id === _runProject ? ' selected' : ''}>${esc(p.name)}</option>`).join('');
  const typeOpts = Object.entries(REPORT_TYPE_LABELS)
    .map(([v, label]) => `<option value="${esc(v)}"${v === _runType ? ' selected' : ''}>${esc(label)}</option>`).join('');
  return `
    <div class="council-run-form">
      <select id="council-run-project" class="settings-input">${projOpts}</select>
      <select id="council-run-type" class="settings-input">${typeOpts}</select>
      <button id="council-run-btn" class="memory-toolbar-btn" ${_runBusy ? 'disabled' : ''}>
        ${_runBusy ? 'Queuing…' : 'Run report'}
      </button>
    </div>`;
};

const _reportRow = (r, { actions }) => {
  const chip = reportChip(r);
  const open = _openBodies.has(r.id);
  const isDraft = r.status === 'draft';
  const color = DEPT_COLORS[r.department] || 'var(--fg)';
  return `
    <div class="report-row admin-card council-member" data-report-id="${esc(r.id)}" style="--dept-color:${color}">
      <div class="proj-card-head">
        <span class="proj-name">${esc(r.title || 'Report')}</span>
        <span class="proj-badge ${chip.cls}${r.status === 'running' ? ' report-chip-running' : ''}">${esc(chip.label)}</span>
      </div>
      <div class="memory-desc report-scores">${esc(_projectName(r.project_id))} · ${esc(r.department || '')}</div>
      ${scoreBarsHtml(r.scores, esc)}
      ${r.status === 'error' ? `<div class="memory-desc report-scores">${esc(r.error || 'unknown error')}</div>` : ''}
      <div class="proj-actions">
        ${r.status !== 'running' && r.status !== 'error'
          ? `<button class="memory-toolbar-btn" data-action="toggle-body">${open ? 'Hide' : 'View'}</button>` : ''}
        ${actions && isDraft ? `<button class="memory-toolbar-btn" data-action="approve">Approve</button>` : ''}
        ${actions && isDraft ? `<button class="memory-toolbar-btn" data-action="dismiss">Dismiss</button>` : ''}
      </div>
      ${open ? `<div class="report-body" data-body-for="${esc(r.id)}">Loading…</div>` : ''}
    </div>`;
};

const _renderMembers = () => `
  <div class="projects-grid">${_members.map(_memberCard).join('')}</div>
  <h2 class="council-reports-title">Run a report</h2>
  <p class="memory-desc doclib-desc">Pick a project and a report type — the department drafts it for your approval.</p>
  ${_runForm()}`;

const _renderApprovals = () => {
  const { inbox } = splitReports(_reports);
  if (!inbox.length) return '<p class="memory-desc">Nothing awaiting approval.</p>';
  return `<div class="council-reports">${inbox.map((r) => _reportRow(r, { actions: true })).join('')}</div>`;
};

const _renderHistory = () => {
  const { history } = splitReports(_reports);
  const depts = [...new Set(history.map((r) => r.department).filter(Boolean))].sort();
  const opts = ['<option value="">All departments</option>']
    .concat(depts.map((d) => `<option value="${esc(d)}"${d === _historyDept ? ' selected' : ''}>${esc(d)}</option>`))
    .join('');
  const rows = filterHistory(history, { department: _historyDept || undefined });
  return `
    <div class="council-history-bar"><select id="council-history-dept" class="settings-input">${opts}</select></div>
    ${rows.length
      ? `<div class="council-reports">${rows.map((r) => _reportRow(r, { actions: false })).join('')}</div>`
      : '<p class="memory-desc">No settled reports yet.</p>'}`;
};

const _render = () => {
  const panel = _panel();
  if (!panel) return;
  const { inbox, history } = splitReports(_reports);
  const counts = { office: 0, members: 0, approvals: inbox.length, history: history.length };
  document.querySelectorAll('#council-tabs [data-council-tab]').forEach((b) => {
    const tab = b.dataset.councilTab;
    b.classList.toggle('active', tab === _tab);
    const n = counts[tab];
    b.textContent = `${tab[0].toUpperCase()}${tab.slice(1)}${n ? ` (${n})` : ''}`;
  });
  panel.innerHTML = _tab === 'office' ? officeHtml(_members, _reports, inbox.filter((r) => r.status === 'draft').length)
    : _tab === 'members' ? _renderMembers()
    : _tab === 'approvals' ? _renderApprovals()
    : _renderHistory();
  _openBodies.forEach((rid) => _loadBody(rid));
};

const _loadBody = async (rid) => {
  const el = document.querySelector(`#council-panel [data-body-for="${rid}"]`);
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

// ── actions ──────────────────────────────────────────────────────────────────

const _refresh = async () => {
  try {
    await _fetchAll();
    _render();
  } catch (e) {
    uiModule.showError(`Council: ${e.message}`);
  } finally {
    _schedulePoll();   // a transient error must never kill the poll loop
  }
};

const _schedulePoll = () => {
  clearTimeout(_pollTimer);
  const active = _reports.some((r) => r.status === 'running')
    || _members.some((m) => m.status !== 'Ready');
  if (!isOpen() || !active) return;
  _pollTimer = setTimeout(_refresh, 5000);
};

const _runReport = async () => {
  const project_id = document.getElementById('council-run-project')?.value;
  const report_type = document.getElementById('council-run-type')?.value;
  if (!project_id || !report_type || _runBusy) return;
  _runProject = project_id;
  _runType = report_type;
  _runBusy = true;
  _render();
  try {
    const res = await fetch('/api/council/reports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id, report_type }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `run failed (${res.status})`);
    uiModule.showToast(`${REPORT_TYPE_LABELS[report_type]} generating — watch the Approvals tab`);
    await _refresh();
  } catch (e) {
    uiModule.showError(`Run report: ${e.message}`);
  } finally {
    _runBusy = false;
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

let _wired = false;
const _wire = () => {
  if (_wired) return;
  _wired = true;
  document.getElementById('close-council-modal')?.addEventListener('click', close);
  document.getElementById('council-tabs')?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-council-tab]');
    if (!btn) return;
    _tab = btn.dataset.councilTab;
    _render();
  });
  // One delegated handler for everything inside the panel (CSP-safe).
  _panel()?.addEventListener('click', (ev) => {
    if (ev.target.closest('#council-run-btn')) return _runReport();
    const node = ev.target.closest('.office-node');
    if (node) {
      _historyDept = node.dataset.department || '';
      _tab = 'history';
      return _render();
    }
    if (ev.target.closest('[data-action="open-approvals"]')) {
      _tab = 'approvals';
      return _render();
    }
    const btn = ev.target.closest('button[data-action]');
    if (!btn) return;
    const rid = btn.closest('[data-report-id]')?.dataset.reportId;
    if (!rid) return;
    if (btn.dataset.action === 'approve') _reportAction(rid, 'approve');
    if (btn.dataset.action === 'dismiss') _reportAction(rid, 'dismiss');
    if (btn.dataset.action === 'toggle-body') {
      if (_openBodies.has(rid)) _openBodies.delete(rid); else _openBodies.add(rid);
      _render();
    }
  });
  _panel()?.addEventListener('change', (ev) => {
    if (ev.target.id === 'council-history-dept') {
      _historyDept = ev.target.value;
      _render();
    }
    // Remember run-form selections so re-renders (polls, refreshes) keep them.
    if (ev.target.id === 'council-run-project') _runProject = ev.target.value;
    if (ev.target.id === 'council-run-type') _runType = ev.target.value;
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
  clearTimeout(_pollTimer);
  _modal()?.classList.add('hidden');
};

export const isOpen = () => !!_modal() && !_modal().classList.contains('hidden');

export default { open, close, isOpen };
