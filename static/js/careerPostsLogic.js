// ============================================
// Career hub — Posts tab pure helpers (no DOM, Node-importable)
// ============================================

export const SOURCE_LABELS = { git: 'Local git', github: 'GitHub', trello: 'Trello' };
export const POST_TYPE_LABELS = {
  progress: 'Progress update',
  technique: 'Technique / TIL',
  lesson: 'Lesson learned',
  narrative: 'Job-search narrative',
};
export const DISCLOSURE_LABELS = { anonymous: 'Anonymous', name_only: 'Name only', public: 'Public' };

export const cadenceLabel = (posts, now = new Date()) => {
  const stamps = (posts || [])
    .filter((p) => p.status === 'posted' && p.posted_at)
    .map((p) => new Date(p.posted_at.endsWith('Z') ? p.posted_at : `${p.posted_at}Z`).getTime());
  if (!stamps.length) return 'Nothing posted yet';
  const days = Math.floor((now.getTime() - Math.max(...stamps)) / 86400000);
  if (days <= 0) return 'Last posted today';
  return `Last posted ${days} day${days === 1 ? '' : 's'} ago`;
};

export const activityItemHtml = (item, esc) => `
  <li class="career-activity-item">
    <span class="proj-chip">${esc(item.source)}/${esc(item.kind)}</span>
    <span class="career-activity-date">${esc((item.date || '').slice(0, 10))}</span>
    <span class="career-activity-project">${esc(item.project_name)}</span>
    <span class="career-activity-title">${esc(item.title)}</span>
    ${item.body ? `<div class="memory-desc career-activity-body">${esc(item.body)}</div>` : ''}
  </li>`;

export const postCardHtml = (post, esc) => {
  const hooks = (post.hooks || []).map((h) => `<li>${esc(h)}</li>`).join('');
  const actions = post.status === 'draft'
    ? `<button class="memory-toolbar-btn" data-action="mark-posted">Mark posted</button>
       <button class="memory-toolbar-btn" data-action="discard-post">Discard</button>`
    : '';
  return `
    <div class="proj-card admin-card career-post" data-post-id="${esc(post.id)}">
      <div class="proj-card-head">
        <span class="proj-name">${esc(POST_TYPE_LABELS[post.post_type] || post.post_type)}</span>
        <span class="proj-badge ${post.status === 'posted' ? 'career-chip-positive' : 'career-chip-waiting'}">${esc(post.status)}</span>
      </div>
      <pre class="career-post-body">${esc(post.body)}</pre>
      ${hooks ? `<div class="memory-desc">Alternative hooks</div><ul class="career-hooks">${hooks}</ul>` : ''}
      <div class="proj-actions">
        <button class="memory-toolbar-btn" data-action="copy-post">Copy</button>
        ${actions}
      </div>
    </div>`;
};
