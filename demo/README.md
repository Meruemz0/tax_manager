# 税务客户管理 Demo

一个供局域网使用的小型网页：公开首页可直接访问；查看客户信息、调用业务接口、更新中国时间逐月保存的报税状态、管理标签分类以及管理图片均需先登录。图片可上传、预览、改名、替换、下载和删除。客户删除会同时删除其月度记录和关联图片。登录账号从 PostgreSQL `site_users` 表读取。

## 准备数据库

先在 Navicat 中连接 `tax_db`，对目标数据库运行本目录的 `init.sql`（右键数据库 → **运行 SQL 文件**，编码 UTF-8，关闭“出错后继续执行”）。如果根目录 `../init.sql` 已执行，直接运行这里的 `init.sql` 即可；现有三张表会跳过，新增登录表、登录限速表、无归属图片表、当月状态视图和五条演示客户记录。脚本可重复执行，不覆盖已有数据；如果曾执行过旧版 `demo/init.sql`，也要重新执行当前版本以补齐 `login_attempts` 表。

**已有数据库升级至逐月记录和标签分类：** 关闭正在运行的网站，在 Navicat 对同一个 `tax_db` 运行 [upgrade_v2.sql](upgrade_v2.sql)，然后重新双击 `启动网站.bat`。这是在已执行 `demo/init.sql` 的基础上运行的增量脚本，不要删除数据库，也不要重新导入测试数据。它把现有客户的注册日期初始化为建档时间的中国日期，从注册月份到当前中国月份补齐“未报税”记录；已有“已报税”记录不会被覆盖。旧备注如超过 255 字，脚本会报错并回滚，请先检查 `SELECT id, name, length(note) FROM customers WHERE length(note) > 255;`，人工缩短后重新运行。新数据库也应按 `init.sql`、`upgrade_v2.sql` 的顺序运行。

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
uv run --frozen --no-dev --env-file .env gunicorn --chdir src --bind 0.0.0.0:8000 --workers 2 'tax_manager.app:create_app()'
```

访问 `http://服务器地址:8000/` 查看公开首页；登录后进入 `http://服务器地址:8000/customers`。新数据库预置账号为 `wfg1`，但没有可用的公开初始密码。**首次登录前必须设置私有密码**；已有数据库也建议更换旧演示密码。在 `demo` 目录运行以下命令，按提示输入两次新密码（不会写入命令历史）：

```bash
PYTHONPATH=src uv run --frozen --env-file .env flask --app tax_manager.app:create_app auth set-password wfg1
```

登录后也可在“修改密码”页面更换。登录失败按来源和账号分别限制尝试次数，计数共享在 PostgreSQL 中。未登录访问客户、报税和图片相关页面或接口会跳转到登录页。局域网以外开放访问时应先配置 HTTPS，并将 `COOKIE_SECURE=1`。

开发时可使用 Flask 自带服务器：

```bash
PYTHONPATH=src uv run --frozen --env-file .env flask --app tax_manager.app:create_app run --debug
```

Windows PowerShell 手动运行开发服务器时，先设置 `$env:PYTHONPATH = 'src'`，再运行上面命令中 `uv run` 开始的部分。Flask 自带服务器仅用于开发；正式运行使用 Gunicorn。

## 使用方式

- **客户**：列表支持按名称或税号搜索，可直接切换中国时间当月的“已报税 / 未报税”。进入详情可查看并修改从注册月份开始的每个月状态、编辑客户资料和注册日期、管理最多 255 字备注、关联标签分类、管理图片。若把注册日期改晚，且此前有“已报税”记录，页面会拒绝修改以保护历史数据。
- **标签分类**：登录后点顶部“标签分类”可新增、删除分类；客户详情可关联或移除分类。API：`GET /api/tag-categories` 列表、`POST /api/tag-categories` 创建，JSON 例子为 `{"name":"一般纳税人"}`；请求必须带登录 Cookie 和 `X-CSRF-Token` 头，令牌可从登录后的页面隐藏表单字段 `csrf_token` 获取。名称最多 60 字且不允许重复。
- **图片库**：保存不关联客户的截图。两类图片都可预览、改名、替换、下载和删除。仅允许真实 PNG、JPEG、WebP 文件，单张最多 10 MB。
- **数据位置**：PostgreSQL 存客户、报税状态、账号和图片目录信息；图片原件保存在 `STORAGE_DIR`。页面下载和预览经网站登录校验后提供。

## 测试与恢复

```bash
uv run --frozen pytest -q
```

需要同时备份 PostgreSQL 和 `STORAGE_DIR`。删除操作会立即移除数据；如需恢复，请从对应时间点的数据库和图片备份一起恢复。数据库提交后若文件清理失败，页面会提示，服务器日志会记录错误；此时可能留有孤立文件，需要人工清理。

停止网站写入后，可检查数据库与图片目录是否一致：

```bash
PYTHONPATH=src uv run --frozen --env-file .env flask --app tax_manager.app:create_app images check-files
```

命令只报告缺失与孤立文件，不自动删除。缺失文件需要从备份恢复；孤立文件在核实后人工清理。图片写入会同步文件和目录，但意外断电、存储设备故障仍需依赖备份与上述检查。

## 目录与依赖

`src/tax_manager/auth` 负责登录和密码，`customers` 负责客户及月度状态，`tags` 负责标签分类，`images` 负责图片表单和文件，`db` 负责连接，`templates` 与 `static` 提供网页界面。SQL 使用参数化查询，动态表名只从固定白名单选择。

Flask（BSD-3-Clause）用于表单页面与会话；相比直接使用标准库 HTTP 服务，减少路由和模板代码。Psycopg 3 及其 binary 包（LGPL-3.0-only）连接 PostgreSQL；相比调用 `psql` 子进程，能使用参数化查询和事务。Pillow（MIT-CMU）校验上传图片的实际格式；相比只检查扩展名，可以拒绝伪装文件。Gunicorn（MIT）用于 Linux 部署。依赖版本锁在 `uv.lock`；这些项目在 2026 年仍有维护中的发布版本。应用本身不使用 LLM。
