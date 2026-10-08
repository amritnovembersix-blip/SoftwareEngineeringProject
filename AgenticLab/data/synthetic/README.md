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
