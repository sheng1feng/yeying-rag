from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

TMP_ROOT = Path(tempfile.gettempdir()) / "knowledge_test_runtime"
TMP_ROOT.mkdir(parents=True, exist_ok=True)
db_path = TMP_ROOT / "knowledge_test.db"
if db_path.exists():
    db_path.unlink()

os.environ.setdefault("DATABASE_URL", f"sqlite:///{db_path}")
os.environ.setdefault("WAREHOUSE_GATEWAY_MODE", "mock")
os.environ.setdefault("WAREHOUSE_MOCK_ROOT", str(TMP_ROOT / "mock_warehouse"))
os.environ.setdefault("VECTOR_STORE_MODE", "db")
os.environ.setdefault("MODEL_PROVIDER_MODE", "mock")
