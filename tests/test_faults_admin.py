# -*- coding: utf-8 -*-
"""E 类故障场景 + admin 接口 + 405 行为自测。"""

import time
import unittest

from .support import ProviderTestCase, request, obtain_token


class FaultsTest(ProviderTestCase):

    def url(self, path):
        return "%s/s/faults%s" % (self.base, path)

    def test_always_500(self):
        resp = request(self.url("/always-500"))
        self.assertEqual(500, resp.status)
        self.assertEqual(500, resp.json["code"])

    def test_flaky_fails_twice_then_succeeds(self):
        statuses = [request(self.url("/flaky")).status for _ in range(3)]
        self.assertEqual([503, 503, 200], statuses)
        # 复位后重新从第 1 次开始
        request(self.base + "/__admin/reset", method="POST")
        self.assertEqual(503, request(self.url("/flaky")).status)

    def test_rate_limit_alternates_with_retry_after(self):
        first = request(self.url("/rate-limit"))
        self.assertEqual(429, first.status)
        self.assertEqual("2", first.headers.get("Retry-After"))
        self.assertEqual(200, request(self.url("/rate-limit")).status)

    def test_biz_error_is_http_200(self):
        resp = request(self.url("/biz-error"))
        self.assertEqual(200, resp.status)
        self.assertEqual(5001, resp.json["code"])

    def test_slow_actually_sleeps(self):
        started = time.time()
        resp = request(self.url("/slow"), query={"seconds": "1"})
        self.assertEqual(200, resp.status)
        self.assertGreaterEqual(time.time() - started, 1.0)
        self.assertEqual(1, resp.json["data"]["slept"])

    def test_slow_returns_deterministic_records(self):
        """慢响应端点必须给真实数据：后端要从中推断记录结构"""
        item = self.endpoint("faults", "/slow")
        self.assertEqual("FAULT", item["role"])
        self.assertEqual("data.items", item["recordPath"])
        self.assertEqual("id", item["primaryKey"])
        self.assertEqual(5, item["expectedTotal"])

        payload = request(self.url("/slow"), query={"seconds": "0"}).json
        rows = payload["data"]["items"]
        self.assertEqual(5, len(rows))
        self.assertTrue(all(r["id"].startswith("SLOW-") for r in rows), rows[0]["id"])
        self.assertEqual(len(rows), len({r["id"] for r in rows}))
        self.assertEqual(item["sampleFirst3"], rows[:3])
        self.assertEqual(5, payload["data"]["total"])
        # 确定性：两次调用完全一致
        self.assertEqual(payload, request(self.url("/slow"), query={"seconds": "0"}).json)

    def test_slow_openapi_declares_record_structure_and_param(self):
        doc = request(self.base + "/s/faults/openapi.json").json
        operation = doc["paths"]["/slow"]["get"]
        self.assertEqual({"seconds"}, {p["name"] for p in operation["parameters"]})
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        record = schema["properties"]["data"]["properties"]["items"]["items"]["properties"]
        for field in ("id", "name", "price", "quantity", "active", "updated_at", "spec"):
            self.assertIn(field, record)

    def test_not_json_returns_html(self):
        resp = request(self.url("/not-json"))
        self.assertEqual(200, resp.status)
        self.assertIn("text/html", resp.headers["Content-Type"])
        self.assertIn("gateway error", resp.text)

    def test_huge_response_exceeds_3mb(self):
        resp = request(self.url("/huge"))
        self.assertEqual(200, resp.status)
        self.assertGreater(len(resp.text), 3 * 1024 * 1024)

    def test_drifted_drops_and_adds_fields(self):
        rows = request(self.url("/drifted")).json["data"]["items"]
        self.assertTrue(all("price" not in r for r in rows))
        self.assertTrue(all(r["extra_note"] == "新增字段" for r in rows))

    def test_redirect_is_302_and_not_followed(self):
        resp = request(self.url("/redirect"))
        self.assertEqual(302, resp.status)
        self.assertEqual("/s/faults/ok", resp.headers["Location"])
        self.assertEqual(200, request(self.url("/ok")).status)

    def test_counters_are_reset(self):
        request(self.url("/flaky"))
        state = request(self.base + "/__admin/state").json
        self.assertEqual(1, state["faultCounters"]["flaky"])
        request(self.base + "/__admin/reset", method="POST")
        state = request(self.base + "/__admin/state").json
        self.assertEqual({}, state["faultCounters"])


class AdminTest(ProviderTestCase):

    def test_health(self):
        resp = request(self.base + "/health")
        self.assertEqual(200, resp.status)
        self.assertEqual({"status": "ok"}, resp.json)

    def test_requests_log_records_headers_query_cookies_and_body(self):
        entry = self.scenario("fixed-param")
        request(entry["baseUrl"] + "/items", query={"sign": "test-sign"},
                headers={"X-App-Id": "app-001"}, cookies={"tenant": "t-001"})
        logged = self.requests_of("fixed-param")
        self.assertEqual(1, len(logged))
        row = logged[0]
        self.assertEqual("GET", row["method"])
        self.assertEqual("test-sign", row["query"]["sign"])
        self.assertEqual("app-001", row["headers"]["X-App-Id"])
        self.assertEqual("t-001", row["cookies"]["tenant"])
        self.assertEqual(200, row["status"])
        self.assertEqual(1, row["seq"])

    def test_requests_log_records_body_and_failed_status(self):
        item = self.endpoint("body-page", "/products/query", method="POST")
        request(item["url"], method="POST", json_body={"page": "1", "size": 20})
        row = self.requests_of("body-page")[-1]
        self.assertEqual(400, row["status"])
        self.assertIn('"page": "1"', row["body"])

    def test_reset_clears_request_log(self):
        request(self.scenario("none")["baseUrl"] + "/items")
        self.assertEqual(1, len(self.requests_of("none")))
        request(self.base + "/__admin/reset", method="POST")
        self.assertEqual(0, len(self.requests_of("none")))

    def test_scoped_reset_only_clears_that_scenario_log(self):
        request(self.scenario("none")["baseUrl"] + "/items")
        request(self.scenario("root-array")["baseUrl"] + "/warehouses")
        resp = request(self.base + "/__admin/reset?scenario=none", method="POST")
        self.assertEqual(200, resp.status)
        self.assertEqual("none", resp.json["scenario"])
        self.assertEqual(1, resp.json["removedRequests"])
        self.assertEqual(0, len(self.requests_of("none")))
        self.assertEqual(1, len(self.requests_of("root-array")))

    def test_scoped_reset_only_invalidates_that_scenario_tokens(self):
        bearer = self.scenario("bearer-request")
        oauth = self.scenario("oauth2-post")
        bearer_token = obtain_token(bearer["baseUrl"], bearer["auth"]["tokenFlow"])
        oauth_token = obtain_token(oauth["baseUrl"], oauth["auth"]["tokenFlow"])
        request(self.base + "/__admin/reset?scenario=bearer-request", method="POST")
        self.assertEqual(401, request(bearer["baseUrl"] + "/items",
                                      headers={"Authorization": "Bearer " + bearer_token}).status)
        self.assertEqual(200, request(oauth["baseUrl"] + "/items",
                                      headers={"Authorization": "Bearer " + oauth_token}).status)

    def test_scoped_reset_touches_fault_counters_only_for_faults(self):
        request(self.base + "/s/faults/flaky")
        request(self.base + "/__admin/reset?scenario=none", method="POST")
        state = request(self.base + "/__admin/state").json
        self.assertEqual(1, state["faultCounters"]["flaky"], "复位别的场景不应动故障计数器")
        request(self.base + "/__admin/reset?scenario=faults", method="POST")
        state = request(self.base + "/__admin/state").json
        self.assertEqual({}, state["faultCounters"])

    def test_scoped_reset_keeps_config(self):
        request(self.base + "/__admin/config", method="POST",
                json_body={"oauth2BasicTokenMaxUses": 3})
        try:
            resp = request(self.base + "/__admin/reset?scenario=none", method="POST")
            self.assertEqual(3, resp.json["config"]["oauth2BasicTokenMaxUses"])
        finally:
            request(self.base + "/__admin/config", method="POST",
                    json_body={"oauth2BasicTokenMaxUses": 2})

    def test_scoped_reset_rejects_unknown_scenario(self):
        resp = request(self.base + "/__admin/reset?scenario=nope", method="POST")
        self.assertEqual(404, resp.status)

    def test_requests_filter_and_limit(self):
        none_url = self.scenario("none")["baseUrl"] + "/items"
        for _ in range(3):
            request(none_url)
        request(self.scenario("root-array")["baseUrl"] + "/warehouses")
        self.assertEqual(3, len(self.requests_of("none")))
        resp = request("%s/__admin/requests?scenario=none&limit=2" % self.base)
        self.assertEqual(3, resp.json["matched"])
        self.assertEqual(2, resp.json["returned"])
        # 按时间顺序返回最近的若干条
        seqs = [r["seq"] for r in resp.json["requests"]]
        self.assertEqual(sorted(seqs), seqs)

    def test_admin_requests_rejects_bad_limit(self):
        resp = request("%s/__admin/requests?limit=abc" % self.base)
        self.assertEqual(400, resp.status)

    def test_admin_endpoints_are_not_logged(self):
        request(self.base + "/__admin/scenarios")
        request(self.base + "/health")
        resp = request("%s/__admin/requests?limit=1000" % self.base)
        self.assertEqual(0, resp.json["matched"])

    def test_scenarios_catalog_shape(self):
        catalog = self.catalog()
        self.assertEqual(39, len(catalog))
        for name, entry in catalog.items():
            with self.subTest(scenario=name):
                self.assertTrue(entry["openapiUrl"].endswith("/openapi.json"))
                self.assertIn("type", entry["auth"])
                self.assertIsInstance(entry["auth"]["extraAuthParams"], list)
                for item in entry["endpoints"]:
                    self.assertIn(item["role"], ("DATA", "TOKEN", "FAULT"))
                    self.assertIn(item["paginationType"],
                                  ("NONE", "PAGE_NUMBER", "OFFSET", "CURSOR", "NEXT_URL"))
                    if item["role"] == "DATA" and item["expectedTotal"]:
                        self.assertTrue(item["sampleFirst3"] is None
                                        or len(item["sampleFirst3"]) <= 3)

    def test_token_endpoints_are_marked_as_token_role(self):
        for name in ("bearer-request", "oauth2-post", "oauth2-basic", "oauth2-expiring"):
            roles = {i["path"]: i["role"] for i in self.scenario(name)["endpoints"]}
            self.assertEqual("DATA", roles["/items"])
            token_path = "/login" if name.startswith("bearer") else "/oauth/token"
            self.assertEqual("TOKEN", roles[token_path])

    def test_write_methods_are_405_everywhere(self):
        targets = [self.base + "/s/none/items",
                   self.base + "/s/page-number/products",
                   self.base + "/s/faults/ok",
                   self.base + "/__admin/reset"]
        for url in targets:
            for method in ("PUT", "PATCH", "DELETE"):
                with self.subTest(url=url, method=method):
                    resp = request(url, method=method)
                    self.assertEqual(405, resp.status)
                    self.assertEqual("GET, POST", resp.headers["Allow"])

    def test_unknown_scenario_and_endpoint_return_404(self):
        self.assertEqual(404, request(self.base + "/s/nope/items").status)
        self.assertEqual(404, request(self.base + "/s/none/nope").status)
        self.assertEqual(404, request(self.base + "/nope").status)

    def test_wrong_method_on_existing_path_returns_405(self):
        resp = request(self.base + "/s/none/items", method="POST")
        self.assertEqual(405, resp.status)


if __name__ == "__main__":
    unittest.main()
