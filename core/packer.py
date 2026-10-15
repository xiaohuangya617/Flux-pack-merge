# -*- coding: utf-8 -*-
"""打包器：把平铺重命名后的临时目录压缩为单个压缩包。

    fmt  后端              说明
    rar  Rar.exe            WinRAR 官方命令行（本机已实测存在）
    zip  zipfile            Python 标准库，逐文件进度
    7z   py7zr              纯 Python，一次写入

进度约定：on_progress(done, total) —— rar 为估算（进程运行中 0->95，完成置 100），
zip/7z 按文件数真实推进。取消由调用方设置标志后本模块检查 proc 存活即可。
"""

import os
import shutil
import subprocess
import threading
import time
import zipfile

from core.archiver import find_rar

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Cancelled(Exception):
    """内部信号：打包阶段被调用方取消（调用方负责清理与善后）。"""


def count_files(work_dir):
    """统计临时目录内文件总数（用于进度分母）。"""
    n = 0
    for _, _, files in os.walk(work_dir):
        n += len(files)
    return n


def cleanup(work_dir):
    """删除工具自建的临时工作目录（取消/完成后调用，绝不触碰用户原始文件）。"""
    if work_dir and os.path.isdir(work_dir):
        shutil.rmtree(work_dir, ignore_errors=True)


def _purge_empty_dirs(work_dir):
    """删除工作目录中的空目录（解压残留的 _pkg_* 等），防止被 rar 递归打进包。"""
    for root, dirs, files in os.walk(work_dir, topdown=False):
        if root == work_dir:
            continue
        try:
            if not os.listdir(root):
                os.rmdir(root)
        except OSError:
            pass


# ---------------------------------------------------------------- zip
def _pack_zip(work_dir, out_path, on_progress, should_cancel=None):
    files = []
    for root, _, names in os.walk(work_dir):
        for name in names:
            files.append(os.path.join(root, name))
    total = max(len(files), 1)
    done = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            if should_cancel and should_cancel():
                raise Cancelled()
            arc = os.path.relpath(f, work_dir).replace(os.sep, "/")
            zf.write(f, arc)
            done += 1
            if on_progress:
                on_progress(done, total)


# ---------------------------------------------------------------- 7z
def _pack_7z(work_dir, out_path, on_progress, should_cancel=None):
    import py7zr

    files = []
    for root, _, names in os.walk(work_dir):
        for name in names:
            files.append(os.path.join(root, name))
    total = max(len(files), 1)
    with py7zr.SevenZipFile(out_path, "w") as zf:
        for i, f in enumerate(files):
            if should_cancel and should_cancel():
                raise Cancelled()
            arc = os.path.relpath(f, work_dir).replace(os.sep, "/")
            zf.write(f, arc)
            if on_progress:
                on_progress(i + 1, total)


# ---------------------------------------------------------------- rar
def _pack_rar(work_dir, out_path, on_progress, should_cancel=None):
    rar_path = find_rar()
    n = max(count_files(work_dir), 1)
    stop = {"v": False}

    def tick():
        done = 0
        while not stop["v"]:
            done = min(done + 2, 95)
            if on_progress:
                on_progress(done, 100)
            time.sleep(0.25)

    th = threading.Thread(target=tick, daemon=True)
    th.start()
    proc = None
    try:
        proc = subprocess.Popen(
            [rar_path, "a", "-r", "-y", "-ep1", out_path, "*"],
            cwd=work_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        # 轮询等待：响应取消（terminate 子进程后按取消处理，不留半成品）
        while True:
            if should_cancel and should_cancel():
                proc.terminate()
                raise Cancelled()
            if proc.poll() is not None:
                break
            time.sleep(0.1)
    finally:
        stop["v"] = True
        th.join(timeout=0.5)
    if proc is None or proc.returncode != 0:
        raise RuntimeError("Rar.exe 打包失败（返回码 %r）" % (proc and proc.returncode))
    if on_progress:
        on_progress(100, 100)


# ---------------------------------------------------------------- 分发
def pack(work_dir, out_path, fmt, on_progress=None, should_cancel=None):
    """把 work_dir 打包为 out_path（格式由扩展名/参数决定）。

    should_cancel: 无参回调，返回 True 时立即中止并抛 Cancelled。
    """
    _purge_empty_dirs(work_dir)
    if fmt == "rar":
        _pack_rar(work_dir, out_path, on_progress, should_cancel)
    elif fmt == "zip":
        _pack_zip(work_dir, out_path, on_progress, should_cancel)
    elif fmt == "7z":
        _pack_7z(work_dir, out_path, on_progress, should_cancel)
    else:
        raise ValueError("未知输出格式: %r" % fmt)
