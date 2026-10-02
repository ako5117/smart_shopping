from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
COPIES = [HERE.parent / "payments" / "app" / "sqldb.py", HERE.parent / "dispatch" / "dispatch" / "sqldb.py",
          HERE.parent / "notify" / "notify" / "sqldb.py"]


@pytest.mark.parametrize("other", COPIES, ids=["payments", "dispatch", "notify"])
def test_services_use_the_same_storage_layer(other):
    """services/inventory/inventory/sqldb.py is copied into services/payments, services/dispatch and services/notify; keep them identical."""
    if not other.exists():
        pytest.skip("other service not alongside (e.g. inside a Docker build)")
    assert (HERE / "inventory" / "sqldb.py").read_text() == other.read_text()
