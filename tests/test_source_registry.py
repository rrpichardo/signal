import unittest
from signal_stream.source_registry import generate_source_id, SourceRecord, source_config_to_record, source_record_to_config


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


class TestConversionHelpers(unittest.TestCase):
    def _make_source_config(self, **kwargs):
        """Returns a minimal SourceConfig-compatible object."""
        from signal_stream.models import SourceConfig
        # Build defaults from the SourceConfig dataclass signature.
        defaults = dict(
            name="Test Feed", kind="rss", group="medium",
            url="https://example.com/feed", limit=8, enabled=True,
        )
        defaults.update(kwargs)
        return SourceConfig(**defaults)

    def test_round_trip_preserves_key_fields(self):
        """Verify that converting config->record->config preserves key fields."""
        config = self._make_source_config()
        record = source_config_to_record(config)
        back = source_record_to_config(record)
        self.assertEqual(back.name, config.name)
        self.assertEqual(back.kind, config.kind)
        self.assertEqual(back.url, config.url)
        self.assertEqual(back.limit, config.limit)
        self.assertEqual(back.enabled, config.enabled)

    def test_optional_fields_default_to_none(self):
        """Verify that optional fields default to None when not specified."""
        config = self._make_source_config()  # no channel_id, path, article_link_pattern
        record = source_config_to_record(config)
        self.assertIsNone(record.channel_id)
        self.assertIsNone(record.path)
        self.assertIsNone(record.article_link_pattern)


if __name__ == "__main__":
    unittest.main()
