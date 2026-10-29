#!/usr/bin/env python
"""开通/维护认证用户。

用法（项目根目录）：
    PYTHONPATH=src .venv/bin/python scripts/manage_auth_users.py create \
        --user-id u_1001 --username zhangsan --roles requester

    PYTHONPATH=src .venv/bin/python scripts/manage_auth_users.py reset-password \
        --user-id u_1001

    PYTHONPATH=src .venv/bin/python scripts/manage_auth_users.py list

密码通过交互输入；自动化场景可用 --password-stdin 从标准输入读取。
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent.procurement.database import Database  # noqa: E402
from api_view.auth import config  # noqa: E402
from api_view.auth.passwords import hash_password  # noqa: E402
from api_view.auth.users import UserRepository  # noqa: E402


def _read_password(password_stdin: bool) -> str:
    if password_stdin:
        password = sys.stdin.readline().rstrip("\n")
        if not password:
            raise SystemExit("标准输入中没有密码")
        return password
    first = getpass.getpass("密码: ")
    second = getpass.getpass("确认密码: ")
    if first != second:
        raise SystemExit("两次输入的密码不一致")
    return first


async def _run(args: argparse.Namespace) -> int:
    database = Database(config.AUTH_DATABASE_URL)
    await database.connect()
    try:
        repository = UserRepository(database)
        await repository.setup()

        if args.command == "create":
            display_name = (args.display_name or args.username).strip()
            record = await repository.create(
                user_id=args.user_id,
                username=args.username,
                display_name=display_name,
                password_hash=hash_password(_read_password(args.password_stdin)),
                roles=tuple(
                    role.strip() for role in args.roles.split(",") if role.strip()
                ),
            )
            print(
                f"已创建用户 {record.user_id} ({record.username})，"
                f"显示名: {record.display_name}，角色: {', '.join(record.roles) or '-'}"
            )
            return 0

        if args.command == "reset-password":
            await repository.update_password(
                args.user_id,
                hash_password(_read_password(args.password_stdin)),
            )
            print(f"已重置用户 {args.user_id} 的密码")
            return 0

        if args.command == "list":
            records, _total = await repository.list_users(limit=100)
            if not records:
                print("（用户表为空）")
                return 0
            for record in records:
                state = "active" if record.is_active else "disabled"
                print(
                    f"{record.user_id}\t{record.username}\t{record.display_name or '-'}\t"
                    f"{','.join(record.roles) or '-'}\t{state}"
                )
            return 0

        raise SystemExit(f"未知命令: {args.command}")
    finally:
        await database.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="维护 ERP 采购助手认证用户")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="创建用户")
    create.add_argument("--user-id", required=True, help="内部用户标识，如 u_1001")
    create.add_argument("--username", required=True, help="登录名")
    create.add_argument("--display-name", default="", help="显示名称，默认使用用户名")
    create.add_argument("--roles", default="requester", help="角色，逗号分隔")
    create.add_argument(
        "--password-stdin",
        action="store_true",
        help="从标准输入读取密码（自动化场景）",
    )

    reset = sub.add_parser("reset-password", help="重置密码")
    reset.add_argument("--user-id", required=True)
    reset.add_argument("--password-stdin", action="store_true")

    sub.add_parser("list", help="列出用户")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # CLI 场景直接给出可读错误
        print(f"操作失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
