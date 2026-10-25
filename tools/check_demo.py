#!/usr/bin/env python3
"""只读核对部署实例的健康、场景与文档地址，不调用业务、令牌、模型或故障开关。"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from demo_scenarios import MAIN, BACKUP
from server import normalize_public_base_url


def check(base_url, public_base_url=None):
    """只检查同一访问入口，不跟随清单里的令牌或业务地址。"""
    base_url = normalize_public_base_url(base_url)
    public_base_url = normalize_public_base_url(public_base_url or base_url)

    def read(path):
        with urllib.request.urlopen(base_url + path, timeout=10) as response:
            return json.load(response)

    if read("/health").get("status") != "ok":
        raise ValueError("健康检查未通过")
    catalog = read("/__admin/scenarios")
    if catalog.get("baseUrl") != public_base_url:
        raise ValueError("实例宣告地址与演示地址不一致，请核对--advertise-base-url")
    entries = {item["name"]: item for item in catalog["scenarios"]}
    required = sorted({name for scene in MAIN + BACKUP for name in scene["scenarios"]})
    for name in required:
        if name not in entries:
            raise ValueError("缺少演示场景：" + name)
        document = read("/s/%s/openapi.json" % name)
        if document.get("servers") != [{"url": "%s/s/%s" % (public_base_url, name)}]:
            raise ValueError("场景文档地址不一致：" + name)
    return {"status": "passed", "baseUrl": base_url, "publicBaseUrl": public_base_url,
            "scenarioCount": len(entries), "checkedDocuments": len(required),
            "businessOrModelRequests": False, "hdpPageRehearsal": False}


def main():
    """读取部署入口并输出不含凭证的检查结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, type=normalize_public_base_url)
    parser.add_argument("--public-base-url", type=normalize_public_base_url,
                        help="从服务器本机检查时另传客户端访问地址")
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.base_url, args.public_base_url), ensure_ascii=False))
    except (OSError, ValueError) as error:
        parser.exit(1, "部署检查失败：%s\n" % error)


if __name__ == "__main__":
    main()
