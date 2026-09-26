"""体系文档业务规则：状态流转、字段校验与筛选口径都收在这里。

字段与状态口径统一来自 app.datasets，接口层、前端与启动准备共用同一份声明。
"""
from __future__ import annotations

from typing import Any

from app.datasets import (
    DOCUMENT_ACTION_RULES,
    DOCUMENT_FIELDS,
    DOCUMENT_NEGATIVE_ACTIONS,
    DOCUMENT_STATUS_LABELS,
    DOCUMENT_STATUS_ORDER,
)
from app.store import store

MODULE = "document"
REQUIRED_FIELDS = list(DOCUMENT_FIELDS)
STATUS_ORDER = list(DOCUMENT_STATUS_ORDER)
ACTION_RULES = dict(DOCUMENT_ACTION_RULES)
NEGATIVE_ACTIONS = list(DOCUMENT_NEGATIVE_ACTIONS)


class DocumentService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """按文档编号、文档名称、文档类型做并列子串筛选，按状态精确筛选。"""
        rows = store.rows(MODULE)
        for field_name, keyword in (filters or {}).items():
            keyword = (keyword or "").strip()
            if not keyword:
                continue
            if field_name not in REQUIRED_FIELDS:
                # 未知口径字段直接拒绝，避免前端传了参数却被静默忽略
                raise ValueError(f"体系文档不支持按「{field_name}」检索")
            rows = [
                row for row in rows if keyword in str(row.get(field_name, ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def stats(self) -> list[dict[str, object]]:
        """按统一状态口径统计看板卡片。"""
        rows = store.rows(MODULE)
        return [
            {"label": label, "status": state, "value": sum(
                1 for row in rows if row.get("status") == state
            )}
            for state, label in DOCUMENT_STATUS_LABELS
        ]

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        key = str(values[REQUIRED_FIELDS[0]]).strip()
        if any(str(row.get(REQUIRED_FIELDS[0])) == key for row in rows):
            return None, [f"{REQUIRED_FIELDS[0]}已存在：{key}"]
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field_name in DOCUMENT_FIELDS:
            entry[field_name] = str(values[field_name]).strip()
        for field_name in ("编制人", "版本号", "生效日期", "分发范围", "文档状态"):
            if values.get(field_name):
                entry[field_name] = values[field_name]
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"体系文档 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于体系文档可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"体系文档已{action}"
