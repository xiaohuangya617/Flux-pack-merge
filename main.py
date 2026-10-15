# -*- coding: utf-8 -*-
"""压缩包整合工具 v1.3 — 主入口。

pywebview 桌面窗口 + Api（JS<->Python 桥）。
运行：python main.py
依赖：pywebview、py7zr（见 requirements.txt），rar 打包需本机 WinRAR（Rar.exe）。
"""

import ctypes
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import traceback
import uuid
from ctypes import wintypes
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

from core import archiver, packer, renamer, sequence
from core.config import load as load_cfg
from core.config import save as save_cfg

APP_VERSION = "1.3.0"
APP_TITLE = "压缩包整合工具 v1.3"
HERE = os.path.dirname(os.path.abspath(__file__))
RESOURCE_DIR = os.path.abspath(getattr(sys, "_MEIPASS", HERE))
UI_PATH = Path(RESOURCE_DIR, "ui", "index.html").as_uri()
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *("COM%d" % i for i in range(1, 10)),
    *("LPT%d" % i for i in range(1, 10)),
}


class _Cancelled(Exception):
    """内部信号：任务被用户取消。"""


class _SHFILEOPSTRUCTW(ctypes.Structure):
    """SHFileOperationW 的入参结构（回收站删除用）。"""

    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def _recycle_paths(paths):
    """把文件移入回收站（SHFileOperationW + FOF_ALLOWUNDO），返回 (成功数, 失败数)。

    删除是危险操作，本工具只在用户明确确认后调用；回收站可恢复，误删可救。
    失败不抛异常，由调用方统计并展示。
    """
    FO_DELETE = 3
    FOF_SILENT = 0x0004           # 不显示系统进度
    FOF_NOCONFIRMATION = 0x0010   # 不弹系统确认框（本工具弹窗已确认）
    FOF_ALLOWUNDO = 0x0040        # 允许撤销 = 进回收站
    targets = [p for p in (paths or []) if p and os.path.isfile(p)]
    if not targets:
        return 0, 0
    # pFrom 为双 \0 结尾的多路径串（Windows 规范）
    buf = "\x00".join(os.path.abspath(p) for p in targets) + "\x00\x00"
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = buf
    op.pTo = None
    op.fFlags = FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO
    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception:
        return 0, len(targets)
    if result != 0:
        return 0, len(targets)
    remaining = [p for p in targets if os.path.exists(p)]
    return len(targets) - len(remaining), len(remaining)


def _uniquify(path):
    """目标文件已存在时追加 _1/_2 兜底（预演已处理冲突，此为双保险）。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists("%s_%d%s" % (base, i, ext)):
        i += 1
    return "%s_%d%s" % (base, i, ext)


def _normalise_output_name(name, fmt):
    """清洗输出名，并避免用户填写扩展名后生成 .zip.rar 之类双后缀。"""
    clean = renamer.sanitize(str(name or "").strip()).rstrip(" .")
    suffix = "." + str(fmt or "").lower()
    if clean.lower().endswith(suffix):
        clean = clean[:-len(suffix)].rstrip(" .")
    if clean and clean.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        return ""
    if len((clean + suffix).encode("utf-16-le")) // 2 > 255:
        return ""
    return clean


def _unique_output_path(path):
    """返回不覆盖已有输出的路径。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists("%s_%d%s" % (base, i, ext)):
        i += 1
    return "%s_%d%s" % (base, i, ext)


class Api:
    """暴露给前端的桥接对象（js_api）。"""

    def __init__(self):
        # 注意：必须用下划线私有名！pywebview 的 get_functions 会递归遍历
        # js_api 的全部公开属性，公开的 window 引用会被一路下钻到
        # native.AccessibilityObject.Bounds.Empty... 造成无限递归崩溃。
        self._window = None
        self._cancel = threading.Event()
        self._conflict_evt = threading.Event()
        self._conflict_choice = None
        self._output_evt = threading.Event()
        self._output_choice = None
        self._worker = None
        self._analysis = {}
        self._analysis_lock = threading.Lock()

    # ================= 前端调用 =================
    def get_state(self):
        """读取记忆的配置（上次输出目录等）。"""
        state = load_cfg()
        state["rar_available"] = archiver.rar_available()
        return state

    def pick_folder(self):
        """弹出系统文件夹选择器，返回选中路径列表。"""
        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            return list(result) if result else []
        except Exception:
            return []

    def scan(self, paths):
        """快速扫描路径并返回待检查条目，不在此处阻塞做完整性校验。"""
        found = []
        for p in paths or []:
            if os.path.isdir(p):
                for root, _, names in os.walk(p):
                    for n in names:
                        fp = os.path.join(root, n)
                        if archiver.is_archive(fp):
                            found.append(fp)
            elif archiver.is_archive(p):
                found.append(p)
        found = sorted(set(found))
        return [self._meta(fp) for fp in found]

    def analyze_files(self, paths, generation=None):
        """逐个分析压缩包并实时推送结果，供前端更新就绪状态。"""
        results = []
        unique = list(dict.fromkeys(paths or []))
        for i, path in enumerate(unique):
            result = self._analysis_for(path, force=True)
            public = self._public_analysis(result)
            public["checked"] = i + 1
            public["total"] = len(unique)
            if generation is not None:
                public["generation"] = generation
            results.append(public)
            self._push("onArchiveAnalyzed", public)
        return results

    def check_readiness(self, conf):
        """返回开始按钮所需的统一就绪判定，不执行压缩任务。"""
        return self._readiness(conf or {})

    def start(self, conf):
        """开始处理（后台线程执行，进度/冲突/结果通过事件推送）。"""
        if self._worker and self._worker.is_alive():
            return
        self._cancel.clear()
        self._worker = threading.Thread(target=self._run, args=(conf or {},), daemon=True)
        self._worker.start()

    def cancel(self):
        """取消当前任务。"""
        self._cancel.set()
        if not self._conflict_evt.is_set():
            self._conflict_choice = "cancel"
            self._conflict_evt.set()
        if not self._output_evt.is_set():
            self._output_choice = "cancel"
            self._output_evt.set()

    def resolve(self, choice):
        """冲突弹窗回传决策：auto=自动加序号  reseq=序列命名  cancel=取消。"""
        self._conflict_choice = choice
        self._conflict_evt.set()

    def resolve_output(self, choice):
        """同名输出弹窗回传：new=生成新名称 overwrite=覆盖 cancel=取消。"""
        self._output_choice = choice
        self._output_evt.set()

    def copy_text(self, text):
        """写入系统剪贴板（v1.1：file:// 下 JS clipboard API 受限时的兜底）。

        用系统自带 clip.exe（UTF-16LE+BOM），中文/换行均安全。
        """
        try:
            import subprocess
            proc = subprocess.Popen(["clip.exe"], stdin=subprocess.PIPE)
            proc.communicate(b"\xff\xfe" + text.encode("utf-16-le"))
            return proc.returncode == 0
        except Exception:
            return False

    def delete_to_recycle(self, paths):
        """v1.3：把整合成功的源压缩包移入回收站（前端弹窗确认后才调用）。

        返回 {"ok": 成功数, "failed": 失败数, "error": 错误描述或 None}。
        """
        try:
            ok, failed = _recycle_paths(paths)
            return {"ok": ok, "failed": failed, "error": None}
        except Exception as e:
            return {"ok": 0, "failed": len(paths or []), "error": str(e)}

    # ================= 内部 =================
    def _meta(self, path):
        """返回快速元数据；完整条目数和体积由 analyze_files 后台补齐。"""
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return {
            "path": path,
            "name": os.path.basename(path),
            "size": size,
            "count": -1,
            "unpacked_size": None,
            "status": "pending",
        }

    @staticmethod
    def _fingerprint(path):
        try:
            st = os.stat(path)
            return st.st_size, st.st_mtime_ns
        except OSError:
            return None

    def _analysis_for(self, path, force=False):
        fingerprint = self._fingerprint(path)
        with self._analysis_lock:
            cached = self._analysis.get(path)
            if not force and cached and cached[0] == fingerprint:
                return cached[1]
        result = archiver.analyze(path)
        with self._analysis_lock:
            self._analysis[path] = (fingerprint, result)
        return result

    @staticmethod
    def _public_analysis(result):
        """剥离可能很大的 entries，仅向前端发送按钮判断所需字段。"""
        return {k: v for k, v in result.items() if k != "entries"}

    def _cached_analysis(self, path):
        fingerprint = self._fingerprint(path)
        with self._analysis_lock:
            cached = self._analysis.get(path)
            if cached and cached[0] == fingerprint:
                return cached[1]
        return None

    def _readiness(self, conf, analyses=None):
        """计算文件、规则、输出格式和磁盘空间是否全部满足。"""
        files = list(conf.get("files") or [])
        if analyses is None:
            analyses = [self._cached_analysis(path) or {
                "path": path, "status": "pending", "unpacked_size": None,
            } for path in files]

        reasons = []
        statuses = [a.get("status", "pending") for a in analyses]
        checked = sum(s not in ("pending", "checking") for s in statuses)
        ready_items = [a for a in analyses if a.get("status") == "ready"]
        skipped = [a for a in analyses if str(a.get("status", "")).startswith("skip_")]
        if not files:
            reasons.append({"code": "no_files", "message": "请先添加压缩包"})
        elif checked < len(files):
            reasons.append({"code": "checking", "message": "正在检查压缩包 %d/%d" % (checked, len(files))})
        if any(s == "unsupported" for s in statuses):
            reasons.append({"code": "unsupported", "message": "列表中存在当前环境无法处理的压缩包"})
        if files and checked == len(files) and not ready_items:
            reasons.append({"code": "no_processable", "message": "没有可处理的压缩包"})

        rule = int(conf.get("rule", 2))
        seq_mode = conf.get("seq_mode", "single")
        seq_max = None
        if rule == 1 and not str(conf.get("prefix", "")).strip():
            reasons.append({"code": "invalid_rule", "message": "请填写统一前缀"})
        elif rule == 2:
            try:
                seq_max = sequence.seq_limit(seq_mode)
                if len(files) > seq_max:
                    reasons.append({
                        "code": "sequence_overflow",
                        "message": "当前共有 %d 个压缩包，所选序列最多支持 %d 个" % (len(files), seq_max),
                    })
            except ValueError as e:
                reasons.append({"code": "invalid_rule", "message": str(e)})

        fmt = conf.get("fmt", "rar")
        if fmt not in ("zip", "7z", "rar"):
            reasons.append({"code": "invalid_format", "message": "当前输出格式无效"})
        elif fmt == "rar" and not archiver.rar_available():
            reasons.append({"code": "rar_unavailable", "message": "未检测到 WinRAR，RAR 输出不可用"})

        out_name = _normalise_output_name(conf.get("out_name", ""), fmt)
        if not out_name:
            reasons.append({"code": "invalid_name", "message": "请填写有效的输出文件名"})

        out_dir = str(conf.get("output_dir", "") or "").strip()
        required = None
        free = None
        if not out_dir or not os.path.isdir(out_dir) or not os.access(out_dir, os.W_OK):
            reasons.append({"code": "invalid_output_dir", "message": "请选择有效且可写的输出目录"})
        elif ready_items:
            if any(a.get("unpacked_size") is None for a in ready_items):
                reasons.append({"code": "space_unknown", "message": "无法估算所需磁盘空间"})
            else:
                unpacked = sum(int(a.get("unpacked_size") or 0) for a in ready_items)
                # v1.3：放宽预估——解压 1 份 + 输出按 30% 余量（原 v1.2 为 ×2 + 512MB，
                # 对高压缩比素材明显高估）。不可压缩内容极端场景若中途磁盘不足，
                # 由原子输出 + 清理兜底，不会留下半成品。
                required = math.ceil(unpacked * 1.3)
                try:
                    free = shutil.disk_usage(out_dir).free
                    if free < required:
                        reasons.append({
                            "code": "insufficient_space",
                            "message": "磁盘空间不足：需要 %.1f GB，可用 %.1f GB" % (
                                required / 2 ** 30, free / 2 ** 30),
                        })
                except OSError:
                    reasons.append({"code": "space_unknown", "message": "无法读取输出目录剩余空间"})

        return {
            "ready": not reasons,
            "reasons": reasons,
            "message": reasons[0]["message"] if reasons else "可以开始输出",
            "checked_count": checked,
            "total_count": len(files),
            "processable_count": len(ready_items),
            "skipped_count": len(skipped),
            "required_bytes": required,
            "free_bytes": free,
            "sequence_limit": seq_max,
            "rar_available": archiver.rar_available(),
        }

    def _push(self, fn, payload):
        """向前端推送事件：window.<fn>(payload)。"""
        try:
            self._window.evaluate_js("window.%s(%s)" % (fn, json.dumps(payload, ensure_ascii=False)))
        except Exception:
            pass

    def _check_cancel(self):
        if self._cancel.is_set():
            raise _Cancelled()

    @staticmethod
    def _locate(pkg_dir, rel):
        """按解压相对路径 O(1) 定位文件（extract 保持包内相对路径）。

        取代旧的 basename 全树扫描（O(n²)，1000+ 文件会明显卡顿），
        并修复子目录同名文件被误定位到第一个的问题。
        """
        parts = rel.replace("\\", "/").split("/")
        if not parts or any(p in ("", ".", "..") for p in parts) or ":" in parts[0]:
            return None  # 路径穿越/绝对路径守卫（extract 已过滤，此处双保险）
        p = os.path.join(pkg_dir, *parts)
        return p if os.path.isfile(p) else None

    def _run(self, conf):
        """后台任务流水线：预演 -> 冲突决策 -> 解压重命名 -> 打包。"""
        files = conf.get("files") or []
        rule = int(conf.get("rule", 2))
        prefix = conf.get("prefix", "")
        seq_mode = conf.get("seq_mode", "single")
        # v1.3：默认连接符 -（与 UI 默认一致；sep 不记忆，前端每次必传）
        sep = conf.get("sep", "-")
        keep_orig = bool(conf.get("keep_orig", True))
        fmt = conf.get("fmt", "rar")
        out_dir = conf.get("output_dir", "")
        out_name = _normalise_output_name(conf.get("out_name", "整合结果"), fmt)
        if fmt not in ("rar", "zip", "7z"):
            fmt = "rar"
        work = None
        temp_out = None
        last_pct = -1

        def _push_prog(pct, msg):
            """进度推送（按整数 pct 节流，避免上千文件时上千次 JS 桥往返）。"""
            nonlocal last_pct
            if pct != last_pct:
                last_pct = pct
                self._push("onProgress", {"pct": pct, "msg": msg})

        try:
            # 0. 最终分析与二次门控：前端状态只负责体验，Python 才是执行边界。
            analyses = []
            for i, fp in enumerate(files):
                self._check_cancel()
                analyses.append(self._analysis_for(fp))
                _push_prog(round((i + 1) / max(len(files), 1) * 10),
                           "正在确认 %d/%d…" % (i + 1, len(files)))
            gate = self._readiness(conf, analyses)
            if not gate["ready"]:
                self._push("onReadiness", gate)
                self._push("onError", gate["message"])
                return

            archives = [a.get("entries", []) if a.get("status") == "ready" else []
                        for a in analyses]
            bad = [a["name"] for a in analyses if str(a.get("status", "")).startswith("skip_")]

            # 1. 计算目标名（序列耗尽 / 空命名在此抛出）
            try:
                items = renamer.compute_targets(archives, rule, prefix, sep, keep_orig, seq_mode)
            except ValueError as e:
                self._push("onError", str(e))
                return
            conflicts = renamer.detect_conflicts(items)

            # 2. 文件名冲突交互
            if conflicts:
                payload = [{"target": t, "sources": [n for _, n in v]}
                           for t, v in conflicts.items()]
                self._conflict_evt.clear()
                self._conflict_choice = None
                self._push("onConflict", payload)
                self._conflict_evt.wait()
                choice = self._conflict_choice or "cancel"
                if choice == "cancel" or self._cancel.is_set():
                    self._push("onCancel", "任务已取消，临时文件已清理")
                    return
                items = self._apply_conflict(items, conflicts, rule, prefix,
                                             seq_mode, sep, keep_orig, choice)

            # 3. 输出路径冲突交互。默认生成新名称，覆盖必须由用户明确选择。
            out_path = os.path.join(out_dir, out_name + "." + fmt)
            allow_overwrite = False
            if os.path.exists(out_path):
                self._output_evt.clear()
                self._output_choice = None
                self._push("onOutputExists", {"path": out_path})
                self._output_evt.wait()
                choice = self._output_choice or "cancel"
                if choice == "cancel" or self._cancel.is_set():
                    self._push("onCancel", "任务已取消，原输出文件保持不变")
                    return
                if choice == "new":
                    out_path = _unique_output_path(out_path)
                elif choice == "overwrite":
                    allow_overwrite = True
                else:
                    self._push("onCancel", "任务已取消，原输出文件保持不变")
                    return

            # 4. 解压 + 重命名（按包分组，惰性解压）
            # 临时工作目录放输出目录同盘：大包展开可能数 GB，系统盘 %TEMP% 空间不足会失败
            work = tempfile.mkdtemp(prefix="整合工具_", dir=out_dir)
            groups = {}
            for idx, orig, target in items:
                groups.setdefault(idx, []).append((orig, target))

            done, total = 0, len(items)
            # v1.3：记录"整合成功"的源包（解压成功即算），供删除确认弹窗使用；
            # 坏包/加密/解压失败的不在列表，绝不进入删除候选。
            processed_sources = []
            for idx, pairs in groups.items():
                self._check_cancel()
                pkg_dir = os.path.join(work, "_pkg_%d" % idx)
                try:
                    archiver.extract_all(files[idx], pkg_dir)
                except Exception:
                    name = os.path.basename(files[idx])
                    if name not in bad:
                        bad.append(name)
                    shutil.rmtree(pkg_dir, ignore_errors=True)
                    continue
                processed_sources.append(files[idx])
                for orig, target in pairs:
                    self._check_cancel()
                    src_f = self._locate(pkg_dir, orig)
                    if src_f is None:
                        continue  # 异常情况（解压缺失）跳过
                    dst_f = _uniquify(os.path.join(work, target))
                    os.makedirs(os.path.dirname(dst_f), exist_ok=True)
                    shutil.move(src_f, dst_f)
                    done += 1
                    # 解压重命名 10-80%
                    _push_prog(10 + round(done / max(total, 1) * 70),
                               "解压重命名 %d/%d" % (done, total))
                # 丢弃未被目标清单消费的异常残留，避免 _pkg_* 被打进最终输出。
                shutil.rmtree(pkg_dir, ignore_errors=True)

            # 5. 打包（无可用文件时跳过打包）
            if done == 0:
                saved = save_cfg(conf)
                self._push("onDone", {
                    "packs": len(files), "files": 0, "bad": bad, "out": None,
                    "sources": [],  # v1.3：无可删文件
                    "config_warning": None if saved else "本次设置未能保存，下次启动不会记忆",
                })
                return

            def pack_prog(d, t):
                # 打包 80-100%（内部也走节流）
                _push_prog(80 + round(d / max(t, 1) * 20),
                           "正在打包 %s… %d%%" % (fmt, round(d / max(t, 1) * 100)))

            # 始终先写工具自己的临时输出，成功后再原子替换/改名。
            temp_out = os.path.join(out_dir, ".整合工具_%s.%s" % (uuid.uuid4().hex, fmt))
            try:
                packer.pack(work, temp_out, fmt, on_progress=pack_prog,
                            should_cancel=self._cancel.is_set)
            except packer.Cancelled:
                raise _Cancelled()

            # 若非明确覆盖且等待期间同名文件被其他程序创建，继续生成新名称。
            if not allow_overwrite and os.path.exists(out_path):
                out_path = _unique_output_path(out_path)
            os.replace(temp_out, out_path)
            temp_out = None

            # 6. 完成（记忆本次设置）
            saved = save_cfg(conf)
            self._push("onDone", {
                "packs": len(files), "files": done, "bad": bad, "out": out_path,
                "sources": processed_sources,  # v1.3：删除流程候选（仅成功整合的源包）
                "config_warning": None if saved else "本次设置未能保存，下次启动不会记忆",
            })
        except _Cancelled:
            self._push("onCancel", "已取消，临时文件已清理")
        except Exception as e:
            traceback.print_exc()
            self._push("onError", "执行失败：%s" % e)
        finally:
            if temp_out and os.path.exists(temp_out):
                try:
                    os.remove(temp_out)
                except OSError:
                    pass
            if work:
                packer.cleanup(work)

    @staticmethod
    def _apply_conflict(items, conflicts, rule, prefix, seq_mode, sep, keep_orig, choice):
        """按用户决策改写冲突条目：auto 加序号 / reseq 序列命名。"""
        conflict_set = set(conflicts.keys())
        out = []
        counter = {}
        for idx, orig, target in items:
            if target not in conflict_set:
                out.append((idx, orig, target))
                continue
            if choice == "auto":
                n = counter.get(target, 0)
                counter[target] = n + 1
                if n == 0:
                    out.append((idx, orig, target))
                else:
                    base, ext = os.path.splitext(target)
                    out.append((idx, orig, "%s_%d%s" % (base, n, ext)))
            elif choice == "reseq":
                pre = sequence.seq_at(seq_mode, idx)
                out.append((idx, orig, renamer.build_name(pre, sep, keep_orig,
                                                          os.path.basename(orig))))
            else:
                out.append((idx, orig, target))
        return out


def _setup_drop(window):
    """注册拖拽：文件/文件夹拖入窗口任意位置后推给前端合并列表。

    v1.1 起注册在 body 上（全局拖放）：使用 pywebview DOM API
    （EdgeChromium 平台经 WebView2 获取真实路径），
    平台不支持时静默降级（➕ 按钮添加仍可用）。
    """

    def on_drop(event):
        paths = []
        try:
            for f in event.get("dataTransfer", {}).get("files", []):
                p = f.get("pywebviewFullPath")
                if p:
                    paths.append(p)
        except Exception:
            pass
        if paths:
            try:
                window.evaluate_js("window.addDropped(%s)" % json.dumps(paths, ensure_ascii=False))
            except Exception:
                pass

    try:
        el = window.dom.body
        el.on("drop", DOMEventHandler(on_drop, prevent_default=True))
    except Exception:
        pass


def main():
    api = Api()
    window = webview.create_window(
        APP_TITLE,
        url=UI_PATH,
        js_api=api,
        width=1080,
        height=780,
        min_size=(900, 640),
    )
    api._window = window

    # 拖拽注册必须等页面加载完成后（DOM API 内部要 evaluate_js，
    # 在 webview.start() 之前调用会静默失败——旧代码的隐藏 bug）。
    window.events.loaded += lambda: _setup_drop(window)

    webview.start()


if __name__ == "__main__":
    main()
