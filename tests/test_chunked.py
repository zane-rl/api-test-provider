# -*- coding: utf-8 -*-
"""分块请求体（Transfer-Encoding: chunked）自测。

JDK HttpClient 发 POST 时默认用 chunked 且不带 Content-Length，`http.server` 不会自动解码，
所以所有读请求体的端点都得能吃下分块体。`curl -d` 会带 Content-Length，覆盖不到这条路径。
"""

import http.client
import json
import unittest
from urllib.parse import urlencode, urlparse

from .support import ProviderTestCase, request, obtain_token


def chunked_post(url, body, content_type, headers=None, chunk_size=None):
    """手工发一个分块 POST：自己写块长度行、块数据与结束块"""
    parsed = urlparse(url)
    target = parsed.path + (("?" + parsed.query) if parsed.query else "")
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=30)
    try:
        conn.putrequest("POST", target)
        conn.putheader("Content-Type", content_type)
        conn.putheader("Transfer-Encoding", "chunked")
        # 刻意模仿 JDK HttpClient
        conn.putheader("User-Agent", "Java-http-client/17")
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        conn.endheaders()

        data = body.encode("utf-8")
        size = chunk_size or len(data) or 1
        for start in range(0, len(data), size):
            piece = data[start:start + size]
            conn.send(b"%x\r\n" % len(piece) + piece + b"\r\n")
        conn.send(b"0\r\n\r\n")

        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


def raw_post(url, raw_bytes, content_type="application/json"):
    """发一个格式非法的分块体，用来验证服务端给的是 400 而不是挂掉"""
    parsed = urlparse(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=30)
    try:
        conn.putrequest("POST", parsed.path)
        conn.putheader("Content-Type", content_type)
        conn.putheader("Transfer-Encoding", "chunked")
        conn.endheaders()
        conn.send(raw_bytes)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


class ChunkedBodyTest(ProviderTestCase):

    def test_login_accepts_chunked_json(self):
        flow = self.scenario("bearer-request")["auth"]["tokenFlow"]
        status, text = chunked_post(flow["url"], json.dumps(flow["requestBody"]),
                                    "application/json")
        self.assertEqual(200, status, text)
        self.assertTrue(json.loads(text)["data"]["accessToken"])

    def test_oauth_token_accepts_chunked_form(self):
        flow = self.scenario("oauth2-post")["auth"]["tokenFlow"]
        form = urlencode({"grant_type": flow["grantType"], "client_id": flow["clientId"],
                          "client_secret": flow["clientSecret"]})
        status, text = chunked_post(flow["url"], form,
                                    "application/x-www-form-urlencoded")
        self.assertEqual(200, status, text)
        self.assertTrue(json.loads(text)["access_token"])

    def test_cursor_body_accepts_chunked_json(self):
        item = self.endpoint("cursor-body", "/events/search", method="POST")
        status, text = chunked_post(item["url"], json.dumps({"limit": 20}),
                                    "application/json")
        self.assertEqual(200, status, text)
        self.assertEqual(20, len(json.loads(text)["data"]))

    def test_body_page_accepts_chunked_json(self):
        item = self.endpoint("body-page", "/products/query", method="POST")
        status, text = chunked_post(item["url"], json.dumps({"page": 1, "size": 20}),
                                    "application/json")
        self.assertEqual(200, status, text)
        self.assertEqual(45, json.loads(text)["data"]["total"])

    def test_echo_body_accepts_chunked_json(self):
        item = self.endpoint("echo", "/echo-body", method="POST")
        body = {"page": 1, "keyword": "中文 关键词", "extra": None}
        status, text = chunked_post(item["url"], json.dumps(body, ensure_ascii=False),
                                    "application/json")
        self.assertEqual(200, status, text)
        self.assertEqual(body, json.loads(text)["data"]["items"][0]["received_body"])

    def test_multi_chunk_body_is_reassembled(self):
        """分成多个块发送（含多字节中文被切断的情况）"""
        item = self.endpoint("echo", "/echo-body", method="POST")
        body = {"keyword": "中文关键词很长很长很长", "page": 1, "size": 20}
        payload = json.dumps(body, ensure_ascii=False)
        for chunk_size in (1, 3, 7, 64):
            with self.subTest(chunk_size=chunk_size):
                status, text = chunked_post(item["url"], payload, "application/json",
                                            chunk_size=chunk_size)
                self.assertEqual(200, status, text)
                self.assertEqual(body, json.loads(text)["data"]["items"][0]["received_body"])

    def test_chunked_body_is_logged_decoded(self):
        item = self.endpoint("echo", "/echo-body", method="POST")
        chunked_post(item["url"], json.dumps({"page": 1}), "application/json", chunk_size=2)
        logged = self.requests_of("echo")[-1]
        self.assertEqual('{"page": 1}', logged["body"], "日志里应是解码后的请求体")
        self.assertIn("chunked", json.dumps(logged["headers"]).lower())
        self.assertEqual(200, logged["status"])

    def test_chunked_request_without_body_still_works(self):
        """只有结束块、没有数据块"""
        item = self.endpoint("depend-body", "/customer-orders/query", method="POST")
        status, text = chunked_post(item["url"], "", "application/json")
        self.assertEqual(400, status)      # 体为空 → 缺 customerId，是业务 400 而不是读体失败
        self.assertIn("customerId", json.loads(text)["message"])

    def test_malformed_chunk_size_returns_400(self):
        item = self.endpoint("echo", "/echo-body", method="POST")
        status, text = raw_post(item["url"], b"zz\r\nabc\r\n0\r\n\r\n")
        self.assertEqual(400, status)
        self.assertIn("分块请求体格式错误", json.loads(text)["message"])

    def test_chunk_extension_is_ignored(self):
        """块长度行带 `;` 扩展也要能解析"""
        item = self.endpoint("echo", "/echo-body", method="POST")
        payload = b'{"page": 1}'
        raw = b"%x;ext=1\r\n" % len(payload) + payload + b"\r\n0\r\n\r\n"
        status, text = raw_post(item["url"], raw)
        self.assertEqual(200, status, text)
        self.assertEqual({"page": 1}, json.loads(text)["data"]["items"][0]["received_body"])

    def test_content_length_path_still_works(self):
        """向后兼容：带 Content-Length 的请求不受影响"""
        item = self.endpoint("body-page", "/products/query", method="POST")
        resp = request(item["url"], method="POST", json_body={"page": 1, "size": 20})
        self.assertEqual(200, resp.status)
        self.assertEqual(45, resp.json["data"]["total"])


if __name__ == "__main__":
    unittest.main()
