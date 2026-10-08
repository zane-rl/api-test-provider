# -*- coding: utf-8 -*-
"""按鉴权类型合并的 OpenAPI 文档：与单场景文档一致、在线地址可用、静态文件未过期、YAML 可回读。"""

import json
import os
import subprocess
import sys
import unittest

from scenarios import REGISTRY
from scenarios.bundle import ALL, auth_kinds, build_bundle, rename_param_refs
from scenarios.openapi import HTTP_METHODS, build_openapi
from scenarios.yamlout import dump as yaml_dump
from tests.support import ProviderTestCase, request

try:
    import yaml      # 只在自测里用来回读；服务本身不依赖第三方库
except ImportError:  # pragma: no cover
    yaml = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://127.0.0.1:18090"


class BundleContentTest(unittest.TestCase):
    """合并文档只是单场景文档的改名合并，不能多也不能少"""

    def test_every_endpoint_lands_in_exactly_one_kind(self):
        seen = {}
        for kind in auth_kinds():
            for path, operations in build_bundle(kind, BASE)["paths"].items():
                for method in operations:
                    if method not in HTTP_METHODS:
                        continue
                    key = (method, path)
                    self.assertNotIn(key, seen, "端点同时出现在 %s 与 %s" % (seen.get(key), kind))
                    seen[key] = kind
        expected = {(e.method.lower(), "/s/%s%s" % (s.name, e.path))
                    for s in REGISTRY.values() for e in s.endpoints}
        self.assertEqual(expected, set(seen))
        all_paths = build_bundle(ALL, BASE)["paths"]
        self.assertEqual(len(expected), sum(1 for ops in all_paths.values()
                                            for m in ops if m in HTTP_METHODS))

    def test_operations_match_per_scenario_docs(self):
        for kind in auth_kinds() + [ALL]:
            bundle = build_bundle(kind, BASE)
            schemes = bundle.get("components", {}).get("securitySchemes", {})
            op_ids = []
            for scenario in REGISTRY.values():
                if kind != ALL and scenario.auth.kind != kind:
                    continue
                doc = build_openapi(scenario, "%s/s/%s" % (BASE, scenario.name))
                rename = lambda reqs: [{"%s.%s" % (scenario.name, k): v for k, v in r.items()}
                                       for r in reqs]
                for name, scheme in doc.get("components", {}).get("securitySchemes", {}).items():
                    self.assertEqual(scheme, schemes["%s.%s" % (scenario.name, name)])
                # 公共参数改名为 <场景>.<原名>，引用它的 $ref 跟着改名、不内联
                for name, param in doc.get("components", {}).get("parameters", {}).items():
                    self.assertEqual(param, bundle["components"]["parameters"][
                        "%s.%s" % (scenario.name, name)])
                for path, operations in doc["paths"].items():
                    merged_item = bundle["paths"]["/s/%s%s" % (scenario.name, path)]
                    self.assertEqual(set(operations), set(merged_item), path)
                    for method, op in operations.items():
                        merged = merged_item[method]
                        where = "%s %s %s" % (kind, method, path)
                        if method == "parameters":      # 路径项级参数
                            self.assertEqual(rename_param_refs(op, scenario.name), merged, where)
                            continue
                        self.assertEqual(rename_param_refs(op.get("parameters", []), scenario.name),
                                         merged.get("parameters", []), where + " parameters")
                        for field in ("summary", "description", "requestBody", "responses"):
                            self.assertEqual(op.get(field), merged.get(field), where + " " + field)
                        self.assertEqual(op["operationId"], merged["x-original-operation-id"])
                        self.assertEqual(op.get("security", rename(doc.get("security", []))),
                                         merged.get("security", []), where)
                        self.assertEqual(scenario.name, merged["x-scenario"])
                        self.assertEqual([scenario.name], merged["tags"])
                        op_ids.append(merged["operationId"])
            self.assertEqual(len(op_ids), len(set(op_ids)), "%s 的 operationId 有重复" % kind)

    def test_x_test_matches_catalog(self):
        bundle = build_bundle(ALL, BASE)
        for scenario in REGISTRY.values():
            base = "%s/s/%s" % (BASE, scenario.name)
            for endpoint in scenario.endpoints:
                meta = bundle["paths"]["/s/%s%s" % (scenario.name, endpoint.path)][
                    endpoint.method.lower()]["x-test"]
                catalog = endpoint.catalog(base)
                for key in ("role", "paginationType", "pagination", "recordPath",
                            "primaryKey", "expectedTotal", "requiresAuth"):
                    self.assertEqual(catalog[key], meta[key], "%s %s %s" % (scenario.name, endpoint.path, key))

    def test_shared_parameter_refs_resolve_inside_bundle(self):
        """合并文档里每个参数 $ref 都指得到 components.parameters 里的定义"""
        for kind in ("none", ALL):
            bundle = build_bundle(kind, BASE)
            defined = bundle["components"]["parameters"]
            self.assertEqual({"shared-params.TenantId", "shared-params.Locale",
                              "shared-params.ApiVersion"}, set(defined))
            refs = []
            for path_item in bundle["paths"].values():
                params = list(path_item.get("parameters", []))
                for method, op in path_item.items():
                    if method in HTTP_METHODS:
                        params += op.get("parameters", [])
                refs += [p["$ref"] for p in params if "$ref" in p]
            self.assertEqual(7, len(refs))          # 3（操作级）+ 3（路径项级）+ 1
            for ref in refs:
                self.assertIn(ref[len("#/components/parameters/"):], defined)

    def test_unknown_kind(self):
        with self.assertRaises(KeyError):
            build_bundle("nope", BASE)


class StaticFormatsTest(unittest.TestCase):
    """openapi/ 下每种鉴权类型都有 .yaml / .yml / .json，且三份内容等价"""

    @staticmethod
    def _read(name):
        with open(os.path.join(ROOT, "openapi", name), encoding="utf-8") as fh:
            return fh.read()

    def test_three_extensions_per_kind_with_equal_content(self):
        for kind in auth_kinds() + [ALL]:
            with self.subTest(kind=kind):
                doc = json.loads(self._read(kind + ".json"))
                self.assertEqual(json.loads(json.dumps(build_bundle(kind, BASE))), doc)
                self.assertEqual("3.0.3", doc["openapi"])
                body = lambda text: "".join(l for l in text.splitlines(True)
                                            if not l.startswith("#"))
                as_yaml, as_yml = self._read(kind + ".yaml"), self._read(kind + ".yml")
                self.assertEqual(body(as_yaml), body(as_yml))
                if yaml is not None:
                    self.assertEqual(doc, yaml.safe_load(as_yaml))

    def test_no_swagger2_or_openapi31_among_generated_files(self):
        for name in sorted(os.listdir(os.path.join(ROOT, "openapi"))):
            if name.endswith((".yaml", ".yml")):
                first = [l for l in self._read(name).splitlines() if not l.startswith("#")][0]
                self.assertEqual('openapi: "3.0.3"', first, name)
            elif name.endswith(".json"):
                doc = json.loads(self._read(name))
                self.assertNotIn("swagger", doc, name)
                self.assertEqual("3.0.3", doc["openapi"], name)


class StaticFilesTest(unittest.TestCase):
    def test_committed_files_are_up_to_date(self):
        # 独立进程跑，保证用的是默认配置，不受本进程其他用例改过的 config 影响
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "export_openapi_by_auth.py"), "--check"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


@unittest.skipIf(yaml is None, "未安装 PyYAML，跳过 YAML 回读校验")
class YamlRoundTripTest(unittest.TestCase):
    def test_bundles_round_trip(self):
        for kind in auth_kinds() + [ALL]:
            doc = build_bundle(kind, BASE)
            self.assertEqual(doc, yaml.safe_load(yaml_dump(doc)), kind)

    def test_ambiguous_scalars_stay_strings(self):
        # 这些值在 YAML 1.1 里裸写会被读成布尔 / 数字 / 日期 / null，必须加引号
        values = ["yes", "No", "on", "OFF", "y", "2026-01-01", "0x1F", "1_000", "1:20", "200",
                  "-1", ".5", "1e3", "~", "null", "", " lead", "trail ", "a: b", "a #b", "#x",
                  "-x", "? q", "[x]", "{sku}", "/s/{sku}/b", "x\ny", "x\n", "\n", "x\n\n",
                  "  indented\nx", "tab\there", "u x", "emoji😀", "中文：冒号", "<<", "@x",
                  "`x", "'q'", '"q"', "%x", "!t", "&a", "*a", "|", "http://127.0.0.1:18090/s/x"]
        doc = {"values": values, "numbers": [0, -3, 1.5, 1e20, -2.5e-10, True, False, None],
               "empty": {"d": {}, "l": []}, "200": {"nested": [[1, 2], [{"k": "v"}]]}}
        self.assertEqual(doc, yaml.safe_load(yaml_dump(doc)))


class OnlineBundleTest(ProviderTestCase):
    def test_yaml_and_json_follow_actual_port(self):
        resp = request(self.base + "/openapi/oauth2.yaml")
        self.assertEqual(200, resp.status)
        self.assertTrue(resp.headers["Content-Type"].startswith("application/yaml"))
        self.assertIn("url: %s\n" % self.base, resp.text)
        if yaml is not None:
            self.assertEqual(build_bundle("oauth2", self.base), yaml.safe_load(resp.text))

        resp = request(self.base + "/openapi/all.json")
        self.assertEqual(200, resp.status)
        self.assertEqual(build_bundle(ALL, self.base), resp.json)

    def test_catalog_lists_every_bundle(self):
        bundles = request(self.base + "/__admin/scenarios").json["openapiBundles"]
        self.assertEqual(auth_kinds() + [ALL], list(bundles))
        for name, url in bundles.items():
            self.assertEqual(200, request(url).status, name)

    def test_errors_and_not_logged(self):
        self.assertEqual(404, request(self.base + "/openapi/nope.yaml").status)
        self.assertEqual(404, request(self.base + "/openapi/bearer.xml").status)
        self.assertEqual(405, request(self.base + "/openapi/bearer.yaml", method="POST", body="").status)
        logged = request(self.base + "/__admin/requests?limit=500").json["requests"]
        self.assertFalse([r for r in logged if r["path"].startswith("/openapi/")])


if __name__ == "__main__":
    unittest.main()
