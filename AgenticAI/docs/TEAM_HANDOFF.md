# Agentic v2: handoff for Backend and Frontend

**From:** Justin. **Background:** team issues #13 and #14.

This sync adds a semester of demo data, richer review results and analytics. **The tables and views below are stable
enough to build on. Review accuracy is still being tuned** (see the end), so treat what the reviewer says as
placeholder content, not final results. SASS still stores no student text and never scores student work.

## Get data to build against (no API keys)

From the team repo root, with Docker running. These commands are for Git Bash; on PowerShell, use
`AgenticAI\.venv\Scripts\python` instead. Run `fill-demo` from the team repo root.

```bash
cd AgenticAI && cp .env.example .env     # first time only
docker compose up -d postgres
docker compose up --exit-code-from flyway flyway
cd ..
python -m venv AgenticAI/.venv
AgenticAI/.venv/Scripts/python -m pip install -e "AgenticAI[dev]" -e "AgenticLab[dev]"
AgenticAI/.venv/Scripts/python -m agentic_lab fill-demo
```

`fill-demo` needs no model. It loads the standards if needed, seeds both classrooms, saves about 500 labelled demo
reviews (weeks 1–7) and runs the analytics. Run it again safely at any time; `--reset` starts over, and
`--through-week 11` fills the whole term.

**The rows are demo data.** They come from the synthetic answer key, not from a model, and say so: reviewer
`answer-key-demo`, and summaries that start with "Demo data". The levels, gaps, trends and groups are realistic and vary
by student and week, so your pages will look right.

**What you'll get:**
- 2 classrooms, `English 9, Period 1` (no interventions) and `Period 2` (with interventions), one teacher, 18 students each.
- 11 weeks: Reading Literature (weeks 1–5), then Reading Informational (6–11).
- 22 assignments of 4 types (essay, paragraph, short answer, multiple choice), plus 2 intervention assignments.

`classroom_1.01` still works as before.

## What to read

Everything is in the shared Postgres. All IDs follow the existing conventions.

| Read | For | Key columns |
|---|---|---|
| `concept_status_latest` | A student's status per concept (student page, FR6) | `student_id`, `classroom_id`, `concept_code`, `status`, `current_level`, `basis`, `points`, `trend`, `as_of` |
| `class_concept_week_latest` | Class over time (heatmap) | `classroom_id`, `concept_code`, `week_start`, `n_level0`..`n_level3`, `n_too_little` |
| `reteach_group_latest` | Students who share a root cause (class summary) | `group_id`, `classroom_id`, `root_concept`, `grade_concept`, `student_id` |
| `peer_pair_latest` | Peer tutoring suggestions (FR7) | `pair_id`, `classroom_id`, `concept_code`, `tutor_id`, `tutee_id`, `status` |
| `verdict` | Every standard judged per submission, including strengths | `verdict`, `support_level`, `working_grade`, `notable_strength`, `strength_note`, `confidence` |
| `finding` (new columns) | More on a growth area | `support_level`, `confidence`, `condition`, `pattern`, `skill_state` |
| `finding_link` | A growth area traced to its lower-grade root | `finding_id`, `root_finding_id`, `rationale` |
| `display_label` | The words to show users | `kind`, `code`, `label` |

**Values:**
- `status`: `needs_attention`, `secure`, `emerging`, `too_little_evidence`.
- `current_level` and `points`: 0 Meets, 1 Approaching, 2 Developing, 3 Beginning. `basis` is a count such as `2/3`.
- `trend`: `improving`, `declining`, `steady`, `unclear`.

**Wording:** show "growth area" and "support level", not "weakness" or "severity". Read the labels from `display_label`.

**FR6 and FR7** (as agreed in class, issue #14) use evidence rules instead of percentages:
- `needs_attention` is Developing or Beginning on at least 2 of the last 3 pieces of work. Use it to notify the teacher.
- `secure` is meeting the standard on at least 3 of the last 4, with a strength. Pairs match `secure` tutors with
  `needs_attention` tutees.

Don't show a percentage: SASS doesn't score.

**Your one write:** when a teacher decides on a pairing, update `peer_pair_suggestion` where `id` is the view's `pair_id` (`status` to `approved` or
`rejected`, plus `decided_by` and `decided_at`). A teacher's decision survives later analyses.

Findings stay immutable. A teacher dismissing or disagreeing with a finding is a `teacher_response` row; the next
analysis counts that as meets.

## What changes on your side

- **Students screen:** it shows only each student's latest submission. With 22 assignments, it needs an assignment or
  week view.
- **Findings screen:** it mixes every assignment. Group or filter by assignment. The current query still works, and a
  test covers it. Strengths are in `verdict`, never in `finding`, so they won't appear there.
- **One gap can appear at several grades:** `9-10.W.3.A`, `8.W.3.A`, `7.W.3.A` and `6.W.3.A` are one skill. To show it
  once, group by the code without its grade (`W.3.A`).
- **`Backend/scripts/fill_demo_data.sh`** hard-codes `classroom_1.01`. It still works; `fill-demo` above is the way to
  get the new classrooms.

## Agentic service API (`http://localhost:8000`)

Same auth as before. Unchanged: `/sweeps`, `/runs/{id}`, `/events/submission-graded`, `/review-policy`.

| New | What |
|---|---|
| `POST /trend-analyses` | Body `{"scope": "classroom", "course_id": "classroom_2.00-control"}` (or `"scope": "student"` with `user_id`; optional `as_of`). Returns 202 with a `run_id`; poll `GET /runs/{id}`. By default it counts everything reviewed so far. Analyses also run automatically after real reviews |
| `GET /settings`, `PUT /settings/{key}`, `GET /settings/{key}/history` | Teacher/admin settings, e.g. `attention_hits`, `secure_hits`, `mc_finding_min_picks`. `PUT` takes `{"value": ..., "changed_by": ...}`. For a settings page |

## Real reviews (only when you need them)

**"Review class" now runs the v2 review.** The default profile `graded` makes about 9 model calls per written
submission, which uses up a free tier quickly. Options in `AgenticAI/.env`:

| Setting | Effect |
|---|---|
| `FAKE_LLM=1` | No key, no cost, identical placeholder answers. For day-to-day work |
| `BANDS_PROFILE=lean` | About 2–3 calls per submission, for real runs on free keys |
| `REVIEW_PIPELINE=mvp` | The old behavior |
| `LMS_SOURCE=coursedir` | Lets the service serve the new 2.00 classrooms; the default `jsonl` serves `classroom_1.01` |

Loading the v2 standards replaces the `standard` table, which findings reference. If `load-standards` fails on an
existing database, run `python -m agentic reset-reviews --classroom 1.01` first.

## Accuracy, honestly

Measured on 12 synthetic students, weeks 1–3, against the planted answer key (not a real teacher):

| | Precision | Recall |
|---|---|---|
| Exact standard | 0.29 | 0.75 |
| Concept | 0.36 | 0.83 |

It finds most planted weaknesses but **over-flags**, especially the grade 3–5 Writing and Reading reviewers. Some of those
flags may be real mistakes nobody planted; that hasn't been checked yet. Tuning continues, and it won't change the tables
above. So don't present a real review's findings as final, and show the confidence value where you can.

## Questions

Ask Justin. Design docs are in the lab repo (`GrouchoLarks/csci-5030-project`, `docs/superpowers/specs/`). `AgenticAI/`
and `AgenticLab/` are synced from there, so please don't edit them here.
