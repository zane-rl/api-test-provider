# -*- coding: utf-8 -*-
"""文档自测：每份 OpenAPI / Swagger 文档是合法 JSON，且声明的每个 path+method 真能请求通。

这组用例是「文档 / 实现 / 清单」三者一致的兜底守卫。
"""

import json
import unittest
from urllib.parse import urlencode

from .support import ProviderTestCase, request, build_auth, operations_of

try:
    import yaml      # 只在自测里用来回读；服务本身不依赖第三方库
except ImportError:  # pragma: no cover
    yaml = None


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
                    for operation in operations_of(doc["paths"][path]).values():
                        self.assertEqual([], operation["security"])

    def test_doc_paths_match_catalog_endpoints(self):
        for entry, doc in self.docs():
            with self.subTest(scenario=entry["name"]):
                declared = {(m.upper(), p) for p, ops in doc["paths"].items()
                            for m in operations_of(ops)}
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
                for method, operation in operations_of(operations).items():
                    item = by_key[(method.upper(), path)]
                    checked += 1
                    with self.subTest(scenario=entry["name"], op=operation["operationId"]):
                        resp = self._call_declared(entry, item, operation, auth)
                        self.assertNotIn(resp.status, (404, 405),
                                         "文档声明的 %s %s 打不通：%s"
                                         % (method.upper(), path, resp.text[:200]))
                        self._assert_no_schema_drift(entry, operation, resp)
        self.assertEqual(87, checked, "端点总数变了，请同步 README")

    def _call_declared(self, entry, item, operation, auth):
        url = item["url"]
        for name, value in (item.get("pathSample") or {}).items():
            url = url.replace("{%s}" % name, value)
        query, headers, cookies, body, raw = {}, {}, {}, None, None
        for param in operation.get("parameters", []):
            # 慢响应端点按 0 秒调用，避免自测被拖住（$ref 引用的公共参数没有 name）
            if param.get("name") == "seconds":
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


class DocFormatTest(ProviderTestCase):
    """每个场景的文档都有 .json / .yaml / .yml 三种扩展名，内容等价，且都是 OpenAPI 3.0.x"""

    def test_every_scenario_serves_three_equivalent_formats(self):
        for name, entry in self.catalog().items():
            with self.subTest(scenario=name):
                urls = entry["openapiUrls"]
                self.assertEqual({"json", "yaml", "yml"}, set(urls))
                self.assertEqual(entry["openapiUrl"], urls["json"])
                as_json = request(urls["json"])
                as_yaml = request(urls["yaml"])
                as_yml = request(urls["yml"])
                for resp in (as_json, as_yaml, as_yml):
                    self.assertEqual(200, resp.status)
                self.assertTrue(as_json.headers["Content-Type"].startswith("application/json"))
                self.assertTrue(as_yaml.headers["Content-Type"].startswith("application/yaml"))
                self.assertEqual(as_yaml.text, as_yml.text)
                self.assertTrue(as_yaml.text.startswith('openapi: "3.0.3"\n'), as_yaml.text[:40])
                self.assertTrue(as_json.json["openapi"].startswith("3.0."))
                if yaml is not None:
                    self.assertEqual(as_json.json, yaml.safe_load(as_yaml.text))

    def test_bundles_serve_three_formats(self):
        payload = request(self.base + "/__admin/scenarios").json
        self.assertEqual(set(payload["openapiBundles"]), set(payload["openapiBundleUrls"]))
        for name, urls in payload["openapiBundleUrls"].items():
            with self.subTest(bundle=name):
                self.assertEqual(payload["openapiBundles"][name], urls["yaml"])
                texts = {ext: request(url) for ext, url in urls.items()}
                self.assertEqual({200}, {r.status for r in texts.values()})
                self.assertEqual(texts["yaml"].text, texts["yml"].text)
                self.assertEqual("3.0.3", texts["json"].json["openapi"])

    def test_swagger2_is_no_longer_served_for_normal_scenarios(self):
        payload = request(self.base + "/__admin/scenarios").json
        for entry in payload["scenarios"]:
            with self.subTest(scenario=entry["name"]):
                self.assertNotIn("swagger2Url", entry)
                self.assertEqual(404, request(entry["baseUrl"] + "/swagger2.json").status)
                self.assertEqual(404, request(entry["baseUrl"] + "/openapi.xml").status)


class NegativeDocumentTest(ProviderTestCase):
    """应被后端拒绝的文档版本：Swagger 2.0 与 OpenAPI 3.1.0，不计入正常场景"""

    def _negatives(self):
        payload = request(self.base + "/__admin/scenarios").json
        return payload, {item["name"]: item for item in payload["negativeDocuments"]}

    def test_listed_separately_and_marked_rejected(self):
        payload, negatives = self._negatives()
        self.assertEqual({"swagger2", "openapi31"}, set(negatives))
        self.assertEqual(len(payload["scenarios"]), payload["count"])
        self.assertFalse([s for s in payload["scenarios"] if s["name"].startswith("__")])
        for item in negatives.values():
            self.assertEqual("REJECTED", item["expectedBackendSupport"])
            self.assertIn(item["url"], item["urls"].values())
        self.assertTrue(negatives["swagger2"]["url"].endswith("/__negative/swagger2.json"))
        self.assertTrue(negatives["openapi31"]["url"].endswith("/__negative/openapi31.yaml"))

    def test_swagger2_document(self):
        item = self._negatives()[1]["swagger2"]
        doc = request(item["urls"]["json"]).json
        self.assertEqual("2.0", doc["swagger"])
        self.assertNotIn("openapi", doc)
        self.assertNotIn("components", doc)
        self.assertEqual("/__negative", doc["basePath"])
        self.assertEqual(["http"], doc["schemes"])
        self.assertEqual({"/items"}, set(doc["paths"]))
        self.assertEqual({"get"}, set(doc["paths"]["/items"]))
        ref = doc["paths"]["/items"]["get"]["responses"]["200"]["schema"]["$ref"]
        self.assertIn(ref.split("/")[-1], doc["definitions"])
        text = request(item["urls"]["yaml"]).text
        self.assertEqual(text, request(item["urls"]["yml"]).text)
        self.assertTrue(text.startswith('swagger: "2.0"\n'), text[:40])
        if yaml is not None:
            self.assertEqual(doc, yaml.safe_load(text))

    def test_openapi31_document(self):
        item = self._negatives()[1]["openapi31"]
        text = request(item["urls"]["yaml"]).text
        self.assertTrue(text.startswith('openapi: "3.1.0"\n'), text[:40])
        self.assertEqual(text, request(item["urls"]["yml"]).text)
        doc = request(item["urls"]["json"]).json
        self.assertEqual("3.1.0", doc["openapi"])
        self.assertEqual([{"url": self.base + "/__negative"}], doc["servers"])
        self.assertEqual({"/items"}, set(doc["paths"]))
        self.assertNotIn("nullable", json.dumps(doc))
        if yaml is not None:
            self.assertEqual(doc, yaml.safe_load(text))

    def test_described_endpoint_is_reachable_and_logged(self):
        item = self._negatives()[1]["swagger2"]["endpoint"]
        resp = request(item["url"])
        self.assertEqual(200, resp.status)
        rows = resp.json["data"]["items"]
        self.assertEqual(item["expectedTotal"], len(rows))
        self.assertEqual(3, len({r[item["primaryKey"]] for r in rows}))
        self.assertEqual(405, request(item["url"], method="POST", body="").status)
        self.assertEqual(404, request(self.base + "/__negative/nope.json").status)
        # 后端若没有拒绝文档而真的发了请求，这里查得到
        logged = self.requests_of("__negative")
        self.assertEqual(["/__negative/items"] * 2 + ["/__negative/nope.json"],
                         [r["path"] for r in logged])
        resp = request(self.base + "/__admin/reset?scenario=__negative", method="POST")
        self.assertEqual(200, resp.status)
        self.assertEqual([], self.requests_of("__negative"))


if __name__ == "__main__":
    unittest.main()
