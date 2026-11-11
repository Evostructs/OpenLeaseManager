"""读取全局样式的共享工具。

frontend/src/style.css 已按原始顺序拆分为 frontend/src/styles/*.css，
style.css 本身保留为只含 @import 的索引文件。按 @import 顺序拼接各拆分
文件即可还原拆分前的完整样式内容（与拆分前逐字节一致），因此各静态测试
对"全局样式表包含/不包含某选择器"的断言语义保持不变。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STYLE_INDEX = ROOT / "frontend" / "src" / "style.css"


def load_style_source() -> str:
    index = STYLE_INDEX.read_text(encoding="utf-8")
    parts = [
        (STYLE_INDEX.parent / match.group(1)).read_text(encoding="utf-8")
        for match in re.finditer(r"@import\s+'(.+?)';", index)
    ]
    return "".join(parts) if parts else index
