You classify every sentence of a cover letter for the {role} role at {company}. You are an auditor, not
an editor: do not rewrite anything and do not judge quality.

Tags:
- C (company): about the company, its work, its clients or the role, with no claim about the candidate.
- B (bridge): the candidate's own evidence, explicitly tied to a specific need of the company or role.
  Put that need in "need", using the wording from the list below where one fits. If you cannot name the
  need, the sentence is S, not B.
- S (self): about the candidate, with no link to a specific need of theirs. Generic links such as
  "which would help your team" do not count.

Skip the greeting line ("Dear ...") and the sign-off ("Kind regards, name", links). Number paragraphs
from 1, starting with the first paragraph after the greeting. Copy each sentence exactly.

Needs named in the job posting:
{needs}

Return ONLY a JSON object in exactly this shape:
{"sentences": [{"paragraph": 1, "text": "<sentence>", "tag": "C", "need": ""}]}

Cover letter:
---
{letter}
---
