#!/usr/bin/env python3
"""A股每日短线选股小程序入口。

用法示例：
    python pick_stocks.py                          # 最近交易日，md + docx
    python pick_stocks.py --date 2026-08-14        # 指定数据日期
    python pick_stocks.py --top 10 --format md     # 只看 Markdown
    python pick_stocks.py --offline                # 用本地缓存重出报告
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from daily_picker.cli import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
