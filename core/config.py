"""
全局配置: 路径 + 用户设置。

路径分两类 —— 绿色版(整包放任意目录、甚至只读介质)靠这个区分:
- **程序目录** `ROOT_DIR`: 代码、平台脚本、Playwright 内核, 只读也能跑;
- **工作空间** `DATA_ROOT`: 账单、浏览器数据(登录态)、日志、三个 json 配置、录制, 必须可写。

默认两者相同(就地模式, 老用户零迁移)。第一次运行(既没指过环境变量/命令行, 程序目录里
也没有 `workspace.json` 书签, 程序目录还不像已经用过)时, 会留一个"待用户选目录"的标记,
由界面问一次; 选定后在工作空间里放标记文件、在程序目录里放书签, 下次认路。
平台信息已迁移到 platforms/ 目录下的各平台脚本中, 不在这里维护。
"""

import json
import os
import sys

# 程序根目录(此文件位于 core/ 子目录,上溯一级)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRAM_DIR = ROOT_DIR          # 同义, 免得读代码时和"工作空间"混起来

# ===== 工作空间的几条线索 =====
WORKSPACE_MARKER = ".liushui_workspace.json"                  # 放工作空间里: 证明这目录是本工具的
WORKSPACE_POINTER = os.path.join(ROOT_DIR, "workspace.json")  # 放程序目录里: 记住工作空间在哪
# 程序目录只读时(绿色版放 U 盘/CD/受限目录)书签没处写, 退到用户目录这一份。
# 这是全仓唯一一处 `expanduser`: 有它才能"包只读、指针另放", 没有它每次启动都要重问一遍。
POINTER_FALLBACK = os.path.join(os.path.expanduser("~"), ".liushui_export", "workspace.json")
WORKSPACE_ENV = "LIUSHUI_DATA_DIR"                            # 环境变量指定
WORKSPACE_ARG = "--data-dir"                                  # 命令行指定: --data-dir=D:\流水数据

# 就地模式的痕迹: 有这些说明这份拷贝真被用过了(存过设置/勾过商户/建过定时任务),
# 别再拦着用户选目录。⚠ 不能拿 downloads/browser_data/logs 当痕迹 —— 日志目录是 import
# logger 时建的、downloads 与 browser_data 是 BrowserManager 构造时建的, 只要跑过一次
# (哪怕只是自检)就会出现, 拿它们当痕迹会让"全新的一份包"永远不问用户, 于是账单和登录
# 态悄悄写回只读的程序目录里。
LEGACY_MARKERS = ("settings.json", "selection_state.json", "scheduled_tasks.json")

# 解析中咽下的话(指针指向的目录没了/不可写...), 由界面在启动日志里交代
RESOLVE_NOTES = []


def _probe_writable(path):
    """目录能创建且能写删一个探针文件 → (True, ""); 否则 (False, 原因)。

    只 mkdir 不够: 只读介质/受限目录下 mkdir 可能成功而写入失败, 那时账单才写到一半报错。
    """
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe.tmp")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(probe)
        return True, ""
    except Exception as e:
        return False, str(e)[:120]


def marker_path(root):
    return os.path.join(root, WORKSPACE_MARKER)


def has_marker(root):
    """该目录里有本工具的工作空间标记吗(内容坏了也算, 说明用户没选错地方)。"""
    return os.path.isfile(marker_path(root))


def write_marker(root):
    """在工作空间里落标记。失败不抛: 只是下次认路麻烦一点, 不能拦启动。"""
    try:
        write_json_atomic(marker_path(root), {"app": "liushui_export", "version": 1})
        return True
    except Exception:
        return False


def _pointer_files():
    """书签的候选位置: 程序目录优先, 其次用户目录(程序目录只读时用)。"""
    return [WORKSPACE_POINTER, POINTER_FALLBACK]


def read_pointer():
    """读书签, 拿上次记下的工作空间; 都没有/坏了返回空串。"""
    for path in _pointer_files():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            root = str(data.get("data_root") or "").strip() if isinstance(data, dict) else ""
            if root:
                return root
        except Exception:
            continue
    return ""


def write_pointer(root):
    """写书签; 程序目录写不了就写用户目录那份。两处都写不了返回 False。"""
    for path in _pointer_files():
        try:
            write_json_atomic(path, {"data_root": os.path.abspath(root)})
            return True
        except Exception:
            continue
    return False


def default_workspace_suggestion():
    """给用户当默认值的建议位置: 文档/流水导出工作空间(拿不到文档目录就用用户主目录)。"""
    home = os.path.expanduser("~")
    for base in (os.path.join(home, "Documents"), home):
        if os.path.isdir(base):
            return os.path.join(base, "流水导出工作空间")
    return os.path.abspath("流水导出工作空间")


def _cli_data_root(argv=None):
    """从命令行摘出 `--data-dir=...`(摘掉后别让它继续传给业务代码)。"""
    args = list(sys.argv[1:] if argv is None else argv)
    for a in args:
        if a.startswith(WORKSPACE_ARG + "="):
            return a.split("=", 1)[1].strip()
        if a == WORKSPACE_ARG:
            i = args.index(a)
            if i + 1 < len(args):
                return args[i + 1].strip()
    return ""


def resolve_data_root():
    """定工作空间, 返回 (路径, 是否还要问用户)。

    优先级: 命令行 `--data-dir=` > 环境变量 `LIUSHUI_DATA_DIR` > 程序目录书签
    > 就地(程序目录已有使用痕迹) > 程序目录并标记"待用户选"。
    取到但不可写时不硬崩: 退回程序目录, 并把原因放进 RESOLVE_NOTES 让日志说。
    """
    del RESOLVE_NOTES[:]
    for source, cand in ((WORKSPACE_ARG, _cli_data_root()),
                         ("环境变量", os.environ.get(WORKSPACE_ENV, "").strip()),
                         ("书签", read_pointer())):
        if not cand:
            continue
        ok, why = _probe_writable(cand)
        if ok:
            if source == "书签" and not has_marker(cand):
                # 书签指向的目录还在, 只是标记被手动删了: 认它, 顺手补回标记
                write_marker(cand)
            return os.path.abspath(cand), False
        RESOLVE_NOTES.append(f"{source}指定的工作空间不可用({cand}): {why or '目录不存在或不能写'}")
    if any(os.path.exists(os.path.join(ROOT_DIR, m)) for m in LEGACY_MARKERS):
        return ROOT_DIR, False          # 老拷贝就地模式, 不打扰
    return ROOT_DIR, True               # 全新的一份: 先按程序目录跑, 稍后问用户


def _derive(root):
    """由工作空间算出全部派生路径。"""
    return {
        "DATA_ROOT": root,
        "DOWNLOAD_DIR": os.path.join(root, "downloads"),
        "BROWSER_DATA_DIR": os.path.join(root, "browser_data"),
        "LOG_DIR": os.path.join(root, "logs"),
        "RECORDINGS_DIR": os.path.join(root, "recordings"),
        "SETTINGS_FILE": os.path.join(root, "settings.json"),
        "SCHEDULED_TASKS_FILE": os.path.join(root, "scheduled_tasks.json"),
        "SELECTION_FILE": os.path.join(root, "selection_state.json"),
    }


def apply_data_root(root, remember=True):
    """换工作空间: 重算所有派生常量(界面在用户选完目录后调), 并按需落标记/书签。

    ⚠ 各模块是 `from .config import DOWNLOAD_DIR` 这种**取值拷贝**, 已经 import 完再换
    值对它们无效 —— 所以界面选完目录后要重启进程(见 main_gui.main())。
    """
    ok, why = _probe_writable(root)
    if not ok:
        return False, f"这个目录不能写入: {why}"
    globals().update(_derive(os.path.abspath(root)))
    globals()["PENDING_PICK"] = False
    if remember:
        write_marker(globals()["DATA_ROOT"])
        write_pointer(globals()["DATA_ROOT"])
    return True, globals()["DATA_ROOT"]


def describe_data_root():
    """给人看的一句: 工作空间在哪、和程序目录是否同一个。"""
    same = os.path.abspath(DATA_ROOT) == os.path.abspath(ROOT_DIR)
    return f"工作空间: {DATA_ROOT}" + ("（与程序同目录）" if same else "")


# ===== Playwright 浏览器内核目录(三级优先) =====
# 放在 config 而不是 browser: 依赖体检(core/deps.py)要在**没装 playwright 的机器**上判断
# "内核在不在", 而 core.browser 顶层就 import playwright, 请不动它。
# 内核目录名带版本号(chromium-1234), 必须由同版本 playwright 生成, 混用两套会直接起不来。
# 第 2 档是**程序目录**而不是工作空间: 绿色包带的内核在 <包>/runtime/ms-playwright,
# 产出空间里放的永远是账单/登录态/日志, 不放内核。
PW_BROWSERS_PATH = r"C:\pw_browsers"        # 第 3 档: 老开发机上的既有位置
PW_BROWSERS_DEFAULT = "ms-playwright"       # 一个都不命中时 Playwright 自己的默认目录名


def resolve_browsers_path(env=None, program_dir=None, fallback=PW_BROWSERS_PATH):
    """挑内核目录: 外部已设的 PLAYWRIGHT_BROWSERS_PATH > 包内 runtime/ms-playwright > C:\\pw_browsers。

    只认**真实存在**的目录(设了个不存在的路径不如不设), 一个都不存在时返回空串,
    交给 Playwright 的默认位置(Windows 上是 %LOCALAPPDATA%\\ms-playwright)。
    """
    env = os.environ if env is None else env
    program_dir = PROGRAM_DIR if program_dir is None else program_dir
    outside = (env.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if outside and os.path.isdir(outside):
        return outside
    inside = os.path.join(program_dir, "runtime", "ms-playwright")
    if os.path.isdir(inside):
        return inside
    if fallback and os.path.isdir(fallback):
        return fallback
    return ""


def default_browsers_root(env=None):
    """Playwright 自己那个默认内核根(%LOCALAPPDATA%\\ms-playwright)。

    resolve_browsers_path() 返回空串时内核就落在这里 —— 体检要说"没找到内核"得连它一起看。
    """
    env = os.environ if env is None else env
    local = (env.get("LOCALAPPDATA") or "").strip()
    if not local:
        home = os.path.expanduser("~") if hasattr(os.path, "expanduser") else ""
        local = os.path.join(home, "AppData", "Local") if home else ""
    return os.path.join(local, PW_BROWSERS_DEFAULT) if local else ""


DATA_ROOT = ROOT_DIR            # 工作空间: 账单/浏览器数据/日志/配置都写在这里(文件末尾解析)
PENDING_PICK = False            # True = 全新的一份, 界面该问一次"账单存哪儿"

# 下面这几个是本模块对外提供的路径常量; 真实值在文件末尾按 DATA_ROOT 统一算出。
DOWNLOAD_DIR = os.path.join(DATA_ROOT, "downloads")               # 账单归档根
BROWSER_DATA_DIR = os.path.join(DATA_ROOT, "browser_data")        # 每商户一个 Chromium profile
LOG_DIR = os.path.join(DATA_ROOT, "logs")                         # 运行日志 / stats.jsonl / crash_*.txt
RECORDINGS_DIR = os.path.join(DATA_ROOT, "recordings")            # 「打开」窗口录制的点击流
SETTINGS_FILE = os.path.join(DATA_ROOT, "settings.json")          # 用户设置
SCHEDULED_TASKS_FILE = os.path.join(DATA_ROOT, "scheduled_tasks.json")   # 定时任务
SELECTION_FILE = os.path.join(DATA_ROOT, "selection_state.json")  # 平台/商户勾选状态

DEFAULT_SETTINGS = {
    "show_browser": True,        # 是否显示浏览器窗口(取消勾选=后台运行)
    "download_name_mode": "unified",  # 下载文件名: unified=前缀+原始名 / original=只加商户前缀
    "enable_keepalive": True,        # 登录保活开关
    "keepalive_interval_min": 30,    # 保活巡检间隔(分钟)
    "retry_times": 2,                # 失败自动重试次数(0=不重试)
    "retry_interval_s": 30,          # 首次重试间隔秒(后续递增×2)
    "preflight_login_check": True,   # 导出前统一查登录: 失效的一次列出、集中重登(定时任务不查)
    "cleanup_keep_days": 365,        # 老日志/老汇总副本保留天数(0=不清理)
}


def load_settings():
    """读取用户设置,缺失字段用默认值"""
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                for k, v in DEFAULT_SETTINGS.items():
                    if k in data:
                        settings[k] = data[k]
    except Exception:
        pass
    return settings


def write_text_atomic(path, text):
    """先写同目录临时文件再 os.replace, 不留半截文件(文本/源码同理)。"""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:                      # 失败的这次不算数, 别留垃圾文件
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path, data):
    """JSON 版原子写。

    这几个文件都是"界面每次改动就整体重写"的(settings / scheduled_tasks /
    selection_state): 直接 open("w") 覆盖时若进程在写入中途死掉, 文件会是截断的;
    而三处读取都是 except → 用默认值/返回空, 结果用户看到的是"配置和定时任务被
    静默清空"。os.replace 在同一磁盘卷上是原子替换。
    """
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))


def save_settings(settings):
    """保存用户设置: 只覆盖传进来的那几个键, 没传的键保持文件里的现状。

    界面各处都是"点一下只写自己那一两个键"(显示浏览器、文件名模式、保活开关、失败
    重试), 而旧实现是 `dict(DEFAULT_SETTINGS)` 再 update —— 于是勾一下"失败重试"就把
    首次重试间隔写回 30, 动一下保活开关就把文件名模式、显示浏览器统统恢复默认, 用户
    改过的设置在毫无提示的情况下被抹平。
    """
    try:
        merged = load_settings()          # 已按 DEFAULT_SETTINGS 兜底过缺失键
        if isinstance(settings, dict):
            merged.update(settings)
        write_json_atomic(SETTINGS_FILE, merged)
    except Exception:
        pass


# ===== 导入本模块时就把工作空间定下来 =====
# 放在文件末尾: 上面这些函数彼此有定义顺序依赖(write_marker 要用 write_json_atomic),
# 而各业务模块是 `from .config import DOWNLOAD_DIR` 取值拷贝 —— 必须在我们这边先算完。
DATA_ROOT, PENDING_PICK = resolve_data_root()
globals().update(_derive(DATA_ROOT))
