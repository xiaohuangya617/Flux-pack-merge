# -*- coding: utf-8 -*-
"""真实文件全场景测试：走完整 Api 流程，验证输出包内容/命名/顺序/边界。

用法：python 测试/test_real.py
要求：先运行 make_real_cases.py 生成真实用例。
"""

import json
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CASES = os.path.join(HERE, "真实用例")
sys.path.insert(0, ROOT)

from main import Api  # noqa: E402
from core.archiver import list_entries, extract_all  # noqa: E402

PASS = []


def ok(name):
    PASS.append(name)
    print("  [PASS]", name)


class FW:
    def __init__(self):
        self.events = []

    def evaluate_js(self, js):
        self.events.append(js)

    def create_file_dialog(self, *a, **k):
        return []


def parse_events(events):
    out = []
    for e in events:
        m = re.match(r"window\.(\w+)\((.*)\)\s*$", e)
        if m:
            out.append((m.group(1), json.loads(m.group(2))))
    return out


def run(conf, auto_resolve=None):
    api = Api()
    api._window = FW()
    api.start(conf)
    deadline = time.time() + 15
    while time.time() < deadline:
        names = [n for n, _ in parse_events(api._window.events)]
        if "onConflict" in names and auto_resolve is not None:
            api.resolve(auto_resolve)
        if any(n in ("onDone", "onError", "onCancel") for n in names):
            break
        time.sleep(0.1)
    api._worker.join(timeout=120)
    return parse_events(api._window.events), api


def endpoint(events, name):
    return next((p for n, p in events if n == name), None)


def read_bytes(archive, rel):
    d = tempfile.mkdtemp()
    try:
        extract_all(archive, d)
        with open(os.path.join(d, rel.replace("/", os.sep)), "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_mixed_rule2():
    print("== 1 混合3包+规则二 single -> zip ==")
    out = tempfile.mkdtemp()
    try:
        ev, _ = run({
            "files": [os.path.join(CASES, "素材_中文.zip"),
                      os.path.join(CASES, "素材_英文.zip"),
                      os.path.join(CASES, "素材_大文件.zip")],
            "rule": 2, "seq_mode": "single", "sep": "_", "keep_orig": True,
            "out_name": "混合", "fmt": "zip", "output_dir": out,
        })
        done = endpoint(ev, "onDone")
        assert done, [n for n, _ in ev]
        assert done["files"] == 12, done  # 中文5 + 英文4 + 大文件3
        names = list_entries(os.path.join(out, "混合.zip"))
        assert len(names) == len(set(names)), "有重名: %s" % names
        assert names[0].startswith("a_") and names[5].startswith("b_") and names[9].startswith("c_"), names
        assert "a_角色立绘.png" in names and "b_00.jpg" in names and "c_chunk_0.bin" in names, names
        ok("混合3包：12 文件、a/b/c 分组、无重名、顺序正确")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_content_integrity():
    print("== 2 内容一致性（源包 vs 输出） ==")
    out = tempfile.mkdtemp()
    try:
        ev, _ = run({
            "files": [os.path.join(CASES, "素材_中文.zip")],
            "rule": 2, "seq_mode": "single", "sep": "_", "keep_orig": True,
            "out_name": "一致性", "fmt": "zip", "output_dir": out,
        })
        done = endpoint(ev, "onDone")
        assert done and done["files"] == 5, done
        z = os.path.join(out, "一致性.zip")
        src = os.path.join(CASES, "素材_中文.zip")
        for rel in list_entries(src):
            tgt = "a_" + os.path.basename(rel)
            assert tgt in list_entries(z), "输出缺 %s" % tgt
            assert read_bytes(z, tgt) == read_bytes(src, rel), "内容不一致: %s" % rel
        ok("内容一致性：输出文件字节与源包逐字节相同")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_rule1_no_orig():
    print("== 3 规则一 + 不保留原文件名（同名冲突 auto） ==")
    out = tempfile.mkdtemp()
    try:
        ev, _ = run({
            "files": [os.path.join(CASES, "素材_英文.zip")],
            "rule": 1, "prefix": "pic", "sep": "-", "keep_orig": False,
            "out_name": "前缀", "fmt": "zip", "output_dir": out,
        }, auto_resolve="auto")
        done = endpoint(ev, "onDone")
        assert done and done["files"] == 4, done
        names = list_entries(os.path.join(out, "前缀.zip"))
        assert len(names) == len(set(names)), "输出有重名: %s" % names
        assert names[0] == "pic", names  # 第一个保留原名 pic，其余 pic_1...
        ok("规则一不保留原文件名：冲突弹窗 -> auto 加序号去重，无重名")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_double_num_modes():
    print("== 4 双字母/数字模式 ==")
    out = tempfile.mkdtemp()
    try:
        ev, _ = run({
            "files": [os.path.join(CASES, "素材_英文.zip"),
                      os.path.join(CASES, "素材_rar.rar")],
            "rule": 2, "seq_mode": "double", "sep": "_", "keep_orig": True,
            "out_name": "双字", "fmt": "zip", "output_dir": out,
        })
        done = endpoint(ev, "onDone")
        assert done, [n for n, _ in ev]
        names = list_entries(os.path.join(out, "双字.zip"))
        assert any(n.startswith("aa_") for n in names) and any(n.startswith("ab_") for n in names), names
        ev, _ = run({
            "files": [os.path.join(CASES, "素材_英文.zip")],
            "rule": 2, "seq_mode": "num", "sep": "", "keep_orig": True,
            "out_name": "数字", "fmt": "zip", "output_dir": out,
        })
        done = endpoint(ev, "onDone")
        assert done
        names = list_entries(os.path.join(out, "数字.zip"))
        assert any(n.startswith("1") for n in names), names
        ok("双字母 aa/ab 与数字模式前缀正确")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_rar_7z_output():
    print("== 5 输出 rar 与 7z ==")
    out = tempfile.mkdtemp()
    try:
        for fmt in ("rar", "7z"):
            ev, _ = run({
                "files": [os.path.join(CASES, "素材_中文.zip")],
                "rule": 1, "prefix": "p", "sep": "_", "keep_orig": True,
                "out_name": "输出", "fmt": fmt, "output_dir": out,
            })
            done = endpoint(ev, "onDone")
            assert done and done["files"] == 5, (fmt, done)
            p = os.path.join(out, "输出." + fmt)
            names = list_entries(p)
            assert len(names) == 5 and "p_角色立绘.png" in names, (fmt, names)
        ok("rar/7z 输出产物完整")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_bad_edge_packs():
    print("== 6 混入损坏/空包/加密包 ==")
    out = tempfile.mkdtemp()
    try:
        ev, _ = run({
            "files": [os.path.join(CASES, "素材_中文.zip"),
                      os.path.join(CASES, "素材_损坏.zip"),
                      os.path.join(CASES, "素材_空包.zip"),
                      os.path.join(CASES, "素材_加密.7z"),
                      os.path.join(CASES, "素材_加密.rar")],
            "rule": 1, "prefix": "x", "sep": "_", "keep_orig": True,
            "out_name": "边界", "fmt": "zip", "output_dir": out,
        })
        done = endpoint(ev, "onDone")
        assert done, [n for n, _ in ev]
        assert done["files"] == 5, done
        assert len(done["bad"]) == 4, done["bad"]  # 损坏/空包/加密7z/加密rar 全部跳过
        ok("坏包边界：损坏/空包/加密(7z+rar) 全部跳过并列入报告，正常包不受影响")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_longname():
    print("== 7 超长文件名 ==")
    out = tempfile.mkdtemp()
    try:
        ev, _ = run({
            "files": [os.path.join(CASES, "素材_长文件名.zip")],
            "rule": 2, "seq_mode": "single", "sep": "_", "keep_orig": True,
            "out_name": "长名", "fmt": "zip", "output_dir": out,
        })
        done = endpoint(ev, "onDone")
        assert done and done["files"] == 1, done
        names = list_entries(os.path.join(out, "长名.zip"))
        assert len(names) == 1 and names[0].startswith("a_"), names
        ok("超长文件名正常整合")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_dup_same_pack():
    print("== 8 同一包加两次 + 规则二 ==")
    out = tempfile.mkdtemp()
    try:
        ev, _ = run({
            "files": [os.path.join(CASES, "素材_英文.zip"),
                      os.path.join(CASES, "素材_英文.zip")],
            "rule": 2, "seq_mode": "single", "sep": "_", "keep_orig": True,
            "out_name": "同包", "fmt": "zip", "output_dir": out,
        })
        done = endpoint(ev, "onDone")
        assert done and done["files"] == 8, done
        names = list_entries(os.path.join(out, "同包.zip"))
        assert len(names) == len(set(names)), "输出有重名"
        ok("同包两次 + 规则二（a/b 前缀天然区分，无冲突）")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_same_name_subdirs():
    print("== 9 子目录同名文件（不同目录同名，内容不得串包） ==")
    tmp = tempfile.mkdtemp()
    out = tempfile.mkdtemp()
    try:
        import zipfile

        src = os.path.join(tmp, "同名包.zip")
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("dir1/same.txt", "AAA")
            z.writestr("dir2/same.txt", "BBB")
        ev, _ = run({
            "files": [src],
            "rule": 2, "seq_mode": "single", "sep": "_", "keep_orig": True,
            "out_name": "同名", "fmt": "zip", "output_dir": out,
        }, auto_resolve="auto")
        done = endpoint(ev, "onDone")
        assert done and done["files"] == 2, done
        # 两个同名 -> 冲突 -> auto 加序号 -> 内容必须分别是 AAA/BBB（不得重复）
        d = tempfile.mkdtemp()
        try:
            extract_all(os.path.join(out, "同名.zip"), d)
            contents = {}
            for root, _, names in os.walk(d):
                for n in names:
                    with open(os.path.join(root, n), "r", encoding="utf-8") as f:
                        contents[n] = f.read().strip()
            assert sorted(contents.values()) == ["AAA", "BBB"], contents
            assert sorted(contents.keys()) == ["a_same.txt", "a_same_1.txt"], contents
        finally:
            shutil.rmtree(d, ignore_errors=True)
        ok("子目录同名文件：auto 去重后内容一一对应，无串包")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    for f in os.listdir(CASES):
        assert os.path.isfile(os.path.join(CASES, f)), "缺少真实用例，先运行 make_real_cases.py"
    # 场景会走 save_cfg 改写用户配置，跑完恢复
    from core.config import CONFIG_PATH
    cfg_backup = None
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "rb") as f:
            cfg_backup = f.read()
    try:
        test_mixed_rule2()
        test_content_integrity()
        test_rule1_no_orig()
        test_double_num_modes()
        test_rar_7z_output()
        test_bad_edge_packs()
        test_longname()
        test_dup_same_pack()
        test_same_name_subdirs()
    finally:
        if cfg_backup is not None:
            with open(CONFIG_PATH, "wb") as f:
                f.write(cfg_backup)
        else:
            try:  # 原本无 config.json，删除测试写入的残留
                if os.path.exists(CONFIG_PATH):
                    os.remove(CONFIG_PATH)
            except OSError:
                pass
    print("\nALL REAL TESTS PASS (%d)" % len(PASS))
