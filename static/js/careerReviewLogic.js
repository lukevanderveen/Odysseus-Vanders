// ============================================
// Career hub — reviewer panel pure logic (no DOM, Node-importable)
// Mirror of services/career/review_scoring.py: keep REVIEW_DIMS/DIM_LABELS in sync.
// ============================================

export const REVIEW_DIMS = {
  career_recruiter: ['ats_match', 'clarity'],
  career_hiring_manager: ['role_fit', 'impact_evidence'],
  career_engineer: ['technical_credibility', 'specificity'],
  career_hr: ['consistency_with_cv', 'professionalism'],
};

export const DIM_LABELS = {
  ats_match: 'ATS keyword match',
  clarity: 'Clarity',
  role_fit: 'Role fit',
  impact_evidence: 'Impact evidence',
  technical_credibility: 'Technical credibility',
  specificity: 'Specificity',
  consistency_with_cv: 'Consistency with CV',
  professionalism: 'Professionalism',
};

const REVIEWER_LABELS = {
  career_recruiter: 'Recruiter screener',
  career_hiring_manager: 'Hiring manager',
  career_engineer: 'Senior engineer',
  career_hr: 'HR / People partner',
};

export const reviewerLabel = (department) => REVIEWER_LABELS[department] || department;

// verdict = {name, verdict, body, scores} as persisted by the runner.
export const reviewScoreEntries = (department, verdict) => {
  const scores = (verdict || {}).scores || {};
  const out = [];
  for (const key of REVIEW_DIMS[department] || []) {
    if (Number.isFinite(scores[key])) out.push({ key, label: DIM_LABELS[key], value: scores[key] });
  }
  return out;
};

// Every panel score is an LLM judgement → hatched bar + AI tag (council convention).
export const reviewScoreBarsHtml = (department, verdict, esc) => {
  const entries = reviewScoreEntries(department, verdict);
  if (!entries.length) return '';
  const rows = entries.map((e) => `
    <div class="score-bar-row">
      <span class="score-bar-label">${esc(e.label)}<span class="score-ai-tag">AI</span></span>
      <span class="score-bar-track"><span class="score-bar-fill judged" style="width:${Math.max(0, Math.min(100, e.value))}%"></span></span>
      <span class="score-bar-val">${esc(String(e.value))}</span>
    </div>`).join('');
  return `<div class="score-bars">${rows}</div>`;
};

const VERDICT_CHIPS = {
  advance: { label: 'Advance', cls: 'career-chip-positive' },
  maybe: { label: 'Maybe', cls: 'career-chip-waiting' },
  revise: { label: 'Revise', cls: 'career-chip-waiting' },
  reject: { label: 'Reject', cls: 'career-chip-negative' },
  rewrite: { label: 'Rewrite', cls: 'career-chip-negative' },
};

export const panelVerdictChip = (verdict) => VERDICT_CHIPS[verdict] || { label: 'Pending', cls: 'career-chip-muted' };
