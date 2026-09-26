"""启动数据准备的命令行入口：部署前预检、手动重建与状态查看。

用法（在 backend 目录下）：

    python -m app.prepare_data             # 准备数据，缓存有效则复用
    python -m app.prepare_data --force     # 忽略缓存，从源文件强制重建
    python -m app.prepare_data --status    # 只查看缓存状态，不写入

失败时以退出码 1 结束并打印失败阶段与修复建议，可直接接入容器健康检查或 CI。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.bootstrap import DataPrepError, DataPreparer
from app.datasets import document_spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="体系文档启动数据准备")
    parser.add_argument("--force", action="store_true", help="忽略缓存，强制从源文件重建")
    parser.add_argument("--status", action="store_true", help="只输出准备结果，不触发重建")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    spec = document_spec()

    if args.status:
        try:
            report = DataPreparer().inspect_cache(spec)
        except DataPrepError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    try:
        outcome = DataPreparer().prepare(spec, force_rebuild=args.force)
    except DataPrepError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    action = "已重建缓存" if not outcome.used_cache else "缓存校验通过并复用"
    print(
        f"体系文档数据准备完成：{action}，{outcome.row_count} 条，"
        f"schema v{outcome.schema_version}，源 SHA-256 {outcome.source_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
