// ============================================
// Council dashboard (Agent Council Phase 3)
// Tabs: Members (department status chips + run-a-report), Approvals (draft
// inbox), History (settled reports, filterable). Modal shell lives in
// index.html (#council-modal); this module renders into it.
// ============================================
import uiModule from './ui.js';
import { DEPT_COLORS, filterHistory, memberEditPayload, memberStatusChip, nextShaderMode, splitReports } from './councilLogic.js';
import { reportsByProject } from './projectsLogic.js';
import { officeHtml } from './councilOffice.js';
import { stackHtml } from './councilStack.js';
import councilShader from './councilShader.js';
import { sortModelIds } from './modelSort.js';

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
let _stackActive = new Map();   // projectId -> active report_type in its stack
let _bodyCache = new Map();     // report id -> loaded body (survives poll re-renders)
let _editing = null;   // council member being edited (card #10), or null
let _pollTimer = null;
let _shaderMode = (typeof localStorage !== 'undefined' && localStorage.getItem('councilShaderMode')) || 'subtle';

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
    <div class="proj-card admin-card council-member" data-department="${esc(m.department)}" style="--dept-color:${color}">
      <div class="proj-card-head">
        <span class="council-avatar">${esc((m.name || '?')[0].toUpperCase())}</span>
        <span class="proj-name">${esc(m.name)}</span>
        <span class="proj-badge ${chip.cls}${m.status === 'Busy' ? ' report-chip-running' : ''}">${esc(chip.label)}</span>
      </div>
      <div class="council-dept">${esc(m.department)} department</div>
      <div class="memory-desc report-scores">${esc(detail)}</div>
      <div class="proj-actions"><button class="memory-toolbar-btn" data-action="edit-member">Edit persona</button></div>
    </div>`;
};

const _memberForm = (m) => {
  const color = DEPT_COLORS[m.department] || 'var(--fg)';
  return `
    <div class="council-edit-form" data-member-id="${esc(m.id)}" style="--dept-color:${color}">
      <h2 class="council-reports-title">Edit ${esc(m.department)} member</h2>
      <label class="assistant-field"><span>Name</span>
        <input type="text" id="council-edit-name" class="settings-input" value="${esc(m.name || '')}"></label>
      <label class="assistant-field"><span>Personality (system prompt)</span>
        <textarea id="council-edit-personality" class="settings-input" rows="6">${esc(m.personality || '')}</textarea></label>
      <div class="assistant-field-row">
        <label class="assistant-field" style="flex:1;"><span>Model endpoint</span>
          <select id="council-edit-endpoint" class="settings-input"></select></label>
        <label class="assistant-field" style="flex:1;"><span>Model</span>
          <select id="council-edit-model" class="settings-input"></select></label>
      </div>
      <div class="council-run-form">
        <button id="council-edit-save" class="memory-toolbar-btn">Save</button>
        <button id="council-edit-cancel" class="memory-toolbar-btn">Cancel</button>
      </div>
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
      <button id="council-run-full-btn" class="memory-toolbar-btn proj-council-btn" ${_runBusy ? 'disabled' : ''}
        title="Run every department review, then a unified Council Review">
        ${_runBusy ? 'Queuing…' : 'Run full council'}
      </button>
    </div>`;
};

// Reports grouped into one consolidated stack per project (shared renderer).
const _stacksHtml = (reports, showActions) => {
  const groups = reportsByProject(reports);
  return `<div class="council-reports">${groups.map((g) => stackHtml(
    { projectId: g.projectId, name: _projectName(g.projectId), reports: g.reports },
    esc, { activeType: _stackActive.get(g.projectId), showActions },
  )).join('')}</div>`;
};

const _renderMembers = () => {
  if (_editing) return _memberForm(_editing);
  if (!_members.length) return '<p class="memory-desc">No council members yet.</p>';
  return `
    <div class="projects-grid">${_members.map(_memberCard).join('')}</div>
    <p class="memory-desc doclib-desc">Click a member's Edit persona to change its name, personality, model, or endpoint. Convene a council from the Office tab.</p>`;
};

const _renderApprovals = () => {
  const { inbox } = splitReports(_reports);
  if (!inbox.length) {
    return '<p class="memory-desc">Nothing awaiting approval — convene a council from the Office tab.</p>';
  }
  return _stacksHtml(inbox, true);
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
      ? _stacksHtml(rows, false)
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
  const active = _reports.some((r) => r.status === 'running')
    || _members.some((m) => m.status !== 'Ready');
  const draftCount = inbox.filter((r) => r.status === 'draft').length;
  panel.innerHTML = _tab === 'office' ? officeHtml(_members, _reports, draftCount, active) + _runForm()
    : _tab === 'members' ? _renderMembers()
    : _tab === 'approvals' ? _renderApprovals()
    : _renderHistory();
  panel.querySelectorAll('[data-body-for]').forEach((el) => _loadBody(el.dataset.bodyFor));
  // Office background shader: re-attach into the freshly-rendered canvas (the
  // persistent canvas keeps its GL context), or stop it when we leave Office.
  if (_tab === 'office') {
    const canvas = panel.querySelector('.office-canvas');
    if (canvas) {
      canvas.classList.toggle('shader-bold', _shaderMode === 'bold');
      councilShader.attach(canvas, { active, mode: _shaderMode });
    }
  } else {
    councilShader.stop();
  }
};

const _loadBody = async (rid) => {
  const el = document.querySelector(`#council-panel [data-body-for="${rid}"]`);
  if (!el) return;
  if (_bodyCache.has(rid)) { el.textContent = _bodyCache.get(rid); return; }
  try {
    const res = await fetch(`/api/council/reports/${rid}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `report fetch failed (${res.status})`);
    const body = data.body || '(empty report)';
    _bodyCache.set(rid, body);
    el.textContent = body;
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
  // Don't re-render out from under an open editor form.
  if (!isOpen() || _editing || !active) return;
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

const _runFullCouncil = async () => {
  const project_id = document.getElementById('council-run-project')?.value;
  if (!project_id || _runBusy) return;
  _runProject = project_id;
  _runBusy = true;
  _render();
  try {
    const res = await fetch('/api/council/run-full', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `run failed (${res.status})`);
    uiModule.showToast('Full council convened — watch the Office, then the Approvals tab');
    _tab = 'office';   // the office comes alive as each department reports in
    await _refresh();
  } catch (e) {
    uiModule.showError(`Full council: ${e.message}`);
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

// ── member editor (card #10) ──────────────────────────────────────────────────

const _openEditor = async (department) => {
  try {
    const res = await fetch('/api/council/members');
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `members fetch failed (${res.status})`);
    const member = (data.members || []).find((m) => m.department === department);
    if (!member) throw new Error('member not found');
    _editing = member;
    _render();
    _populateEndpointModel(member);   // fill the endpoint/model selects once
  } catch (e) {
    uiModule.showError(`Edit member: ${e.message}`);
  }
};

const _populateEndpointModel = (member) => {
  const epSelect = document.getElementById('council-edit-endpoint');
  const modelSelect = document.getElementById('council-edit-model');
  if (!epSelect || !modelSelect) return;
  fetch('/api/model-endpoints').then((r) => (r.ok ? r.json() : [])).then((eps) => {
    const endpoints = Array.isArray(eps) ? eps : [];
    let html = '<option value="">(use default)</option>';
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

const _saveMember = async () => {
  if (!_editing) return;
  const payload = memberEditPayload({
    name: document.getElementById('council-edit-name')?.value,
    personality: document.getElementById('council-edit-personality')?.value,
    model: document.getElementById('council-edit-model')?.value,
    endpoint: document.getElementById('council-edit-endpoint')?.value,
  });
  try {
    const res = await fetch(`/api/council/members/${_editing.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `save failed (${res.status})`);
    uiModule.showToast('Member updated');
    _editing = null;
    await _refresh();
  } catch (e) {
    uiModule.showError(`Save member: ${e.message}`);
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
    if (ev.target.closest('#council-run-full-btn')) return _runFullCouncil();
    if (ev.target.closest('#council-run-btn')) return _runReport();
    if (ev.target.closest('#council-edit-save')) return _saveMember();
    if (ev.target.closest('#council-edit-cancel')) { _editing = null; return _render(); }
    const editBtn = ev.target.closest('[data-action="edit-member"]');
    if (editBtn) {
      const dept = editBtn.closest('[data-department]')?.dataset.department;
      if (dept) _openEditor(dept);
      return;
    }
    // Clicking a department in the Office opens that member's persona (not the
    // old jump to a frequently-empty filtered History).
    const node = ev.target.closest('.office-node');
    if (node) {
      _tab = 'members';
      return _openEditor(node.dataset.department || '');
    }
    if (ev.target.closest('[data-action="toggle-shader"]')) {
      _shaderMode = nextShaderMode(_shaderMode);
      try { localStorage.setItem('councilShaderMode', _shaderMode); } catch (_) { /* private mode */ }
      const canvas = _panel()?.querySelector('.office-canvas');
      if (canvas) canvas.classList.toggle('shader-bold', _shaderMode === 'bold');
      councilShader.setMode(_shaderMode);
      return;
    }
    if (ev.target.closest('[data-action="open-approvals"]')) {
      _tab = 'approvals';
      return _render();
    }
    const tabBtn = ev.target.closest('[data-report-tab]');
    if (tabBtn) {
      const pid = tabBtn.closest('[data-project-id]')?.dataset.projectId;
      if (pid) { _stackActive.set(pid, tabBtn.dataset.reportTab); _render(); }
      return;
    }
    const btn = ev.target.closest('button[data-action]');
    if (!btn) return;
    const rid = btn.closest('[data-report-id]')?.dataset.reportId;
    if (!rid) return;
    if (btn.dataset.action === 'approve') _reportAction(rid, 'approve');
    if (btn.dataset.action === 'dismiss') _reportAction(rid, 'dismiss');
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
  councilShader.stop();
  _modal()?.classList.add('hidden');
};

export const isOpen = () => !!_modal() && !_modal().classList.contains('hidden');

export default { open, close, isOpen };
