from __future__ import annotations

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from src.gpt_feed_bridge import CONTENT_NS, parse_datetime, parse_feed, run


FIXTURES = Path(__file__).parent / "fixtures"


class FeedBridgeTests(unittest.TestCase):
    def test_parses_rss_and_strips_html(self) -> None:
        xml_text = (FIXTURES / "sample-rss.xml").read_text(encoding="utf-8")
        entries = parse_feed(xml_text, "測試來源", "sample-rss.xml")
        self.assertEqual(len(entries), 1)
        self.assertIn("personal knowledge systems", entries[0].title)
        self.assertIn("separates collection", entries[0].content)
        self.assertNotIn("<p>", entries[0].content)

    def test_parses_atom_and_iso_datetime(self) -> None:
        xml_text = (FIXTURES / "sample-atom.xml").read_text(encoding="utf-8")
        entries = parse_feed(xml_text, "Atom 測試來源", "sample-atom.xml")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].author, "Atom Author")
        self.assertIn("support attention", entries[0].content)
        self.assertEqual(parse_datetime(entries[0].published).year, 2026)

    def test_mock_run_creates_valid_feed_and_deduplicates(self) -> None:
        base_config = json.loads((FIXTURES / "config.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source = temp / "sample-rss.xml"
            source.write_text((FIXTURES / "sample-rss.xml").read_text(encoding="utf-8"), encoding="utf-8")
            base_config["sources"][0]["url"] = "sample-rss.xml"
            config_path = temp / "config.json"
            config_path.write_text(json.dumps(base_config, ensure_ascii=False), encoding="utf-8")

            first = run(config_path, mock_ai=True)
            second = run(config_path, mock_ai=True)

            self.assertEqual(first["new_items"], 1)
            self.assertEqual(second["new_items"], 0)
            feed_path = temp / "work" / "feed.xml"
            root = ET.fromstring(feed_path.read_text(encoding="utf-8"))
            items = root.findall("./channel/item")
            self.assertEqual(len(items), 1)
            self.assertIn("GPT 精讀", items[0].findtext("title", ""))
            self.assertTrue(items[0].findtext(f"{{{CONTENT_NS}}}encoded"))


if __name__ == "__main__":
    unittest.main()
