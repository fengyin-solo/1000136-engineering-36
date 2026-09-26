"""体系文档数据准备的一致性测试。

覆盖三种启动场景（首次启动、重复启动、异常中断）与两条工程要求：
失败可诊断（BootstrapError 带阶段与上下文）、完成前后
文档编号/文档名称/文档类型 口径一致（种子 → 产物 → 接口）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import bootstrap


class BootstrapScenarioTest(unittest.TestCase):
    """每个用例都用独立临时目录当数据产物目录，互不影响。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_file = self.data_dir / bootstrap.DATA_FILE
        self.manifest_file = self.data_dir / bootstrap.MANIFEST_FILE

    def read_prepared_rows(self) -> list[dict]:
        return json.loads(self.data_file.read_text(encoding="utf-8"))

    def test_first_startup_builds_cache(self) -> None:
        """首次启动：产物不存在 → 全量重建，建完即通过一致性检查。"""
        self.assertFalse(self.data_file.exists())
        manifest, rebuilt = bootstrap.ensure_prepared(self.data_dir)
        self.assertTrue(rebuilt)
        self.assertTrue(self.data_file.is_file())
        self.assertTrue(self.manifest_file.is_file())
        self.assertEqual(manifest["row_count"], len(bootstrap.seed_rows()))
        # 完成前后口径一致：文档编号/文档名称/文档类型 与种子数据完全对齐
        self.assertEqual(
            bootstrap.key_tuples(self.read_prepared_rows()),
            bootstrap.key_tuples(bootstrap.seed_rows()),
        )

    def test_repeat_startup_reuses_valid_cache(self) -> None:
        """重复启动：指纹一致 → 直接复用，不重写任何文件。"""
        bootstrap.ensure_prepared(self.data_dir)
        data_before = self.data_file.read_bytes()
        manifest_before = self.manifest_file.read_bytes()
        _, rebuilt = bootstrap.ensure_prepared(self.data_dir)
        self.assertFalse(rebuilt)
        self.assertEqual(self.data_file.read_bytes(), data_before)
        self.assertEqual(self.manifest_file.read_bytes(), manifest_before)

    def test_missing_data_file_is_rebuilt(self) -> None:
        """缺少文件：数据文件丢失 → 重建恢复，不报错也不沿用旧内容。"""
        bootstrap.ensure_prepared(self.data_dir)
        self.data_file.unlink()
        _, rebuilt = bootstrap.ensure_prepared(self.data_dir)
        self.assertTrue(rebuilt)
        self.assertEqual(
            bootstrap.key_tuples(self.read_prepared_rows()),
            bootstrap.key_tuples(bootstrap.seed_rows()),
        )

    def test_stale_cache_is_rebuilt_not_served(self) -> None:
        """缓存过期：产物被改旧 → 重建回种子口径，旧内容不会被悄悄放行。"""
        bootstrap.ensure_prepared(self.data_dir)
        rows = self.read_prepared_rows()
        rows[0]["文档名称"] = "过期缓存里的旧名称"
        self.data_file.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        _, rebuilt = bootstrap.ensure_prepared(self.data_dir)
        self.assertTrue(rebuilt)
        restored = self.read_prepared_rows()
        self.assertNotEqual(restored[0]["文档名称"], "过期缓存里的旧名称")
        self.assertEqual(
            bootstrap.key_tuples(restored), bootstrap.key_tuples(bootstrap.seed_rows())
        )

    def test_interrupted_run_recovers_on_retry(self) -> None:
        """异常中断：半截数据文件 + 残留临时文件 + 清单缺失 → 重试可恢复。"""
        bootstrap.ensure_prepared(self.data_dir)
        self.data_file.write_text('{"半截文件": ', encoding="utf-8")
        (self.data_dir / ".document.json.tmp-999").write_text("残留", encoding="utf-8")
        self.manifest_file.unlink()
        _, rebuilt = bootstrap.ensure_prepared(self.data_dir)
        self.assertTrue(rebuilt)
        self.assertEqual(list(self.data_dir.glob(".*.tmp-*")), [])
        self.assertEqual(
            bootstrap.key_tuples(self.read_prepared_rows()),
            bootstrap.key_tuples(bootstrap.seed_rows()),
        )

    def test_seed_change_invalidates_cache(self) -> None:
        """种子数据变更：指纹对不上 → 缓存作废重建，完成前后口径仍一致。"""
        bootstrap.ensure_prepared(self.data_dir)
        changed = bootstrap.seed_rows()
        changed[0]["文档类型"] = "程序文件（新版）"
        with mock.patch.object(bootstrap, "seed_rows", return_value=changed):
            _, rebuilt = bootstrap.ensure_prepared(self.data_dir)
            self.assertTrue(rebuilt)
            self.assertEqual(
                bootstrap.key_tuples(self.read_prepared_rows()),
                bootstrap.key_tuples(changed),
            )

    def test_failure_is_diagnosable(self) -> None:
        """失败可诊断：目录不可写时报错带阶段、路径与原因，而不是静默失败。"""
        blocker = Path(self.tmp.name) / "not-a-dir"
        blocker.write_text("occupied", encoding="utf-8")
        with self.assertRaises(bootstrap.BootstrapError) as ctx:
            bootstrap.ensure_prepared(blocker)
        self.assertEqual(ctx.exception.stage, "prepare")
        message = str(ctx.exception)
        self.assertIn("stage=prepare", message)
        self.assertIn(str(blocker), message)

    def test_seed_problems_are_diagnosable(self) -> None:
        """失败可诊断：种子数据本身缺关键字段时，准备阶段就拦下并说明。"""
        bad_rows = [{"文档编号": "DOCU-0001", "文档名称": "", "文档类型": "程序文件"}]
        with mock.patch.object(bootstrap, "seed_rows", return_value=bad_rows):
            with self.assertRaises(bootstrap.BootstrapError) as ctx:
                bootstrap.prepare(self.data_dir)
        self.assertEqual(ctx.exception.stage, "prepare")
        self.assertIn("文档名称", str(ctx.exception))

    def test_cli_check_mode(self) -> None:
        """--check 只检查不重建：无缓存时退出码 1，准备好之后退出码 0。"""
        self.assertEqual(bootstrap.main(["--check", "--data-dir", str(self.data_dir)]), 1)
        self.assertFalse(self.data_file.exists())
        bootstrap.ensure_prepared(self.data_dir)
        self.assertEqual(bootstrap.main(["--check", "--data-dir", str(self.data_dir)]), 0)


class KeyFieldConsistencyTest(unittest.TestCase):
    """文档编号、文档名称、文档类型 的口径一致性：种子 → 产物 → 接口。"""

    def test_seed_key_fields_wellformed(self) -> None:
        rows = bootstrap.seed_rows()
        self.assertEqual(bootstrap.validate_rows(rows), [])
        tuples = bootstrap.key_tuples(rows)
        self.assertEqual(len({item[0] for item in tuples}), len(tuples))
        for doc_no, name, doc_type in tuples:
            self.assertRegex(doc_no, r"^DOCU-\d{4}$")
            self.assertTrue(name.strip())
            self.assertTrue(doc_type.strip())

    def test_store_and_service_match_seed_on_key_fields(self) -> None:
        """数据仓库与业务服务读到的关键字段口径，与种子数据完全一致。"""
        from app.services.document import DocumentService
        from app.store import store

        expected = bootstrap.key_tuples(bootstrap.seed_rows())
        self.assertEqual(bootstrap.key_tuples(store.rows("document")), expected)
        items, total = DocumentService().list_entries(page=1, size=100)
        self.assertEqual(total, len(expected))
        self.assertEqual(bootstrap.key_tuples(items), expected)

    def test_service_required_fields_share_key_field_spec(self) -> None:
        """登记必填口径与数据准备关键字段口径同源。"""
        from app.services.document import REQUIRED_FIELDS

        self.assertEqual(tuple(REQUIRED_FIELDS), bootstrap.DOCUMENT_KEY_FIELDS)


if __name__ == "__main__":
    unittest.main()
