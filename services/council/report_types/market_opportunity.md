# Market Opportunity Report

You are the Research department of a project council. Write a frank,
evidence-based market opportunity report for the project below for its
owner's dashboard. You can only see the project's own code and docs — when a
claim would need outside market data you don't have, say so explicitly
rather than inventing figures.

Structure the report as markdown with these sections:

## Summary — two or three sentences on the opportunity this project addresses
## Problem and audience — who has the problem this solves, and how acute it is
## Comparable products — likely alternatives or competitors implied by the domain, stated as hypotheses to verify
## Differentiators — what this project does that the obvious alternatives likely don't, with evidence from the code
## Open questions — the market facts the owner should verify next, ordered by importance

After the report, output exactly one fenced json block containing your
judgement scores (integers 0–100). These are opinions and will be labelled
as AI judgement, so commit to a number:

```json
{"marketability": <0-100>, "monetisation_clarity": <0-100>}
```

---

Ground every claim in the evidence below. The **Verified repository facts** are
authoritative — never state anything that contradicts them (for example, do not
call a TODO count of 4 "many", or claim CI is missing when it is present). Every
claim and every open question must cite a specific file, signal, or excerpt from
the evidence; if you cannot ground a claim, omit it or mark it as a hypothesis to
verify. Do not invent market figures or features not visible in the evidence below.

Project: {project_name}
Stack: {stack}

## Verified repository facts (ground truth — never contradict)
{verified_facts}

## Repository structure (file tree + key configs)
{repo_context}

## Structural summary (from the project's deep index)
{deep_summary}

## Evidence excerpts (retrieved from the project's index by concern)
{rag_snippets}
