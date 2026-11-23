"""项目内路径解析。所有写入/读取都必须经由这里，越界在入口就被拒。

目录名以 `10_项目配置/project.yaml` 为准（V1.0 §8），读不到才退回 §4.2 默认值。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import config as cfg_mod
from .errors import SecurityError, UsageError

# V1.0 §8 的 16 类知识库分类（+ 时间线，共 17 个目录）。
# 分类依据是 frontmatter 的 `type`，不是原目录——旧结构里 World/ 装了 336 份物品、
# Systems/ 装了 171 份技能，按目录搬只会把错配原样保留。
CANON_KINDS = (
    "世界与宇宙",
    "地理与地点",
    "种族与族群",
    "人物与角色",
    "生物与生态",
    "势力与组织",
    "物品与装备",
    "技能与能力",
    "力量与成长",
    "事件与历史",
    "规则与机制",
    "资源与经济",
    "技术与文明",
    "文化与社会",
    "特殊设定",
    "关系与状态",
    "时间线",
)

# V1.0 §8 目录名。project.yaml 中同名键可覆盖。
DEFAULT_DIRS = {
    "project_meta_root": "10_项目配置",
    "canon_root": "02_世界知识库",
    "planning_root": "03_故事规划",
    "manuscript_root": "04_正文",
    "assets_root": "06_素材与研究",
    # V1.0 取消了独立的 Ledgers 目录：事件与关系进知识库、伏笔进规划、
    # 骰子进素材。这里指向知识库根，让仍按 ledger_root 取路径的老代码继续工作。
    "ledger_root": "02_世界知识库",
    "memory_root": "05_记忆与连续性",
    "staging_root": "_staging",
    "review_root": "07_审核与报告",
    "archive_root": "90_Archive",
    # V1.0 §8 的 01_灵感与创意。WF-01 的落点，与规划区分开：
    # 灵感还没有「打不打算写」的判断，规划已经有。
    "idea_root": "01_灵感与创意",
}

MANUSCRIPT_STAGES = ("初稿", "修订稿", "正式定稿")

# frontmatter 的 `type` → 知识库分类目录。
#
# 这张表只服务「新建实体时放哪个目录」，不服务「已有档案算哪一类」——
# 后者读它自己的目录，改分类靠搬文件（那是人的决定，不是代码的）。
#
# V1.0 §5.1 给的是「类型域」，一域底下有多个具体类型（势力与组织里有国家、
# 宗门、商会），所以这里一个域收多个别名。别名不设上限，但**必须落在十七类里**，
# 落不进去的说明该走 `04_Story_Assets`（素材）而不是知识库。
TYPE_TO_CANON_KIND = {
    "world": "世界与宇宙",
    "cosmos": "世界与宇宙",
    "plane": "世界与宇宙",
    "location": "地理与地点",
    "place": "地理与地点",
    "race": "种族与族群",
    "species": "种族与族群",
    "clan": "种族与族群",
    "character": "人物与角色",
    "person": "人物与角色",
    "creature": "生物与生态",
    "beast": "生物与生态",
    "faction": "势力与组织",
    "organization": "势力与组织",
    "org": "势力与组织",
    "item": "物品与装备",
    "equipment": "物品与装备",
    "artifact": "物品与装备",
    "skill": "技能与能力",
    "ability": "技能与能力",
    "spell": "技能与能力",
    "power": "力量与成长",
    "realm": "力量与成长",
    "level": "力量与成长",
    "career": "力量与成长",
    "destiny": "力量与成长",
    "event": "事件与历史",
    "history": "事件与历史",
    "rule": "规则与机制",
    "mechanic": "规则与机制",
    "law": "规则与机制",
    "resource": "资源与经济",
    "economy": "资源与经济",
    "currency": "资源与经济",
    "technology": "技术与文明",
    "civilization": "技术与文明",
    "culture": "文化与社会",
    "society": "文化与社会",
    "religion": "文化与社会",
    "special": "特殊设定",
    "anomaly": "特殊设定",
    "relation": "关系与状态",
    "relationship": "关系与状态",
    "state": "关系与状态",
    "timeline": "时间线",
}

# 不进知识库的类型：素材卡与模板有自己的目录。
NON_CANON_TYPES = {
    "story_asset": "assets_root",
    "template": "assets_root",
}

# ID 查重的三级别。**不是三种目录**——是「同一 ID 出现在两处时该怎么判」：
# formal 里撞两次 = 真撞号；pending（待发布/审阅）里的副本是同一对象在途，
# 不算撞号；牵连 archive 只告警，因为历史包袱不该让今天发不出号。
ID_AREA_KEYS = {
    "formal": None,  # PUBLISH_TARGETS，见 area_roots()
    "pending": ("staging_root", "review_root"),
    "archive": ("archive_root",),
}

# publish 允许写入的正式区。staging / planning 草稿不在其列。
PUBLISH_TARGETS = (
    "canon_root",
    "manuscript_root",
    "ledger_root",
    "memory_root",
    "planning_root",
    "assets_root",
)


@dataclass
class ProjectPaths:
    vault: Path
    root: Path
    project_id: str
    meta: dict

    # ---- 构造 ----
    @classmethod
    def resolve(cls, cfg: cfg_mod.Config, project_id: str | None = None) -> "ProjectPaths":
        vault = cfg.require_vault()
        pid = cfg.resolve_project_id(project_id)
        root = cfg.project_root(pid)
        if not root.is_dir():
            raise UsageError(f"项目目录不存在：{root}")
        cfg_mod.ensure_inside(root, vault)
        meta = cfg_mod.read_yaml(root / "10_项目配置" / "project.yaml")
        return cls(vault=vault, root=root, project_id=pid, meta=meta)

    # ---- 目录名 ----
    def dir_name(self, key: str) -> str:
        """返回目录的 Vault 内相对路径，供记录与展示。"""
        if key not in DEFAULT_DIRS:
            raise UsageError(f"未知目录键：{key}")
        return self._resolve_dir(key).relative_to(self.vault.resolve()).as_posix()

    def _resolve_dir(self, key: str) -> Path:
        """解析目录。兼容两种写法：

        - Vault 相对（V4.0 §4.3 的要求，如 `10_Projects/novel-001_x/01_Canon`）
        - 项目相对（如 `01_Canon`，不随目录改名失效）

        判据不看字面前缀：解析结果落在项目根内就用它，否则按项目相对解释。
        """
        if key not in DEFAULT_DIRS:
            raise UsageError(f"未知目录键：{key}")
        raw = str(self.meta.get(key) or DEFAULT_DIRS[key]).strip()
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts:
            raise SecurityError(f"project.yaml 中的 {key} 必须是安全相对路径：{raw}")

        vault = self.vault.resolve()
        root = self.root.resolve()
        from_vault = (vault / path).resolve()
        from_project = (root / path).resolve()
        for candidate in (from_vault, from_project):
            if candidate == root or root in candidate.parents:
                cfg_mod.ensure_inside(candidate, vault)
                return candidate
        raise SecurityError(f"project.yaml 中的 {key} 指向项目之外：{raw}")

    # ---- 路径 ----
    def at(self, *parts: str) -> Path:
        """拼项目内路径并校验。接受 `at('a/b.md')` 或 `at('a', 'b.md')`。"""
        rel = Path(*parts)
        if rel.is_absolute():
            raise SecurityError(f"拒绝绝对路径：{rel}")
        if ".." in rel.parts:
            raise SecurityError(f"拒绝含 '..' 的路径：{rel}")
        target = self.root / rel
        cfg_mod.ensure_inside(target, self.vault)
        return target

    def in_dir(self, key: str, *parts: str) -> Path:
        base = self._resolve_dir(key)
        if parts:
            rel = Path(*parts)
            if rel.is_absolute() or ".." in rel.parts:
                raise SecurityError(f"拒绝越界路径：{rel}")
            base = base / rel
        cfg_mod.ensure_inside(base, self.vault)
        cfg_mod.ensure_inside(base, self.root)
        return base

    def rel(self, path: Path) -> str:
        """Vault 内相对路径（POSIX 风格），用于记录与比对。"""
        return path.resolve().relative_to(self.vault.resolve()).as_posix()

    def rel_to_project(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    # ---- 常用目录 ----
    @property
    def project_meta(self) -> Path:
        return self.in_dir("project_meta_root")

    @property
    def canon(self) -> Path:
        return self.in_dir("canon_root")

    @property
    def planning(self) -> Path:
        return self.in_dir("planning_root")

    @property
    def manuscripts(self) -> Path:
        return self.in_dir("manuscript_root")

    @property
    def assets(self) -> Path:
        return self.in_dir("assets_root")

    @property
    def ledger(self) -> Path:
        return self.in_dir("ledger_root")

    @property
    def memory(self) -> Path:
        return self.in_dir("memory_root")

    @property
    def staging(self) -> Path:
        return self.in_dir("staging_root")

    @property
    def reviews(self) -> Path:
        return self.in_dir("review_root")

    @property
    def archive(self) -> Path:
        return self.in_dir("archive_root")

    def canon_dir(self, kind: str) -> Path:
        if kind not in CANON_KINDS:
            raise UsageError(f"未知 Canon 类别：{kind}（可选 {', '.join(CANON_KINDS)}）")
        return self.canon / kind

    def manuscript_dir(self, stage: str) -> Path:
        if stage not in MANUSCRIPT_STAGES:
            raise UsageError(f"未知正文阶段：{stage}（可选 {', '.join(MANUSCRIPT_STAGES)}）")
        return self.manuscripts / stage

    def dir_for_type(self, entity_type: str) -> Path:
        """`type` → 该建档的目录。**不猜**：认不出就报错并列出可用类型。

        报错而不是兜底到 `特殊设定`，理由是兜底会把「类型填错了」变成
        「文件去了奇怪的地方」，而后者要到很久以后才被发现。
        """
        key = (entity_type or "").strip().lower()
        if not key:
            raise UsageError("未指定实体类型（--type）")
        if key in NON_CANON_TYPES:
            return self.in_dir(NON_CANON_TYPES[key])
        kind = TYPE_TO_CANON_KIND.get(key)
        if kind is None:
            raise UsageError(
                f"未知实体类型 {entity_type!r}。"
                f"知识库可用的类型：{', '.join(sorted(TYPE_TO_CANON_KIND))}；"
                f"素材类用：{', '.join(sorted(NON_CANON_TYPES))}"
            )
        return self.canon_dir(kind)

    def control_roots(self) -> list[Path]:
        """受控区根目录：Canon、定稿、账本、记忆、规划、素材。"""
        return [self.in_dir(key) for key in PUBLISH_TARGETS]

    def area_roots(self) -> dict[str, list[Path]]:
        """按 ID 查重的三级别返回根目录。目录名一律从布局配置取，不写死。

        顺序有意义：同一路径若被两处声明（例如有人把 staging 指到 canon），
        先声明者胜，所以 formal 排第一。
        """
        roots: dict[str, list[Path]] = {"formal": self.control_roots()}
        for area, keys in ID_AREA_KEYS.items():
            if keys is None:
                continue
            roots[area] = [self.in_dir(key) for key in keys]
        return roots

    def is_control_path(self, target: Path) -> bool:
        """目标是否落在允许 publish 的正式区。"""
        target = target.resolve()
        for root in self.control_roots():
            if target == root.resolve() or root.resolve() in target.parents:
                return True
        return False

    def define_area(self, target: Path) -> str:
        """目标属于哪个正式区，用于报告与 staging 分栏。"""
        target = target.resolve()
        for key in PUBLISH_TARGETS:
            root = self.in_dir(key)
            if target == root.resolve() or root.resolve() in target.parents:
                return self.dir_name(key)
        return ""
