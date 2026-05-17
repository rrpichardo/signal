import unittest
from signal_stream.source_registry import generate_source_id, SourceRecord


class TestGenerateSourceId(unittest.TestCase):
    def test_same_inputs_produce_same_id(self):
        """Verify that identical inputs always produce identical IDs."""
        id1 = generate_source_id("toml", "Towards AI", "rss", "https://pub.towardsai.net/feed", None, None)
        id2 = generate_source_id("toml", "Towards AI", "rss", "https://pub.towardsai.net/feed", None, None)
        self.assertEqual(id1, id2)

    def test_different_url_produces_different_id(self):
        """Verify that different URLs produce different IDs."""
        id1 = generate_source_id("toml", "Feed", "rss", "https://a.com/feed", None, None)
        id2 = generate_source_id("toml", "Feed", "rss", "https://b.com/feed", None, None)
        self.assertNotEqual(id1, id2)

    def test_id_has_src_prefix(self):
        """Verify that all source IDs start with 'src_'."""
        sid = generate_source_id("toml", "X", "rss", "https://x.com", None, None)
        self.assertTrue(sid.startswith("src_"))

    def test_none_url_and_path_handled(self):
        """Verify that None values for optional fields don't break ID generation."""
        sid = generate_source_id("toml", "Local", "sample", None, "/data/file.json", None)
        self.assertIsInstance(sid, str)
        self.assertTrue(sid.startswith("src_"))


if __name__ == "__main__":
    unittest.main()
