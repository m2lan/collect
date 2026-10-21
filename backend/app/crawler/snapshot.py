"""生成可在前端 iframe 中安全展示的页面快照，用于可视化选取器。"""

from lxml import etree, html

REMOVE = {"script", "noscript", "iframe", "object", "embed", "frame", "frameset"}
LAZY_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-url")


def sanitize_snapshot(source: str, base_url: str) -> str:
    doc = html.document_fromstring(source)
    for el in list(doc.iter()):
        if not isinstance(el.tag, str):
            continue
        if el.tag in REMOVE or (el.tag == "meta" and (el.get("http-equiv") or "").lower() == "refresh"):
            el.drop_tree()
            continue
        for attr in list(el.attrib):
            if attr.startswith("on") or (attr in ("href", "src", "action") and el.attrib[attr].strip().lower().startswith("javascript:")):
                del el.attrib[attr]
        if el.tag == "img":
            lazy = next((el.get(a) for a in LAZY_ATTRS if el.get(a)), None)
            if lazy and (not el.get("src") or el.get("src", "").startswith("data:")):
                el.set("src", lazy)
        if el.tag == "base":
            el.drop_tree()
    head = doc.find("head")
    if head is None:
        head = etree.Element("head")
        doc.insert(0, head)
    base = etree.Element("base", href=base_url)
    head.insert(0, base)
    head.insert(0, etree.Element("meta", charset="utf-8"))
    return "<!DOCTYPE html>\n" + etree.tostring(doc, encoding="unicode", method="html")
