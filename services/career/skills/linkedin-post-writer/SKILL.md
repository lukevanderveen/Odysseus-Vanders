---
name: linkedin-post-writer
description: "Draft a ready-to-paste LinkedIn post in my voice from my recent project activity (git, GitHub, Trello), privacy-scrubbed. Never publishes."
version: 2.0.0
category: career
tags: [linkedin, posts, writing, career, social]
status: published
confidence: 0.9
source: user
---

## When to Use

When I ask to write or draft a LinkedIn post about a project, an update, something I learned, or an open-to-work post. Also triggered by `/linkedin-post-writer <what it's about>`.

## Procedure

1. Decide the post type with me if it is not obvious: progress (what shipped), technique (one reusable idea, no internals), lesson (a mistake or trade-off), narrative (open-to-work).
2. Fetch the privacy-scrubbed activity with the app_api tool: GET /api/career/activity?days=14&sources=git,github,trello (use days=7 or 30 if I ask). Show me the item list and any warnings (an unregistered source is a warning, not an error).
3. If the preview is empty, say so and ask whether to widen the window or register GitHub/Trello under Settings → Integrations. Do not invent activity.
4. Draft with app_api: POST /api/career/posts/draft with {"post_type": "<type>", "days": <n>, "sources": [...], "n_variants": 2}. The server applies the disclosure rules and stores the drafts.
5. Present each variant as one clean copy-paste block followed by its three alternative hooks. Point out which variant you would post and why in one sentence.
6. Offer one round of edits; apply them with PUT /api/career/posts/{id} {"body": "..."} so the hub keeps the final text.
7. Remind me it is a draft to review in the Career hub → Posts tab, and that this skill never publishes anywhere.

## Pitfalls

- The activity is already privacy-scrubbed; never re-add project names, file names, vendors, hosts or anything that appeared as [redacted]. "a project I'm building" must stay unnamed.
- app_api is an admin-only tool. If it is refused, tell me to run the draft from the Career hub → Posts tab instead.
- My voice: first-person, plain-spoken, authentic. No buzzword salad, no "thrilled to announce", no "excited to share".
- Open with a strong hook in the first line (LinkedIn truncates the preview around 140 characters). 100-200 words, 2-4 relevant hashtags, at most one soft call to action.
- Never publish, never post, never offer to post. Drafts only.

## Verification

- The first line of each variant is 140 characters or fewer and would make sense on its own.
- Each variant is 100-200 words with at most 4 hashtags and no links.
- No [redacted] markers, file names, hostnames, credentials or internal module names appear in the post.
- The drafts exist in the Career hub (GET /api/career/posts returns them with status draft).
