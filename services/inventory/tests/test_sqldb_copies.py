from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
OTHER = HERE.parent / "payments" / "app" / "sqldb.py"


@pytest.mark.skipif(not OTHER.exists(), reason="payments service not alongside (e.g. inside a Docker build)")
def test_payments_uses_the_same_storage_layer():
    """services/inventory/inventory/sqldb.py and services/payments/app/sqldb.py must stay identical."""
    assert (HERE / "inventory" / "sqldb.py").read_text() == OTHER.read_text()
