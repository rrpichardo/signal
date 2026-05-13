"""Tests for the html_scrape source kind added in Wave 5."""
from __future__ import annotations

import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from signal_stream.models import SourceConfig
from signal_stream.source_tools import fetch_source, _load_html_scrape_source


def _make_html_scrape_source(**kwargs) -> SourceConfig:
    defaults = dict(
        name="Test Scrape",
        kind="html_scrape",
        url="https://example.com/archive",
        article_link_pattern="/p/",
        limit=5,
        enabled=True,
    )
    defaults.update(kwargs)
    return SourceConfig(**defaults)


def _fake_urlopen_factory(archive_html: str, article_html: str):
    """Return a context-manager mock that serves archive_html on the first call
    and article_html on all subsequent calls (for individual article fetches)."""
    call_count = [0]

    class _FakeResponse:
        def __init__(self, html: str):
            self._data = html.encode("utf-8")
            self.headers = MagicMock()
            self.headers.get = lambda key, default="": "text/html" if key == "Content-Type" else default

        def read(self, size=None):
            return self._data if size is None else self._data[:size]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    def fake_urlopen(req, timeout=None):
        call_count[0] += 1
        # First call = archive page; subsequent = article pages
        return _FakeResponse(archive_html if call_count[0] == 1 else article_html)

    return fake_urlopen


ARCHIVE_HTML = """
<html>
<body>
  <a href="/p/article-one">Article One</a>
  <a href="/p/article-two">Article Two</a>
  <a href="/p/article-three">Article Three</a>
  <a href="/about">About</a>
</body>
</html>
"""

ARTICLE_HTML = """
<html>
<head>
  <title>Article Title</title>
  <meta property="og:image" content="https://example.com/img.jpg">
</head>
<body>
  <article>
    <p>This is the article body content which is long enough to exceed the 200 character minimum threshold
    needed for the full page extraction to be accepted instead of falling back to the RSS body text content.</p>
  </article>
</body>
</html>
"""


def test_html_scrape_finds_article_links():
    """html_scrape should parse the archive page and return up to limit articles."""
    fake = _fake_urlopen_factory(ARCHIVE_HTML, ARTICLE_HTML)
    with patch("signal_stream.source_tools.request.urlopen", fake), \
         patch("signal_stream.source_tools.request.Request", side_effect=lambda url, headers=None: url):
        source = _make_html_scrape_source(limit=3)
        articles = _load_html_scrape_source(source)

    # 3 /p/ links found, limit=3, so we get 3 articles
    assert len(articles) == 3
    for art in articles:
        assert "/p/" in art.url


def test_html_scrape_fetches_each_article():
    """Each article link should produce an Article with non-empty body."""
    fake = _fake_urlopen_factory(ARCHIVE_HTML, ARTICLE_HTML)
    with patch("signal_stream.source_tools.request.urlopen", fake), \
         patch("signal_stream.source_tools.request.Request", side_effect=lambda url, headers=None: url):
        source = _make_html_scrape_source(limit=2)
        articles = _load_html_scrape_source(source)

    assert len(articles) == 2
    # Article body should contain the extracted text (>200 chars passes full-page threshold)
    for art in articles:
        assert len(art.body) > 0


def test_html_scrape_handles_404_gracefully():
    """If the archive page fetch fails, fetch_source returns status='error', not a crash."""
    def failing_urlopen(req, timeout=None):
        raise OSError("connection refused")

    with patch("signal_stream.source_tools.request.urlopen", failing_urlopen), \
         patch("signal_stream.source_tools.request.Request", side_effect=lambda url, headers=None: url):
        source = _make_html_scrape_source()
        result = fetch_source(source)

    assert result["status"] == "error"
    assert result["articles"] == []


def test_html_scrape_respects_limit():
    """fetch_source with html_scrape should honour source.limit."""
    # Archive has 3 links but limit=2
    fake = _fake_urlopen_factory(ARCHIVE_HTML, ARTICLE_HTML)
    with patch("signal_stream.source_tools.request.urlopen", fake), \
         patch("signal_stream.source_tools.request.Request", side_effect=lambda url, headers=None: url):
        source = _make_html_scrape_source(limit=2)
        articles = _load_html_scrape_source(source)

    assert len(articles) == 2


def test_html_scrape_article_link_pattern_filters_correctly():
    """Only hrefs containing article_link_pattern should be returned."""
    fake = _fake_urlopen_factory(ARCHIVE_HTML, ARTICLE_HTML)
    with patch("signal_stream.source_tools.request.urlopen", fake), \
         patch("signal_stream.source_tools.request.Request", side_effect=lambda url, headers=None: url):
        # Pattern that matches nothing
        source = _make_html_scrape_source(article_link_pattern="/nomatch/", limit=10)
        articles = _load_html_scrape_source(source)

    assert articles == []
