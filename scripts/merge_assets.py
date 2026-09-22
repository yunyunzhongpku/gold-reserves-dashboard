"""Combine the existing gold page with a reviewed, self-contained ETF snapshot."""
from __future__ import annotations

from collections import Counter
from datetime import date
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import tempfile

if __package__:
    from .asset_tabs import build_asset_page
else:
    from asset_tabs import build_asset_page


class _SnapshotParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.count = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "tracking-data":
            self.inside = True
            self.count += 1

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)


def read_etf_html(path, *, today=None):
    """Reject incomplete or future-dated snapshots before replacing either page."""
    html = Path(path).read_text(encoding="utf-8")
    parser = _SnapshotParser()
    parser.feed(html)
    if parser.count != 1:
        raise ValueError("ETF page must contain exactly one tracking-data snapshot")
    try:
        snapshot = json.loads("".join(parser.parts))
        meta = snapshot["meta"]
        if meta["schema_version"] != 1:
            raise ValueError("Unsupported ETF snapshot version")
        if date.fromisoformat(meta["asof"]) > (today or date.today()):
            raise ValueError("ETF snapshot has a future date")
        indices = snapshot["indices"]
        if len(indices) != 2 or {item["group"] for item in indices} != {"纳指100", "标普500"}:
            raise ValueError("ETF index coverage is incomplete")
        etfs = snapshot["etfs"]
        codes = [item["code"] for item in etfs]
        if len(codes) != 16 or len(set(codes)) != 16 or not all(codes):
            raise ValueError("ETF snapshot must contain 16 unique fund codes")
        if Counter(item["group"] for item in etfs) != {"纳指100": 12, "标普500": 4}:
            raise ValueError("ETF group coverage does not match the reviewed universe")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("ETF snapshot is missing required fields") from exc
    return html


def _prepare_file(target, content):
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".asset-", delete=False) as stream:
        stream.write(content)
        return Path(stream.name)


def write_merged_site(gold_html, etf_input, output, *, etf_source=None):
    """Prepare both outputs first; restore their previous bytes on a write failure."""
    etf_input, output = Path(etf_input), Path(output)
    etf_html = read_etf_html(etf_source if etf_source is not None else etf_input)
    merged = build_asset_page(gold_html, etf_html)
    writes = [(output, merged.encode("utf-8"))]
    if etf_source is not None:
        writes.insert(0, (etf_input, etf_html.encode("utf-8")))
    prepared, installed, previous = [], [], {}
    try:
        for target, content in writes:
            previous[target] = target.read_bytes() if target.exists() else None
            prepared.append((_prepare_file(target, content), target))
        for temporary, target in prepared:
            os.replace(temporary, target)
            installed.append(target)
    except BaseException:
        for target in reversed(installed):
            old = previous[target]
            if old is None:
                target.unlink(missing_ok=True)
            else:
                restored = _prepare_file(target, old)
                try:
                    os.replace(restored, target)
                finally:
                    restored.unlink(missing_ok=True)
        raise
    finally:
        for temporary, _ in prepared:
            temporary.unlink(missing_ok=True)
    return merged
