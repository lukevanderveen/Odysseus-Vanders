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

// Alphabetical (case-insensitive); projects whose path vanished sink to the end.
export const sortProjects = (projects) =>
  [...projects].sort((a, b) => {
    if (a.exists !== b.exists) return a.exists ? -1 : 1;
    return (a.name || '').toLowerCase().localeCompare((b.name || '').toLowerCase());
  });
