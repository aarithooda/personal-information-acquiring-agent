from pathlib import Path

import pytest

from pia.sources.registry import load_sources

DEFAULT_CONFIG = Path(__file__).parent.parent / "config" / "sources.toml"


def write(tmp_path, text: str) -> Path:
    path = tmp_path / "sources.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_builds_sources_from_config(tmp_path):
    path = write(
        tmp_path,
        """
        [[source]]
        type = "arxiv"
        categories = ["cs.AI"]

        [[source]]
        type = "hn"
        min_points = 200

        [[source]]
        type = "rss"
        name = "quanta"
        url = "https://www.quantamagazine.org/feed/"
        """,
    )
    sources = load_sources(path)
    assert [s.name for s in sources] == ["arxiv", "hn", "quanta"]
    assert sources[1].min_points == 200


def test_unknown_source_type_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="unknown source type 'twitter'"):
        load_sources(write(tmp_path, '[[source]]\ntype = "twitter"\n'))


def test_bad_parameters_are_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="hn"):
        load_sources(write(tmp_path, '[[source]]\ntype = "hn"\nmin_pointz = 5\n'))


def test_duplicate_source_names_are_rejected(tmp_path):
    text = '[[source]]\ntype = "rss"\nname = "a"\nurl = "https://x.test/1"\n' * 2
    with pytest.raises(ValueError, match="duplicate"):
        load_sources(write(tmp_path, text))


def test_the_shipped_default_config_loads():
    sources = load_sources(DEFAULT_CONFIG)
    assert len(sources) >= 5
    assert len({s.name for s in sources}) == len(sources)
