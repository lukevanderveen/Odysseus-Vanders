// ============================================
// Shared report-stack renderer — the single representation of a project's
// council reports, used by the Projects Command Centre and the Council
// Approvals/History tabs alike. Pure string builders (no DOM), so they're
// Node-testable and the three former copies can't drift again.
// ============================================
import { councilTabs, reportChip } from './projectsLogic.js';
import {
  DEPT_COLORS, radarChartSvg, scoreBarsHtml, scoreEntries,
} from './councilLogic.js';

// One report's panel: chip, scores (+ radar on the unified Overview), draft
// actions (only when the host allows them), and a body placeholder the host
// fills on demand. opts: { showActions }.
export const stackPanelHtml = (r, esc, opts = {}) => {
  if (!r) return '';
  const chip = reportChip(r);
  const color = DEPT_COLORS[r.department] || 'var(--fg)';
  const viewable = r.status !== 'running' && r.status !== 'error';
  const radar = r.report_type === 'council_review'
    ? `<div class="radar-wrap">${radarChartSvg(scoreEntries(r.scores), esc, { color })}</div>` : '';
  const actions = opts.showActions && r.status === 'draft'
    ? `<div class="proj-actions">
        <button class="memory-toolbar-btn" data-action="approve">Approve</button>
        <button class="memory-toolbar-btn" data-action="dismiss">Dismiss</button>
      </div>` : '';
  return `
    <div class="report-row" data-report-id="${esc(r.id)}" style="--dept-color:${color}">
      <div class="proj-card-head">
        <span class="proj-name">${esc(r.title || 'Report')}</span>
        <span class="proj-badge ${chip.cls}${r.status === 'running' ? ' report-chip-running' : ''}">${esc(chip.label)}</span>
      </div>
      ${radar}
      ${scoreBarsHtml(r.scores, esc)}
      ${r.status === 'error' ? `<div class="memory-desc report-scores">${esc(r.error || 'unknown error')}</div>` : ''}
      ${actions}
      ${viewable ? `<div class="report-body" data-body-for="${esc(r.id)}">Loading…</div>` : ''}
    </div>`;
};

// One project's consolidated stack: a colour-coded tab per agent review (the
// council synthesis "Overview" first) over a single active-report panel.
// group: { projectId, name, reports }. opts: { activeType, showActions }.
export const stackHtml = (group, esc, opts = {}) => {
  const reports = (group || {}).reports || [];
  const tabs = councilTabs(reports);
  if (!tabs.length) return '';
  const wanted = opts.activeType;
  const activeType = tabs.some((t) => t.type === wanted) ? wanted : tabs[0].type;
  const tabBar = tabs.map((t) => {
    const color = DEPT_COLORS[t.department] || 'var(--fg)';
    return `<button class="report-tab${t.type === activeType ? ' active' : ''}"
      data-report-tab="${esc(t.type)}" style="--dept-color:${color}">${esc(t.label)}</button>`;
  }).join('');
  const active = tabs.find((t) => t.type === activeType);
  const report = reports.find((r) => r.id === active.reportId);
  return `
    <div class="proj-stack admin-card" data-project-id="${esc((group || {}).projectId || '')}">
      <div class="proj-stack-head"><span class="proj-name">${esc((group || {}).name || '')}</span></div>
      <div class="proj-stack-tabs">${tabBar}</div>
      ${stackPanelHtml(report, esc, opts)}
    </div>`;
};
