"""
平台管理模块 - 客户端内新增/编辑/调试平台脚本
- 骨架生成与 key 校验为纯函数,便于单元测试
- PlatformManagerDialog / DebugDialog 为 Tkinter UI 壳(后续 Task 补充)
"""
import json
import os
import re
import shutil
import tkinter as tk
from tkinter import ttk, messagebox

from core.loader import PLATFORMS_DIR
from core.config import write_text_atomic
from core.logger import log

_KEY_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _py_str(value):
    """把用户输入变成一个合法的 Python 字符串字面量。

    以前骨架用 str.format 把这些值裸插进 "..." 里: 操作指引里写一个英文引号
    (后台按钮本来常常写作 点击"导出"), 或 URL 里带反斜杠, 生成的就是语法错误文件。
    """
    return json.dumps(str(value if value is not None else ""), ensure_ascii=False)


def _plain(value):
    """给 docstring 用的纯文本: 引号和反斜杠在文档注释里同样能破坏语法。"""
    return re.sub(r'[\\"]', " ", str(value or "")).replace("\r", " ").replace("\n", " ").strip()


_SKELETON = '''\
"""{title} 平台导出脚本(骨架)"""

from core.platform_base import PlatformBase


class {cls}(PlatformBase):
    key = {key}
    name = {name}
    login_url = {login_url}
    export_url = {export_url}
    guide = {guide}

    # 不覆盖 export() 时走默认智能导出(SmartExporter): 按 placeholder/按钮文字猜
    # 日期框和"查询/导出"按钮。识别不准时, 参考 kuaishou/alipay 用 run_standard_flow
    # 骨架只覆盖有差异的钩子, 比靠猜稳。
'''


def render_platform_skeleton(key, name, login_url, export_url, guide):
    """生成骨架源码(纯函数, 不碰磁盘)。"""
    cls = (key.title().replace("_", "") or "X") + "Exporter"
    return _SKELETON.format(
        title=_plain(name or key),
        cls=cls,
        key=_py_str(key),
        name=_py_str(name or key),
        login_url=_py_str(login_url or ""),
        export_url=_py_str(export_url or ""),
        guide=_py_str(guide or ""),
    )


def validate_platform_key(key):
    """平台 key: 小写字母或下划线开头, 后接小写字母/数字/下划线。

    首位不能是数字: 骨架会生成 `class 2MeiExporter` 这种非法标识符, 文件写下去编译
    不过, 而目录已经建好 —— 再建提示"平台已存在", 平台列表里又看不到它, 成了死路。
    """
    return bool(key) and bool(_KEY_RE.match(key))


def save_platform_script(path, content):
    """校验语法通过后才原子写入平台脚本, 语法错误抛 ValueError。

    以前是先 open("w") 写盘、再 py_compile 检查: 检查失败时磁盘上**已经是坏文件**,
    而下一次 loader 扫描/reload 会静默跳过它(loader 只写日志) —— 用户原本能跑的
    脚本等于被一次误编辑毁掉, 而且编辑器里那份好内容也已被覆盖。
    """
    try:
        compile(content, path, "exec")
    except SyntaxError as e:
        raise ValueError("第 %s 行语法错误: %s" % (e.lineno, e.msg))
    except Exception as e:                       # 编码错误等
        raise ValueError("脚本无法编译: %s" % e)
    write_text_atomic(path, content)
    return path


def generate_platform_skeleton(key, name, login_url, export_url, guide, platforms_dir=PLATFORMS_DIR):
    """生成平台骨架文件到 platforms_dir/<key>/。重名/非法 key 抛 ValueError。"""
    if not validate_platform_key(key):
        raise ValueError("平台 key 只能由小写字母、数字、下划线组成, 且不能以数字开头")
    target = os.path.join(platforms_dir, key)
    if os.path.isdir(target):
        raise ValueError(f"平台 {key} 已存在")
    content = render_platform_skeleton(key, name, login_url, export_url, guide)
    os.makedirs(target, exist_ok=True)
    try:
        with open(os.path.join(target, "__init__.py"), "w", encoding="utf-8"):
            pass
        save_platform_script(os.path.join(target, "export.py"), content)
    except Exception:
        # 生成失败不能留个空目录: 下次"新增"会因 os.path.isdir 直接判"平台已存在"
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


class DebugProbe:
    """包装 BrowserManager,记录平台脚本对 browser 的每次调用(步骤调试)。

    steps: 每项为 dict {i, action, args, ms, ok, error}。
    以 browser 参数传入 PlatformBase.export(), 平台脚本无需任何改动。
    """

    def __init__(self, inner, steps=None):
        self.inner = inner
        self.steps = steps if steps is not None else []
        self.page = getattr(inner, "page", None)

    def _record(self, action, args, ok=True, error=None):
        self.steps.append({
            "i": len(self.steps) + 1,
            "action": action,
            "args": args,
            "ok": ok,
            "error": error,
        })

    def _call(self, action, fn, args, kwargs):
        """转发给真实 BrowserManager, 记一步, 然后把异常原样抛出去。

        吞掉异常会让同一段脚本在"脚本调试"里得到 manual、在生产里得到 failed,
        调试结果反而骗人 —— 调试器必须和生产走同一条错误路径。
        """
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            self._record(action, (args, kwargs), ok=False, error=str(e))
            raise
        self._record(action, (args, kwargs), ok=True)
        return result

    # ---- 通用透传 ----
    # 以前这里逐个手写 navigate/click_text/... 十几个方法, 结果漏掉了
    # wait_for、snapshot、wait_user、_log 等真实存在的方法: 平台脚本或
    # PlatformBase.check_login 调用它们时, 只在"脚本调试"里 AttributeError,
    # 生产环境跑得好好的 —— 让人误判脚本写坏了。逐个列还会随 BrowserManager
    # 的签名变化漂移(如 navigate 的 skip_if_same 参数就被丢了)。
    def __getattr__(self, name):
        inner = self.__dict__.get("inner")
        if inner is None:
            raise AttributeError(name)
        attr = getattr(inner, name)           # 真对象没有的名字, 这里同样 AttributeError
        if not callable(attr):
            return attr

        def _proxy(*args, **kwargs):
            return self._call(name, attr, args, kwargs)
        return _proxy

class PlatformWizard(tk.Toplevel):
    """新增平台表单向导: 填元信息生成骨架, 完成后回调刷新。"""

    FIELDS = [("key", "平台标识(字母数字下划线)"), ("name", "平台名称"),
              ("login_url", "登录页地址"), ("export_url", "导出页地址"),
              ("guide", "操作指引")]

    def __init__(self, master, on_created=None):
        super().__init__(master)
        self.on_created = on_created
        self.title("新增平台")
        self.geometry("460x300")
        self.transient(master)
        self.grab_set()
        self.vars = {}
        for col, (k, label) in enumerate(self.FIELDS):
            tk.Label(self, text=label, font=("Microsoft YaHei", 9)).grid(
                row=col, column=0, sticky="w", padx=8, pady=4)
            v = tk.StringVar()
            tk.Entry(self, textvariable=v, width=38, font=("Microsoft YaHei", 9)).grid(
                row=col, column=1, padx=8, pady=4)
            self.vars[k] = v
        tk.Button(self, text="生成", command=self._do_create,
                  font=("Microsoft YaHei", 9)).grid(row=len(self.FIELDS), column=0, pady=10)
        tk.Button(self, text="取消", command=self.destroy,
                  font=("Microsoft YaHei", 9)).grid(row=len(self.FIELDS), column=1, pady=10)

    def _do_create(self):
        data = {k: v.get().strip() for k, v in self.vars.items()}
        try:
            generate_platform_skeleton(
                data["key"], data["name"], data["login_url"],
                data["export_url"], data["guide"])
        except ValueError as e:
            messagebox.showwarning("无法创建", str(e), parent=self)
            return
        log(f"已新增平台 {data.get('key')}({data.get('name')})", callback=None)
        if self.on_created:
            self.on_created()
        self.destroy()


class PlatformEditor(tk.Toplevel):
    """内置脚本编辑器: 编辑 platforms/<key>/export.py, 保存时语法检查。"""

    def __init__(self, master, key, on_saved=None):
        super().__init__(master)
        self.key = key
        self.on_saved = on_saved
        import os as _os
        from core.loader import PLATFORMS_DIR as _PD
        self.path = _os.path.join(_PD, key, "export.py")
        self.title(f"编辑脚本 - {key}")
        self.geometry("640x480")
        self.transient(master)
        self.grab_set()
        tk.Label(self, text=f"文件: {self.path}", font=("Microsoft YaHei", 8),
                 fg="#666").pack(anchor="w", padx=8, pady=(6, 2))
        self.text = tk.Text(self, font=("Consolas", 10), undo=True)
        self.text.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.status = tk.Label(self, text="", font=("Microsoft YaHei", 9), fg="#e74c3c")
        self.status.pack(anchor="w", padx=8)
        tk.Button(self, text="保存", command=self._save,
                  font=("Microsoft YaHei", 9)).pack(pady=6)
        self.text.insert("1.0", _os.path.exists(self.path) and open(self.path, encoding="utf-8").read() or "")

    def _save(self):
        content = self.text.get("1.0", "end-1c")
        try:
            save_platform_script(self.path, content)
        except ValueError as e:
            self.status.config(text=f"保存失败: {e}(磁盘上的原文件未改动)")
            return
        except Exception as e:
            self.status.config(text=f"写入失败: {e}")
            return
        self.status.config(text="已保存 ✓", fg="#27ae60")
        log(f"平台脚本已保存: {self.key}", callback=None)
        if self.on_saved:
            self.on_saved()


class PlatformManagerDialog(tk.Toplevel):
    """平台管理: 平台列表 + 新增(向导) + 编辑(编辑器)。"""

    def __init__(self, master, platforms, on_refresh):
        super().__init__(master)
        self.on_refresh = on_refresh
        self.platforms = platforms
        self.title("平台管理")
        self.geometry("420x360")
        self.transient(master)
        self.grab_set()
        tk.Button(self, text="+ 新增平台", command=self._wizard,
                  font=("Microsoft YaHei", 9)).pack(anchor="w", padx=8, pady=6)
        self.tree = ttk.Treeview(self, columns=("key", "name"), show="headings", height=10)
        self.tree.heading("key", text="key")
        self.tree.heading("name", text="名称")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=8)
        self._reload()
        tk.Button(self, text="编辑选中脚本", command=self._edit,
                  font=("Microsoft YaHei", 9)).pack(pady=6)

    def _reload(self):
        self.tree.delete(*self.tree.get_children())
        for key, plat in self.platforms.items():
            self.tree.insert("", "end", iid=key, values=(key, plat.name))

    def _wizard(self):
        PlatformWizard(self, on_created=self._after_change)

    def _edit(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择一个平台", parent=self)
            return
        PlatformEditor(self, sel[0], on_saved=self._after_change)

    def _after_change(self):
        refreshed = self.on_refresh() or {}
        self.platforms = refreshed
        self._reload()


class DebugDialog(tk.Toplevel):
    """脚本调试: 选择平台/日期, 试运行其 export() 并流式输出步骤。"""

    def __init__(self, master, platforms, on_run):
        super().__init__(master)
        self.on_run = on_run
        self.title("脚本调试")
        self.geometry("420x240")
        self.transient(master)
        self.grab_set()
        row = 0
        tk.Label(self, text="平台:", font=("Microsoft YaHei", 9)).grid(
            row=row, column=0, sticky="w", padx=8, pady=6)
        self.key_var = tk.StringVar()
        keys = [k for k, p in platforms.items() if getattr(p, "enabled", True)]
        cb = ttk.Combobox(self, textvariable=self.key_var, values=keys,
                          state="readonly", font=("Microsoft YaHei", 9))
        cb.grid(row=row, column=1, sticky="w", padx=8)
        if keys:
            self.key_var.set(keys[0])
        row += 1
        tk.Label(self, text="开始日期:", font=("Microsoft YaHei", 9)).grid(
            row=row, column=0, sticky="w", padx=8, pady=6)
        self.start_var = tk.StringVar(value="")
        self.end_var = tk.StringVar(value="")
        tk.Entry(self, textvariable=self.start_var, width=14,
                 font=("Microsoft YaHei", 9)).grid(row=row, column=1, sticky="w", padx=8)
        tk.Label(self, text="结束日期:", font=("Microsoft YaHei", 9)).grid(
            row=row, column=2, sticky="w", padx=8)
        tk.Entry(self, textvariable=self.end_var, width=14,
                 font=("Microsoft YaHei", 9)).grid(row=row, column=3, sticky="w", padx=8)
        row += 1
        self.status = tk.Label(self, text="", font=("Microsoft YaHei", 9), fg="#27ae60")
        self.status.grid(row=row, column=0, columnspan=4, sticky="w", padx=8, pady=4)
        row += 1
        tk.Button(self, text="试运行", command=self._run,
                  font=("Microsoft YaHei", 9)).grid(row=row, column=0, pady=8)
        tk.Button(self, text="关闭", command=self.destroy,
                  font=("Microsoft YaHei", 9)).grid(row=row, column=1, pady=8)

    def _run(self):
        key = self.key_var.get()
        if not key:
            return
        start = self.start_var.get().strip() or None
        end = self.end_var.get().strip() or None
        self.status.config(text="调试中…")
        self.on_run(key, start, end)