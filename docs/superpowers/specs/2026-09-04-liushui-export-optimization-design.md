# 流水自动导出工具 v1.1 优化设计（Spec）

> 日期：2026-09-04
> 状态：设计已获确认（brainstorming 产出）

## 1. 背景与目标

当前 v1.0 是单入口 Tkinter 桌面工具（`core/main_gui.py` + 插件式平台 `platforms/*/export.py`），仅内置有赞平台。本次优化目标：让工具更"实用"，覆盖 5 项增强，**分 3 批迭代交付**。

已确认的需求决策：

| 方向 | 决策 |
|------|------|
| 日志 | 两套日志合并为「单文件 + 单视图」 |
| 登录保活 | 程序运行期后台周期刷新（无托盘驻留） |
| 平台管理 | 表单向导生成骨架 + 内置编辑器 |
| 脚本调试 | 步骤化试运行 + 实时日志 + 每步截图（不做断点暂停） |
| 定时任务 | cron 表达式调度（轻量自实现解析，不引入第三方依赖） |
| 额外增强 | 失败自动重试 |

## 2. 总体架构

### 2.1 新增/修改模块

```
core/
├── main_gui.py          # 主窗口：三栏保留；日志区改单视图；顶部/中间栏新增功能入口
├── scheduler.py         # 【新】CronScheduler：cron 解析 + 后台调度线程 + 任务持久化
├── keepalive.py         # 【新】KeepAliveService：登录保活后台线程
├── platform_admin.py    # 【新】PlatformWizard / PlatformEditor / DebugProbe（调试执行器）
├── logger.py            # 统一日志（合并后唯一写入通道）
└── browser.py / exporters.py / loader.py / platform_base.py / config.py  # 基本不动
```

### 2.2 数据文件

| 文件 | 说明 | .gitignore |
|------|------|-----------|
| `scheduled_tasks.json` | 定时任务持久化（项目根） | 新增忽略 |
| `settings.json` | 扩展 `keepalive_interval_min`（默认 30）、`retry_times`（默认 2）、`retry_interval_s`（默认 30）、`enable_keepalive`（默认 true） | 已忽略 |
| `logs/run_YYYYMMDD.log` | 合并后的唯一日志文件 | 已忽略 |

### 2.3 并发约束（全局约定）

- 后台线程（调度触发 / 保活 / 调试执行）与手动导出**串行互斥**：统一复用 `LiushuiApp.running` 任务锁（`_run_async` 已实现互斥，新增入口同样走它）。
- 后台线程更新 GUI 一律通过 `root.after(0, ...)` 切主线程，不得直接操作控件。
- 任何长任务（导出/调试）期间保活线程暂停一轮。

## 3. 批 1：日志合并 + 登录保活

### 3.1 日志合并（单文件 + 单视图）

- `logger.log(message, level, callback)` 成为唯一写入通道；**系统日志与用户操作日志**统一调用它。
- `main_gui.py` 中 `_record_action` 与 `_append_log` 合并：
  - 统一进入右侧日志视图（保留现有深色样式、时间戳 `[HH:MM:SS]` 前缀、行数上限）。
  - 所有内容落 `logs/run_YYYYMMDD.log`。
- 移除中间栏「操作历史」区块与 `HISTORY_FILE`/`operation_history.txt`/`_load_history`/`_record_action` 旧逻辑；`_record_action` 语义保留为「用户操作级日志」（换用 `log()`，级别 `info`），供界面统一展示。
- 日志按级别区分前缀（如 `[WARN]`/`[ERROR]`），供界面/文件辨别。

验收：一次完整「首次登录→导出」流程后，`run_YYYYMMDD.log` 包含系统与操作两类记录且时间有序；界面右侧日志区可见同一序列；中间历史区移除。

### 3.2 登录保活（KeepAliveService）

- 新文件 `core/keepalive.py`：
  - `KeepAliveService(app)`：接收主窗口对象，持有配置（启用开关、间隔分钟）。
  - `start()` 启动后台守护线程：循环 `while not stop:`，每次休眠一个间隔周期；到点后**仅当 `app.running == False`** 才执行一轮巡检；否则跳过本轮。
  - `stop()` 置停止标志。
  - 巡检逻辑：遍历「当前已勾选且有商户的」平台 × 商户 → 对每个商户用**独立 `BrowserManager`**（`set_browser_profile(key, merchant)`）打开 `login_url`，`navigate` + `sleep(3)` + `close_popup()` 后关闭；用 `get_page_info()` 判断会话：URL 含 `login` 或标题含「登录」→ 标记平台灯红色 + 日志「登录已失效，建议重新登录」；否则更新为绿色。
  - 每轮巡检所有商户之间串行；**并发约束明确为**：轮询时检查 `app.running`，若正在执行导出/调试/其他任务则**跳过本轮**，绝不与任务并发。
- GUI：主界面「显示浏览器」一行的可选配置区增加「启用保活」勾选 + 间隔输入（Spinbox，分钟），修改即 `save_settings`。

验收：启用保活后日志出现周期巡检记录；勾选商户的登录态定期刷新；导出任务运行时巡检自动跳过；关闭保活开关即停止。

## 4. 批 2：平台管理 + 脚本调试

### 4.1 表单向导（PlatformWizard）

- `core/platform_admin.py` 内 `PlatformWizard(root, on_created)` 弹窗：
  - 字段：key（字母数字下划线，必填）、name（中文名，必填）、login_url（可选）、export_url（可选）、guide（可选）。
  - 校验：key 与 `platforms/` 现有目录不冲突；非法字符即时提示。
  - 保存动作：创建 `platforms/<key>/__init__.py`（空文件）+ `export.py`，后者由模板渲染：
    ```python
    """<name>平台导出脚本"""
    from core.platform_base import PlatformBase

    class <Key>Exporter(PlatformBase):
        key = "<key>"
        name = "<name>"
        login_url = "<login_url>"
        export_url = "<export_url>"
        guide = "<guide>"
        # 不写 export() 时使用默认智能导出(SmartExporter)
    ```
  - 完成后回调 `discover_platforms()` 刷新主界面平台列表。
- 弹窗样式遵循现有紧凑弹窗规范（单列 label+entry、底部「确定/取消」）。

### 4.2 内置编辑器（PlatformEditor）

- 弹窗：上侧工具条（平台下拉、保存按钮、语法状态标签），主体 `tk.Text` 编辑 `platforms/<key>/export.py` 内容。
- 「保存」流程：写盘 → `py_compile` 语法检查 → 成功则 `reload_platforms()`（重新 `discover_platforms()`）并提示成功；失败则不清除用户文本、状态标签显示「第 N 行：错误摘要」，原平台继续可用。
- 不实现完整语法高亮；仅实现行号区（可选）与关键字着色（保持轻量）。

### 4.3 调试执行器（DebugProbe）

- `DebugProbe` 包装 `BrowserManager`：作为 `browser` 参数传入平台 `export()`，拦截以下调用并记录步骤：
  - `navigate / open_youzan_record` → 记录目标 URL，成功后截图
  - `click_text / click_selector / safe_click / click_text` → 记录文字/选择器，截图
  - `fill_placeholder / fill_selector / safe_fill` → 记录目标与值（值截断保护隐私）
  - `sleep / is_visible_text / close_popup / begin_wait_download / wait_download / screenshot / get_page_info` → 记录参数与返回
  - 每步记录：`#N 动作(参数摘要) → 结果，耗时 Xs`，异常时记录异常摘要并高亮。
  - 内部持有真实 BrowserManager，`page` 等属性透传。
- 调试入口（主界面「调试」按钮 → 平台/日期选择弹窗）→ 后台线程执行 `plat.export(probe, start, end)`，探针回调把步骤流写入统一日志视图；结束回传 `success/manual/fail` 总状态。
- 调试浏览器 profile：`browser_data/_debug/<key>/`，**独立于商户登录态**。

验收：对已有赞脚本「试运行」，日志区逐步显示动作序列与截图；勾选「自动截图」默认开启；脚本抛错时步骤以错误级别标出且不弹窗崩溃。

## 5. 批 3：定时任务 + 失败重试

### 5.1 cron 调度（CronScheduler）

- `core/scheduler.py`：
  - `CronExpr`：解析 5 字段 cron（`minute hour day month weekday`），支持 `*`、`*/n`、`a-b`、`a,b`、`?`（days/weeks 中视为 `*`）。轻量自实现，**不新增第三方依赖**。提供 `match(dt)` 与 `next_run(after)`。
  - `CronJob`：`{id, name, cron, platforms:[key], merchants:[name], start_date, end_date, enabled}`。
  - `TaskStore`：读写 `scheduled_tasks.json`（默认键值/格式见附件）。
  - `CronScheduler(app)`：后台线程，每 30~60s 轮询一次所有启用任务，若 `now >= next_run 且任务锁空闲` 则触发执行（同样走 `_run_async` 管道的导出逻辑：按任务绑定的平台×商户×日期范围执行 `_do_export` 式的任务），触发后记录 `last_run`，错过时间戳的触发在下次启动跳过并记录。
- UI「定时任务」面板（Toplevel）：
  - 列表列：任务名 / 对象（平台·商户数·日期区间）/ cron / 启用 / 上次运行 / 下次运行；右键或按钮：新增、编辑、删除、启用/停用。
  - 编辑弹窗：名称输入、平台多选（复用现有商户选择逻辑 → **任务对象 = 勾选的平台×商户**）、日期范围（默认固定区间或空=每次用当天）、cron 输入 + 常用模板下拉（每天9:00 / 工作日18:00 / 每2小时 / 每周一9:00）+ 合法性校验提示。

验收：新增「每天 9:00 导出有赞全部商户」任务并保存；重启程序后任务仍在（持久化）；用测试用短 cron（如每分钟）能触发一次导出（可临时关闭真实平台避免误导出，或以仅日志的驱动模式验证）。

### 5.2 失败自动重试

- `_do_export` 内：单个任务的 `success/manual` 原样处理；`failed/异常` 进入重试逻辑：
  - 重试次数 `retry_times`（默认 2），间隔 `retry_interval_s`（默认 30，递增 `×2`）。
  - 每次重试：重新 `_ensure_browser` + `set_export_context` + `plat.export`；日志记录「尝试 i/N 失败，Xs 后重试」。
  - 最终仍失败才计数 failed、平台灯红色，弹窗提示一次。
- `retry_times=0` 等价关闭重试（v1.0 行为）。

验收：搭建一个必然失败的伪平台（如设一个无效 export_url）→ 日志出现重试序列；设置重试 0 次则直接失败；成功平台不受影响。

## 6. 错误处理与测试

### 6.1 通用错误处理
- 新增线程统一用 try/except 兜底：异常写日志（级别 error）+ 状态栏提示，线程不静默死亡。
- 所有表单（向导 / 编辑器 / 定时任务 / 保活设置）做输入校验，错误即时提示且不写坏数据。
- cron 解析失败：表单层驳回；任务文件损坏时跳过损坏项并记录。

### 6.2 测试清单
| 模块 | 用例 |
|------|------|
| `scheduler.CronExpr` | `* * * * *`、`0 9 * * *`、`*/30 * * * *`、`0 9 * * 1-5`、`0 9 1 * *` 的 `match` 与 `next_run` 断言（含跨天/跨月/周-日 OR 语义） |
| `keepalive` | 用假 BrowserManager 断言巡检调用顺序与「运行中跳过本轮」 |
| `platform_admin.DebugProbe` | 用假 BrowserManager 断言各拦截方法记录步骤与截图调用 |
| `platform_admin.Wizard` | 模板渲染字段转义、重名冲突提示 |
| `main_gui` | 日志合并后 `run_*.log` 同时含系统/操作记录；重试参数生效 |

## 7. 交付批次与验收总纲

| 批次 | 内容 | 验收 |
|------|------|------|
| 批 1 | 日志合并 + 登录保活 | §3.1 / §3.2 验收通过 |
| 批 2 | 平台向导 + 编辑器 + 调试探针 | §4.1~4.3 验收通过 |
| 批 3 | cron 定时任务 + 失败重试 | §5.1 / §5.2 验收通过 |

每批实施后均需回归：`python -m core.main_gui` 正常启动、`discover_platforms()` 返回 10 平台中已实现的有赞、`python -m py_compile` 全绿。

## 附录 A：scheduled_tasks.json 格式

```json
{
  "version": 1,
  "jobs": [
    {
      "id": "8f3c...",
      "name": "每日导出有赞",
      "cron": "0 9 * * *",
      "platforms": ["youzan"],
      "merchants": ["旗舰店A"],
      "title_row": false,
      "enabled": true,
      "last_run": null
    }
  ]
}
```

> 说明：`title_row` 为保留字段，v1.1 不启用；日期范围固定为「每天触发时使用当天前后（昨天到今天）」；`last_run` 记录最近一次触发时间用于展示「下次运行」。