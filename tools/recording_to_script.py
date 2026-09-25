"""
录制 JSONL → export.py 骨架生成器

用法:
    python tools/recording_to_script.py <recordings/xxx.jsonl> [--out platforms/xxx/export_skeleton.py]
    python tools/recording_to_script.py --latest        # 用 recordings 目录最新的一份
    python tools/recording_to_script.py --list          # 列出所有录制文件

工作原理:
    1. 读取 browser.enable_action_trace() 写出的 JSONL(每行一条 click/change 步骤)
    2. 把元素信息(tag/cls/id/text/ph/name)翻译成稳定的定位选择器
       - 有 id:           #id
       - 有 placeholder:   按 placeholder 填写
       - 有 class+text:    .cls:has-text("...")  (element-ui 常见结构)
       - 兜底:            按文本点击
    3. 输出可粘贴进 platforms/xxx/export.py 的骨架代码: 直接按 PlatformBase 的骨架
       钩子生成(set_date_range / trigger_export 覆盖 + run_standard_flow 收尾)

注意: 骨架仅是起点,实际平台脚本可能需要手工调整(日期选择器/弹窗等复杂控件)。
      --out 默认**拒绝覆盖**已存在的文件(默认落点正是 loader 导入的线上脚本),
      确认要覆盖用 --force。
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC_DIR = os.path.join(ROOT_DIR, "recordings")

# 基类骨架收尾点击的文字(PlatformBase.download_export_file 的默认 label)。
# 生成器要把录制里同名的那一步去掉, 否则"下载"会被点两次。
DOWNLOAD_LABEL = "下载"


def list_recordings():
    """列出所有录制文件(按修改时间倒序)"""
    if not os.path.isdir(REC_DIR):
        return []
    files = glob.glob(os.path.join(REC_DIR, "*.jsonl"))
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    return files


def load_records(path):
    """读取 JSONL,返回步骤列表"""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                pass
    return records


def first_css_class(cls):
    """取 className 的第一个非空 class 作为选择器(避免过长的多 class 字符串)"""
    if not cls:
        return ""
    # 取第一个看起来稳定的 class(避开 ad-hoc 的样式类如 active/selected/...)
    for c in cls.split():
        c = c.strip()
        if c and not any(skip in c.lower() for skip in ("active", "selected", "hover",
                                                         "focus", "disabled", "loading")):
            return c
    return ""


def gen_selector(info):
    """根据元素信息生成最稳定的 CSS 选择器(回退到文本)
    返回 (selector, hint): selector 可空(用 hint 文本点击)"""
    tag = (info.get("tag") or "").lower()
    cls = info.get("cls") or ""
    el_id = info.get("id") or ""
    text = (info.get("text") or "").strip()
    ph = info.get("ph") or ""

    # 1) 有 id 最稳
    if el_id:
        return f"#{el_id}", ""
    # 2) placeholder → 不用选择器,直接走 fill_placeholder
    if ph:
        return "", f"placeholder={ph}"
    # 3) 第一个稳定 class + tag
    c = first_css_class(cls)
    if c and tag:
        sel = f"{tag}.{c}"
        # 若有简短文本,加 :has-text 提高精度(element-ui 这类组件常见)
        if text and len(text) <= 12:
            sel += f':has-text("{text}")'
        return sel, ""
    # 4) 兜底: 纯文本点击
    if text:
        return "", f'text="{text}"'
    return "", ""


def render_skeleton(records, platform_name="某平台", class_name="XxxExporter", platform_key="xxx"):
    """把步骤转成 export.py 骨架代码字符串

    输出直接用 PlatformBase 的骨架钩子(run_standard_flow + set_date_range/
    trigger_export 覆盖), 而不是再抄一遍"导航→等待→关弹窗→begin_wait_download→
    wait_download" —— 那套收尾各平台已经飘过(60/90/120 秒都写过), 由基类统一。
    """
    lines = []
    lines.append('"""')
    lines.append(f"{platform_name}导出脚本(骨架,由录制生成)")
    lines.append("")
    lines.append("此文件由 tools/recording_to_script.py 从操作录制 JSONL 自动生成。")
    lines.append('录制只看得见"点了什么", 看不见"点完之后页面变成什么样", 因此以下位置')
    lines.append("必须手工核对: 日期组件的真实交互、异步生成报表的等待、下载按钮所在的列表行。")
    lines.append('"""')
    lines.append("")
    lines.append("from core.platform_base import PlatformBase")
    lines.append("")
    lines.append("")
    lines.append(f"class {class_name}(PlatformBase):")
    lines.append(f'    key = "{platform_key}"')
    lines.append(f'    name = "{platform_name}"')
    lines.append('    login_url = "https://..."   # TODO: 填登录页地址')
    lines.append('    export_url = "https://..."  # TODO: 填导出页地址')
    lines.append('    guide = "TODO: 填操作指引"')
    lines.append("")
    lines.append("    # 打开导出页、等页面稳定、收尾的下载捕获都用基类实现;")
    lines.append("    # 若该平台报表生成较慢, 放开下一行调大等待秒数即可。")
    lines.append("    # DOWNLOAD_TIMEOUT_S = 90")
    lines.append("")
    lines.append("    def set_date_range(self, browser, start_date, end_date):")
    lines.append('        """TODO: 按页面实际的日期组件改写(下面是最常见的 placeholder 写法)。')
    lines.append('        必须返回 bool: 骨架靠它判断日期是否真的填进去了, 返回 False 时')
    lines.append('        不会继续点查询/导出(否则会拿到页面默认区间的账单)。"""')
    lines.append('        ok_start = browser.fill_placeholder("开始日期", start_date[:10])')
    lines.append('        ok_end = browser.fill_placeholder("结束日期", end_date[:10])')
    lines.append("        browser.sleep(1)")
    lines.append("        return bool(ok_start) and bool(ok_end)")
    lines.append("")
    lines.append("    def trigger_export(self, browser, start_date, end_date):")
    lines.append('        """查询 + 导出(基类骨架的第 3 步)。"""')
    lines.append('        browser.click_text("查询", exact=True)')
    lines.append("        browser.sleep(5)")

    seen = set()   # 去重(同一选择器+type 只保留首次,避免录制中重复点击污染)
    skipped_download = False   # 录制里是否出现过基类收尾要点的"下载"
    for r in records:
        typ = r.get("type", "")
        info = r.get("element", {}) or {}
        sel, hint = gen_selector(info)
        key = (typ, sel, hint)
        if key in seen:
            continue
        seen.add(key)
        text = (info.get("text") or "").strip()
        ph = info.get("ph") or ""

        if typ == "click":
            if sel:
                lines.append(f'        browser.click_selector(\'{sel}\')')
            elif hint.startswith("text="):
                t = hint.split("=", 1)[1].strip('"')
                if t == DOWNLOAD_LABEL:
                    # 基类收尾 download_export_file 会点它; 这里再点一次会点到列表里
                    # 另一行的下载按钮, 所以录制到的这一步必须丢掉
                    skipped_download = True
                    continue
                lines.append(f'        browser.click_text("{t}")')
            else:
                lines.append("        # TODO: 未提取到稳定选择器,手工补全")
        elif typ == "change":
            # 占位值写成字符串字面量: 骨架要能直接跑通(裸 TODO_VALUE 会 NameError)
            val_note = '"TODO_VALUE"'
            if ph:
                lines.append(f'        browser.fill_placeholder("{ph}", {val_note})')
            elif sel:
                lines.append(f'        browser.fill_selector(\'{sel}\', {val_note})')
            else:
                lines.append("        # TODO: 未提取到稳定选择器,手工补全输入")
        lines.append("        browser.sleep(1)")
    if skipped_download:
        lines.append(f'        # 录制里的"{DOWNLOAD_LABEL}"点击由基类 download_export_file 负责, 未在此重复')
    lines.append('        browser.snapshot("触发导出后")')
    lines.append("")
    lines.append("    def export(self, browser, start_date, end_date):")
    lines.append("        try:")
    lines.append("            # 基类骨架: 打开导出页 → set_date_range → trigger_export → 等下载")
    lines.append("            return self.run_standard_flow(browser, start_date, end_date)")
    lines.append("        except Exception as e:")
    lines.append('            browser._log(f"导出异常: {e}", "error")')
    lines.append('            browser.snapshot_on_failure("导出异常")')
    lines.append('            return "failed"')
    lines.append("")
    return "\n".join(lines)


def write_skeleton(path, code, force=False):
    """写出骨架; 目标已存在时必须显式 force 才覆盖。

    生成器默认提示的落点就是 platforms/xxx/export.py —— 那正是 loader 导入的线上脚本,
    无条件 "w" 会把上面手工调好的日期组件/弹窗处理整个抹掉。
    """
    if os.path.exists(path) and not force:
        raise FileExistsError(
            f"目标已存在: {path}\n"
            f"覆盖会丢掉手工调整过的逻辑; 确认要覆盖请加 --force, 或先输出到别处对比。")
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


def main():
    ap = argparse.ArgumentParser(description="录制 JSONL → export.py 骨架")
    ap.add_argument("jsonl", nargs="?", help="录制文件路径")
    ap.add_argument("--out", help="输出文件路径(默认打印到 stdout;目标已存在需 --force)")
    ap.add_argument("--force", action="store_true",
                    help="允许覆盖已存在的目标文件(通常是手工调过的 platforms/xxx/export.py)")
    ap.add_argument("--latest", action="store_true", help="使用 recordings/ 最新一份")
    ap.add_argument("--list", action="store_true", help="列出所有录制文件")
    ap.add_argument("--platform", default="某平台", help="平台显示名")
    ap.add_argument("--class-name", default="XxxExporter", help="导出类名")
    ap.add_argument("--key", default="xxx", help="平台唯一标识")
    args = ap.parse_args()

    if args.list:
        files = list_recordings()
        if not files:
            print("(暂无录制文件,先用'打开商户'按钮操作一遍)")
            return
        for f in files:
            mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M:%S")
            print(f"{mtime}  {os.path.basename(f)}")
        return

    path = args.jsonl
    if args.latest or not path:
        files = list_recordings()
        if not files:
            print("找不到录制文件,先用'打开商户'按钮操作一遍", file=sys.stderr)
            sys.exit(1)
        path = files[0]
        print(f"# 使用最新录制: {path}", file=sys.stderr)

    if not os.path.isfile(path):
        print(f"文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    records = load_records(path)
    if not records:
        print("录制文件为空", file=sys.stderr)
        sys.exit(1)

    code = render_skeleton(records, platform_name=args.platform,
                           class_name=args.class_name, platform_key=args.key)
    if args.out:
        try:
            write_skeleton(args.out, code, force=args.force)
        except OSError as e:
            print(e, file=sys.stderr)
            sys.exit(1)
        print(f"已生成: {args.out}")
    else:
        print(code)


if __name__ == "__main__":
    main()
