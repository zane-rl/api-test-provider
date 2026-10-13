# -*- coding: utf-8 -*-
"""H-VERIFY-1013 补测场景自测（10 个场景）：

A 组 `token-typed-fields` / `oauth2-scope-required` / `token-slow`，
C 组 `nested-two-level` / `special-field-names`，
D 组 `required-header-cookie` / `depend-empty-parent` / `depend-mixed-key`，
E 组 `depend-two-roots` / `page-fail-midway`。
"""

import time
import unittest
from urllib.parse import urlencode

from scenarios import auth as auth_module

from .support import ProviderTestCase, request, records_of, build_auth, fetch_all, extract

try:
    import yaml      # 只在自测里用来回读
except ImportError:  # pragma: no cover
    yaml = None

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


class _Base(ProviderTestCase):

    def url(self, scenario, path):
        return "%s/s/%s%s" % (self.base, scenario, path)

    def rows(self, scenario, path, **kwargs):
        resp = request(self.url(scenario, path), **kwargs)
        self.assertEqual(200, resp.status, resp.text)
        return records_of(resp.json, "data.items")

    def doc(self, scenario):
        resp = request(self.url(scenario, "/openapi.json"))
        self.assertEqual(200, resp.status)
        return resp.json


# ==================== A 组 ====================

class TokenTypedFieldsTest(_Base):
    NAME = "token-typed-fields"

    def test_token_response_carries_boolean_and_decimal_fields(self):
        flow = self.scenario(self.NAME)["auth"]["tokenFlow"]
        resp = request(flow["url"], method="POST", json_body=flow["requestBody"])
        self.assertEqual(200, resp.status, resp.text)
        payload = resp.json
        self.assertIs(True, payload["success"])
        self.assertIs(True, payload["data"]["refreshable"])
        self.assertEqual(0.8, payload["data"]["refreshRatio"])
        self.assertIsInstance(payload["data"]["refreshRatio"], float)
        self.assertEqual(3600, payload["data"]["expiresIn"])
        self.assertIsInstance(extract(payload, flow["tokenPath"]), str)
        self.assertEqual("data.expiresIn", flow["expiresInPath"])
        # 清单里列出的非文本字段，类型与真实响应一致
        kinds = {"boolean": bool, "number": float, "integer": int}
        for kind, paths in flow["nonTextTokenResponseFields"].items():
            for path in paths:
                self.assertIs(kinds[kind], type(extract(payload, path)), path)

    def test_doc_declares_the_extra_fields_with_their_types(self):
        op = self.doc(self.NAME)["paths"]["/login"]["post"]
        content = op["responses"]["200"]["content"]["application/json"]
        props = content["schema"]["properties"]
        self.assertEqual("boolean", props["success"]["type"])
        data = props["data"]["properties"]
        self.assertEqual({"accessToken": "string", "expiresIn": "integer", "tokenType": "string",
                          "refreshable": "boolean", "refreshRatio": "number"},
                         {k: v["type"] for k, v in data.items()})
        self.assertIs(True, content["example"]["data"]["refreshable"])
        self.assertEqual(0.8, content["example"]["data"]["refreshRatio"])

    def test_business_endpoint_checks_the_issued_token(self):
        entry = self.scenario(self.NAME)
        self.assertEqual(401, request(self.url(self.NAME, "/items")).status)
        self.assertEqual(401, request(self.url(self.NAME, "/login"), method="POST",
                                      json_body={"username": "x", "password": "y"}).status)
        auth = build_auth(entry)
        self.assertEqual(20, len(self.rows(self.NAME, "/items", headers=auth.headers)))

    def test_bearer_request_login_response_is_unchanged(self):
        flow = self.scenario("bearer-request")["auth"]["tokenFlow"]
        payload = request(flow["url"], method="POST", json_body=flow["requestBody"]).json
        self.assertEqual({"code", "message", "data"}, set(payload))
        self.assertEqual({"accessToken", "expiresIn", "tokenType"}, set(payload["data"]))


class OAuth2ScopeRequiredTest(_Base):
    NAME = "oauth2-scope-required"

    def token(self, **overrides):
        flow = self.scenario(self.NAME)["auth"]["tokenFlow"]
        form = {"grant_type": "client_credentials", "client_id": flow["clientId"],
                "client_secret": flow["clientSecret"], "scope": flow["scope"]}
        for key, value in overrides.items():
            if value is None:
                form.pop(key, None)
            else:
                form[key] = value
        return request(flow["url"], method="POST", headers=FORM, body=urlencode(form))

    def test_correct_scope_issues_a_usable_token(self):
        resp = self.token()
        self.assertEqual(200, resp.status, resp.text)
        self.assertEqual("read", resp.json["scope"])
        self.assertEqual("Bearer", resp.json["token_type"])
        rows = self.rows(self.NAME, "/items",
                         headers={"Authorization": "Bearer " + resp.json["access_token"]})
        self.assertEqual(20, len(rows))

    def test_missing_or_wrong_scope_is_rejected_with_invalid_scope(self):
        for label, scope in (("missing", None), ("empty", ""), ("other", "write"),
                             ("superset", "read write"), ("case", "READ")):
            with self.subTest(scope=label):
                resp = self.token(scope=scope)
                self.assertEqual(400, resp.status, resp.text)
                self.assertEqual("invalid_scope", resp.json["error"])
                self.assertTrue(resp.json["error_description"])
                self.assertNotIn("access_token", resp.json)
        # 被拒绝的请求不签发令牌
        state = request(self.base + "/__admin/state").json
        self.assertFalse([t for t, rec in state["issuedTokens"].items()
                          if rec["scenario"] == self.NAME])

    def test_client_credentials_are_checked_before_scope(self):
        resp = self.token(client_secret="wrong", scope="write")
        self.assertEqual(401, resp.status)
        self.assertEqual("invalid_client", resp.json["error"])
        resp = self.token(grant_type="password")
        self.assertEqual(400, resp.status)
        self.assertEqual("unsupported_grant_type", resp.json["error"])

    def test_doc_and_catalog_declare_the_required_scope(self):
        doc = self.doc(self.NAME)
        scheme = doc["components"]["securitySchemes"]["tokenAuth"]
        self.assertEqual({"read"}, set(scheme["flows"]["clientCredentials"]["scopes"]))
        body = doc["paths"]["/oauth/token"]["post"]["requestBody"]["content"][
            "application/x-www-form-urlencoded"]
        self.assertIn("scope", body["schema"]["required"])
        self.assertEqual("read", body["example"]["scope"])
        self.assertEqual("read", self.scenario(self.NAME)["auth"]["tokenFlow"]["scope"])

    def test_other_oauth2_scenarios_still_ignore_scope(self):
        flow = self.scenario("oauth2-post")["auth"]["tokenFlow"]
        form = {"grant_type": "client_credentials", "client_id": flow["clientId"],
                "client_secret": flow["clientSecret"]}
        self.assertEqual(200, request(flow["url"], method="POST", headers=FORM,
                                      body=urlencode(form)).status)
        form["scope"] = "anything"
        resp = request(flow["url"], method="POST", headers=FORM, body=urlencode(form))
        self.assertEqual(200, resp.status)
        self.assertEqual("anything", resp.json["scope"])
        doc = self.doc("oauth2-post")
        body = doc["paths"]["/oauth/token"]["post"]["requestBody"]["content"][
            "application/x-www-form-urlencoded"]
        self.assertNotIn("scope", body["schema"]["required"])


class TokenSlowTest(_Base):
    NAME = "token-slow"

    def test_default_delay_is_five_seconds(self):
        self.assertEqual(5, auth_module.TOKEN_SLOW_DEFAULT_SECONDS)
        self.assertEqual((0, 60), auth_module.CONFIG_BOUNDS["tokenSlowDelaySeconds"])

    def test_login_is_delayed_by_configured_seconds(self):
        flow = self.scenario(self.NAME)["auth"]["tokenFlow"]
        self.assertEqual(0, flow["tokenDelaySeconds"])      # 自测基类把它调成了 0
        self.set_config(tokenSlowDelaySeconds=1)
        self.addCleanup(self.set_config, tokenSlowDelaySeconds=0)
        self.assertEqual(1, self.scenario(self.NAME)["auth"]["tokenFlow"]["tokenDelaySeconds"])
        started = time.monotonic()
        resp = request(flow["url"], method="POST", json_body=flow["requestBody"])
        elapsed = time.monotonic() - started
        self.assertEqual(200, resp.status, resp.text)
        self.assertGreaterEqual(elapsed, 1.0)
        self.assertEqual({"accessToken", "expiresIn", "tokenType"}, set(resp.json["data"]))
        # 账号密码错误立即返回，不延迟
        started = time.monotonic()
        resp = request(flow["url"], method="POST", json_body={"username": "x", "password": "y"})
        self.assertEqual(401, resp.status)
        self.assertLess(time.monotonic() - started, 0.9)
        # 业务接口与取文档都不延迟
        token = request(flow["url"], method="POST", json_body=flow["requestBody"]).json
        started = time.monotonic()
        rows = self.rows(self.NAME, "/items",
                         headers={"Authorization": "Bearer " + token["data"]["accessToken"]})
        self.doc(self.NAME)
        self.assertEqual(20, len(rows))
        self.assertLess(time.monotonic() - started, 0.9)

    def test_delay_only_affects_this_scenario(self):
        self.set_config(tokenSlowDelaySeconds=1)
        self.addCleanup(self.set_config, tokenSlowDelaySeconds=0)
        flow = self.scenario("bearer-request")["auth"]["tokenFlow"]
        started = time.monotonic()
        self.assertEqual(200, request(flow["url"], method="POST",
                                      json_body=flow["requestBody"]).status)
        self.assertLess(time.monotonic() - started, 0.9)

    def test_delay_bounds(self):
        resp = request(self.base + "/__admin/config", method="POST",
                       json_body={"tokenSlowDelaySeconds": 61})
        self.assertEqual(400, resp.status)


# ==================== C 组 ====================

class NestedTwoLevelTest(_Base):
    NAME = "nested-two-level"

    def test_two_levels_of_object_arrays_with_unique_keys(self):
        item = self.endpoint(self.NAME, "/orders")
        orders = self.rows(self.NAME, "/orders")
        self.assertEqual(["NTL-0000%d" % i for i in (1, 2, 3)], [o["order_no"] for o in orders])
        self.assertEqual(item["expectedTotal"], len(orders))
        lines = [x for o in orders for x in o["lines"]]
        batches = [b for x in lines for b in x["batches"]]
        self.assertEqual([2, 3, 1], [len(o["lines"]) for o in orders])
        self.assertEqual(6, len(lines))
        self.assertEqual(9, len(batches))
        self.assertTrue(all(1 <= len(x["batches"]) <= 2 for x in lines))
        self.assertEqual(6, len({x["line_no"] for x in lines}))
        self.assertEqual(9, len({b["batch_no"] for b in batches}))
        for order in orders:
            for line in order["lines"]:
                self.assertTrue(line["line_no"].startswith(order["order_no"] + "-L"))
                for batch in line["batches"]:
                    self.assertTrue(batch["batch_no"].startswith(line["line_no"] + "-B"))
        nested = {n["path"]: n for n in item["nestedArrays"]}
        self.assertEqual({"lines": "OBJECT_ARRAY", "lines.batches": "OBJECT_ARRAY"},
                         item["arrayFields"])
        self.assertEqual(("line_no", 6), (nested["lines"]["primaryKey"],
                                          nested["lines"]["expectedTotal"]))
        self.assertEqual(("batch_no", 9), (nested["lines.batches"]["primaryKey"],
                                           nested["lines.batches"]["expectedTotal"]))
        self.assertEqual([len(x["batches"]) for x in lines],
                         nested["lines.batches"]["countsPerParent"])

    def test_doc_schema_describes_both_levels(self):
        op = self.doc(self.NAME)["paths"]["/orders"]["get"]
        content = op["responses"]["200"]["content"]["application/json"]
        order = content["schema"]["properties"]["data"]["properties"]["items"]["items"]
        line = order["properties"]["lines"]
        self.assertEqual("array", line["type"])
        batch = line["items"]["properties"]["batches"]
        self.assertEqual("array", batch["type"])
        self.assertEqual({"batch_no", "qty", "produced_on"}, set(batch["items"]["properties"]))
        self.assertIn("batch_no", content["example"]["data"]["items"][0]["lines"][0]["batches"][0])


class SpecialFieldNamesTest(_Base):
    NAME = "special-field-names"
    ARRAYS = {"sku-list": ["sku-code", "库存", "1st_price"],
              "规格列表": ["spec-name", "取值", "2nd_unit"],
              "3d_models": ["model-id", "格式", "3d_size"]}

    def test_records_use_the_special_names(self):
        item = self.endpoint(self.NAME, "/products")
        rows = self.rows(self.NAME, "/products")
        self.assertEqual(3, len(rows))
        self.assertEqual(item["expectedTotal"], len(rows))
        nested = {n["path"]: n for n in item["nestedArrays"]}
        self.assertEqual(set(self.ARRAYS), set(nested))
        for name, children in self.ARRAYS.items():
            with self.subTest(array=name):
                elements = [e for r in rows for e in r[name]]
                self.assertEqual(6, len(elements))
                self.assertEqual(6, nested[name]["expectedTotal"])
                self.assertTrue(all(set(e) == set(children) for e in elements))
                key = nested[name]["primaryKey"]
                self.assertEqual(children[0], key)
                self.assertEqual(6, len({e[key] for e in elements}))
        # 每个数组的子字段里三种情形各一个
        for children in self.ARRAYS.values():
            self.assertEqual(1, sum("-" in c for c in children))
            self.assertEqual(1, sum(c[0].isdigit() for c in children))
            self.assertEqual(1, sum(any(ord(ch) > 127 for ch in c) for c in children))

    def test_doc_keeps_the_names_in_every_format(self):
        doc = self.doc(self.NAME)
        record = doc["paths"]["/products"]["get"]["responses"]["200"]["content"][
            "application/json"]["schema"]["properties"]["data"]["properties"]["items"]["items"]
        for name, children in self.ARRAYS.items():
            self.assertEqual(set(children), set(record["properties"][name]["items"]["properties"]))
        text = request(self.url(self.NAME, "/openapi.yaml")).text
        for name in list(self.ARRAYS) + [c for cs in self.ARRAYS.values() for c in cs]:
            self.assertIn(name, text)
        if yaml is not None:
            self.assertEqual(doc, yaml.safe_load(text))


# ==================== D 组 ====================

class RequiredHeaderCookieTest(_Base):
    NAME = "required-header-cookie"

    def test_missing_header_or_cookie_returns_400(self):
        resp = request(self.url(self.NAME, "/invoices"))
        self.assertEqual(400, resp.status)
        self.assertIn("X-Tenant-Id", resp.json["message"])
        self.assertEqual(400, request(self.url(self.NAME, "/invoices"),
                                      headers={"X-Tenant-Id": ""}).status)
        resp = request(self.url(self.NAME, "/quotas"))
        self.assertEqual(400, resp.status)
        self.assertIn("region", resp.json["message"])
        # 放错位置不算：Cookie 参数放到请求头 / Query 里仍然 400
        self.assertEqual(400, request(self.url(self.NAME, "/quotas"),
                                      headers={"region": "cn-east"},
                                      query={"region": "cn-east"}).status)
        self.assertEqual(400, request(self.url(self.NAME, "/invoices"),
                                      query={"X-Tenant-Id": "RHC-00001"},
                                      cookies={"X-Tenant-Id": "RHC-00001"}).status)

    def test_values_from_the_parent_reach_expected_totals(self):
        tenants = self.rows(self.NAME, "/tenants")
        self.assertEqual(["RHC-0000%d" % i for i in (1, 2, 3)], [t["id"] for t in tenants])
        self.assertEqual(3, len({t["region"] for t in tenants}))
        invoices, quotas, invoice_counts, quota_counts = [], [], [], []
        for tenant in tenants:
            own = self.rows(self.NAME, "/invoices", headers={"X-Tenant-Id": tenant["id"]})
            self.assertTrue(all(x["tenant_id"] == tenant["id"] for x in own))
            invoice_counts.append(len(own))
            invoices += own
            own = self.rows(self.NAME, "/quotas", cookies={"region": tenant["region"]})
            self.assertTrue(all(x["region"] == tenant["region"] for x in own))
            quota_counts.append(len(own))
            quotas += own
        self.assertEqual([2, 1, 3], invoice_counts)
        self.assertEqual([1, 2, 1], quota_counts)
        for path, got, key in (("/invoices", invoices, "invoice_no"), ("/quotas", quotas, "quota_id")):
            item = self.endpoint(self.NAME, path)
            self.assertEqual(item["expectedTotal"], len(got))
            self.assertEqual(key, item["primaryKey"])
            self.assertEqual(len(got), len({r[key] for r in got}))
        self.assertEqual([], self.rows(self.NAME, "/invoices", headers={"X-Tenant-Id": "nope"}))
        self.assertEqual([], self.rows(self.NAME, "/quotas", cookies={"region": "nope"}))

    def test_doc_declares_required_params_without_default_or_example(self):
        doc = self.doc(self.NAME)
        for path, location, name in (("/invoices", "header", "X-Tenant-Id"),
                                     ("/quotas", "cookie", "region")):
            with self.subTest(path=path):
                params = doc["paths"][path]["get"]["parameters"]
                self.assertEqual(1, len(params))
                param = params[0]
                self.assertEqual((name, location, True),
                                 (param["name"], param["in"], param["required"]))
                self.assertEqual({"type": "string"}, param["schema"])
                self.assertNotIn("example", param)
                self.assertNotIn("default", param["schema"])
        self.assertNotIn("securitySchemes", doc["components"])

    def test_catalog_declares_dependency_and_param_location(self):
        invoices = self.endpoint(self.NAME, "/invoices")
        quotas = self.endpoint(self.NAME, "/quotas")
        self.assertEqual({"path": "/tenants", "field": "id",
                          "param": {"name": "X-Tenant-Id", "in": "HEADER"}}, invoices["dependsOn"])
        self.assertEqual({"path": "/tenants", "field": "region",
                          "param": {"name": "region", "in": "COOKIE"}}, quotas["dependsOn"])


class DependEmptyParentTest(_Base):
    NAME = "depend-empty-parent"

    def test_parent_always_returns_zero_records(self):
        resp = request(self.url(self.NAME, "/categories"))
        self.assertEqual(200, resp.status)
        self.assertEqual({"code": 0, "message": "ok", "data": {"items": [], "total": 0}}, resp.json)
        item = self.endpoint(self.NAME, "/categories")
        self.assertEqual(0, item["expectedTotal"])
        self.assertEqual([], item["sampleFirst3"])

    def test_child_works_when_called_directly_with_a_valid_value(self):
        item = self.endpoint(self.NAME, "/categories/{categoryId}/products")
        self.assertEqual(0, item["expectedTotal"])
        self.assertEqual(2, item["directCallTotal"])
        values = item["validPathValues"]["categoryId"]
        self.assertEqual(3, len(values))
        self.assertIn(item["pathSample"]["categoryId"], values)
        keys = set()
        for value in values:
            rows = self.rows(self.NAME, "/categories/%s/products" % value)
            self.assertEqual(item["directCallTotal"], len(rows))
            self.assertTrue(all(r["category_id"] == value for r in rows))
            keys |= {r["product_id"] for r in rows}
        self.assertEqual(6, len(keys))
        self.assertEqual(404, request(self.url(self.NAME, "/categories/nope/products")).status)

    def test_doc_declares_parent_record_fields_explicitly(self):
        doc = self.doc(self.NAME)
        content = doc["paths"]["/categories"]["get"]["responses"]["200"]["content"]["application/json"]
        record = content["schema"]["properties"]["data"]["properties"]["items"]["items"]
        self.assertEqual({"id": {"type": "string"}, "name": {"type": "string"}}, record["properties"])
        self.assertEqual({"id", "name"}, set(content["example"]["data"]["items"][0]))


class DependMixedKeyTest(_Base):
    NAME = "depend-mixed-key"

    def test_ref_mixes_number_and_text_for_the_same_value(self):
        shops = self.rows(self.NAME, "/shops")
        self.assertEqual([1, "1", 2, "3"], [s["ref"] for s in shops])
        self.assertEqual([int, str, int, str], [type(s["ref"]) for s in shops])
        self.assertEqual(4, len({s["id"] for s in shops}))
        mixed = self.endpoint(self.NAME, "/shops")["mixedTypeField"]
        self.assertEqual("ref", mixed["field"])
        self.assertEqual([("integer", 1), ("string", "1"), ("integer", 2), ("string", "3")],
                         [(v["jsonType"], v["value"]) for v in mixed["values"]])

    def test_staff_by_ref_value(self):
        item = self.endpoint(self.NAME, "/shops/{ref}/staff")
        self.assertEqual({"1": 2, "2": 1, "3": 2}, item["rowsByParamValue"])
        shops = self.rows(self.NAME, "/shops")
        per_parent, distinct = [], {}
        for shop in shops:
            rows = self.rows(self.NAME, "/shops/%s/staff" % shop["ref"])
            self.assertEqual(item["rowsByParamValue"][str(shop["ref"])], len(rows))
            per_parent += rows
            distinct.update({r["staff_no"]: r for r in rows})
        self.assertEqual(item["referenceTotals"]["oneCallPerParentRecord"], len(per_parent))
        self.assertEqual(item["referenceTotals"]["oneCallPerDistinctValue"], len(distinct))
        self.assertEqual((7, 5), (len(per_parent), len(distinct)))
        self.assertEqual(item["expectedTotal"], len(distinct))
        self.assertEqual(404, request(self.url(self.NAME, "/shops/9/staff")).status)

    def test_sales_takes_one_param_from_each_parent(self):
        item = self.endpoint(self.NAME, "/sales")
        self.assertEqual(["/shops", "/seasons"], [d["path"] for d in item["dependsOn"]])
        self.assertEqual(["shop_ref", "season"], [d["param"]["name"] for d in item["dependsOn"]])
        for query, missing in (({"season": "S1"}, "shop_ref"), ({"shop_ref": "1"}, "season"), ({}, "shop_ref")):
            resp = request(self.url(self.NAME, "/sales"), query=query)
            self.assertEqual(400, resp.status)
            self.assertIn(missing, resp.json["message"])
        seasons = self.rows(self.NAME, "/seasons")
        self.assertEqual(["S1", "S2"], [s["code"] for s in seasons])
        self.assertTrue(all(isinstance(s["seq"], int) for s in seasons))
        got = {}
        for ref in ("1", "2", "3"):
            for season in seasons:
                rows = self.rows(self.NAME, "/sales", query={"shop_ref": ref, "season": season["code"]})
                self.assertEqual(item["rowsPerCombination"], len(rows))
                got.update({r["sale_id"]: r for r in rows})
        self.assertEqual(item["referenceTotalIfAllCombinations"], len(got))
        self.assertEqual(6, len(got))
        self.assertEqual([], self.rows(self.NAME, "/sales", query={"shop_ref": "9", "season": "S1"}))
        self.assertEqual([], self.rows(self.NAME, "/sales", query={"shop_ref": "1", "season": "S9"}))

    def test_doc_declares_ref_as_integer_or_string(self):
        doc = self.doc(self.NAME)
        content = doc["paths"]["/shops"]["get"]["responses"]["200"]["content"]["application/json"]
        ref = content["schema"]["properties"]["data"]["properties"]["items"]["items"]["properties"]["ref"]
        self.assertEqual([{"type": "integer"}, {"type": "string"}], ref["oneOf"])
        self.assertNotIn("type", ref)
        self.assertEqual([1, "1"], [s["ref"] for s in content["example"]["data"]["items"]])
        if yaml is not None:
            loaded = yaml.safe_load(request(self.url(self.NAME, "/openapi.yaml")).text)
            self.assertEqual(doc, loaded)      # YAML 里数字 1 与文本 "1" 不能混成同一种类型


# ==================== E 组 ====================

class _SwitchBase(_Base):

    def faults_url(self):
        return "%s/__admin/faults?scenario=%s" % (self.base, self.NAME)

    def switches(self):
        resp = request(self.faults_url())
        self.assertEqual(200, resp.status)
        self.assertEqual(self.NAME, resp.json["scenario"])
        return {item["key"]: item for item in resp.json["endpoints"]}

    def switch(self, **body):
        resp = request(self.faults_url(), method="POST", json_body=body)
        self.assertEqual(200, resp.status, resp.text)
        return {item["key"]: item for item in resp.json["endpoints"]}

    def reset_scenario(self, name=None):
        resp = request("%s/__admin/reset?scenario=%s" % (self.base, name or self.NAME), method="POST")
        self.assertEqual(200, resp.status)


class DependTwoRootsTest(_SwitchBase):
    NAME = "depend-two-roots"
    QUERY = {"warehouse_id": "TRW-00001", "period": "2026-01"}

    def test_roots_are_independent_and_stock_needs_both(self):
        warehouses = self.rows(self.NAME, "/warehouses")
        periods = self.rows(self.NAME, "/periods")
        self.assertEqual(["TRW-00001", "TRW-00002", "TRW-00003"], [w["id"] for w in warehouses])
        self.assertEqual(["2026-01", "2026-02"], [p["code"] for p in periods])
        item = self.endpoint(self.NAME, "/stock")
        self.assertEqual(["/warehouses", "/periods"], [d["path"] for d in item["dependsOn"]])
        self.assertEqual(["id", "code"], [d["field"] for d in item["dependsOn"]])
        self.assertNotIn("dependsOn", self.endpoint(self.NAME, "/warehouses"))
        self.assertNotIn("dependsOn", self.endpoint(self.NAME, "/periods"))
        for query, missing in (({"period": "2026-01"}, "warehouse_id"),
                               ({"warehouse_id": "TRW-00001"}, "period"), ({}, "warehouse_id")):
            resp = request(self.url(self.NAME, "/stock"), query=query)
            self.assertEqual(400, resp.status)
            self.assertIn(missing, resp.json["message"])
        got = {}
        for warehouse in warehouses:
            for period in periods:
                rows = self.rows(self.NAME, "/stock",
                                 query={"warehouse_id": warehouse["id"], "period": period["code"]})
                self.assertEqual(item["rowsPerCombination"], len(rows))
                self.assertTrue(all(r["warehouse_id"] == warehouse["id"]
                                    and r["period"] == period["code"] for r in rows))
                got.update({r["stock_id"]: r for r in rows})
        self.assertEqual(12, len(got))
        self.assertEqual(item["referenceTotalIfAllCombinations"], len(got))
        self.assertEqual(item["expectedTotal"], len(got))
        self.assertEqual([], self.rows(self.NAME, "/stock",
                                       query={"warehouse_id": "nope", "period": "2026-01"}))
        doc = self.doc(self.NAME)
        self.assertEqual([("warehouse_id", True), ("period", True)],
                         [(p["name"], p["required"])
                          for p in doc["paths"]["/stock"]["get"]["parameters"]])

    def test_each_endpoint_can_be_switched_independently(self):
        self.assertEqual({"warehouses", "periods", "stock"}, set(self.switches()))
        self.assertEqual({"normal"}, {s["mode"] for s in self.switches().values()})
        self.switch(endpoint="warehouses", mode="fail")
        resp = request(self.url(self.NAME, "/warehouses"))
        self.assertEqual(503, resp.status)
        self.assertEqual(503, resp.json["code"])
        self.assertEqual(200, request(self.url(self.NAME, "/periods")).status)
        self.assertEqual(200, request(self.url(self.NAME, "/stock"), query=self.QUERY).status)
        self.switch(switches=[{"endpoint": "warehouses", "mode": "normal"},
                              {"endpoint": "periods", "mode": "fail", "status": 500}])
        self.assertEqual(200, request(self.url(self.NAME, "/warehouses")).status)
        self.assertEqual(500, request(self.url(self.NAME, "/periods")).status)
        # 下游切失败后先于参数校验返回
        self.switch(endpoint="stock", mode="fail", status=502)
        self.assertEqual(502, request(self.url(self.NAME, "/stock")).status)
        state = self.switches()
        self.assertEqual({"total": 2, "succeeded": 1, "failed": 1}, state["warehouses"]["calls"])
        self.assertEqual({"total": 2, "succeeded": 1, "failed": 1}, state["periods"]["calls"])
        self.assertEqual({"total": 2, "succeeded": 1, "failed": 1}, state["stock"]["calls"])
        self.assertEqual(("fail", 500), (state["periods"]["mode"], state["periods"]["failStatus"]))
        self.assertEqual(("normal", 503), (state["warehouses"]["mode"],
                                           state["warehouses"]["failStatus"]))

    def test_invalid_switch_is_rejected_as_a_whole(self):
        resp = request(self.faults_url(), method="POST", json_body={"switches": [
            {"endpoint": "warehouses", "mode": "fail"}, {"endpoint": "customers", "mode": "fail"}]})
        self.assertEqual(400, resp.status)
        self.assertEqual("normal", self.switches()["warehouses"]["mode"])
        for body in ({"endpoint": "stock", "mode": "broken"},
                     {"endpoint": "stock", "mode": "fail", "status": 200}, {"switches": []}):
            self.assertEqual(400, request(self.faults_url(), method="POST", json_body=body).status)

    def test_reset_is_isolated_per_scenario(self):
        other = "%s/__admin/faults?scenario=depend-switchable" % self.base
        self.switch(endpoint="stock", mode="fail")
        resp = request(other, method="POST", json_body={"endpoint": "orders", "mode": "fail"})
        self.assertEqual(200, resp.status)
        request(self.url(self.NAME, "/periods"))
        # 复位别的场景不动本场景；复位本场景不动别的场景
        self.reset_scenario("none")
        self.assertEqual("fail", self.switches()["stock"]["mode"])
        self.reset_scenario()
        state = self.switches()
        self.assertEqual({"normal"}, {s["mode"] for s in state.values()})
        self.assertEqual({0}, {s["calls"]["total"] for s in state.values()})
        others = {i["key"]: i for i in request(other).json["endpoints"]}
        self.assertEqual("fail", others["orders"]["mode"])
        self.assertEqual({"customers", "orders", "items", "addresses"}, set(others))
        # 切开关不清计数；取文档不计数
        request(self.url(self.NAME, "/periods"))
        self.switch(endpoint="periods", mode="fail")
        self.doc(self.NAME)
        self.assertEqual(1, self.switches()["periods"]["calls"]["total"])

    def test_admin_lists_every_switch_board(self):
        resp = request(self.base + "/__admin/faults")
        self.assertEqual(400, resp.status)
        for name in ("depend-switchable", "depend-two-roots", "page-fail-midway"):
            self.assertIn(name, resp.json["message"])
        self.assertEqual(404, request(self.base + "/__admin/faults?scenario=none").status)
        state = request(self.base + "/__admin/state").json
        self.assertEqual(["depend-switchable", "depend-two-roots", "page-fail-midway"],
                         list(state["faultSwitchesByScenario"]))
        self.assertEqual(state["faultSwitches"], state["faultSwitchesByScenario"]["depend-switchable"])
        self.assertEqual(["warehouses", "periods", "stock"],
                         [i["key"] for i in state["faultSwitchesByScenario"][self.NAME]])


class PageFailMidwayTest(_SwitchBase):
    NAME = "page-fail-midway"

    def page(self, number, **query):
        return request(self.url(self.NAME, "/records"), query=dict({"page": number}, **query))

    def test_default_state_fails_from_page_three(self):
        state = self.switches()["records"]
        self.assertEqual(("fail", 500), (state["mode"], state["failStatus"]))
        item = self.endpoint(self.NAME, "/records")
        self.assertEqual(("fail", 3, 20), (item["defaultMode"], item["failFromPage"],
                                           item["rowsBeforeFailure"]))
        ids = []
        for number in (1, 2):
            resp = self.page(number)
            self.assertEqual(200, resp.status, resp.text)
            self.assertEqual(50, resp.json["data"]["total"])
            self.assertIs(True, resp.json["data"]["has_next"])
            ids += [r["id"] for r in resp.json["data"]["items"]]
        self.assertEqual(item["rowsBeforeFailure"], len(set(ids)))
        for number in (3, 4, 5, 6):
            resp = self.page(number)
            self.assertEqual(500, resp.status)
            self.assertEqual(500, resp.json["code"])
        # 不带 page 等同第 1 页
        self.assertEqual(200, request(self.url(self.NAME, "/records")).status)
        self.assertEqual({"total": 7, "succeeded": 3, "failed": 4},
                         self.switches()["records"]["calls"])

    def test_switching_to_normal_lets_all_pages_through(self):
        item = self.endpoint(self.NAME, "/records")
        self.switch(endpoint="records", mode="normal")
        rows = fetch_all(item)
        self.assertEqual(50, len(rows))
        self.assertEqual(item["expectedTotal"], len(rows))
        self.assertEqual(50, len({r["id"] for r in rows}))
        # 切回失败可指定状态码；不指定时用缺省的 500
        self.switch(endpoint="records", mode="fail", status=503)
        self.assertEqual(503, self.page(3).status)
        self.assertEqual(200, self.page(2).status)
        self.switch(endpoint="records", mode="normal")
        state = self.switch(endpoint="records", mode="fail")["records"]
        self.assertEqual(500, state["failStatus"])
        self.assertEqual(500, self.page(5).status)

    def test_reset_returns_to_the_failing_default(self):
        self.switch(endpoint="records", mode="normal")
        self.assertEqual(200, self.page(3).status)
        self.reset_scenario("depend-two-roots")
        self.assertEqual("normal", self.switches()["records"]["mode"])
        self.reset_scenario()
        state = self.switches()["records"]
        self.assertEqual(("fail", 500, 0), (state["mode"], state["failStatus"], state["calls"]["total"]))
        self.assertEqual(500, self.page(3).status)
        # 全量复位同样回到缺省的失败状态
        self.switch(endpoint="records", mode="normal")
        self.assertEqual(200, request(self.base + "/__admin/reset", method="POST").status)
        self.assertEqual("fail", self.switches()["records"]["mode"])

    def test_page_size_and_bad_page(self):
        resp = self.page(3, page_size=5)
        self.assertEqual(500, resp.status)
        resp = self.page(2, page_size=5)
        self.assertEqual(["PFM-%05d" % i for i in range(6, 11)],
                         [r["id"] for r in resp.json["data"]["items"]])
        self.assertEqual(400, self.page("abc").status)
        self.assertEqual(400, self.page(0).status)

    def test_doc_warns_up_front_and_is_independent_of_the_switch(self):
        before = self.doc(self.NAME)
        self.assertTrue(before["info"]["description"].startswith("【不是正常场景】"))
        operation = before["paths"]["/records"]["get"]
        self.assertTrue(operation["description"].startswith("【不是正常场景】"))
        example = operation["responses"]["200"]["content"]["application/json"]["example"]
        self.assertEqual(1, example["data"]["page"])
        self.switch(endpoint="records", mode="normal")
        self.assertEqual(before, self.doc(self.NAME))
        self.assertEqual(0, self.switches()["records"]["calls"]["total"])


if __name__ == "__main__":
    unittest.main()
