# -*- coding: utf-8 -*-
"""解压器：zip / 7z / rar 三种后端统一接口。

接口约定（全部返回/操作"包内相对路径"，正斜杠分隔）：
    list_entries(src)              -> list[str]   包内全部文件（非目录）
    extract_all(src, dest_dir)     -> None        解压到 dest_dir（保持相对路径）
    test(src)                      -> bool        完整性校验（预演阶段坏包检测）

rar 后端依赖本机 WinRAR 命令行（Rar.exe），路径由 find_rar() 探测。
中文文件名处理：zip 兼容 GBK（cp437->gbk 回退），rar 输出按 gbk 解码回退 utf-8。
"""

import os
import re
import shutil
import subprocess
import zipfile

SUPPORTED_EXTS = (".zip", ".rar", ".7z")


def is_archive(path):
    """判断路径是否为受支持的压缩包。"""
    return os.path.isfile(path) and path.lower().endswith(SUPPORTED_EXTS)


def find_rar():
    """探测 WinRAR 命令行路径（依次常见位置 + PATH）。"""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", "")
    candidates = [
        os.path.join(pf, "WinRAR", "Rar.exe"),
        os.path.join(pf86, "WinRAR", "Rar.exe"),
        r"C:\Program Files\WinRAR\Rar.exe",
        "Rar.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
        if os.path.basename(c).lower() == "rar.exe":
            found = shutil.which(c)
            if found:
                return found
    return None


def rar_available():
    """返回当前环境是否具备可执行的 Rar.exe。"""
    return bool(find_rar())


def _safe_rel(name):
    """过滤路径穿越，返回规范化相对路径；非法返回 None。"""
    n = name.replace("\\", "/")
    if n.startswith("/") or ":" in n.split("/")[0]:
        return None
    parts = []
    for p in n.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            return None
        parts.append(p)
    return "/".join(parts) if parts else None


def _zip_decode_name(info):
    """zip 文件名解码：UTF-8 标志位判断，否则 cp437->gbk 回退。"""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _run(cmd):
    """执行命令，返回 (returncode, stdout_bytes)。"""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out, err = proc.communicate()
    return proc.returncode, out, err


# ---------------------------------------------------------------- zip
def _zip_entries(src):
    out = []
    with zipfile.ZipFile(src) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            rel = _safe_rel(_zip_decode_name(info))
            if rel is not None:
                out.append(rel)
    return out


def _zip_extract(src, dest_dir):
    """流式解压 zip：逐文件 copyfileobj，避免大文件全量读入内存。

    1GB 级单文件包若用 z.read() 会瞬时吃 1GB+ 内存，此处固定 1MB 缓冲。
    """
    with zipfile.ZipFile(src) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            rel = _safe_rel(_zip_decode_name(info))
            if rel is None:
                continue
            target = os.path.join(dest_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src_f, open(target, "wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f, 1024 * 1024)


def _file_names(names):
    """从条目列表中过滤目录项（裸名目录无结尾斜杠，用前缀探测剔除）+ 路径穿越过滤。"""
    raw = []
    for n in names:
        if n.endswith("/"):
            continue
        rel = _safe_rel(n)
        if rel is not None:
            raw.append(rel)
    return [n for n in raw if not any(n + "/" in o for o in raw)]


# ---------------------------------------------------------------- 7z
def _seven_entries(src):
    import py7zr

    with py7zr.SevenZipFile(src, "r") as z:
        return _file_names(z.getnames())


def _seven_extract(src, dest_dir):
    import py7zr

    with py7zr.SevenZipFile(src, "r") as z:
        z.extract(path=dest_dir)


# ---------------------------------------------------------------- rar
def _rar_entries(src, rar_path):
    if not rar_path:
        return []
    code, out, _ = _run([rar_path, "lb", src])
    if code != 0:
        return []
    text = _decode_rar_output(out)
    raw = [ln.strip() for ln in text.splitlines() if ln.strip()]
    raw_norm = [ln.replace("\\", "/") for ln in raw]  # rar 输出用 \ 分隔，统一为正斜杠
    names = []
    for i, line in enumerate(raw):
        if line.endswith("\\") or line.endswith("/"):
            continue  # 显式目录项（以 \ 结尾）
        rel = _safe_rel(line)
        if rel is None:
            continue
        # Rar.exe lb 的目录项是裸名：若它是其他条目的路径前缀，视为目录跳过
        if any(rel + "/" in other for other in raw_norm):
            continue
        names.append(rel)
    return names


def _decode_rar_output(data):
    """Rar.exe 输出解码：GBK 优先（中文系统 OEM 码页），失败回退 utf-8。"""
    for enc in ("gbk", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _rar_extract(src, dest_dir, rar_path):
    if not rar_path:
        return False
    os.makedirs(dest_dir, exist_ok=True)
    code, _, _ = _run([rar_path, "x", "-y", "-o+", src, dest_dir + os.sep])
    return code == 0


# ---------------------------------------------------------------- 分发
def list_entries(src):
    """读取压缩包内全部文件相对路径。"""
    ext = os.path.splitext(src)[1].lower()
    if ext == ".zip":
        return _zip_entries(src)
    if ext == ".7z":
        return _seven_entries(src)
    if ext == ".rar":
        return _rar_entries(src, find_rar())
    return []


def _rar_unpacked_size(src, rar_path):
    """从 Rar.exe 的 l 汇总行读取未压缩总大小。

    -c- 关闭彩色/交互输出；汇总行是纯数字，即使系统语言改变也可解析。
    解析失败返回 None，调用方必须把“未知”当作不可就绪，而不是当作 0。
    """
    if not rar_path:
        return None
    code, out, _ = _run([rar_path, "l", "-c-", src])
    if code != 0:
        return None
    text = _decode_rar_output(out)
    # 文件明细行含属性字段，汇总行只有“总大小 总文件数”，取最后一行匹配。
    matches = re.findall(r"(?m)^\s*([0-9][0-9,]*)\s+([0-9][0-9,]*)\s*$", text)
    if not matches:
        return None
    try:
        return int(matches[-1][0].replace(",", ""))
    except ValueError:
        return None


def analyze(src):
    """分析压缩包并返回就绪门控所需的结构化结果。

    返回字段：path/name/size/count/unpacked_size/status/entries。
    status 为 ready、skip_empty、skip_locked、skip_broken 或 unsupported。
    """
    result = {
        "path": src,
        "name": os.path.basename(src),
        "size": 0,
        "count": -1,
        "unpacked_size": None,
        "status": "skip_broken",
        "entries": [],
    }
    try:
        result["size"] = os.path.getsize(src)
    except OSError:
        result["status"] = "skip_broken"
        return result

    ext = os.path.splitext(src)[1].lower()
    if ext not in SUPPORTED_EXTS:
        result["status"] = "unsupported"
        return result

    try:
        if ext == ".zip":
            with zipfile.ZipFile(src) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                result["entries"] = [
                    rel for rel in (_safe_rel(_zip_decode_name(i)) for i in infos)
                    if rel is not None
                ]
                result["count"] = len(result["entries"])
                result["unpacked_size"] = sum(i.file_size for i in infos)
                if any(i.flag_bits & 0x1 for i in infos):
                    result["status"] = "skip_locked"
                    return result
                if z.testzip() is not None:
                    result["status"] = "skip_broken"
                    return result
        elif ext == ".7z":
            import py7zr

            with py7zr.SevenZipFile(src, "r") as z:
                result["entries"] = _file_names(z.getnames())
                result["count"] = len(result["entries"])
                result["unpacked_size"] = z.archiveinfo().uncompressed
                if z.needs_password():
                    result["status"] = "skip_locked"
                    return result
                z.test()
        elif ext == ".rar":
            rar_path = find_rar()
            if not rar_path:
                result["status"] = "unsupported"
                return result
            result["entries"] = _rar_entries(src, rar_path)
            result["count"] = len(result["entries"])
            result["unpacked_size"] = _rar_unpacked_size(src, rar_path)
            if result["unpacked_size"] is None:
                result["status"] = "skip_broken"
                return result
            code, _, err = _run([rar_path, "t", "-p-", "-idq", src])
            if code != 0:
                # Rar.exe 对无密码加密包返回“密码不正确”，统一按锁定包展示。
                result["status"] = "skip_locked" if code == 11 else "skip_broken"
                return result
        else:
            result["status"] = "unsupported"
            return result
    except Exception as e:
        result["status"] = "skip_locked" if "password" in str(e).lower() else "skip_broken"
        return result

    if not result["entries"]:
        result["status"] = "skip_empty"
    else:
        result["status"] = "ready"
    return result


def extract_all(src, dest_dir):
    """解压全部文件到 dest_dir（保持包内相对路径）。"""
    ext = os.path.splitext(src)[1].lower()
    if ext == ".zip":
        _zip_extract(src, dest_dir)
    elif ext == ".7z":
        _seven_extract(src, dest_dir)
    elif ext == ".rar":
        ok = _rar_extract(src, dest_dir, find_rar())
        if not ok:
            raise RuntimeError("Rar.exe 解压失败：%s" % src)
    else:
        raise ValueError("不支持的格式：%s" % src)


def test(src):
    """完整性校验。返回 True 表示包完好（预演阶段用于坏包/加密包检测）。"""
    ext = os.path.splitext(src)[1].lower()
    try:
        if ext == ".zip":
            with zipfile.ZipFile(src) as z:
                if any(i.flag_bits & 0x1 for i in z.infolist()):
                    return False  # 加密 zip（传统加密标志），无密码跳过
                return z.testzip() is None
        if ext == ".7z":
            import py7zr

            with py7zr.SevenZipFile(src, "r") as z:
                if z.needs_password():
                    return False  # 加密 7z：无密码跳过
                z.test()  # 校验失败抛异常，成功无返回（None）
            return True
        if ext == ".rar":
            code, _, _ = _run([find_rar(), "t", src])
            return code == 0  # 加密 rar 无密码时 rar t 返回非 0
    except Exception:
        return False
    return False


def estimate_unpacked_size(paths):
    """估算一批压缩包解压后的总大小（字节），供磁盘空间预检。

    仅 zip/7z 可从中央目录/头拿到未压缩总大小（读取开销小）；
    rar 拿不到返回 0（跳过预检，由写入端 OSError 兜底）。
    失败静默返回 0，不阻塞主流程。
    """
    total = 0
    for p in paths or []:
        ext = os.path.splitext(p)[1].lower()
        try:
            if ext == ".zip":
                with zipfile.ZipFile(p) as z:
                    total += sum(i.file_size for i in z.infolist())
            elif ext == ".7z":
                import py7zr

                with py7zr.SevenZipFile(p, "r") as z:
                    total += z.archiveinfo().uncompressed
            elif ext == ".rar":
                size = _rar_unpacked_size(p, find_rar())
                if size is not None:
                    total += size
        except Exception:
            pass
    return total
