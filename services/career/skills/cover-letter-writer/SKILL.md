---
name: cover-letter-writer
description: "Draft a cover letter for a job description, grounded in my CV, via the Career hub; shows a claims check."
version: 1.0.0
category: career
tags: [jobs, cover-letter, career, writing]
status: published
confidence: 0.9
source: user
---

## When to Use

When I ask for a cover letter, to tailor an application to a job description, or paste a job posting and ask what to send. Also triggered by the /cover-letter-writer slash command.

## Procedure

1. Find or create the application. Call `app_api` with POST /api/career/applications and body {"company": "...", "role": "...", "jd_text": "<pasted description>", "jd_url": "<posting URL if given>"}. If I named an existing application, GET /api/career/applications and pick the matching id instead.
2. Draft the letter: `app_api` POST /api/career/applications/{id}/cover-letter with no body. It returns doc_id, version, claims, rubric_warnings and requirements.
3. If the call returns 409 "No CV on file", stop and tell me to upload my CV in Career → Setup; do not write a letter from memory.
4. If it returns 409 about a missing job description, ask me to paste the posting text or give the URL, then retry from step 1.
5. Fetch the letter text with GET /api/document/{doc_id} and show it to me in full, followed by the claims check as a table (claim → evidence) and any rubric_warnings as a short list.
6. Link the editable document as (#document-{doc_id}) so I can open it in the editor.
7. If I ask for changes, explain them in one line and call POST /api/career/applications/{id}/cover-letter again (it creates a new version of the same document), or edit the document directly with update_document when the change is small.

## Pitfalls

- Never invent experience, employers, dates or technologies. Every claim must appear in the claims check with CV evidence; if a claim has none, remove the sentence.
- The app_api tool is admin-only. If it is unavailable, tell me to run the draft from the Career hub's Applications tab instead of writing the letter yourself.
- A job description is required: paste it or give a URL. Do not guess requirements from a job title.
- Do not pad: the letter is at most 300 words and must not contain "passionate", "thrilled" or "I am writing to apply".

## Verification

- Letter is under 300 words and names at least two specifics from the posting.
- Every factual claim maps to a CV evidence snippet in the claims check.
- rubric_warnings is empty, or each warning was addressed in a regenerated version.
- The response links #document-{doc_id} so the letter opens in the editor.
