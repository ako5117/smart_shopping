import pytest

from notify import texts


@pytest.mark.parametrize("raw,want", [
    ("0712345678", "+254712345678"), ("0112345678", "+254112345678"), ("+254 712 345 678", "+254712345678"),
    ("254712345678", "+254712345678"), ("712345678", "+254712345678"), ("0712-345-678", "+254712345678"),
])
def test_kenyan_mobile_numbers(raw, want):
    assert texts.msisdn(raw) == want


@pytest.mark.parametrize("raw", ["", "020 123 4567", "0812345678", "071234567", "+255712345678", "hello"])
def test_not_kenyan_mobiles(raw):
    assert texts.msisdn(raw) is None


def test_gsm_swaps_curly_quotes_and_drops_emoji():
    assert texts.gsm("We’ll “see” – ok… 🎉") == "We'll \"see\" - ok... "


LONG = "The Very Long Named Supermarket And Grocery Emporium Ltd"


def test_every_text_fits_one_sms_even_with_long_names():
    bodies = [texts.paid(LONG, "ABCDEFGH", 1_234_567), texts.paid(LONG, "ABCDEFGH", 1_234_567, "4821"),
              texts.ready(LONG, "ABCDEFGH"),
              texts.on_the_way(LONG, "ABCDEFGH", "Bartholomew-Wanyama-Ochieng", "+254712345678", "4821")]
    for b in bodies:
        assert len(b) <= texts.LIMIT
        assert set(b) <= texts.GSM


def test_paid_with_and_without_delivery_code():
    assert texts.paid("Shop", "ABCDEFGH", 2500) == \
        "Shop: order ABCDEFGH is paid, KES 2,500. We'll text you when it's ready to collect."
    assert "Your delivery code is 4821" in texts.paid("Shop", "ABCDEFGH", 2500, "4821")


def test_on_the_way_without_rider_phone():
    assert texts.on_the_way("Shop", "ABCDEFGH", "otieno", "", "0042") == \
        "Shop: order ABCDEFGH is on the way with otieno. Delivery code 0042."


def test_fit_refuses_long_text():
    with pytest.raises(ValueError):
        texts.fit("x" * 161)
