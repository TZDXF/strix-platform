"""平台技能库：自定义 strix 技能的存储、校验与扫描时材料化。

引擎侧机制（strix 1.5.3）：技能是 `<类目>/<名称>.md` 知识包（YAML frontmatter 的
name/description + Markdown 正文），智能体创建时按任务相关性动态加载（每个智能体
最多 5 个），也可由根智能体经 load_skill 工具显式加载。技能目录默认只有内置包，
外部目录须经 register_skill_dir() 在引擎进程内注册——平台在启动 strix 子进程时经
python -c 引导段完成注册（runner._BOOTSTRAP），注册目录优先级高于内置目录，同
`<类目>/<名称>.md` 相对路径可遮蔽内置技能。

「联网搜索」由旧的指令注入方案迁移为种子技能：管理员配置 WEB_SEARCH_MCP_URL 后，
系统在技能库维护一条可启停的内置技能（custom/web_search_internal），任务勾选联网
搜索时指令中仅注入一行加载指引，具体调用方法全部沉淀在技能正文里。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from .config import get_settings
from .models import Skill

SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CATEGORY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# 联网搜索种子技能（内置，custom 类目）：内容随 WEB_SEARCH_MCP_URL 配置生成
WEB_SEARCH_CATEGORY = "custom"
WEB_SEARCH_NAME = "web_search_internal"
WEB_SEARCH_FULL_NAME = f"{WEB_SEARCH_CATEGORY}/{WEB_SEARCH_NAME}"


def new_skill_id() -> str:
    return uuid.uuid4().hex


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)


def parse_skill_markdown(content: str) -> tuple[dict[str, str], str]:
    """解析技能 md 的 frontmatter，返回 (元数据, 正文)；无/坏 frontmatter 返回 ({}, 原文)。"""
    m = _FRONTMATTER_RE.match(content or "")
    if m is None:
        return {}, (content or "")
    try:
        import yaml

        parsed = yaml.safe_load(m.group(1))
    except Exception:  # noqa: BLE001 —— yaml 异常类型不定，坏头按无元数据处理
        return {}, content
    if not isinstance(parsed, dict):
        return {}, content
    return {str(k): str(v) for k, v in parsed.items()}, content


def ensure_frontmatter(content: str, name: str, description: str) -> str:
    """内容缺 frontmatter 时按名称/描述补齐（引擎靠 frontmatter 识别技能元数据）。"""
    if _FRONTMATTER_RE.match(content or ""):
        return content
    desc = (description or "").replace('"', "'")[:300]
    return f'---\nname: {name}\ndescription: "{desc}"\n---\n\n{content.strip()}\n'


def valid_skill_name(name: str) -> bool:
    return bool(SKILL_NAME_RE.match(name or ""))


def valid_category(category: str) -> bool:
    return bool(CATEGORY_RE.match(category or ""))


def find_web_search_skill(db: Session) -> Skill | None:
    return (
        db.query(Skill)
        .filter(Skill.category == WEB_SEARCH_CATEGORY, Skill.name == WEB_SEARCH_NAME)
        .one_or_none()
    )


def build_web_search_content(mcp_url: str) -> str:
    """把内网 MCP 搜索端点的调用指南生成为技能正文（含 frontmatter）。

    与旧版指令注入同源：端点为 Streamable HTTP MCP，支持无状态 JSON-RPC 直调
    （免 initialize/会话），返回 SSE data: 行，content[0].text 内是再包一层的
    JSON 结果串（title/link/content）。差异只在载体：从根智能体任务文本改为
    可启停、可编辑的技能包，指令里只留一行加载指引。
    """
    payload = (
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"web_search_prime",'
        '"arguments":{"search_query":"<english keywords>","location":"us"}}}'
    )
    body = f"""---
name: {WEB_SEARCH_NAME}
description: Internal web-search gateway reachable from the sandbox shell; use it to research CVEs, exploit techniques, payload syntax and bypasses
---

# Internal Web Search (sandbox gateway)

An internal web-search gateway is reachable from the sandbox shell. Load and use this
skill whenever current external knowledge would help the assessment: CVE details,
exploit/PoC approaches, payload syntax, library versions and bypass techniques for the
exact technologies in scope.

## How to search

Run this from the sandbox shell:

```bash
curl -s -m 30 -X POST '{mcp_url}' \\
  -H 'Content-Type: application/json' \\
  -H 'Accept: application/json, text/event-stream' \\
  -d '{payload}' \\
  | sed -n "s/^data://p" \\
  | jq -r '.result.content[0].text | fromjson | .[] | "\\(.title) - \\(.link)\\n\\(.content)"'
```

## Guidance

- Keep queries in English and under 70 characters.
- Optional arguments: `search_recency_filter` (oneDay/oneWeek/oneMonth/oneYear),
  `content_size=high` for deeper summaries, `search_domain_filter` to restrict to
  specific sites (e.g. nvd.nist.gov).
- Cross-check findings across multiple results before acting on them.
- If the gateway is unreachable, continue the assessment without web research.
- When spawning specialist agents whose task benefits from web research, tell them to
  load this skill (`{WEB_SEARCH_FULL_NAME}`) in their task text.
"""
    return body


def sync_web_search_skill(db: Session, admin_user_id: int | None = None) -> Skill:
    """确保联网搜索种子技能存在且内容与当前 WEB_SEARCH_MCP_URL 一致（幂等）。

    - 配置了端点：技能不存在则创建（启用），已存在且 builtin 则刷新正文（保留启停状态）；
      端点被清空时停用技能（正文保留供参考）。
    - 用户改过该技能（builtin=False）：完全不触碰，尊重自定义。
    """
    s = get_settings()
    skill = find_web_search_skill(db)
    if skill is not None and not skill.builtin:
        return skill  # 已被管理员改为自定义技能，不再自动管理
    mcp_url = (s.web_search_mcp_url or "").strip()
    if skill is None:
        if not mcp_url:
            return skill  # type: ignore[return-value]  # 未配置且无种子：什么都不建
        skill = Skill(
            id=new_skill_id(), name=WEB_SEARCH_NAME, category=WEB_SEARCH_CATEGORY,
            description="Internal web-search gateway (sandbox shell); research CVEs, exploits and bypasses",
            content=build_web_search_content(mcp_url), enabled=True, builtin=True,
            created_by=admin_user_id,
        )
        db.add(skill)
    else:
        skill.builtin = True
        if mcp_url:
            skill.content = build_web_search_content(mcp_url)
            skill.enabled = True
        else:
            skill.enabled = False
    db.commit()
    return skill


def web_search_ready(db: Session) -> bool:
    """任务勾选联网搜索的前置条件：端点已配置且种子技能存在并启用。"""
    if not (get_settings().web_search_mcp_url or "").strip():
        return False
    skill = find_web_search_skill(db)
    return bool(skill and skill.enabled)


def web_search_pointer() -> str:
    """任务指令里的联网搜索一行指引：具体方法在技能正文（引擎可经 load_skill 加载）。"""
    return (
        "Web research is ENABLED for this scan: load the skill "
        f"`{WEB_SEARCH_FULL_NAME}` (via the load_skill tool) and follow it to search "
        "the internal web gateway from your sandbox shell. When spawning specialist "
        f"agents, instruct them to load `{WEB_SEARCH_FULL_NAME}` as well."
    )


def materialize_skills(db: Session, dest: Path) -> Path | None:
    """把启用的技能写成 `<dest>/<类目>/<名称>.md`，返回目录；无可用技能返回 None。

    扫描启动前由 worker 调用：目标目录随 workspace 卷持久化，每次全量重建
    （先清空再写入），保证启停/编辑即时生效；api 与 worker 共享该卷，路径一致。
    """
    import shutil

    skills = db.query(Skill).filter(Skill.enabled == True).all()  # noqa: E712  # ruff 未启用 E712 时忽略
    if not skills:
        return None
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    written = 0
    for sk in skills:
        if not (valid_category(sk.category) and valid_skill_name(sk.name) and (sk.content or "").strip()):
            continue
        d = dest / sk.category
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sk.name}.md").write_text(sk.content, encoding="utf-8")
        written += 1
    return dest if written else None
