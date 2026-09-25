"""
平台管理模块 - 客户端内新增/编辑/调试平台脚本
- 骨架生成与 key 校验为纯函数,便于单元测试
- PlatformManagerDialog / DebugDialog 为 Tkinter UI 壳(后续 Task 补充)
"""
import os
import re
import tkinter as tk
from tkinter import ttk, messagebox
import py_compile

from core.loader import PLATFORMS_DIR
from core.logger import log

_KEY_RE = re.compile(r"^[a-z0-9_]+$")

_SKELETON = '''\
"""{name}平台导出脚本"""

from core.platform_base import PlatformBase


class {Key}Exporter(PlatformBase):
    key = "{key}"
    name = "{name}"
    login_url = "{login_url}"
    export_url = "{export_url}"
    guide = "{guide}"

    # 不覆盖 export() 时使用默认智能导出(SmartExporter)
'''


def validate_platform_key(key):
    """平台 key 合法性: 小写字母/数字/下划线。"""
    return bool(key) and bool(_KEY_RE.match(key))


def generate_platform_skeleton(key, name, login_url, export_url, guide, platforms_dir=PLATFORMS_DIR):
    """生成平台骨架文件到 platforms_dir/<key>/。重名/非法 key 抛 ValueError。"""
    if not validate_platform_key(key):
        raise ValueError("平台 key 只能包含小写字母、数字、下划线且不能为空")
    target = os.path.join(platforms_dir, key)
    if os.path.isdir(target):
        raise ValueError(f"平台 {key} 已存在")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "__init__.py"), "w", encoding="utf-8"):
        pass
    content = _SKELETON.format(
        Key=key.title().replace("_", ""),
        key=key,
        name=name or "",
        login_url=login_url or "",
        export_url=export_url or "",
        guide=guide or "",
    )
    with open(os.path.join(target, "export.py"), "w", encoding="utf-8") as f:
        f.write(content)
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
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(content)
            py_compile.compile(self.path, doraise=True)
        except Exception as e:
            self.status.config(text=f"保存失败(语法错误): {e}")
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