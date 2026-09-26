"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
体系文档（document）表不走内置种子，改由启动数据准备流水线
（app.bootstrap + app.datasets）从源文件校验后装载，避免悄悄使用旧内容。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows]
            for name, rows in SEED_ROWS.items()
        }
        # 体系文档必须由 prepare() 显式装载，先建空表占位防止误读
        self._tables.setdefault("document", [])
        # 启动准备结果：None 表示尚未完成准备，服务不得对外宣称就绪
        self._prepared_at: datetime | None = None
        self._bootstrap_info: dict[str, Any] | None = None

    def prepare(self, name: str, rows: list[dict[str, Any]], info: dict[str, Any]) -> None:
        """以整表替换方式装载准备好的数据；重复启动时结果幂等。"""
        self._tables[name] = [dict(row) for row in rows]
        self._prepared_at = datetime.now()
        self._bootstrap_info = dict(info)

    def is_prepared(self) -> bool:
        return self._prepared_at is not None

    def bootstrap_info(self) -> dict[str, Any] | None:
        if self._bootstrap_info is None:
            return None
        info = dict(self._bootstrap_info)
        info["prepared_at"] = (
            self._prepared_at.isoformat(timespec="seconds")
            if self._prepared_at
            else None
        )
        return info

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
