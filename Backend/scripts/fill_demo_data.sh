#!/usr/bin/env bash
# Fill the database with demo data using Justin's tool (AgenticAI/).
#
#   1. seed-demo       -> teacher, classroom 1.01, 20 students, their enrollments, assignment
#   2. load-standards  -> the 335 Missouri ELA standards
#   3. (only with REVIEW=1) review --persist x20 -> fake reviews with placeholder findings
#
# By default it stops after step 2, so every student shows "not reviewed yet" and the
# "Review class" button does the reviewing in the demo.
#
#   bash Backend/scripts/fill_demo_data.sh            # demo setup (recommended)
#   REVIEW=1 bash Backend/scripts/fill_demo_data.sh   # also pre-review every essay with fake findings
#
# Uses FAKE_LLM=1 for the standards (and reviews), so no API key is needed.
# Needs: Postgres running with Justin's 6 migrations applied (user sass / change-me, db sass).
set -euo pipefail

CLASSROOM="${CLASSROOM:-1.01}"
REVIEW="${REVIEW:-0}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AGENTIC="$REPO_ROOT/AgenticAI"
ESSAYS="$REPO_ROOT/AgenticLab/data/synthetic/essays_classroom_${CLASSROOM}.jsonl"

cd "$AGENTIC"

echo "== Installing Justin's agentic tool"
pip install -q -e .

echo "== Preparing AgenticAI/.env"
[ -f .env ] || cp .env.example .env
grep -q '^DATA_DIR=../AgenticLab/data/synthetic' .env || echo "DATA_DIR=../AgenticLab/data/synthetic" >> .env

echo "== 1/2 Classroom, teacher, students, enrollments, assignment"
python -m agentic seed-demo --classroom "$CLASSROOM"

echo "== 2/2 Missouri standards"
FAKE_LLM=1 python -m agentic load-standards

if [ "$REVIEW" = "1" ]; then
  echo "== Extra: reviewing every essay with fake findings (REVIEW=1)"
  for s in $(python -c "import json,sys;[print(json.loads(l)['student_hash'][:12]) for l in open(sys.argv[1])]" "$ESSAYS"); do
    FAKE_LLM=1 python -m agentic review "$s" --classroom "$CLASSROOM" --persist | tail -1
  done
fi

echo "== Done. Row counts:"
python - <<'PY'
import psycopg
from agentic.config import Settings
with psycopg.connect(Settings.load().database_url_local) as c:
    for t in ("classroom", "student", "enrollment", "standard", "submission", "finding"):
        print(f"  {t:<11} {c.execute(f'SELECT count(*) FROM {t}').fetchone()[0]}")
    if c.execute("SELECT count(*) FROM enrollment").fetchone()[0] == 0:
        print("\n  Warning: no enrollments, so the Students page will be empty.\n"
              "  Pull the latest main: seed-demo needs Justin's enrollment change.")
PY

if [ "$REVIEW" != "1" ]; then
  cat <<EOF

Next: nothing is reviewed yet. Start the agentic service and the backend, then click
"Review class" on the Students page (or POST /classrooms/classroom_${CLASSROOM}/reviews).
To start over after a demo:  cd AgenticAI && python -m agentic reset-reviews --classroom ${CLASSROOM}
EOF
fi
