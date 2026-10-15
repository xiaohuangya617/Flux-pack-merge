# -*- coding: utf-8 -*-
"""Api 全流程模拟测试（不启动 GUI，mock window.evaluate_js）。

用法：python 测试/test_api_flow.py
要求：先运行 make_test_packs.py 生成测试数据。

覆盖场景：
  A 正常流程（规则二，3 格式包混合）
  B 冲突分支（规则一撞名 -> resolve('auto')）
  C 坏包跳过（全部坏包不打包）
  D 冲突时取消（resolve('cancel') -> onCancel）
  E 序列耗尽（单字母模式 27 个包 -> onError）
"""

import json
import os
import re
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data")
sys.path.insert(0, ROOT)

from main import Api  # noqa: E402

PASS = []


def ok(name):
    PASS.append(name)
    print("  [PASS]", name)


class FakeWindow:
    """模拟 pywebview 窗口：记录 evaluate_js 调用。"""

    def __init__(self):
        self.events = []

    def evaluate_js(self, js):
        self.events.append(js)

    def create_file_dialog(self, *a, **k):
        return []


def parse(js):
    m = re.match(r"window\.(\w+)\((.*)\)\s*$", js)
    assert m, "无法解析事件: " + js[:80]
    return m.group(1), json.loads(m.group(2))


def run_api(conf, auto_resolve=None, output_choice=None, wait_conflict=8.0):
    """启动 Api 任务并等待结束，返回 (事件列表, api)。"""
    api = Api()
    api._window = FakeWindow()
    api.start(conf)
    deadline = time.time() + wait_conflict
    conflict_resolved = False
    output_resolved = False
    while time.time() < deadline:
        names = [parse(e)[0] for e in api._window.events]
        if "onConflict" in names and auto_resolve is not None and not conflict_resolved:
            api.resolve(auto_resolve)
            conflict_resolved = True
        if "onOutputExists" in names and output_choice is not None and not output_resolved:
            api.resolve_output(output_choice)
            output_resolved = True
        if any(n in ("onDone", "onError", "onCancel") for n in names):
            break
        time.sleep(0.1)
    api._worker.join(timeout=60)
    events = [parse(e) for e in api._window.events]
    return events, api


def wait_endpoint(events, *names):
    return next((p for n, p in events if n in names), None)


def test_normal():
    print("== A 正常流程（规则二） ==")
    out = tempfile.mkdtemp()
    try:
        conf = {
            "files": [os.path.join(DATA, "包1.zip"), os.path.join(DATA, "包2.rar"),
                      os.path.join(DATA, "包3.7z")],
            "rule": 2, "seq_mode": "single", "sep": "_", "keep_orig": True,
            "out_name": "整合A", "fmt": "zip", "output_dir": out,
        }
        events, _ = run_api(conf)
        done = wait_endpoint(events, "onDone")
        assert done, "应收到 onDone，实际: %s" % [n for n, _ in events]
        assert done["files"] == 12, done
        assert done["bad"] == []
        p = os.path.join(out, "整合A.zip")
        assert os.path.isfile(p)
        ok("A 正常流程：12 文件整合输出成功")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_conflict_auto():
    print("== B 冲突分支（规则一 + auto） ==")
    out = tempfile.mkdtemp()
    try:
        conf = {
            "files": [os.path.join(DATA, "包1.zip"), os.path.join(DATA, "包1.zip")],
            "rule": 1, "prefix": "p", "sep": "_", "keep_orig": True,
            "out_name": "整合B", "fmt": "zip", "output_dir": out,
        }
        events, _ = run_api(conf, auto_resolve="auto")
        conflict = wait_endpoint(events, "onConflict")
        done = wait_endpoint(events, "onDone")
        assert conflict and len(conflict) >= 4, conflict  # 4 个同名文件撞名
        assert done and done["files"] == 8, done
        p = os.path.join(out, "整合B.zip")
        assert os.path.isfile(p)
        from core.archiver import list_entries
        names = list_entries(p)
        assert len(names) == len(set(names)), "输出包内不应有重名: %s" % names
        assert "p_图1.jpg" in names and "p_图1_1.jpg" in names, names
        ok("B 冲突 auto：加序号去重，输出无重名")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_conflict_cancel():
    print("== D 冲突时取消 ==")
    conf = {
        "files": [os.path.join(DATA, "包1.zip"), os.path.join(DATA, "包1.zip")],
        "rule": 1, "prefix": "p", "sep": "_", "keep_orig": True,
        "out_name": "整合D", "fmt": "zip",
        "output_dir": tempfile.mkdtemp(),
    }
    events, _ = run_api(conf, auto_resolve="cancel")
    assert wait_endpoint(events, "onConflict"), "应先弹冲突"
    assert wait_endpoint(events, "onCancel"), "取消应收到 onCancel"
    assert wait_endpoint(events, "onDone") is None
    ok("D 冲突取消：onCancel 且无 onDone")


def test_bad_pack():
    print("== C 坏包 ==")
    out = tempfile.mkdtemp()
    try:
        conf = {
            "files": [os.path.join(DATA, "坏包.zip")],
            "rule": 1, "prefix": "p", "sep": "_", "keep_orig": True,
            "out_name": "整合C", "fmt": "zip", "output_dir": out,
        }
        events, _ = run_api(conf)
        err = wait_endpoint(events, "onError")
        assert err and "没有可处理" in err, events
        assert not os.path.exists(os.path.join(out, "整合C.zip")), "不应生成空包"
        ok("C 坏包：开始按钮门控拒绝全部坏包且不生成空输出")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_exhausted():
    print("== E 序列耗尽 ==")
    out = tempfile.mkdtemp()
    tmp = tempfile.mkdtemp()
    try:
        src = os.path.join(DATA, "包1.zip")
        files = []
        for i in range(27):
            p = os.path.join(tmp, "包%02d.zip" % i)
            shutil.copy(src, p)
            files.append(p)
        conf = {
            "files": files,
            "rule": 2, "seq_mode": "single", "sep": "_", "keep_orig": True,
            "out_name": "整合E", "fmt": "zip", "output_dir": out,
        }
        events, _ = run_api(conf)
        err = wait_endpoint(events, "onError")
        assert err and ("序列耗尽" in err or "最多支持" in err), events
        ok("E 序列容量超限：开始前拦截并提示")
    finally:
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def test_readiness_gate():
    print("== F v1.2 就绪门控 ==")
    out = tempfile.mkdtemp()
    try:
        src = os.path.join(DATA, "包1.zip")
        api = Api()
        api._window = FakeWindow()
        conf = {
            "files": [src], "rule": 2, "seq_mode": "single", "sep": "_",
            "keep_orig": True, "out_name": "门控", "fmt": "zip", "output_dir": out,
        }
        assert api.check_readiness(conf)["reasons"][0]["code"] == "checking"
        api.analyze_files([src])
        gate = api.check_readiness(conf)
        assert gate["ready"] and gate["processable_count"] == 1
        # v1.3：磁盘预估公式 = ceil(Σ未压缩体积 × 1.3)，v1.2 的 ×2+512MB 下限已放宽
        import math
        entry = api._analysis.get(src)
        assert entry and entry[1].get("status") == "ready", "分析结果缺失"
        unpacked = entry[1].get("unpacked_size") or 0
        assert unpacked > 0
        assert gate["required_bytes"] == math.ceil(unpacked * 1.3), (
            "required=%s 期望=%s" % (gate["required_bytes"], math.ceil(unpacked * 1.3)))
        assert gate["required_bytes"] < 512 * 1024 * 1024, "v1.3 不应再有 512MB 下限"

        for mode, limit in (("single", 26), ("double", 676), ("num", 999), ("numz", 999)):
            within = dict(conf, files=[src] * limit, seq_mode=mode)
            assert api.check_readiness(within)["ready"], "%s 容量上限应可用" % mode
            over = dict(conf, files=[src] * (limit + 1), seq_mode=mode)
            over_gate = api.check_readiness(over)
            assert any(r["code"] == "sequence_overflow" for r in over_gate["reasons"]), mode

        prefix_gate = api.check_readiness(dict(conf, files=[src] * 1000, rule=1, prefix="p"))
        assert prefix_gate["ready"], "统一前缀不受序列容量限制"

        with mock.patch("main.archiver.rar_available", return_value=False):
            no_rar = api.check_readiness(dict(conf, fmt="rar"))
        assert any(r["code"] == "rar_unavailable" for r in no_rar["reasons"])

        for invalid_name in ("CON", "nul.txt", "x" * 256):
            invalid = api.check_readiness(dict(conf, out_name=invalid_name))
            assert any(r["code"] == "invalid_name" for r in invalid["reasons"]), invalid_name

        with mock.patch("main.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            low = api.check_readiness(conf)
        assert any(r["code"] == "insufficient_space" for r in low["reasons"])
        ok("F 就绪门控：检查中/可用/空间不足/序列全边界/无 WinRAR")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_existing_output_choices():
    print("== G v1.2 同名输出保护 ==")
    out = tempfile.mkdtemp()
    try:
        src = os.path.join(DATA, "包1.zip")
        existing = os.path.join(out, "同名.zip")
        marker = b"KEEP-ORIGINAL"
        with open(existing, "wb") as f:
            f.write(marker)
        conf = {
            "files": [src], "rule": 2, "seq_mode": "single", "sep": "_",
            "keep_orig": True, "out_name": "同名", "fmt": "zip", "output_dir": out,
        }
        events, _ = run_api(conf, output_choice="new")
        done = wait_endpoint(events, "onDone")
        assert wait_endpoint(events, "onOutputExists") and done
        assert done["out"].endswith("同名_1.zip") and os.path.isfile(done["out"])
        with open(existing, "rb") as f:
            assert f.read() == marker

        events, _ = run_api(conf, output_choice="overwrite")
        done = wait_endpoint(events, "onDone")
        assert done and done["out"] == existing
        from core.archiver import test as test_archive
        assert test_archive(existing)
        assert not any(n.startswith(".整合工具_") for n in os.listdir(out))
        ok("G 同名输出：默认新名称保留旧文件，明确覆盖后原子替换")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_delete_flow():
    print("== H v1.3 删除流程（sources 字段 + 回收站桥） ==")
    out = tempfile.mkdtemp()
    try:
        good1 = os.path.join(DATA, "包1.zip")
        bad = os.path.join(DATA, "坏包.zip")
        # H1：全部成功 -> sources 含全部源包
        conf = {
            "files": [good1, os.path.join(DATA, "包2.rar")],
            "rule": 2, "seq_mode": "single", "sep": "-", "keep_orig": True,
            "out_name": "整合H", "fmt": "zip", "output_dir": out,
        }
        events, _ = run_api(conf)
        done = wait_endpoint(events, "onDone")
        assert done and done["bad"] == [], done
        assert sorted(done["sources"]) == sorted(conf["files"]), done["sources"]
        ok("H1 成功任务的 sources 包含全部源包")

        # H2：混入坏包 -> sources 排除坏包
        conf2 = dict(conf, files=[good1, bad], out_name="整合H2")
        events2, _ = run_api(conf2)
        done2 = wait_endpoint(events2, "onDone")
        assert done2 and done2["bad"] == ["坏包.zip"], done2
        assert done2["sources"] == [good1], done2["sources"]
        ok("H2 坏包不在删除候选 sources 中")

        # H3：回收站桥（mock 底层删除，验证参数传递与返回值）
        from main import Api as ApiCls
        from unittest import mock
        api3 = ApiCls()
        api3._window = FakeWindow()
        with mock.patch("main._recycle_paths", return_value=(1, 0)) as m_recycle:
            r = api3.delete_to_recycle([good1])
            m_recycle.assert_called_once_with([good1])
            assert r == {"ok": 1, "failed": 0, "error": None}, r
        ok("H3 delete_to_recycle 桥调用 _recycle_paths 并透传结果")
    finally:
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    for p in ("包1.zip", "包2.rar", "包3.7z", "坏包.zip"):
        assert os.path.isfile(os.path.join(DATA, p)), "缺少测试数据，先运行 make_test_packs.py"
    # 场景会走 save_cfg 改写用户配置，跑完恢复
    from core.config import CONFIG_PATH
    cfg_backup = None
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "rb") as f:
            cfg_backup = f.read()
    try:
        test_normal()
        test_conflict_auto()
        test_conflict_cancel()
        test_bad_pack()
        test_exhausted()
        test_readiness_gate()
        test_existing_output_choices()
        test_delete_flow()
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
    print("\nALL API FLOW TESTS PASS (%d)" % len(PASS))
