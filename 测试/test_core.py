# -*- coding: utf-8 -*-
"""core 层全量单元测试（P2-P6）。

用法：python 测试/test_core.py
要求：先运行 make_test_packs.py 生成测试数据。
"""

import os
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data")
sys.path.insert(0, ROOT)

from core.sequence import seq_at, seq_limit  # noqa: E402
from core.renamer import compute_targets, detect_conflicts  # noqa: E402
import core.config as cfg  # noqa: E402
from core import archiver, packer  # noqa: E402

PASS = []


def ok(name):
    PASS.append(name)
    print("  [PASS]", name)


# ---------------------------------------------------------------- sequence
def test_sequence():
    print("== sequence ==")
    assert seq_at("single", 0) == "a" and seq_at("single", 25) == "z"
    assert seq_at("double", 0) == "aa" and seq_at("double", 25) == "az"
    assert seq_at("double", 26) == "ba" and seq_at("double", 675) == "zz"
    assert seq_at("num", 0) == "1" and seq_at("num", 998) == "999"
    assert seq_at("numz", 0) == "001" and seq_at("numz", 9) == "010"
    assert seq_limit("single") == 26 and seq_limit("double") == 676
    for m in ("single", "double", "num", "numz"):
        try:
            seq_at(m, seq_limit(m))
            assert False, m
        except ValueError:
            pass
    ok("sequence 边界(26/676/999)与耗尽")


# ---------------------------------------------------------------- renamer
def test_renamer():
    print("== renamer ==")
    archives = [["图1.jpg", "图2.jpg"], ["图1.jpg", "图3.jpg"], ["场景.png"]]
    items = compute_targets(archives, 1, "my", "_", True)
    assert items[0][2] == "my_图1.jpg" and items[-1][2] == "my_场景.png"
    items2 = compute_targets(archives, 2, "", "_", True, "single")
    assert [t for _, _, t in items2] == [
        "a_图1.jpg", "a_图2.jpg", "b_图1.jpg", "b_图3.jpg", "c_场景.png",
    ]
    items0 = compute_targets(archives, 0, "", "", True)
    assert items0[0][2] == "图1.jpg"
    items_k = compute_targets([["x.jpg"]], 1, "my_", "_", False)
    assert items_k[0][2] == "my_"
    try:
        compute_targets([["x.jpg"]], 0, "", "", False)
        assert False
    except ValueError:
        pass
    conf = detect_conflicts(items)
    assert "my_图1.jpg" in conf and len(conf["my_图1.jpg"]) == 2
    assert detect_conflicts(items2) == {}
    ok("命名规则/原文件名开关/冲突检测")


# ---------------------------------------------------------------- config
def test_config():
    print("== config ==")
    tmp_dir = tempfile.mkdtemp()
    tmp = os.path.join(tmp_dir, "config.json")
    old = cfg.CONFIG_PATH
    old_fallback = cfg._FALLBACK_DIR
    cfg.CONFIG_PATH = tmp
    cfg._FALLBACK_DIR = os.path.join(tmp_dir, "fallback")
    try:
        d = cfg.load()
        assert d["rule"] == 2 and d["seq_mode"] == "single" and d["keep_orig"] is True
        # v1.3：默认格式 zip；out_name 与 sep 不记忆（不写不读）
        assert d["fmt"] == "zip", "v1.3 默认格式应为 zip"
        assert "out_name" not in d and "sep" not in d, "v1.3 out_name/sep 不应出现"
        cfg.save({"output_dir": "E:\\out", "fmt": "rar", "files": ["D:\\private.zip"],
                  "unknown": 1, "out_name": "不应记忆", "sep": "-"})
        d2 = cfg.load()
        assert d2["output_dir"] == "E:\\out" and d2["fmt"] == "rar" and d2["prefix"] == ""
        assert "files" not in d2 and "unknown" not in d2 and d2["config_version"] == 2
        assert "out_name" not in d2 and "sep" not in d2, "v1.3 out_name/sep 不应被保存"
    finally:
        cfg.CONFIG_PATH = old
        cfg._FALLBACK_DIR = old_fallback
        shutil.rmtree(tmp_dir, ignore_errors=True)
    ok("config 读写与默认值合并")


# ---------------------------------------------------------------- archiver
def test_archiver():
    print("== archiver ==")
    z, r, s = os.path.join(DATA, "包1.zip"), os.path.join(DATA, "包2.rar"), os.path.join(DATA, "包3.7z")
    for p in (z, r, s):
        assert os.path.isfile(p), "缺少测试包，请先运行 make_test_packs.py: " + p

    expect = ["图1.jpg", "图2.png", "子目录/图3.jpg", "子目录/图4.png"]
    for name, p in (("zip", z), ("rar", r), ("7z", s)):
        entries = archiver.list_entries(p)
        assert sorted(entries) == sorted(expect), "%s 条目不符: %s" % (name, entries)
        assert archiver.test(p) is True, "%s 校验应为 True" % name
        d = tempfile.mkdtemp()
        try:
            archiver.extract_all(p, d)
            for rel in expect:
                assert os.path.isfile(os.path.join(d, rel.replace("/", os.sep))), rel
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
        ok("archiver/%s 中文文件名+子目录+校验" % name)

    bad = os.path.join(DATA, "坏包.zip")
    assert os.path.isfile(bad)
    assert archiver.test(bad) is False
    ok("archiver 坏包检测")

    analysed = archiver.analyze(z)
    assert analysed["status"] == "ready" and analysed["count"] == 4
    assert analysed["unpacked_size"] == 80
    assert archiver.analyze(bad)["status"] == "skip_broken"
    ok("archiver 结构化状态与未压缩体积")


# ---------------------------------------------------------------- packer
def test_packer():
    print("== packer ==")
    work = tempfile.mkdtemp()
    out_dir = tempfile.mkdtemp()
    try:
        for i in range(3):
            with open(os.path.join(work, "文件%d.txt" % i), "w", encoding="utf-8") as f:
                f.write("x" * 10)
        os.makedirs(os.path.join(work, "sub"))
        with open(os.path.join(work, "sub", "内嵌.txt"), "w", encoding="utf-8") as f:
            f.write("y" * 10)

        progress = []
        for fmt in ("zip", "7z", "rar"):
            out = os.path.join(out_dir, "out." + fmt)
            packer.pack(work, out, fmt, on_progress=lambda a, b: progress.append((a, b)))
            assert os.path.isfile(out) and os.path.getsize(out) > 0, fmt
            entries = archiver.list_entries(out)
            assert sorted(entries) == ["sub/内嵌.txt", "文件0.txt", "文件1.txt", "文件2.txt"], (fmt, entries)
            assert not any(e.startswith("_pkg") for e in entries), "%s 混入临时目录: %s" % (fmt, entries)
            assert archiver.test(out) is True, fmt
            ok("packer/%s 产物有效且内容完整" % fmt)
        assert progress, "应有进度回调"

        # 取消响应：should_cancel=True 时三格式都应立即中止并抛 Cancelled
        for fmt in ("zip", "7z", "rar"):
            out = os.path.join(out_dir, "cancel." + fmt)
            try:
                packer.pack(work, out, fmt, should_cancel=lambda: True)
                raise AssertionError("%s 打包未响应取消" % fmt)
            except packer.Cancelled:
                ok("packer/%s 取消响应（抛 Cancelled）" % fmt)
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    test_sequence()
    test_renamer()
    test_config()
    test_archiver()
    test_packer()
    print("\nALL CORE TESTS PASS (%d)" % len(PASS))
