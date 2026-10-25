"""研发IP部署地址及演示材料契约；只启动自有随机回环实例。"""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from server import create_server, normalize_public_base_url
from scenarios import REGISTRY
from tests.support import request
from tools.check_demo import check
from tools.prepare_demo import build_materials


class PublicAddressTest(unittest.TestCase):
    """监听端口与外部访问地址分离，所有对外材料使用同一地址。"""

    def test_external_address_in_docs_token_catalog_and_pagination(self):
        public = "https://203.0.113.8:19443"
        server = create_server(port=0, quiet=True, advertise_base_url=public + "/")
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:%d" % server.server_address[1]
        catalog = request(base + "/__admin/scenarios", headers={"Host": "wrong.invalid"}).json
        self.assertEqual(public, catalog["baseUrl"])
        oauth = next(item for item in catalog["scenarios"] if item["name"] == "oauth2-post")
        self.assertEqual(public + "/s/oauth2-post/oauth/token", oauth["auth"]["tokenFlow"]["url"])
        self.assertEqual([{"url": public + "/s/none"}], request(base + "/s/none/openapi.json").json["servers"])
        self.assertEqual(public + "/s/next-url/suppliers?page=2", request(base + "/s/next-url/suppliers").json["next"])
        cross_host = request(base + "/s/next-url/suppliers-crosshost").json["next"]
        self.assertNotEqual(urlsplit(public).netloc, urlsplit(cross_host).netloc)
        self.assertEqual([{"url": public}], request(base + "/openapi/none.json").json["servers"])

    def test_invalid_public_address_rejected_before_binding(self):
        for value in ("", "ftp://demo:90", "http://user:password@demo:90", "http://demo:0",
                      "http://demo:99999", "http://demo/path", "http://demo?q=1", "http://demo#hash"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_public_base_url(value)


class DemoMaterialsTest(unittest.TestCase):
    """文档来自既有注册表，模板与实际研发地址明确区分。"""

    def test_all_scenarios_and_narrative_use_requested_address(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "materials"
            manifest = build_materials("http://203.0.113.8:19090", output)
            self.assertEqual((25, 10), (manifest["mainMinutes"], manifest["backupMinutes"]))
            self.assertEqual(set(REGISTRY), set(manifest["scenarios"]))
            self.assertFalse(manifest["hdpRehearsalCompletedByThisScript"])
            self.assertFalse(manifest["addressIsTemplate"])
            for name in REGISTRY:
                document = json.loads((output / "documents" / (name + ".json")).read_text())
                self.assertEqual([{"url": "http://203.0.113.8:19090/s/" + name}], document["servers"])
            for scene in manifest["main"] + manifest["backup"]:
                self.assertTrue(set(scene["scenarios"]).issubset(REGISTRY))
            with self.assertRaises(FileExistsError):
                build_materials("http://203.0.113.8:19090", output)

    def test_deployment_check_only_reads_health_catalog_and_documents(self):
        server = create_server(port=0, quiet=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:%d" % server.server_address[1]
        result = check(base)
        self.assertEqual("passed", result["status"])
        self.assertGreater(result["checkedDocuments"], 15)
        rows = server.request_log.query()[1]
        self.assertTrue(rows)
        self.assertTrue(all(row["method"] == "GET" and row["path"].endswith("/openapi.json") for row in rows))
