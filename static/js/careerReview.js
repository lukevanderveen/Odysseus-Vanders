// ============================================
// Career hub — panel review section on an application's detail view.
// Run the panel, show four reviewer verdict cards + the chair's synthesis,
// poll while running. Plugs in through installReview(api) + detailSections.
// ============================================
import uiModule from './ui.js';
import { detailSections } from './careerApplications.js';
import { REVIEWER_COLORS } from './careerLogic.js';
import { panelVerdictChip, reviewerLabel, reviewScoreBarsHtml, REVIEW_DIMS } from './careerReviewLogic.js';

const esc = uiModule.esc;

let _api = null;
const _reviews = new Map();   // review id -> full review dict
const _loading = new Set();
let _pollTimer = null;
let _runBusy = false;

const _json = async (url, opts = {}) => {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${url} failed (${res.status})`);
  return data;
};

const _loadReview = async (rid) => {
  if (_loading.has(rid)) return;
  _loading.add(rid);
  try {
    _reviews.set(rid, await _json(`/api/career/reviews/${rid}`));
  } catch (e) {
    uiModule.showError(`Review: ${e.message}`);
  } finally {
    _loading.delete(rid);
  }
  _api.render();
  _schedulePoll(rid);
};

const _schedulePoll = (rid) => {
  clearTimeout(_pollTimer);
  const r = _reviews.get(rid);
  if (!r || r.status !== 'running' || !_api.isOpen()) return;
  _pollTimer = setTimeout(async () => {
    _reviews.delete(rid);
    await _loadReview(rid);
    const again = _reviews.get(rid);
    if (again && again.status !== 'running') await _api.refresh();   // picks up latest_review_id + toast state
  }, 5000);
};

const _run = async (appId) => {
  if (_runBusy) return;
  _runBusy = true;
  _api.render();
  try {
    const data = await _json(`/api/career/applications/${appId}/reviews`, { method: 'POST' });
    uiModule.showToast('Panel convened — reviewers are reading your letter');
    _reviews.set(data.review_id, { id: data.review_id, status: 'running', scores: {}, verdicts: {} });
    await _api.refresh();
    _schedulePoll(data.review_id);
  } catch (e) {
    uiModule.showError(`Run panel: ${e.message}`);
  } finally {
    _runBusy = false;
    _api.render();
  }
};

const _reviewerCard = (dept, review) => {
  const v = (review.verdicts || {})[dept];
  const running = review.status === 'running';
  const chip = running ? { label: 'Busy', cls: 'council-chip-busy report-chip-running' } : panelVerdictChip(v?.verdict);
  return `
    <div class="proj-card admin-card council-member" style="--dept-color:${REVIEWER_COLORS[dept] || 'var(--fg)'}">
      <div class="proj-card-head">
        <span class="council-avatar">${esc(reviewerLabel(dept)[0])}</span>
        <span class="proj-name">${esc(v?.name || reviewerLabel(dept))}</span>
        <span class="proj-badge ${chip.cls}">${esc(chip.label)}</span>
      </div>
      <div class="council-dept">${esc(reviewerLabel(dept))}</div>
      ${v ? reviewScoreBarsHtml(dept, v, esc) : ''}
      ${v ? `<details class="proj-summary"><summary>Review</summary><div class="proj-summary-body">${esc(v.body || '')}</div></details>` : ''}
    </div>`;
};

const sectionHtml = (app) => {
  const rid = app.latest_review_id;
  const review = rid ? _reviews.get(rid) : null;
  if (rid && !review) _loadReview(rid);
  const running = review?.status === 'running';
  const chip = panelVerdictChip(review?.scores?.panel_verdict);
  const head = `
    <div class="council-run-form">
      <button id="career-review-run" class="memory-toolbar-btn" ${running || _runBusy || !app.cover_letter_doc_id ? 'disabled' : ''}
        title="${app.cover_letter_doc_id ? 'Four reviewers read the letter, then the chair synthesises' : 'Draft a cover letter first'}">
        ${running ? 'Panel in session…' : 'Run panel review'}
      </button>
      ${app.cover_letter_doc_id ? `<a class="memory-toolbar-btn" href="#document-${esc(app.cover_letter_doc_id)}">Open letter in editor</a>` : ''}
      ${review ? `<span class="proj-badge ${chip.cls}">${esc(chip.label)}</span>` : ''}
      ${Number.isFinite(review?.scores?.overall) ? `<span class="proj-score">${esc(String(review.scores.overall))}<span class="proj-score-denom">/100</span></span>` : ''}
    </div>`;
  if (!review) return `<h3 class="cc-section">Reviewer panel</h3>${head}<p class="memory-desc">No panel review yet.</p>`;
  if (review.status === 'error') return `<h3 class="cc-section">Reviewer panel</h3>${head}<p class="memory-desc cc-warn">Last run failed: ${esc(review.error || 'unknown error')}</p>`;
  return `
    <h3 class="cc-section">Reviewer panel</h3>${head}
    <div class="projects-grid">${Object.keys(REVIEW_DIMS).map((d) => _reviewerCard(d, review)).join('')}</div>
    ${review.panel_summary ? `<h3 class="cc-section">Chair's synthesis</h3><div class="report-body career-panel-summary">${esc(review.panel_summary)}</div>` : ''}`;
};

export const handleReviewClick = (ev, state) => {
  if (!ev.target.closest('#career-review-run')) return false;
  if (state.openId) _run(state.openId);
  return true;
};

export const installReview = (api) => {
  _api = api;
  detailSections.push(sectionHtml);
  api.onPanelClick(handleReviewClick);
};
