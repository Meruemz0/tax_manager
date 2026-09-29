"""Optional local SSH forwarding for a PostgreSQL server reached through SSH."""

from contextlib import contextmanager
import os
import socket
import subprocess
import time
from urllib.parse import urlsplit

from psycopg.conninfo import make_conninfo


class TunnelError(RuntimeError):
    pass


def _port(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise TunnelError(f"{name} 必须是端口号") from exc
    if not 1 <= value <= 65535:
        raise TunnelError(f"{name} 必须在 1 到 65535 之间")
    return value


def _local_port_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


@contextmanager
def database_tunnel(database_url: str):
    """Yield the effective DB URL, keeping the SSH process alive until exit."""
    ssh_host = os.environ.get("SSH_HOST", "").strip()
    if not ssh_host:
        yield database_url
        return

    ssh_user = os.environ.get("SSH_USER", "").strip()
    if not ssh_user:
        raise TunnelError("已设置 SSH_HOST，但缺少 SSH_USER")
    ssh_port = _port("SSH_PORT", 22)
    local_port = _port("SSH_LOCAL_PORT", 15433)
    remote_host = os.environ.get("SSH_TARGET_HOST", "127.0.0.1").strip()
    if not remote_host:
        raise TunnelError("SSH_TARGET_HOST 不能为空")
    try:
        remote_port = urlsplit(database_url).port or 5432
    except ValueError as exc:
        raise TunnelError("DATABASE_URL 中的数据库端口无效") from exc
    if _local_port_ready(local_port):
        raise TunnelError(f"本机端口 {local_port} 已被占用，无法建立 SSH 转发")

    command = [
        "ssh", "-N", "-L", f"127.0.0.1:{local_port}:{remote_host}:{remote_port}",
        "-p", str(ssh_port), "-o", "ExitOnForwardFailure=yes",
        "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30",
        f"{ssh_user}@{ssh_host}",
    ]
    print("正在建立 SSH 隧道；如有提示，请输入 SSH 密码（输入时不会显示字符）。", flush=True)
    try:
        process = subprocess.Popen(command)
    except OSError as exc:
        raise TunnelError("无法启动 Windows 的 ssh 程序") from exc

    try:
        deadline = time.monotonic() + 120
        while not _local_port_ready(local_port):
            if process.poll() is not None:
                raise TunnelError("SSH 连接已退出；请检查 SSH 用户名、密码和服务器地址")
            if time.monotonic() >= deadline:
                raise TunnelError("等待 SSH 隧道超时；请检查 SSH 密码提示和服务器连通性")
            time.sleep(0.2)
        print("SSH 隧道已建立。", flush=True)
        yield make_conninfo(database_url, host="127.0.0.1", port=local_port)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
