// ============================================
// Council dashboard — pure logic (no DOM, Node-importable for tests)
// Rendering decisions for council.js: member status chips, the
// approvals-inbox vs history split, and history filtering.
// ============================================

export const DEPT_COLORS = {
  research: '#8a7bd8',
  business: '#d9a13b',
  architect: '#5b8abf',
  developer: '#5dbf6e',
  marketing: '#d873a8',
  council: '#9cdef2',   // the unified-synthesis "department" (matches --fg accent)
};

// status: "Ready" | "Waiting" | "Busy" from GET /api/council/status.
export const memberStatusChip = (status) => {
  const map = {
    Ready: { label: 'Ready', cls: 'council-chip-ready' },
    Waiting: { label: 'Waiting', cls: 'council-chip-waiting' },
    Busy: { label: 'Busy', cls: 'council-chip-busy' },
  };
  return map[status] || map.Ready;
};

// Inbox = needs the user's attention or will shortly (running, draft).
// History = settled outcomes (approved, dismissed, error).
export const splitReports = (reports) => {
  const inbox = [];
  const history = [];
  for (const r of reports || []) {
    (r.status === 'running' || r.status === 'draft' ? inbox : history).push(r);
  }
  return { inbox, history };
};

export const filterHistory = (reports, { department, projectId } = {}) =>
  (reports || []).filter((r) =>
    (!department || r.department === department)
    && (!projectId || r.project_id === projectId));

// Score dimensions in display order — grounded (computed from real repo
// signals) lead, judged (LLM opinion, always tagged AI in the UI) follow.
const GROUNDED_DIMS = [
  ['technical_readiness', 'Technical readiness'],
  ['recent_activity', 'Recent activity'],
];
const JUDGED_DIMS = [
  ['launch_readiness', 'Launch readiness'],
  ['monetisation_clarity', 'Monetisation clarity'],
  ['marketability', 'Marketability'],
  ['ai_automation_potential', 'AI automation potential'],
];

// scores: {"grounded": {...}, "judged": {...}} → ordered entries for bars.
export const scoreEntries = (scores) => {
  const g = (scores || {}).grounded || {};
  const j = (scores || {}).judged || {};
  const out = [];
  for (const [key, label] of GROUNDED_DIMS) {
    if (Number.isFinite(g[key])) out.push({ key, label, value: g[key], judged: false });
  }
  for (const [key, label] of JUDGED_DIMS) {
    if (Number.isFinite(j[key])) out.push({ key, label, value: j[key], judged: true });
  }
  return out;
};

// Spatial office view: place member nodes evenly on an ellipse around the
// canvas centre, first node at top-centre, clockwise in member order.
export const officeLayout = (members, width, height) => {
  const list = members || [];
  if (!list.length) return [];
  const cx = width / 2;
  const cy = height / 2;
  const rx = width * 0.38;
  const ry = height * 0.34;
  return list.map((m, i) => {
    const angle = -Math.PI / 2 + (i * 2 * Math.PI) / list.length;
    return {
      department: m.department,
      x: Math.round(cx + rx * Math.cos(angle)),
      y: Math.round(cy + ry * Math.sin(angle)),
    };
  });
};

// Single /100 headline for the Command Centre: plain mean of every available
// dimension (grounded and judged alike). Null when nothing is scored yet —
// show "no score", never invent one (the Atlas "20/100, Stage: unknown" trap).
export const overallScore = (scores) => {
  const entries = scoreEntries(scores);
  if (!entries.length) return null;
  return Math.round(entries.reduce((sum, e) => sum + e.value, 0) / entries.length);
};

// Newest report that actually carries scores — the Command Centre's source.
export const latestScoredReport = (reports) => {
  const scored = (reports || []).filter((r) => scoreEntries(r.scores).length > 0);
  if (!scored.length) return null;
  return [...scored].sort(
    (a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')),
  )[0];
};

// Office background shader intensity toggle: subtle underlay ⇄ bold full-bleed.
// Anything that isn't already "bold" settles to "subtle" (the safe default).
export const nextShaderMode = (cur) => (cur === 'subtle' ? 'bold' : cur === 'bold' ? 'subtle' : 'subtle');

// Human commit-age from a day count (scores.grounded._signals.days_since_commit).
// Null when there is no signal — callers show their own "unknown" copy.
export const commitAgeLabel = (days) => {
  if (days === null || days === undefined) return null;
  const d = Math.round(days);
  return d === 0 ? 'today' : d === 1 ? 'yesterday' : `${d} days ago`;
};

// At-a-glance facts for a project card: the headline /100 from its newest
// scored report, how many reports it has, how many await approval, and the
// last-commit age captured at score time. reports = that project's reports.
export const projectHealth = (reports) => {
  const list = reports || [];
  const latest = latestScoredReport(list);
  return {
    score: latest ? overallScore(latest.scores) : null,
    reviewCount: list.length,
    draftCount: list.filter((r) => r.status === 'draft').length,
    lastCommitDays: latest?.scores?.grounded?._signals?.days_since_commit ?? null,
  };
};

// Atlas-style labelled score bars; pure string builder so it's Node-testable
// and shareable between the Projects and Council modals. esc is the caller's
// HTML escaper.
export const scoreBarsHtml = (scores, esc) => {
  const entries = scoreEntries(scores);
  if (!entries.length) return '';
  // uiModule.esc is string-only — numbers must be stringified before escaping.
  const rows = entries.map((e) => `
    <div class="score-bar-row">
      <span class="score-bar-label">${esc(e.label)}${e.judged ? '<span class="score-ai-tag">AI</span>' : ''}</span>
      <span class="score-bar-track"><span class="score-bar-fill${e.judged ? ' judged' : ''}" style="width:${Math.max(0, Math.min(100, e.value))}%"></span></span>
      <span class="score-bar-val">${esc(String(e.value))}</span>
    </div>`).join('');
  return `<div class="score-bars">${rows}</div>`;
};

// Council member editor (card #10): normalise raw form values into the PUT
// payload. A blank name is dropped (so an edit never blanks the member); empty
// model/endpoint selects become null (clear the override).
export const memberEditPayload = (v = {}) => ({
  name: (v.name || '').trim() || null,
  personality: v.personality ?? null,
  model: v.model || null,
  endpoint_url: v.endpoint || null,
});

// Hand-rolled SVG radar/spider chart over the score dimensions — the council
// scorecard's "fancier" view. Pure string builder (Node-testable); needs ≥3
// axes to form a polygon, returns '' otherwise. entries: scoreEntries() output;
// esc is the caller's HTML escaper (string-only). opts.color sets the fill hue.
export const radarChartSvg = (entries, esc, opts = {}) => {
  const dims = (entries || []).filter((e) => Number.isFinite(e.value));
  if (dims.length < 3) return '';
  const color = opts.color || 'var(--fg)';
  const cx = 110;
  const cy = 100;
  const maxR = 70;
  const n = dims.length;
  const angle = (i) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
  const pt = (i, r) => [
    +(cx + r * Math.cos(angle(i))).toFixed(1),
    +(cy + r * Math.sin(angle(i))).toFixed(1),
  ];
  const ring = (level) =>
    dims.map((_, i) => pt(i, maxR * level).join(',')).join(' ');
  const grids = [0.25, 0.5, 0.75, 1].map((l) =>
    `<polygon class="radar-grid" points="${ring(l)}"/>`).join('');
  const spokes = dims.map((_, i) => {
    const [x, y] = pt(i, maxR);
    return `<line class="radar-spoke" x1="${cx}" y1="${cy}" x2="${x}" y2="${y}"/>`;
  }).join('');
  const dataPts = dims.map((e, i) =>
    pt(i, (Math.max(0, Math.min(100, e.value)) / 100) * maxR).join(',')).join(' ');
  const labels = dims.map((e, i) => {
    const [x, y] = pt(i, maxR + 14);
    const anchor = x > cx + 1 ? 'start' : x < cx - 1 ? 'end' : 'middle';
    return `<text class="radar-axis-label" x="${x}" y="${y}" text-anchor="${anchor}"
      >${esc(e.label)}${e.judged ? ' (AI)' : ''}</text>`;
  }).join('');
  return `
    <svg class="radar-chart" viewBox="0 0 220 200" style="--radar-color:${color}">
      ${grids}${spokes}
      <polygon class="radar-area" points="${dataPts}"/>
      ${labels}
    </svg>`;
};
