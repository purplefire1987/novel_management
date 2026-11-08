#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记录作者对「大纲 / 细纲 / 正文」的手工修改，给出改动清单与行级差异。

## 为什么需要它

`04_Outline/`、`08_Manuscripts/`、`99_Archive/` 都在 `.gitignore` 里
（2026-11-04 作者裁定：作品本体不进公开仓库）。后果是：

- 作者直接改这三处，`git diff` 一个字都看不到，`git status` 永远是干净的；
- `novel snapshot` 只是给 HEAD 打 tag（`gitops.create_tag`），
  而 HEAD 里根本没有这些文件，所以它管不到；
- `novel rollback` 只回滚系统自己写过的事务（`.novel_system/backups/OP-*`），
  人手改的字它不认识。

也就是说：**「谁在什么时候改了正文哪一句」这件事，此前没有任何记录。**
本脚本补的正是这里。按文件指纹存一份基线，下次运行时给出增删改清单与行级差异。

## 射程

管：`04_Outline/`、`08_Manuscripts/` 下的 `.md` `.yaml` `.yml`。

不管：判断改动好不好。判据在别处——
`check_kept_lines.py`（必留句是否还在）、`check_hooks.py`（钩子与逼迫反应）、
`verify_cards.py`（卡片字段与引文）、`novel integrity`（字数、关系、字段）。
本脚本只回答一个问题：**哪里变了**。

## 它不做什么

- 不自动接受现状。比对完必须人跑 `--save` 才推进基线，
  否则下次还会报同一批差异——这是故意的：差异要人看过才算数。
- 不写回任何被跟踪的文件。全脚本除基线快照外只读。

## 用法

    # 建基线（或确认「这批改动我已看过」）
    .venv/bin/python 00_System/scripts/track_author_edits.py --save

    # 比对：列出改了什么（行级摘要，默认每文件最多 40 行）
    .venv/bin/python 00_System/scripts/track_author_edits.py

    # 看某个文件基线里存的那一版（只打印，不还原）
    .venv/bin/python 00_System/scripts/track_author_edits.py --show 08_Manuscripts/Volume_01/CH-0001.md

退出码：0 无差异 / 1 有差异 / 2 环境不可用（无基线、路径不在射程内）

## 基线存在哪

`.novel_system/snapshots/author_baseline.json.gz`。它内含正文原文，
与 `08_Manuscripts/` 同级别，**必须继续被 git 忽略**，
否则一次 `git add -A` 就把整卷正文推上公开远端。对应规则见 `.gitignore`。
"""

from __future__ import annotations

import argparse
import difflib
import gzip
import hashlib
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SNAP_REL = ".novel_system/snapshots/author_baseline.json.gz"
TARGETS = ("04_Outline", "08_Manuscripts")
EXTS = (".md", ".yaml", ".yml")
CH_LIMITS = (3000, 4000)
DRIFT = 0.03

HAN = re.compile(r"[\u4e00-\u9fff]")
EN = re.compile(r"[A-Za-z]+")
BODY = re.compile(r"^##[ \t]*正文[ \t]*$(.*?)(?=^##[ \t]|\Z)", re.S | re.M)
DECLARED = re.compile(r"^word_count:[ \t]*(\d+)", re.M)


def _fallback_count(text: str) -> int:
    """汉字 + 英文整词。与 `vault.count_words()` 同式。"""
    return len(HAN.findall(text)) + len(EN.findall(text))


def _body_text(text: str) -> str:
    """取 `## 正文` 一节；没有这一节就当全文是正文。

    与 `validators/integrity.py` 的 `MANUSCRIPT_BODY` 同式。
    """
    m = BODY.search(text)
    return m.group(1) if m else text


def _fallback_body_words(text: str) -> int:
    """切 `## 正文`、切 `## 写作备注`，再计。与 `body_word_count()` 同式。"""
    return _fallback_count(_body_text(text).split("## 写作备注")[0])


def _body_han(text: str) -> int:
    """正文一节的汉字数。

    **这是篇幅门真正量的东西。** `integrity.py:839` 用 `han` 判 3000–4000，
    而 `han` 取的是 `MANUSCRIPT_BODY` 匹配到的那一段，不含 frontmatter，
    也不含 `## 写作备注`。整份文件的汉字数会多出四五百（备注里的修订记录），
    拿它去撞篇幅门必然误报——所以本脚本分开记这两个数。
    """
    return len(HAN.findall(_body_text(text)))


def _load_counter():
    """优先用仓库自己的口径；导不到就用同式内置实现，并在输出里标出来。"""
    try:
        sys.path.insert(0, os.path.join(ROOT, "00_System", "novel_system", "src"))
        from novel_system.workflow.writing import body_word_count  # type: ignore
        from novel_system.vault import count_words  # type: ignore

        return body_word_count, count_words, "novel_system"
    except Exception:
        return _fallback_body_words, _fallback_count, "内置（同式副本）"


def scan():
    """扫射程内全部文件，返回 {相对路径: 记录} 与 {相对路径: 原文}。"""
    body_words, count_words, _ = _load_counter()
    meta: dict[str, dict] = {}
    texts: dict[str, str] = {}
    for root in TARGETS:
        base = os.path.join(ROOT, root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if not name.endswith(EXTS):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with open(path, "rb") as fh:
                        raw = fh.read()
                except OSError:
                    continue
                text = raw.decode("utf-8", errors="replace")
                rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
                rec = {
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                    "lines": text.count("\n") + 1,
                    "han": len(HAN.findall(text)),
                }
                if re.match(r"^CH-\d{4}", name):
                    rec["body_words"] = body_words(text)
                    rec["body_han"] = _body_han(text)
                    rec["declared"] = None
                    m = DECLARED.search(text)
                    if m:
                        rec["declared"] = int(m.group(1))
                meta[rel] = rec
                texts[rel] = text
    return meta, texts


def load_snapshot():
    path = os.path.join(ROOT, SNAP_REL)
    if not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_snapshot(meta, texts):
    path = os.path.join(ROOT, SNAP_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "version": 1,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "counter": _load_counter()[2],
        "files": meta,
        "texts": texts,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    tmp = path + ".tmp"
    with gzip.open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, path)
    return path, len(raw), len(meta)


def chapter_flags(rel: str, rec: dict, text: str) -> list[str]:
    """正文特有的硬指标。这些是「改完之后必须回头看」的东西，不是风格意见。

    两把尺子分别对应两处真实门限，量错对象就会天天误报：

    - 篇幅：`integrity.py:839` 用正文一节的**汉字**（不是 `body_word_count()`，
      后者含英文整词）判 3000–4000；
    - 字数漂移：`integrity.py:833` 用 `declared` 与**正文汉字**比，超过 3% 报警。
    """
    if not re.match(r"^CH-\d{4}", os.path.basename(rel)):
        return []
    out = []
    body_han = rec.get("body_han")
    body_words = rec.get("body_words")
    if isinstance(body_han, int):
        low, high = CH_LIMITS
        if not low <= body_han <= high:
            out.append(f"篇幅越界：正文汉字 {body_han}，门限 {low}–{high}")
        declared = rec.get("declared")
        if isinstance(declared, int) and declared:
            drift = abs(declared - body_han) / max(body_han, 1)
            if drift > DRIFT:
                out.append(
                    f"`word_count: {declared}` 与正文汉字 {body_han} 差 {drift:.1%}，"
                    f"超过 {DRIFT:.0%} 会报「字数漂移」"
                )
        elif declared is None:
            out.append("frontmatter 缺 `word_count`")
    pub = re.search(r"^status:[ \t]*(\S+)", text, re.M)
    if pub and pub.group(1) == "published":
        out.append("本章 `status: published`——改的是已发布正文，改完要重跑 `novel style` 与 `integrity`")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="跟踪作者对大纲/细纲/正文的手工修改（git 看不见的那一块）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--save", action="store_true", help="把当前状态存成新基线")
    ap.add_argument("--show", metavar="路径", help="打印基线里该文件的内容（只读，不还原）")
    ap.add_argument("--full", action="store_true", help="输出完整行级 diff（默认每文件最多 40 行）")
    ap.add_argument("--max-lines", type=int, default=40, help="每个文件 diff 行数上限，默认 40")
    args = ap.parse_args(argv)

    meta, texts = scan()
    if not meta:
        print("射程内没有可跟踪的文件，检查是否在仓库根目录运行", file=sys.stderr)
        return 2

    if args.show:
        snap = load_snapshot()
        if snap is None:
            print(f"没有基线：{SNAP_REL}，先跑 --save", file=sys.stderr)
            return 2
        rel = args.show.replace(os.sep, "/").lstrip("./")
        body = snap.get("texts", {}).get(rel)
        if body is None:
            print(f"基线里没有 {rel}", file=sys.stderr)
            return 2
        sys.stdout.write(body)
        return 0

    if args.save:
        path, raw_len, n = save_snapshot(meta, texts)
        print(f"基线已存：{os.path.relpath(path, ROOT)}")
        print(f"  文件 {n} 个｜原文 {raw_len / 1024:.1f} KB（gzip 后 {os.path.getsize(path) / 1024:.1f} KB）")
        print(f"  口径：{_load_counter()[2]}")
        return 0

    snap = load_snapshot()
    if snap is None:
        print(f"还没有基线：{SNAP_REL}", file=sys.stderr)
        print("先跑一次 --save 建立起点；此后每次比对都以它为参照。", file=sys.stderr)
        return 2

    old_files = snap.get("files", {})
    old_texts = snap.get("texts", {})
    added = sorted(set(meta) - set(old_files))
    removed = sorted(set(old_files) - set(meta))
    modified = sorted(
        p for p in set(meta) & set(old_files)
        if meta[p]["sha256"] != old_files[p]["sha256"]
    )

    print(f"基线 {snap.get('saved_at', '?')}　→　现在 {time.strftime('%Y-%m-%dT%H:%M:%S')}")
    print(f"文件 {len(meta)} 个｜改动 {len(modified)}｜新增 {len(added)}｜删除 {len(removed)}")

    flags: list[tuple[str, list[str]]] = []

    for rel in modified:
        new, old = meta[rel], old_files[rel]
        d_han = new["han"] - old.get("han", 0)
        d_line = new["lines"] - old.get("lines", 0)
        print(f"\n── 改动 {rel}")
        print(f"   汉字 {old.get('han', 0)} → {new['han']}（{d_han:+d}）｜行 {old.get('lines', 0)} → {new['lines']}（{d_line:+d}）")
        if "body_han" in new and "body_han" in old:
            print(
                f"   正文汉字 {old['body_han']} → {new['body_han']}"
                f"（{new['body_han'] - old['body_han']:+d}）"
                f"｜正文报字数 {old.get('body_words', 0)} → {new.get('body_words', 0)}"
            )
        diff = list(
            difflib.unified_diff(
                old_texts.get(rel, "").splitlines(),
                texts.get(rel, "").splitlines(),
                fromfile="基线", tofile="现在", lineterm="", n=1,
            )
        )
        shown = diff if args.full else diff[: args.max_lines]
        for line in shown:
            print("   " + line)
        if not args.full and len(diff) > len(shown):
            print(f"   …… 还有 {len(diff) - len(shown)} 行，加 --full 看全")
        f = chapter_flags(rel, new, texts[rel])
        if f:
            flags.append((rel, f))

    for rel in added:
        rec = meta[rel]
        print(f"\n── 新增 {rel}　{rec['lines']} 行 / 汉字 {rec['han']}")
        f = chapter_flags(rel, rec, texts[rel])
        if f:
            flags.append((rel, f))

    for rel in removed:
        print(f"\n── 删除 {rel}（基线里有，现在没了）")
        flags.append((rel, ["文件被删；若是有意删除，--save 确认后可消掉本条"]))

    if flags:
        print("\n== 改完必须回头看的硬指标 ==")
        for rel, items in flags:
            for it in items:
                print(f"  [{os.path.basename(rel)}] {it}")

    if not (modified or added or removed):
        print("\n无差异。")
        return 0

    print(
        "\n看完再决定：跑 `--save` 推进基线（等于说「这批改动我认可，作为新起点」）；"
        "\n不 save 的话，下次仍会报同一批差异。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
