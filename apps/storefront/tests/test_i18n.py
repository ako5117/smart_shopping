import ast
from pathlib import Path

import pytest

from storefront.i18n import missing, translate

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent.parent


def messages(path: Path, raises: bool = False, strings_in=()):
    """The English messages a file can send customers: HTTPException details (and, with raises, every exception
    raised with a message; strings_in: the values of these dict keys), with {} for the filled-in parts."""
    def text(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.JoinedStr):
            out = [""]
            for v in node.values:
                if isinstance(v, ast.Constant):
                    out = [o + v.value for o in out]
                else:
                    out = [o + "{}" for o in out]
            return out
        if isinstance(node, ast.IfExp):
            return text(node.body) + text(node.orelse)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
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
        elif strings_in and isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value in strings_in:
                    found += [t for t in text(v) if t]
        elif strings_in and isinstance(node, ast.Assign) and any(getattr(t, "id", "") in strings_in for t in node.targets):
            values = node.value.values if isinstance(node.value, ast.Dict) else [node.value]
            found += [t for v in values for t in text(v) if t]
    return [m for m in found if m.strip()]


def shop_messages():
    return [m for f in ("app.py", "account.py", "reports.py") for m in messages(HERE / "storefront" / f)]


def passed_on():
    """What the services behind the shop say that the shop passes on to customers."""
    services = REPO / "services"
    return (messages(services / "customers" / "customers" / "service.py", raises=True)
            + messages(services / "customers" / "customers" / "reports.py", raises=True)
            + messages(services / "dispatch" / "dispatch" / "pricing.py", raises=True, strings_in=("reason", "label"))
            + messages(services / "inventory" / "inventory" / "returns.py", raises=True, strings_in=("reason", "why"))
            + messages(services / "customers" / "customers" / "reports.py", strings_in=("KINDS",)))


def test_every_message_has_a_kiswahili_translation():
    said = shop_messages()
    assert len(said) > 40
    assert missing(said) == []


def test_what_the_services_say_is_translated_too():
    if not (REPO / "services" / "customers").exists():
        pytest.skip("the services aren't alongside (e.g. inside a Docker build)")
    said = passed_on()
    assert len(said) > 30
    assert missing(said) == []


def test_delivery_words():
    assert translate("Today 14:00–16:00", "sw") == "Leo 14:00–16:00"
    assert translate("Saturday 10:00–12:00", "sw") == "Jumamosi 10:00–12:00"
    assert translate("15% off", "sw") == "Punguzo la 15%"
