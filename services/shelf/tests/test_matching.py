import pytest

from shelf.matching import allowed_error_g, weight_candidates
from shelf.models import Product

FLOUR = Product("flour", "6161000000019", "Flour", 2012, 18, "flour")
SUGAR = Product("sugar", "6161000000026", "Sugar", 1010, 8, "sugar")
RICE = Product("rice", "6161000000033", "Rice", 1006, 8, "rice")
MILK = Product("milk", "6161000000040", "Milk", 528, 6, "milk")


def test_single_item():
    [c] = weight_candidates(-2012, [FLOUR, MILK])
    assert (c.product, c.qty) == (FLOUR, 1)


@pytest.mark.parametrize("qty", [2, 3, 5])
def test_multiple_identical_items(qty):
    [c] = weight_candidates(-528 * qty, [MILK])
    assert c.qty == qty


def test_similar_weights_both_fit():
    ids = {c.product.product_id for c in weight_candidates(-1008, [SUGAR, RICE], sensor_noise_g=2)}
    assert ids == {"sugar", "rice"}


def test_no_fit_between_whole_items():
    assert weight_candidates(-792, [MILK], sensor_noise_g=2) == []  # 1.5 milks


def test_tolerance_grows_with_quantity_and_noise():
    assert allowed_error_g(MILK, 4, 0) == pytest.approx(12.0)
    assert allowed_error_g(MILK, 1, 2) > allowed_error_g(MILK, 1, 0)


def test_sensor_noise_widens_the_window():
    assert weight_candidates(-540, [MILK]) == []                      # 12 g off, product tolerance only
    assert len(weight_candidates(-540, [MILK], sensor_noise_g=3)) == 1  # plausible with noisy readings
