#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
# 启动前数据准备：源文件缺失/口径不符时在此处失败并打印修复建议，
# 不会带着空表或旧缓存启动；修复后重新执行本脚本即可恢复。
.venv/bin/python -m app.prepare_data
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
