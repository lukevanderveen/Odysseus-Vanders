// ============================================
// Career hub — Posts tab (plan 03). Plugs in through installPosts(api); never imports career.js.
// ============================================
import uiModule from './ui.js';
import {
  activityItemHtml, cadenceLabel, DISCLOSURE_LABELS, POST_TYPE_LABELS, postCardHtml, SOURCE_LABELS,
} from './careerPostsLogic.js';

const esc = uiModule.esc;
let _api = null;

const local = {
  sources: new Set(['git', 'github', 'trello']),
  days: 14,
  postType: 'technique',
  variants: 2,
  projects: [],
  selectedProjects: new Set(),
  activity: null,       // { items, warnings } after a preview
  posts: [],
  busy: false,
  loaded: false,
};

const _json = async (url, opts = {}) => {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail?.[0]?.msg || data.detail || `${url} failed (${res.status})`);
  return data;
};

const _load = async () => {
  const [projects, posts] = await Promise.all([
    _json('/api/projects').catch(() => ({ projects: [] })),
    _json('/api/career/posts'),
  ]);
  local.projects = (projects.projects || []).filter((p) => p.exists !== false);
  local.posts = posts.posts || [];
  local.loaded = true;
};

const _controls = (settings) => {
  const sources = Object.entries(SOURCE_LABELS).map(([k, label]) => `
    <label class="career-check"><input type="checkbox" data-source="${k}" ${local.sources.has(k) ? 'checked' : ''}> ${esc(label)}</label>`).join('');
  const days = [7, 14, 30].map((d) => `<option value="${d}" ${d === local.days ? 'selected' : ''}>${d} days</option>`).join('');
  const types = Object.entries(POST_TYPE_LABELS).map(([k, label]) => `<option value="${k}" ${k === local.postType ? 'selected' : ''}>${esc(label)}</option>`).join('');
  const disclosure = settings.project_disclosure || {};
  const projects = local.projects.map((p) => {
    const level = disclosure[p.id] || settings.disclosure_default || 'anonymous';
    const opts = Object.entries(DISCLOSURE_LABELS).map(([k, label]) => `<option value="${k}" ${k === level ? 'selected' : ''}>${esc(label)}</option>`).join('');
    return `<li class="career-project-row" data-project-id="${esc(p.id)}">
      <label class="career-check"><input type="checkbox" data-project="${esc(p.id)}" ${local.selectedProjects.has(p.id) ? 'checked' : ''}> ${esc(p.name)}</label>
      <select class="settings-input career-disclosure" data-disclosure-for="${esc(p.id)}">${opts}</select></li>`;
  }).join('');
  return `
    <div class="council-run-form">${sources}<select id="career-posts-days" class="settings-input">${days}</select></div>
    <details class="proj-summary" ${local.projects.length ? '' : 'hidden'}><summary>Local projects (${local.projects.length}) — tick to limit, set disclosure per project</summary>
      <ul class="career-project-list">${projects}</ul></details>
    <div class="council-run-form">
      <button id="career-posts-preview" class="memory-toolbar-btn" ${local.busy ? 'disabled' : ''}>Preview activity</button>
      <select id="career-posts-type" class="settings-input">${types}</select>
      <input id="career-posts-variants" class="settings-input" type="number" min="1" max="5" value="${local.variants}" style="width:60px">
      <button id="career-posts-draft" class="memory-toolbar-btn" ${local.busy ? 'disabled' : ''}>${local.busy ? 'Working…' : 'Draft posts'}</button>
    </div>`;
};

const _activity = () => {
  if (!local.activity) return '';
  const { items, warnings } = local.activity;
  const warn = (warnings || []).map((w) => `<div class="memory-desc cc-warn">${esc(w)}</div>`).join('');
  const list = items.length
    ? `<ul class="career-activity-list">${items.map((i) => activityItemHtml(i, esc)).join('')}</ul>`
    : '<p class="memory-desc">No activity in this window.</p>';
  return `<h3 class="cc-section">What the model will see (${items.length} items, redacted)</h3>${warn}${list}`;
};

const render = (state) => {
  if (!local.loaded) { _load().then(() => _api.render()).catch((e) => uiModule.showError(`Posts: ${e.message}`)); }
  const drafts = local.posts.filter((p) => p.status === 'draft');
  const posted = local.posts.filter((p) => p.status === 'posted');
  return `
    <div class="career-posts">
      <div class="memory-desc report-scores">${esc(cadenceLabel(local.posts))}</div>
      ${_controls(state.settings || {})}
      ${_activity()}
      <h3 class="cc-section">Drafts (${drafts.length})</h3>
      ${drafts.length ? `<div class="projects-grid">${drafts.map((p) => postCardHtml(p, esc)).join('')}</div>` : '<p class="memory-desc">No drafts yet.</p>'}
      ${posted.length ? `<h3 class="cc-section">Posted (${posted.length})</h3><div class="projects-grid">${posted.map((p) => postCardHtml(p, esc)).join('')}</div>` : ''}
    </div>`;
};

const _readControls = () => {
  local.days = Number(document.getElementById('career-posts-days')?.value || 14);
  local.postType = document.getElementById('career-posts-type')?.value || 'technique';
  local.variants = Number(document.getElementById('career-posts-variants')?.value || 2);
};

const _query = () => {
  const ids = [...local.selectedProjects].join(',');
  return `days=${local.days}&sources=${[...local.sources].join(',')}${ids ? `&project_ids=${encodeURIComponent(ids)}` : ''}`;
};

const _preview = async () => {
  _readControls();
  local.busy = true; _api.render();
  try {
    local.activity = await _json(`/api/career/activity?${_query()}`);
  } catch (e) {
    uiModule.showError(`Preview: ${e.message}`);
  } finally {
    local.busy = false; _api.render();
  }
};

const _draft = async () => {
  _readControls();
  local.busy = true; _api.render();
  try {
    const out = await _json('/api/career/posts/draft', {
      method: 'POST',
      body: JSON.stringify({ post_type: local.postType, days: local.days, sources: [...local.sources],
        project_ids: [...local.selectedProjects], n_variants: local.variants }),
    });
    local.posts = [...out.posts, ...local.posts];
    (out.warnings || []).forEach((w) => uiModule.showToast(w));
    uiModule.showToast(`${out.posts.length} draft${out.posts.length === 1 ? '' : 's'} ready`);
  } catch (e) {
    uiModule.showError(`Draft posts: ${e.message}`);
  } finally {
    local.busy = false; _api.render();
  }
};

const _update = async (id, patch) => {
  try {
    const post = await _json(`/api/career/posts/${id}`, { method: 'PUT', body: JSON.stringify(patch) });
    local.posts = local.posts.map((p) => (p.id === id ? post : p));
    _api.render();
  } catch (e) {
    uiModule.showError(`Update post: ${e.message}`);
  }
};

const _discard = async (id) => {
  if (!(await uiModule.styledConfirm('Discard this draft?'))) return;
  try {
    await _json(`/api/career/posts/${id}`, { method: 'DELETE' });
    local.posts = local.posts.filter((p) => p.id !== id);
    _api.render();
  } catch (e) {
    uiModule.showError(`Discard: ${e.message}`);
  }
};

const _copy = async (id) => {
  const post = local.posts.find((p) => p.id === id);
  if (!post) return;
  try {
    await navigator.clipboard.writeText(post.body);
    uiModule.showToast('Post copied');
  } catch (_) {
    uiModule.showError('Clipboard unavailable — select the text and copy manually');
  }
};

const _setDisclosure = async (projectId, level) => {
  try {
    await _json('/api/career/projects-disclosure', { method: 'PUT', body: JSON.stringify({ project_id: projectId, level }) });
    await _api.refresh();
  } catch (e) {
    uiModule.showError(`Disclosure: ${e.message}`);
  }
};

const onClick = (ev, state) => {
  if (state.tab !== 'posts') return false;
  if (ev.target.closest('#career-posts-preview')) { _preview(); return true; }
  if (ev.target.closest('#career-posts-draft')) { _draft(); return true; }
  const src = ev.target.closest('input[data-source]');
  if (src) { src.checked ? local.sources.add(src.dataset.source) : local.sources.delete(src.dataset.source); return true; }
  const proj = ev.target.closest('input[data-project]');
  if (proj) { proj.checked ? local.selectedProjects.add(proj.dataset.project) : local.selectedProjects.delete(proj.dataset.project); return true; }
  const btn = ev.target.closest('button[data-action]');
  const id = btn?.closest('[data-post-id]')?.dataset.postId;
  if (!btn || !id) return false;
  if (btn.dataset.action === 'copy-post') { _copy(id); return true; }
  if (btn.dataset.action === 'mark-posted') { _update(id, { status: 'posted' }); return true; }
  if (btn.dataset.action === 'discard-post') { _discard(id); return true; }
  return false;
};

export const installPosts = (api) => {
  _api = api;
  api.registerTab('posts', render);
  api.onPanelClick(onClick);
  document.addEventListener('change', (ev) => {
    const sel = ev.target.closest?.('select[data-disclosure-for]');
    if (sel && api.getState().tab === 'posts') _setDisclosure(sel.dataset.disclosureFor, sel.value);
  });
};
