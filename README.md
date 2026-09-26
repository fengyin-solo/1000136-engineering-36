# 实验室样品检测管理平台

面向检测实验室的样品接收、检测任务分配、仪器校准、试剂耗材、结果报告与质量审核的综合管理后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   ├── app/bootstrap.py      体系文档数据准备与一致性检查
│   ├── app/store.py          内存数据仓库与示例数据
│   └── tests/                数据准备与一致性测试
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`（返回里带 `document_data`，
可以看到体系文档数据准备的结果与指纹）。

### 体系文档数据准备（启动一致性检查）

体系文档模块的启动数据不直接读种子，而是走 `app/bootstrap.py` 的数据准备流程：
种子数据物化成 `backend/data/document.json` 与 `document.manifest.json`
（清单含种子指纹、文件校验和、关键字段摘要），校验通过才会加载。三种启动场景：

- **首次启动**：产物不存在，自动全量重建；
- **重复启动**：指纹一致，直接复用缓存，不重写任何文件；
- **异常中断**：残留的临时文件或半截文件会被清理并重建，重试安全
  （写盘统一走临时文件 + 原子替换）。

缺少文件、缓存过期、文件被改动都不会悄悄沿用旧内容：校验失败先重建，
重建也失败则启动中止，并打印带阶段信息的错误（`stage=prepare/verify/load`），
按错误里的路径与原因排查即可。

手工执行：

```bash
cd backend
.venv/bin/python -m app.bootstrap            # 数据准备（有缓存则校验复用）
.venv/bin/python -m app.bootstrap --check    # 只做一致性检查，不重建
.venv/bin/python -m app.bootstrap --force    # 强制重建
```

一致性口径：**文档编号、文档名称、文档类型** 三个关键字段在种子数据、
数据产物、接口返回之间完全一致，`backend/tests/test_document_bootstrap.py`
覆盖了首次启动、重复启动、缓存过期、缺少文件、异常中断与失败诊断。

### 测试

```bash
make test   # 等价于 cd backend && .venv/bin/python -m unittest discover -s tests -t . -v
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 样品接收 | `sample` | 检测样品 | 样品编号、样品名称、委托单位 |
| 检测任务 | `task` | 检测任务单 | 任务编号、所属样品、检测项目 |
| 仪器管理 | `instrument` | 检测仪器 | 仪器编号、仪器名称、型号规格 |
| 校准记录 | `calibration` | 校准记录单 | 记录编号、仪器编号、校准机构 |
| 试剂耗材 | `reagent` | 试剂耗材 | 试剂编号、试剂名称、规格等级 |
| 检测结果 | `result` | 检测结果 | 结果编号、所属任务、检测项 |
| 检测报告 | `report` | 检测报告 | 报告编号、委托单位、样品名称 |
| 质量控制 | `qc` | 质控样品 | 质控编号、质控类别、标准值 |
| 偏离处理 | `deviation` | 偏离记录 | 偏离编号、偏离描述、涉及样品 |
| 样品留存 | `sample_storage` | 留存样品 | 留存编号、样品编号、留存位置 |
| 委托合同 | `contract` | 委托检验合同 | 合同编号、委托单位、联系人 |
| 检测人员 | `staff` | 检测员 | 员工编号、姓名、技术职称 |
| 检测方法 | `method` | 检测方法 | 方法编号、方法名称、适用标准 |
| 环境监测 | `environment` | 环境记录 | 记录编号、监测区域、温度值 |
| 客户申诉 | `complain` | 申诉记录 | 申诉编号、申诉单位、涉及报告 |
| 内审管理 | `audit` | 内审记录 | 内审编号、审核范围、审核组长 |
| 仪器维修 | `equipment_repair` | 维修记录 | 维修编号、仪器编号、故障描述 |
| 体系文档 | `document` | 体系文档 | 文档编号、文档名称、文档类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 体系文档的关键字段口径（文档编号、文档名称、文档类型）以 `app/bootstrap.py`
  的 `DOCUMENT_KEY_FIELDS` 为准，登记必填、数据准备校验都从它派生。
