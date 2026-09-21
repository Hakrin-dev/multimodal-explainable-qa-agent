#!/usr/bin/env bash
# One-command bootstrap: DB + Chinook import + terms + backend + smoke test.
# W1 deliverable (PLAN §8). Usage: ./deploy/quick_start.sh [--reset-db]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RESET_DB=0
[[ "${1:-}" == "--reset-db" ]] && RESET_DB=1

# 1. env file
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "· created .env from template (fill API keys to enable real LLMs)"
fi

# 2. database up
echo "· starting postgres (pgvector) …"
docker compose -f deploy/docker-compose.yml --env-file .env --project-name mqa up -d db

echo -n "· waiting for db"
for i in $(seq 1 30); do
  if docker compose -f deploy/docker-compose.yml --env-file .env --project-name mqa exec -T db \
       pg_isready -U "${POSTGRES_USER:-chinook}" -d "${POSTGRES_DB:-chinook}" >/dev/null 2>&1; then
    echo " ok"; break
  fi
  echo -n "."; sleep 1
  [[ $i == 30 ]] && { echo " FAILED"; exit 1; }
done

# 3. import Chinook + extract term dictionary (run once; idempotent data load)
DB_STATE=$(docker compose -f deploy/docker-compose.yml --env-file .env --project-name mqa exec -T db \
  psql -U "${POSTGRES_USER:-chinook}" -d "${POSTGRES_DB:-chinook}" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_name='track'" 2>/dev/null || echo 0)

if [[ "$DB_STATE" == "0" || $RESET_DB == 1 ]]; then
  echo "· importing Chinook (SQLite -> PostgreSQL) …"
  docker compose -f deploy/docker-compose.yml --env-file .env --project-name mqa run --rm --no-deps backend \
    python scripts/chinook_to_pg.py --drop
fi

echo "· extracting term dictionary …"
docker compose -f deploy/docker-compose.yml --env-file .env --project-name mqa run --rm --no-deps backend \
  python scripts/extract_terms.py

# 3b. knowledge-base docs (PDFs versioned in git; ingest idempotent by content hash).
# Clean state: embedding model is NOT in git — B downloads it per onboarding doc;
# until then the RAG path is skipped gracefully so NL2SQL flow stays green.
EMB_MODEL_OK=$(docker compose -f deploy/docker-compose.yml --env-file .env --project-name mqa \
  run --rm --no-deps backend python -c \
  "from app.core.config import resolve_repo_path, Settings; exit(0 if resolve_repo_path(Settings().embedding_model_path).exists() else 1)" 2>/dev/null \
  && echo yes || echo no)
if [ "$EMB_MODEL_OK" = "yes" ] && ls data/docs_raw/*.pdf >/dev/null 2>&1; then
  echo "· ingesting KB docs (local embedding) …"
  docker compose -f deploy/docker-compose.yml --env-file .env --project-name mqa \
    run --rm --no-deps backend python scripts/ingest_docs.py 2>/dev/null | tail -4
else
  echo "· KB ingest skipped (embedding model not downloaded — see docs/onboarding/B_onboarding.md §1)"
fi

# 4. backend + frontend up
echo "· starting backend + frontend …"
docker compose -f deploy/docker-compose.yml --env-file .env --project-name mqa up -d backend frontend

# 5. smoke test
echo "· smoke test …"
for i in $(seq 1 20); do
  curl -sf http://localhost:8000/api/health >/dev/null 2>&1 && break
  sleep 1
done
curl -s http://localhost:8000/api/health
echo

RESP=$(curl -s -X POST http://localhost:8000/api/nl2sql \
  -H 'Content-Type: application/json' \
  -d '{"question": "数据库里一共有多少种音乐曲风？"}')
echo "· /api/nl2sql smoke (mock provider):"
echo "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print('  status =', d['status']); print('  sql    =', d['sql'] or '(mock: no SQL without API key)')"

# orchestrated chat smoke (intent routing + trace tree)
CHAT=$(curl -s -m 60 -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"question": "销量前十的曲目是哪些", "session_id": "quickstart"}')
echo "· /api/chat smoke (orchestration):"
echo "$CHAT" | python3 -c "import json,sys; d=json.load(sys.stdin); print('  intent =', d['intent'], '| status =', d['status']); print('  nodes  =', [c['label'] for c in d['trace']['root']['children']])" 2>/dev/null \
  || echo '  (chat smoke unavailable)'

# scripted-mock eval over NL2SQL cases — proves the full loop (no API key needed)
echo "· scripted eval smoke (mock provider):"
docker compose -f deploy/docker-compose.yml --env-file .env --project-name mqa \
  run --rm --no-deps backend python scripts/smoke_nl2sql.py 2>/dev/null | tail -4

# RAG retrieval smoke (only when embedding model is present)
if [ "$EMB_MODEL_OK" = "yes" ]; then
  echo "· RAG retrieval smoke:"
  docker compose -f deploy/docker-compose.yml --env-file .env --project-name mqa \
    run --rm --no-deps backend python scripts/smoke_rag.py 2>/dev/null | grep -E 'recall|loaded' | tail -2
fi

cat <<'EOF'

✓ done.
  API      http://localhost:8000/api/health
  NL2SQL   POST http://localhost:8000/api/nl2sql  {"question": "..."}
  Frontend http://localhost:8090 (placeholder until W2)
  Provider currently: see .env LLM_PROVIDER (mock works without keys)
EOF
