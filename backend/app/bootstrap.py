"""启动数据准备流水线：源文件校验、缓存一致性检查与崩溃恢复。

设计目标（对应体系文档管理的启动要求）：

* 失败可诊断：源文件缺失、格式错误、字段口径不符时抛出 :class:`DataPrepError`，
  错误信息带阶段、文件路径与修复建议，绝不静默退回旧缓存。
* 重试可恢复：缓存损坏 / 写入中途被杀死时，下次启动自动重建；重建采用
  「临时文件 + fsync + 原子 rename」，不会留下半截缓存。
* 一致性可验证：缓存记录源文件 SHA-256、schema 版本、构建时间与行指纹；
  源文件改动、schema 升级、缓存过期任一情况都会触发重建。
* 并发安全：多进程 / 多 worker 同时启动时用文件锁串行化，只允许一个进程重建。

只依赖标准库，测试中可用临时目录替换源文件与缓存路径。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

try:  # Linux / macOS：posix 文件锁；Windows 上退化为不加锁（本机与部署均为 Linux）
    import fcntl
except ImportError:  # pragma: no cover - 平台兜底
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger("app.bootstrap")


class DataPrepError(RuntimeError):
    """数据准备失败：stage 标识失败环节，hint 给出可执行的修复建议。"""

    def __init__(self, message: str, *, stage: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.hint = hint

    def __str__(self) -> str:
        text = f"[数据准备/{self.stage}] {super().__str__()}"
        if self.hint:
            text += f"\n修复建议：{self.hint}"
        return text


@dataclass(frozen=True)
class DatasetSpec:
    """一份待准备数据集的口径声明：路径、版本、必填字段与状态枚举。"""

    name: str
    source_path: Path
    cache_path: Path
    schema_version: int
    required_fields: list[str]
    key_field: str
    allowed_statuses: list[str]
    ttl_seconds: int = 24 * 3600
    derive: Callable[[dict[str, Any]], dict[str, Any]] | None = None


@dataclass
class PrepOutcome:
    """一次准备的结果，同时作为 /api/ready 的诊断依据。"""

    dataset: str
    used_cache: bool
    reason: str
    row_count: int
    schema_version: int
    source_sha256: str
    cache_path: str
    built_at: datetime
    rows: list[dict[str, Any]] = field(repr=False, default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "used_cache": self.used_cache,
            "reason": self.reason,
            "row_count": self.row_count,
            "schema_version": self.schema_version,
            "source_sha256": self.source_sha256,
            "cache_path": self.cache_path,
            "built_at": self.built_at.isoformat(timespec="seconds"),
        }


class DataPreparer:
    """按 :class:`DatasetSpec` 把源文件准备成可直接装载的行数据，并维护缓存。"""

    def inspect_cache(self, spec: DatasetSpec) -> dict[str, Any]:
        """只读诊断：核对源文件与缓存，不写任何文件。"""
        rows, source_sha = self._load_and_normalize(spec)
        cached, reason = self._read_valid_cache(spec, source_sha)
        return {
            "dataset": spec.name,
            "source_path": str(spec.source_path),
            "cache_path": str(spec.cache_path),
            "source_exists": spec.source_path.is_file(),
            "source_sha256": source_sha,
            "cache_valid": cached is not None,
            "reason": "缓存有效" if cached is not None else reason,
            "source_row_count": len(rows),
            "cache_row_count": len(cached["rows"]) if cached else 0,
        }

    def prepare(self, spec: DatasetSpec, *, force_rebuild: bool = False) -> PrepOutcome:
        spec.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with _file_lock(spec.cache_path.with_suffix(".lock")):
            # 持锁后先读源文件：源不可读即失败，保证不会拿旧缓存冒充新数据。
            rows, source_sha = self._load_and_normalize(spec)
            fingerprint = _fingerprint(rows)

            cached: dict[str, Any] | None = None
            reject_reason = "首次启动，缓存不存在"
            if not force_rebuild:
                cached, reject_reason = self._read_valid_cache(spec, source_sha)

            if cached is not None:
                logger.info("数据集 %s 复用有效缓存：%s", spec.name, spec.cache_path)
                built_at = datetime.fromisoformat(cached["built_at"])
                return PrepOutcome(
                    dataset=spec.name,
                    used_cache=True,
                    reason="缓存与源文件一致且未过期",
                    row_count=len(cached["rows"]),
                    schema_version=spec.schema_version,
                    source_sha256=source_sha,
                    cache_path=str(spec.cache_path),
                    built_at=built_at,
                    rows=cached["rows"],
                )

            reason = "强制重建" if force_rebuild else reject_reason
            logger.warning("数据集 %s 触发缓存重建：%s", spec.name, reason)
            built_at = datetime.now()
            self._write_cache_atomic(
                spec,
                rows=rows,
                fingerprint=fingerprint,
                source_sha=source_sha,
                built_at=built_at,
            )
            return PrepOutcome(
                dataset=spec.name,
                used_cache=False,
                reason=reason,
                row_count=len(rows),
                schema_version=spec.schema_version,
                source_sha256=source_sha,
                cache_path=str(spec.cache_path),
                built_at=built_at,
                rows=rows,
            )

    # ------------------------------------------------------------------ 源文件

    def _load_and_normalize(
        self, spec: DatasetSpec
    ) -> tuple[list[dict[str, Any]], str]:
        path = spec.source_path
        if not path.is_file():
            raise DataPrepError(
                f"源文件缺失：{path}",
                stage="读取源文件",
                hint=(
                    "请恢复该文件后重新启动；如需跳过缓存重新生成，"
                    "可执行 python -m app.prepare_data --force"
                ),
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise DataPrepError(
                f"源文件无法读取：{path}（{exc}）",
                stage="读取源文件",
                hint="检查文件权限或挂载卷是否正常，然后重新启动",
            ) from exc

        source_sha = hashlib.sha256(raw).hexdigest()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataPrepError(
                f"源文件不是合法的 UTF-8 JSON：{path}（{exc}）",
                stage="源文件语法",
                hint="按 data/document_seed.json 的结构修正 JSON 后重新启动",
            ) from exc

        rows = self._validate_payload(spec, payload)
        normalized = self._normalize_rows(spec, rows)
        return normalized, source_sha

    def _validate_payload(
        self, spec: DatasetSpec, payload: Any
    ) -> list[dict[str, Any]]:
        path = spec.source_path
        if not isinstance(payload, dict):
            raise DataPrepError(
                f"源文件顶层必须是对象，实际是 {type(payload).__name__}：{path}",
                stage="源文件结构",
            )
        if payload.get("module") != spec.name:
            raise DataPrepError(
                f"源文件 module={payload.get('module')!r} 与期望的 {spec.name!r} 不一致：{path}",
                stage="源文件结构",
                hint="确认挂载的是体系文档种子文件，而不是其他数据集",
            )
        if int(payload.get("schema_version", -1)) != spec.schema_version:
            raise DataPrepError(
                f"源文件 schema_version={payload.get('schema_version')!r}，"
                f"代码要求 {spec.schema_version}：{path}",
                stage="版本一致性",
                hint="升级种子文件版本，或同步升级后端代码后重新启动",
            )
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            raise DataPrepError(
                f"源文件 rows 必须是非空数组：{path}",
                stage="源文件结构",
            )
        return rows

    def _normalize_rows(
        self, spec: DatasetSpec, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        seen_keys: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for index, raw_row in enumerate(rows, start=1):
            if not isinstance(raw_row, dict):
                raise DataPrepError(
                    f"第 {index} 行不是对象：{spec.source_path}",
                    stage="行级校验",
                )
            row: dict[str, Any] = {}
            for field_name, value in raw_row.items():
                if field_name == "id":
                    continue  # id 由装载端统一分配，源文件不携带
                row[field_name] = value.strip() if isinstance(value, str) else value

            missing = [
                field_name
                for field_name in spec.required_fields
                if not str(row.get(field_name) or "").strip()
            ]
            if missing:
                raise DataPrepError(
                    f"第 {index} 行缺少必填字段（{ '、'.join(missing) }），"
                    f"当前 {spec.key_field}={row.get(spec.key_field)!r}：{spec.source_path}",
                    stage="字段口径",
                    hint="文档编号、文档名称、文档类型三项口径在源文件、缓存与接口中保持一致",
                )
            key = str(row[spec.key_field]).strip()
            if key in seen_keys:
                raise DataPrepError(
                    f"第 {index} 行 {spec.key_field} 重复：{key}：{spec.source_path}",
                    stage="唯一性校验",
                )
            seen_keys.add(key)

            status = str(row.get("status") or "").strip()
            if status not in spec.allowed_statuses:
                raise DataPrepError(
                    f"第 {index} 行（{spec.key_field}={key}）状态 {status!r} 不在允许集合"
                    f" { '、'.join(spec.allowed_statuses) } 内：{spec.source_path}",
                    stage="状态口径",
                )
            row["status"] = status

            if spec.derive is not None:
                row.update(spec.derive(row))
            row["id"] = index
            normalized.append(row)
        return normalized

    # ------------------------------------------------------------------ 缓存

    def _read_valid_cache(
        self, spec: DatasetSpec, source_sha: str
    ) -> tuple[dict[str, Any] | None, str]:
        """返回 (缓存内容, 不可复用原因)；缓存不可用时原因用于诊断与重建。"""
        path = spec.cache_path
        if not path.is_file():
            return None, "缓存文件不存在"
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
            return None, f"缓存文件损坏无法解析（{exc}）"

        if not isinstance(cached, dict):
            return None, "缓存顶层结构不是对象"
        if cached.get("module") != spec.name:
            return None, "缓存 module 与当前数据集不一致"
        if int(cached.get("schema_version", -1)) != spec.schema_version:
            return None, (
                f"缓存 schema_version={cached.get('schema_version')!r}，"
                f"代码要求 {spec.schema_version}"
            )
        if cached.get("source_sha256") != source_sha:
            return None, "源文件内容哈希与缓存不一致（源文件已更新）"

        rows = cached.get("rows")
        if not isinstance(rows, list) or not rows:
            return None, "缓存缺少有效的 rows 数据"
        if int(cached.get("row_count", -1)) != len(rows):
            return None, "缓存行数声明与实际不一致（写入可能被中断）"
        if _fingerprint(rows) != cached.get("rows_fingerprint"):
            return None, "缓存行指纹不匹配（内容被截断或篡改）"

        ids = [row.get("id") for row in rows if isinstance(row, dict)]
        if len(ids) != len(rows) or len(set(ids)) != len(ids):
            return None, "缓存行 id 缺失或重复"
        if any(
            not str(row.get(field_name) or "").strip()
            for row in rows
            for field_name in spec.required_fields
        ):
            return None, "缓存存在必填口径字段为空的记录"

        try:
            built_at = datetime.fromisoformat(str(cached["built_at"]))
        except (KeyError, TypeError, ValueError):
            return None, "缓存 built_at 无法解析"
        if spec.ttl_seconds > 0 and datetime.now() >= built_at + timedelta(
            seconds=spec.ttl_seconds
        ):
            return None, (
                f"缓存已过期（构建于 {built_at.isoformat(timespec='seconds')}，"
                f"TTL {spec.ttl_seconds} 秒），需重新核对源文件"
            )
        return cached, ""

    def _write_cache_atomic(
        self,
        spec: DatasetSpec,
        *,
        rows: list[dict[str, Any]],
        fingerprint: str,
        source_sha: str,
        built_at: datetime,
    ) -> None:
        path = spec.cache_path
        tmp_path = path.with_name(path.name + ".tmp")
        payload = {
            "module": spec.name,
            "schema_version": spec.schema_version,
            "source_sha256": source_sha,
            "built_at": built_at.isoformat(timespec="seconds"),
            "ttl_seconds": spec.ttl_seconds,
            "row_count": len(rows),
            "rows_fingerprint": fingerprint,
            "rows": rows,
        }
        try:
            # 固定临时文件名：上次异常中断留下的残文件会被持锁后直接覆盖
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            _fsync_directory(path.parent)
        except OSError as exc:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise DataPrepError(
                f"缓存写入失败：{path}（{exc}）",
                stage="写入缓存",
                hint="检查缓存目录磁盘空间与权限后重新启动，已写入的半成品缓存会被自动清理",
            ) from exc


def _fingerprint(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fsync_directory(directory: Path) -> None:  # pragma: no cover - 依赖文件系统语义
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


@contextlib.contextmanager
def _file_lock(lock_path: Path) -> Iterator[None]:
    """跨进程互斥锁；进程异常退出时内核自动释放，不会产生永久死锁。"""
    if fcntl is None:  # pragma: no cover - 平台兜底
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
