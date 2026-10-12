"""Messages in the customer's language: English, or Kiswahili.

The page sends the language it's showing in an X-Language header (en or sw). Error messages are written in English in
the code; install() translates them on the way out, from the catalogue below. A message missing from it goes out in
English, so a gap never breaks anything (tests/test_i18n.py checks the catalogue covers what the code can say).

A message with something filled in (a product name, a number) is listed with {name}-style placeholders. The
filled-in parts are kept as they are, unless one is itself in the catalogue (the day in "Today 14:00-16:00").

The online shop (apps/storefront) and Scan & Go have identical copies of this file; a test checks they match.
"""

import re
from typing import Dict, List, Optional, Pattern, Tuple

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException

LANGUAGES = {"en": "English", "sw": "Kiswahili"}
HEADER = "x-language"

SW: Dict[str, str] = {
    # --- Both apps: payments and orders
    "Too many payment requests for this number. Please wait a few minutes.":
        "Maombi mengi mno ya malipo kwa nambari hii. Tafadhali subiri dakika chache.",
    "Enter a valid Safaricom number, e.g. 0712 345 678.": "Weka nambari sahihi ya Safaricom, mfano 0712 345 678.",
    "M-Pesa could not send the payment request. Please try again.":
        "M-Pesa haikuweza kutuma ombi la malipo. Tafadhali jaribu tena.",
    "Card payments aren't available right now. Please pay with M-Pesa.":
        "Malipo ya kadi hayapatikani kwa sasa. Tafadhali lipa kwa M-Pesa.",
    "Card payments aren't available right now. Please pay with M-Pesa or at a till.":
        "Malipo ya kadi hayapatikani kwa sasa. Tafadhali lipa kwa M-Pesa au kwenye kaunta ya malipo.",
    "Enter your email address: the card receipt goes there.": "Weka barua pepe yako: risiti ya kadi itatumwa huko.",
    "Enter a valid email address for the card receipt.": "Weka barua pepe sahihi kwa risiti ya kadi.",
    "Card payment couldn't start. Please try again, or pay with M-Pesa.":
        "Malipo ya kadi hayakuweza kuanza. Tafadhali jaribu tena, au lipa kwa M-Pesa.",
    "Enter your M-Pesa number.": "Weka nambari yako ya M-Pesa.",
    "Order not found": "Oda haikupatikana",
    "This order is already paid.": "Oda hii tayari imelipwa.",
    "This order was cancelled.": "Oda hii ilighairiwa.",
    "No photo": "Hakuna picha",

    # --- Scan & Go
    "Scan your boarding pass first.": "Changanua pasi yako ya kupanda ndege kwanza.",
    "Flight details can't be checked right now. Please pay at a till.":
        "Maelezo ya safari hayawezi kukaguliwa kwa sasa. Tafadhali lipa kwenye kaunta ya malipo.",
    "The store system is not reachable right now. Please pay at a till.":
        "Mfumo wa duka haupatikani kwa sasa. Tafadhali lipa kwenye kaunta ya malipo.",
    "Problem reports aren't set up in this store. Please ask a member of staff.":
        "Ripoti za matatizo hazijawekwa katika duka hili. Tafadhali muulize mhudumu.",
    "Thanks: we've had several reports from you in the last hour. Please ask a member of staff.":
        "Asante: tumepokea ripoti kadhaa kutoka kwako katika saa iliyopita. Tafadhali muulize mhudumu.",
    "Reports can't be sent right now. Please ask a member of staff.":
        "Ripoti haziwezi kutumwa kwa sasa. Tafadhali muulize mhudumu.",
    "Please check what you wrote and try again.": "Tafadhali kagua ulichoandika kisha ujaribu tena.",
    "This store isn't in an airport": "Duka hili haliko uwanja wa ndege",
    "That isn't a valid barcode. Try scanning again.": "Huo si msimbopau sahihi. Jaribu kuchanganua tena.",
    "This product isn't in Scan & Go yet. Please pay for it at a till.":
        "Bidhaa hii bado haiko kwenye Scan & Go. Tafadhali ilipie kwenye kaunta ya malipo.",
    "{name} has no Scan & Go price yet. Please pay for it at a till.":
        "{name} bado haina bei ya Scan & Go. Tafadhali ilipie kwenye kaunta ya malipo.",
    "Scan & Go takes up to {n} different products.": "Scan & Go inachukua hadi bidhaa {n} tofauti.",
    "For more than {n} of one product, please use a till.":
        "Kwa zaidi ya {n} za bidhaa moja, tafadhali tumia kaunta ya malipo.",
    "Something in your basket can't be bought with Scan & Go. Please remove it.":
        "Kuna bidhaa kwenye kikapu chako isiyoweza kununuliwa kwa Scan & Go. Tafadhali iondoe.",
    "The store system couldn't start your order. Please pay at a till.":
        "Mfumo wa duka haukuweza kuanzisha oda yako. Tafadhali lipa kwenye kaunta ya malipo.",

    # --- Scan & Go points (scan_and_go/account.py)
    "Points aren't available in this store": "Pointi hazipatikani katika duka hili",
    "Points aren't available right now. You can still pay without them.":
        "Pointi hazipatikani kwa sasa. Bado unaweza kulipa bila hizo.",
    "Your points can't be used right now. Try again, or pay without them.":
        "Pointi zako haziwezi kutumika kwa sasa. Jaribu tena, au lipa bila hizo.",

    # --- Scan & Go airport mode (scan_and_go/airport.py)
    "That flight number isn't right": "Nambari hiyo ya safari si sahihi",
    "Enter the flight number as on your boarding pass, e.g. KQ 100":
        "Weka nambari ya safari kama ilivyo kwenye pasi yako ya kupanda ndege, mfano KQ 100",
    "That isn't a boarding pass barcode": "Huo si msimbopau wa pasi ya kupanda ndege",
    "That boarding pass barcode is cut short": "Msimbopau huo wa pasi ya kupanda ndege haujakamilika",
    "That boarding pass barcode can't be read": "Msimbopau huo wa pasi ya kupanda ndege hausomeki",
    "AIRPORT_CODE should be the 3-letter IATA code, e.g. NBO": "AIRPORT_CODE iwe msimbo wa IATA wa herufi 3, mfano NBO",
    "This boarding pass is for {day}. Use the one for today's flight.":
        "Pasi hii ya kupanda ndege ni ya {day}. Tumia ile ya safari ya leo.",
    "This boarding pass is for {day}, not today.": "Pasi hii ya kupanda ndege ni ya {day}, si ya leo.",
    "We can't find {flight} leaving {airport} today. Check the flight number, or ask a member of staff.":
        "Hatuioni {flight} ikiondoka {airport} leo. Kagua nambari ya safari, au muulize mhudumu.",
    "{flight} today goes to {to}, not {other}. Check your boarding pass.":
        "{flight} ya leo inaenda {to}, si {other}. Kagua pasi yako ya kupanda ndege.",
    "{flight} was due to leave at {time}. Please ask a member of staff.":
        "{flight} ilitarajiwa kuondoka saa {time}. Tafadhali muulize mhudumu.",
    "Enter your name as on your boarding pass": "Weka jina lako kama lilivyo kwenye pasi yako ya kupanda ndege",
    "This boarding pass is for a flight from {other}, not {airport}.":
        "Pasi hii ya kupanda ndege ni ya safari kutoka {other}, si {airport}.",
    "The destination is a 3-letter airport code, e.g. LHR": "Unakoenda ni msimbo wa uwanja wa ndege wa herufi 3, mfano LHR",
    "Enter the flight date": "Weka tarehe ya safari",

    # --- Online shop (apps/storefront)
    "The shop can't reach the store right now. Please try again in a minute.":
        "Duka la mtandaoni haliwezi kufikia duka kwa sasa. Tafadhali jaribu tena baada ya dakika moja.",
    "This store doesn't deliver. Choose collect from the store.": "Duka hili halipeleki. Chagua kuchukua dukani.",
    "Delivery isn't available right now. Choose collect from the store, or try again later.":
        "Huduma ya kupeleka haipatikani kwa sasa. Chagua kuchukua dukani, au jaribu tena baadaye.",
    "That location isn't valid. Try finding it on the map again.": "Mahali hapo si sahihi. Jaribu kupatafuta kwenye ramani tena.",
    "Not enough in stock: {names}. Change your basket and try again.":
        "Hazitoshi dukani: {names}. Badilisha kikapu chako kisha ujaribu tena.",
    "An online order takes up to {n} different products.": "Oda ya mtandaoni inachukua hadi bidhaa {n} tofauti.",
    "Up to {n} of one product per order, please.": "Tafadhali, hadi {n} za bidhaa moja kwa kila oda.",
    "Something in your basket isn't sold online any more. Please remove it.":
        "Kuna bidhaa kwenye kikapu chako ambayo haiuzwi tena mtandaoni. Tafadhali iondoe.",
    "Enter where to deliver.": "Weka mahali pa kupeleka.",
    "Express delivery has closed for today. Choose a delivery time instead.":
        "Huduma ya haraka ya kupeleka imefungwa kwa leo. Chagua muda wa kupeleka badala yake.",
    "That delivery time isn't available any more. Choose another.": "Muda huo wa kupeleka haupatikani tena. Chagua mwingine.",
    "The store couldn't take your order. Please try again.": "Duka halikuweza kupokea oda yako. Tafadhali jaribu tena.",
    "This order has expired and its items were released. Please place it again.":
        "Muda wa oda hii umeisha na bidhaa zake zimeachiliwa. Tafadhali iweke tena.",
    "Items can only be returned unopened and in the same state as when you got them.":
        "Bidhaa zinaweza kurudishwa tu zikiwa hazijafunguliwa na katika hali ile ile uliyozipata.",
    "Too many return requests for this order. Please ask at the store.":
        "Maombi mengi mno ya kurudisha kwa oda hii. Tafadhali uliza dukani.",
    "Please check what you chose to return.": "Tafadhali kagua ulichochagua kurudisha.",
    "The return couldn't be recorded. Please try again, or ask at the store.":
        "Urudishaji haukuweza kurekodiwa. Tafadhali jaribu tena, au uliza dukani.",
    "Thanks: we've had several reports from you in the last hour. Please ask at the store if it's urgent.":
        "Asante: tumepokea ripoti kadhaa kutoka kwako katika saa iliyopita. Tafadhali uliza dukani kama ni dharura.",
    "No such report": "Ripoti hiyo haipo",

    # --- Online shop: accounts and points (apps/storefront/account.py)
    "Accounts aren't available in this shop": "Akaunti hazipatikani katika duka hili",
    "Accounts aren't available right now. You can still shop without signing in.":
        "Akaunti hazipatikani kwa sasa. Bado unaweza kununua bila kuingia.",
    "Sign in to use your points.": "Ingia ili kutumia pointi zako.",
    "Your points can't be used right now. Try again, or order without them.":
        "Pointi zako haziwezi kutumika kwa sasa. Jaribu tena, au agiza bila hizo.",
    "Please sign in again.": "Tafadhali ingia tena.",
    "Please sign in.": "Tafadhali ingia.",
    "Too many tries. Please wait a few minutes.": "Majaribio mengi mno. Tafadhali subiri dakika chache.",
    "That picture is too large.": "Picha hiyo ni kubwa mno.",

    # --- Passed on by the online shop from the services behind it (customers, dispatch, inventory returns)
    "Enter a Kenyan mobile number, e.g. 0712 345 678.": "Weka nambari ya simu ya Kenya, mfano 0712 345 678.",
    "Enter a valid email address.": "Weka barua pepe sahihi.",
    "Loyalty rates must be at least 1": "Viwango vya pointi viwe angalau 1",
    "Enter your name.": "Weka jina lako.",
    "You need to be {age} or over to join.": "Unahitaji kuwa na umri wa miaka {age} au zaidi ili kujiunga.",
    "Check your date of birth.": "Kagua tarehe yako ya kuzaliwa.",
    "Choose female, male, other, or leave it blank.": "Chagua mwanamke, mwanamume, nyingine, au uache wazi.",
    "Please read and accept the privacy notice to join.": "Tafadhali soma na ukubali ilani ya faragha ili kujiunga.",
    "Too many codes sent to this email. Please wait {n} minutes.":
        "Misimbo mingi mno imetumwa kwa barua pepe hii. Tafadhali subiri dakika {n}.",
    "That code isn't right. {n} tries left.": "Msimbo huo si sahihi. Umebakiza majaribio {n}.",
    "That code isn't right. Ask for a new one.": "Msimbo huo si sahihi. Omba mpya.",
    "That code has expired. Ask for a new one.": "Muda wa msimbo huo umeisha. Omba mpya.",
    "Not signed in": "Hujaingia",
    "No such customer": "Mteja huyo hayupo",
    "No such order": "Oda hiyo haipo",
    "Enter your date of birth.": "Weka tarehe yako ya kuzaliwa.",
    "There's already an account with this email. Sign in instead.": "Tayari kuna akaunti yenye barua pepe hii. Ingia badala yake.",
    "That phone number belongs to another account. Sign in with that account's email.":
        "Nambari hiyo ya simu ni ya akaunti nyingine. Ingia kwa barua pepe ya akaunti hiyo.",
    "That phone number belongs to another account.": "Nambari hiyo ya simu ni ya akaunti nyingine.",
    "No account with this email.": "Hakuna akaunti yenye barua pepe hii.",
    "That order belongs to someone else": "Oda hiyo ni ya mtu mwingine",
    "Choose English (en) or Kiswahili (sw).": "Chagua Kiingereza (en) au Kiswahili (sw).",
    "Choose what the problem is about": "Chagua tatizo linahusu nini",
    "Unknown app": "Programu isiyojulikana",
    "Tell us what happened (a few words at least)": "Tueleze kilichotokea (angalau maneno machache)",
    "That order number doesn't look right": "Nambari hiyo ya oda haionekani sahihi",
    "Unknown report status": "Hali ya ripoti isiyojulikana",
    "Tell the customer what was done before marking it resolved":
        "Mweleze mteja kilichofanyika kabla ya kuweka kuwa imetatuliwa",
    "A problem with an order": "Tatizo la oda", "Something in the store": "Kitu fulani dukani",
    "The app isn't working": "Programu haifanyi kazi", "An idea or suggestion": "Wazo au pendekezo",
    "That delivery slot isn't available any more. Choose another.": "Muda huo wa kupeleka haupatikani tena. Chagua mwingine.",
    "STORE_LAT / STORE_LNG aren't a valid location": "STORE_LAT / STORE_LNG si mahali sahihi",
    "Delivery hours should look like 08-20, with slots of at least 1 hour":
        "Saa za kupeleka ziwe kama 08-20, na vipindi vya angalau saa 1",
    "Delivery prices and the distance limit must be positive (express at least 1x)":
        "Bei za kupeleka na kikomo cha umbali viwe zaidi ya sifuri (ya haraka angalau mara 1)",
    "That isn't a valid location": "Hapo si mahali sahihi",
    "No delivery times are left. Please try again later.": "Hakuna muda wa kupeleka uliobaki. Tafadhali jaribu tena baadaye.",
    "No delivery times are left. Choose express or collect.":
        "Hakuna muda wa kupeleka uliobaki. Chagua huduma ya haraka au kuchukua dukani.",
    "Delivery settings: {problem}": "Mipangilio ya kupeleka: {problem}",
    "You're about {km} km from the store. We deliver within {max} km, so this order would need to be collected from the store.":
        "Uko takriban km {km} kutoka dukani. Tunapeleka ndani ya km {max}, kwa hivyo oda hii itahitaji kuchukuliwa dukani.",
    "Only paid orders can be returned": "Oda zilizolipwa tu ndizo zinaweza kurudishwa",
    "The {days}-day return period ended on {date}": "Muda wa kurudisha wa siku {days} uliisha tarehe {date}",
    "RETURN_DAYS can't be negative": "RETURN_DAYS haiwezi kuwa chini ya sifuri",
    "Food and other perishables can't be returned": "Vyakula na bidhaa nyingine zinazoharibika haziwezi kurudishwa",
    "Already returned": "Tayari imerudishwa",
    "Say why the items are coming back": "Sema kwa nini bidhaa zinarudishwa",
    "Items can only be returned unopened and in the same state as when sold":
        "Bidhaa zinaweza kurudishwa tu zikiwa hazijafunguliwa na katika hali ile ile zilipouzwa",
    "Choose what to return": "Chagua unachorudisha",
    "Check the items first: unopened and in the same state as when sold":
        "Kagua bidhaa kwanza: hazijafunguliwa na ziko katika hali ile ile zilipouzwa",
    "This return is already {status}": "Urudishaji huu tayari ni {status}",
    "Say why the return is refused (the customer is told)": "Sema kwa nini urudishaji umekataliwa (mteja ataambiwa)",
    "The refund went back by mpesa, card or cash": "Pesa zilirudishwa kwa mpesa, kadi au taslimu",
    "Enter the M-Pesa or card reference of the refund": "Weka kumbukumbu ya M-Pesa au ya kadi ya pesa zilizorudishwa",
    "No return {id}": "Hakuna urudishaji {id}",
    "Unknown return status": "Hali ya urudishaji isiyojulikana",
    "Quantities must be positive": "Idadi ziwe zaidi ya sifuri",
    "Someone else dealt with this return a moment ago": "Mtu mwingine ameshughulikia urudishaji huu muda mfupi uliopita",
    "This return has already been dealt with": "Urudishaji huu tayari umeshughulikiwa",
    "Approve the return before recording the refund": "Idhinisha urudishaji kabla ya kurekodi pesa zilizorudishwa",
    "This return is {status}": "Urudishaji huu ni {status}",
    "{name} wasn't in this order": "{name} haikuwa kwenye oda hii",
    "Only {n} of {name} can be returned": "Ni {n} tu za {name} zinazoweza kurudishwa",
    "{n}% off": "Punguzo la {n}%",
    "KES {n} off": "Punguzo la KES {n}",
    # Words that are filled in to other messages (a delivery slot's day, why an item can't come back)
    "Today": "Leo", "Tomorrow": "Kesho", "Monday": "Jumatatu", "Tuesday": "Jumanne", "Wednesday": "Jumatano",
    "Thursday": "Alhamisi", "Friday": "Ijumaa", "Saturday": "Jumamosi", "Sunday": "Jumapili",

    # --- Last: the most general patterns, tried after every other one
    "{day} {start}–{end}": "{day} {start}–{end}",  # a delivery slot, "Today 14:00–16:00"
    "{name}: {why}": "{name}: {why}",  # an item that can't be returned, "Sugar: Already returned"
}

CATALOGUES = {"sw": SW}

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _compile(catalogue: Dict[str, str]) -> List[Tuple[Pattern, str]]:
    """The messages with placeholders, as patterns: 'Added {name}' matches 'Added Milk' and keeps 'Milk'."""
    out = []
    for english, translated in catalogue.items():
        if _PLACEHOLDER.search(english):
            parts = _PLACEHOLDER.split(english)  # text, name, text, name, ..., text
            pattern = "".join(re.escape(p) if i % 2 == 0 else f"(?P<{p}>.+?)" for i, p in enumerate(parts))
            out.append((re.compile(f"^{pattern}$", re.S), translated))
    return out


_TEMPLATES = {lang: _compile(c) for lang, c in CATALOGUES.items()}


def language(request: Request) -> str:
    lang = request.headers.get(HEADER, "").strip().lower()[:2]
    return lang if lang in LANGUAGES else "en"


def translate(text: str, lang: str) -> str:
    """The message in this language, or as it is if there's no translation."""
    catalogue = CATALOGUES.get(lang)
    if not catalogue or not isinstance(text, str):
        return text
    if text in catalogue:
        return catalogue[text]
    for pattern, translated in _TEMPLATES[lang]:
        m = pattern.match(text)
        if m:  # the filled-in parts stay as they are, unless they're words in the catalogue ("Today")
            return _PLACEHOLDER.sub(lambda p: catalogue.get(m.group(p.group(1)), m.group(p.group(1))), translated)
    return text


def translate_detail(detail, lang: str):
    """An error's detail: a message, or {"message": ..., ...} with other fields left alone."""
    if isinstance(detail, str):
        return translate(detail, lang)
    if isinstance(detail, dict) and isinstance(detail.get("message"), str):
        return {**detail, "message": translate(detail["message"], lang)}
    return detail


def install(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def translated(request: Request, exc: StarletteHTTPException):
        lang = language(request)
        if lang != "en":
            exc = StarletteHTTPException(exc.status_code, translate_detail(exc.detail, lang), getattr(exc, "headers", None))
        return await http_exception_handler(request, exc)


def missing(messages: List[str], lang: str = "sw") -> List[str]:
    """The messages (English, with {} for filled-in parts) that have no translation: for the tests."""
    known = {_PLACEHOLDER.sub("{}", k) for k in CATALOGUES[lang]}
    return [m for m in messages if _PLACEHOLDER.sub("{}", m) not in known]


def supported(lang: Optional[str]) -> str:
    return lang if lang in LANGUAGES else "en"
