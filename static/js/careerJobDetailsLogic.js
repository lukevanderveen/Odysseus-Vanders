// ============================================
// Career hub — editable job details for the Applications detail view
// (posting URL + job description, company site + context). Pure html builder
// (Node-testable); career.js wires the buttons.
// ============================================

// busy: a fetch/scan is in flight, so the fetch buttons are disabled.
export const jobDetailsHtml = (app, busy, esc) => {
  const dis = busy ? ' disabled' : '';
  return `
    <details class="proj-summary career-job-details"${app.jd_text ? '' : ' open'}>
      <summary>Job details${app.jd_text ? '' : ' — no job description yet'}</summary>
      <div class="proj-summary-body">
        <label class="assistant-field"><span>Posting URL</span>
          <div class="council-run-form">
            <input id="career-jd-url" class="settings-input" type="url" style="flex:1;" value="${esc(app.url || '')}">
            <button id="career-jd-fetch" class="memory-toolbar-btn"${dis}>Fetch description</button>
          </div></label>
        <label class="assistant-field"><span>Job description</span>
          <textarea id="career-jd-text" class="settings-input" rows="8">${esc(app.jd_text || '')}</textarea></label>
        <label class="assistant-field"><span>Company website (scanned for projects/products the letter can mention)</span>
          <div class="council-run-form">
            <input id="career-company-url" class="settings-input" type="url" style="flex:1;" value="${esc(app.company_url || '')}">
            <button id="career-company-scan" class="memory-toolbar-btn"${dis}>Scan site</button>
          </div></label>
        <label class="assistant-field"><span>About the company (edit down to what's worth mentioning)</span>
          <textarea id="career-company-context" class="settings-input" rows="5">${esc(app.company_context || '')}</textarea></label>
        <div class="council-run-form"><button id="career-jd-save" class="memory-toolbar-btn">Save job details</button></div>
      </div>
    </details>`;
};

export const readJobDetailsForm = (doc) => ({
  url: doc.getElementById('career-jd-url')?.value.trim() || '',
  jd_text: doc.getElementById('career-jd-text')?.value || '',
  company_url: doc.getElementById('career-company-url')?.value.trim() || '',
  company_context: doc.getElementById('career-company-context')?.value || '',
});
