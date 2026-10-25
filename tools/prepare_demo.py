#!/usr/bin/env python3
"""离线生成按研发访问地址定制的演示文档，不连接HDP、模型或共享Provider。"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from demo_scenarios import MAIN, BACKUP
from scenarios import REGISTRY, negative
from scenarios.openapi import build_openapi
from server import normalize_public_base_url


def build_materials(base_url, output):
    """从同一个注册表生成文档、场景事实和演示索引；已有目录不覆盖。"""
    base_url = normalize_public_base_url(base_url)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "documents").mkdir()
    (output / "boundaries").mkdir()
    facts = {}
    for name, scenario in REGISTRY.items():
        base = "%s/s/%s" % (base_url, name)
        document = build_openapi(scenario, base)
        (output / "documents" / (name + ".json")).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        facts[name] = {"title": scenario.title, "authKind": scenario.auth.kind,
                       "document": "documents/%s.json" % name,
                       "openapiUrl": base + "/openapi.json", "endpoints": []}
        for endpoint in scenario.endpoints:
            item = endpoint.catalog(base)
            facts[name]["endpoints"].append({key: item.get(key) for key in (
                "method", "path", "recordPath", "primaryKey", "expectedTotal", "paginationType", "note")})
    for name in negative.DOCUMENTS:
        (output / "boundaries" / (name + ".json")).write_text(
            json.dumps(negative.build_document(name, base_url), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    manifest = {"demoDate": "2026-10-26", "baseUrl": base_url,
                "addressIsTemplate": base_url == "http://provider.example.invalid:18090",
                "mainMinutes": sum(item["minutes"] for item in MAIN),
                "backupMinutes": sum(item["minutes"] for item in BACKUP),
                "scenarioCount": len(facts), "endpointCount": sum(len(s.endpoints) for s in REGISTRY.values()),
                "providerFactsOnly": True, "hdpRehearsalCompletedByThisScript": False,
                "main": MAIN, "backup": BACKUP, "scenarios": facts}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 10月26日API数据源演示素材", "", "访问地址：`%s`。" % base_url,
             "", "25分钟主线＋10分钟备用。先上传对应JSON；每幕结束再进入下一幕。",
             "", "本生成器仅生成Provider材料，不代表HDP页面已排练。正式保存、首次同步及空间API发布须有当次明确资源授权；无授权时停在创建前，或展示已有演示结果。", ""]
    if manifest["addressIsTemplate"]:
        lines += ["**当前为地址模板，不能直接上传演示。部署后用真实IP:端口重新生成整个素材目录。**", ""]
    for title, scenes in (("主线", MAIN), ("备用", BACKUP)):
        lines += ["## " + title, ""]
        for scene in scenes:
            links = "、".join("[%s](documents/%s.json)" % (name, name) for name in scene["scenarios"])
            lines += ["### %s（%d分钟）" % (scene["title"], scene["minutes"]), "", links, "",
                      scene["actions"], "", "验收画面：" + scene["checkpoint"], ""]
    lines += ["## 文档边界素材", "", "[Swagger 2.0](boundaries/swagger2.json)、[OpenAPI 3.1](boundaries/openapi31.json)。",
              "", "边界口径须在目标版本预演，不按旧剧本口述为成功或失败。Provider的NEXT_URL端点也不代表产品支持下一页链接分页。", ""]
    (output / "README.md").write_text("\n".join(lines), encoding="utf-8")
    return manifest


def main():
    """生成指定访问地址的全量文档与分幕索引。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, type=normalize_public_base_url)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build_materials(args.base_url, args.output)
    print(json.dumps({"output": str(args.output), "baseUrl": result["baseUrl"],
                      "scenarios": result["scenarioCount"], "addressIsTemplate": result["addressIsTemplate"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
