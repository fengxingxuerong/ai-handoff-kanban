# -*- coding: utf-8 -*-
"""多智能体入口文件：把 AGENTS.md 投影成各工具要求的入口（对比同类得出的能力补强）

同类工具各要各的入口文件（Claude Code 只认 CLAUDE.md、Cursor 要 .cursor/rules/*.mdc、
Copilot 要 .github/copilot-instructions.md、Gemini 要 GEMINI.md、Cline 要 .clinerules、
Aider 要 CONVENTIONS.md、Windsurf 要 .windsurfrules）。这里验证一次生成、全部对齐，
且 AGENTS.md 是唯一事实来源（其余都是它的投影，不会各写各的慢慢漂移）。
"""
from __future__ import annotations

import json

import pytest

from app.main import AGENT_ENTRY_TARGETS, CLAUDE_BRIDGE, build_agent_entries

CONTENT = "# AGENTS.md — 示例\n\n## 构建\n\npytest\n"
PROJECT = {"id": "p1", "name": "示例项目 A · 后台系统", "status": "进行中", "progress": 65,
           "priority": "高", "tags": [], "tasks": []}

EXPECTED_PATHS = [
    "AGENTS.md",
    "CLAUDE.md",
    ".cursor/rules/project.mdc",
    ".github/copilot-instructions.md",
    "GEMINI.md",
    ".clinerules/project.md",
    "CONVENTIONS.md",
    ".windsurfrules",
]


@pytest.fixture
def ai(ai_client):
    """假 LLM 端点上的进程内客户端（夹具来自 conftest）"""
    return ai_client


# ---------------- 纯生成逻辑 ----------------

def test_all_targets_covered():
    assert sorted(AGENT_ENTRY_TARGETS.values()) == sorted(EXPECTED_PATHS)


def test_default_generates_eight_entries():
    entries = build_agent_entries(PROJECT, CONTENT)
    assert sorted(entries) == sorted(EXPECTED_PATHS)
    assert entries["AGENTS.md"] == CONTENT


def test_claude_is_a_bridge_not_a_copy():
    """Claude Code 不读 AGENTS.md，官方做法是 CLAUDE.md 里导入它 —— 必须是桥接而不是复制"""
    claude = build_agent_entries(PROJECT, CONTENT)["CLAUDE.md"]
    assert claude == CLAUDE_BRIDGE
    assert "@AGENTS.md" in claude
    assert "## 构建" not in claude, "内容要留在 AGENTS.md 里，避免两份漂移"


def test_cursor_rule_has_frontmatter():
    rule = build_agent_entries(PROJECT, CONTENT)[".cursor/rules/project.mdc"]
    assert rule.startswith("---\n")
    head = rule.split("---")[1]
    assert "description:" in head and "alwaysApply: true" in head
    assert CONTENT.strip() in rule, "frontmatter 之后必须带正文"


def test_plain_targets_receive_content_verbatim():
    entries = build_agent_entries(PROJECT, CONTENT)
    for rel in ("GEMINI.md", ".github/copilot-instructions.md", "CONVENTIONS.md",
                ".clinerules/project.md", ".windsurfrules"):
        assert CONTENT.strip() in entries[rel], f"{rel} 应直接承载正文"


def test_subset_and_unknown_targets():
    entries = build_agent_entries(PROJECT, CONTENT, ["agents", "cursor", "不存在的工具"])
    assert sorted(entries) == sorted(["AGENTS.md", ".cursor/rules/project.mdc"])


def test_empty_content_still_produces_valid_files():
    entries = build_agent_entries(PROJECT, "")
    assert sorted(entries) == sorted(EXPECTED_PATHS)
    assert entries[".cursor/rules/project.mdc"].startswith("---\n")


# ---------------- 接口：预览 ----------------

def test_preview_does_not_touch_disk(ai, tmp_path):
    pid = ai.get("/api/projects")[1]["projects"][0]["id"]
    ai.stub.push_text(CONTENT)
    status, res = ai.post(f"/api/export/agent-files/{pid}", {"dir": ""})
    assert status == 200 and res["preview"] is True
    assert res["written"] == []
    assert sorted(f["path"] for f in res["files"]) == sorted(EXPECTED_PATHS)
    assert res["source"] == "ai"
    assert not list(tmp_path.rglob("AGENTS.md")), "预览阶段不该落盘"


def test_passed_in_content_skips_llm(ai):
    """前端已经预览过内容再写盘时，不该再花一次模型调用"""
    pid = ai.get("/api/projects")[1]["projects"][0]["id"]
    before = len(ai.stub.calls)
    status, res = ai.post(f"/api/export/agent-files/{pid}", {"dir": "", "content": CONTENT})
    assert status == 200 and res["source"] == "passed-in"
    assert len(ai.stub.calls) == before, "传了内容就不该再请求模型"


# ---------------- 接口：写盘 ----------------

def test_write_creates_all_entry_files(ai, tmp_path):
    pid = ai.get("/api/projects")[1]["projects"][0]["id"]
    repo = tmp_path / "fake-repo"
    repo.mkdir()
    status, res = ai.post(f"/api/export/agent-files/{pid}", {"dir": str(repo), "content": CONTENT})
    assert status == 200 and res["preview"] is False
    assert sorted(res["written"]) == sorted(EXPECTED_PATHS)
    assert res["skipped_existing"] == []

    for rel in EXPECTED_PATHS:
        f = repo / rel
        assert f.exists(), f"{rel} 未写出"
        assert f.read_text(encoding="utf-8").strip(), f"{rel} 内容为空"
    assert (repo / ".cursor" / "rules" / "project.mdc").exists(), "嵌套目录要自动创建"
    assert "@AGENTS.md" in (repo / "CLAUDE.md").read_text(encoding="utf-8")


def test_existing_files_are_not_overwritten_by_default(ai, tmp_path):
    """仓库里已有同名文件（多半是用户手写的）时默认跳过，绝不静默覆盖"""
    pid = ai.get("/api/projects")[1]["projects"][0]["id"]
    repo = tmp_path / "repo-handwritten"
    repo.mkdir()
    mine = "# 我手写的 AGENTS.md\n\n重要内容\n"
    (repo / "AGENTS.md").write_text(mine, encoding="utf-8")

    status, res = ai.post(f"/api/export/agent-files/{pid}", {"dir": str(repo), "content": CONTENT})
    assert status == 200
    assert res["skipped_existing"] == ["AGENTS.md"]
    assert "AGENTS.md" not in res["written"] and len(res["written"]) == 7
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == mine, "手写文件被覆盖了"


def test_overwrite_flag_replaces_existing(ai, tmp_path):
    pid = ai.get("/api/projects")[1]["projects"][0]["id"]
    repo = tmp_path / "repo-force"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# 旧内容\n", encoding="utf-8")

    status, res = ai.post(f"/api/export/agent-files/{pid}",
                          {"dir": str(repo), "content": CONTENT, "overwrite": True})
    assert status == 200 and "AGENTS.md" in res["written"]
    assert "旧内容" not in (repo / "AGENTS.md").read_text(encoding="utf-8")


def test_write_falls_back_to_local_when_llm_down(ai, tmp_path):
    """LLM 全挂时也要能写出入口文件（用本地模板），否则这功能在断网时就是废的"""
    pid = ai.get("/api/projects")[1]["projects"][0]["id"]
    ai.stub.set_default_status(500)
    repo = tmp_path / "repo-offline"
    status, res = ai.post(f"/api/export/agent-files/{pid}", {"dir": str(repo)})
    assert status == 200 and res["source"] == "local"
    assert (repo / "AGENTS.md").read_text(encoding="utf-8").startswith("# AGENTS.md")


def test_write_rejects_system_directory(ai):
    pid = ai.get("/api/projects")[1]["projects"][0]["id"]
    status, body = ai.post(f"/api/export/agent-files/{pid}",
                           {"dir": r"C:\Windows\System32", "content": CONTENT})
    assert status == 400 and "系统目录" in str(body)


def test_write_to_a_file_path_fails_cleanly(ai, tmp_path):
    pid = ai.get("/api/projects")[1]["projects"][0]["id"]
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("我是文件", encoding="utf-8")
    status, body = ai.post(f"/api/export/agent-files/{pid}",
                           {"dir": str(blocker), "content": CONTENT})
    assert status == 500 and "没有任何文件写入成功" in str(body)


def test_unknown_project_404(ai):
    assert ai.post("/api/export/agent-files/nope", {"dir": ""})[0] == 404


def test_agentsmd_endpoint_still_works(ai):
    """原有的单文件 AGENTS.md 接口行为不变（前端主流程还在用它）"""
    pid = ai.get("/api/projects")[1]["projects"][0]["id"]
    ai.stub.push_text(CONTENT)
    status, body = ai.post(f"/api/export/agentsmd/{pid}")
    assert status == 200 and body["text"] == CONTENT.strip() and body["source"] == "ai"


def test_agentsmd_actually_recalls_the_memory_layer(ai_client):
    """`kanban_memory_add` 对模型承诺"记忆会在交接包、AGENTS.md 和 AI 监管里自动召回"。

    交接包与监管真读了，AGENTS.md 那两处（本地兜底 + 送给模型的素材）以前都不读 ——
    用户按说明把坑记进记忆层，换一个编码工具接手还是踩一遍。
    这条判据钉的是"承诺的那一处真的在那里"，不是文案。
    """
    import app.main as kanban

    ai = ai_client
    _, p = ai.create_project("要召回记忆的项目")
    pid = p["id"]
    ai.post("/api/memories", {"pid": pid, "type": "坑", "text": "迁移前必须先跑 tests/migrate_check.py"})
    ai.post("/api/memories", {"pid": pid, "type": "约定", "text": "接口统一返回 {code,data}"})
    # 别的项目的记忆不该混进来
    _, other = ai.create_project("不相干的项目")
    ai.post("/api/memories", {"pid": other["id"], "type": "事实", "text": "不该出现在这份里的内容"})

    local = kanban._build_agents_md_local(kanban.find_project(pid)[1])
    assert "## 项目记忆" in local, local[:400]
    assert "迁移前必须先跑 tests/migrate_check.py" in local
    assert "【坑】" in local and "【约定】" in local
    assert "不该出现在这份里的内容" not in local, "按项目过滤没生效，别人的坑会被当成自己的"

    # AI 路径：素材里必须带记忆，而且带之前先过脱敏（送出侧也算输出边界）
    ai.stub.push_text("# AGENTS.md\n" + "生成内容占位 enough length here" * 3)
    st, body = ai.post(f"/api/export/agentsmd/{pid}")
    assert st == 200 and body["source"] == "ai", body
    sent = json.dumps(ai.stub.calls[-1]["payload"], ensure_ascii=False)
    assert "迁移前必须先跑" in sent, "模型拿不到记忆，等于这条承诺还是假的"
    assert "不该出现在这份里的内容" not in sent


def test_agentsmd_memory_recall_masks_before_sending_to_the_model(ai_client):
    """记忆正文里写密钥时，送出去的那一份必须已经盖掉（不是只盖返回体）。"""
    ai = ai_client
    _, p = ai.create_project("记忆里带钥匙")
    secret = "sk-" + "MEMPROBE" + "0123456789ab"
    ai.post("/api/memories", {"pid": p["id"], "type": "坑", "text": f"轮换 {secret} 这把"})
    ai.stub.push_text("# AGENTS.md\n" + "占位内容要足够长才能被接受" * 3)
    assert ai.post(f"/api/export/agentsmd/{p['id']}")[0] == 200
    sent = json.dumps(ai.stub.calls[-1]["payload"], ensure_ascii=False)
    assert secret not in sent and "********" in sent, "记忆是新增的送出面，别只给老字段脱敏"
