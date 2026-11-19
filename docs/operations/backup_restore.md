# 备份与恢复

## 原则

1. **备份独立于 Git。** Git 仓库不是备份——它只在同一块磁盘上，且可能被误操作改写。
2. **备份独立于 Vault。** 备份不放在 Vault 内，否则一次误删连备份一起消失。
3. **备份必须可验证。** 只声称"已备份"没有意义，要能逐文件比对。

## 手工备份（当前阶段）

```bash
SRC="/path/to/author-vault"
DST="/path/to/backup/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DST"
rsync -a "$SRC/" "$DST/"
```

## 验证

```bash
# 文件数一致
find "$SRC" -type f | wc -l
find "$DST" -type f | wc -l

# Vault 内有 git 时，用对象哈希比对（比 mtime 可靠）
git -C "$SRC" ls-tree -r HEAD --format="%(objectname) %(path)" | sort > /tmp/a
git -C "$DST" ls-tree -r HEAD --format="%(objectname) %(path)" | sort > /tmp/b
diff -q /tmp/a /tmp/b && echo "对象库一致"
```

## 恢复

```bash
# 先确认目标已另行备份，再覆盖
cp -a "/path/to/backup/YYYYMMDD-HHMMSS" "/path/to/author-vault"
```

恢复后必须重跑 `novel validate` 与 `novel rebuild-index`。

## Runtime 不入备份

`runtime/` 下的索引、缓存可重建，不纳入备份。但 `runtime/staging/` 若含未落盘的提案，需单独判断。

## 尚未实现

`novel backup` 与 `novel restore` 命令在后续阶段提供；当前阶段用手工流程。
