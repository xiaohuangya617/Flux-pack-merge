# -*- coding: utf-8 -*-
"""GUI 端到端自动化测试：真实窗口 + 真实页面 + 真实 JS 桥。

在真实桌面会话中弹出窗口，用 evaluate_js 驱动前端 UI 完成
实测指引.txt 的场景 1/2/3/4/5，并验证：
  - 页面加载无 JS 错误、JS 桥注入成功
  - 配置记忆（get_state）
  - 完整处理链路（注入 -> 命名规则 -> 冲突决策 -> 打包 -> 完成报告）
  - 输出包内容与命名规则一致

用法：.venv/Scripts/python.exe 测试/test_gui_e2e.py
要求：先运行 make_real_cases.py 生成真实用例。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

# Windows 控制台默认 GBK，打印 ➕ 等字符会 UnicodeEncodeError，统一转 UTF-8
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CASES = os.path.join(HERE, "真实用例")
sys.path.insert(0, ROOT)

import webview  # noqa: E402

from main import APP_TITLE, UI_PATH, Api, _setup_drop  # noqa: E402
from core.archiver import list_entries  # noqa: E402
from core.config import CONFIG_PATH  # noqa: E402

PASS = []
FAIL = []


def ok(name):
    PASS.append(name)
    print("  [PASS]", name)


def fail(name, detail):
    FAIL.append(name)
    print("  [FAIL]", name, "->", detail)


OUT_DIR = tempfile.mkdtemp(prefix="e2e_out_")
TEST_TMP = tempfile.mkdtemp(prefix="e2e_src_")


def meta(p):
    """与 main.Api._meta 相同结构的元数据（模拟 scan 返回）。"""
    return {"path": p, "name": os.path.basename(p), "size": os.path.getsize(p), "count": -1}


def good(name):
    return os.path.join(CASES, name)


# ---------------- 窗口与桥 ----------------
api = Api()
window = webview.create_window(
    APP_TITLE, url=UI_PATH, js_api=api, width=1080, height=780, min_size=(900, 640)
)
api._window = window
window.events.loaded += lambda: _setup_drop(window)


def js(code):
    return window.evaluate_js(code)


def wait_js(cond_js, timeout=30, desc=""):
    """轮询 JS 表达式直到返回真值。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            v = js(cond_js)
        except Exception:
            v = None
        if v:
            return v
        time.sleep(0.15)
    raise TimeoutError("等待超时: %s (%s)" % (cond_js, desc))


def reset():
    """重置前端状态机，并把 Python->JS 事件回调替换为捕获器。

    注意：捕获器会先执行原版回调（v1.1 起 onDone 内含清空列表逻辑，
    测试必须让它真实执行），再记录数据；running/按钮状态仍显式重置。
    """
    js("""
    window.running = false;
    document.getElementById('go').disabled = true;
    document.getElementById('go').setAttribute('aria-disabled', 'true');
    document.getElementById('cancel').style.display = 'none';
    setProgress(0, '就绪');
    window.__done = null; window.__error = null; window.__cancel = null;
    window.__conflict = null; window.__prog = null;
    window.__outputExists = null; window.__readiness = null;
    window.sequenceWarningKey = '';
    /* 注意：必须用 IIFE 参数做闭包快照——顶层 var 是全局变量，
       每次 reset 重赋值会让旧捕获器引用指向自己，调用时无限递归。 */
    window.onDone = (function (orig) {
      return function (s) { window.__done = s; if (orig) orig(s); };
    })(window.onDone);
    window.onError = (function (orig) {
      return function (m) { window.__error = m; if (orig) orig(m); };
    })(window.onError);
    window.onCancel = (function (orig) {
      return function (m) { window.__cancel = m; if (orig) orig(m); };
    })(window.onCancel);
    window.onConflict = function (l) { window.__conflict = l; };
    window.onProgress = function (p) { window.__prog = p; };
    window.onOutputExists = function (p) { window.__outputExists = p; };
    window.onReadiness = (function (orig) {
      return function (g) { window.__readiness = g; if (orig) orig(g); };
    })(window.onReadiness);
    /* v1.3：清空删除流程状态（防上一场景残留触发删除弹窗） */
    window.deletePending = null;
    document.getElementById('delMask').classList.remove('show');
    /* 删除源文件开关复位为默认关（S16 开启后必须复位，否则污染后续场景） */
    document.getElementById('delSrc').checked = false;
    document.getElementById('delSrcTxt').textContent = '关';
    window.files = []; render();
    closeMsg();
    document.getElementById('outputMask').classList.remove('show');
    document.getElementById('sequenceMask').classList.remove('show');
    """)


def inject(pkgs):
    """把包注入列表（走前端 mergeFiles 去重逻辑）。"""
    js("window.mergeFiles(%s);" % json.dumps([meta(p) for p in pkgs], ensure_ascii=False))
    unique = list(dict.fromkeys(pkgs))
    api.analyze_files(unique)
    wait_js("window.files.length && window.files.every(function(f){ return f.status !== 'pending' && f.status !== 'checking'; })",
            30, "压缩包分析完成")


def set_form(rule=1, prefix="", seq="single", sep="_", keep=True,
             out_name="e2e输出", fmt="zip"):
    js("pickRule(%d);" % rule)
    js("document.getElementById('prefix').value = %s;" % json.dumps(prefix, ensure_ascii=False))
    js("document.getElementById('seq').value = %s;" % json.dumps(seq))
    js("document.getElementById('sep').value = %s;" % json.dumps(sep))
    js("document.getElementById('keep').checked = %s;" % ("true" if keep else "false"))
    js("document.getElementById('outdir').value = %s;" % json.dumps(OUT_DIR))
    js("document.getElementById('outname').value = %s;" % json.dumps(out_name, ensure_ascii=False))
    js("document.getElementById('fmt').value = %s;" % json.dumps(fmt))
    js("refreshReadiness()")


def click_go():
    wait_js("!document.getElementById('go').disabled", 30, "开始按钮就绪")
    js("document.getElementById('go').click()")


def names_in(out_path):
    """解压输出包（zip），返回扁平文件名集合。"""
    d = tempfile.mkdtemp(prefix="e2e_check_")
    try:
        from core.archiver import extract_all
        extract_all(out_path, d)
        got = set()
        for root, _, files in os.walk(d):
            for f in files:
                got.add(f)
        return got
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ================= 场景 =================
def s1_main_flow():
    print("== 场景1 主流程: 3包 + 规则二single + zip ==")
    reset()
    pkgs = [good("素材_中文.zip"), good("素材_英文.zip"), good("素材_rar.rar")]
    inject(pkgs)
    set_form(rule=2, seq="single", out_name="场景1输出")
    click_go()
    done = wait_js("window.__done", 60, "场景1完成")
    if done.get("bad"):
        fail("S1 无坏包", "bad=%s" % done["bad"])
        return
    if done.get("files") != 12:
        fail("S1 文件数=12", "files=%s" % done["files"])
        return
    # 期望名 = 前缀 + 原包内文件名（从真实包读取，不依赖人工猜测）
    expect = set()
    for i, p in enumerate(pkgs):
        pre = chr(ord("a") + i) + "_"
        for n in list_entries(p):
            expect.add(pre + os.path.basename(n))
    got = names_in(done["out"])
    if got == expect:
        ok("S1 输出12文件 a/b/c 前缀与内容一致 (%s)" % done["out"])
    else:
        fail("S1 输出内容", "缺=%s 多=%s" % (sorted(expect - got), sorted(got - expect)))


def s2_bad_packs():
    print("== 场景2 边界包: 3好 + 4坏(损坏/空包/加密7z/加密rar) ==")
    reset()
    pkgs = [good("素材_中文.zip"), good("素材_英文.zip"), good("素材_rar.rar")]
    inject(pkgs)
    inject([good("素材_损坏.zip"), good("素材_空包.zip"),
            good("素材_加密.7z"), good("素材_加密.rar")])
    set_form(rule=2, seq="single", out_name="场景2输出")
    click_go()
    done = wait_js("window.__done", 60, "场景2完成")
    bad = sorted(done.get("bad") or [])
    if done.get("files") != 12:
        fail("S2 正常包12文件", "files=%s" % done["files"])
    elif len(bad) == 4:
        ok("S2 跳过4坏包(%s)，正常包不受影响" % "、".join(bad))
    else:
        fail("S2 坏包数=4", "bad=%s" % bad)


def s3_conflict_auto():
    print("== 场景3 冲突弹窗: 同包x2 + 规则一 -> auto ==")
    reset()
    src = good("素材_英文.zip")
    s3_dir = os.path.join(TEST_TMP, "s3")
    os.makedirs(s3_dir, exist_ok=True)
    p1 = os.path.join(s3_dir, "副本一.zip")
    p2 = os.path.join(s3_dir, "副本二.zip")
    shutil.copy2(src, p1)
    shutil.copy2(src, p2)
    inject([p1, p2])
    set_form(rule=1, prefix="同", out_name="场景3输出")
    click_go()
    conflict = wait_js("window.__conflict", 30, "场景3冲突弹窗")
    if len(conflict) != 4:
        fail("S3 冲突条目=4", "conflict=%s" % len(conflict))
        return
    js("resolve('auto')")  # 前端决策按钮
    done = wait_js("window.__done", 60, "场景3完成")
    got = sorted(names_in(done["out"]))
    if len(got) == 8 and len(set(got)) == 8:
        ok("S3 auto加序号去重, 输出8文件无重名 (%s)" % got[:2])
    else:
        fail("S3 输出去重", "got=%s" % got)


def s4_cancel():
    print("== 场景4 取消 ==")
    # S4a 预演阶段取消：3个大包 test 校验耗时约1s，点击后立即取消必中预演
    reset()
    src = good("素材_大文件.zip")
    s4_dir = os.path.join(TEST_TMP, "s4")
    os.makedirs(s4_dir, exist_ok=True)
    s4_paths = []
    for i in range(3):
        target = os.path.join(s4_dir, "大文件副本%d.zip" % (i + 1))
        shutil.copy2(src, target)
        s4_paths.append(target)
    inject(s4_paths)
    set_form(rule=2, seq="single", out_name="场景4a输出")
    click_go()
    time.sleep(0.15)
    js("cancelRun()")
    cancel = wait_js("window.__cancel", 30, "S4a 预演取消")
    if cancel:
        ok("S4a 预演阶段取消: %s" % cancel)
    else:
        fail("S4a 预演取消", "done=%s" % js("window.__done"))
    # S4b 处理中取消：等进入解压/打包阶段（pct>=10，预演阶段也会推进度）再取消
    reset()
    inject([good("素材_大文件.zip")])
    set_form(rule=2, seq="single", out_name="场景4b输出")
    click_go()
    wait_js("window.__prog && window.__prog.pct >= 10", 30, "S4b 进入解压/打包")
    js("cancelRun()")
    cancel = wait_js("window.__cancel", 30, "S4b 处理中取消")
    if cancel:
        ok("S4b 处理中取消（解压/打包阶段均响应）: %s" % cancel)
    else:
        fail("S4b 处理中取消", "done=%s" % js("window.__done"))


def s5_prefix_no_keep():
    print("== 场景5 规则一 + 关闭保留原文件名 -> auto ==")
    reset()
    inject([good("素材_英文.zip")])
    set_form(rule=1, prefix="my", keep=False, out_name="场景5输出")
    click_go()
    wait_js("window.__conflict", 30, "场景5冲突弹窗")
    js("resolve('auto')")
    done = wait_js("window.__done", 60, "场景5完成")
    got = sorted(names_in(done["out"]))
    if got == ["my", "my_1", "my_2", "my_3"]:
        ok("S5 输出 my/my_1/my_2/my_3")
    else:
        fail("S5 输出", "got=%s" % got)


def s6_config_memory():
    print("== 场景6 配置记忆: get_state 返回 output_dir ==")
    st = api.get_state()
    if isinstance(st.get("output_dir"), str) and st["output_dir"]:
        ok("S6 配置记忆 output_dir=%s" % st["output_dir"])
    else:
        fail("S6 配置记忆", "state=%s" % st)


def s7_bridge_dom():
    print("== 场景7 桥注入与 DOM 通路 ==")
    t = js("typeof window.pywebview.api")
    if t == "object":
        ok("S7 JS桥注入 typeof=object")
    else:
        fail("S7 JS桥注入", "typeof=%s" % t)
        return
    # DOM API 通路：v1.1 拖拽注册在 body 上（#drop 已随全局化删除）
    try:
        el = window.dom.body
        if el is not None:
            ok("S7 DOM API body 可用（全局拖拽注册通路正常）")
        else:
            fail("S7 DOM API", "window.dom.body 返回 None")
    except Exception as e:
        fail("S7 DOM API", repr(e))
    if js("!!document.getElementById('drop')"):
        fail("S7 无旧 #drop 元素", "应已删除")
    else:
        ok("S7 旧 #drop 元素已删除，全局拖拽遮罩存在: %s"
           % js("!!document.getElementById('dropMask')"))


def s8_clear_after_done():
    print("== 场景8 完成后清空列表与输出文件名 ==")
    reset()
    inject([good("素材_英文.zip")])
    set_form(rule=2, seq="single", out_name="场景8输出")
    click_go()
    wait_js("window.__done", 60, "S8 完成")
    n = js("window.files.length")
    outname = js("document.getElementById('outname').value")
    outdir = js("document.getElementById('outdir').value")
    diag = js("(function(){ var s = window.onDone ? window.onDone.toString().slice(0, 100) : 'UNDEFINED'; "
              "return 'onDone=' + s + ' | showMsg=' + (typeof showMsg) + ' | esc=' + (typeof esc); })()")
    print("  S8 诊断:", diag)
    if n == 0 and outname == "" and outdir:
        ok("S8 列表已清空(%d)，输出文件名已清空，输出目录保留(%s)" % (n, outdir))
    else:
        fail("S8 清空逻辑", "files=%s outname=%r outdir=%r" % (n, outname, outdir))


def s9_layout():
    print("== 场景9 v1.1 布局断言 ==")
    # ④ 卡片（最后一个 .card）内应包含：加宽 outname、go 按钮、进度条
    go_text = js("document.getElementById('go').textContent")
    outname_w = js("document.getElementById('outname').offsetWidth")
    outdir_w = js("document.getElementById('outdir').offsetWidth")
    in_card4 = js("""
    (function () {
      var cards = document.querySelectorAll('.card');
      var c4 = cards[cards.length - 1];
      return c4.querySelector('.bar') !== null && c4.querySelector('#go') !== null;
    })()
    """)
    btn_add = js("!!document.getElementById('btnAdd')")
    tip = js("!!document.querySelector('.add-tip')")
    errors = []
    if go_text.strip() != "开始输出": errors.append("按钮文案异常=%r" % go_text)
    if outname_w < outdir_w: errors.append("outname偏小(%d)<outdir(%d)" % (outname_w, outdir_w))
    if not in_card4: errors.append("④卡片内缺进度条/按钮")
    if not (btn_add and tip): errors.append("➕按钮/提示缺失")
    if errors: fail("S9 布局", "；".join(errors))
    else: ok("S9 布局断言全部通过")


def s10_row_drag():
    print("== 场景10 行拖拽手柄排序 ==")
    reset()
    pkgs = [good("素材_中文.zip"), good("素材_英文.zip"), good("素材_rar.rar")]
    inject(pkgs)
    # JS 派发 dragstart -> dragover -> drop 到手柄（模拟拖第 1 行到第 3 行之后）
    ok_flag = js("""
    (function () {
      var tb = document.getElementById('tbody');
      var grips = tb.querySelectorAll('td.grip');
      if (grips.length !== 3) return 'grip数量异常:' + grips.length;
      var dt = { types: ['text/plain'], effectAllowed: '', dropEffect: '', setData: function () {} };
      var ds = new DragEvent('dragstart', { bubbles: true, cancelable: true });
      Object.defineProperty(ds, 'dataTransfer', { value: dt });
      grips[0].dispatchEvent(ds);
      var tr2 = grips[2].closest('tr');
      var rect = tr2.getBoundingClientRect();
      var dv2 = new DragEvent('dragover', { bubbles: true, cancelable: true, clientY: rect.top + rect.height - 2 });
      Object.defineProperty(dv2, 'dataTransfer', { value: dt });
      grips[2].dispatchEvent(dv2);
      var dv = new DragEvent('drop', { bubbles: true, cancelable: true });
      Object.defineProperty(dv, 'dataTransfer', { value: dt });
      grips[2].dispatchEvent(dv);
      return window.files.map(function (f) { return f.name; }).join('|');
    })()
    """)
    # 第1行(中文)拖到第3行(rar)之后：中文移到末尾 -> [英文, rar, 中文]
    expect = ["素材_英文.zip", "素材_rar.rar", "素材_中文.zip"]
    if ok_flag == "|".join(expect):
        ok("S10 手柄拖拽排序: " + ok_flag)
    else:
        fail("S10 手柄拖拽排序", "got=%s" % ok_flag)


def s11_click_copy():
    print("== 场景11 文件名点击复制反馈 ==")
    reset()
    inject([good("素材_中文.zip")])
    # 点击文件名单元格：复制链路可能是异步的（clipboard API / clip.exe 兜底），
    # 轮询「已复制」反馈 class（剪贴板实际内容由人工验收）
    js("(function(){ var el = document.querySelector('.fname-scroll'); if (el) el.click(); })()")
    cls = wait_js("(function(){ var el = document.querySelector('.fname-scroll'); "
                  "return (el && el.className.indexOf('copied') >= 0) ? el.className : null; })()",
                  8, "S11 复制反馈")
    clip = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Clipboard -Raw"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()
    if "copied" in str(cls) and clip == "素材_中文":
        ok("S11 点击复制去除 .zip 后缀，剪贴板内容正确")
    else:
        fail("S11 点击复制", "class=%s clipboard=%r" % (cls, clip))


def s12_default_rule():
    print("== 场景12 v1.2 默认序列规则 ==")
    state = api.get_state()
    if state.get("rule") == 2 and state.get("seq_mode") == "single":
        ok("S12 默认规则为序列命名 a-z")
    else:
        fail("S12 默认规则", "state=%s" % state)


def s13_readiness_button():
    print("== 场景13 开始按钮真实门控 ==")
    reset()
    set_form(rule=2, seq="single", out_name="场景13输出")
    wait_js("document.getElementById('go').disabled", 10, "空列表禁用")
    inject([good("素材_中文.zip")])
    js("refreshReadiness()")
    wait_js("!document.getElementById('go').disabled", 30, "条件满足启用")
    aria = js("document.getElementById('go').getAttribute('aria-disabled')")
    if aria == "false": ok("S13 空列表禁用，全部就绪后按钮启用")
    else: fail("S13 aria-disabled", aria)


def s14_sequence_overflow():
    print("== 场景14 30包+a-z 容量警告 ==")
    reset()
    p = good("素材_中文.zip")
    api.analyze_files([p])
    data = meta(p)
    analysed = api._public_analysis(api._analysis_for(p))
    data.update(analysed)
    js("window.files = %s; render();" % json.dumps([data] * 30, ensure_ascii=False))
    set_form(rule=2, seq="single", out_name="场景14输出")
    wait_js("document.getElementById('sequenceMask').classList.contains('show')", 20, "容量弹窗")
    disabled = js("document.getElementById('go').disabled")
    js("closeSequenceWarning(); refreshReadiness()")
    time.sleep(0.4)
    repeated = js("document.getElementById('sequenceMask').classList.contains('show')")
    js("window.files = window.files.slice(0, 26); render()")
    wait_js("!document.getElementById('go').disabled", 20, "数量恢复到容量内")
    js("while (window.files.length < 30) window.files.push(Object.assign({}, window.files[0])); render()")
    restored = wait_js("document.getElementById('sequenceMask').classList.contains('show')", 20,
                       "状态变化后再次提示")
    js("applySequenceMode('double')")
    wait_js("!document.getElementById('go').disabled", 20, "改规则后恢复")
    if disabled and not repeated and restored:
        ok("S14 30包+a-z 同状态仅弹一次，状态变化后再提示，改 aa-zz 后恢复")
    else:
        fail("S14 容量弹窗门控", "disabled=%s repeated=%s restored=%s" %
             (disabled, repeated, restored))


def s15_v13_defaults():
    print("== 场景15 v1.3 默认值：sep=-、fmt=zip、outname 空、删除开关关 ==")
    # 必须放在所有场景之前（页面加载后的初始状态），且等 pywebviewready
    # 的配置恢复完成（get_state 为异步）——见 main() 中的 sleep。
    sep = js("document.getElementById('sep').value")
    fmt = js("document.getElementById('fmt').value")
    outname = js("document.getElementById('outname').value")
    del_src = js("document.getElementById('delSrc').checked")
    if sep == "-" and fmt == "zip" and outname == "" and del_src is False:
        ok("S15 页面初始 sep=- fmt=zip outname空 删除开关关")
    else:
        fail("S15 默认值", "sep=%r fmt=%r outname=%r delSrc=%r" % (sep, fmt, outname, del_src))


def s16_delete_flow_on():
    print("== 场景16 v1.3 删除流程（开关开启 -> 确认后回收站删除） ==")
    reset()
    # 用临时副本做源包（真实回收站删除，不能碰测试素材原件）
    src_dir = os.path.join(TEST_TMP, "s16")
    os.makedirs(src_dir, exist_ok=True)
    copies = []
    for i, name in enumerate(["素材_中文.zip", "素材_英文.zip"]):
        target = os.path.join(src_dir, "待删%d_%s" % (i + 1, name))
        shutil.copy(good(name), target)
        copies.append(target)
    inject(copies)
    set_form(rule=2, seq="single", out_name="场景16输出")
    js("document.getElementById('delSrc').checked = true; refreshReadiness();")
    click_go()
    wait_js("window.__done", 60, "S16 完成")
    sources = js("(window.__done && window.__done.sources) || []")
    if len(sources) != 2:
        fail("S16 onDone.sources", "sources=%s" % sources)
        return
    # 关闭完成报告 -> 应弹出删除确认
    js("closeMsg()")
    wait_js("document.getElementById('delMask').classList.contains('show')", 10, "S16 删除确认弹窗")
    js("resolveDelete(true)")
    # 轮询源文件消失（回收站删除）
    deadline = time.time() + 15
    while time.time() < deadline and any(os.path.exists(p) for p in copies):
        time.sleep(0.2)
    gone = all(not os.path.exists(p) for p in copies)
    if gone:
        ok("S16 确认删除后源文件已移入回收站 (%d 个)" % len(copies))
    else:
        fail("S16 源文件删除", "残留=%s" % [p for p in copies if os.path.exists(p)])


def s17_delete_flow_off():
    print("== 场景17 v1.3 删除流程（开关关闭 -> 无删除弹窗） ==")
    reset()
    inject([good("素材_英文.zip")])
    set_form(rule=2, seq="single", out_name="场景17输出")
    click_go()
    wait_js("window.__done", 60, "S17 完成")
    js("closeMsg()")
    time.sleep(0.5)
    shown = js("document.getElementById('delMask').classList.contains('show')")
    pending = js("window.deletePending !== null && window.deletePending !== undefined")
    if not shown and not pending:
        ok("S17 开关关闭：无删除确认弹窗，源文件保留")
    else:
        fail("S17 无删除弹窗", "delMask=%s pending=%s" % (shown, pending))
        # 兜底：确保不会真的弹窗干扰后续场景
        js("window.deletePending = null; document.getElementById('delMask').classList.remove('show');")


def main():
    # 备份用户配置（场景运行会写入记忆配置）
    cfg_backup = None
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "rb") as f:
            cfg_backup = f.read()
    try:
        # v1.3：预写测试配置（output_dir 指向测试输出目录），保证 S6 记忆断言稳定
        from core.config import save as cfg_save
        cfg_save({"output_dir": OUT_DIR})
        if not window.events.loaded.wait(timeout=30):
            print("[FATAL] 页面加载超时")
            return 1
        # 等 pywebviewready 的 get_state 配置恢复完成（异步），S15 需要页面初始状态
        time.sleep(1.0)
        s15_v13_defaults()
        s7_bridge_dom()
        s6_config_memory()
        s1_main_flow()
        s2_bad_packs()
        s3_conflict_auto()
        s4_cancel()
        s5_prefix_no_keep()
        s8_clear_after_done()
        s9_layout()
        s10_row_drag()
        s11_click_copy()
        s12_default_rule()
        s13_readiness_button()
        s14_sequence_overflow()
        s16_delete_flow_on()
        s17_delete_flow_off()
    finally:
        time.sleep(0.5)
        if cfg_backup is not None:
            with open(CONFIG_PATH, "wb") as f:
                f.write(cfg_backup)  # 恢复用户配置
        else:
            # 原本无 config.json（测试预写了），清理避免残留测试配置
            try:
                if os.path.exists(CONFIG_PATH):
                    os.remove(CONFIG_PATH)
            except OSError:
                pass
        shutil.rmtree(OUT_DIR, ignore_errors=True)
        shutil.rmtree(TEST_TMP, ignore_errors=True)
        window.destroy()  # 优雅退出，回到 webview.start()

    print("-" * 50)
    print("GUI E2E 结果: %d 通过, %d 失败" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    exit_code = [1]

    def run_main():
        exit_code[0] = main()

    threading.Thread(target=run_main, daemon=True).start()
    webview.start()
    sys.exit(exit_code[0])
