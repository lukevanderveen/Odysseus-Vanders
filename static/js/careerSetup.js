// ============================================
// Career hub — Setup tab (CV, voice rules, targets, example pairs, timings)
// ============================================
import uiModule from './ui.js';

const esc = uiModule.esc;

export const setupHtml = (state) => {
  const s = state.settings || {};
  const examples = (state.examples || []).map((e) => `
    <li class="career-example-row" data-example="${esc(e.filename)}">
      <span>${esc(e.filename)}</span>
      <button class="memory-toolbar-btn" data-action="delete-example">Delete</button>
    </li>`).join('');
  return `
    <div class="career-setup">
      <h3 class="cc-section">CV</h3>
      <p class="memory-desc">${s.cv_filename ? `Current: <strong>${esc(s.cv_filename)}</strong>` : 'No CV yet — upload a PDF, DOCX or Markdown file.'}</p>
      <div class="council-run-form">
        <input id="career-cv-file" type="file" accept=".pdf,.docx,.md,.txt" class="settings-input">
        <button id="career-cv-upload" class="memory-toolbar-btn">Upload CV</button>
      </div>

      <h3 class="cc-section">Voice and targets</h3>
      <label class="assistant-field"><span>Voice rules (how you write: tone, phrases to avoid, length)</span>
        <textarea id="career-voice" class="settings-input" rows="4">${esc(s.voice_rules || '')}</textarea></label>
      <label class="assistant-field"><span>Target roles (comma separated)</span>
        <input id="career-roles" class="settings-input" type="text" value="${esc((s.target_roles || []).join(', '))}"></label>
      <label class="assistant-field"><span>Locations (comma separated)</span>
        <input id="career-locations" class="settings-input" type="text" value="${esc((s.locations || []).join(', '))}"></label>

      <h3 class="cc-section">Tracker timing</h3>
      <div class="assistant-field-row">
        <label class="assistant-field"><span>Suggest a nudge after (days)</span>
          <input id="career-nudge" class="settings-input" type="number" min="1" value="${esc(String(s.nudge_after_days ?? 10))}"></label>
        <label class="assistant-field"><span>Mark ghosted after (days)</span>
          <input id="career-ghosted" class="settings-input" type="number" min="1" value="${esc(String(s.ghosted_after_days ?? 21))}"></label>
      </div>
      <div class="council-run-form"><button id="career-settings-save" class="memory-toolbar-btn">Save settings</button></div>

      <h3 class="cc-section">Example cover letters (job description + the letter you sent)</h3>
      <ul class="career-example-list">${examples || '<li class="memory-desc">None yet. The closest examples are used as style references when drafting.</li>'}</ul>
      <div class="council-edit-form">
        <label class="assistant-field"><span>Title</span><input id="career-ex-title" class="settings-input" type="text"></label>
        <label class="assistant-field"><span>Job description</span><textarea id="career-ex-jd" class="settings-input" rows="5"></textarea></label>
        <label class="assistant-field"><span>Cover letter</span><textarea id="career-ex-letter" class="settings-input" rows="6"></textarea></label>
        <div class="council-run-form"><button id="career-ex-save" class="memory-toolbar-btn">Add example</button></div>
      </div>

      <h3 class="cc-section">Post sources</h3>
      <p class="memory-desc">Register <strong>GitHub</strong> and <strong>Trello</strong> under Settings → Integrations (presets are provided). Local repositories come from the Projects tool.</p>
    </div>`;
};

const _csv = (id) => (document.getElementById(id)?.value || '').split(',').map((s) => s.trim()).filter(Boolean);

export const readSettingsForm = () => ({
  voice_rules: document.getElementById('career-voice')?.value || '',
  target_roles: _csv('career-roles'),
  locations: _csv('career-locations'),
  nudge_after_days: Number(document.getElementById('career-nudge')?.value || 10),
  ghosted_after_days: Number(document.getElementById('career-ghosted')?.value || 21),
});
