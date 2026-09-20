# -*- coding: utf-8 -*-
"""分页 + OAuth2 两个场景的自测。

这两个场景存在的意义：**一个同步批次里有多次业务请求**，才观察得到后端的令牌复用、
401 后重取重放、以及临近过期时的主动重取。这里用「模拟后端行为」的方式把期望钉住。
"""

import time
import unittest

from .support import ProviderTestCase, request, obtain_token, records_of


def _bearer(token):
    return {"Authorization": "Bearer " + token}


class PagedOauth2CatalogTest(ProviderTestCase):

    def test_catalog_declares_pagination_and_expectations(self):
        for name in ("oauth2-paged-maxuses", "oauth2-paged-expiring"):
            with self.subTest(scenario=name):
                item = self.endpoint(name, "/items")
                self.assertEqual("DATA", item["role"])
                self.assertEqual(50, item["expectedTotal"])
                self.assertEqual("data.items", item["recordPath"])
                self.assertEqual("id", item["primaryKey"])
                self.assertEqual("PAGE_NUMBER", item["paginationType"])
                pg = item["pagination"]
                self.assertEqual({"name": "page", "in": "QUERY"}, pg["pageParam"])
                self.assertEqual({"name": "page_size", "in": "QUERY"}, pg["pageSizeParam"])
                self.assertEqual(1, pg["startFrom"])
                self.assertEqual(10, pg["defaultPageSize"])
                self.assertEqual("data.total", pg["totalPath"])
                self.assertEqual("data.has_next", pg["hasMorePath"])
                self.assertIn("期望", item["note"])
                self.assertEqual("TOKEN",
                                 self.endpoint(name, "/oauth/token", "POST")["role"])

    def test_token_flow_declares_failure_policy(self):
        maxuses = self.scenario("oauth2-paged-maxuses")["auth"]["tokenFlow"]
        self.assertEqual(2, maxuses["tokenMaxUses"])
        self.assertEqual("post", maxuses["clientAuthMethod"])
        self.assertNotIn("expiresInSeconds", maxuses)

        expiring = self.scenario("oauth2-paged-expiring")["auth"]["tokenFlow"]
        self.assertEqual(35, expiring["expiresInSeconds"])
        self.assertNotIn("tokenMaxUses", expiring)

    def test_page_delay_is_declared_only_for_expiring_scenario(self):
        self.assertEqual(3, self.endpoint("oauth2-paged-expiring", "/items")
                         ["pagination"]["pageDelaySeconds"])
        self.assertNotIn("pageDelaySeconds",
                         self.endpoint("oauth2-paged-maxuses", "/items")["pagination"])

    def test_page_size_override_changes_page_count(self):
        self.set_config(oauth2PagedDelaySeconds=0)
        self.addCleanup(self.set_config, oauth2PagedDelaySeconds=3)
        entry = self.scenario("oauth2-paged-expiring")
        token = obtain_token(entry["baseUrl"], entry["auth"]["tokenFlow"])
        payload = request(entry["baseUrl"] + "/items", headers=_bearer(token),
                          query={"page": "1", "page_size": "25"}).json
        self.assertEqual(25, len(payload["data"]["items"]))
        self.assertEqual(50, payload["data"]["total"])
        self.assertTrue(payload["data"]["has_next"])


class PagedMaxUsesTest(ProviderTestCase):
    """oauth2-paged-maxuses：复用令牌遇 401 → 重取 → 重放同页"""

    def test_third_business_request_with_same_token_is_401(self):
        entry = self.scenario("oauth2-paged-maxuses")
        url = entry["baseUrl"] + "/items"
        token = obtain_token(entry["baseUrl"], entry["auth"]["tokenFlow"])
        self.assertEqual(200, request(url, headers=_bearer(token), query={"page": "1"}).status)
        self.assertEqual(200, request(url, headers=_bearer(token), query={"page": "2"}).status)
        self.assertEqual(401, request(url, headers=_bearer(token), query={"page": "3"}).status)

    def test_full_sync_matches_the_expectation_written_in_catalog(self):
        """按后端应有的行为跑一遍：遇 401 就重取令牌并重放同页"""
        entry = self.scenario("oauth2-paged-maxuses")
        item = self.endpoint("oauth2-paged-maxuses", "/items")
        url = item["url"]
        flow = entry["auth"]["tokenFlow"]

        token = obtain_token(entry["baseUrl"], flow)
        token_calls = 1
        unauthorized = 0
        records = []
        page = 1
        for _ in range(20):                      # 防死循环
            resp = request(url, headers=_bearer(token), query={"page": str(page)})
            if resp.status == 401:
                unauthorized += 1
                token = obtain_token(entry["baseUrl"], flow)
                token_calls += 1
                continue                          # 重放同一页
            self.assertEqual(200, resp.status, resp.text[:200])
            records.extend(records_of(resp.json, item["recordPath"]))
            if not resp.json["data"]["has_next"]:
                break
            page += 1

        self.assertEqual(item["expectedTotal"], len(records))
        self.assertEqual(50, len(records))
        keys = [r["id"] for r in records]
        self.assertEqual(len(keys), len(set(keys)), "记录有重复")
        self.assertEqual(3, token_calls, "令牌端点应被请求 3 次")
        self.assertEqual(2, unauthorized, "业务请求应出现 2 次 401")

    def test_each_401_is_followed_by_a_same_page_replay(self):
        """从请求日志验证：每次 401 之后紧跟一次同页重放且成功"""
        entry = self.scenario("oauth2-paged-maxuses")
        item = self.endpoint("oauth2-paged-maxuses", "/items")
        flow = entry["auth"]["tokenFlow"]
        token = obtain_token(entry["baseUrl"], flow)
        page = 1
        for _ in range(20):
            resp = request(item["url"], headers=_bearer(token), query={"page": str(page)})
            if resp.status == 401:
                token = obtain_token(entry["baseUrl"], flow)
                continue
            if not resp.json["data"]["has_next"]:
                break
            page += 1

        logged = self.requests_of("oauth2-paged-maxuses")
        token_requests = [r for r in logged if r["path"].endswith("/oauth/token")]
        business = [r for r in logged if r["path"].endswith("/items")]
        self.assertEqual(3, len(token_requests))
        self.assertEqual(7, len(business), "5 页成功 + 2 次 401 重放")
        failures = [i for i, r in enumerate(business) if r["status"] == 401]
        self.assertEqual(2, len(failures))
        for index in failures:
            replay = business[index + 1]
            self.assertEqual(business[index]["query"]["page"], replay["query"]["page"],
                             "401 之后应重放同一页")
            self.assertEqual(200, replay["status"])

    def test_max_uses_is_configurable(self):
        entry = self.scenario("oauth2-paged-maxuses")
        self.set_config(oauth2PagedTokenMaxUses=5)
        self.addCleanup(self.set_config, oauth2PagedTokenMaxUses=2)
        flow = self.scenario("oauth2-paged-maxuses")["auth"]["tokenFlow"]
        self.assertEqual(5, flow["tokenMaxUses"], "清单应反映改后的配置")
        token = obtain_token(entry["baseUrl"], flow)
        url = entry["baseUrl"] + "/items"
        for page in range(1, 6):
            self.assertEqual(200, request(url, headers=_bearer(token),
                                          query={"page": str(page)}).status)
        self.assertEqual(401, request(url, headers=_bearer(token), query={"page": "1"}).status)


class PagedExpiringTest(ProviderTestCase):
    """oauth2-paged-expiring：临近过期主动重取，全程不应出现 401"""

    def setUp(self):
        super().setUp()
        # 自测把有效期与延迟调小，只验服务端行为，别让用例变慢
        self.set_config(oauth2PagedTtlSeconds=2, oauth2PagedDelaySeconds=0)
        self.addCleanup(self.set_config, oauth2PagedTtlSeconds=35, oauth2PagedDelaySeconds=3)

    def test_proactive_refetch_avoids_any_401(self):
        """模拟后端：每页前判断令牌剩余不足就重取——全程无 401"""
        entry = self.scenario("oauth2-paged-expiring")
        item = self.endpoint("oauth2-paged-expiring", "/items")
        flow = self.scenario("oauth2-paged-expiring")["auth"]["tokenFlow"]
        self.assertEqual(2, flow["expiresInSeconds"])

        token, issued_at = obtain_token(entry["baseUrl"], flow), time.monotonic()
        token_calls = 1
        records = []
        for page in range(1, 6):
            # 剩余不足 1 秒就提前重取（真实后端的阈值是 30 秒）
            if flow["expiresInSeconds"] - (time.monotonic() - issued_at) < 1:
                token, issued_at = obtain_token(entry["baseUrl"], flow), time.monotonic()
                token_calls += 1
            resp = request(item["url"], headers=_bearer(token), query={"page": str(page)})
            self.assertEqual(200, resp.status, "主动重取后不应出现 401")
            records.extend(records_of(resp.json, item["recordPath"]))
            time.sleep(0.5)

        self.assertEqual(50, len(records))
        self.assertEqual(len(records), len({r["id"] for r in records}))
        self.assertGreaterEqual(token_calls, 2, "令牌端点应被请求至少 2 次")
        self.assertFalse([r for r in self.requests_of("oauth2-paged-expiring")
                          if r["status"] == 401], "不应出现任何 401")

    def test_without_refetch_the_token_expires(self):
        """反面对照：不重取，过期后必然 401"""
        entry = self.scenario("oauth2-paged-expiring")
        flow = self.scenario("oauth2-paged-expiring")["auth"]["tokenFlow"]
        token = obtain_token(entry["baseUrl"], flow)
        url = entry["baseUrl"] + "/items"
        self.assertEqual(200, request(url, headers=_bearer(token), query={"page": "1"}).status)
        time.sleep(2.2)
        self.assertEqual(401, request(url, headers=_bearer(token), query={"page": "2"}).status)

    def test_token_is_not_limited_by_use_count(self):
        """这个场景只按时间失效，连发多次不应被次数打掉"""
        entry = self.scenario("oauth2-paged-expiring")
        flow = self.scenario("oauth2-paged-expiring")["auth"]["tokenFlow"]
        token = obtain_token(entry["baseUrl"], flow)
        url = entry["baseUrl"] + "/items"
        for page in range(1, 6):
            self.assertEqual(200, request(url, headers=_bearer(token),
                                          query={"page": str(page)}).status)

    def test_page_delay_is_applied(self):
        self.set_config(oauth2PagedDelaySeconds=1)
        entry = self.scenario("oauth2-paged-expiring")
        flow = self.scenario("oauth2-paged-expiring")["auth"]["tokenFlow"]
        token = obtain_token(entry["baseUrl"], flow)
        started = time.monotonic()
        resp = request(entry["baseUrl"] + "/items", headers=_bearer(token), query={"page": "1"})
        self.assertEqual(200, resp.status)
        self.assertGreaterEqual(time.monotonic() - started, 1.0)
        # 令牌端点不受延迟影响
        started = time.monotonic()
        obtain_token(entry["baseUrl"], flow)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_scoped_reset_only_affects_its_own_scenario(self):
        maxuses = self.scenario("oauth2-paged-maxuses")
        expiring = self.scenario("oauth2-paged-expiring")
        token_a = obtain_token(maxuses["baseUrl"], maxuses["auth"]["tokenFlow"])
        token_b = obtain_token(expiring["baseUrl"], expiring["auth"]["tokenFlow"])
        request(self.base + "/__admin/reset?scenario=oauth2-paged-maxuses", method="POST")
        self.assertEqual(401, request(maxuses["baseUrl"] + "/items",
                                      headers=_bearer(token_a)).status)
        self.assertEqual(200, request(expiring["baseUrl"] + "/items",
                                      headers=_bearer(token_b)).status)


if __name__ == "__main__":
    unittest.main()
