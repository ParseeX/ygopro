"""python tools/replay_to_text.py replay.yrp --content 客户端目录。"""
import sys

sys.dont_write_bytecode = True
from replay_log.__main__ import main

if __name__ == "__main__":
    # Windows 的重定向输出也保持 UTF-8，避免中文诊断乱码。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
