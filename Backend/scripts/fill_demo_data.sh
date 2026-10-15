#!/usr/bin/env bash
# Fill the database with demo data using Justin's tool (AgenticAI/).
#
#   1. seed-demo       -> teacher, classroom 1.01, 20 students, assignment
#   2. load-standards  -> the 335 Missouri ELA standards
#   3. review --persist (x20) -> reviews every essay and saves findings
#
# Uses FAKE_LLM=1, so no API key is needed (findings are placeholders, not real AI feedback).
# Needs: Postgres running with Justin's 6 migrations applied (user sass / change-me, db sass).
# Run from anywhere:  bash Backend/scripts/fill_demo_data.sh
set -euo pipefail

CLASSROOM="${CLASSROOM:-1.01}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AGENTIC="$REPO_ROOT/AgenticAI"
ESSAYS="$REPO_ROOT/AgenticLab/data/synthetic/essays_classroom_${CLASSROOM}.jsonl"

cd "$AGENTIC"

echo "== Installing Justin's agentic tool"
pip install -q -e .

echo "== Preparing AgenticAI/.env"
[ -f .env ] || cp .env.example .env
grep -q '^DATA_DIR=../AgenticLab/data/synthetic' .env || echo "DATA_DIR=../AgenticLab/data/synthetic" >> .env

echo "== 1/3 Classroom, teacher, students, assignment"
python -m agentic seed-demo --classroom "$CLASSROOM"

echo "== 2/3 Missouri standards"
FAKE_LLM=1 python -m agentic load-standards

echo "== 3/3 Reviewing every essay and saving findings"
for s in $(python -c "import json,sys;[print(json.loads(l)['student_hash'][:12]) for l in open(sys.argv[1])]" "$ESSAYS"); do
  FAKE_LLM=1 python -m agentic review "$s" --classroom "$CLASSROOM" --persist | tail -1
done

echo "== Done. Row counts:"
python - <<'PY'
import psycopg
from agentic.config import Settings
with psycopg.connect(Settings.load().database_url_local) as c:
    for t in ("classroom", "student", "standard", "submission", "finding"):
        print(f"  {t:<11} {c.execute(f'SELECT count(*) FROM {t}').fetchone()[0]}")
PY
