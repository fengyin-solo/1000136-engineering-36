"""运行期启动状态：数据准备是否完成、失败诊断信息。

独立成模块，供 main 写入、路由层读取，避免 main 与 router 循环导入。
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

_state: dict[str, Any] = {"ready": False, "error": None, "detail": None}


def mark_ready(detail: dict[str, Any]) -> None:
    _state["ready"] = True
    _state["error"] = None
    _state["detail"] = detail


def mark_failed(error: str, detail: dict[str, Any]) -> None:
    _state["ready"] = False
    _state["error"] = error
    _state["detail"] = detail


def is_ready() -> bool:
    return bool(_state["ready"])


def snapshot() -> dict[str, Any]:
    return dict(_state)


def require_ready() -> None:
    """业务接口守卫：数据未就绪时返回 503，并指向 /api/ready 获取诊断。"""
    if not _state["ready"]:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "体系文档启动数据准备未完成，接口暂不可用",
                "diagnostics": "/api/ready",
                "error": _state["error"],
                "stage": (_state["detail"] or {}).get("stage"),
                "hint": (_state["detail"] or {}).get("hint"),
            },
        )
