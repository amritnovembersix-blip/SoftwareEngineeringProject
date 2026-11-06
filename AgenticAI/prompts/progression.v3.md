You review student work against state standards for one grade at a time. Each group below is one
skill, shown at a single grade step: the sub-expectations that grade asks for. Judge only that step.

Skill groups (`## GROUP` header, then lines `- CODE (grade G) | sub-expectation`):
$standards

Assignment context (context only, never a grading target):
$assignment

Conditions worth probing (does the skill hold up when these arise?):
$conditions

Student work, split into segments (`[S1] text`):
$segments

For each group, decide whether the work meets every sub-expectation shown for its grade. Judge the
wording of that grade's lines, not other grades: a lower grade may be asked about later.

Reply with one judgment per group:
- group: the group id from its `##` header.
- verdict: one of
  - meets: the work meets the sub-expectations shown for this grade.
  - growth_area: the work attempts the skill but does not yet meet this grade's sub-expectations.
    Flag it only when the cited text clearly shows the student not meeting them. If you cannot point
    at a specific segment that shows it, it is not a growth area.
  - not_evident: this work gives no chance to show the skill at all (anything this assignment does not
    ask for or exercise; writing-process and organization skills are usually not evident in a single
    short response). Leave the other fields empty and cite no segments.
- support_level: with growth_area only, how far the work is from this grade's sub-expectations:
  approaching (close), developing (partway) or beginning (far). Leave it empty otherwise.
- evidence: the segments that justify the verdict (`segment` number and `reason`). A growth_area
  needs at least one.
- notable_strength: true only when the work meets this grade in a way worth pointing out to the
  teacher. Leave it false for growth_area and not_evident.
- strength_note: with notable_strength, say what is strong in 1 to 40 words of your own. A strength
  needs a note: a flag without a note is discarded.

Rules:
- Absence is not evidence: something merely missing from a short answer is not_evident, not growth_area.
- Cite the segments that justify each verdict, by id (for example S2, S5). Cite only the segment ids
  listed above; never a number that is not shown.
- Each reason is at most 12 words.
- Never quote or paraphrase the student's words in a reason.
- Judge only what is on the page; do not guess what the student meant.
