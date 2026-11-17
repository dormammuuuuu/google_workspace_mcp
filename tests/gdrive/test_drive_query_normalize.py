"""normalize_drive_query: function-style contains(...) that Drive rejects with HTTP 400 is rewritten."""
import pytest
from unittest.mock import Mock

from gdrive.drive_helpers import normalize_drive_query
from gdrive.drive_tools import search_drive_files


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.mark.parametrize(
    "query, expected",
    [
        ("trashed = false and (contains(name,'Wachusett'))", "trashed = false and (name contains 'Wachusett')"),
        ("contains(name, 'A') or contains(name,'B')", "name contains 'A' or name contains 'B'"),
        ("CONTAINS( fullText , 'invoice' )", "fullText contains 'invoice'"),
        ("contains(title,'X')", "name contains 'X'"),
        ('contains(name,"O\'Brien")', "name contains 'O\\'Brien'"),
        ("mimeType = 'application/vnd.google-apps.folder' and (contains(name,'Canton'))",
         "mimeType = 'application/vnd.google-apps.folder' and (name contains 'Canton')"),
    ],
)
def test_function_style_contains_is_rewritten(query, expected):
    assert normalize_drive_query(query) == expected


@pytest.mark.parametrize(
    "query",
    ["budget", "name contains 'Budget'", "fullText contains 'contains(name,x)'", "'abc' in parents", ""],
)
def test_valid_or_free_text_queries_are_unchanged(query):
    assert normalize_drive_query(query) == query


@pytest.mark.asyncio
async def test_search_drive_files_sends_normalized_query():
    service = Mock()
    service.files().list().execute.return_value = {"files": []}
    await _unwrap(search_drive_files)(
        service=service, user_google_email="user@example.com", query="contains(name,'Wachusett')"
    )
    q = service.files.return_value.list.call_args.kwargs["q"]
    assert "name contains 'Wachusett'" in q
    assert "contains(name" not in q
