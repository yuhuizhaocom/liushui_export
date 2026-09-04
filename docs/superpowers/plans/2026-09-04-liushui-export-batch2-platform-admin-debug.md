# 批2：平台管理 + 脚本调试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户能在客户端内新增平台（表单向导生成骨架）、编辑平台脚本（内置编辑器）、并对脚本做步骤化试运行调试（DebugProbe）。

**Architecture:** 核心可测逻辑（骨架生成、步骤探针）抽成纯函数/独立类放在 `core/platform_admin.py`，Tkinter 弹窗作为薄 UI 壳。平台产出目录写入 `loader.PLATFORMS_DIR`（可注入便于测试）；调试通过包装 `BrowserManager` 拦截平台脚本对 browser 的调用，把步骤流经统一 `log()` 输出到日志视图。

**Tech Stack:** Python 3.10+、Tkinter、pytest（dev）、Playwright（现有）。

**说明：** 仓库已有批1 提交基线；每个 Task 独立提交。沙箱会拦截 pytest 写 site-packages 的 .pyc（噪音），运行 pytest 前设置 `$env:PYTHONDONTWRITEBYTECODE=1`。此环境存在「并行 Edit 偶发未落盘」问题：每个 Edit 后必须用 Read/rg 复核落盘再继续。

**参考 spec：** `docs/superpowers/specs/2026-09-04-liushui-export-optimization-design.md` §4。

---

## 文件结构

| 文件 | 动作 | 职责 |
|------|------|------|
| `core/platform_admin.py` | Create | `generate_platform_skeleton()`（纯函数）、`validate_platform_key()`、`DebugProbe`、`PlatformManagerDialog`（向导+编辑器+列表的 UI 壳）、`DebugDialog` |
| `core/main_gui.py` | Modify | 中间栏按钮区新增「平台管理」「脚本调试」入口；调试执行线程；平台管理重建列表钩子 |
| `tests/test_platform_admin.py` | Create | 骨架生成、key 校验、DebugProbe 步骤记录测试 |
| `CODE_WIKI.md` | Modify | 批2 文档同步 |

---

### Task 1: 骨架生成纯函数（TDD）

**Files:**
- Create: `core/platform_admin.py`（本 Task 只含纯函数部分；UI 类在 Task 3/4 补充）
- Test: `tests/test_platform_admin.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_platform_admin.py
import os
import pytest

from core.platform_admin import generate_platform_skeleton, validate_platform_key


def _read_rel(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


def test_validate_key_rejects_invalid():
    assert validate_platform_key("youzan2") is True
    assert validate_platform_key("has space") is False
    assert validate_platform_key("大写X") is False
    assert validate_platform_key("ok_1") is True


def test_generate_skeleton_creates_files(tmp_path):
    gen_dir = tmp_path / "platforms"
    generate_platform_skeleton("mypay", "我的支付", "https://a/login",
                               "https://a/export", "后台→流水→导出", str(gen_dir))
    init_py = _read_rel(str(gen_dir), "mypay/__init__.py")
    export_py = _read_rel(str(gen_dir), "mypay/export.py")
    assert init_py.strip() == ""
    assert 'key = "mypay"' in export_py
    assert 'name = "我的支付"' in export_py
    assert "class MypayExporter(PlatformBase)" in export_py
    assert "from core.platform_base import PlatformBase" in export_py


def test_generate_skeleton_rejects_conflict(tmp_path):
    gen_dir = tmp_path / "platforms"
    (gen_dir / "youzan").mkdir(parents=True)
    with pytest.raises(ValueError, match="已存在"):
        generate_platform_skeleton("youzan", "有赞", "", "", "", str(gen_dir))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests/test_platform_admin.py -v`
Expected: `ModuleNotFoundError: core.platform_admin` → collection error，全部 FAIL。

- [ ] **Step 3: 实现纯函数**

```python
# core/platform_admin.py (Task 1 部分)
"""
平台管理模块 - 客户端内新增/编辑/调试平台脚本
- 骨架生成与 key 校验为纯函数,便于单元测试
- PlatformManagerDialog / DebugDialog 为 Tkinter UI 壳(后续 Task 补充)
"""
import os
import re

from core.loader import PLATFORMS_DIR

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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests/test_platform_admin.py -v`
Expected: 3 个用例 PASS。

- [ ] **Step 5: 提交**

```bash
git add core/platform_admin.py tests/test_platform_admin.py
git commit -m "feat(admin): platform skeleton generator + key validation"
```

---

### Task 2: DebugProbe 步骤拦截（TDD）

**Files:**
- Modify: `core/platform_admin.py`（追加 `DebugProbe`）
- Test: `tests/test_platform_admin.py`（追加用例）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_platform_admin.py` 末尾追加：

```python
import time  # 顶部若有则复用

from core.platform_admin import DebugProbe


class FakeInner:
    """桩 BrowserManager: 记录调用,有 page 属性。"""

    def __init__(self):
        self.calls = []
        self.page = object()

    def navigate(self, url, retries=3):
        self.calls.append(("navigate", url))
        return True

    def click_text(self, text, exact=False, retries=3):
        self.calls.append(("click_text", text))
        return True

    def begin_wait_download(self):
        self.calls.append(("begin_wait_download",))
        return None

    def wait_download(self, timeout=120):
        self.calls.append(("wait_download", timeout))
        return "/tmp/out.xlsx"

    def sleep(self, seconds):
        self.calls.append(("sleep", seconds))

    def screenshot(self, name="screenshot"):
        self.calls.append(("screenshot", name))
        return name


def test_debug_probe_records_steps():
    inner = FakeInner()
    probe = DebugProbe(inner, steps=[])
    assert probe.page is inner.page  # page 透传
    probe.navigate("https://x")
    probe.click_text("导出")
    probe.begin_wait_download()
    path = probe.wait_download(timeout=60)
    assert path == "/tmp/out.xlsx"
    assert len(probe.steps) >= 4
    assert probe.steps[0]["action"] == "navigate"
    assert probe.steps[1]["action"] == "click_text"
    assert probe.steps[2]["action"] == "begin_wait_download"
    assert probe.steps[3]["action"] == "wait_download"


def test_debug_probe_records_exception():
    class BoomInner(FakeInner):
        def click_text(self, text, exact=False, retries=3):
            self.calls.append(("click_text", text))
            raise RuntimeError("boom")

    inner = BoomInner()
    probe = DebugProbe(inner, steps=[])
    probe.click_text("导出")
    last = probe.steps[-1]
    assert last["action"] == "click_text"
    assert last["ok"] is False
    assert "boom" in last["error"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests/test_platform_admin.py -v`
Expected: 新增 2 用例因 `NameError: DebugProbe` FAIL，原有 3 用例仍 PASS。

- [ ] **Step 3: 实现 DebugProbe**

在 `core/platform_admin.py` 中追加：

```python
import time


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
        t0 = time.time()
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            self._record(action, (args, kwargs), ok=False, error=str(e))
            return None
        self._record(action, (args, kwargs), ok=True)
        return result

    # ---- 透传拦截: 平台脚本常用方法 ----
    def navigate(self, url, retries=3):
        return self._call("navigate", self.inner.navigate, (url,), {"retries": retries})

    def safe_click(self, selector, description="", retries=3):
        return self._call("safe_click", self.inner.safe_click,
                          (selector,), {"description": description, "retries": retries})

    def click_text(self, text, exact=False, retries=3):
        return self._call("click_text", self.inner.click_text,
                          (text,), {"exact": exact, "retries": retries})

    def click_selector(self, selector, retries=3):
        return self._call("click_selector", self.inner.click_selector, (selector,), {"retries": retries})

    def fill_placeholder(self, placeholder, value, retries=2):
        return self._call("fill_placeholder", self.inner.fill_placeholder,
                          (placeholder,), {"value": value, "retries": retries})

    def fill_selector(self, selector, value, retries=2):
        return self._call("fill_selector", self.inner.fill_selector,
                          (selector,), {"value": value, "retries": retries})

    def sleep(self, seconds):
        return self._call("sleep", self.inner.sleep, (seconds,), {})

    def is_visible_text(self, text, timeout=3):
        return self._call("is_visible_text", self.inner.is_visible_text,
                          (text,), {"timeout": timeout})

    def close_popup(self, retries=2):
        return self._call("close_popup", self.inner.close_popup, (), {"retries": retries})

    def begin_wait_download(self):
        return self._call("begin_wait_download", self.inner.begin_wait_download, (), {})

    def wait_download(self, timeout=120):
        return self._call("wait_download", self.inner.wait_download, (), {"timeout": timeout})

    def screenshot(self, name="screenshot"):
        return self._call("screenshot", self.inner.screenshot, (name,), {})

    def get_page_info(self):
        return self._call("get_page_info", self.inner.get_page_info, (), {})
```

- [ ] **Step 4: 运行测试确认通过**

Run: `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests/test_platform_admin.py -v`
Expected: 5 个用例全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add core/platform_admin.py tests/test_platform_admin.py
git commit -m "feat(admin): debug probe recording browser call steps"
```

---

### Task 3: 平台管理弹窗 UI（向导 + 编辑器 + 列表）

**Files:**
- Modify: `core/platform_admin.py`（追加 3 个 Tkinter 弹窗类）
- Modify: `core/main_gui.py`（按钮入口与刷新钩子）

- [ ] **Step 1: 实现 PlatformManagerDialog / PlatformWizard / PlatformEditor**

在 `core/platform_admin.py` 末尾追加（纯 UI，无单元测试；用静态检查 + import 冒烟验收）：

```python
import tkinter as tk
from tkinter import ttk, messagebox
import py_compile

from core.logger import log


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
```

- [ ] **Step 2: main_gui 挂接入口与刷新钩子**

在 `core/main_gui.py` 中：
1. **统一采用方法内局部导入**（不要在文件顶部 import platform_admin）：
2. 新增方法：

```python
def _open_platform_manager(self):
    """打开平台管理弹窗(新增/编辑脚本)。"""
    from core.platform_admin import PlatformManagerDialog

    def _refresh():
        self.platforms = discover_platforms()
        return self.platforms

    PlatformManagerDialog(self.root, self.platforms, on_refresh=_refresh)
```

3. 在 `_build_middle_panel` 的按钮区（`others` 列表中「使用说明」之后）追加两个按钮：
   - ("平台管理", self._open_platform_manager, "#8e44ad", 1, 2)  —— 若 1 行 2 列已被占，改放 row=1, column=2 或扩展 grid。当前布局：others 有 [首次登录(0,0), 检查状态(0,1), 打开下载(1,0), 使用说明(1,1)]，「开始导出」占 (0,2) rowspan2。`row=1, column=2` 被「开始导出」占用。因此把「平台管理」放在第 2 行平铺：`row=2, column=0`；调试按钮（Task 4）放 `row=2, column=1`。
   实际落地时需按当前网格结构调整，若拥挤可新增一行 `bar.grid_rowconfigure(2, weight=1)`。在报告中说明最终摆放。

4. 静态检查：`python -m py_compile core/platform_admin.py core/main_gui.py` → 退出码 0。

- [ ] **Step 3: 冒烟**

Run: `python -c "import core.main_gui; import core.platform_admin as pa; print('IMPORT_OK', hasattr(pa,'PlatformManagerDialog'), hasattr(pa,'PlatformWizard'), hasattr(pa,'PlatformEditor'))"`
Expected: `IMPORT_OK True True True`

- [ ] **Step 4: 提交**

```bash
git add core/platform_admin.py core/main_gui.py
git commit -m "feat(gui): platform manager dialog (wizard + editor)"
```

---

### Task 4: 脚本调试入口（DebugDialog + 执行线程）

**Files:**
- Modify: `core/platform_admin.py`（追加 `DebugDialog`）
- Modify: `core/main_gui.py`（调试按钮 + 执行方法）

- [ ] **Step 1: 实现 DebugDialog**

追加到 `core/platform_admin.py`：

```python
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
```

- [ ] **Step 2: main_gui 挂接调试执行**

在 `core/main_gui.py` 中：
1. 新增方法（放入 `PlatformManagerDialog` import 附近的方法区）：

```python
def _open_debug_dialog(self):
    """脚本调试弹窗。"""
    from core.platform_admin import DebugDialog, DebugProbe

    def _run(key, start_date, end_date):
        plat = self.platforms.get(key)
        if not plat:
            return
        if not start_date:
            start_date = self.date_start.get().strip()
        if not end_date:
            end_date = self.date_end.get().strip()

        def _task():
            self.progress.config(maximum=100, value=0)
            self._set_status(f"调试 {plat.name}…")
            steps = []
            try:
                self._ensure_browser(plat)
                probe = DebugProbe(self.browser, steps=steps)
                result = plat.export(probe, start_date, end_date)
                for s in steps:
                    line = f"  #{s['i']} {s['action']}{s['args']}"
                    if not s["ok"]:
                        line += f"  [出错] {s['error']}"
                        self._append_log(line)
                        continue
                    self._append_log(line)
                self._append_log(f"[调试完成] {plat.name}: {result}")
                self._set_status(f"调试完成: {result}")
            except Exception as e:
                self._append_log(f"[调试异常] {e}")
                self._set_status(f"调试异常: {e}")

        self._run_async(_task)

    DebugDialog(self.root, self.platforms, on_run=_run)
```

2. 在中间栏按钮网格新增「脚本调试」按钮（Task 3 若未放，则与「平台管理」同排）。最终布局以不遮挡「开始导出」为原则（可 `bar.grid_rowconfigure(2, weight=1)` 增加第 3 行）。
3. 静态检查：`python -m py_compile core/platform_admin.py core/main_gui.py`。
4. import 冒烟：`python -c "import core.main_gui as m; print(hasattr(m.LiushuiApp,'_open_debug_dialog'))"` → True。

- [ ] **Step 3: 提交**

```bash
git add core/platform_admin.py core/main_gui.py
git commit -m "feat(gui): debug dialog with step-by-step trial run"
```

---

### Task 5: 批2 回归与文档同步

**Files:**
- Modify: `CODE_WIKI.md`

- [ ] **Step 1: 回归**

Run:
- `python -m py_compile core/platform_admin.py core/main_gui.py core/*.py platforms/youzan/export.py`
- `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests -v` → 全部 PASS（应为 11 个：logger 3 + keepalive 3 + platform_admin 5）
- `python -c "from core.loader import discover_platforms; print(sorted(discover_platforms().keys()))"` → `['youzan']`
- `python -c "import core.main_gui as m; print('GUI_OK', hasattr(m.LiushuiApp,'_open_platform_manager') and hasattr(m.LiushuiApp,'_open_debug_dialog'))"` → True

- [ ] **Step 2: GUI 人工验收点（写入文档供用户执行）**

启动验证：中间栏出现「平台管理」「脚本调试」按钮；新增一个有赞之外的测试平台（向导生成骨架后左侧列表出现）；编辑其 export.py 保存成功；调试弹窗对已有赞脚本试运行，日志区逐条显示 `#N action...` 步骤。

- [ ] **Step 3: 更新 CODE_WIKI.md**

1. 第 4 章模块职责表新增：`| \`core/platform_admin.py\` | **平台管理/调试**。骨架生成纯函数 \`generate_platform_skeleton()\`、\`DebugProbe\` 步骤探针、\`PlatformManagerDialog\`（向导+内置编辑器+列表）与 \`DebugDialog\`（试运行）三个 Tkinter 弹窗。 |`
2. 第 5 章新增小节（5.7 与 5.8 编号顺延）：
   - `### 5.7 PlatformAdmin（core/platform_admin.py）— 平台管理与调试`：列 `generate_platform_skeleton / validate_platform_key / DebugProbe(intercept 方法清单) / PlatformManagerDialog / DebugDialog` 简要说明。
   - 注：原「5.6 模块级关键函数」若已顺延，则新小节直接追加在 5.6 之后编号 5.7；避免编号冲突。
3. 第 7 章「核心流程解析」末尾追加：`### 7.6 平台新增与调试流程`（向导生成骨架 → 编辑器保存语法检查 → 调试探针包装 browser 逐步试运行）。
4. 附录文件清单：新增 `core/platform_admin.py`（约 300 行）与「`tests/`（…platform_admin）」。

- [ ] **Step 4: 提交**

```bash
git add CODE_WIKI.md
git commit -m "docs: batch2 (platform admin + debug) sync"
```

---

## 验收总纲（批 2）

- [ ] pytest 11 个用例全绿
- [ ] 主界面出现「平台管理」「脚本调试」按钮
- [ ] 向导新增平台后左侧列表立即出现；骨架文件正确生成
- [ ] 编辑器保存脚本进行语法检查（错误不离屏）
- [ ] 调试弹窗试运行：日志区出现 `#N` 步骤序列与最终结果
- [ ] `python -m core.main_gui` 启动正常