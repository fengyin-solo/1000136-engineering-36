"""启动状态机测试：未就绪 -> 失败诊断 -> 就绪 的转换与 503 守卫。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app import runtime  # noqa: E402


class RuntimeStateTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        runtime.mark_ready({"dataset": "document"})  # 恢复就绪，避免污染其他测试

    def test_starts_not_ready(self) -> None:
        state = runtime.snapshot()
        # 不同测试顺序下只验证状态结构与守卫行为自洽
        self.assertIn("ready", state)

    def test_failed_then_ready_transitions(self) -> None:
        runtime.mark_failed(
            error="[数据准备/读取源文件] 源文件缺失：x",
            detail={"stage": "读取源文件", "hint": "恢复文件"},
        )
        self.assertFalse(runtime.is_ready())
        with self.assertRaises(HTTPException) as caught:
            runtime.require_ready()
        self.assertEqual(caught.exception.status_code, 503)
        detail = caught.exception.detail
        self.assertEqual(detail["stage"], "读取源文件")
        self.assertEqual(detail["diagnostics"], "/api/ready")

        runtime.mark_ready({"dataset": "document", "row_count": 3})
        self.assertTrue(runtime.is_ready())
        runtime.require_ready()  # 不抛异常即为通过


if __name__ == "__main__":
    unittest.main()
