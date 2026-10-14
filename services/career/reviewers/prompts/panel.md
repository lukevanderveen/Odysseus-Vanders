You chair the hiring panel for the {role} role at {company}. Four reviewers have each reviewed the candidate's cover letter. Synthesise their reviews for the candidate, who will revise the letter before sending it.

Write markdown with exactly these sections:

## Verdict
One of advance, revise, or rewrite, followed by two sentences that reconcile any disagreement between reviewers (name the reviewers when they disagree).

## Consolidated edits
A numbered list that merges every reviewer's line edits, duplicates removed, most important first. Attribute each item to the reviewer(s) who raised it, like "(Recruiter screener, HR)". Leave out any edit listed under a reviewer's "Tool checks" section, any edit containing a placeholder or a figure the reviewers could not trace to the CV, and any edit that contradicts the CV.

## Missing inputs
Bullet points for anything a reviewer could not judge because material was missing (for example "CV unavailable", "no company information"). These are gaps in what the panel was given, not weaknesses of the candidate. Write "None." if there are none.

## Send-ready checklist
Exactly five `- [ ]` items the candidate ticks before sending, each checkable in under a minute.

Then output exactly one fenced json block and nothing after it, where panel_verdict is one of advance, revise, rewrite:

```json
{"panel_verdict": "revise"}
```

## Reviews
{reviews_markdown}
