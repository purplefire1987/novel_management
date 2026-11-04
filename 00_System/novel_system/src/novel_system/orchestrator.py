"""编排层：把工作流、Agent、规则与事务串成 CLI 可调用的门面。"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from . import ids as id_service
from . import vault
from .config import Config
from .database.indexer import index_vault
from .database.rebuild import ensure_index, rebuild
from .database.sqlite import Index
from .memory import context_builder, retrieval
from .memory.entity_resolver import resolve_chapter
from .transaction import Transaction, rollback as rollback_txn
from .validators import continuity, foreshadowing
from .validators.schema import Issue

# 类型 -> (目录, 模板)
TYPE_LAYOUT: dict[str, tuple[str, str]] = {
    "character": ("01_Core_Memory/Characters", "Character.md"),
    "location": ("01_Core_Memory/Locations", "Location.md"),
    "faction": ("01_Core_Memory/Factions", "Faction.md"),
    "item": ("01_Core_Memory/Items", "Item.md"),
    "enemy": ("01_Core_Memory/Enemies", "Enemy.md"),
    "world": ("01_Core_Memory/World", "World.md"),
    "mission": ("02_Hot_Memory/Missions", "Mission.md"),
    "story_asset": ("05_Story_Assets/Dialogue", "Story_Asset.md"),
    "volume": ("04_Outline/Volumes", "Volume.md"),
    "chapter_outline": ("04_Outline/Chapters", "Chapter_Outline.md"),
    "foreshadowing": ("06_Plot_Ledger/Foreshadowing", "Foreshadowing.md"),
    "event": ("07_Entity_Graph/Events", "Event.md"),
    "relationship": ("07_Entity_Graph/Relationships", "Relationship.md"),
    "scene_beat": ("04_Outline/Scenes", "Scene_Beat.md"),
    "character_state": ("02_Hot_Memory/Characters", "Character_State.md"),
}

SAFE_NAME_RE = re.compile(r"[^\w\u4e00-\u9fff·\-]+")


class Orchestrator:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config.load()
        self.cfg.ensure_dirs()
        self.index = ensure_index(self.cfg)

    def close(self) -> None:
        self.index.close()

    def __enter__(self) -> "Orchestrator":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------- 查询
    def status(self, chapter_id: str | None = None) -> dict[str, Any]:
        chapters = []
        for row in self.index.all_chapters():
            chapter = dict(row)
            draft = self.cfg.staging_dir / "draft" / f"{row['chapter_id']}.draft.md"
            chapter["has_draft"] = draft.exists()
            chapter["pack"] = (self.cfg.staging_dir / "preflight" / f"PREFLIGHT-{row['chapter_id']}.md").exists()
            chapters.append(chapter)
        out: dict[str, Any] = {"stats": self.index.stats(), "chapters": chapters}
        if chapter_id:
            out["chapter"] = self.index.get_chapter(chapter_id)
            out["events"] = self.index.events_of_chapter(chapter_id)
            out["foreshadowing"] = [r for r in self.index.foreshadowing_all() if r.get("planted_in") == chapter_id]
        return out

    def show(self, doc_id: str) -> dict[str, Any] | None:
        row = self.index.get_entity(doc_id)
        if not row:
            return None
        doc = None
        if row.get("file_path"):
            path = self.cfg.abs(str(row["file_path"]))
            doc = vault.read_doc(path) if path.exists() else None
        return {"entity": row, "body": doc.body if doc else "", "path": row.get("file_path")}

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return retrieval.search(self.cfg, self.index, query, limit)

    def find_entities(self, keyword: str, limit: int = 30) -> list[dict[str, Any]]:
        """按名称/别名/概要查实体档案：录入选名前的必查步骤。"""
        return self.index.search_entities(keyword, limit)

    def audit_log(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.index.recent_audits(limit)

    # ------------------------------------------------------------- 工作流
    def preflight(self, chapter_id: str):
        from .workflow import preflight as preflight_flow

        return preflight_flow.run(self.cfg, self.index, chapter_id)

    def write(self, chapter_id: str, force: bool = False):
        from .workflow import writing

        return writing.run(self.cfg, self.index, chapter_id, force=force)

    def review(self, chapter_id: str):
        from .workflow import review as review_flow

        return review_flow.run(self.cfg, self.index, chapter_id)

    def publish(self, chapter_id: str, confirmed: bool = False):
        from .workflow import review as review_flow

        return review_flow.publish(self.cfg, self.index, chapter_id, confirmed=confirmed)

    def commit_memory(self, chapter_id: str):
        from .workflow import memory_commit

        return memory_commit.generate(self.cfg, self.index, chapter_id)

    def apply_memory(self, chapter_id: str, approve: str = "auto", ids: list[str] | None = None):
        from .workflow import memory_commit

        return memory_commit.apply(
            self.cfg, self.index, chapter_id, approve=approve, ids=ids or []
        )

    def ingest(
        self,
        source: str,
        *,
        volume_id: str = "VOL-0001",
        mode: str = "auto",
        title: str = "",
        dry_run: bool = False,
        tag: bool = False,
        force: bool = False,
    ):
        """原始稿件录入：切章、定 ID、建大纲与正文、批次提交。"""
        from .workflow import ingest as ingest_flow

        result = ingest_flow.run(
            self.cfg,
            self.index,
            source,
            volume_id=volume_id,
            mode=mode,
            title=title,
            dry_run=dry_run,
            tag=tag,
            force=force,
        )
        return result

    # ------------------------------------------------------------- 录入标注
    def annotate(
        self,
        chapter_id: str,
        lines: list[str],
        *,
        replace: bool = False,
    ):
        """给已发布章节补写「作者备注」结构化标注，供记忆抽取使用。"""
        from .workflow import ingest as ingest_flow

        return ingest_flow.annotate(self.cfg, self.index, chapter_id, lines, replace=replace)

    # ------------------------------------------------------------- 素材录入
    def import_assets(
        self,
        profile: str,
        *,
        source: str | None = None,
        dry_run: bool = False,
        force: bool = False,
        archive: bool = True,
        tag: bool = False,
    ):
        """Excel 资产库 → 分类实体文档（人物 / 势力 / 物品 / 地点 / 生物 / 总表）。"""
        from .workflow import assets as assets_flow

        return assets_flow.import_workbook(
            self.cfg,
            self.index,
            profile,
            source=source,
            dry_run=dry_run,
            force=force,
            archive=archive,
            tag=tag,
        )

    def import_lore(
        self,
        profile: str,
        *,
        root: str | None = None,
        dry_run: bool = False,
        force: bool = False,
        archive: bool = True,
        tag: bool = False,
    ):
        """Markdown 设定稿 → 底层记忆（world）文档。"""
        from .workflow import assets as assets_flow

        return assets_flow.import_lore(
            self.cfg,
            self.index,
            profile,
            root=root,
            dry_run=dry_run,
            force=force,
            archive=archive,
            tag=tag,
        )

    # ------------------------------------------------------------- 版本控制
    def git_state(self) -> dict[str, Any]:
        from . import gitops

        return gitops.repo_state(self.cfg)

    def git_history(self, limit: int = 15, path: str | None = None) -> list[dict[str, Any]]:
        from . import gitops

        return gitops.history(self.cfg, limit=limit, path=path)

    def git_tags(self, pattern: str = "") -> list[dict[str, str]]:
        from . import gitops

        return gitops.list_tags(self.cfg, pattern=pattern)

    def snapshot(self, name: str, message: str = "") -> dict[str, Any]:
        from . import gitops

        return gitops.create_tag(self.cfg, name, message)

    def audit_chapter(self, chapter_id: str) -> dict[str, Any]:
        outline = retrieval.outline_doc(self.cfg, self.index, chapter_id)
        if not outline:
            raise FileNotFoundError(f"找不到大纲：{chapter_id}")
        resolved = resolve_chapter(self.cfg, self.index, outline)
        issues: list[Issue] = []
        issues.extend(
            continuity.check_cast(
                self.cfg, resolved.cast_ids, resolved.states_by_character, self.index.exists
            )
        )
        for row in self.index.foreshadowing_all():
            meta = {
                "id": row["id"],
                "status": row["status"],
                "planted_in": row.get("planted_in"),
                "target_reveal_window": {
                    "start": row.get("target_start"),
                    "end": row.get("target_end"),
                },
            }
            issues.extend(foreshadowing.check_ledger_entry(meta, chapter_id))

        # 索引一致性
        for row in self.index.all_entities():
            path = self.cfg.abs(str(row.get("file_path") or ""))
            if not path.exists():
                issues.append(
                    Issue("MAJOR", "index", f"索引指向不存在的文件：{row.get('file_path')}", refs=[str(row["id"])])
                )
        # 事件参与者有效性
        for event in self.index.events_of_chapter(chapter_id):
            participants = yaml.safe_load(event.get("participants") or "[]")
            for participant in participants or []:
                if not self.index.exists(str(participant)):
                    issues.append(
                        Issue(
                            "MAJOR",
                            "event",
                            f"事件 {event['event_id']} 的参与者 {participant} 不存在",
                            refs=[event["event_id"], str(participant)],
                        )
                    )

        verdict = "BLOCKED" if any(i.severity == "BLOCKER" for i in issues) else (
            "NEEDS_ATTENTION" if issues else "CLEAN"
        )
        from .audit.reports import issues_table, write_report
        from .transaction import commit_artifacts

        body = "\n".join(
            [
                f"# 章节审计｜{chapter_id}",
                "",
                "## 结论",
                f"**{verdict}**",
                "",
                "## 检查范围",
                "- 出场人物状态与生死",
                "- 伏笔窗口与状态",
                "- 索引与文件一致性",
                "- 事件参与者引用",
                "",
                "## 问题清单",
                issues_table(issues),
                "",
                "## 伏笔账本",
                *[
                    f"- {row['id']}｜{row['status']}｜窗口 {row.get('target_start')}～{row.get('target_end')}"
                    for row in self.index.foreshadowing_all()
                ],
            ]
        )
        report = write_report(
            self.cfg,
            "continuity",
            f"AUDIT-{chapter_id}",
            report_type="Continuity",
            target=chapter_id,
            verdict=verdict,
            body=body,
            generated_by="orchestrator/audit",
            extra_meta={"issue_count": len(issues)},
        )
        commit_artifacts(self.cfg, f"audit report {chapter_id}")
        return {"verdict": verdict, "issues": [i.to_dict() for i in issues], "report": report}
    def rollback(self, op_id: str, force: bool = False) -> dict[str, Any]:
        result = rollback_txn(self.cfg, self.index, op_id, force=force)
        # 回滚内部会重建数据库文件，当前连接已失效
        self.index.close()
        self.index = ensure_index(self.cfg)
        return result

    def rebuild_index(self) -> dict[str, Any]:
        self.index.close()
        result = rebuild(self.cfg)
        self.index = ensure_index(self.cfg)
        # 索引页是索引库的投影，重建完顺手刷新，省得两边脱节
        result["index_md"] = self.write_entity_index()
        # 实体↔地图的挂链同样是投影：索引一变就跟着重算
        result["map_sync"] = self.sync_map()
        # 人物↔物品↔势力 的关系索引，和地图投影一样是派生页
        result["relation_sync"] = self.sync_relations()
        return result

    def sync_map(self) -> dict[str, Any]:
        from .workflow import map_sync

        return map_sync.run(self.cfg)

    def sync_relations(self) -> dict[str, Any]:
        from .workflow import relation_sync

        return relation_sync.run(self.cfg)

    def integrity(self) -> Any:
        """结构校验：单一事实源 + 可维护性。只读，不改档案。"""
        from .validators import integrity

        return integrity.run(self.cfg)

    def write_entity_index(self) -> dict[str, Any]:
        from .workflow.index_md import write_entity_index

        return write_entity_index(self.cfg, self.index)

    # ------------------------------------------------------------- 新建实体
    def new_entity(
        self,
        doc_type: str,
        name: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if doc_type not in TYPE_LAYOUT:
            raise ValueError(f"不支持的类型：{doc_type}（可选：{', '.join(sorted(TYPE_LAYOUT))}）")
        directory, template_name = TYPE_LAYOUT[doc_type]
        extra = extra or {}

        if doc_type in ("event", "scene_beat", "character_state"):
            parent = str(
                extra.get("chapter_id")
                or extra.get("character_id")
                or extra.get("parent")
                or ""
            )
            if not parent:
                raise ValueError(f"{doc_type} 需要父级 ID，例如 --set chapter_id=CH-0002 / --set character_id=CHAR-0001")
            new_id = id_service.next_child_id(self.cfg, self.index, doc_type, parent)
        elif doc_type == "chapter_outline":
            new_id = id_service.next_id(self.cfg, self.index, "chapter_outline")
        else:
            new_id = id_service.next_id(self.cfg, self.index, doc_type)

        template_path = self.cfg.dir("templates") / template_name
        if not template_path.exists():
            raise FileNotFoundError(f"缺少模板：{template_path}")
        text = template_path.read_text(encoding="utf-8")

        replacements: dict[str, Any] = {
            "id": new_id,
            "name": name,
            "title": name,
            "date": date.today().isoformat(),
            "volume_id": extra.get("volume_id", "VOL-0001"),
            "chapter_id": extra.get("chapter_id", ""),
            "character_id": extra.get("character_id", ""),
            "character_serial": new_id.replace("STATE-CHAR-", ""),
            "sequence": extra.get("sequence", 1),
            "planted_in": extra.get("planted_in", ""),
            "window_start": extra.get("window_start", ""),
            "window_end": extra.get("window_end", ""),
            "source_id": extra.get("source_id", ""),
            "target_id": extra.get("target_id", ""),
            "relation_type": extra.get("relation_type", "unknown"),
            "valid_from": extra.get("valid_from", ""),
            "source_event": extra.get("source_event", ""),
        }
        for key, value in replacements.items():
            text = text.replace("{{" + key + "}}", str(value))

        meta, body = vault.split_frontmatter(text)
        meta.update({k: v for k, v in extra.items() if k not in replacements})
        meta["id"] = new_id

        safe = SAFE_NAME_RE.sub("_", name).strip("_") or new_id
        filename = f"{new_id}_{safe}.md" if doc_type != "chapter_outline" else f"{new_id}.md"
        rel = f"{directory}/{filename}"

        tx = Transaction(
            self.cfg,
            self.index,
            actor="author",
            agent="author",
            operation="create_entity",
            target=new_id,
        )
        tx.stage_doc(rel, meta, body)
        commit = tx.commit(f"create {doc_type} {new_id}")
        return {"id": new_id, "path": rel, "operation_id": commit["operation_id"]}

    # ------------------------------------------------------------- 体检
    def doctor(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        cfg = self.cfg

        def add(name: str, ok: bool, detail: str) -> None:
            checks.append({"check": name, "ok": ok, "detail": detail})

        add("Vault 根目录", (cfg.vault / "00_System").is_dir(), str(cfg.vault))
        for key in ("core_memory", "hot_memory", "outline", "manuscripts"):
            add(f"目录 {key}", cfg.dir(key).is_dir(), str(cfg.dir(key)))
        add("SQLite 索引", cfg.index_db.exists(), str(cfg.index_db))
        prompt_count = len(list((cfg.vault / "00_System" / "Prompts").glob("*.md")))
        add(f"提示词 {prompt_count} 份", prompt_count >= 5, "00_System/Prompts")
        add("模板数量", len(list(cfg.dir("templates").glob("*.md"))) >= 10, "11_Templates")

        from .agents.base import LLMClient

        llm = LLMClient(cfg)
        # 作者已裁定正文由会话直接成稿，`llm.enabled=false` 时没有 key 是**预期状态**，
        # 不该让体检一直挂红。只有显式开启却没配 key 才算问题。
        llm_enabled = bool((cfg.llm or {}).get("enabled", True))
        add(
            "LLM 配置",
            llm.available or not llm_enabled,
            f"{llm.model} @ {llm.base_url}"
            if llm.available
            else ("已关闭外部 LLM（正文由会话直接成稿）" if not llm_enabled else "未配置 API Key"),
        )

        import subprocess

        git = subprocess.run(
            ["git", "-C", str(cfg.vault), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
        add("Git 仓库", git.returncode == 0, (git.stdout or git.stderr).strip()[:80])

        stats = self.index.stats()
        add("已索引实体", stats["entities"] > 0, str(stats))

        # 结构校验只取「结论」，明细留给 novel integrity。
        # 放在最后：前面任何一项失败都不影响这里跑完。
        from .validators import integrity

        report = integrity.run(cfg)
        for check in integrity.checks_for_doctor(report):
            add(check["check"], bool(check["ok"]), str(check["detail"]))
        return {"checks": checks, "stats": stats, "all_ok": all(c["ok"] for c in checks)}
