# -*- coding: utf-8 -*-
"""生成测试压缩包：zip / rar / 7z，含中文文件名与子目录，以及一个损坏包。

用法：python 测试/make_test_packs.py
输出目录：测试/data/
"""

import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
MATERIAL = os.path.join(DATA, "素材")

sys.path.insert(0, os.path.dirname(HERE))
from core.archiver import find_rar  # noqa: E402

FILES = [
    "图1.jpg",
    "图2.png",
    os.path.join("子目录", "图3.jpg"),
    os.path.join("子目录", "图4.png"),
]


def build_material():
    if os.path.isdir(MATERIAL):
        shutil.rmtree(MATERIAL)
    for rel in FILES:
        p = os.path.join(MATERIAL, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(("内容:" + rel).encode("utf-8"))


def make_zip():
    out = os.path.join(DATA, "包1.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in FILES:
            zf.write(os.path.join(MATERIAL, rel), rel)
    print("zip ->", out)


def make_rar():
    out = os.path.join(DATA, "包2.rar")
    rar = find_rar()
    code, _, err = _run([rar, "a", "-y", "-r", out, "*"], cwd=MATERIAL)
    if code != 0:
        print("rar 生成失败:", err.decode("utf-8", "replace"))
    else:
        print("rar ->", out)


def make_7z():
    import py7zr

    out = os.path.join(DATA, "包3.7z")
    with py7zr.SevenZipFile(out, "w") as zf:
        for rel in FILES:
            zf.write(os.path.join(MATERIAL, rel), rel)
    print("7z ->", out)


def make_bad_zip():
    out = os.path.join(DATA, "坏包.zip")
    with open(out, "wb") as f:
        f.write(b"this is not a real zip file at all")
    print("坏zip ->", out)


def _run(cmd, cwd=None):
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out, err = proc.communicate()
    return proc.returncode, out, err


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    build_material()
    make_zip()
    make_rar()
    make_7z()
    make_bad_zip()
    print("测试数据就绪:", DATA)
