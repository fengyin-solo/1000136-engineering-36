"""体系文档数据准备：启动前把种子数据物化成运行期数据文件，并做一致性检查。

为什么需要这一层：体系文档的启动依赖（种子数据）与运行期缓存（data/ 下的产物）
之间如果没有校验，缺少文件或缓存过期时会悄悄沿用旧内容。这里把数据准备做成
可重复执行的工程化流程：

- 首次启动：产物不存在，全量重建；
- 重复启动：指纹一致，直接复用，不重写文件；
- 异常中断：残留临时文件或半截文件，清理后重建，重试安全（临时文件 + 原子替换）。

一致性口径：文档编号、文档名称、文档类型 三个关键字段在种子数据、数据产物、
接口返回之间必须完全一致（prepare → verify → load 三道检查）。

手工执行：

    python -m app.bootstrap            数据准备（有缓存则校验复用）
    python -m app.bootstrap --check    只做一致性检查，不重建
    python -m app.bootstrap --force    强制重建
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.seed import SEED_ROWS

logger = logging.getLogger(__name__)

MODULE = "document"
SCHEMA_VERSION = 1
# 体系文档的关键字段口径：登记必填、列表检索、数据准备校验都以此为准
DOCUMENT_KEY_FIELDS = ("文档编号", "文档名称", "文档类型")
DOC_NO_PATTERN = re.compile(r"^DOCU-\d{4}$")

DATA_FILE = "document.json"
MANIFEST_FILE = "document.manifest.json"
DEFAULT_DATA_DIR = Path(
    os.environ.get("APP_DATA_DIR") or Path(__file__).resolve().parent.parent / "data"
)

_last_report: dict[str, Any] | None = None


class BootstrapError(RuntimeError):
    """数据准备失败：携带阶段与上下文，保证报错可诊断、可定位。"""

    def __init__(self, stage: str, reason: str, **context: Any) -> None:
        self.stage = stage
        self.reason = reason
        self.context = context
        detail = "、".join(f"{key}={value}" for key, value in context.items())
        super().__init__(f"[stage={stage}] {reason}" + (f"（{detail}）" if detail else ""))


def seed_rows() -> list[dict[str, Any]]:
    """种子数据是体系文档的唯一事实来源；返回拷贝，避免调用方改坏。"""
    return [dict(row) for row in SEED_ROWS[MODULE]]


def key_tuples(rows: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    """关键字段口径：文档编号、文档名称、文档类型 的三元组序列。"""
    return [tuple(str(row.get(field) or "") for field in DOCUMENT_KEY_FIELDS) for row in rows]


def validate_rows(rows: list[dict[str, Any]]) -> list[str]:
    """关键字段体检：缺字段、编号重复、编号格式不符都逐项列出，不静默放过。"""
    problems: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        for field in DOCUMENT_KEY_FIELDS:
            if not str(row.get(field) or "").strip():
                problems.append(f"第{index}行缺少关键字段「{field}」")
        doc_no = str(row.get("文档编号") or "").strip()
        if doc_no:
            if doc_no in seen:
                problems.append(f"文档编号「{doc_no}」重复")
            seen.add(doc_no)
            if not DOC_NO_PATTERN.match(doc_no):
                problems.append(f"文档编号「{doc_no}」格式应为 DOCU-XXXX")
    return problems


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _file_checksum(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _data_path(data_dir: Path) -> Path:
    return data_dir / DATA_FILE


def _manifest_path(data_dir: Path) -> Path:
    return data_dir / MANIFEST_FILE


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(path: Path, payload: Any) -> None:
    """先写临时文件再原子替换：异常中断只会留下临时文件，不会出现半截正式文件。"""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def clean_stale_tmp(data_dir: Path) -> list[Path]:
    """清理异常中断残留的临时文件；返回清掉的文件，方便日志诊断。"""
    if not data_dir.is_dir():
        return []
    removed: list[Path] = []
    for tmp in sorted(data_dir.glob(".*.tmp-*")):
        try:
            tmp.unlink()
            removed.append(tmp)
        except OSError:
            logger.warning("临时文件清理失败：%s", tmp)
    return removed


def _build_manifest(rows: list[dict[str, Any]], data_checksum: str) -> dict[str, Any]:
    return {
        "module": MODULE,
        "schema_version": SCHEMA_VERSION,
        "key_fields": list(DOCUMENT_KEY_FIELDS),
        "seed_fingerprint": _digest(rows),
        "key_digest": _digest(key_tuples(rows)),
        "row_count": len(rows),
        "data_file": DATA_FILE,
        "data_checksum": data_checksum,
        "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def verify(data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, Any]:
    """一致性检查：任何一项不过都抛 BootstrapError，绝不悄悄放行旧内容。"""
    manifest_path = _manifest_path(data_dir)
    data_path = _data_path(data_dir)
    if not manifest_path.is_file():
        raise BootstrapError("verify", "缺少清单文件，缓存视为不存在", 文件=str(manifest_path))
    if not data_path.is_file():
        raise BootstrapError("verify", "缺少数据文件，缓存视为不存在", 文件=str(data_path))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapError("verify", "清单文件损坏，无法解析", 文件=str(manifest_path), 原因=str(exc)) from exc
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise BootstrapError(
            "verify", "清单版本不兼容", 期望=SCHEMA_VERSION, 实际=manifest.get("schema_version")
        )
    expected = seed_rows()
    if manifest.get("seed_fingerprint") != _digest(expected):
        raise BootstrapError(
            "verify",
            "缓存过期：种子数据已变更，旧内容不可沿用",
            清单指纹=manifest.get("seed_fingerprint"),
            当前指纹=_digest(expected),
        )
    if manifest.get("data_checksum") != _file_checksum(data_path):
        raise BootstrapError("verify", "数据文件与清单不一致，可能被改动或写坏", 文件=str(data_path))
    try:
        rows = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapError("verify", "数据文件损坏，无法解析", 文件=str(data_path), 原因=str(exc)) from exc
    if not isinstance(rows, list):
        raise BootstrapError("verify", "数据文件结构不对，应为记录数组", 文件=str(data_path))
    if key_tuples(rows) != key_tuples(expected):
        raise BootstrapError(
            "verify",
            "关键字段口径不一致：文档编号/文档名称/文档类型 与种子数据对不上",
            产物=key_tuples(rows),
            种子=key_tuples(expected),
        )
    return manifest


def prepare(data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, Any]:
    """全量重建数据产物：种子自检 → 原子落盘 → 回读校验。"""
    rows = seed_rows()
    problems = validate_rows(rows)
    if problems:
        raise BootstrapError("prepare", "种子数据未通过关键字段体检", 问题="；".join(problems))
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BootstrapError("prepare", "数据目录不可写", 目录=str(data_dir), 原因=str(exc)) from exc
    clean_stale_tmp(data_dir)
    _atomic_write(_data_path(data_dir), rows)
    manifest = _build_manifest(rows, _file_checksum(_data_path(data_dir)))
    _atomic_write(_manifest_path(data_dir), manifest)
    logger.info("数据产物已重建：%s（%d 条）", _data_path(data_dir), len(rows))
    return verify(data_dir)


def ensure_prepared(data_dir: Path = DEFAULT_DATA_DIR) -> tuple[dict[str, Any], bool]:
    """保证数据产物可用：有效则复用，失效则重建；返回 (清单, 是否重建)。

    重复执行安全：校验通过不重写任何文件；校验失败先清理中断残留再重建，
    重建失败会把带阶段信息的 BootstrapError 抛给调用方，启动应当中止。
    """
    removed = clean_stale_tmp(data_dir)
    if removed:
        logger.warning(
            "清理异常中断残留的临时文件：%s",
            "、".join(path.name for path in removed),
        )
    try:
        manifest = verify(data_dir)
    except BootstrapError as exc:
        logger.warning("缓存不可用（%s），开始重建", exc)
        manifest = prepare(data_dir)
        return manifest, True
    logger.info("数据缓存有效，直接复用：%s", _data_path(data_dir))
    return manifest, False


def load_prepared_rows(data_dir: Path = DEFAULT_DATA_DIR) -> list[dict[str, Any]]:
    """供数据仓库加载：先保证产物可用，再做最后一道口径复核。"""
    global _last_report
    manifest, rebuilt = ensure_prepared(data_dir)
    try:
        rows = json.loads(_data_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapError("load", "数据文件回读失败", 文件=str(_data_path(data_dir)), 原因=str(exc)) from exc
    if key_tuples(rows) != key_tuples(seed_rows()):
        raise BootstrapError(
            "load", "加载前口径复核失败：文档编号/文档名称/文档类型 与种子数据不一致"
        )
    _last_report = {
        "prepared": True,
        "rebuilt": rebuilt,
        "rows": len(rows),
        "seed_fingerprint": manifest["seed_fingerprint"],
        "prepared_at": manifest["prepared_at"],
        "data_dir": str(data_dir),
    }
    return rows


def status() -> dict[str, Any]:
    """最近一次数据准备的结果，给健康检查用。"""
    if _last_report is None:
        return {"prepared": False, "detail": "尚未执行数据准备"}
    return dict(_last_report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.bootstrap", description="体系文档数据准备与一致性检查"
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="数据产物目录")
    parser.add_argument("--check", action="store_true", help="只做一致性检查，不重建")
    parser.add_argument("--force", action="store_true", help="强制重建，忽略现有缓存")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s [document-bootstrap] %(message)s")
    try:
        if args.check:
            manifest = verify(args.data_dir)
            logger.info(
                "一致性检查通过：%d 条记录，关键字段口径一致，指纹 %s",
                manifest["row_count"],
                manifest["seed_fingerprint"],
            )
            return 0
        if args.force:
            manifest = prepare(args.data_dir)
            logger.info("已强制重建：%d 条记录", manifest["row_count"])
            return 0
        _, rebuilt = ensure_prepared(args.data_dir)
        logger.info("数据准备完成（%s）", "已重建" if rebuilt else "复用缓存")
        return 0
    except BootstrapError as exc:
        logger.error("数据准备失败：%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
