import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from baomoi_fast import ArticleStore, ListingHTML, save_result
from crawl_baomoi import extract_article_objects
from crawl_vnexpress import collect_comments, normalized_comment, save_result as save_vnexpress


class CrawlerTests(unittest.TestCase):
    def test_window_boundaries_and_duplicate_ids(self):
        end = datetime(2026, 10, 7, tzinfo=timezone.utc)
        start = end - timedelta(hours=24)
        store = ArticleStore(start, end)
        rows = [{"id": i, "title": "Tin tiếng Việt", "date": stamp,
                 "url": f"/tin-c{i}.epi#tracking"} for i, stamp in enumerate(
                     [start.timestamp()-1, start.timestamp(), end.timestamp()-1, end.timestamp()], 1)]
        self.assertEqual(store.process({"nested": rows}, "test"), 4)
        self.assertEqual(store.process(rows, "duplicate"), 0)
        self.assertEqual(set(store.articles), {2, 3})
        self.assertTrue(store.reached_old)
        self.assertNotIn("#", store.articles[2]["url"])

    def test_invalid_objects_and_external_urls(self):
        end = datetime.now(timezone.utc)
        row = {"id": 12, "date": end.timestamp()-1, "title": "Test", "url": "https://evil.example/tin-c12.epi"}
        store = ArticleStore(end-timedelta(hours=24), end)
        self.assertEqual(store.process(row, "test"), 0)
        self.assertEqual(list(extract_article_objects({**row, "id": True})), [])
        self.assertEqual(list(extract_article_objects({**row, "date": True})), [])

    def test_observed_links_only_and_embedded_json(self):
        parser = ListingHTML()
        parser.feed('<a href="/tin-moi/trang5.epi">Next</a><a href="https://evil.example/tin-moi/trang6.epi">Bad</a>'
                    '<script type="application/json">{"data": [1,2]}</script>'
                    '<script type="application/json">broken</script>')
        self.assertEqual(parser.links, {"https://baomoi.com/tin-moi/trang5.epi"})
        self.assertEqual(parser.payloads, [{"data": [1, 2]}])

    def test_partial_comments_never_receive_complete_ranking(self):
        result = {"articles": [{"article_id": 1, "comment_count": 8, "comments_complete": False, "interaction_score": None},
                               {"article_id": 2, "comment_count": 3, "comments_complete": True, "interaction_score": 5}],
                  "failures": [], "time_unknown": [], "candidate_count": 3, "processed_candidates": 2}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory)/"nested"/"result.json"
            save_vnexpress(path, result)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["ranking_by_comment_count"], [1, 2])
            self.assertEqual(loaded["ranking_by_interaction"], [2])
            self.assertTrue(loaded["ranking_is_partial"])
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_cross_article_comment_is_rejected(self):
        with self.assertRaises(ValueError):
            normalized_comment({"article_id": 123}, 456)

    def test_paginated_comments_deduplicate_embedded_replies(self):
        def comment(cid, parent, likes):
            return {"article_id": 99, "comment_id": cid, "parent_id": parent, "userlike": likes}
        reply = comment(2, 1, 2)
        root = {**comment(1, 1, 4), "replys": {"total": 2, "items": [reply]}}
        initial = {"error": 0, "data": {"total": 2, "totalitem": 4, "items": [root]}}
        next_roots = {"error": 0, "data": {"total": 2, "totalitem": 4, "items": [comment(4, 4, 3)]}}
        next_replies = {"error": 0, "data": {"items": [reply, comment(3, 1, 1)]}}
        template = "https://usi-saas.vnexpress.net/index/get?siteid=100&objectid=99&offset=0"
        with patch("crawl_vnexpress.fetch_json", side_effect=[next_roots, next_replies]) as fetch:
            result = collect_comments(None, None, template, initial, 99)
        self.assertTrue(result["comments_complete"])
        self.assertEqual(result["loaded_reply_count"], 2)
        self.assertEqual(result["total_comment_likes"], 10)
        self.assertEqual(result["interaction_score"], 12)
        self.assertEqual(fetch.call_count, 2)

    def test_comment_total_mismatch_keeps_partial_score(self):
        initial = {"error": 0, "data": {"total": 1, "totalitem": 2, "items": [
            {"article_id": 99, "comment_id": 1, "parent_id": 1, "userlike": 4}]}}
        result = collect_comments(None, None, "https://usi-saas.vnexpress.net/index/get?siteid=100&offset=0", initial, 99)
        self.assertFalse(result["comments_complete"])
        self.assertIsNone(result["interaction_score"])
        self.assertEqual(result["partial_interaction_score"], 4)

    def test_utf8_atomic_checkpoint(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory)/"nested"/"result.json"
            save_result(path, {"title": "Tiếng Việt"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["title"], "Tiếng Việt")


if __name__ == "__main__":
    unittest.main()
