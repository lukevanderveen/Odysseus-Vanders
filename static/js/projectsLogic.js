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

// Ids that were running last poll and have landed as drafts — drives the
// "ready for approval" toast. Errors surface on the row itself, not a toast.
export const newlyDrafted = (prevRunningIds, reports) => {
  const prev = new Set(prevRunningIds || []);
  return (reports || [])
    .filter((r) => prev.has(r.id) && r.status === 'draft')
    .map((r) => r.id);
};

// Consolidated per-project report stack: one tab per report type, the synthesis
// "Overview" first, then a fixed department order. Short tab labels keep the
// stack compact (the full titles live on each report row).
const TAB_LABELS = {
  council_review: 'Overview',
  developer_review: 'Developer',
  market_opportunity: 'Research',
  architecture_review: 'Architecture',
  launch_strategy: 'Business',
  marketing_audit: 'Marketing',
};
const TAB_ORDER = Object.keys(TAB_LABELS);

// Group a flat report list by project, preserving first-seen project order.
export const reportsByProject = (reports) => {
  const order = [];
  const byId = new Map();
  for (const r of reports || []) {
    const pid = r.project_id;
    if (!byId.has(pid)) { byId.set(pid, []); order.push(pid); }
    byId.get(pid).push(r);
  }
  return order.map((pid) => ({ projectId: pid, reports: byId.get(pid) }));
};

// One project's reports → ordered tab descriptors (latest report per type).
export const councilTabs = (projectReports) => {
  const latest = new Map();
  for (const r of projectReports || []) {
    const type = r.report_type || 'developer_review';
    const cur = latest.get(type);
    if (!cur || String(r.created_at || '').localeCompare(String(cur.created_at || '')) > 0) {
      latest.set(type, r);
    }
  }
  const rank = (t) => {
    const i = TAB_ORDER.indexOf(t);
    return i === -1 ? TAB_ORDER.length : i;
  };
  return [...latest.values()]
    .sort((a, b) => rank(a.report_type) - rank(b.report_type))
    .map((r) => ({
      type: r.report_type,
      reportId: r.id,
      department: r.department || '',
      label: TAB_LABELS[r.report_type] || r.report_type,
      status: r.status,
    }));
};

// Alphabetical (case-insensitive); projects whose path vanished sink to the end.
export const sortProjects = (projects) =>
  [...projects].sort((a, b) => {
    if (a.exists !== b.exists) return a.exists ? -1 : 1;
    return (a.name || '').toLowerCase().localeCompare((b.name || '').toLowerCase());
  });
