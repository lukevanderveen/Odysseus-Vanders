// ============================================
// Council office view + Project Command Centre (Agent Council Phase 4)
// Office: department nodes arranged around a central Council hub.
// Command Centre: the one deep view of a single project — score, activity, and
// its live, actionable council report stack (shared with the Council tabs).
// ============================================
import uiModule from './ui.js';
import {
  DEPT_COLORS, commitAgeLabel, latestScoredReport, memberStatusChip,
  officeLayout, projectHealth,
} from './councilLogic.js';
import { stackHtml } from './councilStack.js';

const esc = uiModule.esc;

// ── Office view (rendered into the council modal's panel) ───────────────────
// Layout runs in a 0–100 virtual space; nodes are %-positioned divs and the
// connector lines an SVG underlay sharing the same coordinates, so the whole
// scene scales with the modal.

const _officeNode = (m, pos, reports) => {
  const chip = memberStatusChip(m.status);
  const color = DEPT_COLORS[m.department] || 'var(--fg)';
  const report = (reports || []).find((r) => r.id === m.current_report_id);
  const detail = m.status === 'Busy'
    ? esc(report ? report.title : 'Generating…')
    : m.status === 'Waiting' ? `Queued #${esc(String(m.queue_position ?? ''))}` : 'Idle';
  return `
    <div class="office-node" data-department="${esc(m.department)}"
         style="left:${pos.x}%;top:${pos.y}%;--dept-color:${color}">
      <span class="council-avatar">${esc((m.name || '?')[0].toUpperCase())}</span>
      <div class="office-node-text">
        <span class="office-node-name">${esc(m.name)}</span>
        <span class="office-node-detail">${detail}</span>
      </div>
      <span class="proj-badge ${chip.cls}${m.status === 'Busy' ? ' report-chip-running' : ''}">${esc(chip.label)}</span>
    </div>`;
};

// active: a report is generating — the scene comes alive (flowing connector
// lines, a pulsing hub) so a full-council run reads as work in progress.
export const officeHtml = (members, reports, draftCount, active = false) => {
  if (!members.length) {
    return '<p class="memory-desc office-empty">No council members yet — open the Members tab to seed the council.</p>';
  }
  const layout = officeLayout(members, 100, 100);
  const lines = layout.map((p) =>
    `<line x1="50" y1="50" x2="${p.x}" y2="${p.y}"/>`).join('');
  const nodes = members.map((m, i) => _officeNode(m, layout[i], reports)).join('');
  return `
    <div class="office-canvas${active ? ' office-active' : ''}">
      <svg class="office-lines" viewBox="0 0 100 100" preserveAspectRatio="none">${lines}</svg>
      ${nodes}
      <button class="office-hub${active ? ' office-hub-active' : ''}" data-action="open-approvals" title="Open the approval inbox">
        <span class="office-hub-label">Council</span>
        ${draftCount ? `<span class="office-hub-count">${esc(String(draftCount))}</span>` : ''}
      </button>
      <button class="office-shader-toggle" data-action="toggle-shader" title="Toggle background intensity">✦</button>
    </div>
    <p class="memory-desc doclib-desc office-hint">Click a department to view or edit its persona, or the Council hub for approvals. Convene the council below.</p>`;
};

// ── Project Command Centre ───────────────────────────────────────────────────
// The single per-project deep view: opened from a project card, it shows the
// headline score, activity/index freshness, and the live report stack with
// openable bodies + approve/dismiss. It re-renders from a getter so a run in
// progress updates it in place.

const _modal = () => document.getElementById('command-centre-modal');
const _body = () => document.getElementById('command-centre-body');

let _cc = { project: null, getReports: null, onAction: null, activeTab: null };
const _ccBodyCache = new Map();

const _ccReports = () => {
  const all = _cc.getReports ? _cc.getReports() : [];
  return all.filter((r) => r.project_id === (_cc.project || {}).id);
};

const _indexStatusLine = (project) => {
  if (!project.indexed_at && !project.deep_indexed_at) {
    return '<span class="cc-warn">Not indexed — run Index in the Projects tool.</span>';
  }
  if (project.stale) return '<span class="cc-warn">Index may be stale. Run Deep Index.</span>';
  const when = project.deep_indexed_at || project.indexed_at;
  return `Index fresh — last indexed ${esc(String(when).slice(0, 10))}`;
};

const _ccLoadBody = async (rid) => {
  const el = _body()?.querySelector(`[data-body-for="${rid}"]`);
  if (!el) return;
  if (_ccBodyCache.has(rid)) { el.textContent = _ccBodyCache.get(rid); return; }
  try {
    const res = await fetch(`/api/council/reports/${rid}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `report fetch failed (${res.status})`);
    const body = data.body || '(empty report)';
    _ccBodyCache.set(rid, body);
    el.textContent = body;
  } catch (e) {
    el.textContent = `Failed to load report: ${e.message}`;
  }
};

export const renderCommandCentre = () => {
  const body = _body();
  const project = _cc.project;
  if (!body || !project) return;
  const mine = _ccReports();
  const latest = latestScoredReport(mine);
  const health = projectHealth(mine);
  const chips = (project.stack || [])
    .map((s) => `<span class="proj-chip">${esc(s)}</span>`).join('');
  const commit = commitAgeLabel(health.lastCommitDays);
  const group = { projectId: project.id, name: project.name, reports: mine };
  body.innerHTML = `
    <div class="cc-head">
      <div>
        <h2 class="cc-title">${esc(project.name)}</h2>
        <div class="proj-chips">${chips}</div>
        <div class="memory-desc report-scores" title="${esc(project.path || '')}">${esc(project.path || '')}</div>
      </div>
      <div class="cc-score">
        <span class="cc-score-num">${health.score === null ? '—' : esc(String(health.score))}</span>
        <span class="cc-score-denom">/100</span>
        <span class="cc-score-hint">${health.score === null ? 'no scored report yet' : `from ${esc((latest || {}).title || 'latest report')}`}</span>
      </div>
    </div>
    <div class="memory-desc report-scores">${commit
      ? `Last commit ${esc(commit)} (as of the latest report).`
      : 'Commit activity unknown (not a git repo, or no scored report yet).'}</div>
    <div class="memory-desc report-scores">${_indexStatusLine(project)}</div>
    <h3 class="cc-section">Council reviews</h3>
    ${stackHtml(group, esc, { activeType: _cc.activeTab, showActions: true })
      || '<p class="memory-desc">No reports yet — run a Developer Review or full Council from the project card.</p>'}`;
  body.querySelectorAll('[data-body-for]').forEach((el) => _ccLoadBody(el.dataset.bodyFor));
};

export const openCommandCentre = (project, opts = {}) => {
  const modal = _modal();
  if (!modal || !_body()) return;
  wireCommandCentre();
  _cc = {
    project,
    getReports: opts.getReports || (() => []),
    onAction: opts.onAction || null,
    activeTab: null,
  };
  renderCommandCentre();
  modal.classList.remove('hidden');
};

// Re-render the open Command Centre from fresh data (driven by the Projects
// poll so a run in progress lights up live). No-op while it's closed.
export const refreshCommandCentre = () => {
  if (_modal() && !_modal().classList.contains('hidden')) renderCommandCentre();
};

export const closeCommandCentre = () => _modal()?.classList.add('hidden');

let _wired = false;
export const wireCommandCentre = () => {
  if (_wired) return;
  _wired = true;
  document.getElementById('close-command-centre-modal')
    ?.addEventListener('click', closeCommandCentre);
  // One delegated handler: tab switches and draft approve/dismiss.
  _body()?.addEventListener('click', (ev) => {
    const tabBtn = ev.target.closest('[data-report-tab]');
    if (tabBtn) { _cc.activeTab = tabBtn.dataset.reportTab; return renderCommandCentre(); }
    const btn = ev.target.closest('button[data-action]');
    if (!btn) return;
    const rid = btn.closest('[data-report-id]')?.dataset.reportId;
    if (!rid || !_cc.onAction) return;
    if (btn.dataset.action === 'approve') _cc.onAction(rid, 'approve');
    if (btn.dataset.action === 'dismiss') _cc.onAction(rid, 'dismiss');
  });
};
