# -*- coding: utf-8 -*-
"""JSON 配置读写：记忆用户偏好，并区分冻结资源与可写数据目录。"""

import json
import os
import sys
import tempfile

_DEFAULTS = {
    "config_version": 2,
    "output_dir": "",
    # v1.3：out_name（输出文件名）与 sep（连接符）不记忆——
    # 每次启动回到稳定默认，避免上次任务的选择"复活"。
    "fmt": "zip",          # zip（默认） / rar（需 WinRAR） / 7z
    "rule": 2,             # 0=不重命名 1=统一前缀 2=序列前缀
    "prefix": "",
    "seq_mode": "single",
    "keep_orig": True,
}

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if getattr(sys, "frozen", False):
    DATA_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    DATA_DIR = PROJECT_DIR

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
_FALLBACK_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "压缩包整合工具", "v1.3"
)
_LAST_SAVE_ERROR = None


def _candidate_paths():
    """返回主配置和用户目录回退路径，去重后保持优先级。"""
    paths = [CONFIG_PATH, os.path.join(_FALLBACK_DIR, "config.json")]
    out = []
    for path in paths:
        path = os.path.abspath(path)
        if path not in out:
            out.append(path)
    return out


def load():
    """读取配置；文件缺失或损坏时返回默认值。"""
    cfg = dict(_DEFAULTS)
    for path in _candidate_paths():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key in _DEFAULTS:
                    if key in data:
                        cfg[key] = data[key]
                break
        except (OSError, ValueError):
            continue
    # 旧配置没有版本字段，读取时补齐；不覆盖用户已经明确选择的规则。
    cfg["config_version"] = _DEFAULTS["config_version"]
    return cfg


def save(cfg):
    """写入配置（自动合并缺失字段，保持默认键完整）。"""
    global _LAST_SAVE_ERROR
    merged = {key: cfg.get(key, default) for key, default in _DEFAULTS.items()}
    merged["config_version"] = _DEFAULTS["config_version"]
    last_error = None
    for path in _candidate_paths():
        tmp = None
        try:
            directory = os.path.dirname(path)
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".config_", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            _LAST_SAVE_ERROR = None
            return True
        except OSError as e:
            last_error = e
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    _LAST_SAVE_ERROR = str(last_error) if last_error else "未知错误"
    return False


def last_save_error():
    """返回最近一次配置写入错误，成功或尚未写入时返回 None。"""
    return _LAST_SAVE_ERROR
