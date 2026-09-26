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
│   ├── data/                 启动依赖的源数据文件（如 document_seed.json）
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   ├── app/bootstrap.py      启动数据准备：校验、哈希一致性、缓存与崩溃恢复
│   ├── app/datasets.py       体系文档字段/状态统一口径声明
│   ├── app/prepare_data.py   数据准备 CLI（预检 / 强制重建 / 状态查看）
│   └── app/store.py          内存数据仓库与示例数据
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

健康检查（存活）：`curl http://127.0.0.1:8000/api/health`
就绪检查（数据准备）：`curl http://127.0.0.1:8000/api/ready`

### 体系文档启动数据准备

体系文档（`document`）不再使用内置种子常量，启动时执行一条数据准备流水线，
保证「文档编号、文档名称、文档类型」口径在源文件、缓存、接口、前端之间一致：

```text
data/document_seed.json  ──校验──▶  .cache/document_cache.json  ──装载──▶ 内存表
       （源文件，入库）              （构建缓存，可重建，不入库）
```

- **一致性检查**：缓存记录源文件 SHA-256、schema 版本、构建时间与行指纹；
  源文件改动、版本升级、缓存过期、缓存损坏/被篡改任一情况都会重建，绝不静默使用旧内容。
- **失败可诊断**：源文件缺失、JSON 非法、必填口径字段缺失、编号重复、状态越界时，
  错误带「阶段 + 原因 + 修复建议」；`/api/ready` 返回 503 与诊断信息，业务接口同步拒绝服务。
- **重试可恢复**：缓存写入采用「临时文件 + fsync + 原子 rename」，异常中断后
  下次启动自动清理残文件并重建；多进程启动用文件锁串行化，只重建一次。
- 手动操作：

```bash
python -m app.prepare_data          # 启动前预检/准备（缓存有效则复用）
python -m app.prepare_data --status # 只读查看源与缓存一致性
python -m app.prepare_data --force  # 强制重建
```

可用环境变量调整：`APP_DATA_DIR`、`APP_CACHE_DIR`、`DOCUMENT_CACHE_TTL_SECONDS`
（TTL 秒数，默认 86400，设为 0 表示不过期）。

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
