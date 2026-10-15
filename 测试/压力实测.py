# -*- coding: utf-8 -*-
"""大包/大数量文件压力实测（手动运行，不进默认回归套件）。

用法：.venv/Scripts/python.exe 测试/压力实测.py
生成数据约 4GB（随机数据不可压缩），跑完自动清理。
实测指标：整流程耗时、进程内存峰值（50ms 采样）、输出正确性。
"""

import ctypes
import os
import shutil
import sys
import tempfile
import time
import zipfile
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from main import Api  # noqa: E402
from core.archiver import list_entries  # noqa: E402


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def rss_mb():
    """当前进程工作集内存（MB）。"""
    _GetProcessMemoryInfo = ctypes.windll.psapi.GetProcessMemoryInfo
    _GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
        wintypes.DWORD,
    ]
    _GetProcessMemoryInfo.restype = wintypes.BOOL
    c = PROCESS_MEMORY_COUNTERS_EX()
    c.cb = ctypes.sizeof(c)
    h = ctypes.windll.kernel32.GetCurrentProcess()
    if not _GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
        return -1.0  # 调用失败（不应发生）
    return c.WorkingSetSize / 1048576.0


class FW:
    """最小窗口 mock（不弹 GUI，直接跑 Api 流水线）。"""

    def __init__(self):
        self.events = []

    def evaluate_js(self, js):
        self.events.append(js)

    def create_file_dialog(self, *a, **k):
        return []


def run_flow(conf, label, timeout=1800):
    """跑完整 Api 流水线，监控内存峰值与耗时。"""
    api = Api()
    api._window = FW()
    t0 = time.time()
    peak = 0.0
    api.start(conf)
    while time.time() - t0 < timeout:
        peak = max(peak, rss_mb())
        if any(k in " ".join(api._window.events) for k in
               ("window.onDone(", "window.onError(", "window.onCancel(")):
            break
        time.sleep(0.05)
    api._worker.join(timeout=120)
    elapsed = time.time() - t0
    ev = api._window.events
    last = ev[-1] if ev else "(无事件)"
    print("[%s] 耗时 %.1fs | 内存峰值 %.0f MB | 末事件: %s" % (label, elapsed, peak, last[:140]))
    return ev


def main():
    base = tempfile.mkdtemp(prefix="压力实测_")
    # 完整流程会 save_cfg 改写用户配置，跑完恢复
    from core.config import CONFIG_PATH
    cfg_backup = None
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "rb") as f:
            cfg_backup = f.read()
    try:
        # ---------------- 场景 A：1GB 级单文件 zip ----------------
        print("== 构造 1GB 级 zip（随机数据，STORED 直存）…")
        big_bin = os.path.join(base, "big.bin")
        big_zip = os.path.join(base, "大包1GB.zip")
        chunk = os.urandom(8 * 1048576)
        with open(big_bin, "wb") as f:  # 8MB × 128 = 1GB
            for _ in range(128):
                f.write(chunk)
        with zipfile.ZipFile(big_zip, "w", zipfile.ZIP_STORED) as z:
            with z.open("video.bin", "w") as dst, open(big_bin, "rb") as src:
                while True:
                    b = src.read(8 * 1048576)
                    if not b:
                        break
                    dst.write(b)
        print("  构造完成: %.2f GB" % (os.path.getsize(big_zip) / 2 ** 30))

        out_a = os.path.join(base, "outA")
        os.makedirs(out_a)
        ev_a = run_flow({
            "files": [big_zip], "rule": 2, "seq_mode": "single", "sep": "_",
            "keep_orig": True, "out_name": "大包输出", "fmt": "zip",
            "output_dir": out_a,
        }, "场景A 1GB包(单文件) 校验+解压+重命名+打包")
        n = list_entries(os.path.join(out_a, "大包输出.zip"))
        print("  A 输出条目: %s（期望 ['a_video.bin']）" % n)

        # ---------------- 场景 B：1500 文件小包 ----------------
        print("== 构造 1500 文件 zip（10 个子目录，DEFLATE）…")
        many_zip = os.path.join(base, "多文件1500.zip")
        with zipfile.ZipFile(many_zip, "w", zipfile.ZIP_DEFLATED) as z:
            for i in range(1500):
                z.writestr("dir%d/文件%04d.txt" % (i % 10, i), ("内容%d" % i) * 40)
        print("  构造完成: %.1f MB" % (os.path.getsize(many_zip) / 1048576.0))

        out_b = os.path.join(base, "outB")
        os.makedirs(out_b)
        ev_b = run_flow({
            "files": [many_zip], "rule": 2, "seq_mode": "single", "sep": "_",
            "keep_orig": True, "out_name": "多文件输出", "fmt": "zip",
            "output_dir": out_b,
        }, "场景B 1500文件 校验+解压+重命名+打包")
        names = list_entries(os.path.join(out_b, "多文件输出.zip"))
        print("  B 输出条目数: %d（期望 1500），重名: %s" % (len(names), len(names) != len(set(names))))
    finally:
        if cfg_backup is not None:
            with open(CONFIG_PATH, "wb") as f:
                f.write(cfg_backup)
        shutil.rmtree(base, ignore_errors=True)
        print("== 临时数据已清理 ==")


if __name__ == "__main__":
    main()
