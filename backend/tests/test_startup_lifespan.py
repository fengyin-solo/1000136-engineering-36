"""真实启动流程冒烟：run_bootstrap 成功装载 / 失败降级，并验证路由齐全。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app import main as main_module  # noqa: E402
from app import runtime  # noqa: E402
from app.store import store  # noqa: E402

ROWS = [
    {"status": "草案", "文档编号": "DOCU-0001", "文档名称": "质量手册", "文档类型": "质量手册"},
]
PAYLOAD = json.dumps(
    {"module": "document", "schema_version": 1, "rows": ROWS}, ensure_ascii=False
)


class LifespanSmokeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        (self.tmp / "data").mkdir()
        (self.tmp / "cache").mkdir()
        self.source = self.tmp / "data" / "document_seed.json"
        self.source.write_text(PAYLOAD, encoding="utf-8")
        os.environ["APP_DATA_DIR"] = str(self.tmp / "data")
        os.environ["APP_CACHE_DIR"] = str(self.tmp / "cache")

    def tearDown(self) -> None:
        os.environ.pop("APP_DATA_DIR", None)
        os.environ.pop("APP_CACHE_DIR", None)
        self._tmp.cleanup()
        runtime.mark_ready({"dataset": "document"})

    def test_successful_startup_loads_documents(self) -> None:
        main_module.run_bootstrap()
        self.assertTrue(runtime.is_ready())
        self.assertEqual(len(store.rows("document")), 1)
        info = store.bootstrap_info()
        self.assertEqual(info["dataset"], "document")
        self.assertFalse(info["used_cache"])

        # 重复启动：复用缓存
        main_module.run_bootstrap()
        self.assertTrue(store.bootstrap_info()["used_cache"])

    def test_failed_startup_marks_not_ready_with_diagnostics(self) -> None:
        self.source.unlink()
        main_module.run_bootstrap()  # 不抛异常：进程存活但未就绪
        self.assertFalse(runtime.is_ready())
        state = runtime.snapshot()
        self.assertEqual(state["detail"]["stage"], "读取源文件")
        self.assertIn("源文件缺失", state["error"])

    def test_routes_include_health_ready_overview(self) -> None:
        paths = {getattr(route, "path", "") for route in main_module.app.routes}
        self.assertIn("/api/health", paths)
        self.assertIn("/api/ready", paths)
        self.assertIn("/api/overview", paths)


if __name__ == "__main__":
    unittest.main()
