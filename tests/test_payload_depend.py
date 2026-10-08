# -*- coding: utf-8 -*-
"""C 类（请求与响应）与 D 类（依赖）场景自测。"""

import unittest
from urllib.parse import quote

from .support import ProviderTestCase, request, fetch_all, records_of, build_auth


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


class RequiredBusinessParamTest(ProviderTestCase):

    SCENARIO = "required-business-param"
    PATH = "/customers/{customerId}"

    def _call(self, customer_id, query):
        item = self.endpoint(self.SCENARIO, self.PATH)
        auth = build_auth(self.catalog()[self.SCENARIO])
        url = item["url"].replace("{customerId}", customer_id)
        return request(url, headers=auth.headers, query=dict(auth.query, **query))

    def test_all_values_present_returns_one_record_echoing_tenant(self):
        item = self.endpoint(self.SCENARIO, self.PATH)
        resp = self._call("RBP-CUS-00002", {"tenant": "t-abc"})
        self.assertEqual(200, resp.status, resp.text[:200])
        rows = records_of(resp.json, item["recordPath"])
        self.assertEqual(1, len(rows))
        self.assertEqual("RBP-CUS-00002", rows[0]["id"])
        self.assertEqual("t-abc", rows[0]["tenant"])

    def test_catalog_test_values_reproduce_expected_record(self):
        item = self.endpoint(self.SCENARIO, self.PATH)
        values = {v["name"]: v["testValue"] for v in item["requiredTestValues"]}
        resp = self._call(values["customerId"], {"tenant": values["tenant"]})
        self.assertEqual(200, resp.status)
        self.assertEqual(item["sampleFirst3"], records_of(resp.json, item["recordPath"]))
        self.assertEqual(1, item["expectedTotal"])

    def test_missing_tenant_returns_400(self):
        for query in ({}, {"tenant": ""}):
            with self.subTest(query=query):
                resp = self._call("RBP-CUS-00001", query)
                self.assertEqual(400, resp.status)
                self.assertIn("tenant", resp.json["message"])

    def test_unknown_customer_returns_404(self):
        resp = self._call("RBP-CUS-99999", {"tenant": "t-abc"})
        self.assertEqual(404, resp.status)

    def test_missing_api_key_returns_401(self):
        item = self.endpoint(self.SCENARIO, self.PATH)
        url = item["url"].replace("{customerId}", "RBP-CUS-00001")
        self.assertEqual(401, request(url, query={"tenant": "t-abc"}).status)

    def test_doc_params_are_required_without_sample_values(self):
        """两个参数都必须是 required，且不带任何可能被当成固定值的样例 / 默认值"""
        doc = request(self.catalog()[self.SCENARIO]["openapiUrl"]).json
        self.assertEqual(1, sum(len(ops) for ops in doc["paths"].values()), "应只有一个接口")
        params = {p["name"]: p for p in doc["paths"][self.PATH]["get"]["parameters"]}
        self.assertEqual({"customerId": "path", "tenant": "query"},
                         {k: v["in"] for k, v in params.items()})
        sample_keys = {"example", "examples", "default", "x-example", "enum"}
        for name, param in params.items():
            with self.subTest(param=name):
                self.assertTrue(param["required"])
                self.assertFalse(sample_keys & set(param), param)
                self.assertFalse(sample_keys & set(param["schema"]), param)


class DottedParamTest(ProviderTestCase):

    SCENARIO = "dotted-param"
    PATH = "/items"

    def _call(self, query):
        item = self.endpoint(self.SCENARIO, self.PATH)
        auth = build_auth(self.catalog()[self.SCENARIO])
        return request(item["url"], headers=auth.headers, query=dict(auth.query, **query))

    def test_all_values_present_echo_dotted_params(self):
        item = self.endpoint(self.SCENARIO, self.PATH)
        resp = self._call({"filter.name": "名称 a+b", "page.size": "15"})
        self.assertEqual(200, resp.status, resp.text[:200])
        rows = records_of(resp.json, item["recordPath"])
        self.assertEqual(3, len(rows))
        for row in rows:
            self.assertEqual("名称 a+b", row["filterName"])
            self.assertEqual(15, row["pageSize"])

    def test_optional_page_size_absent_echoes_null(self):
        item = self.endpoint(self.SCENARIO, self.PATH)
        values = {v["name"]: v["testValue"] for v in item["requiredTestValues"]}
        self.assertEqual(["filter.name"], list(values))
        resp = self._call(values)
        self.assertEqual(200, resp.status)
        rows = records_of(resp.json, item["recordPath"])
        self.assertTrue(all(r["pageSize"] is None for r in rows))
        self.assertEqual(item["sampleFirst3"], rows)
        self.assertEqual(3, item["expectedTotal"])

    def test_missing_filter_name_returns_400(self):
        # 带的是「filter[name]」或嵌套写法时同样视为缺失
        for query in ({}, {"filter.name": ""}, {"filter[name]": "x"}, {"name": "x"}):
            with self.subTest(query=query):
                resp = self._call(query)
                self.assertEqual(400, resp.status)
                self.assertIn("filter.name", resp.json["message"])

    def test_non_integer_page_size_returns_400(self):
        resp = self._call({"filter.name": "x", "page.size": "abc"})
        self.assertEqual(400, resp.status)
        self.assertIn("page.size", resp.json["message"])

    def test_missing_api_key_returns_401(self):
        item = self.endpoint(self.SCENARIO, self.PATH)
        self.assertEqual(401, request(item["url"], query={"filter.name": "x"}).status)

    def test_doc_params_keep_dotted_names_without_sample_values(self):
        doc = request(self.catalog()[self.SCENARIO]["openapiUrl"]).json
        self.assertEqual(1, sum(len(ops) for ops in doc["paths"].values()), "应只有一个接口")
        params = {p["name"]: p for p in doc["paths"][self.PATH]["get"]["parameters"]}
        self.assertEqual({"filter.name": True, "page.size": False},
                         {k: v["required"] for k, v in params.items()})
        self.assertEqual("integer", params["page.size"]["schema"]["type"])
        sample_keys = {"example", "examples", "default", "x-example", "enum"}
        for name, param in params.items():
            with self.subTest(param=name):
                self.assertEqual("query", param["in"])
                self.assertFalse(sample_keys & set(param), param)
                self.assertFalse(sample_keys & set(param["schema"]), param)


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


class SharedParamsTest(ProviderTestCase):
    """公共参数：components.parameters + $ref（操作级 / 路径项级），内联参数不是公共参数"""

    SCENARIO = "shared-params"
    FULL = {"headers": {"X-Tenant-Id": "t-9"}, "query": {"locale": "en-US", "apiVersion": "3"}}

    def _call(self, path, headers=None, query=None):
        return request(self.endpoint(self.SCENARIO, path)["url"], headers=headers, query=query)

    def _doc(self):
        return request(self.catalog()[self.SCENARIO]["openapiUrl"]).json

    def test_doc_defines_three_shared_parameters(self):
        params = self._doc()["components"]["parameters"]
        self.assertEqual({"TenantId", "Locale", "ApiVersion"}, set(params))
        self.assertEqual(("header", "X-Tenant-Id", "string"),
                         (params["TenantId"]["in"], params["TenantId"]["name"],
                          params["TenantId"]["schema"]["type"]))
        self.assertEqual(("query", "locale", ["zh-CN", "en-US"]),
                         (params["Locale"]["in"], params["Locale"]["name"],
                          params["Locale"]["schema"]["enum"]))
        self.assertEqual(("query", "apiVersion", "integer"),
                         (params["ApiVersion"]["in"], params["ApiVersion"]["name"],
                          params["ApiVersion"]["schema"]["type"]))
        for param in params.values():
            self.assertTrue(param["required"])
            self.assertFalse({"example", "default"} & (set(param) | set(param["schema"])))

    def test_doc_reference_shapes(self):
        paths = self._doc()["paths"]
        ref = lambda name: {"$ref": "#/components/parameters/" + name}
        all_three = [ref("TenantId"), ref("Locale"), ref("ApiVersion")]
        inline = lambda path: [p for p in paths[path]["get"].get("parameters", []) if "$ref" not in p]
        refs = lambda path: [p for p in paths[path]["get"].get("parameters", []) if "$ref" in p]

        # /orders：操作级引用全部，另有内联 keyword
        self.assertNotIn("parameters", paths["/orders"])
        self.assertEqual(all_three, refs("/orders"))
        self.assertEqual(["keyword"], [p["name"] for p in inline("/orders")])
        # /customers：路径项级引用全部，操作级只有内联 keyword
        self.assertEqual(all_three, paths["/customers"]["parameters"])
        self.assertEqual([], refs("/customers"))
        self.assertEqual(["keyword"], [p["name"] for p in inline("/customers")])
        # /products：只引用 Locale
        self.assertNotIn("parameters", paths["/products"])
        self.assertEqual([ref("Locale")], paths["/products"]["get"]["parameters"])
        # /warehouses：不引用；内联一个与公共参数同名、但定义不同的 locale
        self.assertNotIn("parameters", paths["/warehouses"])
        self.assertEqual([], refs("/warehouses"))
        (own,) = inline("/warehouses")
        self.assertEqual(("locale", "query", False), (own["name"], own["in"], own["required"]))
        self.assertNotIn("enum", own["schema"])
        # keyword 在两个接口里重复出现，但没有被提成公共参数
        self.assertNotIn("keyword", [p["name"] for p in self._doc()["components"]["parameters"].values()])
        self.assertFalse(inline("/orders")[0]["required"])

    def test_catalog_states_reference_location(self):
        expect = {"/orders": ("operation", 3, ["keyword"]), "/customers": ("pathItem", 3, ["keyword"]),
                  "/products": ("operation", 1, []), "/warehouses": (None, 0, ["locale"])}
        for path, (location, ref_count, inline) in expect.items():
            with self.subTest(path=path):
                item = self.endpoint(self.SCENARIO, path)
                shared = item["sharedParameters"]
                self.assertEqual(location, shared["refLocation"])
                self.assertEqual(ref_count, len(shared["refs"]))
                self.assertEqual(inline, [p["name"] for p in shared["inlineParameters"]])
                self.assertEqual([r["name"] for r in shared["refs"]],
                                 [v["name"] for v in item["requiredTestValues"]])

    def test_catalog_test_values_reproduce_sample(self):
        for path in ("/orders", "/customers", "/products", "/warehouses"):
            with self.subTest(path=path):
                item = self.endpoint(self.SCENARIO, path)
                headers = {v["name"]: v["testValue"] for v in item["requiredTestValues"]
                           if v["in"] == "HEADER"}
                query = {v["name"]: v["testValue"] for v in item["requiredTestValues"]
                         if v["in"] == "QUERY"}
                resp = request(item["url"], headers=headers, query=query)
                self.assertEqual(200, resp.status, resp.text[:200])
                rows = records_of(resp.json, item["recordPath"])
                self.assertEqual(item["expectedTotal"], len(rows))
                self.assertEqual(item["sampleFirst3"], rows[:3])

    def test_full_references_echo_values(self):
        for path in ("/orders", "/customers"):
            with self.subTest(path=path):
                resp = self._call(path, **self.FULL)
                self.assertEqual(200, resp.status, resp.text[:200])
                rows = resp.json["data"]["items"]
                self.assertEqual(3, len(rows))
                for row in rows:
                    self.assertEqual(("t-9", "en-US", 3),
                                     (row["tenantId"], row["locale"], row["apiVersion"]))

    def test_missing_or_invalid_shared_param_returns_400(self):
        cases = [
            ({"headers": {}, "query": self.FULL["query"]}, "X-Tenant-Id"),
            ({"headers": self.FULL["headers"], "query": {"apiVersion": "3"}}, "locale"),
            ({"headers": self.FULL["headers"], "query": {"locale": "en-US"}}, "apiVersion"),
            ({"headers": self.FULL["headers"], "query": {"locale": "fr-FR", "apiVersion": "3"}}, "枚举"),
            ({"headers": self.FULL["headers"], "query": {"locale": "en-US", "apiVersion": "v3"}}, "整数"),
        ]
        for path in ("/orders", "/customers"):
            for kwargs, keyword in cases:
                with self.subTest(path=path, expect=keyword):
                    resp = self._call(path, **kwargs)
                    self.assertEqual(400, resp.status, resp.text[:200])
                    self.assertEqual(400, resp.json["code"])
                    self.assertIn(keyword, resp.json["message"])
        resp = self._call("/orders", headers=self.FULL["headers"],
                          query={"locale": "fr-FR", "apiVersion": "3"})
        self.assertIn("fr-FR", resp.json["message"])
        self.assertIn("zh-CN / en-US", resp.json["message"])

    def test_partial_reference_only_needs_locale(self):
        resp = self._call("/products", query={"locale": "zh-CN"})
        self.assertEqual(200, resp.status)
        for row in resp.json["data"]["items"]:
            self.assertEqual((None, "zh-CN", None),
                             (row["tenantId"], row["locale"], row["apiVersion"]))
        self.assertEqual(400, self._call("/products").status)
        self.assertEqual(400, self._call("/products", query={"locale": "ja-JP"}).status)

    def test_inline_locale_is_not_validated_as_shared(self):
        # 不传、传枚举外的值都 200：它不是公共参数，没有必填与枚举限制
        for query, expected in (({}, None), ({"locale": "fr-FR"}, "fr-FR"), ({"locale": "zh-CN"}, "zh-CN")):
            with self.subTest(query=query):
                resp = self._call("/warehouses", query=query)
                self.assertEqual(200, resp.status, resp.text[:200])
                rows = resp.json["data"]["items"]
                self.assertEqual(3, len(rows))
                self.assertTrue(all(r["locale"] == expected and r["tenantId"] is None for r in rows))

    def test_inline_keyword_filters_and_is_optional(self):
        for path, prefix in (("/orders", "SP-ORD"), ("/customers", "SP-CUS")):
            with self.subTest(path=path):
                resp = self._call(path, headers=self.FULL["headers"],
                                  query=dict(self.FULL["query"], keyword="物料2"))
                self.assertEqual(200, resp.status)
                self.assertEqual([prefix + "-00002"], [r["id"] for r in resp.json["data"]["items"]])
        # 没声明 keyword 的接口不受它影响
        resp = self._call("/products", query={"locale": "zh-CN", "keyword": "物料2"})
        self.assertEqual(3, len(resp.json["data"]["items"]))


class RootObjectRecordTest(ProviderTestCase):

    def test_root_object_is_one_record_with_array_fields(self):
        item = self.endpoint("root-object-record", "/order")
        resp = request(item["url"])
        self.assertEqual(200, resp.status)
        payload = resp.json
        self.assertIsInstance(payload, dict)
        self.assertNotIn("data", payload)
        self.assertEqual("", item["recordPath"])
        self.assertEqual(1, item["expectedTotal"])
        self.assertEqual([payload], records_of(payload, item["recordPath"]))
        self.assertEqual([payload], item["sampleFirst3"])
        self.assertEqual("ROOT-ORD-00001", payload[item["primaryKey"]])
        self.assertEqual({"tags": "SCALAR_ARRAY", "lines": "OBJECT_ARRAY"}, item["arrayFields"])
        self.assertEqual(["a", "b"], payload["tags"])
        self.assertEqual(2, len(payload["lines"]))
        self.assertTrue(all(isinstance(line, dict) for line in payload["lines"]))
        self.assertIsNone(payload["remark"])
        self.assertIs(False, payload["urgent"])

    def test_doc_declares_root_object_with_both_array_kinds(self):
        doc = request(self.catalog()["root-object-record"]["openapiUrl"]).json
        schema = doc["paths"]["/order"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual("object", schema["type"])
        props = schema["properties"]
        self.assertEqual({"type": "array", "items": {"type": "string"}}, props["tags"])
        self.assertEqual("array", props["lines"]["type"])
        self.assertEqual("object", props["lines"]["items"]["type"])
        self.assertEqual({"lineNo", "sku", "quantity", "price"},
                         set(props["lines"]["items"]["properties"]))
        self.assertEqual("number", props["amount"]["type"])
        self.assertEqual("boolean", props["urgent"]["type"])


class FormBodyPostTest(ProviderTestCase):

    FORM = {"Content-Type": "application/x-www-form-urlencoded"}

    def _item(self):
        return self.endpoint("form-body-post", "/orders/search", "POST")

    def test_form_submission_returns_rows(self):
        item = self._item()
        self.assertEqual("UNSUPPORTED", item["expectedBackendSupport"])
        resp = request(item["url"], method="POST", headers=self.FORM, body="")
        self.assertEqual(200, resp.status, resp.text[:200])
        rows = records_of(resp.json, item["recordPath"])
        self.assertEqual(item["expectedTotal"], len(rows))
        self.assertEqual(6, len(rows))
        resp = request(item["url"], method="POST", body="status=PAID",
                       headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
        self.assertEqual(200, resp.status)
        self.assertEqual({"PAID"}, {r["status"] for r in resp.json["data"]["items"]})
        self.assertEqual(2, len(resp.json["data"]["items"]))

    def test_json_or_missing_content_type_returns_415(self):
        item = self._item()
        resp = request(item["url"], method="POST", json_body={"status": "PAID"})
        self.assertEqual(415, resp.status)
        self.assertEqual(415, resp.json["code"])
        self.assertIn("application/x-www-form-urlencoded", resp.json["message"])
        self.assertEqual(415, request(item["url"], method="POST", body="status=PAID",
                                      headers={"Content-Type": "text/plain"}).status)

    def test_status_outside_enum_returns_400(self):
        resp = request(self._item()["url"], method="POST", headers=self.FORM, body="status=LOST")
        self.assertEqual(400, resp.status)
        self.assertIn("status", resp.json["message"])

    def test_doc_declares_only_form_request_body(self):
        doc = request(self.catalog()["form-body-post"]["openapiUrl"]).json
        body = doc["paths"]["/orders/search"]["post"]["requestBody"]
        self.assertEqual(["application/x-www-form-urlencoded"], list(body["content"]))
        props = body["content"]["application/x-www-form-urlencoded"]["schema"]["properties"]
        self.assertEqual({"keyword", "status"}, set(props))
        self.assertEqual(["NEW", "PAID", "SHIPPED"], props["status"]["enum"])


if __name__ == "__main__":
    unittest.main()
