"""体系文档接口：维护体系文档，覆盖提交审批、正式发布、作废文档等动作。

查询参数、表格列、状态集合全部取自 app.datasets 的统一口径，
/meta 把同一份口径下发给前端，避免前后端字段名称各写一套。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.datasets import (
    DOCUMENT_FIELDS,
    DOCUMENT_LIST_FIELDS,
    DOCUMENT_STATUS_LABELS,
    DOCUMENT_STATUS_ORDER,
)
from app.runtime import require_ready
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.document import DocumentService
from app.store import store

# 整个体系文档路由都依赖启动数据准备完成；未就绪统一返回 503
router = APIRouter(
    prefix="/api/document",
    tags=["体系文档"],
    dependencies=[Depends(require_ready)],
)

service = DocumentService()

LIST_FIELDS = list(DOCUMENT_LIST_FIELDS)
STATUSES = list(DOCUMENT_STATUS_ORDER)


ALLOWED_QUERY_PARAMS = {*DOCUMENT_FIELDS, "keyword", "status", "page", "size"}


def _collect_filters(request: Request, keyword: str | None) -> dict[str, str]:
    """收集文档编号/名称/类型三个口径的查询参数。

    历史上只支持 keyword（按文档编号检索），保留兼容并归一到同一字段；
    任何不在统一口径里的参数直接拒绝，避免前端传错字段却被静默忽略。
    """
    unknown = [name for name in request.query_params if name not in ALLOWED_QUERY_PARAMS]
    if unknown:
        raise ValueError(f"体系文档不支持按「{'、'.join(unknown)}」检索，"
                         f"支持口径：{'、'.join(DOCUMENT_FIELDS)}")
    filters: dict[str, str] = {}
    for field_name in DOCUMENT_FIELDS:
        value = request.query_params.get(field_name)
        if value and value.strip():
            filters[field_name] = value.strip()
    if keyword and keyword.strip():
        filters["文档编号"] = keyword.strip()
    return filters


@router.get("/meta")
def document_meta() -> dict[str, Any]:
    """口径元数据：前端表格列、筛选字段、状态、统计卡与数据来源版本都以此为准。"""
    return {
        "module": "document",
        "columns": LIST_FIELDS,
        "filter_fields": list(DOCUMENT_FIELDS),
        "statuses": STATUSES,
        "actions": ["提交审批", "正式发布", "作废文档"],
        "status_labels": [
            {"status": state, "label": label} for state, label in DOCUMENT_STATUS_LABELS
        ],
        "stats": service.stats(),
        "data_source": store.bootstrap_info(),
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="兼容旧参数：按文档编号检索"),
    status: str | None = Query(default=None, description="草案、审批中、正式发布、已作废"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按文档编号、文档名称、文档类型与状态过滤；没有数据时返回空页，不报错。"""
    if page < 1:
        raise HTTPException(status_code=400, detail="页码从 1 开始")
    if size < 1 or size > 200:
        raise HTTPException(status_code=400, detail="每页条数需在 1 到 200 之间")
    try:
        items, total = service.list_entries(
            filters=_collect_filters(request, keyword),
            status=status,
            page=page,
            size=size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出体系文档清单：返回当前全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "document", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条体系文档明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"体系文档 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条体系文档，缺字段或编号重复时说明原因而不是静默丢弃。"""
    entry, errors = service.create_entry(payload.values)
    if errors:
        return ActionResult(ok=False, message=f"登记未通过：{'、'.join(errors)}")
    return ActionResult(ok=True, message="体系文档已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条体系文档执行提交审批、正式发布、作废文档；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
