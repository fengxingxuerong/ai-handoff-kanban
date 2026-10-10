# -*- coding: utf-8 -*-
"""交接内容脱敏。

**从 `app/main.py` 整体搬来（2026-09-26 大文件拆分），内容逐字未改。**
`app/main.py` 用 `from app.redact import ...` 保住既有名字 —— 测试里的
`from app.main import X` / `monkeypatch.setattr(main, "X", ...)` 仍然有效。

搬移理由：这些是**纯函数/纯常量**（不碰文件、不碰网络、不读全局状态），
原先埋在 3596 行的 `main.py` 里，只能靠端到端间接验证边界；独立成模块后可以穷举。
约束型注释（含"不要…否则…"的）已随代码一起搬，不要在新家把它们删掉。
"""
from __future__ import annotations

import re

MASK_MARK = "********"      # 脱敏掩码；「建议回传里带掩码就别落盘」那条判断认它

_REDACT_PATTERNS = [
    # 前缀型 Key：OpenAI / Anthropic / NVIDIA / GitHub 全系 / GitLab / AWS（含临时凭据 ASIA）/
    # Slack / npm / PyPI / Docker Hub / HuggingFace / Sonar
    (r"(?i)\b(sk|rc|nvapi|ghp|gho|ghu|ghs|ghr|github_pat|glpat|xoxb|xoxp|xapp"
     r"|npm_|pypi|dckr_pat|hf|sgpat)[-_][A-Za-z0-9_-]{6,}", r"\1-" + MASK_MARK),
    # AWS 访问密钥是前缀直接跟 16 位大写字母数字，**没有分隔符**，套不进上面那条
    (r"\b(AKIA|ASIA)[0-9A-Z]{16}\b", r"\1-" + MASK_MARK),
    # JWT：三段 base64url，段段有点分着，正常中文/代码里不会出现
    (r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", "JWT." + MASK_MARK),
    (r"(?i)\bBearer\s+[A-Za-z0-9._-]{8,}", "Bearer " + MASK_MARK),
    # 私钥整块（PEM）：正文本来就是 base64，逐条规则都命中不了，只能按块盖掉
    (r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----(.*?)(-----END [A-Z ]*PRIVATE KEY-----|$)",
     "-----" + MASK_MARK + "-----"),
    # key: value 形态。**JSON 里键名是带引号的**（`"api_key": "xxx"`），
    # 原来那条 `key\s*[:=]` 卡在引号上匹配不到，于是最常见的 JSON 配置反而没脱敏。
    # 另外替换必须原样保留分隔符 —— 老写法把 `:` 统一换成 `=`，脱敏顺手改了别人的格式。
    (r"(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|refresh[_-]?token|token"
     r"|secret|password|passwd|pwd|key|授权码|密钥)([\"']?)(\s*[:=]\s*)([\"']?)[^\s\"']{4,}",
     r"\1\2\3\4" + MASK_MARK),
    # 中文键名 kv 形态（密码=xxx / 密钥: xxx / 授权码：xxx）。2026-10-08 黑盒探索发现
    # 上面那条 kv 规则吃不到纯中文键：\b 在中文与符号之间不成边界，且「授权码」以"码"
    # 结尾时词尾 \b 根本不成立 —— 英文 password= 能盖掉、密码= 却原样漏出。
    # 中文不吃 \b，这条不走词边界：词面本身就是信号；值遇到空白/常见标点即停。
    (r"(?i)(密码|口令|密钥|授权码)(\s*[：:=]\s*)([^\s，。；,;、]{4,})", r"\1\2" + MASK_MARK),
    # 连接串 postgres://user:pass@host —— 盖掉口令，主机名留着（排查问题要用）
    # 必须排在邮箱规则前面：否则邮箱规则先把 `pass@host.com` 吃掉，口令就漏在协议头里
    (r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s]+(@)", r"\1" + MASK_MARK + r"\2"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "****@****"),
]

def redact_text(text: str) -> str:
    """把交接内容里的敏感信息替换为掩码（API Key / 令牌 / 密码 / 邮箱）"""
    if not text:
        return text
    for pat, rep in _REDACT_PATTERNS:
        text = re.sub(pat, rep, text)
    return text
