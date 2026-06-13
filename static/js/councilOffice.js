// ============================================
// Council office view + Project Command Centre (Agent Council Phase 4)
// Pure presentation over existing data: department nodes arranged around a
// central Council hub, and a per-project popup with the overall score, score
// bars, index freshness, and latest reports. No new backend.
// ============================================
import uiModule from './ui.js';
import {
  DEPT_COLORS, latestScoredReport, memberStatusChip, officeLayout,
  overallScore, scoreBarsHtml,
} from './councilLogic.js';
import { reportChip } from './projectsLogic.js';

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

export const officeHtml = (members, reports, draftCount) => {
  if (!members.length) return '<p class="memory-desc">No council members yet.</p>';
  const layout = officeLayout(members, 100, 100);
  const lines = layout.map((p) =>
    `<line x1="50" y1="50" x2="${p.x}" y2="${p.y}"/>`).join('');
  const nodes = members.map((m, i) => _officeNode(m, layout[i], reports)).join('');
  return `
    <div class="office-canvas">
      <svg class="office-lines" viewBox="0 0 100 100" preserveAspectRatio="none">${lines}</svg>
      ${nodes}
      <button class="office-hub" data-action="open-approvals" title="Open the approval inbox">
        <span class="office-hub-label">Council</span>
        ${draftCount ? `<span class="office-hub-count">${esc(String(draftCount))}</span>` : ''}
      </button>
    </div>
    <p class="memory-desc doclib-desc office-hint">Click a department for its report history, or the Council hub for approvals.</p>`;
};

// ── Project Command Centre ───────────────────────────────────────────────────

const _modal = () => document.getElementById('command-centre-modal');
const _body = () => document.getElementById('command-centre-body');

const _indexStatusLine = (project) => {
  if (!project.indexed_at && !project.deep_indexed_at) {
    return '<span class="cc-warn">Not indexed — run Index in the Projects tool.</span>';
  }
  if (project.stale) {
    return '<span class="cc-warn">Index may be stale. Run Deep Index.</span>';
  }
  const when = project.deep_indexed_at || project.indexed_at;
  return `Index fresh — last indexed ${esc(String(when).slice(0, 10))}`;
};

// Real git signal captured at report time (scores.grounded._signals).
const _activityLine = (latest) => {
  const days = latest?.scores?.grounded?._signals?.days_since_commit;
  if (days === null || days === undefined) return 'Commit activity unknown (not a git repo, or no scored report yet).';
  const d = Math.round(days);
  const when = d === 0 ? 'today' : d === 1 ? 'yesterday' : `${d} days ago`;
  return `Last commit ${esc(when)} (as of the latest report).`;
};

const _reportLine = (r) => {
  const chip = reportChip(r);
  const color = DEPT_COLORS[r.department] || 'var(--fg)';
  return `
    <div class="cc-report" style="--dept-color:${color}">
      <span class="proj-name">${esc(r.title || 'Report')}</span>
      <span class="cc-report-date">${esc(String(r.created_at || '').slice(0, 10))}</span>
      <span class="proj-badge ${chip.cls}">${esc(chip.label)}</span>
    </div>`;
};

export const openCommandCentre = (project, reports) => {
  const modal = _modal();
  const body = _body();
  if (!modal || !body) return;

  const mine = (reports || []).filter((r) => r.project_id === project.id);
  const latest = latestScoredReport(mine);
  const score = latest ? overallScore(latest.scores) : null;
  const chips = (project.stack || [])
    .map((s) => `<span class="proj-chip">${esc(s)}</span>`).join('');
  const recent = [...mine].sort(
    (a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')),
  ).slice(0, 5);

  body.innerHTML = `
    <div class="cc-head">
      <div>
        <h2 class="cc-title">${esc(project.name)}</h2>
        <div class="proj-chips">${chips}</div>
        <div class="memory-desc report-scores" title="${esc(project.path || '')}">${esc(project.path || '')}</div>
      </div>
      <div class="cc-score">
        <span class="cc-score-num">${score === null ? '—' : esc(String(score))}</span>
        <span class="cc-score-denom">/100</span>
        <span class="cc-score-hint">${score === null ? 'no scored report yet' : `from ${esc(latest.title || 'latest report')}`}</span>
      </div>
    </div>
    ${latest ? scoreBarsHtml(latest.scores, esc) : '<p class="memory-desc">Run a report to score this project.</p>'}
    <h3 class="cc-section">Activity & index</h3>
    <div class="memory-desc report-scores">${_activityLine(latest)}</div>
    <div class="memory-desc report-scores">${_indexStatusLine(project)}</div>
    <h3 class="cc-section">Latest reports</h3>
    ${recent.length
      ? recent.map(_reportLine).join('')
      : '<p class="memory-desc">No reports for this project yet.</p>'}`;

  modal.classList.remove('hidden');
};

export const closeCommandCentre = () => _modal()?.classList.add('hidden');

let _wired = false;
export const wireCommandCentre = () => {
  if (_wired) return;
  _wired = true;
  document.getElementById('close-command-centre-modal')
    ?.addEventListener('click', closeCommandCentre);
};
