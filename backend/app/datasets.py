"""数据集口径声明：体系文档的字段、状态与启动依赖（源文件/缓存）集中在此。

服务层、接口层、缓存构建都从这里取常量，避免「文档编号/文档名称/文档类型」
三处口径在不同文件里各写一份、改漏一处。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.bootstrap import DatasetSpec
from app.config import settings

# 体系文档统一识别口径：以下三个名称在源文件、缓存、接口查询参数与前端表格中完全一致
DOCUMENT_FIELDS = ["文档编号", "文档名称", "文档类型"]
DOCUMENT_LIST_FIELDS = [
    "文档编号",
    "文档名称",
    "文档类型",
    "编制人",
    "版本号",
    "生效日期",
    "分发范围",
    "文档状态",
]
DOCUMENT_STATUS_ORDER = ["草案", "审批中", "正式发布", "已作废"]
DOCUMENT_ACTION_RULES = {"提交审批": "审批中", "正式发布": "正式发布", "作废文档": "已作废"}
DOCUMENT_NEGATIVE_ACTIONS = ["作废文档"]
# 看板统计卡：状态口径与状态序列保持同源
DOCUMENT_STATUS_LABELS = [
    ("草案", "草案文档"),
    ("审批中", "审批中文档"),
    ("正式发布", "正式文档"),
    ("已作废", "作废文档"),
]
SCHEMA_VERSION = 1


def _derive_flags(row: dict[str, Any]) -> dict[str, Any]:
    """pending/abnormal 由 status 与动作口径推导，源文件无需、也不应自带。"""
    status = str(row["status"])
    return {
        "pending": status != DOCUMENT_STATUS_ORDER[-1],
        "abnormal": status == DOCUMENT_STATUS_ORDER[-1],
    }


def document_spec() -> DatasetSpec:
    """体系文档启动依赖声明。

    路径与 TTL 在调用时读取环境变量（而非只在 settings 导入时读一次），
    便于测试切换临时目录，也便于进程启动脚本注入部署路径。
    """
    data_dir = Path(os.environ.get("APP_DATA_DIR", str(settings.data_dir)))
    cache_dir = Path(os.environ.get("APP_CACHE_DIR", str(settings.cache_dir)))
    try:
        ttl = int(os.environ.get("DOCUMENT_CACHE_TTL_SECONDS", str(settings.document_cache_ttl_seconds)))
    except ValueError:
        ttl = settings.document_cache_ttl_seconds
    return DatasetSpec(
        name="document",
        source_path=data_dir / "document_seed.json",
        cache_path=cache_dir / "document_cache.json",
        schema_version=SCHEMA_VERSION,
        required_fields=list(DOCUMENT_FIELDS),
        key_field="文档编号",
        allowed_statuses=list(DOCUMENT_STATUS_ORDER),
        ttl_seconds=ttl,
        derive=_derive_flags,
    )
