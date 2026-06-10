// ============================================
// Projects tab — pure logic (no DOM, Node-importable for tests)
// Rendering decisions for projects.js: status badge + card ordering.
// ============================================

// p: {exists, indexed_at, stale} from GET /api/projects
export const projectStatusBadge = (p) => {
  if (!p.exists) return { label: 'Missing', cls: 'proj-badge-missing' };
  if (!p.indexed_at) return { label: 'Not indexed', cls: 'proj-badge-none' };
  if (p.stale) return { label: 'Index may be stale', cls: 'proj-badge-stale' };
  return { label: 'Indexed', cls: 'proj-badge-ok' };
};

// Clean a user-entered workspace-roots list: trim, drop empties, strip
// trailing slashes (a bare drive root like "D:\" keeps its backslash),
// dedupe case-insensitively (Windows paths). Order of first occurrence wins.
export const normalizeRoots = (roots) => {
  const seen = new Set();
  const out = [];
  for (const raw of roots || []) {
    let r = String(raw || '').trim();
    if (!r) continue;
    while (r.length > 1 && (r.endsWith('/') || r.endsWith('\\')) && !/^[A-Za-z]:[\\/]$/.test(r)) {
      r = r.slice(0, -1);
    }
    const key = r.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(r);
  }
  return out;
};

// r: {status} from GET /api/council/reports — chip for the approval queue.
export const reportChip = (r) => {
  const map = {
    running: { label: 'Generating…', cls: 'proj-badge-stale' },
    draft: { label: 'Awaiting approval', cls: 'proj-badge-none' },
    approved: { label: 'Approved', cls: 'proj-badge-ok' },
    dismissed: { label: 'Dismissed', cls: 'proj-badge-missing' },
    error: { label: 'Failed', cls: 'proj-badge-missing' },
  };
  return map[(r || {}).status] || { label: 'Unknown', cls: 'proj-badge-none' };
};

// scores: {"grounded": {...}, "judged": {...}} — one line, with the judged
// half explicitly labelled as AI opinion (the grounded/judged split must
// survive into the UI, or it was pointless).
export const scoreSummary = (scores) => {
  const g = (scores || {}).grounded || {};
  const j = (scores || {}).judged || {};
  const parts = [];
  if (Number.isFinite(g.technical_readiness)) parts.push(`Technical ${g.technical_readiness}`);
  if (Number.isFinite(g.recent_activity)) parts.push(`Activity ${g.recent_activity}`);
  const judged = [];
  if (Number.isFinite(j.monetisation_clarity)) judged.push(`Monetisation ${j.monetisation_clarity}`);
  if (Number.isFinite(j.marketability)) judged.push(`Marketability ${j.marketability}`);
  if (judged.length) parts.push(`AI judgement: ${judged.join(' · ')}`);
  return parts.join(' · ');
};

// Ids that were running last poll and have landed as drafts — drives the
// "ready for approval" toast. Errors surface on the row itself, not a toast.
export const newlyDrafted = (prevRunningIds, reports) => {
  const prev = new Set(prevRunningIds || []);
  return (reports || [])
    .filter((r) => prev.has(r.id) && r.status === 'draft')
    .map((r) => r.id);
};

// Alphabetical (case-insensitive); projects whose path vanished sink to the end.
export const sortProjects = (projects) =>
  [...projects].sort((a, b) => {
    if (a.exists !== b.exists) return a.exists ? -1 : 1;
    return (a.name || '').toLowerCase().localeCompare((b.name || '').toLowerCase());
  });
