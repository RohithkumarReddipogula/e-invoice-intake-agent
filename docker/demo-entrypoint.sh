#!/usr/bin/env bash
# Starts the mock ERP (internal, 127.0.0.1:8001) and the intake app (0.0.0.0:$PORT).
# If either process ends, the other is stopped and the container exits, so the platform
# restarts it in a clean state.
set -euo pipefail

python -m mock_erp --demo --db /app/data/mock_erp.db --port 8001 &
erp=$!

# The intake app resets the ERP at startup, so wait until the ERP answers.
for _ in $(seq 1 50); do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/api/invoices', timeout=1)" 2>/dev/null; then
    break
  fi
  sleep 0.2
done

python -m intake &
app=$!

trap 'kill -TERM "$erp" "$app" 2>/dev/null || true' TERM INT
set +e
wait -n "$erp" "$app"
status=$?
kill -TERM "$erp" "$app" 2>/dev/null
wait
exit "$status"
