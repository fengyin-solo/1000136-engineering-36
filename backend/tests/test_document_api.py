"""体系文档启动 -> 装载 -> 接口口径的集成测试。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.bootstrap import DatasetSpec, DataPreparer  # noqa: E402
from app.routers import document as document_router  # noqa: E402
from app.services.document import DocumentService  # noqa: E402
from app.store import store  # noqa: E402

SOURCE_ROWS = [
    {"status": "草案", "文档编号": "DOCU-0001", "文档名称": "质量手册", "文档类型": "质量手册"},
    {"status": "审批中", "文档编号": "DOCU-0002", "文档名称": "文件控制程序", "文档类型": "程序文件"},
    {"status": "正式发布", "文档编号": "DOCU-0003", "文档名称": "记录控制程序", "文档类型": "程序文件"},
]


class FakeQuery(dict):
    def get(self, key, default=None):  # type: ignore[override]
        return super().get(key, default)


class FakeRequest:
    def __init__(self, params: dict[str, str]) -> None:
        self.query_params = FakeQuery(params)


class DocumentFlowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        data_dir = self.tmp / "data"
        cache_dir = self.tmp / "cache"
        data_dir.mkdir()
        (data_dir / "document_seed.json").write_text(
            json.dumps({"module": "document", "schema_version": 1, "rows": SOURCE_ROWS},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        # 用临时 spec 显式准备，避免改动 frozen settings
        from app.bootstrap import DatasetSpec

        tmp_spec = DatasetSpec(
            name="document",
            source_path=data_dir / "document_seed.json",
            cache_path=cache_dir / "document_cache.json",
            schema_version=1,
            required_fields=["文档编号", "文档名称", "文档类型"],
            key_field="文档编号",
            allowed_statuses=["草案", "审批中", "正式发布", "已作废"],
            ttl_seconds=3600,
            derive=lambda row: {
                "pending": row["status"] != "已作废",
                "abnormal": row["status"] == "已作废",
            },
        )
        self.outcome = DataPreparer().prepare(tmp_spec)
        store.prepare("document", self.outcome.rows, self.outcome.as_dict())
        self.service = DocumentService()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_bootstrap_loads_document_table(self) -> None:
        self.assertEqual(len(store.rows("document")), 3)
        self.assertTrue(store.is_prepared())
        info = store.bootstrap_info()
        self.assertEqual(info["dataset"], "document")
        self.assertIn("source_sha256", info)

    def test_filter_keeps_three_field_caliber(self) -> None:
        # 按文档编号
        items, total = self.service.list_entries(
            filters={"文档编号": "DOCU-0002"}, page=1, size=20
        )
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["文档名称"], "文件控制程序")

        # 按文档名称子串
        items, total = self.service.list_entries(
            filters={"文档名称": "程序"}, page=1, size=20
        )
        self.assertEqual(total, 2)

        # 按文档类型
        items, total = self.service.list_entries(
            filters={"文档类型": "程序文件"}, page=1, size=20
        )
        self.assertEqual(total, 2)

        # 三字段并列是 AND
        items, total = self.service.list_entries(
            filters={"文档类型": "程序文件", "文档名称": "记录"}, page=1, size=20
        )
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["文档编号"], "DOCU-0003")

        # 状态口径
        items, total = self.service.list_entries(status="正式发布", page=1, size=20)
        self.assertEqual(total, 1)

    def test_unknown_filter_field_rejected_not_silently_ignored(self) -> None:
        with self.assertRaises(ValueError):
            self.service.list_entries(filters={"不存在的口径": "x"}, page=1, size=20)

    def test_router_collects_three_query_fields_and_keyword_alias(self) -> None:
        filters = document_router._collect_filters(
            FakeRequest({"文档名称": "质量", "文档类型": "质量手册"}), keyword=None
        )
        self.assertEqual(filters, {"文档名称": "质量", "文档类型": "质量手册"})

        filters = document_router._collect_filters(FakeRequest({}), keyword="DOCU-0001")
        self.assertEqual(filters, {"文档编号": "DOCU-0001"})

        page = document_router.list_entries(
            FakeRequest({"文档类型": "程序文件"}),
            keyword=None,
            status=None,
            page=1,
            size=20,
        )
        self.assertEqual(page.total, 2)
        self.assertEqual({row["文档类型"] for row in page.items}, {"程序文件"})

    def test_stats_follow_status_caliber(self) -> None:
        stats = self.service.stats()
        values = {item["status"]: item["value"] for item in stats}
        self.assertEqual(values, {"草案": 1, "审批中": 1, "正式发布": 1, "已作废": 0})

    def test_create_requires_three_fields_and_unique_key(self) -> None:
        entry, errors = self.service.create_entry({})
        self.assertIsNone(entry)
        self.assertEqual(errors, ["文档编号", "文档名称", "文档类型"])

        entry, errors = self.service.create_entry({
            "文档编号": "DOCU-0010", "文档名称": "内部审核程序", "文档类型": "程序文件",
        })
        self.assertEqual(errors, [])
        self.assertEqual(entry["status"], "草案")

        entry, errors = self.service.create_entry({
            "文档编号": "DOCU-0010", "文档名称": "重复编号", "文档类型": "程序文件",
        })
        self.assertIsNone(entry)
        self.assertTrue(errors)

    def test_meta_exposes_single_caliber(self) -> None:
        meta = document_router.document_meta()
        self.assertEqual(meta["filter_fields"], ["文档编号", "文档名称", "文档类型"])
        self.assertEqual(meta["columns"][:3], ["文档编号", "文档名称", "文档类型"])
        self.assertEqual(meta["statuses"], ["草案", "审批中", "正式发布", "已作废"])
        self.assertIn("data_source", meta)
        self.assertEqual(len(meta["stats"]), 4)


if __name__ == "__main__":
    unittest.main()
