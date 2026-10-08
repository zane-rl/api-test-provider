# -*- coding: utf-8 -*-
"""文档自测：每份 OpenAPI / Swagger 文档是合法 JSON，且声明的每个 path+method 真能请求通。

这组用例是「文档 / 实现 / 清单」三者一致的兜底守卫。
"""

import json
import unittest
from urllib.parse import urlencode

from .support import ProviderTestCase, request, build_auth


class OpenApiTest(ProviderTestCase):

    def docs(self):
        for entry in self.catalog().values():
            resp = request(entry["openapiUrl"])
            self.assertEqual(200, resp.status, entry["name"])
            yield entry, resp.json

    def test_doc_endpoint_needs_no_auth(self):
        for entry in self.catalog().values():
            with self.subTest(scenario=entry["name"]):
                self.assertEqual(200, request(entry["openapiUrl"]).status)

    def test_doc_is_valid_json_with_correct_server_url(self):
        for entry, doc in self.docs():
            with self.subTest(scenario=entry["name"]):
                self.assertEqual("3.0.3", doc["openapi"])
                self.assertEqual([{"url": entry["baseUrl"]}], doc["servers"])
                self.assertTrue(doc["paths"])
                self.assertIn("Error", doc["components"]["schemas"])

    def test_security_schemes_match_declared_credentials(self):
        for entry, doc in self.docs():
            with self.subTest(scenario=entry["name"]):
                creds = entry["auth"]["credentials"] + entry["auth"]["extraAuthParams"]
                schemes = doc.get("components", {}).get("securitySchemes", {})
                if not creds:
                    self.assertNotIn("securitySchemes", doc.get("components", {}))
                    self.assertNotIn("security", doc)
                    continue
                self.assertEqual(len(creds), len(schemes))
                self.assertEqual(1, len(doc["security"]))
                self.assertEqual(set(schemes), set(doc["security"][0]))
                # apiKey 类方案的位置与名称必须与实际校验一致
                for scheme in schemes.values():
                    if scheme["type"] == "apiKey":
                        self.assertTrue(any(c["name"] == scheme["name"]
                                            and c["in"].lower() == scheme["in"]
                                            for c in creds), scheme)

    def test_token_endpoints_declare_no_security(self):
        for entry, doc in self.docs():
            token_paths = [i["path"] for i in entry["endpoints"] if i["role"] == "TOKEN"]
            for path in token_paths:
                with self.subTest(scenario=entry["name"], path=path):
                    for operation in doc["paths"][path].values():
                        self.assertEqual([], operation["security"])

    def test_doc_paths_match_catalog_endpoints(self):
        for entry, doc in self.docs():
            with self.subTest(scenario=entry["name"]):
                declared = {(m.upper(), p) for p, ops in doc["paths"].items() for m in ops}
                catalog = {(i["method"], i["path"]) for i in entry["endpoints"]}
                self.assertEqual(catalog, declared)

    def test_every_declared_operation_is_reachable(self):
        checked = 0
        # oauth2-paged-expiring 的数据接口默认延迟 3 秒，这里只关心可达性
        self.set_config(oauth2PagedDelaySeconds=0)
        self.addCleanup(self.set_config, oauth2PagedDelaySeconds=6)
        for entry, doc in self.docs():
            auth = build_auth(entry) if entry["auth"]["credentials"] else None
            by_key = {(i["method"], i["path"]): i for i in entry["endpoints"]}
            for path, operations in doc["paths"].items():
                for method, operation in operations.items():
                    item = by_key[(method.upper(), path)]
                    checked += 1
                    with self.subTest(scenario=entry["name"], op=operation["operationId"]):
                        resp = self._call_declared(entry, item, operation, auth)
                        self.assertNotIn(resp.status, (404, 405),
                                         "文档声明的 %s %s 打不通：%s"
                                         % (method.upper(), path, resp.text[:200]))
                        self._assert_no_schema_drift(entry, operation, resp)
        self.assertEqual(76, checked, "端点总数变了，请同步 README")

    def _call_declared(self, entry, item, operation, auth):
        url = item["url"]
        for name, value in (item.get("pathSample") or {}).items():
            url = url.replace("{%s}" % name, value)
        query, headers, cookies, body, raw = {}, {}, {}, None, None
        for param in operation.get("parameters", []):
            # 慢响应端点按 0 秒调用，避免自测被拖住
            if param["name"] == "seconds":
                query["seconds"] = "0"
        request_body = operation.get("requestBody")
        if request_body:
            content = request_body["content"]
            if "application/json" in content:
                body = content["application/json"].get("example", {})
            else:
                form = content["application/x-www-form-urlencoded"].get("example", {})
                raw = urlencode(form)
                headers["Content-Type"] = "application/x-www-form-urlencoded"
        if auth and "security" not in operation:
            auth.merge_into(headers, query, cookies)
        return request(url, method=item["method"], headers=headers, query=query,
                       cookies=cookies, json_body=body, body=raw)

    # 唯一一个「文档与实现故意不一致」的场景，见 scenarios/typemismatch.py 的模块注释
    INTENTIONAL_MISMATCH = {"type-mismatch"}

    def _assert_no_schema_drift(self, entry, operation, resp):
        """200 响应里出现的顶层字段都必须在文档里声明过（防止显式声明的 schema 漂移）。

        反向不成立：文档可以声明可选字段（如 OAuth2 的 scope），响应里不一定出现。

        `type-mismatch` 场景豁免：它的 schema 是手写的、**故意**与实际返回的字段类型对不上，
        那正是它存在的意义（给后端的「验证接口时比对字段类型」提供对端）。
        它的可达性仍然照常校验，类型上的一致性由 tests/test_type_mismatch.py 正面断言。
        """
        if entry["name"] in self.INTENTIONAL_MISMATCH:
            return
        if resp.status != 200 or "application/json" not in resp.headers.get("Content-Type", ""):
            return
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        if schema.get("type") != "object":
            return
        payload = resp.json
        if not isinstance(payload, dict):
            return
        if schema.get("additionalProperties"):
            return
        declared = set(schema.get("properties") or {})
        actual = set(payload)
        self.assertTrue(actual.issubset(declared),
                        "%s 响应里有文档未声明的字段：%s"
                        % (operation["operationId"], actual - declared))


class Swagger2Test(ProviderTestCase):

    EXPECTED = {"apikey-header", "page-number"}

    def test_only_declared_scenarios_expose_swagger2(self):
        names = {name for name, entry in self.catalog().items() if entry["swagger2Url"]}
        self.assertEqual(self.EXPECTED, names)
        for name, entry in self.catalog().items():
            if name in self.EXPECTED:
                continue
            resp = request(entry["baseUrl"] + "/swagger2.json")
            self.assertEqual(404, resp.status)

    def test_swagger2_uses_v2_syntax(self):
        for name in self.EXPECTED:
            entry = self.scenario(name)
            resp = request(entry["swagger2Url"])
            self.assertEqual(200, resp.status)
            doc = resp.json
            with self.subTest(scenario=name):
                self.assertEqual("2.0", doc["swagger"])
                self.assertNotIn("openapi", doc)
                self.assertNotIn("components", doc)
                self.assertEqual("/s/%s" % name, doc["basePath"])
                self.assertEqual(["http"], doc["schemes"])
                self.assertIn(":", doc["host"])
                self.assertTrue(doc["definitions"])
                for path, operations in doc["paths"].items():
                    for operation in operations.values():
                        ref = operation["responses"]["200"]["schema"]["$ref"]
                        key = ref.split("/")[-1]
                        self.assertIn(key, doc["definitions"])
                        self.assertNotIn("nullable", json.dumps(doc["definitions"][key]))

    def test_swagger2_security_definitions(self):
        doc = request(self.scenario("apikey-header")["swagger2Url"]).json
        self.assertEqual({"apiKeyHeader"}, set(doc["securityDefinitions"]))
        self.assertEqual({"type": "apiKey", "name": "X-Api-Key", "in": "header",
                          "description": doc["securityDefinitions"]["apiKeyHeader"]["description"]},
                         doc["securityDefinitions"]["apiKeyHeader"])
        self.assertEqual([{"apiKeyHeader": []}], doc["security"])

    def test_swagger2_paths_are_reachable(self):
        for name in self.EXPECTED:
            entry = self.scenario(name)
            doc = request(entry["swagger2Url"]).json
            auth = build_auth(entry) if entry["auth"]["credentials"] else None
            for path, operations in doc["paths"].items():
                for method in operations:
                    with self.subTest(scenario=name, path=path, method=method):
                        headers, query, cookies = {}, {}, {}
                        if auth:
                            auth.merge_into(headers, query, cookies)
                        url = "http://%s%s%s" % (doc["host"], doc["basePath"], path)
                        resp = request(url, method=method.upper(), headers=headers,
                                       query=query, cookies=cookies)
                        self.assertNotIn(resp.status, (404, 405))


if __name__ == "__main__":
    unittest.main()
