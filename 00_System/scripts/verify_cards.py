#!/usr/bin/env python3
"""场景卡体检：字段完整性 / B 行标记 / 引文核实 / 跨叙述拼接（三级）。

为什么单独一个脚本，不并进 `novel integrity`：
`integrity` 的问题分布是给全库看的，11 张卡的引文噪声不该污染整仓体检报告。

## 检查项

1. **字段完整性**：结构层与驱动层必须写全；极性取值合法；极性相同不许标 `full`。
2. **B 行标记**：`dilemma_b` 永远有值；没有真两难时明写「本场无真两难」；
   返工期标记必须是 `（待重判：…）` 这一固定字面量，两头（触发／清法）都写死，
   且不许盖在原有判定上。
3. **引文核实**：卡片每个「」引文，必须由**正文里按顺序出现的片段**拼出来。
   - 叙述性引文不在「」内（如「箱子一沉」），先按整串直接比对；
   - 分句引文（`「我在想，」他说，「我该按……」`）允许首段中途起、尾段中途止，
     中间段必须是完整片段。

## 跨叙述拼接：为什么是三级，不是两级

两个匹配片段之间被跳过的正文，若其实换了人说话，卡片就等于「把两次发言并成一次」，
造出一句没人说过的话。判据分三级：

| 情形                                   | 级别  | 退出码 |
|----------------------------------------|-------|--------|
| 间隙含 `。！？` 或换行（跨段）          | BLOCK | 1      |
| 间隙含 `问`／`答`                       | BLOCK | 1      |
| 间隙含人物实体名且无 `说`／`道`，不在白名单 | WARN  | 0      |
| 白名单条目本次未命中任何实际间隙（过期） | WARN  | 0      |

`问`／`答` 是**换口信号**（问一句必然换口），`说`／`道` 是**归因**（同一人接着说完）。
「人名出现在间隙里」本身分不开这两种情形——它既可能是同一人在做动作，也可能是换了人开口，
所以只降 WARN、不阻断，交人一眼扫。

## 射程：WARN 只看「被卡片引文跨过的间隙」

**本检查的 WARN 射程 = 被卡片引文跨过的间隙，不是正文里的全部间隙。**
正文不存在「拼接」这个动作，把两次发言并成一次**只可能发生在卡片里**；按全库扫会把正常散文
判红（`CH-0001:29` 那处含句号的中景描写），红一次以后所有人就学会忽略红字。
代价写在下面这一节里，不要读漏。

## 白名单是预授权，不是「已知噪声吸收」

**白名单是预授权**：它登记的是「这处间隙若日后被卡片引文跨过，可以直接放行」，
**不是**「这里曾经报过警、现在被压下去了」。今天全库 `WARN = 0` 的原因是**射程**，
不是白名单在做功——把白名单整个清空，全库仍然是 `WARN = 0`。
这条反事实跑是常设验收（见文末）：**哪天两条跑出来的数不一样，说明射程被改过、
白名单成了拐杖**，此时要么恢复射程，要么把新增的 WARN 逐条审掉。

同理，`--accept` 默认拒收不含人物名的间隙：那种条目 WARN 永远不会响、又确实存在于正文、
因而永远不会过期——**既不能触发也不能过期，任何测试都验不到它**，是不可证伪的配置，
和「没查冒充没问题」是同一个病，只是发生在配置里。确需预授权要显式加 `--pre-authorize`。

## 边界写在这里，不只在聊天记录里

- **「名＋动作」分句（如 `石季崇的手指按在图上，`）若被判红，属规则边界，不是正文错。
  禁止据此修改 `08_Manuscripts`。** 这类间隙是同一人一口气说完、中间插个动作，是正当写法。
- **已知误判**：同一人连续发问（`「几点到？」他问，「……」`）会被 `问` 判成跨叙述拼接，
  遇此情形在**卡片的引文里拆成两段**，不要改正文。
- 白名单的键是「章节＋间隙原文」（逐字），**不是行号**：行号会随正文字数修订漂移，
  一漂白名单就失效、WARN 复活，而没人记得当初为什么放行；用原文做键，只有原文真的
  被改动时才失效，那一刻正是该重审的时刻。

## 备注区为什么不查

`08_Manuscripts` 文件尾「写作备注」区的引文引的是**细纲、章纲、约束、设定名**，
唯独不引正文（例：`与细纲「消耗灵力与时间」一致`、`「不得直接长谈」`、`「三道炉」`）。
拿它们去正文核对必然查不到，而那不是错。引文核实只针对场景卡的字段。
（曾把 6 条这样的引文报成「等主笔改」——那 6 处全是对的，指向本身才是错的。）

**但「不核实」里的「并引正文台词」不能一起放过**：备注里若把两句正文台词用 `、`／`，`／`／`
缝成一句（真出现过：「先别报、人先留着」），那是造了一句没人说过的话，跟「引细纲」不是一类。
宽判据在这里会误伤全部细纲引用，所以只留一条窄的（见 `note_merge_warnings`）：
整串在正文查不到、但按 `」「`／`、`／`，`／`／` 切开后每一段都查得到 → WARN。
切不开、或有一段查不到 → 不算命中。**只 WARN，不阻断，也绝不要求改正文**——
正文一个字都没错，要拆的是备注。

## 用法

    verify_cards.py                      # 全库体检
    verify_cards.py --quiet              # 只打汇总与问题
    verify_cards.py --list-gaps          # 列出所有同段引语间隙（带序号）
    verify_cards.py --accept 1,2,3,4 --reason "同一人分句插入，动作段"
                                         # 按序号把间隙回填进白名单，人不碰字符串
    verify_cards.py --drop 4             # 按同一个序号撤掉一条（判断，不是转录）
    verify_cards.py --accept 7 --pre-authorize --reason "预授权：正文日后可能具名"
                                         # 不含人物名的间隙须显式预授权，默认拒收

`--accept` / `--drop` 的设计意图：**人只判断「放不放行」，不转录「放行什么」**。
判断是人的长处，转录是机器的长处；手抄一个错字，那条白名单就永远命中不了，
第一次跑就被判过期，然后有人会去正文里找一个根本不存在的名字。

## 常设验收（改任何判据或射程后，四条都要重跑）

    python 00_System/scripts/verify_cards.py --quiet
    python 00_System/scripts/verify_cards.py --quiet --whitelist /tmp/none.yaml
    python 00_System/scripts/verify_cards.py --help | grep -E "射程|预授权"
    python 00_System/scripts/verify_cards.py --accept <无名间隙序号> --reason 测试

第 2 条（空白名单）与第 1 条**结果必须一样**——一样才说明白名单今天没在做事；
不一样就说明射程被改过、白名单成了拐杖。第 4 条必须被拒绝。
**一条能证明「某机制当前是空转」的测试，比一条证明「它在工作」的测试更有用**，
因为前者防的是自欺。

## 总纲：豁免只说明「这里不检查」，不说明「这里没问题」

**「没查」永远不等于「合规」。** 所有为了让检查跑得下去而设的豁免，都只说明这一处不被检查：

- 术语豁免不证明陈述为真（「铁路」是确有的术语，用到它的那句话是错的）
- 备注区不核实，不代表备注准确（「先别报、人先留着」那处并引，是写作者自己发现并改的，
  不是规则兜住的）
- 白名单放行不代表那处引文没有争议（它只是「日后若被跨，不必再批一次」）
- `env` 跑不动、脚本报错跳过、条目没验到——一律不等于通过

豁免是**射程的边界**，不是**质量的结论**。改判据时最容易犯的错，就是把边界读成结论。

退出码：0 全过（含 WARN），1 有 BLOCK，2 环境不成立（找不到正文／场景目录）。
正文只读：本脚本只 `read_text` 正文，唯一的写操作是白名单文件。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

# 只用标准库，不 import yaml。
# 理由：PATH 上的 `python3` 是 WorkBuddy 自带的 3.13（无 pyyaml），
# 只有项目 `.venv` 里装了。校验脚本要是非得挑解释器才能跑，就等于没上线——
# 人换个窗口跑一次就报 ModuleNotFoundError，然后没人再跑第二次。
# 本仓库的 frontmatter 与白名单都只有 `key: value` 一种结构，手写解析够用。

ROOT = Path(__file__).resolve().parents[2]
SCENES = ROOT / "04_Outline/Scenes"
MS = ROOT / "08_Manuscripts"
CHARS = ROOT / "01_Core_Memory/Characters"
WHITELIST = ROOT / "00_System/Config/quote_gap_whitelist.yaml"

STRUCT = ("主控价值", "开场极性", "收场极性", "价值转变", "转折点落点")
DRIVE = ("期望", "结果", "缺口（gap）", "危机两难 A", "危机两难 B")
SEAM = ("地点", "时间", "承接上文", "与下一场景的连接")
ENUM_SHIFT = {"full", "weak", "none", "unjudged"}
ENUM_POL = {"+", "−", "-", "±"}
MARK = "本场无真两难"
PENDING = "（待重判："

SENT_END = "。！？"
TURN_WORDS = "问答"      # 换口信号：问一句必然换口
ATTR_WORDS = "说道"      # 归因：同一人接着说完

QUOTE = re.compile(r"「([^」]{2,80})」")
FRAG = re.compile(r"「([^」]*)」")
BODY = re.compile(r"^##[ \t]*正文[ \t]*$(.*?)(?=^##[ \t]|\Z)", re.S | re.M)
NOTE = re.compile(r"^##[ \t]*写作备注[^\n]*$(.*?)(?=^##[ \t]|\Z)", re.S | re.M)
FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)

# 备注区「两句并成一句」的切分符。`、` 与 `，` 必须有——
# 真出现过的那一例正是「先别报、人先留着」，用顿号并的，只按 `」「` 切会漏掉。
NOTE_SEP = re.compile(r"」「|、|，|／")

BLOCK, WARN = "BLOCK", "WARN"

WL_HEADER = """\
# 同段引语间隙白名单。
# 键 = 章节 + 间隙原文（逐字）。不用行号：行号会随正文字数修订漂移，
# 一漂白名单就失效、WARN 立刻复活，而没人记得当初为什么放行。
# 用原文做键：只有原文真的被改动时才失效，那一刻正是该重审的时刻。
# 本文件由 `verify_cards.py --accept` 生成，**不要手抄**——
# 手抄一个错字，那条永远命中不了，第一次跑就被判「条目过期」，
# 然后有人会去正文里找一个根本不存在的名字。
"""


# ---------------------------------------------------------------- 极简 YAML

def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        v = v[1:-1]
        if v and v[0] == "'":
            v = v.replace("''", "'")
    return v


def parse_frontmatter(text: str) -> dict:
    """解析 frontmatter 的顶层标量／列表。只认本仓库用到的两种写法：

        key: value
        key: [a, b]
        key:
        - item
    """
    out: dict = {}
    cur = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("- ") and cur:
            if not isinstance(out.get(cur), list):
                out[cur] = []
            out[cur].append(_unquote(line[2:]))
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v == "":
            cur = k
            out[k] = []
        else:
            cur = None
            if v.startswith("[") and v.endswith("]"):
                inner = v[1:-1].strip()
                out[k] = [_unquote(x) for x in inner.split(",")] if inner else []
            else:
                out[k] = _unquote(v)
    return out


def parse_whitelist(text: str) -> list[dict]:
    """白名单条目：`- chapter: X` 起手，其余 `key: value` 归到当前条目。"""
    entries: list[dict] = []
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("- chapter:"):
            cur = {"chapter": _unquote(s.split(":", 1)[1])}
            entries.append(cur)
        elif cur is not None and ":" in s:
            k, v = s.split(":", 1)
            cur[k.strip()] = _unquote(v)
    return entries


# ---------------------------------------------------------------- 源数据

def load_exempt() -> str:
    """栏目名／术语的豁免依据：必须在模板、编辑器规则或底层记忆里**有定义**。

    「读着像术语」不算依据。豁免范围到 `01_Core_Memory/**` 为止——
    实体档案名（如 `热轮炉原型机`＝ITEM-0334）也算有定义的名字。
    **注意**：豁免只证明「这个词是有定义的名字」，不证明「用到它的那句话是对的」。
    """
    parts = []
    for rel in ("11_Templates/Scene_Beat.md", "00_System/Prompts/story_editor.md"):
        p = ROOT / rel
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    for p in (ROOT / "01_Core_Memory").rglob("*.md"):
        parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def load_names() -> list[str]:
    """人物实体名：`name` + `aliases`（`公主` 这类代称也要认），按长度降序。

    只取 `01_Core_Memory/Characters/**` 的 frontmatter，不用全文匹配——
    全文匹配会把档案正文里提到的地名、器物名一并当成人物。
    """
    out: set[str] = set()
    for p in sorted(CHARS.rglob("*.md")):
        m = FM.match(p.read_text(encoding="utf-8"))
        if not m:
            continue
        fm = parse_frontmatter(m.group(1))
        for key in ("name", "title"):
            v = fm.get(key)
            if isinstance(v, str) and v.strip():
                out.add(v.strip())
        al = fm.get("aliases") or []
        if isinstance(al, str):
            al = [al]
        for a in al:
            if isinstance(a, str) and a.strip():
                out.add(a.strip())
    return sorted((n for n in out if len(n) >= 2), key=len, reverse=True)


def find_name(gap: str, names: list[str]) -> str | None:
    for n in names:
        if n in gap:
            return n
    return None


def load_ms(ms_dir: Path):
    """返回：正文全文 / 正文片段（内容, 绝对行号, 起, 止） / 正文段起始行偏移 / 写作备注区"""
    bodies, frags, heads, notes = {}, {}, {}, {}
    for p in sorted(ms_dir.rglob("CH-*.md")):
        text = p.read_text(encoding="utf-8")
        m = BODY.search(text)
        body = m.group(1) if m else ""
        head = text[: m.start()].count("\n") if m else 0  # 片段行号要报文件绝对行号
        bodies[p.stem] = body
        heads[p.stem] = head
        frags[p.stem] = [
            (g.group(1), head + body[: g.start()].count("\n") + 1, g.start(), g.end())
            for g in FRAG.finditer(body)
        ]
        n = NOTE.search(text)
        notes[p.stem] = n.group(1) if n else ""
    return bodies, frags, heads, notes


# ---------------------------------------------------------------- 间隙

def scan_gaps(bodies, frag_map) -> list[dict]:
    """所有「同段引语间隙」：同一段内相邻两个「」片段之间的正文。

    跨段（含换行）不算同段，不列。同一章节里完全相同的间隙只列一次（键去重）。
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for cid in sorted(bodies):
        body = bodies[cid]
        F = frag_map.get(cid, [])
        for a, b in zip(F, F[1:]):
            gap = body[a[3] : b[2]]
            if not gap.strip() or "\n" in gap:
                continue
            if not re.search(r"[\u4e00-\u9fff]", gap):
                continue
            key = (cid, gap.strip())
            if key in seen:
                continue
            seen.add(key)
            ln = a[1] + body[a[2] : a[3]].count("\n")
            out.append({"chapter": cid, "gap": gap.strip(), "line": ln})
    return out


def classify_gap(gap: str, chapter: str, names, wl_keys) -> tuple[str | None, str]:
    """返回 (级别, 说明)。级别 None 表示通过。"""
    g = gap.strip()
    if not g:
        return None, ""
    if any(c in g for c in SENT_END) or "\n" in gap:
        return BLOCK, "间隙含句末标点或跨段"
    if any(c in g for c in TURN_WORDS):
        return BLOCK, "间隙含「问」/「答」——换口信号"
    nm = find_name(g, names)
    if nm and not any(v in g for v in ATTR_WORDS):
        if (chapter, g) in wl_keys:
            return None, ""
        return WARN, f"含人物名「{nm}」但无归因动词"
    return None, ""


# ---------------------------------------------------------------- 白名单

def load_wl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return parse_whitelist(path.read_text(encoding="utf-8"))


def save_wl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [WL_HEADER, "entries:"]
    for e in entries:
        gap = str(e.get("gap", "")).replace("'", "''")
        reason = str(e.get("reason", "")).replace("'", "''")
        lines.append(f"  - chapter: {e.get('chapter', '')}")
        lines.append(f"    gap: '{gap}'")
        lines.append(f"    reason: '{reason}'")
        lines.append(f"    added_at: '{e.get('added_at') or date.today().isoformat()}'")
        if e.get("pre_authorized"):
            # 不含人物名的间隙：WARN 不会响、又不会过期，任何测试都验不到。
            # 显式标出来，让下一个人一眼看见「这条没人验得了」，不要当成已核实的放行。
            lines.append("    pre_authorized: true")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 引文匹配

def _seek(q, pos, start, first, F, depth=0):
    if pos >= len(q):
        return [], None
    if depth > 12:
        return [], pos
    for j in range(start, len(F)):
        frag, ln, s, e = F[j]
        if not frag:
            continue
        if first:
            for o in range(len(frag)):
                seg = frag[o:]
                if not q.startswith(seg, pos):
                    continue
                if pos + len(seg) == len(q):
                    return [(frag, ln, s + o, e)], None
                sub, fail = _seek(q, pos + len(seg), j + 1, False, F, depth + 1)
                if fail is None:
                    return [(frag, ln, s + o, e)] + sub, None
        else:
            if q.startswith(frag, pos):
                if pos + len(frag) == len(q):
                    return [(frag, ln, s, e)], None
                sub, fail = _seek(q, pos + len(frag), j + 1, False, F, depth + 1)
                if fail is None:
                    return [(frag, ln, s, e)] + sub, None
            elif frag.startswith(q[pos:]):
                return [(frag, ln, s, s + len(q) - pos)], None
    return [], pos


def match_quote(q: str, body: str, F) -> tuple[list, str | None, tuple | None]:
    """返回 (已匹配片段, 未匹配残文, 最像的候选片段)。"""
    if q in body:
        return [], None, None
    used, fail = _seek(q, 0, 0, True, F)
    if fail is None:
        return used, None, None
    rest = q[fail:]
    best = None
    for frag, ln, _s, _e in F:
        k = 0
        while k < min(len(frag), len(rest)) and frag[k] == rest[k]:
            k += 1
        if k and (best is None or k > best[0]):
            best = (k, frag, ln)
    return used, rest, best


# ---------------------------------------------------------------- 主体

def note_merge_warnings(notes, bodies) -> list[tuple[str, str]]:
    """备注区「并引正文台词」的窄判据（只 WARN，不阻断）。

    只抓一种：整串在正文查不到，**但按 `」「`／`、`／`，`／`／` 切开后每一段都查得到**。
    切不开、或有一段查不到 → 不算命中——那是引细纲、引约束、引设定名，属正常。
    **这一条是防误报的全部依靠，不许放宽**：它是备注区不纳入全量核实之后，
    唯一还能抓到「把两句并成一句」的地方。
    """
    out: list[tuple[str, str]] = []
    for cid in sorted(notes):
        note = notes[cid]
        if not note:
            continue
        body = bodies.get(cid, "")
        for q in QUOTE.findall(note):
            if q in body:
                continue
            parts = [x.strip() for x in NOTE_SEP.split(q) if x.strip()]
            if len(parts) < 2:
                continue
            if not all(x in body for x in parts):
                continue
            split = "…".join(f"「{x}」" for x in parts)
            # 不许写「请修改正文」：正文一个字都没错，要改的是备注。
            out.append((WARN, f"{cid} 写作备注区引文疑似把两句并成一句：「{q}」\n"
                              f"       请拆成{split}（只改备注，正文不动）"))
    return out


def check_scenes(body_map, frag_map, heads, exempt, names, wl_keys, scene_dir):
    """返回 ([(级别, 文本)], 待重判卡片)。"""
    out: list[tuple[str, str]] = []
    pending: list[str] = []
    for path in sorted(scene_dir.glob("SCENE-*.md")):
        text = path.read_text(encoding="utf-8")
        f = {}
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("- **") and "**：" in s:
                f[s[4 : s.index("**：")]] = s.split("**：", 1)[1].strip()

        missing = [k for k in STRUCT + DRIVE if not f.get(k)]
        if missing:
            out.append((BLOCK, f"{path.stem} 缺行{missing}"))
        lack = [k for k in SEAM if not f.get(k)]
        if lack:
            out.append((BLOCK, f"{path.stem} 缺接缝{lack}"))
        if f.get("价值转变") not in ENUM_SHIFT:
            out.append((BLOCK, f"{path.stem} shift 越界 {f.get('价值转变')!r}"))
        if f.get("开场极性") not in ENUM_POL or f.get("收场极性") not in ENUM_POL:
            out.append((BLOCK, f"{path.stem} 极性取值非法"))
        if f.get("开场极性") == f.get("收场极性") and f.get("价值转变") == "full":
            out.append((BLOCK, f"{path.stem} 极性相同却标 full"))

        b = f.get("危机两难 B", "")
        if MARK not in b and "代价" not in b:
            out.append((BLOCK, f"{path.stem} B 行既不是两难也没明示「没有」"))
        if "待重判" in b or "待定" in b or "暂挂" in b:
            if PENDING not in b:
                out.append((BLOCK, f"{path.stem} 返工标记写法不合字面量（须以「（待重判：」起手）"))
            elif b.count(PENDING) > 1:
                out.append((BLOCK, f"{path.stem} 同一张卡挂了多个待重判标记"))
            else:
                inner = re.search(re.escape(PENDING) + r"(.+?)\）", b)
                if not inner or "；" not in inner.group(1):
                    out.append((BLOCK, f"{path.stem} 待重判括号里没写两头（触发；清除条件）"))
                if MARK not in b and "代价" not in b.split(PENDING)[0]:
                    out.append((BLOCK, f"{path.stem} 待重判盖在了原判定上"))
            pending.append(path.stem)

        cid = ""
        m = re.search(r"^chapter_id:\s*(\S+)", text, re.M)
        if m:
            cid = m.group(1)
        body = body_map.get(cid, "")
        F = frag_map.get(cid, [])
        for q in QUOTE.findall(text):
            used, rest, cand = match_quote(q, body, F)
            if rest is None:
                for a, bb in zip(used, used[1:]):
                    gap = body[a[3] : bb[2]]
                    lvl, why = classify_gap(gap, cid, names, wl_keys)
                    if lvl is None:
                        continue
                    g0 = a[3] + (len(gap) - len(gap.lstrip()))
                    ln = heads.get(cid, 0) + body[:g0].count("\n") + 1
                    if lvl == BLOCK:
                        out.append((
                            BLOCK,
                            f"{path.stem} 跨叙述拼接·{why}：已匹配「{a[0][:16]}」({cid}:{a[1]})"
                            f" → 间隙「{gap.strip()[:18]}」({cid}:{ln})，不是同一句话",
                        ))
                    else:
                        out.append((
                            WARN,
                            f"{path.stem} 跨叙述拼接·{why}（{cid}:{ln}）\n"
                            f"       间隙「{gap.strip()[:24]}」——若该场是两人对话，"
                            f"请人工确认这里是否换了说话人",
                        ))
                    break
                continue
            if q in exempt:
                continue
            n = len(used) + 1
            if cand:
                _k, frag, ln = cand
                out.append((
                    BLOCK,
                    f"{path.stem} 引文第 {n} 段不是完整片段：正文 {cid}:{ln}「{frag[:26]}」"
                    f"（首尾可截断，中间须完整；差 {len(rest)} 字）",
                ))
            else:
                out.append((BLOCK, f"{path.stem} 引文第 {n} 段无候选：{cid} 无此连写「{rest[:20]}」"))
    return out, pending


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="verify_cards.py",
        description="场景卡体检：字段完整性、B 行「待重判」标记、引文核实、跨叙述拼接（BLOCK/WARN）。",
        epilog=(
            "退出码：0 全过（含 WARN）；1 有 BLOCK；2 环境不成立。正文只读。\n"
            "\n"
            "射程：WARN 只看「被卡片引文跨过的间隙」，**不是**正文里的全部间隙。\n"
            "      正文不存在「拼接」这个动作，把两次发言并成一次只可能发生在卡片里；\n"
            "      按全库扫会把正常散文判红，红一次以后所有人就学会忽略红字。\n"
            "预授权：白名单是**预授权**（这处间隙日后若被跨，可直接放行），\n"
            "      不是「已知噪声吸收」。全库 WARN = 0 的原因是射程，不是白名单在做功——\n"
            "      把白名单清空，全库仍然是 WARN = 0。空白名单跑（--whitelist /tmp/none.yaml）\n"
            "      与正常跑结果必须一样；不一样说明射程被改过、白名单成了拐杖。\n"
            "边界：『名＋动作』分句（如 石季崇的手指按在图上，）若被判红，属规则边界，"
            "不是正文错，禁止据此修改 08_Manuscripts。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--quiet", action="store_true", help="只打印汇总与问题，不逐卡列 OK")
    ap.add_argument("--scene-dir", default=str(SCENES), help="场景卡目录（默认 04_Outline/Scenes）")
    ap.add_argument("--ms-dir", default=str(MS), help="正文目录（默认 08_Manuscripts）")
    ap.add_argument("--whitelist", default=str(WHITELIST), help="间隙白名单（默认 00_System/Config）")
    ap.add_argument("--list-gaps", action="store_true", help="列出所有同段引语间隙，带序号，供 --accept 使用")
    ap.add_argument("--accept", default="", help="按 --list-gaps 序号放行，逗号分隔，如 1,2,3,4")
    ap.add_argument("--drop", default="", help="按 --list-gaps 序号撤掉白名单里对应条目，逗号分隔")
    ap.add_argument("--pre-authorize", action="store_true",
                    help="允许 --accept 收不含人物名的间隙（这种条目不可证伪，默认拒收）")
    ap.add_argument("--reason", default="同一人分句插入，动作段", help="与 --accept 一起写入的理由")
    args = ap.parse_args()

    scene_dir, ms_dir = Path(args.scene_dir), Path(args.ms_dir)
    wl_path = Path(args.whitelist)
    if not scene_dir.is_dir():
        print(f"✗ 找不到场景卡目录：{scene_dir}", file=sys.stderr)
        return 2
    if not ms_dir.is_dir():
        print(f"✗ 找不到正文目录：{ms_dir}", file=sys.stderr)
        return 2
    bodies, frags, heads, notes = load_ms(ms_dir)
    if not bodies:
        print(f"✗ 正文目录里没有任何 CH-*.md：{ms_dir}", file=sys.stderr)
        return 2

    gaps = scan_gaps(bodies, frags)
    entries = load_wl(wl_path)
    if args.accept or args.drop:
        names = load_names()  # --accept 要靠它判断间隙里有没有人物名

    if args.list_gaps:
        for i, g in enumerate(gaps, 1):
            print(f"{i}: {g['chapter']}  :{g['line']}  {g['gap']}")
        marked = {(e.get("chapter"), str(e.get("gap", "")).strip()) for e in entries}
        print(f"\n共 {len(gaps)} 处同段引语间隙｜已在白名单 {len(marked)} 条")
        return 0

    if args.accept:
        try:
            idx = [int(x) for x in re.split(r"[,\s]+", args.accept.strip()) if x]
        except ValueError:
            print(f"✗ --accept 只接受序号：{args.accept}", file=sys.stderr)
            return 2
        bad = [i for i in idx if i < 1 or i > len(gaps)]
        if bad:
            print(f"✗ 序号越界：{bad}（有效范围 1..{len(gaps)}）", file=sys.stderr)
            return 2
        have = {(e.get("chapter"), str(e.get("gap", "")).strip()) for e in entries}
        today = date.today().isoformat()
        # 不含人物名的间隙：WARN 永远不响，又确实存在于正文、永远不过期——
        # 既不能触发也不能过期，任何测试都验不到它。默认拒收，别静默收进配置。
        blank = [i for i in idx if not find_name(gaps[i - 1]["gap"], names)]
        if blank and not args.pre_authorize:
            print(f"✗ 序号 {blank} 的间隙不含人物名：WARN 永远不会响，条目也永远不会过期，"
                  f"任何测试都验不到它。", file=sys.stderr)
            for i in blank:
                print(f"    {i}: {gaps[i - 1]['chapter']} /「{gaps[i - 1]['gap']}」", file=sys.stderr)
            print("  确需预授权请显式加 --pre-authorize（yaml 里会标 pre_authorized: true）。",
                  file=sys.stderr)
            return 2
        added = 0
        for i in idx:
            g = gaps[i - 1]  # 原文从扫描结果回填，人不碰字符串
            if (g["chapter"], g["gap"]) in have:
                continue
            e = {"chapter": g["chapter"], "gap": g["gap"],
                 "reason": args.reason, "added_at": today}
            if not find_name(g["gap"], names):
                e["pre_authorized"] = True
            entries.append(e)
            have.add((g["chapter"], g["gap"]))
            added += 1
        save_wl(wl_path, entries)
        print(f"已写入 {wl_path}：新增 {added} 条，共 {len(entries)} 条")
        for e in entries:
            print(f"  · {e['chapter']} /「{e['gap']}」— {e.get('reason')}")
        return 0

    if args.drop:
        try:
            idx = [int(x) for x in re.split(r"[,\s]+", args.drop.strip()) if x]
        except ValueError:
            print(f"✗ --drop 只接受序号：{args.drop}", file=sys.stderr)
            return 2
        bad = [i for i in idx if i < 1 or i > len(gaps)]
        if bad:
            print(f"✗ 序号越界：{bad}（有效范围 1..{len(gaps)}）", file=sys.stderr)
            return 2
        # 同样只按序号取原文，人不转录；撤掉的是「这一处间隙的放行决定」，不是抄错的字符串。
        drop = {(gaps[i - 1]["chapter"], gaps[i - 1]["gap"]) for i in idx}
        keep = [e for e in entries
                if (e.get("chapter"), str(e.get("gap", "")).strip()) not in drop]
        removed = len(entries) - len(keep)
        if removed:
            save_wl(wl_path, keep)
        print(f"已写入 {wl_path}：撤掉 {removed} 条，剩 {len(keep)} 条")
        for e in keep:
            print(f"  · {e['chapter']} /「{e['gap']}」— {e.get('reason')}")
        return 0

    names = load_names()
    wl_keys = {(e.get("chapter"), str(e.get("gap", "")).strip()) for e in entries}
    problems, pending = check_scenes(bodies, frags, heads, load_exempt(), names, wl_keys, scene_dir)
    problems += note_merge_warnings(notes, bodies)

    # 白名单过期：条目本次未命中任何实际间隙。不对称的白名单会烂在里面，
    # 越积越多，最后没人敢删。过期只报 WARN——条目过期往往说明正文刚改过，
    # 而正文改动是本作最频繁的事；判 BLOCK 的话人只会把白名单删掉，机制就没了。
    # 「实际间隙」一律取 08_Manuscripts，**不取 `--ms-dir`**：
    # `--ms-dir` 只是给测试用例注入假正文的口子，白名单登记的是真正文里的句子，
    # 拿假正文去判过期，会把 4 条全报成过期——那是脚本自己在造噪声。
    real_bodies, real_frags = load_ms(MS)[:2] if MS.is_dir() else (bodies, frags)
    live = {(g["chapter"], g["gap"]) for g in scan_gaps(real_bodies, real_frags)}
    for e in entries:
        k = (e.get("chapter"), str(e.get("gap", "")).strip())
        if k not in live:
            problems.append((
                WARN,
                f"白名单条目过期：{k[0]} /「{k[1]}」（登记于 {e.get('added_at', '?')}，"
                f"理由：{e.get('reason', '')}）\n"
                f"       本次全库未命中该间隙——若正文已改，请核对改动处的引文是否仍成立；"
                f"若规则已改，请删除本条",
            ))

    blocks = [t for lvl, t in problems if lvl == BLOCK]
    warns = [t for lvl, t in problems if lvl == WARN]

    if not args.quiet:
        by_card: dict[str, int] = {}
        for t in blocks:
            by_card[t.split(" ", 1)[0]] = by_card.get(t.split(" ", 1)[0], 0) + 1
        for path in sorted(scene_dir.glob("SCENE-*.md")):
            n = by_card.get(path.stem, 0)
            print(f"{path.stem:22s} {'OK' if not n else f'✗ {n} 项'}")

    cards = len(list(scene_dir.glob("SCENE-*.md")))
    quote_n = sum(len(QUOTE.findall(p.read_text(encoding="utf-8")))
                  for p in scene_dir.glob("SCENE-*.md"))
    # 违规 = BLOCK 数；WARN 另计。两者都不能和退出码打架：摘要不可信就是另一种噪声。
    print(f"\n卡片 {cards} 张｜引文核实 {quote_n} 处｜待重判 {len(pending)} 张"
          f"｜违规 {len(blocks)} 条｜WARN {len(warns)} 条")
    for t in blocks:
        print("  ✗", t)
    for t in warns:
        print("  [WARN]", t)
    return 0 if not blocks else 1


if __name__ == "__main__":
    raise SystemExit(main())
