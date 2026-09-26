#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
# 体系文档数据准备：首次建缓存、重复启动校验、异常中断自愈；失败即中止启动
.venv/bin/python -m app.bootstrap
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
