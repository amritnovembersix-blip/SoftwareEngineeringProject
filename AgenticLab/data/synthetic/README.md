# agentic_lab/data/synthetic/

Synthetic ELA classrooms for SASS. **All data is synthetic. Never commit real student data.**

**Owner:** Justin Keck (generator). Other teams may copy these files to seed the mock LMS.

## Files per classroom version `<v>`

| File | What it is | Who reads it |
|---|---|---|
| `classroom_<v>.jsonl` | **Labels**: one row per student with the grade and the standards the student is underdeveloped on (the ground truth) | Essay generator and evaluator only. The reviewer reads only `grade` |
| `classroom_<v>.assignment.json` | The assignment: `id`, `type` (`argument`/`rl`/`ri`), `title`, `prompt`, `targets`, `notes`, `strands` | Generator, reviewer (context only) |
| `essays_classroom_<v>.jsonl` | **Submissions**: one essay per student, generated from the labels | `JsonlLMSClient` (the file-backed mock LMS) |
| `assignments/*.json` | Starter assignment templates | Copy one to `classroom_<v>.assignment.json` |

Versions: `1.00` (argument, writing only), `1.01` (strand-based RL), `1.02` (`rl-theme`, Animal Farm).

## Mapping to the mock LMS contract (`contracts/mock-lms-api.yaml`)

The mock LMS serves Canvas-shaped resources. To seed it from these files:

| Mock LMS field | From |
|---|---|
| `Course.id` | essay `course_id` (`classroom_<v>`) |
| `Course.grade_level` | label `grade` |
| `Course.subject` | `ELA` |
| `Assignment.id` / `course_id` | essay `assignment_id` / `course_id` |
| `Assignment.name` / `description` | assignment file `title` / `prompt` |
| `Submission.id` | essay `submission_id` |
| `Submission.user_id` | essay `student_hash` (a hashed, synthetic ID) |
| `Submission.attempt` / `submitted_at` | essay `attempt` / `submitted_at` |
| `Submission.body` | essay `paragraphs`, each wrapped in `<p>...</p>` (the reviewer splits HTML back into paragraphs) |
| `Submission.workflow_state` | `submitted` |

Labels (`underdeveloped`, `meets_all_standards`) never go to the LMS: they are the answer key.

## Version 2: course folders (`courses/`, `curricula/`, `plans/`)

v2 replaces the one-file-per-classroom layout with a shared curriculum and a course folder per arm. It
is a matched pair: `2.00-control` and `2.00-intervention` share the curriculum and the same 18 students
(twins); only the intervention arm gets extra assignments. Design: `docs/superpowers/specs/2026-10-26-synthetic-data-v2-design.md`.

```
curricula/ela9-v2/        curriculum.json, assignments/ (shared), texts/        shared by both arms
plans/ela9-v2.json        the plan `build-course` reads (profiles, families, interventions)
skill_families.json       skill families and their standards (lab only; the generator and answer keys use it)
courses/<version>/
  course.json             id classroom_<version>, twin, curriculum, start date
  roster.jsonl            students (hashed ids)
  interventions.json      which students get which intervention, and when
  assignments/            the course's own (intervention) assignments
  submissions/<id>.jsonl  one row per submission (paragraphs, or answers for multiple choice)
  answer_key/             students.jsonl, trajectory.jsonl, expected/<id>.jsonl, fidelity.jsonl
```

**Visibility rule.** The reviewer and the file-backed LMS Adapter (`CourseDirLMSClient`) read only the course
folder minus `answer_key/`, and the shared curriculum. The lab's `skill_families.json` (at the top of this folder) is never read by production either. Everything under `answer_key/` is
ground truth (each student's skill families and levels, the expected weaknesses per submission, and the
fidelity check of what the generated text actually shows). Only the lab's evaluation reads it; tests assert
production never opens it. In the files, assignment ids are file ids (`ela9v2-a05`); the Adapter serves
`<file id>@<version>`. Levels: 1 Approaching, 2 Developing, 3 Beginning; meets is absent.

Generated rows are model output, so they are synthetic like everything else here; they still hold student-like
text, which is why reports and logs only ever name ids.

**Generation model.** The committed v2 slice (weeks 1-3, 6 students per arm) was generated with
`gemini-3.8-flash`. `SYNTH_LLM_PROVIDER`, `SYNTH_LLM_MODEL`, `SYNTH_LLM_API_KEY` and `SYNTH_LLM_RPM` override the
generator's model (each falls back to the matching `LLM_*` value, which is the reviewer's model).
`SYNTH_WORKERS` sets how many submissions are generated in parallel (default 8).
