# 税务客户管理 Demo

一个供局域网使用的小型网页：公开首页可直接访问；查看客户信息、调用业务接口、更新中国时间逐月保存的报税状态、管理标签分类以及管理图片均需先登录。图片可上传、预览、改名、替换、下载和删除。客户删除会同时删除其月度记录和关联图片。登录账号从 PostgreSQL `site_users` 表读取。

## 准备数据库

先在 Navicat 中连接 `tax_db`，对目标数据库运行本目录的 `init.sql`（右键数据库 → **运行 SQL 文件**，编码 UTF-8，关闭“出错后继续执行”）。如果根目录 `../init.sql` 已执行，直接运行这里的 `init.sql` 即可；现有三张表会跳过，新增登录表、登录限速表、无归属图片表、当月状态视图和五条演示客户记录。脚本可重复执行，不覆盖已有数据；如果曾执行过旧版 `demo/init.sql`，也要重新执行当前版本以补齐 `login_attempts` 表。

**已有数据库先升级至逐月记录和标签分类：** 关闭正在运行的网站，在 Navicat 对同一个 `tax_db` 运行 [upgrade_v2.sql](upgrade_v2.sql)，然后重新双击 `启动网站.bat`。这是在已执行 `demo/init.sql` 的基础上运行的增量脚本，不要删除数据库，也不要重新导入测试数据。它把现有客户的注册日期初始化为建档时间的中国日期，从注册月份到当前中国月份补齐“未报税”记录；已有“已报税”记录不会被覆盖。旧备注如超过 255 字，脚本会报错并回滚，请先检查 `SELECT id, name, length(note) FROM customers WHERE length(note) > 255;`，人工缩短后重新运行。新数据库也应按 `init.sql`、`upgrade_v2.sql` 的顺序运行。

**本次功能升级：** 关闭网站，在 Navicat 对同一个 `tax_db` 运行 [upgrade_v3.sql](upgrade_v3.sql)，确认执行成功后再启动网站。它以旧版 `registered_on` 的中国时间零点补齐 `registered_at`，新增“可用”状态、三类单选标签、独立的逐月记账表和其他文件表；已有报税、客户、图片和通用标签保留。新数据库依次执行 `init.sql`、`upgrade_v2.sql`、`upgrade_v3.sql`。三个脚本不可跳步；本次脚本可重复运行。

## 安装与运行

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。在 `demo` 目录执行：

```bash
uv sync --frozen
cp .env.example .env
```

在 `.env` 中设置 `DATABASE_URL` 和 `STORAGE_DIR`。`DATABASE_URL` 指向局域网 PostgreSQL；密码若含特殊字符，需要按 URL 规则编码。`STORAGE_DIR` 必须是持久化的私有目录，不要设在 `src/tax_manager/static` 下。运行程序的 Linux 用户必须有该目录的读写权限；如果程序也放进容器，需将该目录挂载为持久化卷。程序首次启动会在此目录自动创建 `.session-key`，无需手动设置密钥；请保持该文件私有并随目录一起备份。

**Windows 双击启动：** 填好 `.env` 后，在资源管理器中双击 `启动网站.bat`。它会使用 uv 安装锁定的依赖、启动本机网站，并自动打开浏览器。保持弹出的窗口打开；按 `Ctrl+C` 停止。若 `.env` 仍有 `DB_HOST` 等示例值，窗口会直接提示修改，不会等到登录时才出现 500 错误。

**使用 Navicat 的 SSH 隧道时：** Windows 不能直接使用 Linux 的外网地址和数据库端口。将 `DATABASE_URL` 中的数据库主机、端口写成 Navicat“常规”页从 SSH 服务器看到的值；再在 `.env` 中设置 `SSH_HOST`、`SSH_PORT`、`SSH_USER`、`SSH_TARGET_HOST` 和 `SSH_LOCAL_PORT`。双击启动时会调用 Windows 自带的 `ssh`，在窗口提示时输入 SSH 密码，网站通过本机转发端口连接数据库。不要把 SSH 密码写进 `.env`。

如果登录时提示数据库连接超时，可双击 `检查数据库连接.bat`。它会分别检查 Linux 主机端口、PostgreSQL 登录和网站所需的登录表；输出不会显示数据库密码。

Linux 上启动：

```bash
uv run --frozen --no-dev --env-file .env uvicorn --app-dir src --factory tax_manager.app:create_app --host 0.0.0.0 --port 8000 --workers 2
```

访问 `http://服务器地址:8000/` 查看公开首页；登录后进入 `http://服务器地址:8000/customers`。新数据库预置账号为 `wfg1`，但没有可用的公开初始密码。**首次登录前必须设置私有密码**；已有数据库也建议更换旧演示密码。在 `demo` 目录运行以下命令，按提示输入两次新密码（不会写入命令历史）：

```bash
PYTHONPATH=src uv run --frozen --env-file .env python -m tax_manager.cli auth set-password wfg1
```

从旧版 Flask 迁移后，原有浏览器会话需要重新登录；账号和业务数据无需迁移。登录后也可在“修改密码”页面更换。登录失败按来源和账号分别限制尝试次数，计数共享在 PostgreSQL 中。未登录访问客户、报税和图片相关页面或接口会跳转到登录页。局域网以外开放访问时应先配置 HTTPS，并将 `COOKIE_SECURE=1`。

开发时可使用 Uvicorn 自动重载：

```bash
uv run --frozen --env-file .env uvicorn --app-dir src --factory tax_manager.app:create_app --reload
```

Windows PowerShell 可直接运行上面的 `uv run` 命令。管理命令则先设置 `$env:PYTHONPATH = 'src'`。正式运行关闭 `--reload`，可用 `--workers 2` 启动两个进程。

## 使用方式

- **首页**：登录后按中国时间选月份，默认当前月，只显示已在本公司注册且状态为“可用”的客户。显示名称、税号、该月记账与报税状态、纳税人身份、服务类型、客户来源及备注前 20 字。选择名称/税号、三类标签、指定月份的记账 / 报税状态或注册日期区间后，会从所有客户中查找符合全部所选条件的客户（包括标记为不可用的客户）。选择的月份只控制首页两个月度状态列；在该月尚未注册的客户显示“—”。
- **客户详情**：保留原有图片功能；显示完整备注、联系人、可用状态和两个独立的月度历史状态。两种月度状态固定为“已 / 未”，可逐月修改，已完成显示绿色，未完成显示红色。注册时间默认当前中国时间，可用状态默认“可用”。将注册时间改晚且此前存在已记账或已报税记录时会拒绝修改。
- **标签分类**：在页面中分别管理“纳税人身份”“服务类型”“客户来源”的单选值，以及旧版“其他标签”。记账、报税状态不在标签页管理。API `GET /api/tag-categories?kind=service_type` 查询，`POST /api/tag-categories` 创建，例如 `{"kind":"service_type","name":"代理记账"}`。kind 可为 `taxpayer_identity`、`service_type`、`customer_source`、`general`；省略时按 `general` 处理。
- **其他文件**：详情页可上传、改名、下载和删除任意格式文件；单个文件最多 1 MiB，每个客户最多 10 个。文件经登录校验并强制作为附件下载。客户删除时同时清理月度记录、图片和其他文件。
- **图片库**：保留无归属图片及客户图片的预览、改名、替换、下载和删除；只接受真实 PNG、JPEG、WebP，单张最多 10 MB。

### 一次创建完整客户的 API

`POST /api/customers` 需要已登录的 Cookie 和页面中的 `csrf_token`（请求头 `X-CSRF-Token`）。支持 `application/json`，只有 `name` 必填；空缺的注册时间使用中国时间当前时刻，可用状态默认 true。示例 JSON：

```json
{
  "name": "测试客户",
  "tax_identifier": "913000000000000000",
  "contact_name": "张三",
  "contact_phone": "13800000000",
  "note": "最多 255 字",
  "registered_at": "2026-09-01T09:00:00+08:00",
  "is_available": true,
  "taxpayer_identity_id": 1,
  "service_type_id": 2,
  "customer_source_id": 3,
  "general_tag_ids": [4],
  "monthly_bookkeeping": {"2026-09": true},
  "monthly_filings": {"2026-09": false}
}
```

三种单选字段的 ID 来自相应 kind 的标签 API；`general_tag_ids` 是可选的旧版多选标签。历史月份必须处于该客户注册月份至当前中国月份之间，状态使用 JSON 布尔值。成功返回 HTTP 201、客户 ID 与详情页地址。若需一并上传客户图片和其他文件，改用 `multipart/form-data`：`payload` 字段放上述 JSON，图片用多个 `images` 字段，其他文件用多个 `other_files` 字段。图片与文件资料在同一数据库事务中保存；上传总请求最大 64 MiB。页面新增客户表单先填写基本字段，再到详情页管理历史月份与文件。

### 数据位置与备份

PostgreSQL 存客户、两种月度状态、账号和文件目录信息；原件保存在 `STORAGE_DIR`。备份和恢复时必须让数据库与该目录对应。

## 测试与恢复

```bash
uv run --frozen pytest -q
```

需要同时备份 PostgreSQL 和 `STORAGE_DIR`。删除操作会立即移除数据；如需恢复，请从对应时间点的数据库和文件备份一起恢复。数据库提交后若文件清理失败，页面会提示，服务器日志会记录错误；此时可能留有孤立文件，需要人工清理。

停止网站写入后，可检查数据库与图片目录是否一致：

```bash
PYTHONPATH=src uv run --frozen --env-file .env python -m tax_manager.cli images check-files
```

命令只报告缺失与孤立文件，不自动删除。缺失文件需要从备份恢复；孤立文件在核实后人工清理。图片写入会同步文件和目录，但意外断电、存储设备故障仍需依赖备份与上述检查。

## 目录与依赖

`src/tax_manager/auth` 负责登录和密码，`customers` 负责客户及月度状态，`tags` 负责标签分类，`images` 负责图片，`customer_files` 负责其他文件，`db` 负责连接，`templates` 与 `static` 提供网页界面。SQL 使用参数化查询，动态表名只从固定白名单选择。

FastAPI（MIT）处理路由和 API，Starlette（BSD-3-Clause）处理会话与模板响应，Uvicorn（BSD-3-Clause）运行 ASGI 服务。Psycopg 3 及其 binary 包（LGPL-3.0-only）连接 PostgreSQL；Pillow（MIT-CMU）校验上传图片的实际格式。依赖版本锁在 `uv.lock`。应用本身不使用 LLM。
