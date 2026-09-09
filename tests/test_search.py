from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlencode

import app
from search_index import SearchIndex, parse_query
from test_app import DemoServerTestCase


def record(number=0, **overrides):
    return {"id": f"claude:search-{number:04}", "source": "claude", "source_label": "Claude",
            "title": "Planning", "project": "Atlas", "cwd": "/example/atlas", "customer": "",
            "excerpt": "Launch notes", "updated_at": "2026-07-01T10:00:00Z", **overrides}


class LocalSearchTest(unittest.TestCase):
    def test_late_turn_and_chinese_phrase_are_searchable_in_both_views(self):
        text = "opening " * 80000 + "……发布计划 office预算 " + "end " * 100
        index = SearchIndex({"records": [record(excerpt=text)]})
        result = index.search({"q": '"发布计划" "office预算"'})
        self.assertEqual(result["total"], 1)
        preview = result["records"][0]
        self.assertIn("发布计划", preview["excerpt"])
        self.assertGreater(preview["excerpt_offset"], 600000)
        self.assertLessEqual(len(preview["excerpt"]), 822)
        self.assertEqual(index.search({"q": "发布计划", "view": "projects"})["total"], 1)
        self.assertEqual(index.records[0]["excerpt"], text)

    def test_normalized_unicode_phrase_and_snippet_agree(self):
        for raw, query in [("ｒｅｌｅａｓｅ 计划", '"release 计划"'), ("oﬃce", "office"), ("面积 ㎡", "m2"), ("cafe\u0301", "café")]:
            with self.subTest(query=query):
                index = SearchIndex({"records": [record(excerpt="…… " * 2000 + raw + " tail " * 100)]})
                result = index.search({"q": query})
                self.assertEqual(result["total"], 1)
                self.assertIn(raw, result["records"][0]["excerpt"])

    def test_every_query_term_is_required_and_source_quotes_stay_literal(self):
        terms, sources = parse_query('one two three four five six seven eight nine "source:literal" source:Claude')
        self.assertEqual(len(terms), 10)
        self.assertEqual(sources, ["claude"])
        index = SearchIndex({"records": [record(excerpt="one two three four five six seven eight")]})
        self.assertEqual(index.search({"q": "one two three four five six seven eight nine"})["total"], 0)

    def test_source_time_and_project_filters_combine(self):
        recent = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        index = SearchIndex({"records": [record(0, updated_at=recent), record(1, source="codex", source_label="Codex", updated_at=recent), record(2, updated_at=old), record(3, project="Elsewhere", updated_at=recent)]})
        result = index.search({"source": "claude,codex", "q": "source:claude", "range": "30", "project": "Atlas"})
        self.assertEqual([r["id"] for r in result["records"]], ["claude:search-0000"])

    def test_title_match_outranks_repeated_body_and_recency_sorts_work(self):
        index = SearchIndex({"records": [record(0, title="orchid"), record(1, excerpt="orchid " * 5000, updated_at="2026-08-01T10:00:00Z")]})
        self.assertEqual(index.search({"q": "orchid"})["records"][0]["id"], "claude:search-0000")
        self.assertEqual(index.search({"sort": "recent"})["records"][0]["id"], "claude:search-0001")
        self.assertEqual(index.search({"sort": "oldest"})["records"][0]["id"], "claude:search-0000")

    def test_session_paging_has_no_duplicates_and_does_not_return_full_text(self):
        index = SearchIndex({"records": [record(n, excerpt="x" * 250000, notes="private-notes" * 1000, artifacts=[str(i) for i in range(100)]) for n in range(75)]})
        ids, offset = [], 0
        while offset is not None:
            page = index.search({"offset": str(offset)})
            self.assertLessEqual(len(page["records"]), 30)
            for item in page["records"]:
                self.assertNotIn("notes", item)
                self.assertLessEqual(len(item["excerpt"]), 822)
                self.assertEqual(len(item["artifacts"]), 3)
                ids.append(item["id"])
            offset = page["next_offset"]
        self.assertEqual(len(ids), 75)
        self.assertEqual(len(set(ids)), 75)
        self.assertIsNone(index._folded)  # Empty first page needs no full-text folding.

    def test_project_counts_and_expansion_cover_all_matching_sessions(self):
        records = [record(n) for n in range(70)] + [record(71, project="Beta"), record(72, project="")]
        index = SearchIndex({"records": records})
        first = index.search({"view": "projects", "limit": "1"})
        group = first["groups"][0]
        self.assertEqual(first["total_records"], 72)
        self.assertEqual(first["total_projects"], 3)
        self.assertEqual(group["total"], 70)
        self.assertEqual(len(group["records"]), 5)
        self.assertNotIn("excerpt", group["records"][0])
        rest = index.search({"project": "Atlas", "offset": "5", "limit": "60"})
        tail = index.search({"project": "Atlas", "offset": str(rest["next_offset"])})
        self.assertEqual(len({r["id"] for r in group["records"] + rest["records"] + tail["records"]}), 70)
        self.assertEqual(index.search({"view": "projects", "offset": "2"})["groups"][0]["project"], "")
        self.assertEqual(index.search({"project": ""})["total"], 1)

    def test_detail_reassembles_exact_unicode_text_and_edit_preserves_notes(self):
        raw = "中🙂e\u0301<hello>" * 5000
        index = SearchIndex({"records": [record(excerpt=raw, manual=True, notes=raw)]})
        parts, offset = [], 0
        while offset is not None:
            page = index.detail({"id": "claude:search-0000", "offset": str(offset)})
            self.assertLessEqual(len(page["text"]), 12000)
            self.assertNotIn("notes", page["record"])
            parts.append(page["text"])
            offset = page["next_offset"]
        self.assertEqual("".join(parts), raw)
        self.assertEqual(index.detail({"id": "claude:search-0000", "edit": "1"})["record"]["notes"], raw)

    def test_bad_parameters_do_not_disable_bounds(self):
        index = SearchIndex({"records": [record()]})
        for params in ({"limit": "100000"}, {"offset": "-1"}, {"limit": "0"}, {"offset": "NaN"}, {"q": "x" * 4097}, {"view": "unknown"}, {"range": "yesterday"}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                index.search(params)
        with self.assertRaises(ValueError):
            index.detail({"id": "claude:search-0000", "limit": "999999"})
        with self.assertRaises(KeyError):
            index.detail({"id": "missing"})

    def test_cached_query_does_not_survive_an_index_revision(self):
        old = SearchIndex({"records": [record()]})
        self.assertEqual(old.search({"q": "replacement"})["total"], 0)
        new = SearchIndex({"records": [record(title="replacement", project="Renamed")]})
        self.assertNotEqual(old.revision, new.revision)
        self.assertEqual(new.search({"q": "replacement"})["total"], 1)
        self.assertEqual(new.metadata()["projects"], ["Renamed"])


class SearchHTTPTest(DemoServerTestCase):
    def test_bootstrap_has_no_records_and_revision_is_stable(self):
        _, meta = self.read_json("/api/index")
        self.assertNotIn("records", meta)
        self.assertEqual(meta["version"], app.APP_VERSION)
        _, page = self.read_json("/api/search?" + urlencode({"q": "Atlas", "limit": 2, "revision": meta["revision"]}))
        self.assertEqual(len(page["records"]), 2)
        self.assertEqual(page["revision"], meta["revision"])
        self.assertGreater(page["total"], 2)
        _, detail = self.read_json("/api/record?" + urlencode({"id": page["records"][0]["id"], "revision": meta["revision"]}))
        self.assertEqual(detail["revision"], meta["revision"])

    def test_new_endpoints_enforce_existing_local_boundary(self):
        for path in ("/api/search", "/api/record?id=demo"):
            self.assertEqual(self.raw_request("GET", path, with_token=False)[0], 403)
            self.assertEqual(self.raw_request("GET", path, host="evil.example.com")[0], 403)

    def test_stale_revision_invalid_query_and_missing_record_have_explicit_errors(self):
        for path, status, code in (("/api/search?revision=old", 409, "stale_index"), ("/api/record?revision=old", 409, "stale_index"), ("/api/search?limit=999999", 400, "invalid_query"), ("/api/record?id=missing", 404, "record_missing")):
            actual, _, body = self.raw_request("GET", path)
            self.assertEqual(actual, status)
            self.assertEqual(self.error_code(body), code)

    def test_refresh_replaces_search_snapshot(self):
        with patch.object(app, "DEMO_MODE", False), patch.object(app, "load_index_payload", return_value={"records": [record()]}):
            _, old = self.read_json("/api/search")
        with patch.object(app, "DEMO_MODE", False), patch.object(app, "load_index_payload", return_value={"records": [record(title="Changed")]}):
            actual, _, _ = self.raw_request("GET", "/api/search?revision=" + old["revision"])
            self.assertEqual(actual, 409)
            _, new = self.read_json("/api/search?q=Changed")
            self.assertEqual(new["total"], 1)


if __name__ == "__main__":
    unittest.main()
