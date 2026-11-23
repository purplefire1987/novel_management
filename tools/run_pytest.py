"""在沙箱会清临时文件的环境里跑 pytest。

两个坑，都在 `tempfile` 上：

1. `gettempdir()` 会「建一个探针文件再删掉」验证目录可写；沙箱拦了删除，
   于是它判定所有候选目录都不可用，报 `No usable temporary directory found`。
   `tempfile.tempdir` 一旦被显式赋值，`gettempdir()` 直接返回它、跳过验证。
2. 沙箱连 pytest 自己的清理也拦，旧运行留下的 `runtime/locks/id-allocate.lock`
   会活到下一轮，把 `test_lock_is_honored` 之外的用例也一起卡住（陈旧锁删不掉）。
   所以**每次跑用一个新的空目录**，靠「不去碰旧的」而不是「删掉旧的」来避免污染。

只影响本进程，不改仓库里任何文件。
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time

BASE = pathlib.Path(__file__).resolve().parent.parent
# 每个进程一个目录：pid + 时间戳，保证不会撞上上一次的残留。
TMP = BASE / ".pytest_tmp" / f"run-{os.getpid()}-{int(time.time())}"
TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TMP)

import pytest  # noqa: E402  必须在 tempfile.tempdir 之后导入

if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv[1:] or ["tests", "-q"]))
