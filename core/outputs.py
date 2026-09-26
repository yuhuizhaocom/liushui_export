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


# 日期目录最多在平台目录下面第几层: 平台/商户/子商户/日期 = 2。
# 再深就不认了 —— 递归到底会把"历史/"这类归档子目录也卷进来, 一份文件被算两次。
MAX_TASK_DEPTH = 2


def _iter_task_dirs(plat_dir, folder, depth=0):
    """在平台目录下找名为 `folder` 的日期目录, 最多下探 MAX_TASK_DEPTH 层。

    三种结构都认: 平台/日期(旧)、平台/商户/日期、平台/商户/子商户/日期。
    找到就不再往下走: 日期目录里面的 `历史/` 是同一任务的旧版本, 不该当成另一份成品。
    """
    try:
        names = sorted(os.listdir(plat_dir))
    except OSError:
        return
    for name in names:
        path = os.path.join(plat_dir, name)
        if not os.path.isdir(path):
            continue
        if name == folder:
            yield path
        elif depth < MAX_TASK_DEPTH:
            for hit in _iter_task_dirs(path, folder, depth + 1):
                yield hit


def find_output_files(base_dir, platform_names, start_date, end_date):
    """列出这次导出产出的成品文件(绝对路径, 顺序稳定)。

    同一个文件被多种层级重复命中时只保留一份。
    """
    folder = task_folder(start_date, end_date)
    found = []
    for pname in platform_names:
        plat_dir = os.path.join(base_dir, pname)
        if not os.path.isdir(plat_dir):
            continue
        for task_dir in _iter_task_dirs(plat_dir, folder):
            for f in sorted(os.listdir(task_dir)):
                fp = os.path.join(task_dir, f)
                if not os.path.isfile(fp) or f.endswith(INCOMPLETE_SUFFIXES):
                    continue
                found.append(fp)
    seen, unique = set(), []
    for fp in found:
        if fp not in seen:
            seen.add(fp)
            unique.append(fp)
    return unique


def summary_name(task_path, used):
    """汇总目录里该用哪个文件名。

    默认就是原名。只有"同一个汇总目录里已经有同名文件"时才加一层前缀(取它上面那级目录名,
    也就是商户或子商户) —— 统一命名时文件名里本来就带这些段, 不会撞;会撞的是"原始文件名"
    模式: 两个子商户导出的原始文件名一模一样, 默默覆盖等于把一份账单弄丢。
    """
    name = os.path.basename(task_path)
    if name not in used:
        used.add(name)
        return name
    parent = _owner_segment(task_path)
    for candidate in ((f"{parent}_{name}" if parent else ""), f"{parent}_{_mtime_tag(task_path)}_{name}"):
        if candidate and candidate not in used:
            used.add(candidate)
            return candidate
    used.add(name)
    return name


# 日期区间的目录名形状与 browser/outputs 里的 task_folder 一致。
_DATE_FOLDER = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}$")


def _owner_segment(task_path):
    """从文件往上找第一个"不是日期区间"的目录名 —— 也就是商户或子商户那一级。

    直接取父目录是不对的: 成品都躺在日期目录里, 前缀会变成一串日期, 谁都看不出是哪份账单。
    """
    for seg in reversed(os.path.abspath(task_path).split(os.sep)[:-1]):
        if seg and not _DATE_FOLDER.match(seg):
            return seg
    return ""


def _mtime_tag(path):
    """文件修改时刻, 读不到就返回 0 —— 取名这种事不该把整次汇总打断。"""
    try:
        return "%.0f" % os.path.getmtime(path)
    except OSError:
        return "0"


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
    used = set()
    for fp in find_output_files(base_dir, platform_names, start_date, end_date):
        try:
            os.makedirs(target, exist_ok=True)
            shutil.copy2(fp, os.path.join(target, summary_name(fp, used)))
            copied += 1
        except Exception:
            used.discard(os.path.basename(fp))   # 单文件失败不影响其余, 更不能中断导出收尾
    if log:
        if copied:
            log(f"[汇总] 已将 {copied} 个导出文件复制到: {target}")
        else:
            log("[汇总] 未找到可汇总的导出文件,跳过打开文件夹")
    return target if copied else None
