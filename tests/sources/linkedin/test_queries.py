from __future__ import annotations

from visa_jobs_api.sources.linkedin.queries import DEFAULT_KEYWORDS, build_search_queries


def test_build_search_queries_uses_default_keywords_for_every_country() -> None:
    queries = build_search_queries()

    assert len(queries) > 1
    assert all(query.keywords == DEFAULT_KEYWORDS for query in queries)
    assert len({query.location for query in queries}) == len(queries)


def test_build_search_queries_applies_custom_keywords_to_every_country() -> None:
    queries = build_search_queries("(Rust OR Go) AND sponsor")

    assert all(query.keywords == "(Rust OR Go) AND sponsor" for query in queries)
