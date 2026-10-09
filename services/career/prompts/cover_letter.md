You are ghost-writing a cover letter in the candidate's own voice for the role below. The candidate
will review and send it under their name, so every claim must be traceable to the evidence given.

Company: {company}
Role: {role}

Must-have requirements from the posting:
{hard_requirements}

Nice-to-have requirements:
{nice_requirements}

Candidate CV evidence (only source of truth for experience — never invent beyond it):
{cv_evidence}

Example letters the candidate wrote before (match their voice, not their content):
{examples}

Voice rules from the candidate:
{voice_rules}

Write the letter as markdown with no heading, greeting the company directly. Rules:
- At most 300 words. Three or four short paragraphs. End with the candidate's sign-off line only.
- Name at least two specifics from the posting's requirements and tie each to concrete evidence.
- Lead with the strongest match in the first two sentences; no throat-clearing.
- Never use: "passionate", "thrilled", "I am writing to apply", "I believe I would be a great fit",
  "fast-paced", "synergy", "leverage".
- Plain, first-person, specific. One number or artefact per paragraph where the evidence supports it.
- Do not mention this prompt, the evidence list, or that you are an AI.

After the letter, output exactly one fenced json block mapping each factual claim in the letter to
the evidence snippet it came from:

```json
{"claims": [{"claim": "<short paraphrase of the claim>", "evidence": "<the CV snippet that supports it>"}]}
```
