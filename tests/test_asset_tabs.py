import unittest
from html.parser import HTMLParser

from scripts.asset_tabs import build_asset_page


class AssetPageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.iframes = []
        self.tabs = []
        self.panels = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "iframe":
            self.iframes.append(attributes)
        if attributes.get("role") == "tab":
            self.tabs.append(attributes)
        if attributes.get("role") == "tabpanel":
            self.panels.append(attributes)


class BuildAssetPageTest(unittest.TestCase):
    def setUp(self):
        self.gold_html = '''<!doctype html><html lang="zh-CN"><head>
<style>.gold::after { content: "黄金 & 白银"; }</style></head>
<body><h1>黄金</h1><script>window.gold = "</script-safe>";</script></body></html>'''
        self.etf_html = '''<!doctype html><html><head><style>body{width:100%}</style></head>
<body><label>金额<input value="10,000"></label>
<script>window.etf = {name: "纳斯达克", marker: "</script-safe>"};</script></body></html>'''
        self.output = build_asset_page(self.gold_html, self.etf_html)
        self.parser = AssetPageParser()
        self.parser.feed(self.output)

    def test_preserves_complete_child_html_in_srcdoc(self):
        self.assertEqual(2, len(self.parser.iframes))
        self.assertEqual(self.gold_html, self.parser.iframes[0]["srcdoc"])
        self.assertEqual(self.etf_html, self.parser.iframes[1]["srcdoc"])

    def test_escapes_child_markup_out_of_the_outer_document(self):
        before_shell_script = self.output.split("<script>", 1)[0]
        self.assertNotIn('<script>window.gold', before_shell_script)
        self.assertNotIn('<script>window.etf', before_shell_script)
        self.assertIn('&lt;/script-safe&gt;', before_shell_script)
        self.assertIn('黄金 &amp; 白银', before_shell_script)

    def test_builds_two_linked_aria_tabs_and_panels(self):
        self.assertEqual(["tab-gold", "tab-us-etf"], [tab["id"] for tab in self.parser.tabs])
        self.assertEqual(["true", "false"], [tab["aria-selected"] for tab in self.parser.tabs])
        self.assertEqual(["panel-gold", "panel-us-etf"], [panel["id"] for panel in self.parser.panels])
        self.assertEqual(
            ["panel-gold", "panel-us-etf"],
            [tab["aria-controls"] for tab in self.parser.tabs],
        )
        self.assertEqual(
            ["tab-gold", "tab-us-etf"],
            [panel["aria-labelledby"] for panel in self.parser.panels],
        )
        self.assertNotIn("hidden", self.parser.panels[0])
        self.assertIn("hidden", self.parser.panels[1])

    def test_contains_deep_links_keyboard_and_resize_support(self):
        for token in (
            'const ids = ["gold", "us-etf"]',
            'location.hash.slice(1)',
            'event.key === "ArrowRight"',
            'event.key === "ArrowLeft"',
            'event.key === "Home"',
            'event.key === "End"',
            'doc.addEventListener("change", refit)',
            'doc.addEventListener("toggle", refit, true)',
            'doc.body.getBoundingClientRect().height',
            'panel.hidden',
            'position: sticky',
            'scrolling="no"',
        ):
            self.assertIn(token, self.output)


if __name__ == "__main__":
    unittest.main()
