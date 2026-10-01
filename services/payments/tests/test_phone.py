import pytest

from app.phone import normalize_msisdn


@pytest.mark.parametrize("raw", ["0708374149", "+254708374149", "254708374149", "708374149", "0708 374 149", "0708-374-149"])
def test_normalizes_safaricom_07_numbers(raw):
    assert normalize_msisdn(raw) == "254708374149"


def test_normalizes_01_numbers():
    assert normalize_msisdn("0110123456") == "254110123456"


@pytest.mark.parametrize("raw", ["", "12345", "0208374149", "25470837414", "abc", "+1 555 123 4567"])
def test_rejects_invalid_numbers(raw):
    with pytest.raises(ValueError):
        normalize_msisdn(raw)
