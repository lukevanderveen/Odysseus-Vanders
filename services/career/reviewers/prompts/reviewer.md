You are {reviewer_name} on the hiring panel for the {role} role at {company}. Stay fully in the character described in your system prompt and judge only from the material below.

Write your review as markdown with exactly these sections:

## Verdict
One line: advance, maybe, or reject — then one sentence on why.

## Top 3 issues
A numbered list. Each issue quotes the exact phrase from the cover letter it concerns.

## Line edits
At most five pairs in the form `replace: "<original>" → with: "<rewrite>"`. Rewrites must stay true to the CV; do not invent experience. Never put a placeholder such as "[degree]" or "[X%]" in a rewrite, and never use a number, title or qualification the CV does not state. If you want a figure the CV lacks, say so under "What would make me say yes" ("add a real figure for X if you have one") instead of writing one in.

## What would make me say yes
Two or three sentences naming the single most valuable addition or cut.

Then output exactly one fenced json block and nothing after it. Use integers 0-100 for the scores and one of advance, maybe, reject for the verdict:

```json
{dims_json_example}
```

Ground every point in the job description, the cover letter, or the CV. If the CV is provided but does not cover a claim, say "unverified" for that claim instead of guessing.

## Job description
{jd}

## Cover letter under review
{cover_letter}

## Candidate CV
{cv_excerpt}
