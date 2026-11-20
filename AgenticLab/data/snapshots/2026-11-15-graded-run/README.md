# Snapshot: real reviews from the 2026-11-15 graded run

A Postgres data dump of what the **real reviewer** produced on the synthetic twin classrooms. Use it to build and
test the Backend and Frontend against real-looking review output: false flags, real support levels, root-cause
links, trends, reteach groups and peer pairs.

`fill-demo` gives clean data straight from the answer key. This snapshot gives what the model actually said, which
is messier and closer to what the dashboards will show.

## What's in it

| | |
|---|---|
| Classrooms | `classroom_2.00-control` (English 9, Period 1) and `classroom_2.00-intervention` (Period 2); teacher `demo-teacher` |
| Students | 18 enrolled per class, but **only 6 per class were reviewed** (the other 12 have no reviews) |
| Weeks | 1–7 (submissions dated 2027-01-12 to 2027-02-27); 14 assignments per class reviewed |
| Reviews | 169 submissions (84 control, 85 intervention): 42,849 verdicts, 2,708 findings, 6,614 observations, 314 root-cause links |
| Analytics | 1,054 concept statuses (with trends), 1,158 class-concept-week rows, 8 reteach groups, 36 peer-pair suggestions |
| Standards | 432 rows from `mls-ela-reviewable-v2.xlsx`, with **placeholder embeddings** (don't use them for similarity search) |

The configuration was v2 pipeline, `graded` profile, `gemini-3-flash-preview`. Measured accuracy against the answer
key: precision about 0.33 and recall about 0.73 (exact standard). Many findings are therefore false or filed under a
neighbouring standard. Treat the content as realistic placeholder text, not as correct results.

**Not included:** teacher responses (none yet), review policy and settings overrides, display labels (the
migrations insert those), and the `trend_insight` summary sentences (not built yet).

**Privacy:** IDs, codes, levels and short reasons only; no essay text. All data is synthetic. A few reasons quote a
few words of the story being read (for example "Whitney admits 'a sudden dread'").

## Load it

It needs a **fresh** database with migrations V1–V8 applied. It uses the same classroom IDs as `fill-demo`, so don't
load both into one database. From the team repo root, with Docker running (Git Bash):

```bash
cd AgenticAI
docker compose down -v                      # wipes the local database
docker compose up -d postgres
docker compose up --exit-code-from flyway flyway
gunzip -c ../AgenticLab/data/snapshots/2026-11-15-graded-run/data.sql.gz \
  | docker compose exec -T postgres sh -c 'psql -q -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Use Git Bash, not PowerShell: Windows PowerShell 5.1 re-encodes piped text and can corrupt the dump.

The load runs in one transaction: it either loads everything or nothing. The dump disables triggers while loading
(the rows already passed them when they were saved), so the database user must be a superuser, as the compose
`postgres` user is.

**Check:** `select count(*) from verdict;` should return 42849.

## How it was made

`pg_dump --data-only --disable-triggers --no-owner --no-privileges --exclude-table=flyway_schema_history
--exclude-table=display_label` on the lab's run database. The load was tested on a fresh V1–V8 database: every row
count matched, and `python -m agentic analyze` gave the same statuses and trends as the original.
