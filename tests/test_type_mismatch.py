# -*- coding: utf-8 -*-
"""type-mismatch 场景自测。

这个场景的 OpenAPI 是**手写**的、故意与实际返回对不上，所以这里要正面钉住两侧：
文档声明的确是 `declaredType`，实际返回的确是 `actualJsonType`——
否则那张给 E2E 当期望用的 `declaredVsActual` 自己就是错的。
"""

import json
import unittest

from .support import ProviderTestCase, request, records_of

SCENARIO = "type-mismatch"


def _json_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


class TypeMismatchTest(ProviderTestCase):

    def setUp(self):
        super().setUp()
        self.entry = self.scenario(SCENARIO)
        self.doc = request(self.entry["openapiUrl"]).json

    def _declared_record_schema(self, path):
        operation = self.doc["paths"][path]["get"]
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        return schema["properties"]["data"]["properties"]["items"]["items"]["properties"]

    def test_scenario_shape(self):
        self.assertEqual("F 类型不一致", self.entry["group"])
        self.assertEqual("none", self.entry["auth"]["type"])
        self.assertEqual([], self.entry["auth"]["credentials"])
        self.assertIn("故意不一致", self.entry["covers"])
        self.assertEqual(8, len(self.entry["endpoints"]))
        for item in self.entry["endpoints"]:
            with self.subTest(path=item["path"]):
                self.assertEqual("DATA", item["role"])
                self.assertEqual("NONE", item["paginationType"])
                self.assertEqual("data.items", item["recordPath"])
                self.assertEqual("id", item["primaryKey"])
                self.assertEqual(6, item["expectedTotal"])
                self.assertEqual(3, len(item["sampleFirst3"]))
                self.assertTrue(item["declaredVsActual"])

    def test_declared_and_actual_types_are_both_true(self):
        """逐字段对两侧：文档里写的、响应里返回的，都要与 declaredVsActual 相符"""
        for item in self.entry["endpoints"]:
            declared_schema = self._declared_record_schema(item["path"])
            payload = request(item["url"]).json
            rows = records_of(payload, item["recordPath"])
            self.assertEqual(6, len(rows))
            for expectation in item["declaredVsActual"]:
                field = expectation["field"]
                with self.subTest(path=item["path"], field=field):
                    # 文档侧
                    self.assertEqual(expectation["declaredType"],
                                     declared_schema[field]["type"])
                    self.assertEqual(expectation["declaredFormat"],
                                     declared_schema[field].get("format"))
                    # 实际侧：取第一个非 null 值判类型（nullable 字段可能大多为 null）
                    values = [r[field] for r in rows if r[field] is not None]
                    self.assertTrue(values, "%s.%s 全为 null" % (item["path"], field))
                    self.assertEqual(expectation["actualJsonType"], _json_type(values[0]))

    def test_positive_cases_are_really_mismatched(self):
        """5 个正例：声明类型与实际类型确实不同"""
        positives = {"/number-as-string", "/string-as-number", "/bool-as-string",
                     "/datetime-as-epoch", "/object-as-string"}
        seen = set()
        for item in self.entry["endpoints"]:
            for expectation in item["declaredVsActual"]:
                if not expectation["expectIncompatible"]:
                    continue
                seen.add(item["path"])
                self.assertNotEqual(expectation["declaredType"],
                                    expectation["actualJsonType"],
                                    "%s 标了 expectIncompatible 却类型相同" % item["path"])
        self.assertEqual(positives, seen)

    def test_negative_cases_are_declared_compatible(self):
        """2 个负例：写法不同但后端不该判不兼容"""
        decimal = self.endpoint(SCENARIO, "/int-vs-decimal")["declaredVsActual"][0]
        self.assertEqual(("integer", "number", False),
                         (decimal["declaredType"], decimal["actualJsonType"],
                          decimal["expectIncompatible"]))
        rows = request(self.endpoint(SCENARIO, "/int-vs-decimal")["url"]).json["data"]["items"]
        self.assertIsInstance(rows[0]["price"], float)

        nullable = self.endpoint(SCENARIO, "/nullable-mostly-null")["declaredVsActual"][0]
        self.assertFalse(nullable["expectIncompatible"])
        rows = request(self.endpoint(SCENARIO, "/nullable-mostly-null")["url"]).json["data"]["items"]
        nulls = [r for r in rows if r["remark"] is None]
        self.assertEqual(5, len(nulls))
        self.assertEqual(1, len(rows) - len(nulls))

    def test_consistent_endpoint_has_no_mismatch(self):
        item = self.endpoint(SCENARIO, "/consistent")
        self.assertTrue(all(not c["expectIncompatible"] for c in item["declaredVsActual"]))
        covered = {c["declaredType"] for c in item["declaredVsActual"]}
        self.assertEqual({"string", "integer", "number", "boolean"}, covered)
        formats = {c["declaredFormat"] for c in item["declaredVsActual"]}
        self.assertIn("date-time", formats)
        rows = request(item["url"]).json["data"]["items"]
        self.assertTrue(any(r["remark"] is None for r in rows), "应覆盖 nullable")

    def test_datetime_as_epoch_returns_milliseconds(self):
        rows = request(self.endpoint(SCENARIO, "/datetime-as-epoch")["url"]).json["data"]["items"]
        for row in rows:
            self.assertIsInstance(row["updated_at"], int)
            self.assertGreater(row["updated_at"], 1_600_000_000_000)   # 毫秒而不是秒

    def test_object_as_string_payload_is_parseable_json_text(self):
        rows = request(self.endpoint(SCENARIO, "/object-as-string")["url"]).json["data"]["items"]
        for row in rows:
            self.assertIsInstance(row["extra"], str)
            parsed = json.loads(row["extra"])
            self.assertEqual({"color", "size"}, set(parsed))

    def test_data_is_deterministic(self):
        for item in self.entry["endpoints"]:
            with self.subTest(path=item["path"]):
                first = request(item["url"]).json
                self.assertEqual(first, request(item["url"]).json)
                self.assertEqual(item["sampleFirst3"], first["data"]["items"][:3])


if __name__ == "__main__":
    unittest.main()
