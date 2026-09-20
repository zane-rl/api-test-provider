# -*- coding: utf-8 -*-
"""B 类分页场景自测：按清单声明的方式翻到底，累计条数等于 expectedTotal 且主键无重复。"""

import unittest

from .support import ProviderTestCase, request, fetch_all, records_of, build_auth


class PaginationTest(ProviderTestCase):

    def _data_endpoints(self, group="B 分页"):
        for entry in self.catalog().values():
            if entry["group"] != group:
                continue
            for item in entry["endpoints"]:
                if item["role"] == "DATA":
                    yield entry, item

    def test_every_paginated_endpoint_reaches_expected_total(self):
        checked = 0
        for entry, item in self._data_endpoints():
            with self.subTest(scenario=entry["name"], path=item["path"]):
                cross = item["pagination"].get("nextUrlStyle") == "ABSOLUTE_CROSS_ORIGIN"
                rows = fetch_all(item)
                if cross:
                    # 跨主机地址按后端约定不跟随，只应拿到首页
                    self.assertEqual(item["pagination"]["expectedRecordsIfRefused"], len(rows))
                else:
                    self.assertEqual(item["expectedTotal"], len(rows))
                if item["primaryKey"]:
                    keys = [r[item["primaryKey"]] for r in rows]
                    self.assertEqual(len(keys), len(set(keys)), "主键重复")
                checked += 1
        self.assertEqual(12, checked, "B 类数据端点数量变了，请同步 README")

    def test_cross_host_next_url_is_reachable_when_followed(self):
        """证明「只取到首页」是因为拒绝跟随，而不是那个地址本身不可用"""
        item = self.endpoint("next-url", "/suppliers-crosshost")
        rows = fetch_all(item, follow_cross_origin=True)
        self.assertEqual(item["expectedTotal"], len(rows))

    def test_relative_next_url_is_resolved_against_current_page(self):
        item = self.endpoint("next-url", "/suppliers-relative")
        resp = request(item["url"])
        self.assertTrue(resp.json["next"].startswith("/s/next-url/"),
                        "应是相对地址：%s" % resp.json["next"])
        self.assertEqual(item["expectedTotal"], len(fetch_all(item)))

    def test_same_origin_next_url_is_absolute(self):
        item = self.endpoint("next-url", "/suppliers")
        resp = request(item["url"])
        self.assertTrue(resp.json["next"].startswith(self.base + "/s/next-url/suppliers?"))

    def test_last_page_next_cursor_is_null(self):
        item = self.endpoint("cursor-query", "/events")
        resp = request(item["url"], query={"cursor": "cur-60", "limit": "20"})
        self.assertEqual(200, resp.status)
        self.assertIsNone(resp.json["next_cursor"])
        self.assertFalse(resp.json["has_more"])
        self.assertEqual(13, len(resp.json["data"]))

    def test_page_number_zero_based_starts_at_zero(self):
        item = self.endpoint("page-number", "/products0")
        self.assertEqual(0, item["pagination"]["startFrom"])
        first = request(item["url"], query={"page": "0", "page_size": "20"}).json
        self.assertEqual(0, first["data"]["page"])
        self.assertEqual("PRD-00001", first["data"]["items"][0]["sku"])

    def test_fixed_page_size_endpoint_rejects_page_size(self):
        item = self.endpoint("page-number", "/products-nosize")
        resp = request(item["url"], query={"page": "1", "page_size": "20"})
        self.assertEqual(400, resp.status)
        self.assertIn("page_size", resp.json["message"])
        # 不带 page_size 时每页固定 10 条
        resp = request(item["url"], query={"page": "1"})
        self.assertEqual(10, len(resp.json["data"]["items"]))
        self.assertNotIn("total", resp.json["data"])

    def test_fixed_page_size_endpoint_ends_on_empty_page(self):
        item = self.endpoint("page-number", "/products-nosize")
        resp = request(item["url"], query={"page": "7"})     # 57 条 → 第 7 页为空
        self.assertEqual([], resp.json["data"]["items"])

    def test_page_number_rejects_non_integer(self):
        item = self.endpoint("page-number", "/products")
        resp = request(item["url"], query={"page": "abc"})
        self.assertEqual(400, resp.status)

    def test_offset_records_have_no_primary_key(self):
        item = self.endpoint("offset", "/movements")
        self.assertIsNone(item["primaryKey"])
        row = request(item["url"], query={"offset": "0", "limit": "1"}).json["data"]["items"][0]
        self.assertNotIn("id", row)
        self.assertEqual(103, item["expectedTotal"])

    def test_body_page_rejects_string_numbers(self):
        item = self.endpoint("body-page", "/products/query", method="POST")
        resp = request(item["url"], method="POST",
                       json_body={"page": "1", "size": 20, "keyword": ""})
        self.assertEqual(400, resp.status)
        self.assertIn("page", resp.json["message"])
        resp = request(item["url"], method="POST",
                       json_body={"page": 1, "size": "20", "keyword": ""})
        self.assertEqual(400, resp.status)
        self.assertIn("size", resp.json["message"])
        resp = request(item["url"], method="POST", json_body={"page": 1, "size": 20})
        self.assertEqual(200, resp.status)

    def test_body_page_params_are_declared_in_body(self):
        item = self.endpoint("body-page", "/products/query", method="POST")
        self.assertEqual("BODY", item["pagination"]["pageParam"]["in"])
        self.assertEqual("BODY", item["pagination"]["pageSizeParam"]["in"])

    def test_cursor_in_header_is_read_from_header(self):
        item = self.endpoint("cursor-header", "/events")
        self.assertEqual("HEADER", item["pagination"]["cursorParam"]["in"])
        first = request(item["url"], query={"limit": "20"}).json
        second = request(item["url"], query={"limit": "20"},
                         headers={"X-Cursor": first["next_cursor"]}).json
        self.assertNotEqual(first["data"][0]["event_id"], second["data"][0]["event_id"])
        # 游标放 Query 对这个端点无效（仍返回首页）
        ignored = request(item["url"], query={"limit": "20", "cursor": first["next_cursor"]}).json
        self.assertEqual(first["data"][0]["event_id"], ignored["data"][0]["event_id"])

    def test_cursor_in_body_requires_numeric_limit(self):
        item = self.endpoint("cursor-body", "/events/search", method="POST")
        resp = request(item["url"], method="POST", json_body={"limit": "20"})
        self.assertEqual(400, resp.status)

    def test_root_array_has_empty_record_path(self):
        item = self.endpoint("root-array", "/warehouses")
        self.assertEqual("", item["recordPath"])
        payload = request(item["url"]).json
        self.assertIsInstance(payload, list)
        self.assertEqual(4, len(payload))

    def test_invalid_cursor_returns_400(self):
        item = self.endpoint("cursor-query", "/events")
        resp = request(item["url"], query={"cursor": "not-a-cursor"})
        self.assertEqual(400, resp.status)

    def test_datasets_are_deterministic_across_calls(self):
        item = self.endpoint("page-number", "/products")
        first = request(item["url"], query={"page": "1", "page_size": "5"}).json
        second = request(item["url"], query={"page": "1", "page_size": "5"}).json
        self.assertEqual(first, second)
        self.assertEqual(item["sampleFirst3"], first["data"]["items"][:3])


if __name__ == "__main__":
    unittest.main()
