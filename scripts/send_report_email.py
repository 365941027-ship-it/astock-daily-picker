#!/usr/bin/env python3
"""把当日选股报告（docx + md）通过 QQ 邮箱 SMTP 发送给指定收件人。

凭证读取顺序：
1. 环境变量 SMTP_USER / SMTP_AUTH_CODE
2. macOS 钥匙串：service="A股选股报告推送"（account 为发件邮箱，password 为授权码）

用法：
    python scripts/send_report_email.py --date 2026-08-14 \
        --to 365941027@qq.com [--subject 自定义主题]

首次配置钥匙串（把授权码存进系统钥匙串，建议用这个）：
    security add-generic-password -U -a 365941027@qq.com \
        -s "A股选股报告推送" -w "你的16位授权码"
"""

from __future__ import annotations

import argparse
import os
import smtplib
import subprocess
import sys
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
KEYCHAIN_SERVICE = "A股选股报告推送"


def get_credentials() -> tuple[str, str]:
    """返回 (SMTP_USER, SMTP_AUTH_CODE)。"""
    user = os.environ.get("SMTP_USER", "").strip()
    code = os.environ.get("SMTP_AUTH_CODE", "").strip()
    if user and code:
        return user, code
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except Exception:
        out = None
    if out and out.returncode == 0 and out.stdout.strip():
        # 钥匙串存的是 password=授权码；账号再查一次
        acct = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
            capture_output=True, text=True, check=False, timeout=10,
        )
        if acct.returncode == 0:
            import re
            m = re.search(r'"acct"<blob>="([^"]+)"', acct.stdout)
            if m:
                return m.group(1), out.stdout.strip()
    raise RuntimeError(
        "未找到 QQ 邮箱 SMTP 凭证。请设置环境变量 SMTP_USER/SMTP_AUTH_CODE，"
        "或用 `security add-generic-password -U -a 邮箱 -s "
        f"{KEYCHAIN_SERVICE} -w 授权码` 存入钥匙串。"
    )


def build_message(subject: str, to_addr: str, from_name: str, attachments: list[Path]) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), ""))  # 发件人由 SMTP 登录账号决定
    msg["To"] = to_addr
    msg["Subject"] = Header(subject, "utf-8")
    body = (
        f"您好：\n\n附件为 {subject}。\n\n"
        "本报告仅为技术面短线观察池，不构成投资建议，不承诺收益。\n"
        "次日交易请结合盘面、板块与仓位纪律自行判断。\n"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for p in attachments:
        part = MIMEText(p.read_bytes(), "base64", "utf-8")
        part["Content-Type"] = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if p.suffix == ".docx"
            else "text/markdown"
        )
        part.add_header("Content-Disposition", "attachment", filename=("utf-8", "", p.name))
        msg.attach(part)
    return msg


def send(subject: str, to_addr: str, attachments: list[Path]) -> None:
    user, auth = get_credentials()
    msg = build_message(subject, to_addr, "A股每日选股", attachments)
    with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30) as server:
        server.login(user, auth)
        server.sendmail(user, [to_addr], msg.as_string())


def main() -> int:
    parser = argparse.ArgumentParser(description="发送 A股选股报告邮件")
    parser.add_argument("--date", required=True, help="数据日期 YYYY-MM-DD，用于定位报告文件")
    parser.add_argument("--to", required=True, help="收件人邮箱")
    parser.add_argument("--subject", default=None, help="邮件主题，默认取报告标题")
    parser.add_argument("--dir", default=None, help="报告所在目录，默认工作区根目录")
    args = parser.parse_args()

    base = Path(args.dir).resolve() if args.dir else BASE
    files = [
        base / f"A股短线候选_{args.date}.docx",
        base / f"A股短线候选_{args.date}.md",
    ]
    files = [p for p in files if p.exists()]
    if not files:
        print(f"错误：{args.date} 没有找到任何报告文件（{base}）")
        return 2
    subject = args.subject or f"A股短线候选观察清单 - {args.date}"
    try:
        send(subject, args.to, files)
    except Exception as exc:  # noqa: BLE001
        print(f"发送失败：{exc}")
        return 1
    print(f"邮件已发送：{args.to}（附件 {len(files)} 个：{', '.join(p.name for p in files)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
