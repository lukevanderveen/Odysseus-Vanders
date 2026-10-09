// ============================================
// Career hub — pure logic (no DOM, Node-importable for tests)
// ============================================

export const TABS = ['tracker', 'applications', 'reviewers', 'posts', 'setup'];

export const STATUS_LABELS = {
  drafting: 'Drafting',
  applied: 'Applied',
  acknowledged: 'Acknowledged',
  screening: 'Screening',
  interview: 'Interview',
  offer: 'Offer',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
  ghosted: 'Ghosted',
};

const POSITIVE = new Set(['interview', 'offer']);
const NEGATIVE = new Set(['rejected']);
const MUTED = new Set(['ghosted', 'withdrawn', 'drafting']);

export const statusChip = (status) => {
  const key = STATUS_LABELS[status] ? status : 'drafting';
  const cls = POSITIVE.has(key) ? 'career-chip-positive'
    : NEGATIVE.has(key) ? 'career-chip-negative'
      : MUTED.has(key) ? 'career-chip-muted'
        : 'career-chip-waiting';
  return { label: STATUS_LABELS[key], cls };
};

export const REVIEWER_COLORS = {
  career_recruiter: '#d9a13b',
  career_hiring_manager: '#5b8abf',
  career_engineer: '#5dbf6e',
  career_hr: '#d873a8',
};

export const daysLabel = (days) => {
  if (days === null || days === undefined) return '';
  if (days === 0) return 'today';
  return `${days} day${days === 1 ? '' : 's'} ago`;
};

// drafting = not yet sent; active = in flight; closed = settled (ghosted counts as closed).
export const applicationBuckets = (apps) => {
  const out = { drafting: [], active: [], closed: [] };
  for (const a of apps || []) {
    const s = a.computed_status || a.status || 'drafting';
    if (s === 'drafting') out.drafting.push(a);
    else if (s === 'rejected' || s === 'withdrawn' || s === 'ghosted') out.closed.push(a);
    else out.active.push(a);
  }
  return out;
};

const PLACEHOLDER_COPY = {
  tracker: 'The live job tracker (plan 04) lands here: responses from your inbox, who is waiting, what to nudge.',
  reviewers: 'The reviewer panel (plan 02) lands here: edit the four reviewer personas and run them on a cover letter.',
  posts: 'LinkedIn post drafting (plan 03) lands here: pick sources, a window, and a post type.',
};

export const placeholderHtml = (tab) =>
  `<div class="career-placeholder memory-desc">${PLACEHOLDER_COPY[tab] || `${tab} is not built yet.`}</div>`;
