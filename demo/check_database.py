"""Check the local database settings without printing credentials."""

import os
import socket

import psycopg
from psycopg.conninfo import conninfo_to_dict

from ssh_tunnel import TunnelError, database_tunnel


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url or any(part in database_url for part in ("DB_USER", "DB_PASSWORD", "DB_HOST")):
        print("请先在 .env 中填写真实的 DATABASE_URL。")
        return 2

    try:
        with database_tunnel(database_url) as effective_url:
            return check_connection(effective_url)
    except TunnelError as exc:
        print(f"SSH 隧道失败：{exc}")
        return 1


def check_connection(database_url: str) -> int:

    try:
        params = conninfo_to_dict(database_url)
        host, port = params.get("host"), int(params.get("port") or 5432)
        if not host:
            raise ValueError("missing host")
    except (ValueError, psycopg.Error):
        print("DATABASE_URL 格式不正确；请检查主机和端口。")
        return 2

    print(f"检查 PostgreSQL 主机 {host}，端口 {port}。")
    try:
        with socket.create_connection((host, port), timeout=5):
            pass
    except (OSError, TimeoutError) as exc:
        print(f"TCP 连接失败（{type(exc).__name__}）。请核对 Linux IP、Docker 端口映射和防火墙。")
        return 1
    print("TCP 连接成功。")

    try:
        with psycopg.connect(database_url, connect_timeout=5) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT to_regclass('site_users'), to_regclass('login_attempts')")
                site_users, login_attempts = cursor.fetchone()
    except psycopg.Error as exc:
        print(f"PostgreSQL 连接失败（{type(exc).__name__}，SQLSTATE {exc.sqlstate or '无'}）。请核对账号、密码、数据库名和服务器访问规则。")
        return 1

    if not site_users or not login_attempts:
        print("数据库已连接，但缺少登录表。请在这个数据库执行 demo/init.sql。")
        return 1
    print("数据库连接成功，登录表存在。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
