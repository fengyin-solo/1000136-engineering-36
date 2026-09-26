"""体系文档启动数据准备流水线测试。

覆盖：首次启动、重复启动（缓存复用）、源文件变更重建、异常中断恢复、
缓存过期、缓存损坏、源文件缺失/口径错误的可诊断失败、并发锁互斥。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.bootstrap import DataPrepError, DataPreparer, DatasetSpec  # noqa: E402

SOURCE_ROWS = [
    {"status": "草案", "文档编号": "DOCU-0001", "文档名称": "质量手册", "文档类型": "质量手册"},
    {"status": "审批中", "文档编号": "DOCU-0002", "文档名称": "文件控制程序", "文档类型": "程序文件"},
    {"status": "正式发布", "文档编号": "DOCU-0003", "文档名称": "记录控制程序", "文档类型": "程序文件"},
]

STATUSES = ["草案", "审批中", "正式发布", "已作废"]


def write_source(path: Path, rows=None, *, schema_version=1, module="document") -> None:
    payload = {
        "module": module,
        "schema_version": schema_version,
        "rows": rows if rows is not None else SOURCE_ROWS,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _derive_flags(row):
    status = row["status"]
    return {"pending": status != "已作废", "abnormal": status == "已作废"}


def make_spec(base: Path, *, ttl_seconds=24 * 3600) -> DatasetSpec:
    return DatasetSpec(
        name="document",
        source_path=base / "document_seed.json",
        cache_path=base / "cache" / "document_cache.json",
        schema_version=1,
        required_fields=["文档编号", "文档名称", "文档类型"],
        key_field="文档编号",
        allowed_statuses=list(STATUSES),
        ttl_seconds=ttl_seconds,
        derive=_derive_flags,
    )


class BootstrapTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        write_source(self.tmp / "document_seed.json")
        self.preparer = DataPreparer()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_first_startup_builds_cache_and_loads_rows(self) -> None:
        spec = make_spec(self.tmp)
        outcome = self.preparer.prepare(spec)

        self.assertFalse(outcome.used_cache)
        self.assertEqual(outcome.row_count, 3)
        self.assertTrue(spec.cache_path.is_file())
        # id 由准备器统一分配，编号/名称/类型口径原样保留
        self.assertEqual([row["文档编号"] for row in outcome.rows], [
            "DOCU-0001", "DOCU-0002", "DOCU-0003",
        ])
        self.assertEqual(outcome.rows[0]["文档名称"], "质量手册")
        self.assertEqual(outcome.rows[0]["文档类型"], "质量手册")
        self.assertEqual([row["id"] for row in outcome.rows], [1, 2, 3])
        # pending/abnormal 由状态推导
        self.assertTrue(outcome.rows[0]["pending"])
        self.assertTrue(outcome.rows[2]["pending"])
        cached = json.loads(spec.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(cached["source_sha256"], outcome.source_sha256)

    def test_repeated_startup_reuses_valid_cache(self) -> None:
        spec = make_spec(self.tmp)
        first = self.preparer.prepare(spec)
        second = self.preparer.prepare(spec)

        self.assertTrue(second.used_cache)
        self.assertEqual(second.source_sha256, first.source_sha256)
        self.assertEqual(second.rows, first.rows)
        self.assertIn("一致", second.reason)

    def test_changed_source_invalidates_cache(self) -> None:
        spec = make_spec(self.tmp)
        self.preparer.prepare(spec)

        rows = [dict(row) for row in SOURCE_ROWS]
        rows[0]["文档名称"] = "质量手册（第2版）"
        write_source(spec.source_path, rows)

        outcome = self.preparer.prepare(spec)
        self.assertFalse(outcome.used_cache)
        self.assertIn("哈希", outcome.reason)
        self.assertEqual(outcome.rows[0]["文档名称"], "质量手册（第2版）")

    def test_schema_version_mismatch_forces_rebuild_failure(self) -> None:
        spec = make_spec(self.tmp)
        self.preparer.prepare(spec)
        write_source(spec.source_path, schema_version=2)

        with self.assertRaises(DataPrepError) as caught:
            self.preparer.prepare(spec)
        self.assertEqual(caught.exception.stage, "版本一致性")
        # 旧缓存不得被当作有效数据返回
        self.assertFalse(spec.cache_path.exists() and json.loads(
            spec.cache_path.read_text(encoding="utf-8")
        )["schema_version"] == 2)

    def test_expired_cache_is_rebuilt_from_source(self) -> None:
        spec = make_spec(self.tmp, ttl_seconds=1)
        self.preparer.prepare(spec)
        time.sleep(1.1)

        outcome = self.preparer.prepare(spec)
        self.assertFalse(outcome.used_cache)
        self.assertIn("过期", outcome.reason)
        # 重建后新缓存立即有效
        self.assertTrue(self.preparer.prepare(spec).used_cache)

    def test_zero_ttl_cache_never_expires(self) -> None:
        spec = make_spec(self.tmp, ttl_seconds=0)
        self.preparer.prepare(spec)
        time.sleep(0.1)
        self.assertTrue(self.preparer.prepare(spec).used_cache)

    def test_corrupted_cache_is_rebuilt(self) -> None:
        spec = make_spec(self.tmp)
        self.preparer.prepare(spec)

        # 场景 1：半截 JSON（模拟写缓存时被 kill -9）
        spec.cache_path.write_text('{"module": "document", "rows": [', encoding="utf-8")
        outcome = self.preparer.prepare(spec)
        self.assertFalse(outcome.used_cache)
        self.assertIn("损坏", outcome.reason)
        self.assertEqual(outcome.row_count, 3)

        # 场景 2：残留临时文件不应阻塞下一次重建
        spec.cache_path.with_name(spec.cache_path.name + ".tmp").write_text("partial", encoding="utf-8")
        spec.cache_path.unlink()
        outcome = self.preparer.prepare(spec)
        self.assertFalse(outcome.used_cache)
        self.assertEqual(outcome.row_count, 3)
        self.assertFalse(spec.cache_path.with_name(spec.cache_path.name + ".tmp").exists())

    def test_tampered_rows_fingerprint_triggers_rebuild(self) -> None:
        spec = make_spec(self.tmp)
        self.preparer.prepare(spec)
        cached = json.loads(spec.cache_path.read_text(encoding="utf-8"))
        cached["rows"][0]["文档名称"] = "被篡改的旧内容"
        spec.cache_path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")

        outcome = self.preparer.prepare(spec)
        self.assertFalse(outcome.used_cache)
        self.assertEqual(outcome.rows[0]["文档名称"], "质量手册")

    def test_row_count_mismatch_triggers_rebuild(self) -> None:
        spec = make_spec(self.tmp)
        self.preparer.prepare(spec)
        cached = json.loads(spec.cache_path.read_text(encoding="utf-8"))
        cached["row_count"] = 99
        spec.cache_path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")

        outcome = self.preparer.prepare(spec)
        self.assertFalse(outcome.used_cache)
        self.assertIn("行数", outcome.reason)

    def test_missing_source_fails_loudly_without_stale_cache(self) -> None:
        spec = make_spec(self.tmp)
        self.preparer.prepare(spec)
        spec.source_path.unlink()

        with self.assertRaises(DataPrepError) as caught:
            self.preparer.prepare(spec)
        self.assertEqual(caught.exception.stage, "读取源文件")
        self.assertIn("源文件缺失", str(caught.exception))
        self.assertIsNotNone(caught.exception.hint)

    def test_invalid_source_field_fails_with_stage(self) -> None:
        spec = make_spec(self.tmp)
        bad_rows = [{"status": "草案", "文档编号": "DOCU-0009", "文档名称": "", "文档类型": "质量手册"}]
        write_source(spec.source_path, bad_rows)

        with self.assertRaises(DataPrepError) as caught:
            self.preparer.prepare(spec)
        self.assertEqual(caught.exception.stage, "字段口径")
        self.assertIn("文档名称", str(caught.exception))

    def test_duplicate_key_fails(self) -> None:
        spec = make_spec(self.tmp)
        bad_rows = [dict(SOURCE_ROWS[0]), dict(SOURCE_ROWS[0])]
        write_source(spec.source_path, bad_rows)
        with self.assertRaises(DataPrepError) as caught:
            self.preparer.prepare(spec)
        self.assertEqual(caught.exception.stage, "唯一性校验")

    def test_illegal_status_fails(self) -> None:
        spec = make_spec(self.tmp)
        bad_rows = [{"status": "未知状态", "文档编号": "X-1", "文档名称": "n", "文档类型": "t"}]
        write_source(spec.source_path, bad_rows)
        with self.assertRaises(DataPrepError) as caught:
            self.preparer.prepare(spec)
        self.assertEqual(caught.exception.stage, "状态口径")

    def test_bad_json_fails_with_stage(self) -> None:
        spec = make_spec(self.tmp)
        spec.source_path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(DataPrepError) as caught:
            self.preparer.prepare(spec)
        self.assertEqual(caught.exception.stage, "源文件语法")

    def test_force_rebuild_ignores_fresh_cache(self) -> None:
        spec = make_spec(self.tmp)
        self.preparer.prepare(spec)
        outcome = self.preparer.prepare(spec, force_rebuild=True)
        self.assertFalse(outcome.used_cache)
        self.assertEqual(outcome.reason, "强制重建")

    def test_concurrent_prepare_builds_cache_once(self) -> None:
        spec = make_spec(self.tmp)
        results: list[bool] = []
        lock = threading.Lock()

        def worker() -> None:
            outcome = self.preparer.prepare(spec)
            with lock:
                results.append(outcome.used_cache)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 4)
        self.assertEqual(results.count(False), 1)  # 恰好一个进程重建
        self.assertEqual(results.count(True), 3)

    def test_inspect_cache_is_read_only(self) -> None:
        spec = make_spec(self.tmp)
        report = self.preparer.inspect_cache(spec)
        self.assertFalse(report["cache_valid"])
        self.assertFalse(spec.cache_path.exists())  # 只读检查不产生缓存
        self.assertEqual(report["source_row_count"], 3)

        self.preparer.prepare(spec)
        report = self.preparer.inspect_cache(spec)
        self.assertTrue(report["cache_valid"])
        self.assertEqual(report["cache_row_count"], 3)


if __name__ == "__main__":
    unittest.main()
