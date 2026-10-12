"""Every text in the online shop that is marked for translation has its Kiswahili (the SW table in the page)."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "storefront" / "static" / "index.html"
STR = r'"((?:[^"\\]|\\.)*)"'


def table(html: str) -> dict:
    body = html.split("const SW = {", 1)[1].split("\n};", 1)[0]
    return {json.loads(f'"{k}"'): json.loads(f'"{v}"') for k, v in re.findall(STR + r"\s*:\s*" + STR, body)}


def marked(html: str) -> set:
    """Text of elements marked data-t, and attributes marked data-t-placeholder / -aria-label / -alt."""
    found = set()

    class P(HTMLParser):
        stack = []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            for attr in ("placeholder", "aria-label", "alt", "title"):
                if f"data-t-{attr}" in a:
                    found.add(a[attr])
            if tag not in ("input", "img", "br", "meta", "link"):
                self.stack.append("data-t" in a)

        def handle_endtag(self, tag):
            if tag not in ("input", "img", "br", "meta", "link") and self.stack:
                self.stack.pop()

        def handle_data(self, data):
            if self.stack and self.stack[-1] and data.strip():
                found.add(data)

    P().feed(html.split("<script>")[0])
    # data-t-html: the element's own markup is the key (bold labels inside a sentence)
    found |= {m.strip() for m in re.findall(r"<(\w+)[^>]*\bdata-t-html\b[^>]*>(.*?)</\1>", html.split("<script>")[0], re.S)
              for m in [m[1]]}
    return found


def in_script(html: str) -> set:
    script = html.split("<script>")[1].split("const SW = {")[0] + html.split("const SW = {")[1].split("\n};", 1)[1]
    found = set(re.findall(r"\bt\(" + STR, script))
    found |= {s for pair in re.findall(r"\bt\([\w.]+ \? " + STR + r"\s*:\s*" + STR, script) for s in pair}
    found |= {s for pair in re.findall(r"\bt\(\w+ \? " + STR + r"\s*:\s*\w+[^?]*\? " + STR + r"\s*:\s*" + STR, script)
              for s in pair}  # t(a ? "x" : b ? "y" : "z")
    for table in ("LABELS", "REPORT_STATUS"):
        found |= set(re.findall(r"\w+: " + STR, script.split(f"const {table} = {{")[1].split("}")[0]))
    found |= set(re.findall(r'\["\w+", ' + STR + r"\]", script.split("const STEPS = {")[1].split("};")[0]))
    for block in re.findall(r"\bt\(\{(.*?)\}\[", script, re.S):  # t({stage: "text", ...}[stage])
        found |= set(re.findall(r"\w+: " + STR, block))
    return {json.loads(f'"{s}"') for s in found}


def test_every_marked_text_has_kiswahili():
    html = PAGE.read_text()
    sw = table(html)
    texts = marked(html) | in_script(html)
    assert len(texts) > 80
    assert sorted(t for t in texts if t not in sw) == []


def test_translations_keep_their_placeholders():
    for en, sw in table(PAGE.read_text()).items():
        assert sorted(re.findall(r"\{\w+\}", en)) == sorted(re.findall(r"\{\w+\}", sw)), en


def test_every_kiswahili_entry_is_used():
    """A key that matches nothing on the page (a typo, or text since reworded) would leave that text in English."""
    import html as h
    page = PAGE.read_text()
    rest = h.unescape(page.split("const SW = {")[0] + page.split("const SW = {")[1].split("\n};", 1)[1])
    unused = [k for k in table(page) if f'"{k}"' not in rest and k not in rest]
    assert unused == []
