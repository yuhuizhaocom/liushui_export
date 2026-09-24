"""导出结果的落盘汇总 + 目录名清理。

原先这两件事写在 core/main_gui.py 的 LiushuiApp 里, 只有开着 GUI 才能跑到, 一段
纯文件操作因此完全没测试。搬到这里后逻辑与日志文字都保持原样, 差别只是可以脱离
界面单测。
"""
import os
import re
import shutil
from datetime import datetime

# 浏览器还没下完时留下的临时后缀, 汇总时不能算成成品
INCOMPLETE_SUFFIXES = (".crdownload", ".tmp", ".part")


def sanitize_name(name, fallback=""):
    """清理路径非法字符, 避免商户名/平台名造成目录逃逸。

    空结果的兜底值由调用方决定(商户名要空串提示用户重填, profile 目录要 "default")。
    """
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip()
    return cleaned or fallback


def task_folder(start_date, end_date):
    """一次导出对应的日期目录名。"""
    return f"{start_date}_{end_date}"


def find_output_files(base_dir, platform_names, start_date, end_date):
    """列出这次导出产出的成品文件(绝对路径, 顺序稳定)。

    两种目录结构都认: 旧的是 平台/日期/文件, 新的是 平台/商户/日期/文件; 同一个
    文件被两种结构重复命中时只保留一份。
    """
    folder = task_folder(start_date, end_date)
    found = []
    for pname in platform_names:
        plat_dir = os.path.join(base_dir, pname)
        if not os.path.isdir(plat_dir):
            continue
        for sub in sorted(os.listdir(plat_dir)):
            sub_dir = os.path.join(plat_dir, sub)
            if not os.path.isdir(sub_dir):
                continue
            candidates = []
            if sub == folder:                       # 旧结构: 平台/日期
                candidates.append(sub_dir)
            task_dir = os.path.join(sub_dir, folder)  # 新结构: 平台/商户/日期
            if os.path.isdir(task_dir):
                candidates.append(task_dir)
            for d in candidates:
                for f in sorted(os.listdir(d)):
                    fp = os.path.join(d, f)
                    if not os.path.isfile(fp) or f.endswith(INCOMPLETE_SUFFIXES):
                        continue
                    found.append(fp)
    seen, unique = set(), []
    for fp in found:
        if fp not in seen:
            seen.add(fp)
            unique.append(fp)
    return unique


def copy_to_summary_dir(base_dir, platform_names, start_date, end_date,
                        log=None, now=None):
    """把这次导出的文件复制进 downloads/开始_结束_时间戳/, 返回目录路径。

    没有可汇总的文件时返回 None(调用方据此不再去打开文件夹)。
    """
    if not start_date or not end_date:
        return None
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    target = os.path.join(base_dir, f"{task_folder(start_date, end_date)}_{stamp}")
    copied = 0
    for fp in find_output_files(base_dir, platform_names, start_date, end_date):
        try:
            os.makedirs(target, exist_ok=True)
            shutil.copy2(fp, os.path.join(target, os.path.basename(fp)))
            copied += 1
        except Exception:
            pass   # 单个文件失败不影响其余, 更不能中断导出收尾
    if log:
        if copied:
            log(f"[汇总] 已将 {copied} 个导出文件复制到: {target}")
        else:
            log("[汇总] 未找到可汇总的导出文件,跳过打开文件夹")
    return target if copied else None
