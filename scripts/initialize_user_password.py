"""为一个已存在的历史用户本地初始化账号密码，不提供 HTTP 管理入口。"""

import argparse
import getpass
from sqlmodel import Session

from app.core.errors import AppError
from app.db import engine
from app.schemas.account import USERNAME_PATTERN
from app.services.identity.authentication import set_existing_user_password


def parse_args() -> argparse.Namespace:
    """读取必须由操作者明确提供的用户 ID 与用户名。"""
    parser = argparse.ArgumentParser(description="为指定历史用户初始化账号密码。")
    parser.add_argument("--user-id", required=True, help="需要初始化凭据的用户 UUID")
    parser.add_argument("--username", required=True, help="新的全局唯一登录用户名")
    return parser.parse_args()


def main() -> None:
    """交互读取两次密码，校验后只更新指定用户的凭据。"""
    args = parse_args()
    username = args.username.strip().lower()
    if not USERNAME_PATTERN.fullmatch(username):
        raise SystemExit("username 必须为 3～50 位小写字母、数字、下划线或连字符。")

    password = getpass.getpass("请输入新密码：")
    password_confirmation = getpass.getpass("请再次输入新密码：")
    if len(password) < 8 or len(password) > 128:
        raise SystemExit("密码长度必须为 8～128 个字符。")
    if password != password_confirmation:
        raise SystemExit("两次输入的密码不一致。")

    try:
        with Session(engine) as session:
            set_existing_user_password(
                user_id=args.user_id,
                username=username,
                password=password,
                session=session,
            )
    except (AppError, ValueError) as exc:
        raise SystemExit(exc.message if isinstance(exc, AppError) else "用户 ID 格式无效。") from exc

    print("PASSWORD_INITIALIZATION_OK")


if __name__ == "__main__":
    main()
