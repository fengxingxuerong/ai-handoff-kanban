# -*- coding: utf-8 -*-
"""
AI 交接看板 - 本机 AI 端点初始化（安全版）

本脚本**不再内置任何真实 Key**，改从环境变量或本机私有文件读取，
避免密钥随源码 / 安装包 / 项目文件夹一起被分发出去。

用法一（临时生效）：
    set KANBAN_KEY_SENSENOVA=sk-aaa,sk-bbb,sk-ccc
    set KANBAN_KEY_AMD_RADEON=sk-xxx
    .venv\\Scripts\\python.exe scripts\\init_ai_config.py

用法二（本机私有文件，不会进仓库）：
    在 %USERPROFILE%\\.ai-kanban\\keys.local.json 写入：
    {
      "SenseNova": ["sk-aaa", "sk-bbb"],
      "OpenRouter": ["sk-xxx"]
    }
    再执行本脚本即可（无需环境变量）。

说明：
- 键名必须与 app/llm.py 里 DEFAULT_ENDPOINTS 的 name 一致；脚本只覆盖已存在端点的 Key，
  未知端点会被跳过，避免手滑写进无效配置。
- 目标文件：%USERPROFILE%\\.ai-kanban\\config.json（含明文 Key，请勿外传、勿提交仓库）
- 已存在的 config.json 会先备份为 config.json.bak
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app import llm  # noqa: E402

ENV_PREFIX = "KANBAN_KEY_"
LOCAL_KEYS_FILE = llm.config_file().parent / "keys.local.json"


def _split(raw: str) -> list[str]:
    return [k.strip() for k in (raw or "").replace(";", ",").split(",") if k.strip()]


def env_name_for(endpoint_name: str) -> str:
    return ENV_PREFIX + endpoint_name.upper().replace(" ", "_").replace("-", "_")


def collect_keys() -> dict[str, list[str]]:
    """从环境变量与本机私有文件收集 Key，合并去重"""
    keys: dict[str, list[str]] = {}

    for ep in llm.DEFAULT_ENDPOINTS:
        got = _split(os.environ.get(env_name_for(ep["name"]), ""))
        if got:
            keys.setdefault(ep["name"], []).extend(got)

    if LOCAL_KEYS_FILE.exists():
        try:
            local = json.loads(LOCAL_KEYS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"! 读取 {LOCAL_KEYS_FILE} 失败：{e}")
            local = {}
        if isinstance(local, dict):
            for name, vals in local.items():
                if isinstance(vals, str):
                    vals = _split(vals)
                if isinstance(vals, list):
                    keys.setdefault(name, []).extend(str(v).strip() for v in vals if str(v).strip())

    for name, vals in list(keys.items()):
        seen, uniq = set(), []
        for v in vals:
            if v not in seen:
                seen.add(v)
                uniq.append(v)
        keys[name] = uniq
    return keys


def main() -> int:
    keys = collect_keys()
    if not keys:
        print("未发现任何 Key，未做任何修改。")
        print("请设置环境变量（多个 Key 用逗号分隔），例如：")
        print(f"  set {env_name_for('SenseNova')}=sk-aaa,sk-bbb")
        print(f"或把 Key 写进 {LOCAL_KEYS_FILE}")
        return 1

    target = llm.config_file()
    if target.exists():
        shutil.copy2(target, target.with_suffix(".json.bak"))
        print(f"已备份原配置 -> {target.with_suffix('.json.bak')}")

    def apply(cfg: dict) -> None:
        known = {ep.get("name") for ep in cfg.get("ai", {}).get("endpoints", [])}
        for ep in cfg.get("ai", {}).get("endpoints", []):
            if ep.get("name") in keys:
                ep["api_keys"] = keys[ep["name"]]
        for name in keys:
            if name not in known:
                print(f"! 跳过未知端点 {name}（请先在应用设置界面添加该端点）")

    llm.update_config(apply)

    cfg = llm.load_config()
    print(f"完成，已写入 {target}")
    for ep in cfg.get("ai", {}).get("endpoints", []):
        print(f"  - {ep['name']}: {len(ep.get('api_keys', []))} 个 Key · 模型 {'/'.join(ep.get('models', []))}")
    print("提醒：该文件含明文 Key，请勿分享或提交到公开仓库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
