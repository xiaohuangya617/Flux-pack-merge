# -*- coding: utf-8 -*-
"""命名规则引擎：计算目标文件名并检测冲突。

规则一（统一前缀）：所有文件 = 自定义前缀 + 连接符 + 原文件名
规则二（序列前缀）：第 i 个包 = 序列前缀(seq_at) + 连接符 + 原文件名
规则零（不重命名）：原文件名直出（keep_orig 关闭时禁止此规则）

原文件名开关（keep_orig=False）时，只保留前缀部分，连接符无对象自动忽略。

Windows 非法字符（\\ / : * ? " < > | 及控制符）统一替换为 _，
预演与执行共用同一计算路径，保证目标名一致。
"""

import os
import re

from core.sequence import seq_at

_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize(name):
    """清洗文件名中的 Windows 非法字符。"""
    return _ILLEGAL.sub("_", name)


def build_name(prefix, sep, keep_orig, orig_name):
    """单个文件的目标名（已清洗非法字符）。"""
    if keep_orig:
        return sanitize(prefix + sep + orig_name)
    return sanitize(prefix)


def compute_targets(archives, rule, prefix, sep, keep_orig, seq_mode="single"):
    """计算全部目标文件名。

    参数：
        archives : list[list[str]]，每个元素是一个包内的全部文件相对路径
                   （可含子目录，命名只取 basename，平铺输出语义）
        rule     : 1=统一前缀  2=序列前缀  0=不重命名
        prefix   : 规则一的自定义前缀
        sep      : 连接符（"" / "_" / "-"）
        keep_orig: 是否保留原文件名（开关，默认 True）
        seq_mode : 规则二的序列模式（见 sequence.MODES）

    返回：
        items : list[(archive_idx, orig_relpath, target_name)]，按包顺序拍平
    抛出：
        ValueError —— 命名结果为空（未选规则且关闭原文件名）、序列耗尽等
    """
    items = []
    for i, files in enumerate(archives):
        if rule == 1:
            pre = prefix
        elif rule == 2:
            pre = seq_at(seq_mode, i)  # 序列耗尽在此抛出，调用方预演拦截
        else:
            pre, sep = "", ""

        if not keep_orig and pre == "":
            raise ValueError("命名结果为空：未选择任何规则且未保留原文件名")

        for f in files:
            items.append((i, f, build_name(pre, sep, keep_orig, os.path.basename(f))))
    return items


def detect_conflicts(items):
    """检测目标名冲突。

    返回 {target_name: [(archive_idx, orig_name), ...]}，仅含出现超过一次的目标名。
    """
    seen = {}
    for idx, orig, target in items:
        seen.setdefault(target, []).append((idx, orig))
    return {t: v for t, v in seen.items() if len(v) > 1}
