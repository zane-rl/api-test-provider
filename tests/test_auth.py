# -*- coding: utf-8 -*-
"""A 类鉴权场景自测：正确凭证 200、缺凭证 401、凭证放错位置 401，以及各场景的专有语义。"""

import base64
import json
import time
import unittest
from urllib.parse import urlencode

from .support import (
    ProviderTestCase, request, build_auth, obtain_token, records_of, Auth, place,
)

# 位置放错时挪到哪里
_MISPLACE = {"HEADER": "QUERY", "QUERY": "HEADER", "COOKIE": "HEADER"}


class AuthScenarioTest(ProviderTestCase):
    """遍历全部 A 类场景的三件事"""

    def setUp(self):
        super().setUp()
        # 通用用例会打到 oauth2-paged-expiring，默认每页延迟 3 秒会把整组拖慢
        self.set_config(oauth2PagedDelaySeconds=0)

    def tearDown(self):
        self.set_config(oauth2PagedDelaySeconds=6)

    def _auth_scenarios(self):
        return [s for s in self.catalog().values() if s["group"] == "A 鉴权"]

    def _items(self, entry):
        for item in entry["endpoints"]:
            if item["path"] == "/items":
                return item
        raise AssertionError("场景 %s 没有 /items" % entry["name"])

    def test_correct_credentials_return_all_items(self):
        scenarios = self._auth_scenarios()
        self.assertEqual(26, len(scenarios), "A 类场景数量变了，请同步 README")
        for entry in scenarios:
            with self.subTest(scenario=entry["name"]):
                items = self._items(entry)
                auth = build_auth(entry)
                resp = request(items["url"], headers=auth.headers,
                               query=auth.query, cookies=auth.cookies)
                self.assertEqual(200, resp.status, resp.text[:200])
                rows = records_of(resp.json, items["recordPath"])
                if items["paginationType"] == "NONE":
                    self.assertEqual(items["expectedTotal"], len(rows))
                    self.assertEqual(20, len(rows))
                else:
                    # 分页型场景这里只验「首页拿得到数据」，整体翻页在 PagedOauth2Test 里
                    self.assertEqual(items["pagination"]["defaultPageSize"], len(rows))

    def test_missing_credentials_return_401(self):
        for entry in self._auth_scenarios():
            if not entry["auth"]["credentials"]:
                continue
            with self.subTest(scenario=entry["name"]):
                resp = request(self._items(entry)["url"])
                self.assertEqual(401, resp.status, resp.text[:200])
                self.assertEqual(401, resp.json["code"])
                self.assertIn("message", resp.json)

    def test_credentials_in_wrong_place_return_401(self):
        for entry in self._auth_scenarios():
            creds = entry["auth"]["credentials"]
            if not creds:
                continue
            with self.subTest(scenario=entry["name"]):
                auth = build_auth(entry)          # 先拿到正确取值（含动态令牌）
                primary = creds[0]
                value = _value_of(auth, primary)
                wrong = Auth()
                place(wrong, _MISPLACE[primary["in"]], primary["name"], value)
                # 其余凭证照常带上，确保 401 只因为主凭证位置不对
                for other in creds[1:] + [
                        {"in": e["in"], "name": e["name"]} for e in entry["auth"]["extraAuthParams"]]:
                    place(wrong, other["in"], other["name"], _value_of(auth, other))
                resp = request(self._items(entry)["url"], headers=wrong.headers,
                               query=wrong.query, cookies=wrong.cookies)
                self.assertEqual(401, resp.status, resp.text[:200])

    def test_missing_extra_auth_param_returns_401(self):
        for entry in self._auth_scenarios():
            extras = entry["auth"]["extraAuthParams"]
            if not extras:
                continue
            with self.subTest(scenario=entry["name"]):
                auth = build_auth(entry)
                for extra in extras:
                    stripped = Auth(auth.headers, auth.query, auth.cookies)
                    _remove(stripped, extra["in"], extra["name"])
                    resp = request(self._items(entry)["url"], headers=stripped.headers,
                                   query=stripped.query, cookies=stripped.cookies)
                    self.assertEqual(401, resp.status,
                                     "缺附加参数 %s 却没有 401" % extra["name"])

    def test_scenarios_with_extra_params_are_declared(self):
        extras = {s["name"]: s["auth"]["extraAuthParams"] for s in self._auth_scenarios()}
        for name in ("apikey-extra", "basic-extra", "oauth2-extra", "bearer-extra"):
            self.assertTrue(extras[name], "%s 应声明 extraAuthParams" % name)
        self.assertEqual([], extras["none"])


class AuthSpecificTest(ProviderTestCase):
    """各场景的专有语义"""

    def test_apikey_prefix_requires_token_prefix(self):
        entry = self.scenario("apikey-header-prefix")
        url = entry["baseUrl"] + "/items"
        self.assertEqual("Token", entry["auth"]["credentials"][0]["prefix"])
        # 无前缀
        resp = request(url, headers={"Authorization": "test-key-123"})
        self.assertEqual(401, resp.status)
        self.assertIn("前缀 Token", resp.json["message"])
        # 前缀不对
        resp = request(url, headers={"Authorization": "Bearer test-key-123"})
        self.assertEqual(401, resp.status)
        # 正确
        resp = request(url, headers={"Authorization": "Token test-key-123"})
        self.assertEqual(200, resp.status)

    def test_other_apikey_scenarios_have_null_prefix(self):
        for name in ("apikey-header", "apikey-query", "apikey-cookie"):
            entry = self.scenario(name)
            self.assertIsNone(entry["auth"]["credentials"][0]["prefix"], name)

    def test_login_rejects_wrong_password(self):
        for name in ("bearer-request", "bearer-request-header", "bearer-request-query"):
            with self.subTest(scenario=name):
                flow = self.scenario(name)["auth"]["tokenFlow"]
                resp = request(flow["url"], method="POST",
                               json_body={"username": "test", "password": "wrong"})
                self.assertEqual(401, resp.status)

    def test_bearer_request_token_is_injected_where_declared(self):
        for name, location, header_name in (
                ("bearer-request", "HEADER", "Authorization"),
                ("bearer-request-header", "HEADER", "X-Access-Token"),
                ("bearer-request-query", "QUERY", "access_token")):
            with self.subTest(scenario=name):
                entry = self.scenario(name)
                inject = entry["auth"]["tokenFlow"]["inject"]
                self.assertEqual(location, inject["in"])
                self.assertEqual(header_name, inject["name"])
                auth = build_auth(entry)
                resp = request(entry["baseUrl"] + "/items", headers=auth.headers,
                               query=auth.query, cookies=auth.cookies)
                self.assertEqual(200, resp.status)
                # 令牌端点的请求也进了日志
                logged = [r for r in self.requests_of(name) if r["path"].endswith("/login")]
                self.assertEqual(1, len(logged))

    def test_oauth2_post_rejects_basic_header(self):
        flow = self.scenario("oauth2-post")["auth"]["tokenFlow"]
        form = urlencode({"grant_type": "client_credentials",
                          "client_id": flow["clientId"], "client_secret": flow["clientSecret"]})
        resp = request(flow["url"], method="POST", body=form,
                       headers={"Content-Type": "application/x-www-form-urlencoded",
                                "Authorization": "Basic " + base64.b64encode(b"a:b").decode()})
        self.assertEqual(400, resp.status)
        self.assertIn("client_secret_post", resp.json["error_description"])

    def test_oauth2_post_accepts_optional_scope(self):
        flow = self.scenario("oauth2-post")["auth"]["tokenFlow"]
        form = urlencode({"grant_type": "client_credentials", "client_id": flow["clientId"],
                          "client_secret": flow["clientSecret"], "scope": "read"})
        resp = request(flow["url"], method="POST", body=form,
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(200, resp.status)
        self.assertEqual("read", resp.json["scope"])

    def test_oauth2_basic_rejects_secret_in_form(self):
        flow = self.scenario("oauth2-basic")["auth"]["tokenFlow"]
        form = urlencode({"grant_type": "client_credentials",
                          "client_id": flow["clientId"], "client_secret": flow["clientSecret"]})
        resp = request(flow["url"], method="POST", body=form,
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(400, resp.status)
        self.assertIn("client_secret_basic", resp.json["error_description"])

    def test_oauth2_basic_token_expires_by_use_count(self):
        entry = self.scenario("oauth2-basic")
        flow = entry["auth"]["tokenFlow"]
        self.assertEqual(2, flow["tokenMaxUses"])
        token = obtain_token(entry["baseUrl"], flow)
        url = entry["baseUrl"] + "/items"
        headers = {"Authorization": "Bearer " + token}
        self.assertEqual(200, request(url, headers=headers).status)
        self.assertEqual(200, request(url, headers=headers).status)
        # 第 3 次业务请求该令牌已失效
        self.assertEqual(401, request(url, headers=headers).status)
        # 重取令牌后恢复
        fresh = obtain_token(entry["baseUrl"], flow)
        self.assertNotEqual(token, fresh)
        self.assertEqual(200, request(url, headers={"Authorization": "Bearer " + fresh}).status)

    def test_oauth2_basic_max_uses_is_configurable(self):
        entry = self.scenario("oauth2-basic")
        flow = entry["auth"]["tokenFlow"]
        resp = request(self.base + "/__admin/config", method="POST",
                       json_body={"oauth2BasicTokenMaxUses": 1})
        self.assertEqual(200, resp.status)
        self.assertEqual(1, resp.json["config"]["oauth2BasicTokenMaxUses"])
        try:
            token = obtain_token(entry["baseUrl"], flow)
            headers = {"Authorization": "Bearer " + token}
            self.assertEqual(200, request(entry["baseUrl"] + "/items", headers=headers).status)
            self.assertEqual(401, request(entry["baseUrl"] + "/items", headers=headers).status)
        finally:
            request(self.base + "/__admin/config", method="POST",
                    json_body={"oauth2BasicTokenMaxUses": 2})

    def test_oauth2_expiring_declares_ttl_in_catalog(self):
        flow = self.scenario("oauth2-expiring")["auth"]["tokenFlow"]
        self.assertEqual(40, flow["expiresInSeconds"])
        self.assertEqual("post", flow["clientAuthMethod"])

    def test_oauth2_expiring_token_response_carries_actual_ttl(self):
        entry = self.scenario("oauth2-expiring")
        flow = entry["auth"]["tokenFlow"]
        form = urlencode({"grant_type": "client_credentials", "client_id": flow["clientId"],
                          "client_secret": flow["clientSecret"]})
        resp = request(flow["url"], method="POST", body=form,
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(200, resp.status)
        self.assertEqual(40, resp.json["expires_in"])

    def test_oauth2_expiring_token_expires_by_time(self):
        """自测用 2 秒而不是默认的 40 秒，免得把用例拖慢"""
        entry = self.scenario("oauth2-expiring")
        self._set_ttl(2)
        try:
            flow = self.scenario("oauth2-expiring")["auth"]["tokenFlow"]
            self.assertEqual(2, flow["expiresInSeconds"], "清单应反映改后的配置")
            token = obtain_token(entry["baseUrl"], flow)
            headers = {"Authorization": "Bearer " + token}
            url = entry["baseUrl"] + "/items"
            # 未过期时可以反复使用（与 oauth2-basic 的按次数失效区分开）
            self.assertEqual(200, request(url, headers=headers).status)
            self.assertEqual(200, request(url, headers=headers).status)
            self.assertEqual(200, request(url, headers=headers).status)
            time.sleep(2.2)
            self.assertEqual(401, request(url, headers=headers).status)
            # 重取后恢复
            fresh = obtain_token(entry["baseUrl"], flow)
            self.assertNotEqual(token, fresh)
            self.assertEqual(200, request(url, headers={"Authorization": "Bearer " + fresh}).status)
        finally:
            self._set_ttl(40)

    def test_oauth2_expiring_ttl_is_reported_in_state(self):
        entry = self.scenario("oauth2-expiring")
        token = obtain_token(entry["baseUrl"], entry["auth"]["tokenFlow"])
        state = request(self.base + "/__admin/state").json
        self.assertEqual(40, state["config"]["oauth2ExpiringTokenTtlSeconds"])
        self.assertIn("expiresInSeconds", state["issuedTokens"][token])

    def _set_ttl(self, seconds):
        resp = request(self.base + "/__admin/config", method="POST",
                       json_body={"oauth2ExpiringTokenTtlSeconds": seconds})
        self.assertEqual(200, resp.status)

    def test_oauth2_config_rejects_unknown_key(self):
        resp = request(self.base + "/__admin/config", method="POST", json_body={"nope": 1})
        self.assertEqual(400, resp.status)

    def test_oauth2_extra_token_endpoint_rejects_extra_header(self):
        entry = self.scenario("oauth2-extra")
        flow = entry["auth"]["tokenFlow"]
        form = urlencode({"grant_type": "client_credentials", "client_id": flow["clientId"],
                          "client_secret": flow["clientSecret"]})
        resp = request(flow["url"], method="POST", body=form,
                       headers={"Content-Type": "application/x-www-form-urlencoded",
                                "X-App-Key": "extra-key-1"})
        self.assertEqual(400, resp.status)
        self.assertIn("X-App-Key", resp.json["error_description"])
        # 不带附加参数则正常签发
        resp = request(flow["url"], method="POST", body=form,
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(200, resp.status)

    def test_forbidden_scenario_splits_401_and_403(self):
        entry = self.scenario("forbidden")
        auth = build_auth(entry)
        self.assertEqual(200, request(entry["baseUrl"] + "/items",
                                      headers=auth.headers).status)
        resp = request(entry["baseUrl"] + "/secret-items", headers=auth.headers)
        self.assertEqual(403, resp.status)
        self.assertEqual(403, resp.json["code"])
        # 无凭证时是 401 而不是 403
        self.assertEqual(401, request(entry["baseUrl"] + "/secret-items").status)

    def test_fixed_param_requires_all_three_places(self):
        entry = self.scenario("fixed-param")
        creds = {c["name"]: c for c in entry["auth"]["credentials"]}
        self.assertEqual({"X-App-Id", "sign", "tenant"}, set(creds))
        auth = build_auth(entry)
        url = entry["baseUrl"] + "/items"
        self.assertEqual(200, request(url, headers=auth.headers, query=auth.query,
                                      cookies=auth.cookies).status)
        self.assertEqual(401, request(url, query=auth.query, cookies=auth.cookies).status)
        self.assertEqual(401, request(url, headers=auth.headers, cookies=auth.cookies).status)
        self.assertEqual(401, request(url, headers=auth.headers, query=auth.query).status)

    def test_basic_rejects_wrong_password(self):
        entry = self.scenario("basic")
        wrong = base64.b64encode(b"test:nope").decode()
        resp = request(entry["baseUrl"] + "/items",
                       headers={"Authorization": "Basic " + wrong})
        self.assertEqual(401, resp.status)

    def test_scenario_data_does_not_leak_across_scenarios(self):
        """每个场景的记录 id 带自己的场景名，串场一眼可见"""
        for name in ("none", "bearer-static", "apikey-query"):
            entry = self.scenario(name)
            auth = build_auth(entry)
            resp = request(entry["baseUrl"] + "/items", headers=auth.headers,
                           query=auth.query, cookies=auth.cookies)
            rows = resp.json["data"]["items"]
            self.assertTrue(all(r["id"].startswith(name.upper()) for r in rows), name)


def _value_of(auth, cred):
    if cred["in"] == "HEADER":
        return auth.headers[cred["name"]]
    if cred["in"] == "QUERY":
        return auth.query[cred["name"]]
    return auth.cookies[cred["name"]]


def _remove(auth, location, name):
    if location == "HEADER":
        auth.headers.pop(name, None)
    elif location == "QUERY":
        auth.query.pop(name, None)
    else:
        auth.cookies.pop(name, None)


class TokenBodyTest(ProviderTestCase):
    """取 Token 接口只认一种请求体类型，Token 字段路径不是 access_token"""

    JSON = "application/json"
    FORM = "application/x-www-form-urlencoded"
    CREDS = {"appKey": "test-client", "appSecret": "test-secret"}

    def _post(self, name, content_type, raw):
        flow = self.scenario(name)["auth"]["tokenFlow"]
        headers = {"Content-Type": content_type} if content_type else {}
        return request(flow["url"], method="POST", headers=headers, body=raw)

    def test_json_token_endpoint(self):
        name = "token-json-body"
        flow = self.scenario(name)["auth"]["tokenFlow"]
        self.assertEqual((self.JSON, "data.token.value", "data.token.expiresIn", self.CREDS),
                         (flow["contentType"], flow["tokenPath"], flow["expiresInPath"],
                          flow["requestBody"]))
        self.assertTrue(flow["url"].endswith("/s/token-json-body/auth/token"))
        resp = self._post(name, self.JSON + "; charset=utf-8", json.dumps(self.CREDS))
        self.assertEqual(200, resp.status, resp.text)
        self.assertEqual({"code": 0, "message": "ok", "data": {"token": {
            "value": "tok-token-json-body-1", "expiresIn": 7200, "type": "Bearer"}}}, resp.json)
        self.assertNotIn("access_token", resp.text)
        # 表单或其它类型一律 415（urllib 对无类型的请求体会自动补表单类型，所以不单测「不带」）
        for content_type in (self.FORM, "text/plain"):
            with self.subTest(content_type=content_type):
                resp = self._post(name, content_type, urlencode(self.CREDS))
                self.assertEqual(415, resp.status, resp.text)
                self.assertEqual(415, resp.json["code"])
                self.assertIn("application/json", resp.json["message"])
        self.assertEqual(400, self._post(name, self.JSON, "{not json").status)
        self.assertEqual(401, self._post(name, self.JSON, json.dumps(
            dict(self.CREDS, appSecret="wrong"))).status)

    def test_form_token_endpoint(self):
        name = "token-form-body"
        flow = self.scenario(name)["auth"]["tokenFlow"]
        self.assertEqual((self.FORM, "tokenValue", "expiresIn"),
                         (flow["contentType"], flow["tokenPath"], flow["expiresInPath"]))
        resp = self._post(name, self.FORM, urlencode(self.CREDS))
        self.assertEqual(200, resp.status, resp.text)
        self.assertEqual({"tokenValue": "tok-token-form-body-1", "expiresIn": 7200,
                          "tokenType": "Bearer"}, resp.json)
        for content_type in (self.JSON, "text/plain"):
            with self.subTest(content_type=content_type):
                resp = self._post(name, content_type, json.dumps(self.CREDS))
                self.assertEqual(415, resp.status, resp.text)
                self.assertIn(self.FORM, resp.json["message"])
        self.assertEqual(401, self._post(name, self.FORM, "appKey=test-client&appSecret=x").status)

    def test_business_endpoint_checks_bearer(self):
        for name in ("token-json-body", "token-form-body"):
            with self.subTest(scenario=name):
                entry = self.scenario(name)
                url = entry["baseUrl"] + "/items"
                token = obtain_token(entry["baseUrl"], entry["auth"]["tokenFlow"])
                self.assertEqual("tok-%s-1" % name, token)
                resp = request(url, headers={"Authorization": "Bearer " + token})
                self.assertEqual(200, resp.status)
                self.assertEqual(20, len(resp.json["data"]["items"]))
                self.assertEqual(401, request(url).status)
                self.assertEqual(401, request(url, headers={"Authorization": token}).status)
                self.assertEqual(401, request(url, headers={"Authorization": "Bearer nope"}).status)
                roles = {i["path"]: i["role"] for i in entry["endpoints"]}
                self.assertEqual({"/auth/token": "TOKEN", "/items": "DATA"}, roles)

    def test_doc_declares_bearer_and_token_location(self):
        expect = {"token-json-body": (self.JSON, "data.token.value", "data.token.expiresIn"),
                  "token-form-body": (self.FORM, "tokenValue", "expiresIn")}
        for name, (media_type, token_path, expires_path) in expect.items():
            with self.subTest(scenario=name):
                entry = self.scenario(name)
                doc = request(entry["openapiUrl"]).json
                (scheme,) = doc["components"]["securitySchemes"].values()
                self.assertEqual(("http", "bearer"), (scheme["type"], scheme["scheme"]))
                for text in (scheme["description"], doc["info"]["description"]):
                    self.assertIn(entry["baseUrl"] + "/auth/token", text)
                    self.assertIn(token_path, text)
                    self.assertIn(expires_path, text)
                    self.assertIn(media_type, text)
                    self.assertNotIn("{base}", text)
                token_op = doc["paths"]["/auth/token"]["post"]
                self.assertEqual([media_type], list(token_op["requestBody"]["content"]))
                self.assertEqual([], token_op["security"])
                self.assertIn(token_path, token_op["description"])
                # 响应 schema 按 Token 路径逐层可达
                schema = token_op["responses"]["200"]["content"]["application/json"]["schema"]
                for part in token_path.split("."):
                    schema = schema["properties"][part]
                self.assertEqual("string", schema["type"])


if __name__ == "__main__":
    unittest.main()
