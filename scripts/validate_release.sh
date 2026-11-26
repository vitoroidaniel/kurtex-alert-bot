#!/bin/sh
set -eu
# A clean ephemeral environment: no application credentials are inherited.
fixture_dir=$(mktemp -d)
trap 'rm -rf "$fixture_dir"' EXIT
env -i PATH="$PATH" HOME="$fixture_dir" DATA_DIR="$fixture_dir/data" \
    API_SESSION_SECRET="synthetic-validation-only-secret" BOT_TOKEN="123456:FIXTURE" \
    python - <<'PY'
import socket
import sys
import unittest
from unittest.mock import patch

def no_network(*args, **kwargs):
    raise AssertionError("Network access is prohibited in storage validation")

suite = unittest.TestSuite()
sys.path.insert(0, "tests")
with patch.object(socket.socket, "connect", no_network), patch.object(socket.socket, "connect_ex", no_network), patch.object(socket, "getaddrinfo", no_network):
    for name in ("test_sqlite_snapshot", "test_smart_list_query_plan",
                 "test_smart_lists_store_and_routes", "test_presence_concurrency",
                 "test_v304_live_board_collaboration",
                 "test_v305_live_board_production_storage"):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
print("BACKEND_STORAGE_AND_RECOVERY_VALIDATION_PASSED", flush=True)
PY
