#!/usr/bin/env python3
"""按白名单构建Linux演示包；不携带Git、IDE、日志、运行配置或本地业务记录。"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from prepare_demo import build_materials
from server import normalize_public_base_url

NAME = "api-test-provider-demo-20261026"


def source_files():
    """只收录项目运行、测试及演示交付所需文件。"""
    names = ["server.py", "run.sh", "stop.sh", "run_tests.sh", "README.md", "Dockerfile", ".dockerignore"]
    for directory in ("scenarios", "tests", "tools"):
        names += [str(path.relative_to(ROOT)) for path in sorted((ROOT / directory).glob("*.py"))]
    names += [str(path.relative_to(ROOT)) for path in sorted((ROOT / "openapi").iterdir())
              if path.is_file() and path.suffix in (".json", ".yaml", ".yml", ".md")]
    names += ["docs/DEPLOYMENT.md", "docs/DEMO-20261026.md"]
    return names


def build_bundle(output, base_url):
    """输出一个可解压运行的包和校验文件，已有同名包不覆盖。"""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / (NAME + ".tar.gz")
    checksum = output / (NAME + ".tar.gz.sha256")
    if destination.exists() or checksum.exists():
        raise FileExistsError("已有同名部署包，请使用新的输出目录")
    with tempfile.TemporaryDirectory(prefix="api-demo-bundle-") as temporary:
        stage = Path(temporary) / NAME
        stage.mkdir()
        for name in source_files():
            original = ROOT / name
            if original.is_symlink() or not original.is_file():
                raise ValueError("打包白名单文件不存在或为链接：" + name)
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
        materials = build_materials(base_url, stage / "demo-materials")
        hashes = {str(path.relative_to(stage)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(stage.rglob("*")) if path.is_file()}
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        manifest = {"sourceCommit": commit.stdout.strip() if commit.returncode == 0 else None,
                    "buildIncludesWorkingTree": True, "baseUrl": materials["baseUrl"],
                    "addressIsTemplate": materials["addressIsTemplate"], "filesSha256": hashes}
        (stage / "bundle-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with tarfile.open(destination, "w:gz") as archive:
            archive.add(stage, arcname=NAME)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    checksum.write_text("%s  %s\n" % (digest, destination.name), encoding="utf-8")
    return {"archive": str(destination.resolve()), "sha256": digest,
            "bytes": destination.stat().st_size, "files": len(hashes) + 1,
            "addressIsTemplate": materials["addressIsTemplate"]}


def main():
    """生成便携包；真实研发IP未确定时明确生成地址模板。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--base-url", type=normalize_public_base_url,
                        default="http://provider.example.invalid:18090")
    args = parser.parse_args()
    print(json.dumps(build_bundle(args.output, args.base_url), ensure_ascii=False))


if __name__ == "__main__":
    main()
