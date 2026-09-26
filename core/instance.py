"""单实例守卫: 同一个工作空间同时只该开一个导出工具。

为什么要管: 商户目录本身就是 Chromium 的 profile, 而**一个 profile 同一时刻只能被一个
进程占用**(12 章第 1 条)。双开时另一边报回来的是"Sync API inside asyncio loop"这类
莫名其妙错, 用户完全联不到"我开了两份"。三个 json 与 `stats.jsonl` 也一样是"整体重写/
追加", 两个进程互相吞写(实测 10 线程各写 100 条会少 36 条, 而双进程是同一件事的更狠版本)。

⚠ 但**不许拦死**: 锁文件可能是上一次崩溃/被强杀留下的, 那时把它当"别人在跑"就成了
永远开不了。所以这里只做三件事 —— 判断、把结论交给调用方、由调用方问用户一句
(「只问不拦」，与依赖体检第 28 条同一条路子)。本模块任何异常都折成"放行"。
"""
import json
import os
import sys

LOCK_NAME = ".liushui_instance.lock"
_HELD = [""]          # 本次进程真正写下的锁路径(没写成功就别去删别人的)


def lock_path(data_root):
    return os.path.join(data_root or "", LOCK_NAME)


def _my_pid():
    return os.getpid()


def _pid_alive(pid):
    """这个进程号还活着吗。**判不出来时按"死了"处理**(宁可放行也不要开不了)。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True                 # 问不动就当活着
                return code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        import errno
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _read(path):
    """读出锁里记的进程号; 文件坏/读不动返回 None(按没人占处理)。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("pid") or 0) or None
    except Exception:
        return None


def claim(data_root):
    """占住这份工作空间。返回 ``(放行, 另一个还活着的进程号或 None)``。

    放行永远是 True —— 这个函数没有"拦死"的权力，它只负责说出"好像还有别人在用"。
    已经有一个活着的实例时**不覆盖它的锁**(否则两边都在删对方的文件)。
    """
    path = lock_path(data_root)
    try:
        other = _read(path)
        if other and other != _my_pid() and _pid_alive(other):
            return True, other
        folder = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pid": _my_pid(), "app": "liushui_export"}, f)
        _HELD[0] = path
        return True, None
    except Exception:
        return True, None            # 判断本身出错 = 不打扰用户


def release():
    """退出时清掉**自己写下的**那份锁; 清不掉也不报错(文件可能已被手工删)。"""
    path = _HELD[0]
    _HELD[0] = ""
    if not path:
        return
    try:
        if _read(path) == _my_pid():
            os.remove(path)
    except Exception:
        pass
