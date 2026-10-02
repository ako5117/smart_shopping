"""What the customer is texted, and the phone numbers it goes to.

Each text fits one SMS: at most 160 characters, using only characters in the GSM 03.38 alphabet (a
single curly quote or emoji would turn it into a 70-character Unicode SMS, costing two or three).
"""

import re
from typing import Optional

LIMIT = 160
# The basic GSM 03.38 alphabet (no extension table), which every Kenyan network and phone handles.
GSM = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZ"
          "ÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")


def msisdn(phone: str) -> Optional[str]:
    """A Kenyan mobile number as +2547XXXXXXXX / +2541XXXXXXXX, or None if it isn't one."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "71":
        digits = "254" + digits
    return "+" + digits if re.fullmatch(r"254[71]\d{8}", digits) else None


def gsm(text: str) -> str:
    """Swap the usual non-GSM characters (curly quotes, dashes) and drop anything else."""
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "..."}))
    return "".join(c for c in text if c in GSM)


def _short(name: str, n: int) -> str:
    name = gsm(name).strip()
    return name if len(name) <= n else name[:n - 1].rstrip() + "."


def paid(store: str, code: str, total: int, pin: Optional[str] = None) -> str:
    if pin:
        return fit(f"{_short(store, 24)}: order {code} is paid, KES {total:,}. Your delivery code is {pin}. "
                   f"Give it to the rider when your order arrives.")
    return fit(f"{_short(store, 24)}: order {code} is paid, KES {total:,}. "
               f"We'll text you when it's ready to collect.")


def ready(store: str, code: str) -> str:
    return fit(f"{_short(store, 24)}: order {code} is ready. Collect it at the counter and show the pass on "
               f"your order page.")


def on_the_way(store: str, code: str, rider: str, rider_phone: str, pin: str) -> str:
    who = _short(rider, 20) + (f" ({rider_phone})" if rider_phone else "")
    return fit(f"{_short(store, 24)}: order {code} is on the way with {who}. Delivery code {pin}.")


def fit(text: str) -> str:
    text = gsm(text)
    if len(text) > LIMIT:  # can't happen with the trimmed names above; a guard against future edits
        raise ValueError(f"Text too long for one SMS ({len(text)} characters): {text}")
    return text
