# -*- coding: utf-8 -*-
"""生成真实测试用例：覆盖中文名/英文数字名/子目录/大文件/加密/空包/损坏/长文件名。

用法：python 测试/make_real_cases.py
输出目录：测试/真实用例/
"""

import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, "真实用例")

sys.path.insert(0, os.path.dirname(HERE))
from core.archiver import find_rar  # noqa: E402


def _mkdir(path):
    os.makedirs(path, exist_ok=True)


def make_chinese_zip():
    """中文文件名 + 子目录嵌套。"""
    root = os.path.join(CASES, "_src_中文")
    _mkdir(os.path.join(root, "分镜", "内层"))
    files = {
        "角色立绘.png": b"png-bytes-1",
        "背景_白天.jpg": b"jpg-bytes-2",
        os.path.join("分镜", "分镜01.png"): b"p1",
        os.path.join("分镜", "内层", "细节.png"): b"p2",
        os.path.join("分镜", "内层", "备用素材.txt"): b"txt",
    }
    out = os.path.join(CASES, "素材_中文.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in files.items():
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
            z.write(p, rel)
    print("->", out)


def make_english_zip():
    """00 01 02 03 式数字文件名（模拟主人场景）。"""
    root = os.path.join(CASES, "_src_英文")
    _mkdir(os.path.join(root, "img"))
    out = os.path.join(CASES, "素材_英文.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(4):
            rel = "img/%02d.jpg" % i
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"img-%02d" % i)
            z.write(p, rel)
    print("->", out)


def make_big_zip():
    """3 个 2MB 文件（真实体积，测打包性能与进度）。"""
    root = os.path.join(CASES, "_src_大文件")
    _mkdir(root)
    out = os.path.join(CASES, "素材_大文件.zip")
    blob = os.urandom(2 * 1024 * 1024)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(3):
            p = os.path.join(root, "chunk_%d.bin" % i)
            with open(p, "wb") as f:
                f.write(blob)
            z.write(p, "chunk_%d.bin" % i)
    print("->", out)


def make_encrypted_7z():
    """真正加密的 7z（py7zr 密码写入，header 未加密、内容加密）。"""
    import py7zr

    root = os.path.join(CASES, "_src_加密7z")
    _mkdir(root)
    out = os.path.join(CASES, "素材_加密.7z")
    p = os.path.join(root, "秘密.txt")
    with open(p, "wb") as f:
        f.write(b"secret")
    with py7zr.SevenZipFile(out, "w", password="1234") as z:
        z.write(p, "秘密.txt")
    print("->", out)


def make_encrypted_rar():
    """真正加密的 rar（Rar.exe -p 密码写入）。"""
    import subprocess

    root = os.path.join(CASES, "_src_加密rar")
    _mkdir(root)
    out = os.path.join(CASES, "素材_加密.rar")
    p = os.path.join(root, "机密.txt")
    with open(p, "wb") as f:
        f.write(b"topsecret")
    rar = find_rar()
    proc = subprocess.Popen([rar, "a", "-y", "-p1234", out, "*"], cwd=root,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc.wait()
    print("->", out)


def make_empty_zip():
    """空压缩包（无任何文件）。"""
    out = os.path.join(CASES, "素材_空包.zip")
    with zipfile.ZipFile(out, "w"):
        pass
    print("->", out)


def make_broken_zip():
    """损坏包（垃圾字节）。"""
    out = os.path.join(CASES, "素材_损坏.zip")
    with open(out, "wb") as f:
        f.write(b"PK\x03\x04 not a real zip, corrupt data padding padding")
    print("->", out)


def make_longname_zip():
    """超长文件名（>100 字符）。"""
    root = os.path.join(CASES, "_src_长名")
    _mkdir(root)
    out = os.path.join(CASES, "素材_长文件名.zip")
    long_name = "".join(["很长的文件名" * 10]) + "_最终版_绝对不改了_2026.jpg"
    p = os.path.join(root, long_name)
    with open(p, "wb") as f:
        f.write(b"long")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(p, long_name)
    print("->", out)


def make_rar():
    """rar 格式（中文名，走 Rar.exe）。"""
    root = os.path.join(CASES, "_src_rar")
    _mkdir(os.path.join(root, "sub"))
    files = {
        "cg_01.png": b"cg1",
        "cg_02.png": b"cg2",
        os.path.join("sub", "立绘.png"): b"lh",
    }
    for rel, data in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    out = os.path.join(CASES, "素材_rar.rar")
    import subprocess
    rar = find_rar()
    proc = subprocess.Popen([rar, "a", "-y", "-r", out, "*"], cwd=root,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc.wait()
    print("->", out)


def cleanup_src():
    for name in os.listdir(CASES):
        if name.startswith("_src"):
            shutil.rmtree(os.path.join(CASES, name), ignore_errors=True)


if __name__ == "__main__":
    _mkdir(CASES)
    make_chinese_zip()
    make_english_zip()
    make_big_zip()
    make_encrypted_7z()
    make_encrypted_rar()
    make_empty_zip()
    make_broken_zip()
    make_longname_zip()
    make_rar()
    cleanup_src()
    print("真实用例就绪:", CASES)
