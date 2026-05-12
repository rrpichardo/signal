"""Tests for Wave 5 image extraction from RSS feeds and article pages."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from signal_stream.source_tools import _entry_image, _ArticlePageParser


# ---------------------------------------------------------------------------
# _entry_image — RSS/Atom feed image extraction
# ---------------------------------------------------------------------------

def _make_entry(xml_str: str) -> ET.Element:
    """Parse a minimal RSS item XML fragment for testing."""
    return ET.fromstring(f'<item xmlns:media="http://search.yahoo.com/mrss/">{xml_str}</item>')


def test_rss_media_thumbnail_extracted():
    """media:thumbnail should be the preferred image source."""
    entry = _make_entry('<media:thumbnail url="https://example.com/thumb.jpg" />')
    assert _entry_image(entry) == "https://example.com/thumb.jpg"


def test_rss_media_content_url_extracted():
    """media:content with a url attribute should be returned."""
    entry = _make_entry('<media:content url="https://example.com/content.png" medium="image"/>')
    assert _entry_image(entry) == "https://example.com/content.png"


def test_rss_enclosure_image_extracted():
    """<enclosure> with an image type should be extracted."""
    entry = _make_entry('<enclosure url="https://example.com/audio.jpg" type="image/jpeg" />')
    assert _entry_image(entry) == "https://example.com/audio.jpg"


def test_rss_enclosure_non_image_type_not_matched_by_type_but_by_name():
    """<enclosure> without image type but local name 'enclosure' is still matched via name check."""
    entry = _make_entry('<enclosure url="https://example.com/pod.mp3" type="audio/mpeg" />')
    # local name is "enclosure" so the name-based branch matches first
    result = _entry_image(entry)
    assert result == "https://example.com/pod.mp3"


def test_rss_no_image_returns_empty():
    """When no media tags exist, _entry_image returns an empty string."""
    entry = _make_entry("<title>Some article</title><link>https://example.com/art</link>")
    assert _entry_image(entry) == ""


def test_og_image_extracted_from_full_page():
    """_ArticlePageParser should extract og:image from meta tags."""
    html = """
    <html>
    <head>
      <meta property="og:image" content="https://example.com/og.jpg">
    </head>
    <body><article><p>Body text here with enough content to pass the min-char threshold.</p></article></body>
    </html>
    """
    parser = _ArticlePageParser()
    parser.feed(html)
    assert parser.og_image == "https://example.com/og.jpg"


def test_no_image_assigns_icon_key():
    """When no image_url is present, analysis_tools._icon_key returns a non-empty icon key."""
    from signal_stream.analysis_tools import _icon_key

    # Event-type path
    assert _icon_key("platform_shift") == "platform"
    assert _icon_key("regulatory_risk") == "risk"
    assert _icon_key("unknown_type") == "signal"


def test_company_entity_overrides_event_type_icon():
    """NVIDIA in entities should return 'chip' regardless of event_type."""
    from signal_stream.analysis_tools import _icon_key

    entities = {"competitors": ["NVIDIA", "AMD"]}
    assert _icon_key("general_signal", entities=entities) == "chip"


def test_anthropic_entity_returns_claude_icon():
    """Anthropic in competitors should map to the 'claude' icon."""
    from signal_stream.analysis_tools import _icon_key

    entities = {"competitors": ["Anthropic"]}
    assert _icon_key("general_signal", entities=entities) == "claude"
