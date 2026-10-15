# -*- coding: utf-8 -*-
"""序列前缀生成器：按包顺序生成递增前缀。

支持四种模式（与需求文档 3.4 序列模式选择器一致）：
    single  单字母   a~z           (1-26 个包)
    double  双字母   aa~zz         (1-676 个包)
    num     数字    1~999          (1-999 个包)
    numz    补零数字 001~999       (1-999 个包)
"""

MODES = ("single", "double", "num", "numz")

_LIMIT = {"single": 26, "double": 676, "num": 999, "numz": 999}


def seq_limit(mode):
    """返回该序列模式支持的包数量上限。"""
    _check_mode(mode)
    return _LIMIT[mode]


def seq_at(mode, index):
    """返回第 index 个包的前缀（index 从 0 开始，即列表第 1 行）。

    超过序列上限时抛出 ValueError，调用方应在预演阶段拦截。
    """
    _check_mode(mode)
    if index < 0:
        raise ValueError("index 不能为负数: %r" % index)

    if mode == "single":
        if index >= _LIMIT["single"]:
            raise ValueError("序列耗尽：单字母模式最多 26 个包")
        return chr(ord("a") + index)

    if mode == "double":
        if index >= _LIMIT["double"]:
            raise ValueError("序列耗尽：双字母模式最多 676 个包")
        return chr(ord("a") + index // 26) + chr(ord("a") + index % 26)

    if mode == "num":
        if index >= _LIMIT["num"]:
            raise ValueError("序列耗尽：数字模式最多 999 个包")
        return str(index + 1)

    if mode == "numz":
        if index >= _LIMIT["num"]:
            raise ValueError("序列耗尽：补零数字模式最多 999 个包")
        return "%03d" % (index + 1)

    raise ValueError("未知序列模式: %r" % mode)


def _check_mode(mode):
    if mode not in MODES:
        raise ValueError("未知序列模式: %r（可选 %s）" % (mode, "/".join(MODES)))
