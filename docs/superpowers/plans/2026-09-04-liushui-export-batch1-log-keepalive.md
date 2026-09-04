# 批1：日志合并 + 登录保活 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把两套日志（系统日志 + 操作历史）合并为「单文件 + 单界面视图」，并新增程序运行期后台周期刷新登录态的服务（KeepAliveService）。

**Architecture:** 日志统一收敛到 `core/logger.py` 的 `log()`（console + `logs/run_YYYYMMDD.log` + GUI 回调），删除 `main_gui.py` 中的操作历史区与 `operation_history.txt`。保活采用独立后台线程 + 可注入的浏览器工厂（便于测试），巡检时若 `app.running` 为真则跳过本轮，绝不与导出/调试并发。

**Tech Stack:** Python 3.10+、Tkinter、Playwright（现有）、pytest（新增 dev 依赖，用于 TDD）。

**说明（执行前必读）：**
- 仓库当前**没有首个 git 提交**且索引中残留历史暂存（`.idea/*`、旧的 AD 文件等）。开始执行前先与用户确认基线处理（推荐：`git reset` 清空索引 → 用新 `.gitignore` 重新 `git add` 合理文件 → 建立首个提交 "chore: repository baseline"）。本计划中所有 commit 步骤均指在此基础上提交。
- 新增 dev 依赖 pytest：终端执行 `pip install pytest`，并把 `pytest` 写入新建的 `requirements-dev.txt`。

---

## 文件结构

| 文件 | 动作 | 职责 |
|------|------|------|
| `core/logger.py` | Modify | 唯一日志通道；`log()` 支持级别正确落文件、界面行带 `[LEVEL]` 前缀 |
| `core/config.py` | Modify | settings 扩展：`enable_keepalive`、`keepalive_interval_min` |
| `core/keepalive.py` | Create | `KeepAliveService`：后台线程 + 巡检逻辑（浏览器工厂可注入） |
| `core/main_gui.py` | Modify | 移除操作历史区/`HISTORY_FILE`/`_record_action`；日志区统一；保活开关+间隔 UI；服务启停挂接 |
| `tests/test_logger.py` | Create | 日志级别前缀与回调通道测试 |
| `tests/test_keepalive.py` | Create | 巡检顺序 / 运行中跳过 / 失效判定测试 |
| `requirements-dev.txt` | Create | `pytest` |

---

### Task 1: 日志统一（`logger.log` 级别落文件 + 界面前缀）

**Files:**
- Modify: `core/logger.py`
- Test: `tests/test_logger.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_logger.py
from core import logger as lm


def test_warning_line_contains_level_tag():
    got = []
    line = lm.log("disk full", level="warning", callback=got.append)
    assert "[WARN]" in line
    assert "[WARN]" in got[0]


def test_error_line_contains_level_tag():
    got = []
    lm.log("boom", level="error", callback=got.append)
    assert "[ERROR]" in got[0]


def test_info_line_has_no_tag():
    got = []
    lm.log("plain", level="info", callback=got.append)
    assert "[WARN]" not in got[0] and "[ERROR]" not in got[0]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_logger.py -v`
Expected: `log()` 当前产出行不含 `[WARN]` 等标签 → 3 个用例全部 FAIL。

- [ ] **Step 3: 实现最小改动**

```python
# core/logger.py（全文替换为以下内容）
"""
日志模块 - 同时输出到GUI和文件(项目唯一日志通道)
"""
import os
import logging
from datetime import datetime

from .config import ROOT_DIR

# 日志目录(基于项目根的绝对路径,不依赖运行目录)
LOG_DIR = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log_file = os.path.join(LOG_DIR, f"run_{datetime.now().strftime('%Y%m%d')}.log")

logger = logging.getLogger("liushui")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)

_PY_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO,
              "warning": logging.WARNING, "error": logging.ERROR}
_LEVEL_TAG = {"debug": "DEBUG", "warning": "WARN", "error": "ERROR"}


def log(message, level="info", callback=None):
    """统一日志入口: console + 文件 + GUI 回调。
    level: debug/info/warning/error; 界面展示行带 [LEVEL] 前缀(除 info)。"""
    ts = datetime.now().strftime("%H:%M:%S")
    tag = _LEVEL_TAG.get(level, "")
    line = f"[{ts}] [{tag}] {message}" if tag else f"[{ts}] {message}"
    # 无控制台环境(pythonw)下 sys.stdout 可能为 None,print 会抛错
    try:
        print(line)
    except Exception:
        pass
    logger.log(_PY_LEVELS.get(level, logging.INFO), message)
    if callback:
        try:
            callback(line)
        except Exception:
            pass
    return line
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_logger.py -v`
Expected: 3 个用例 PASS。

- [ ] **Step 5: 提交**

```bash
git add core/logger.py tests/test_logger.py requirements-dev.txt
git commit -m "feat(log): unify log channel with level tags"
```

---

### Task 2: 移除操作历史，主界面统一日志视图

**Files:**
- Modify: `core/main_gui.py`（移除中间 history 区、`HISTORY_FILE`、`_load_history`、`_record_action`；`_append_log` 保留）

- [ ] **Step 1: 删除历史文件常量与旧逻辑**

在 `core/main_gui.py` 中：
1. 删除第 55-57 行附近的 `HISTORY_FILE` 定义。
2. 删除 `_build_middle_panel` 中「操作历史」区块（`tk.Label(middle, text="操作历史", ...)` 到 `self._load_history()` 之间的代码）。
3. 删除方法 `_load_history` 与 `_record_action`。
4. 将调用点改为统一日志：
   - `self._record_action("首次登录: ...")` → `log(f"首次登录: ...", callback=self._append_log)`（`log` 已 import，`_append_log` 签名接收行文本）。
   - 其余 `_record_action(...)` 同步替换。
5. `import` 区保留 `from core.logger import log`；不再需要 `os` 的地方可保留（其他逻辑仍用）。

- [ ] **Step 2: 静态检查**

Run: `python -m py_compile core/main_gui.py`
Expected: 输出为空、退出码 0。`rg -n "HISTORY_FILE|operation_history|_record_action|_load_history" core/` 无匹配。

- [ ] **Step 3: 冒烟验收（手动）**

Run: `python -m core.main_gui`
Expected: 界面无「操作历史」区；右侧日志区在登录/导出/检查状态等操作时出现统一序列；`logs/run_YYYYMMDD.log` 同时包含系统与操作记录且时间有序。

- [ ] **Step 4: 提交**

```bash
git add core/main_gui.py
git commit -m "feat(gui): merge history panel into unified log view"
```

---

### Task 3: KeepAliveService 核心（浏览器工厂可注入）

**Files:**
- Create: `core/keepalive.py`
- Test: `tests/test_keepalive.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_keepalive.py
import threading
from core.keepalive import KeepAliveService


class FakeBrowser:
    def __init__(self, url, title, log=None):
        self.url, self.title, self.log = url, title, log
        self.closed = False

    def start(self): pass

    def navigate(self, url): self.url = url

    def sleep(self, s): pass

    def close_popup(self): pass

    def get_page_info(self): return {"url": self.url, "title": self.title}

    def close(self): self.closed = True


class FakeApp:
    def __init__(self, running=False, items=(), login_urls=None):
        self.running = running
        self.items = items
        self.login_urls = login_urls or {}
        self.marks = []

    def iter_selected_merchants(self):
        for it in self.items:
            yield it

    def set_platform_status(self, key, status):
        self.marks.append((key, status))


def _make_factory(created):
    def factory(key, merchant):
        b = FakeBrowser(f"https://{key}.example.com", "首页", log=list())
        created.append((key, merchant, b))
        return b
    return factory


def test_run_once_visits_each_merchant_in_order():
    app = FakeApp(items=[("youzan", "旗舰店A"), ("youzan", "旗舰店B")],
                  login_urls={"youzan": "https://youzan.example.com/login"})
    created = []
    svc = KeepAliveService(app, make_browser=_make_factory(created))
    svc.run_once()
    assert [(k, m) for k, m, _ in created] == [("youzan", "旗舰店A"), ("youzan", "旗舰店B")]
    assert all(b.closed for _, _, b in created)


def test_run_once_marks_expired_login_red():
    def factory(key, merchant):
        return FakeBrowser("https://x/login", "登录", log=list())
    app = FakeApp(items=[("youzan", "A")], login_urls={"youzan": "https://x/login"})
    svc = KeepAliveService(app, make_browser=factory)
    svc.run_once()
    assert ("youzan", "error") in app.marks


def test_run_once_marks_ok_green():
    def factory(key, merchant):
        return FakeBrowser("https://x/home", "首页", log=list())
    app = FakeApp(items=[("youzan", "A")], login_urls={"youzan": "https://x/login"})
    svc = KeepAliveService(app, make_browser=factory)
    svc.run_once()
    assert ("youzan", "ok") in app.marks
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_keepalive.py -v`
Expected: `ModuleNotFoundError: core.keepalive` → 全部 FAIL（collection error）。

- [ ] **Step 3: 实现最小代码**

```python
# core/keepalive.py
"""
登录保活服务 - 程序运行期后台周期刷新各商户登录态
- 独立后台线程, 间隔可配置
- 巡检时若 app.running 为真(正在导出/调试)则跳过本轮, 不与之并发
- 浏览器实例由 make_browser 工厂创建(便于测试注入), 每商户独立 profile
"""
import threading
import time
from datetime import datetime

from core.logger import log


class KeepAliveService:
    def __init__(self, app, interval_min=30, enabled=True, make_browser=None):
        self.app = app
        self.interval_min = max(1, int(interval_min))
        self.enabled = enabled
        self.make_browser = make_browser or self._default_browser
        self._stop = threading.Event()
        self._thread = None

    @staticmethod
    def _default_browser(key, merchant):
        from core.browser import BrowserManager
        b = BrowserManager(headless=True)
        b.set_browser_profile(key, merchant)
        return b

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log("保活服务已启动(间隔 %d 分钟)" % self.interval_min)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log("保活服务已停止")

    def _run(self):
        while not self._stop.wait(self.interval_min * 60):
            if not self.enabled:
                continue
            if getattr(self.app, "running", False):
                log("保活巡检跳过(有任务执行中)")
                continue
            try:
                self.run_once()
            except Exception as e:
                log(f"保活巡检异常: {e}", "error")

    def run_once(self):
        """巡检一轮: 逐个商户打开登录页刷新会话并判定状态。"""
        for key, merchant in self._iter_merchants():
            b = None
            try:
                b = self.make_browser(key, merchant)
                b.start()
                url = getattr(self.app, "login_urls", {}).get(key, "") or ""
                if url:
                    b.navigate(url)
                b.sleep(3)
                b.close_popup()
                info = b.get_page_info()
                cur_url = (info.get("url") or "").lower()
                title = info.get("title") or ""
                if "login" in cur_url or "登录" in title:
                    self._mark(key, "error")
                    log(f"保活: {key}/{merchant} 登录已失效, 建议重新登录", "warning")
                else:
                    self._mark(key, "ok")
                    log(f"保活: {key}/{merchant} 会话有效")
            except Exception as e:
                self._mark(key, "error")
                log(f"保活: {key}/{merchant} 巡检失败 - {e}", "error")
            finally:
                if b is not None:
                    try:
                        b.close()
                    except Exception:
                        pass

    def _iter_merchants(self):
        """迭代器: 取勾选且有商户的平台×商户。app 需提供该接口。"""
        method = getattr(self.app, "iter_selected_merchants", None)
        if method is None:
            return
        yield from method() or []

    def _mark(self, key, status):
        fn = getattr(self.app, "set_platform_status", None)
        if fn:
            fn(key, status)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_keepalive.py -v`
Expected: 3 个用例 PASS。

- [ ] **Step 5: 提交**

```bash
git add core/keepalive.py tests/test_keepalive.py
git commit -m "feat(keepalive): background session refresh service"
```

---

### Task 4: 主界面接入保活（开关/间隔 + 生命周期挂接）

**Files:**
- Modify: `core/config.py`
- Modify: `core/main_gui.py`

- [ ] **Step 1: settings 扩展**

在 `core/config.py` 的 `DEFAULT_SETTINGS` 中追加：

```python
DEFAULT_SETTINGS = {
    "show_browser": True,
    "download_name_mode": "unified",
    "enable_keepalive": True,        # 登录保活开关
    "keepalive_interval_min": 30,    # 保活巡检间隔(分钟)
}
```

- [ ] **Step 2: 主界面加入口与生命周期**

在 `core/main_gui.py` 的 `LiushuiApp.__init__` 末尾（`_refresh_summary()` 后）增加保活服务实例：

```python
from core.keepalive import KeepAliveService  # 文件顶部导入
# 字段初始化:
self.keeplog = []  # 可选: 无需
self.keepalive = KeepAliveService(
    self,
    interval_min=int(self.settings.get("keepalive_interval_min", 30)),
    enabled=bool(self.settings.get("enable_keepalive", True)),
)
```

在 `_build_middle_panel` 的「显示浏览器」行附近追加保活设置行：

```python
ka_row = tk.Frame(middle, bg=BG_PANEL)
ka_row.pack(fill=tk.X, pady=(2, 0))
self.enable_ka_var = tk.BooleanVar(value=bool(self.settings.get("enable_keepalive", True)))
self.enable_ka_var.trace_add("write", self._on_ka_setting)
tk.Checkbutton(ka_row, variable=self.enable_ka_var, text="登录保活",
               bg=BG_PANEL, fg=FG_MUTED, font=("Microsoft YaHei", 9),
               activebackground=BG_PANEL).pack(side=tk.LEFT, padx=(0, 12))
tk.Label(ka_row, text="间隔(分钟):", bg=BG_PANEL, fg=FG_MUTED,
         font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
self.ka_interval = tk.Spinbox(ka_row, from_=5, to=600, increment=5, width=5,
                              font=("Microsoft YaHei", 9))
self.ka_interval.delete(0, tk.END)
self.ka_interval.insert(0, str(int(self.settings.get("keepalive_interval_min", 30))))
self.ka_interval.bind("<FocusOut>", lambda *_: self._on_ka_setting())
self.ka_interval.pack(side=tk.LEFT)
```

新增方法：

```python
def _on_ka_setting(self, *_):
    """保活设置变化即时保存并应用到运行中的服务。"""
    from core.config import save_settings
    enabled = bool(self.enable_ka_var.get())
    try:
        interval = max(1, int(self.ka_interval.get()))
    except Exception:
        interval = 30
    save_settings({"enable_keepalive": enabled, "keepalive_interval_min": interval})
    self.keepalive.enabled = enabled
    self.keepalive.interval_min = interval

def __iter_merchants(self):
    """供保活/调度复用的迭代: (key, merchant)。"""
    for key in self._get_selected():
        for m in self._get_selected_merchants(key):
            yield (key, m)

iter_selected_merchants = __iter_merchants
```

在 `_run_async` 中（任务开始时）暂停保活，`_thread_wrapper` 的 `finally` 恢复：`self.keepalive` 由 `app.running` 判断，故只需保证 `running` 正确即可；无需额外逻辑。

- [ ] **Step 3: 启动/退出挂接**

- `main()` 中 `root.protocol("WM_DELETE_WINDOW", ...)` 改为：

```python
def _on_close(app, root):
    app.keepalive.stop()
    app.cleanup()
    root.destroy()

root.protocol("WM_DELETE_WINDOW", lambda: _on_close(app, root))
```

- `LiushuiApp.__init__` 末尾调用 `self.keepalive.start()`（在 `_refresh_summary()` 之后）。

- [ ] **Step 4: 静态检查 + 手动验收**

Run: `python -m py_compile core/config.py core/main_gui.py core/keepalive.py`
Expected: 退出码 0。

Run: `python -m core.main_gui`
Expected: 界面出现「登录保活」勾选与间隔输入；勾选一个已建档商户后，等待一个间隔（测试时可将间隔临时调到 1-5 分钟观察日志「保活: …会话有效」，观察后改回）；任务运行时日志出现「保活巡检跳过」。

- [ ] **Step 5: 提交**

```bash
git add core/config.py core/main_gui.py
git commit -m "feat(gui): keepalive toggle and interval settings"
```

---

### Task 5: 批1 回归与文档同步

**Files:**
- Modify: `CODE_WIKI.md`

- [ ] **Step 1: 全量编译与测试**

Run: `python -m py_compile core/*.py platforms/youzan/export.py && python -m pytest tests -v`
Expected: 编译通过；全部测试 PASS。

- [ ] **Step 2: 冒烟回归**

Run: `python -m core.main_gui`
Expected: 程序启动正常；`discover_platforms()` 仍只返回 `youzan`；日志合并后无操作历史区。

- [ ] **Step 3: 更新 CODE_WIKI.md**

- 第 4 章模块职责表：`core/logger.py` 描述改为「合并后的唯一日志通道（系统+操作，级别落文件）」；新增 `core/keepalive.py` 行。
- 第 5 章：新增 5.6 小节 `KeepAliveService` 关键方法表（`start/stop/run_once`、并发跳过约定）。
- 第 10 章数据目录：删除 `logs/operation_history.txt` 行。
- 附录文件清单：新增 `core/keepalive.py`、`tests/`、`requirements-dev.txt`。

- [ ] **Step 4: 更新 requirements-dev.txt**

```
pytest
```

- [ ] **Step 5: 提交**

```bash
git add CODE_WIKI.md requirements-dev.txt
git commit -m "docs: batch1 (log merge + keepalive) sync"
```

---

## 验收总纲（批 1）

- [ ] `python -m pytest tests -v` 全绿
- [ ] 一次「首次登录 → 导出」流程后 `run_YYYYMMDD.log` 含系统与操作两类记录，时间有序
- [ ] 界面无操作历史区；右侧日志统一
- [ ] 保活开关与间隔生效；运行中任务时巡检跳过
- [ ] `python -m core.main_gui` 启动正常、有赞平台可见