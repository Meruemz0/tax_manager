# tax_manager

一个基于 Flask 与 PostgreSQL 的税务客户管理示例。公开首页可以直接访问，客户资料、逐月报税状态、标签分类及图片操作需要登录。

## 开始使用

1. 阅读 [Demo 安装与运行说明](demo/README.md)。
2. 新数据库在 Navicat 中依次执行 [基础建表](demo/init.sql) 与 [增量升级](demo/upgrade_v2.sql)；已有 Demo 数据库只执行尚未运行的升级脚本。
3. 复制 `demo/.env.example` 为 `demo/.env` 并填写自己的数据库连接。`.env`、上传文件和会话密钥均被 Git 忽略。
4. 新数据库的 `wfg1` 账号没有公开初始密码；先按 Demo 说明运行 `auth set-password wfg1` 设置私有密码，再登录。

代码位于 `demo/src`，测试在 `demo/tests`，第二阶段的范围见[类云帐房功能与字段规划](第二阶段_类云帐房功能与字段规划.md)。

## GitHub 同步

本目录是独立的 Git 仓库。首次克隆：

```bash
git clone https://github.com/Meruemz0/tax_manager.git
cd tax_manager
```

在本地修改后，从本目录执行：

```bash
git status
git add README.md demo 第二阶段_类云帐房功能与字段规划.md 工程规范.md init.sql
git commit -m "Describe changes"
git push origin main
```

在另一台机器获取更新时执行 `git pull --ff-only origin main`。提交前检查 `git status` 和 `git diff --cached`；不要强制添加 `.env`、数据库备份、客户图片或真实客户资料。Git 只同步源代码与文档，PostgreSQL 数据和上传图片需要另行备份与迁移。

## 许可证

MIT，见 [LICENSE](LICENSE)。
