// ============================================
// Career hub — cover letter section, pure html builders (Node-testable)
// ============================================

export const claimsHtml = (claims, warnings, esc) => {
  const rows = (claims || []).map((c) => `
    <li class="career-claim"><strong>${esc(c.claim)}</strong>
      <span class="memory-desc">${c.evidence ? esc(c.evidence) : 'no evidence given'}</span></li>`).join('');
  const warns = (warnings || []).map((w) => `<li class="career-rubric-warning">${esc(w)}</li>`).join('');
  return `
    <div class="career-claims">
      <h4 class="cc-section">Claims check</h4>
      ${rows ? `<ul class="career-claim-list">${rows}</ul>` : '<p class="memory-desc">No claims check yet.</p>'}
      ${warns ? `<ul class="career-rubric">${warns}</ul>` : ''}
    </div>`;
};

// letter: payload from GET/POST /api/career/applications/{id}/cover-letter, or null.
export const letterSectionHtml = (app, letter, busy, esc) => {
  const docId = (letter && letter.doc_id) || app.cover_letter_doc_id;
  const version = letter && letter.version ? ` <span class="proj-badge">v${esc(String(letter.version))}</span>` : '';
  const label = busy ? 'Drafting…' : docId ? 'Regenerate' : 'Draft cover letter';
  return `
    <div class="career-letter" data-application-id="${esc(app.id)}">
      <h3 class="cc-section">Cover letter${version}</h3>
      <div class="council-run-form">
        <button class="memory-toolbar-btn" data-action="draft-cover-letter" ${busy ? 'disabled' : ''}>${label}</button>
        ${docId ? `<a class="memory-toolbar-btn" href="#document-${esc(docId)}">Open in editor</a>` : ''}
      </div>
      ${docId ? claimsHtml(letter ? letter.claims : [], letter ? letter.rubric_warnings : [], esc)
        : '<p class="memory-desc">Drafts from your CV and the job description, then opens in the editor.</p>'}
    </div>`;
};
