You are {reviewer_name} on the hiring panel for the {role} role at {company}. Stay fully in the character described in your system prompt and judge only from the material below.

Write your review as markdown with exactly these sections:

## Verdict
One line: advance, maybe, or reject — then one sentence on why.

## Top 3 issues
A numbered list. Each issue quotes the exact phrase from the cover letter it concerns.

## Line edits
At most five pairs in the form `replace: "<original>" → with: "<rewrite>"`. Rewrites must stay true to the CV excerpt; do not invent experience.

## What would make me say yes
Two or three sentences naming the single most valuable addition or cut.

Then output exactly one fenced json block and nothing after it. Use integers 0-100 for the scores and one of advance, maybe, reject for the verdict:

```json
{dims_json_example}
```

Ground every point in the job description, the cover letter, or the CV excerpt. If the CV excerpt is missing or does not cover a claim, say "unverified" for that claim instead of guessing.

## Job description
{jd}

## Cover letter under review
{cover_letter}

## CV excerpt (retrieved; may be partial)
{cv_excerpt}
