# -*- coding: utf-8 -*-
"""C 类（请求与响应）与 D 类（依赖）场景自测。"""

import unittest
from urllib.parse import quote

from .support import ProviderTestCase, request, fetch_all, records_of


class EchoTest(ProviderTestCase):

    def test_plus_sign_survives_when_encoded_as_percent2b(self):
        item = self.endpoint("echo", "/echo")
        raw = "2026-09-10T14:30:00+08:00"
        # 正确编码：+ → %2B
        resp = request(item["url"] + "?since=" + quote(raw, safe=""))
        record = resp.json["data"]["items"][0]
        self.assertEqual(raw, record["since"])
        # 未编码的裸 + 会被服务端按表单规则解成空格（这正是后端要避免的）
        resp = request(item["url"] + "?since=" + raw)
        self.assertEqual("2026-09-10T14:30:00 08:00", resp.json["data"]["items"][0]["since"])

    def test_chinese_and_space_round_trip(self):
        item = self.endpoint("echo", "/echo")
        value = "中文 关键词 & = 符号"
        resp = request(item["url"], query={"q": value})
        self.assertEqual(value, resp.json["data"]["items"][0]["q"])

    def test_echo_returns_exactly_one_record(self):
        item = self.endpoint("echo", "/echo")
        self.assertEqual(1, item["expectedTotal"])
        rows = fetch_all(item)
        self.assertEqual(1, len(rows))

    def test_echo_body_reports_json_types(self):
        item = self.endpoint("echo", "/echo-body", method="POST")
        body = {"page": 1, "ratio": 1.5, "active": True, "keyword": "中文 关键词",
                "extra": None, "tags": ["a"], "nested": {"k": "v"}}
        resp = request(item["url"], method="POST", json_body=body)
        record = resp.json["data"]["items"][0]
        self.assertEqual(body, record["received_body"])
        self.assertEqual({"page": "integer", "ratio": "number", "active": "boolean",
                          "keyword": "string", "extra": "null", "tags": "array",
                          "nested": "object"}, record["types"])

    def test_echo_logs_decoded_query_in_admin(self):
        item = self.endpoint("echo", "/echo")
        request(item["url"] + "?since=" + quote("2026-09-10T14:30:00+08:00", safe=""))
        logged = self.requests_of("echo")[-1]
        self.assertEqual("2026-09-10T14:30:00+08:00", logged["query"]["since"])
        self.assertIn("%2B", logged["rawQuery"])


class ResponseShapeTest(ProviderTestCase):

    def test_single_object_record_path_yields_one_record(self):
        item = self.endpoint("single-object", "/profile")
        self.assertEqual("data", item["recordPath"])
        payload = request(item["url"]).json
        self.assertIsInstance(payload["data"], dict)
        self.assertEqual(1, len(records_of(payload, item["recordPath"])))
        self.assertEqual(1, item["expectedTotal"])

    def test_business_codes(self):
        cases = {"/ok-zero": 0, "/ok-200": 200, "/ok-string": "0", "/biz-fail": 5001}
        for path, expected in cases.items():
            with self.subTest(path=path):
                item = self.endpoint("biz-code", path)
                payload = request(item["url"]).json
                self.assertEqual(200, request(item["url"]).status)
                self.assertEqual(expected, payload["code"])
        self.assertIsInstance(request(self.endpoint("biz-code", "/ok-string")["url"]).json["code"], str)
        self.assertIsInstance(request(self.endpoint("biz-code", "/ok-zero")["url"]).json["code"], int)

    def test_biz_fail_carries_message_and_no_data(self):
        item = self.endpoint("biz-code", "/biz-fail")
        payload = request(item["url"]).json
        self.assertEqual("库存服务繁忙", payload["message"])
        self.assertIsNone(payload["data"])
        self.assertEqual(0, item["expectedTotal"])

    def test_nested_orders_have_object_field_and_child_array(self):
        item = self.endpoint("nested", "/orders")
        rows = fetch_all(item)
        self.assertEqual(item["expectedTotal"], len(rows))
        self.assertEqual(12, len(rows))
        for row in rows:
            self.assertIn("customer", row)
            self.assertIsInstance(row["customer"], dict)
            self.assertTrue(1 <= len(row["lines"]) <= 3)
        total_lines = sum(len(r["lines"]) for r in rows)
        self.assertIn(str(total_lines), item["note"])

    def test_record_field_types_are_rich(self):
        """每条记录都覆盖字符串/整数/小数/布尔/时间/嵌套对象，null 至少出现一次"""
        item = self.endpoint("page-number", "/products")
        rows = request(item["url"], query={"page": "1", "page_size": "10"}).json["data"]["items"]
        row = rows[0]
        self.assertIsInstance(row["name"], str)
        self.assertIsInstance(row["quantity"], int)
        self.assertIsInstance(row["price"], float)
        self.assertIsInstance(row["active"], bool)
        self.assertIsInstance(row["spec"], dict)
        self.assertRegex(row["updated_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertTrue(any(r["remark"] is None for r in rows))


class DependTest(ProviderTestCase):

    def test_path_dependency_sums_over_all_parents(self):
        parent = self.endpoint("depend-path", "/products")
        child = self.endpoint("depend-path", "/products/{sku}/batches")
        products = fetch_all(parent)
        self.assertEqual(30, len(products))
        total = 0
        counts = []
        for product in products:
            url = child["url"].replace("{sku}", product["sku"])
            payload = request(url).json
            rows = records_of(payload, child["recordPath"])
            counts.append(len(rows))
            total += len(rows)
            for row in rows:
                self.assertEqual(product["sku"], row["sku"])
        self.assertEqual(child["expectedTotal"], total)
        self.assertEqual(60, total)
        self.assertEqual([2, 4, 1, 3, 0], counts[:5], "批次条数公式变了，请同步 README")

    def test_unknown_sku_returns_404(self):
        child = self.endpoint("depend-path", "/products/{sku}/batches")
        resp = request(child["url"].replace("{sku}", "DPD-99999"))
        self.assertEqual(404, resp.status)
        self.assertEqual(404, resp.json["code"])

    def test_body_dependency_sums_over_all_parents(self):
        parent = self.endpoint("depend-body", "/customers")
        child = self.endpoint("depend-body", "/customer-orders/query", method="POST")
        customers = fetch_all(parent)
        self.assertEqual(12, len(customers))
        total = 0
        for customer in customers:
            payload = request(child["url"], method="POST",
                              json_body={"customerId": customer["id"]}).json
            rows = records_of(payload, child["recordPath"])
            total += len(rows)
            for row in rows:
                self.assertEqual(customer["id"], row["customer_id"])
        self.assertEqual(child["expectedTotal"], total)
        self.assertEqual(18, total)

    def test_missing_customer_id_returns_400(self):
        child = self.endpoint("depend-body", "/customer-orders/query", method="POST")
        resp = request(child["url"], method="POST", json_body={})
        self.assertEqual(400, resp.status)
        self.assertIn("customerId", resp.json["message"])

    def test_unknown_customer_returns_empty_list(self):
        child = self.endpoint("depend-body", "/customer-orders/query", method="POST")
        payload = request(child["url"], method="POST",
                          json_body={"customerId": "CUS-99999"}).json
        self.assertEqual([], payload["data"]["items"])


if __name__ == "__main__":
    unittest.main()
