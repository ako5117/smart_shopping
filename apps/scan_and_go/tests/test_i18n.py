import ast
from pathlib import Path

import pytest

from scan_and_go.i18n import missing, translate, translate_detail

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent.parent


def messages(path: Path, raises: bool = False):
    """The English messages a file can send customers: HTTPException details (and, with raises, every
    exception raised with a message), with {} for the filled-in parts."""
    def text(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.JoinedStr):
            return ["".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)]
        if isinstance(node, ast.IfExp):
            return text(node.body) + text(node.orelse)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):  # "a " "b" split over lines with +
            left, right = text(node.left), text(node.right)
            return [l + r for l in left for r in right] if left and right else []
        if isinstance(node, ast.Dict):
            return [t for k, v in zip(node.keys, node.values)
                    if isinstance(k, ast.Constant) and k.value == "message" for t in text(v)]
        return []

    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "HTTPException" and len(node.args) > 1:
            found += text(node.args[1])
        elif raises and isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            found += text(node.exc.args[0])
    return found


def test_every_message_has_a_kiswahili_translation():
    said = (messages(HERE / "scan_and_go" / "app.py") + messages(HERE / "scan_and_go" / "airport.py", raises=True)
            + messages(HERE / "scan_and_go" / "account.py"))
    assert len(said) > 30
    assert missing(said) == []


def test_translate():
    assert translate("Order not found", "sw") == "Oda haikupatikana"
    assert translate("Order not found", "en") == "Order not found"
    assert translate("Something new nobody translated", "sw") == "Something new nobody translated"
    assert translate("Sugar 1 kg has no Scan & Go price yet. Please pay for it at a till.", "sw") == \
        "Sugar 1 kg bado haina bei ya Scan & Go. Tafadhali ilipie kwenye kaunta ya malipo."
    assert translate("KQ200 today goes to LHR, not JFK. Check your boarding pass.", "sw") == \
        "KQ200 ya leo inaenda LHR, si JFK. Kagua pasi yako ya kupanda ndege."
    d = translate_detail({"message": "Not enough in stock: Milk. Change your basket and try again.", "short": ["milk"]}, "sw")
    assert d == {"message": "Hazitoshi dukani: Milk. Badilisha kikapu chako kisha ujaribu tena.", "short": ["milk"]}


def test_the_online_shop_has_the_same_copy():
    other = REPO / "apps" / "storefront" / "storefront" / "i18n.py"
    if not other.exists():
        pytest.skip("the online shop isn't alongside (e.g. inside a Docker build)")
    assert (HERE / "scan_and_go" / "i18n.py").read_text() == other.read_text()


def test_errors_come_in_the_page_language(client):
    assert client.get("/api/products/6161000000041", headers={"X-Language": "sw"}).json()["detail"] == \
        "Huo si msimbopau sahihi. Jaribu kuchanganua tena."
    assert client.get("/api/products/6161000000041").json()["detail"] == "That isn't a valid barcode. Try scanning again."
    assert client.get("/api/products/6161000000041", headers={"X-Language": "fr"}).json()["detail"].startswith("That isn't")


def test_sign_in_messages_from_the_customers_service_are_translated():
    """Scan & Go passes on what the Customers Service says when a code is asked for or checked."""
    service = REPO / "services" / "customers" / "customers" / "service.py"
    if not service.exists():
        pytest.skip("the Customers Service isn't alongside (e.g. inside a Docker build)")
    assert missing(messages(service, raises=True)) == []
