#!/usr/bin/env python3
"""盘后更新完成通知（可选）。

支持：Server酱(sct) / 钉钉(dingtalk) / 飞书(feishu) / 企业微信(qyweixin) 通用 webhook。

配置方式（任选其一）：
1. 环境变量 ASTOCK_PUSH_WEBHOOK 为完整 webhook URL；
2. 本地文件 ~/.config/astock_push.json：{"type": "sct|dingtalk|feishu|qyweixin", "webhook": "..."}

未配置时静默返回 0，不打扰主流程。注意该文件不要提交到公开仓库。

用法：
    python scripts/send_push.py --date 2026-09-04 --text "可选一句话"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request


def _load_webhook() -> tuple[str, str] | None:
    url = os.environ.get("ASTOCK_PUSH_WEBHOOK", "").strip()
    if url:
        return "auto", url
    path = os.path.expanduser("~/.config/astock_push.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        wh = str(cfg.get("webhook") or "").strip()
        if wh:
            return str(cfg.get("type") or "auto"), wh
    except Exception:
        pass
    return None


def _send(type_: str, webhook: str, title: str, text: str) -> None:
    if type_ == "sct" or "sctapi" in webhook:
        payload = urllib.parse.urlencode({"title": title, "desp": text}).encode()
        req = urllib.request.Request(webhook, data=payload, method="POST")
    elif type_ == "dingtalk" or "oapi.dingtalk.com" in webhook:
        body = {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
        req = urllib.request.Request(webhook, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    elif type_ == "qyweixin" or "qyapi.weixin.qq.com" in webhook:
        body = {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
        req = urllib.request.Request(webhook, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    else:  # feishu / 通用
        body = {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
        req = urllib.request.Request(webhook, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="盘后更新通知")
    parser.add_argument("--date", default="", help="数据日期")
    parser.add_argument("--url", default="https://365941027-ship-it.github.io/astock-daily-picker/", help="公网地址")
    parser.add_argument("--text", default="", help="一句话结论")
    args = parser.parse_args()

    conf = _load_webhook()
    if not conf:
        print("[推送] 未配置 webhook（ASTOCK_PUSH_WEBHOOK 或 ~/.config/astock_push.json），已跳过")
        return 0
    typ, wh = conf
    title = f"A股每日更新 {args.date or ''}"
    text = args.text or f"最新数据已更新：{args.date}\n{args.url}"
    try:
        _send(typ, wh, title, text)
        print("[推送] 已发送")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[推送] 发送失败：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
