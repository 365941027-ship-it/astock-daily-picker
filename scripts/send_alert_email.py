#!/usr/bin/env python3
"""盯盘即时通知（纯文本邮件，快速发送）。

凭证读取顺序：
1. 环境变量 SMTP_USER / SMTP_AUTH_CODE（服务器容器/CI 推荐）
2. 本地配置 ~/.config/astock_smtp.json：{"user": "...", "code": "16位授权码"}

用法：
    python scripts/send_alert_email.py --to 365941027@qq.com \
        --subject "盯盘提醒" --body "贵州茅台 回踩站回…"
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr


def _credentials() -> tuple[str, str]:
    user = os.environ.get("SMTP_USER", "").strip()
    code = os.environ.get("SMTP_AUTH_CODE", "").strip()
    if user and code:
        return user, code
    path = os.path.expanduser("~/.config/astock_smtp.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        u, c = str(cfg.get("user") or "").strip(), str(cfg.get("code") or "").strip()
        if u and c:
            return u, c
    except Exception:
        pass
    raise RuntimeError("缺少 SMTP 凭证：请设置 SMTP_USER/SMTP_AUTH_CODE 或 ~/.config/astock_smtp.json")


def send(to_addr: str, subject: str, body: str) -> None:
    user, auth = _credentials()
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = formataddr((str(Header("A股盯盘", "utf-8")), ""))
    msg["To"] = to_addr
    msg["Subject"] = Header(subject, "utf-8")
    with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=20) as server:
        server.login(user, auth)
        server.sendmail(user, [to_addr], msg.as_string())


def main() -> int:
    parser = argparse.ArgumentParser(description="盯盘即时通知邮件")
    parser.add_argument("--to", required=True, help="收件邮箱")
    parser.add_argument("--subject", required=True, help="邮件主题")
    parser.add_argument("--body", default="", help="正文")
    args = parser.parse_args()
    try:
        send(args.to, args.subject, args.body)
    except Exception as exc:  # noqa: BLE001
        print(f"邮件发送失败：{exc}", file=sys.stderr)
        return 1
    print(f"邮件已发送：{args.to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
