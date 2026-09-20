import pytest

from pia.normalize import canonicalize_url


def test_strips_tracking_params_and_fragment():
    url = "https://example.com/post?utm_source=x&utm_medium=y&fbclid=abc#section"
    assert canonicalize_url(url) == "https://example.com/post"


def test_keeps_meaningful_query_params_sorted():
    a = canonicalize_url("https://example.com/watch?v=1&list=2")
    b = canonicalize_url("https://example.com/watch?list=2&v=1")
    assert a == b == "https://example.com/watch?list=2&v=1"


def test_forces_https_lowercases_host_and_drops_www_and_default_port():
    assert canonicalize_url("HTTP://WWW.Example.COM:443/Path") == "https://example.com/Path"


def test_drops_trailing_slash_but_keeps_root():
    assert canonicalize_url("https://example.com/post/") == "https://example.com/post"
    assert canonicalize_url("https://example.com/") == "https://example.com"


def test_arxiv_versions_and_pdf_links_collapse_to_one_abs_url():
    expected = "https://arxiv.org/abs/2401.12345"
    assert canonicalize_url("http://arxiv.org/abs/2401.12345v3") == expected
    assert canonicalize_url("https://arxiv.org/pdf/2401.12345v2.pdf") == expected
    assert canonicalize_url("https://arxiv.org/abs/2401.12345") == expected


def test_arxiv_old_style_ids():
    assert (
        canonicalize_url("https://arxiv.org/abs/hep-th/9901001v1")
        == "https://arxiv.org/abs/hep-th/9901001"
    )


def test_is_idempotent():
    url = "http://www.arxiv.org/pdf/2401.12345v2.pdf?utm_source=x"
    once = canonicalize_url(url)
    assert canonicalize_url(once) == once


@pytest.mark.parametrize("bad", ["", "   ", "not a url", "ftp://example.com/x", "https://"])
def test_rejects_unusable_urls(bad):
    with pytest.raises(ValueError):
        canonicalize_url(bad)
