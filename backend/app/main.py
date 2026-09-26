"""实验室样品检测管理平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
存活检查：GET /api/health
就绪检查：GET /api/ready  （启动数据准备失败时返回 503 并附带诊断信息）
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app import runtime
from app.bootstrap import DataPrepError, DataPreparer
from app.config import settings
from app.datasets import document_spec
from app.routers import ROUTERS
from app.store import store

logger = logging.getLogger("app.main")


def run_bootstrap() -> None:
    """执行启动数据准备；失败不杀死进程，而是标记未就绪并保留诊断信息。

    这样 /api/health 仍可存活探活、/api/ready 返回 503 供编排重试，
    业务接口在未就绪时拒绝服务，避免把空表或旧内容当正常数据返回。
    修复源文件/缓存后重启进程即可完成准备（缓存可复用，重试成本很低）。
    """
    spec = document_spec()
    try:
        outcome = DataPreparer().prepare(spec)
    except DataPrepError as exc:
        runtime.mark_failed(
            error=str(exc),
            detail={"stage": exc.stage, "hint": exc.hint},
        )
        logger.error("体系文档启动数据准备失败：%s", exc)
        return
    store.prepare("document", outcome.rows, outcome.as_dict())
    runtime.mark_ready(outcome.as_dict())
    logger.info(
        "体系文档数据就绪：%d 条（%s，源哈希 %.8s）",
        outcome.row_count,
        "缓存复用" if outcome.used_cache else "源文件重建",
        outcome.source_sha256,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    run_bootstrap()
    yield


app = FastAPI(title="实验室样品检测管理平台", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in ROUTERS:
    app.include_router(module.router)


@app.get("/api/health")
def health() -> dict[str, object]:
    """存活检查：进程在监听即可响应，不代表业务数据已就绪。"""
    return {"ok": True, "app": settings.app_name, "ready": runtime.is_ready()}


@app.get("/api/ready")
def ready() -> dict[str, object]:
    """就绪检查：启动依赖（体系文档源文件与缓存）准备完成才返回 200。"""
    state = runtime.snapshot()
    if not state["ready"]:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "启动数据准备尚未完成或失败",
                "error": state["error"],
                "stage": (state["detail"] or {}).get("stage"),
                "hint": (state["detail"] or {}).get("hint"),
            },
        )
    return {
        "ok": True,
        "app": settings.app_name,
        "modules": len(store.module_names()),
        "datasets": [store.bootstrap_info()],
    }


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。"""
    return store.overview()
