# -*- coding: utf-8 -*-
"""`depend-switchable` 场景自测：分支依赖的条数、按接口切换持续失败、调用计数、复位。"""

import unittest

from scenarios import openapi

from .support import ProviderTestCase, request, records_of

NAME = "depend-switchable"


class SwitchableTest(ProviderTestCase):

    def url(self, path):
        return "%s/s/%s%s" % (self.base, NAME, path)

    def faults_url(self, scenario=NAME):
        return "%s/__admin/faults?scenario=%s" % (self.base, scenario)

    def switches(self):
        resp = request(self.faults_url())
        self.assertEqual(200, resp.status)
        self.assertEqual(NAME, resp.json["scenario"])
        return {item["key"]: item for item in resp.json["endpoints"]}

    def switch(self, **body):
        resp = request(self.faults_url(), method="POST", json_body=body)
        self.assertEqual(200, resp.status, resp.text)
        return {item["key"]: item for item in resp.json["endpoints"]}

    def rows(self, path, query=None):
        resp = request(self.url(path), query=query)
        self.assertEqual(200, resp.status, resp.text)
        return records_of(resp.json, "data.items")

    # ---------- 数据与依赖 ----------

    def test_branch_dependency_totals_match_catalog(self):
        customers = self.rows("/customers")
        self.assertEqual(["SWC-0000%d" % i for i in range(1, 6)], [c["id"] for c in customers])
        order_counts, address_counts, orders, items, addresses = [], [], [], [], []
        for customer in customers:
            own = self.rows("/customers/%s/orders" % customer["id"])
            order_counts.append(len(own))
            orders += own
            self.assertTrue(all(o["customer_id"] == customer["id"] for o in own))
            own = self.rows("/addresses", query={"customer_id": customer["id"]})
            address_counts.append(len(own))
            addresses += own
        for order in orders:
            own = self.rows("/orders/%s/items" % order["order_no"])
            self.assertTrue(all(i["order_no"] == order["order_no"] for i in own))
            items += own
        self.assertEqual([2, 3, 1, 2, 3], order_counts, "订单条数公式变了，请同步 README")
        self.assertEqual([2, 1, 0, 2, 1], address_counts, "地址条数公式变了，请同步 README")
        totals = {"/customers": customers, "/customers/{customerId}/orders": orders,
                  "/orders/{orderNo}/items": items, "/addresses": addresses}
        self.assertEqual([5, 11, 16, 6], [len(v) for v in totals.values()])
        for path, got in totals.items():
            item = self.endpoint(NAME, path)
            self.assertEqual(item["expectedTotal"], len(got), path)
            self.assertEqual(item["sampleFirst3"], got[:3], path)
            self.assertEqual("DATA", item["role"])
        self.assertEqual("SWC-00001-O1", orders[0]["order_no"])
        self.assertEqual("SWC-00001-O1-I1", items[0]["item_no"])

    def test_records_are_stable_across_calls(self):
        for path in ("/customers", "/customers/SWC-00002/orders", "/orders/SWC-00002-O3/items"):
            self.assertEqual(request(self.url(path)).json, request(self.url(path)).json)

    def test_bad_inputs(self):
        self.assertEqual(404, request(self.url("/customers/SWC-99999/orders")).status)
        self.assertEqual(404, request(self.url("/orders/NOPE/items")).status)
        self.assertEqual(400, request(self.url("/addresses")).status)
        self.assertEqual([], self.rows("/addresses", query={"customer_id": "SWC-99999"}))

    def test_openapi_declares_dependency_params(self):
        doc = request(self.url("/openapi.json")).json
        expected = {
            "/customers/{customerId}/orders": ("customerId", "path"),
            "/orders/{orderNo}/items": ("orderNo", "path"),
            "/addresses": ("customer_id", "query"),
        }
        self.assertNotIn("parameters", doc["paths"]["/customers"]["get"])
        for path, (name, location) in expected.items():
            params = doc["paths"][path]["get"]["parameters"]
            self.assertEqual([(name, location, True)],
                             [(p["name"], p["in"], p["required"]) for p in params], path)
        self.assertNotIn("/__admin/faults", str(doc["paths"].keys()))

    def test_catalog_lists_fault_keys_and_dependencies(self):
        entry = self.scenario(NAME)
        self.assertEqual("E 故障", entry["group"])
        self.assertEqual("none", entry["auth"]["type"])
        by_path = {i["path"]: i for i in entry["endpoints"]}
        self.assertEqual(["customers", "orders", "items", "addresses"],
                         [i["faultKey"] for i in entry["endpoints"]])
        self.assertEqual(sorted(self.switches()), sorted(i["faultKey"] for i in entry["endpoints"]))
        self.assertNotIn("dependsOn", by_path["/customers"])
        self.assertEqual({"path": "/customers", "field": "id",
                          "param": {"name": "customerId", "in": "PATH"}},
                         by_path["/customers/{customerId}/orders"]["dependsOn"])
        self.assertEqual("/customers/{customerId}/orders",
                         by_path["/orders/{orderNo}/items"]["dependsOn"]["path"])
        self.assertEqual({"name": "customer_id", "in": "QUERY"},
                         by_path["/addresses"]["dependsOn"]["param"])

    # ---------- 开关 ----------

    def test_default_state_is_all_normal(self):
        state = self.switches()
        self.assertEqual(["customers", "orders", "items", "addresses"], list(state))
        for item in state.values():
            self.assertEqual("normal", item["mode"])
            self.assertEqual(503, item["failStatus"])
            self.assertEqual({"total": 0, "succeeded": 0, "failed": 0}, item["calls"])
        self.assertEqual("/customers/{customerId}/orders", state["orders"]["path"])

    def test_fail_mode_persists_until_switched_back(self):
        state = self.switch(endpoint="orders", mode="fail")
        self.assertEqual("fail", state["orders"]["mode"])
        # 持续失败：不会像 /flaky 那样到第 N 次自己好
        for _ in range(6):
            resp = request(self.url("/customers/SWC-00001/orders"))
            self.assertEqual(503, resp.status)
            self.assertEqual(503, resp.json["code"])
        # 别的接口、别的场景都不受影响
        self.assertEqual(200, request(self.url("/customers")).status)
        self.assertEqual(200, request(self.url("/orders/SWC-00001-O1/items")).status)
        self.assertEqual(200, request(self.url("/addresses"),
                                      query={"customer_id": "SWC-00001"}).status)
        self.assertEqual(200, request(self.base + "/s/depend-path/products").status)
        state = self.switch(endpoint="orders", mode="normal")
        self.assertEqual("normal", state["orders"]["mode"])
        self.assertEqual(2, len(self.rows("/customers/SWC-00001/orders")))

    def test_each_endpoint_switches_independently(self):
        self.switch(switches=[{"endpoint": "customers", "mode": "fail"},
                              {"endpoint": "addresses", "mode": "fail", "status": 500}])
        self.assertEqual(503, request(self.url("/customers")).status)
        resp = request(self.url("/addresses"), query={"customer_id": "SWC-00001"})
        self.assertEqual(500, resp.status)
        self.assertEqual(500, resp.json["code"])
        self.assertEqual(200, request(self.url("/customers/SWC-00001/orders")).status)
        state = self.switches()
        self.assertEqual(["fail", "normal", "normal", "fail"], [s["mode"] for s in state.values()])
        self.assertEqual(500, state["addresses"]["failStatus"])
        # 切回正常后失败状态码还原成缺省值
        state = self.switch(endpoint="addresses", mode="normal")
        self.assertEqual(503, state["addresses"]["failStatus"])

    def test_failing_endpoint_fails_before_parameter_checks(self):
        self.switch(endpoint="addresses", mode="fail")
        self.assertEqual(503, request(self.url("/addresses")).status)

    def test_failed_calls_are_in_request_log(self):
        self.switch(endpoint="customers", mode="fail")
        request(self.url("/customers"))
        row = self.requests_of(NAME)[-1]
        self.assertEqual("/s/%s/customers" % NAME, row["path"])
        self.assertEqual(503, row["status"])

    def test_switch_rejects_bad_input(self):
        bad = [
            {"endpoint": "nope", "mode": "fail"},
            {"endpoint": "orders", "mode": "broken"},
            {"endpoint": "orders"},
            {"endpoint": "orders", "mode": "fail", "status": 200},
            {"endpoint": "orders", "mode": "fail", "status": "503"},
            {"endpoint": "orders", "mode": "fail", "status": True},
            {"switches": []},
            {"switches": ["orders"]},
            # 一项合法一项不合法：整体拒绝，不能只切一半
            {"switches": [{"endpoint": "customers", "mode": "fail"},
                          {"endpoint": "nope", "mode": "fail"}]},
        ]
        for body in bad:
            with self.subTest(body=body):
                resp = request(self.faults_url(), method="POST", json_body=body)
                self.assertEqual(400, resp.status, resp.text)
        self.assertEqual(400, request(self.faults_url(), method="POST", body="{oops").status)
        self.assertEqual(400, request(self.faults_url(), method="POST").status)
        self.assertEqual({"normal"}, {s["mode"] for s in self.switches().values()})

    def test_faults_requires_switchable_scenario(self):
        self.assertEqual(400, request(self.base + "/__admin/faults").status)
        self.assertEqual(404, request(self.faults_url("faults")).status)
        self.assertEqual(404, request(self.faults_url("nope")).status)
        self.assertEqual(405, request(self.faults_url(), method="DELETE").status)

    # ---------- 调用计数 ----------

    def test_calls_are_counted_separately_per_endpoint(self):
        request(self.url("/customers"))
        request(self.url("/customers"))
        request(self.url("/customers/SWC-00001/orders"))
        request(self.url("/customers/SWC-99999/orders"))          # 404 也算失败
        request(self.url("/addresses"))                           # 400 也算失败
        self.switch(endpoint="customers", mode="fail")
        for _ in range(3):
            request(self.url("/customers"))
        state = self.switches()
        self.assertEqual({"total": 5, "succeeded": 2, "failed": 3}, state["customers"]["calls"])
        self.assertEqual({"total": 2, "succeeded": 1, "failed": 1}, state["orders"]["calls"])
        self.assertEqual({"total": 0, "succeeded": 0, "failed": 0}, state["items"]["calls"])
        self.assertEqual({"total": 1, "succeeded": 0, "failed": 1}, state["addresses"]["calls"])
        # 切开关不清计数
        state = self.switch(endpoint="customers", mode="normal")
        self.assertEqual(5, state["customers"]["calls"]["total"])
        self.assertEqual(state, {s["key"]: s for s in
                                 request(self.base + "/__admin/state").json["faultSwitches"]})

    def test_docs_ignore_switches_and_do_not_count(self):
        self.switch(switches=[{"endpoint": key, "mode": "fail"}
                              for key in ("customers", "orders", "items", "addresses")])
        # 清掉进程内的文档缓存，逼它在「全部持续失败」时重新生成一次
        openapi.clear_cache()
        for url in (self.url("/openapi.json"), self.base + "/openapi/none.json"):
            resp = request(url)
            self.assertEqual(200, resp.status)
        doc = request(self.url("/openapi.json")).json
        schema = doc["paths"]["/customers"]["get"]["responses"]["200"]["content"][
            "application/json"]["schema"]
        record = schema["properties"]["data"]["properties"]["items"]["items"]["properties"]
        self.assertIn("id", record)
        self.assertEqual({0}, {s["calls"]["total"] for s in self.switches().values()})

    # ---------- 复位 ----------

    def test_scoped_reset_restores_switches_and_counts(self):
        self.switch(endpoint="orders", mode="fail", status=502)
        request(self.url("/customers/SWC-00001/orders"))
        # 复位别的场景不动它
        request(self.base + "/__admin/reset?scenario=faults", method="POST")
        state = self.switches()
        self.assertEqual(("fail", 502, 1), (state["orders"]["mode"], state["orders"]["failStatus"],
                                            state["orders"]["calls"]["failed"]))
        resp = request(self.base + "/__admin/reset?scenario=%s" % NAME, method="POST")
        self.assertEqual(200, resp.status)
        state = self.switches()
        self.assertEqual(("normal", 503), (state["orders"]["mode"], state["orders"]["failStatus"]))
        self.assertEqual({0}, {s["calls"]["total"] for s in state.values()})
        self.assertEqual(0, len(self.requests_of(NAME)))
        self.assertEqual(200, request(self.url("/customers/SWC-00001/orders")).status)

    def test_global_reset_restores_switches(self):
        self.switch(endpoint="customers", mode="fail")
        request(self.url("/customers"))
        request(self.base + "/__admin/reset", method="POST")
        state = self.switches()
        self.assertEqual("normal", state["customers"]["mode"])
        self.assertEqual(0, state["customers"]["calls"]["total"])


if __name__ == "__main__":
    unittest.main()
