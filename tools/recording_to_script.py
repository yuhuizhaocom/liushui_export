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
    3. 输出可粘贴进 platforms/xxx/export.py 的骨架代码(带 browser.snapshot 标记位)

注意: 骨架仅是起点,实际平台脚本可能需要手工调整(日期选择器/弹窗等复杂控件)。
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC_DIR = os.path.join(ROOT_DIR, "recordings")


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
    """把步骤转成 export.py 骨架代码字符串"""
    lines = []
    lines.append('"""')
    lines.append(f"{platform_name}导出脚本(骨架,由录制生成)")
    lines.append("")
    lines.append("此文件由 tools/recording_to_script.py 从操作录制 JSONL 自动生成,")
    lines.append("仅是起点,需根据实际页面表现手工调整(尤其日期选择器、弹窗、动态加载)。")
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
    lines.append("    def export(self, browser, start_date, end_date):")
    lines.append("        try:")
    lines.append("            return self._export_inner(browser, start_date, end_date)")
    lines.append("        except Exception as e:")
    lines.append('            browser._log(f"导出异常: {e}", "error")')
    lines.append('            browser.snapshot_on_failure("导出异常")')
    lines.append('            return "failed"')
    lines.append("")
    lines.append("    def _export_inner(self, browser, start_date, end_date):")
    lines.append("        # ===== 1. 打开导出页面 =====")
    lines.append("        browser.navigate(self.export_url)")
    lines.append("        browser.sleep(3)")
    lines.append("        browser.close_popup()")
    lines.append("")
    lines.append("        # ===== 2. 设置日期范围 =====")
    lines.append('        # TODO: 根据页面实际日期组件调整(fill_placeholder / 键盘输入 / el-date-range-picker)')
    lines.append("        # browser.fill_placeholder('开始日期', start_date[:10])")
    lines.append("        # browser.fill_placeholder('结束日期', end_date[:10])")
    lines.append("        browser.snapshot('设置日期后')")
    lines.append("")
    lines.append("        # ===== 3. 查询 =====")
    lines.append('        browser.click_text("查询", exact=True)')
    lines.append("        browser.sleep(5)")
    lines.append("        browser.snapshot('查询后')")
    lines.append("")
    lines.append("        # ===== 4. 录制步骤回放 =====")

    seen = set()   # 去重(同一选择器+type 只保留首次,避免录制中重复点击污染)
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
                lines.append(f'        browser.click_text("{t}")')
            else:
                lines.append("        # TODO: 未提取到稳定选择器,手工补全")
        elif typ == "change":
            val_note = "TODO_VALUE"
            if ph:
                lines.append(f'        browser.fill_placeholder("{ph}", {val_note})')
            elif sel:
                lines.append(f'        browser.fill_selector(\'{sel}\', {val_note})')
            else:
                lines.append("        # TODO: 未提取到稳定选择器,手工补全输入")
        lines.append("        browser.sleep(1)")
    lines.append("")
    lines.append("        # ===== 5. 点击导出/下载 =====")
    lines.append("        browser.begin_wait_download()")
    lines.append('        # TODO: 点击页面上的"导出"按钮/链接')
    lines.append("        path = browser.wait_download(timeout=120)")
    lines.append('        return "success" if path else "manual"')
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="录制 JSONL → export.py 骨架")
    ap.add_argument("jsonl", nargs="?", help="录制文件路径")
    ap.add_argument("--out", help="输出文件路径(默认打印到 stdout)")
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
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(code)
        print(f"已生成: {args.out}")
    else:
        print(code)


if __name__ == "__main__":
    main()
