import re

_KENYAN_MOBILE = re.compile(r"^254(7|1)\d{8}$")


def normalize_msisdn(raw: str) -> str:
    """Convert a Kenyan mobile number to the 2547XXXXXXXX / 2541XXXXXXXX form Daraja expects.

    Accepts 07..., 01..., 7..., 1..., +254..., 254..., with spaces or dashes.
    Raises ValueError if the result is not a valid Kenyan mobile number.
    """
    digits = re.sub(r"[\s\-()]", "", raw or "")
    if digits.startswith("+"):
        digits = digits[1:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "71":
        digits = "254" + digits
    if not _KENYAN_MOBILE.match(digits):
        raise ValueError(f"Not a valid Kenyan mobile number: {raw!r}")
    return digits
