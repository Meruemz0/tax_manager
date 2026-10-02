# 税务客户管理 Demo

一个供局域网使用的小型网页：公开首页可直接访问；查看客户信息、调用业务接口、更新按中国时间逐月保存的记账与报税状态、管理标签分类和客户其他文件均需先登录。登录账号从 PostgreSQL `site_users` 表读取。

## 准备数据库

先在 Navicat 中连接 `tax_db`，对目标数据库运行本目录的 `init.sql`（右键数据库 → **运行 SQL 文件**，编码 UTF-8，关闭“出错后继续执行”）。如果根目录 `../init.sql` 已执行，直接运行这里的 `init.sql` 即可；现有三张表会跳过，新增登录表、登录限速表、无归属图片表、当月状态视图和五条演示客户记录。脚本可重复执行，不覆盖已有数据；如果曾执行过旧版 `demo/init.sql`，也要重新执行当前版本以补齐 `login_attempts` 表。

**已有数据库先升级至逐月记录和标签分类：** 关闭正在运行的网站，在 Navicat 对同一个 `tax_db` 运行 [upgrade_v2.sql](upgrade_v2.sql)，然后重新双击 `启动网站.bat`。这是在已执行 `demo/init.sql` 的基础上运行的增量脚本，不要删除数据库，也不要重新导入测试数据。它把现有客户的注册日期初始化为建档时间的中国日期，从注册月份到当前中国月份补齐“未报税”记录；已有“已报税”记录不会被覆盖。旧备注如超过 255 字，脚本会报错并回滚，请先检查 `SELECT id, name, length(note) FROM customers WHERE length(note) > 255;`，人工缩短后重新运行。新数据库也应按 `init.sql`、`upgrade_v2.sql` 的顺序运行。

**上一版功能升级：** 关闭网站，在 Navicat 对同一个 `tax_db` 运行 [upgrade_v3.sql](upgrade_v3.sql)，确认执行成功后再启动网站。它以旧版 `registered_on` 的中国时间零点补齐 `registered_at`，新增“可用”状态、三类单选标签、独立的逐月记账表和其他文件表；已有报税、客户、图片和通用标签保留。新数据库依次执行 `init.sql`、`upgrade_v2.sql`、`upgrade_v3.sql`。三个脚本不可跳步；本次脚本可重复运行。

**注销时间升级：** 关闭网站后，在 Navicat 对同一个 `tax_db` 执行 [upgrade_v4.sql](upgrade_v4.sql)，成功后再启动网站。该脚本新增 `deactivated_at` 字段，可重复执行，不会修改已有客户记录。已有不可用客户的历史注销时间无法还原，详情页显示“未记录”。

**文件上传升级：** 关闭网站后，在 Navicat 对同一个 `tax_db` 执行 [upgrade_v5.sql](upgrade_v5.sql)，成功后再启动网站。它把单文件数据库上限改为 20 MiB，不修改或删除已有文件，可重复执行。新数据库按 `init.sql`、`upgrade_v2.sql`、`upgrade_v3.sql`、`upgrade_v4.sql`、`upgrade_v5.sql` 顺序执行。

**客户账号与服务订单升级：** 停止网站后，在 Navicat 对同一个 `tax_db` 执行 [upgrade_v6.sql](upgrade_v6.sql)，确认成功后再启动网站。它新增建账月份、客户系统账号、服务订单、分次收款和加密订单文件表；已有客户与其他文件保留。新数据库依次执行 `init.sql` 到 `upgrade_v6.sql`，不可跳步。本脚本可重复运行。**启动新版网站前必须先执行此脚本**，否则客户详情会因缺少新表而报错。

## 安装与运行

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。在 `demo` 目录执行：

```bash
uv sync --frozen
cp .env.example .env
```

在 `.env` 中设置 `DATABASE_URL` 和 `STORAGE_DIR`。`DATABASE_URL` 指向局域网 PostgreSQL；密码若含特殊字符，需要按 URL 规则编码。`STORAGE_DIR` 必须是持久化的私有目录，不要设在 `src/tax_manager/static` 下。运行程序的 Linux 用户必须有该目录的读写权限；如果程序也放进容器，需将该目录挂载为持久化卷。程序首次启动会在此目录自动创建 `.session-key`，用于会话签名；它**不是**客户账号和凭证的加密密钥。新版首次启动还会在 `STORAGE_DIR` 的同级目录创建一个 `<目录名>.data-key` 文件（32 字节）用于数据加密，例如 `STORAGE_DIR=/srv/tax-manager/uploads` 对应 `/srv/tax-manager/uploads.data-key`。请同时备份数据库、`STORAGE_DIR` 和这个密钥文件；丢失密钥后，加密账号和订单文件无法恢复。已生成过密钥但文件丢失时，程序会拒绝自动生成新密钥。首次启动还会在数据库的 `data_key_verification` 表写入校验记录；此后如果数据库与密钥不匹配，网站会在启动时停止，不会把错误的密钥用于现有账号和文件。恢复备份时需同时恢复原密钥；不要通过删除校验记录来绕过检查。

生产环境可设置 `DATA_KEY_FILE` 指向应用可读的、项目目录之外的现有 32 字节密钥文件；Docker Compose **必须**将密钥文件作为持久化文件或 secret 挂入容器；推荐将主机上权限仅限运行用户读取的 32 字节密钥文件挂载为 `/run/secrets/tax_manager_data_key`，并设 `DATA_KEY_FILE=/run/secrets/tax_manager_data_key`。若只持久化上传目录，默认同级密钥可能随容器重建丢失。Compose 只负责挂载，不会自动加密主机上的密钥文件。数据密钥与 `.session-key`、数据库密码和网站登录密码必须分开保管。

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

从旧版 Flask 迁移后，原有浏览器会话需要重新登录；账号和业务数据无需迁移。登录后也可在“修改密码”页面更换。登录失败按来源和账号分别限制尝试次数，计数共享在 PostgreSQL 中。未登录访问客户、报税和其他文件相关页面或接口会跳转到登录页。局域网以外开放访问时应先配置 HTTPS，并将 `COOKIE_SECURE=1`。

开发时可使用 Uvicorn 自动重载：

```bash
uv run --frozen --env-file .env uvicorn --app-dir src --factory tax_manager.app:create_app --reload
```

Windows PowerShell 可直接运行上面的 `uv run` 命令。管理命令则先设置 `$env:PYTHONPATH = 'src'`。正式运行关闭 `--reload`，可用 `--workers 2` 启动两个进程。

## 使用方式

- **首页**：右上角“月份选择”用于选择某年某月，默认中国时间当前月；未设置筛选条件时，只显示截至所选月已在本公司注册且目前标记为“可用”的客户。显示名称、税号、所选月记账与报税状态、纳税人身份、服务类型、客户来源及备注前 20 字。点击“筛选”会在客户列表右侧展开窄面板，不改变表格宽度。名称/税号、可用状态、三类标签、所选月记账与报税状态、注册日期区间可以组合筛选所有客户。选择年月、筛选和清除筛选都只更新客户列表与统计，无需刷新整页；清除筛选保留已选年月。在所选月尚未注册的客户，其该月状态显示“—”。
- **客户详情**：显示完整备注、联系人、可用状态和两个独立的月度历史状态。不可用时在状态下显示注销时间，按中国时间精确到小时；恢复可用会清空该时间，再次设为不可用时重新记录。两种月度状态固定为“已 / 未”，可逐月修改，已完成显示绿色，未完成显示红色。注册时间默认当前中国时间，可用状态默认“可用”。将注册时间改晚且此前存在已记账或已报税记录时会拒绝修改。
- **标签分类**：在页面中分别管理“纳税人身份”“服务类型”“客户来源”的单选值，以及旧版“其他标签”。记账、报税状态不在标签页管理。API `GET /api/tag-categories?kind=service_type` 查询，`POST /api/tag-categories` 创建，例如 `{"kind":"service_type","name":"代理记账"}`。kind 可为 `taxpayer_identity`、`service_type`、`customer_source`、`general`；省略时按 `general` 处理。
- **系统账号**：客户详情页可为一个客户添加最多 10 条外部系统账号。账号与密码都以密文保存；点击“复制账号”或“复制密码”时才按需解密并写入剪贴板，点击“显示账号”或“显示密码”可临时显示 20 秒。修改时新账号、新密码留空表示保留原值。不同电脑通过局域网地址访问时需配置 HTTPS，浏览器才可靠支持一键复制；已复制的内容会留在使用者电脑的剪贴板，直到被覆盖。
- **服务订单**：主导航“服务订单”可跨客户筛选订单。客户详情可添加订单，订单详情可修改服务项目、金额、订单日期、服务起止月份、应收日期、合同编号、备注和独立的完成状态；每笔订单可分次登记、修改或删除收款记录，系统据此计算未收、部分收、已收。订单可上传不限数量的原始凭证、合同和收款凭据，单文件最多 20 MiB，内容和文件名加密保存。
- **其他文件**：详情页的整个文件区默认收起，展开后可一次选择多个文件并上传、改名、下载和删除。格式不限，单文件最多 20 MiB，每个客户没有文件数量上限。单次请求总大小仍为 64 MiB，超过时分批上传。上传表单通过 JavaScript 提交，请保持浏览器启用 JavaScript。文件经登录校验并强制作为附件下载。客户删除时同时清理月度记录和其他文件；如客户有旧版图片，也会清理这些历史图片。

### 一次创建完整客户的 API

`POST /api/customers` 需要已登录的 Cookie 和页面中的 `csrf_token`（请求头 `X-CSRF-Token`）。支持 `application/json`，只有 `name` 必填；空缺的注册时间使用中国时间当前时刻，可用状态默认 true。创建时如果 `is_available` 为 false，会记录当前注销时间。示例 JSON：

```json
{
  "name": "测试客户",
  "tax_identifier": "913000000000000000",
  "contact_name": "张三",
  "contact_phone": "13800000000",
  "note": "最多 255 字",
  "registered_at": "2026-09-01T09:00:00+08:00",
  "is_available": true,
  "bookkeeping_start_month": "2026-09",
  "taxpayer_identity_id": 1,
  "service_type_id": 2,
  "customer_source_id": 3,
  "general_tag_ids": [4],
  "monthly_bookkeeping": {"2026-09": true},
  "monthly_filings": {"2026-09": false},
  "system_accounts": [
    {"system_name": "电子税务局", "login_url": "https://example.org/login",
     "account_name": "demo-account", "password": "demo-password"}
  ],
  "orders": [
    {"service_name": "代理记账", "order_date": "2026-09-15",
     "service_start_month": "2026-09", "service_end_month": "2026-12",
     "amount": "1200.00", "due_date": "2026-10-01",
     "is_completed": false,
     "receipts": [{"amount": "300.00", "received_on": "2026-09-20"}]}
  ]
}
```

如果同一次新增客户还要上传第一笔订单的多份原始凭证，改用 `multipart/form-data`，在 `payload` 放上述 JSON，把文件作为可重复的 `order_vouchers_0` 字段上传；第二笔订单用 `order_vouchers_1`，依此类推。客户新增 API 也支持不带账号和订单，此时仍只有客户名称必填。账号和订单一旦提供，各自的系统名称、服务项目等必须有效。

三种单选字段的 ID 来自相应 kind 的标签 API；`general_tag_ids` 是可选的旧版多选标签。历史月份必须处于该客户注册月份至当前中国月份之间，状态使用 JSON 布尔值。成功返回 HTTP 201、客户 ID 与详情页地址。若需一并上传其他文件，改用 `multipart/form-data`：`payload` 字段放上述 JSON，其他文件用多个 `other_files` 字段。每个文件最多 20 MiB，单次上传请求总大小最多 64 MiB；可分批上传任意数量的文件。`images` 上传字段已停用，传入时返回 HTTP 400。页面新增客户表单先填写基本字段，再到详情页管理历史月份与文件。

### 数据位置与备份

PostgreSQL 存客户、两种月度状态、网站登录账号、加密的客户系统账号、订单和文件目录信息。订单凭证内容在 `STORAGE_DIR/order_vouchers` 加密保存；原有“其他文件”仍按旧方式保存，**不会因为本次升级自动加密**。备份和恢复时必须让数据库、文件目录和数据密钥对应；恢复后应实际测试账号复制及凭证下载。

## 测试与恢复

```bash
uv run --frozen pytest -q
```

需要同时备份 PostgreSQL 和 `STORAGE_DIR`。删除操作会立即移除数据；如需恢复，请从对应时间点的数据库和文件备份一起恢复。数据库提交后若文件清理失败，页面会提示，服务器日志会记录错误；此时可能留有孤立文件，需要人工清理。

旧版图片页面与 API 已移除。旧版 `customer_images`、`unassigned_images` 表及对应文件不会因本次升级自动删除，以免丢失已有资料。若旧客户被删除，其关联的历史图片会一并清理。

## 目录与依赖

`src/tax_manager/auth` 负责登录和密码，`customers` 负责客户及月度状态，`tags` 负责标签分类，`customer_files` 负责其他文件，`db` 负责连接，`templates` 与 `static` 提供网页界面。SQL 使用参数化查询，动态表名只从固定白名单选择。

FastAPI（MIT）处理路由和 API，Starlette（BSD-3-Clause）处理会话与模板响应，Uvicorn（BSD-3-Clause）运行 ASGI 服务。Psycopg 3 及其 binary 包（LGPL-3.0-only）连接 PostgreSQL。依赖版本锁在 `uv.lock`。应用本身不使用 LLM。
