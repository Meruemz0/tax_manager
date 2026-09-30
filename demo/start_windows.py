"""Double-click launcher for the local Windows development server."""

import os
import sys
import webbrowser

import uvicorn

from ssh_tunnel import TunnelError, database_tunnel
from tax_manager.app import create_app


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url or any(part in database_url for part in ("DB_USER", "DB_PASSWORD", "DB_HOST")):
        print("请先在 demo/.env 中填写真实的 DATABASE_URL，再双击启动。", file=sys.stderr)
        return 2

    try:
        with database_tunnel(database_url) as effective_url:
            app = create_app({"DATABASE_URL": effective_url})
            print("网站已启动：http://127.0.0.1:8000/", flush=True)
            print("保持此窗口打开；按 Ctrl+C 停止网站。", flush=True)
            webbrowser.open("http://127.0.0.1:8000/")
            uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
    except KeyboardInterrupt:
        pass
    except TunnelError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
