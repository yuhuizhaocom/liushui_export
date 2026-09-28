"""检查更新与就地更新: 只换程序文件, 不碰依赖、内核、账单、登录态。

发布链: 代码仓打 tag `vportable_<版本>` → CI 出两个 zip → 资产同时发到**公开的 release 仓**
(version.RELEASE_REPO)。走公开仓是前提: 代码仓私有, 匿名读不到它的 Release(实测 404)。

只下核心版 zip 的理由: 全量版上百 MB(自带解释器+依赖+内核), 业务机器上下不动, 而两个包的
程序部分完全一样(build_portable 的 APP_ITEMS), 所以几 MB 的核心包就够把程序文件换掉。
代价是三道闸门, 都不许绕过:
1. 只覆盖包内 `app/` 底下的东西; 顶层的启动器与 README 一概不动。
2. 全量版自带 playwright, 新代码要求的版本与包内自带的不一致时**拒绝就地更新** ——
   内核目录名带版本, 混版本直接起不来(CI 里"三处版本必须一致"钉的就是这件事)。
3. 程序目录只读(U 盘/光碟/受限目录)时不覆盖, 只把 zip 留在 updates/ 并说清手工解压的位置。

任何一步失败都只回一句说明, 不往外抛; 覆盖前逐个备份, 中途失败退回旧文件。
"""

import json
import os
import re
import shutil
import urllib.error
import urllib.request
import zipfile
from datetime import datetime

from . import config as cfg
from . import deps
from . import version as ver

# 内网/代理不通是常态, 查询这一步必须短: 卡住的只该是"更新", 不该是导出。
TIMEOUT_S = 6.0
DOWNLOAD_TIMEOUT_S = 60.0
API_URL = "https://api.github.com/repos/%s/releases/latest" % ver.RELEASE_REPO
CORE_SUFFIX = "-core.zip"
# 核心包里程序本体的根; 本地对应的就是程序目录。
PKG_APP_PREFIX = "app/"
# 与 build_portable.CORE_FORBIDDEN 同一条: 核心包里躺着这三样就是发错了包, 不能拿来覆盖。
# python/ 在包的顶层, 依赖与内核在 app/ 底下, 所以比对要看前两段(见 _forbidden_hit)。
BUNDLED_MARKERS = ("site-packages", "runtime")
FORBIDDEN_MEMBERS = ("python",) + BUNDLED_MARKERS
# 结构探针: 缺这个文件就不是本程序的核心包。
PROBE_MEMBER = PKG_APP_PREFIX + "core/browser.py"


class Release:
    """一次"远端最新版"的摘要。"""

    def __init__(self, tag, download_url, asset, size, page_url):
        self.tag = tag
        self.download_url = download_url
        self.asset = asset
        self.size = size
        self.page_url = page_url


def _member(name):
    """zip 条目名统一成正斜杠口径(Compress-Archive 两种分隔符都见过)。"""
    return str(name or "").replace("\\", "/").lstrip("./")


def _headers():
    # GitHub API 没有 User-Agent 直接 403, 这一句不是装饰。
    return {"User-Agent": "liushui-export/%s" % ver.APP_VERSION,
            "Accept": "application/vnd.github+json"}


def parse_release(payload):
    """把 /releases/latest 的 JSON 摘成 Release → (Release|None, 原因)。

    只认核心版那个资产: 认错成全量版就把"更新"变成"重下整个包"。
    """
    if not isinstance(payload, dict):
        return None, "发布服务器返回的内容不是一个对象"
    tag = str(payload.get("tag_name") or "")
    assets = [a for a in (payload.get("assets") or []) if isinstance(a, dict)]
    for a in assets:
        name = str(a.get("name") or "")
        if name.lower().endswith(CORE_SUFFIX):
            return (Release(tag=tag, download_url=str(a.get("browser_download_url") or ""),
                            asset=name, size=int(a.get("size") or 0),
                            page_url=str(payload.get("html_url") or "")), "")
    return None, "最新版里没有核心包(资产 %d 个: %s)" % (
        len(assets), ", ".join([str(a.get("name") or "") for a in assets][:3]) or "无")


def fetch_latest(timeout=TIMEOUT_S, opener=None):
    """查远端最新版 → (Release|None, 错误说明)。网络/解析问题一律折进第二项, 不抛。

    匿名调 GitHub API 的限额是按 IP 算的, 撞到 403 也只当"这次没查到"。
    """
    try:
        req = urllib.request.Request(API_URL, headers=_headers())
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        return None, "发布服务器返回 HTTP %s" % e.code
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:80])
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except Exception as e:
        return None, "发布服务器的回答读不出(%s)" % type(e).__name__
    return parse_release(payload)


def is_newer(release, local=None):
    local = ver.APP_VERSION if local is None else local
    return bool(release) and ver.is_newer(release.tag, local)


def update_dir():
    """更新缓存目录。取 config 的属性而不是 import 时的拷贝, 换工作空间后仍然跟得到。

    ⚠ 它必须在**工作空间**里而绝不能在 downloads 树下: 兜底扫描认的是"本轮新出现的文件",
    几百 MB 的 zip 冒进那棵树就会被当成账单认领走。
    """
    path = cfg.UPDATE_DIR
    os.makedirs(path, exist_ok=True)
    return path


def download(release, dest_dir=None, timeout=DOWNLOAD_TIMEOUT_S, opener=None):
    """把核心包下到 updates/ → (路径|None, 原因)。"""
    folder = dest_dir or update_dir()
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, release.asset)
    if os.path.exists(dest):
        try:
            os.remove(dest)
        except OSError as e:
            return None, "旧的 %s 删不掉(%s), 不动现有文件" % (release.asset, str(e)[:60])
    try:
        req = urllib.request.Request(release.download_url, headers=_headers())
        with ((opener or urllib.request.urlopen)(req, timeout=timeout)) as resp:
            with open(dest, "wb") as fh:
                shutil.copyfileobj(resp, fh, 1024 * 256)
    except Exception as e:
        _discard(dest)
        return None, "下载没完成(%s: %s)" % (type(e).__name__, str(e)[:80])
    if not os.path.isfile(dest):
        return None, "下载说完成了, 磁盘上却没有这份文件"
    got = os.path.getsize(dest)
    if release.size and got != release.size:
        _discard(dest)
        return None, "下到的大小与发布页写的对不上(%d != %d), 不动现有文件" % (got, release.size)
    return dest, ""


def _discard(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _forbidden_hit(name):
    """条目路径的前两段里有没有"这包不该带"的东西(python/… 或 app/site-packages/…)。"""
    for part in name.split("/")[:2]:
        if part in FORBIDDEN_MEMBERS:
            return part
    return ""


def inspect_package(zip_path):
    """校验核心包 → ({程序目录相对路径: zip 条目名}, 原因)。

    过不了就不覆盖: 不是 zip、缺探针文件、里面混着解释器/依赖/内核、app/ 底下没东西。
    只挑 `app/` 底下的成员; 顶层的启动器/README 属于"整包替换"的范围, 这里不动。
    """
    try:
        with zipfile.ZipFile(zip_path) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            names = [_member(i.filename) for i in infos]
            if PROBE_MEMBER not in names:
                return None, "这个包里没有 %s, 结构对不上本程序" % PROBE_MEMBER
            hit = ""
            for n in names:
                hit = _forbidden_hit(n)
                if hit:
                    break
            if hit:
                return None, ("这个包带着 %s, 不是只换程序的核心包 —— "
                              "别拿它覆盖现有安装" % hit)
            members = {}
            for i in infos:
                name = _member(i.filename)
                if not name.startswith(PKG_APP_PREFIX):
                    continue
                rel = name[len(PKG_APP_PREFIX):]
                # Zip Slip: `app/../../evil.py` 会写到程序目录之外(启动文件夹之类)。
                # 认不出来就当包有问题整批拒, 不许"跳过这一条继续"——那等于部分覆盖。
                if rel.startswith("/") or os.path.isabs(rel) or ".." in rel.split("/"):
                    return None, ("这个包里有越过程序目录的路径 %s, "
                                  "为防止写到别处, 整批不动" % name)
                if rel.endswith((".pyc", ".pyo")):
                    continue
                members[rel] = i.filename
            if not members:
                return None, "这个包在 app/ 底下没有可换的程序文件"
            return members, ""
    except zipfile.BadZipFile:
        return None, "下载到的东西不是一个 zip"
    except Exception as e:
        return None, "打不开这个包(%s: %s)" % (type(e).__name__, str(e)[:80])


def required_playwright_of(zip_path):
    """这个包里的代码要求的 playwright 版本(读包内 app/core/deps.py); 读不出返回空串。"""
    target = PKG_APP_PREFIX + "core/deps.py"
    try:
        with zipfile.ZipFile(zip_path) as z:
            for i in z.infolist():
                if _member(i.filename) == target:
                    text = z.read(i.filename).decode("utf-8", errors="replace")
                    m = re.search(r'REQUIRED_PLAYWRIGHT_VERSION\s*=\s*["\']([^"\']+)["\']', text)
                    return m.group(1) if m else ""
    except Exception:
        return ""
    return ""


def flavor():
    """这份安装自带依赖吗: 全量版(full) / 核心版或开发检出(core)。

    按 build_portable 的摆法认 —— 程序目录里有 site-packages 或 runtime 就是自带依赖。
    """
    app = cfg.PROGRAM_DIR
    for name in BUNDLED_MARKERS:
        if os.path.isdir(os.path.join(app, name)):
            return "full"
    return "core"


def dependency_gate(zip_path):
    """新代码要的 playwright 与本包自带的是不是一致 → (ok, 说明)。

    核心版没有自带依赖, 换完代码后启动时的 core/deps 体检自会引导安装, 所以只在全量版上拦。
    """
    want = required_playwright_of(zip_path)
    if not want:
        return False, "新包里读不出它要求的 playwright 版本, 先不就地更新"
    if flavor() != "full":
        return True, ""
    have = deps.installed_version()
    if not have:
        return True, ""                       # 连自己也读不出就交给启动体检, 这里不瞎拦
    if have != want:
        return False, ("这个安装自带 playwright %s, 而新版本要求 %s —— "
                       "内核目录名带版本, 混用会直接起不来。请改用新的全量版整包"
                       % (have, want))
    return True, ""


def program_writable():
    """程序目录能不能写(U 盘/光碟/受限目录写不了) → (ok, 原因)"""
    if not os.path.isdir(cfg.PROGRAM_DIR):
        return False, "找不到程序目录: %s" % cfg.PROGRAM_DIR
    ok, why = cfg._probe_writable(cfg.PROGRAM_DIR)
    return (True, "") if ok else (False, why or "这个目录不能写入")


def _stamp_dir(root):
    """updates/backup/<日期时刻>; 同一秒里再来一次就加序号, 两份备份都得在。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(root, ts)
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(root, "%s_%d" % (ts, n))
    os.makedirs(path)
    return path


def apply_update(zip_path, log=None, program_dir=None, backup_root=None):
    """把核心包里的程序文件覆盖到程序目录 → (True/False, 说明)。

    先把将被覆盖的旧文件备份, 任何一个文件写失败就整批退回; 不删本地多出来的文件
    (那可能是用户自己加的平台脚本); 换完清掉 __pycache__, 免得旧字节码被复用。
    """
    say = log or (lambda msg: None)
    members, why = inspect_package(zip_path)
    if members is None:
        return False, why
    app = program_dir or cfg.PROGRAM_DIR
    backed = {}
    written = []
    bundle = ""
    tmp = ""
    try:
        root = backup_root or os.path.join(update_dir(), "backup")
        os.makedirs(root, exist_ok=True)
        bundle = _stamp_dir(root)
        with zipfile.ZipFile(zip_path) as z:
            for rel, entry in sorted(members.items()):
                dst = os.path.join(app, *rel.split("/"))
                if os.path.isfile(dst):
                    keep = os.path.join(bundle, *rel.split("/"))
                    os.makedirs(os.path.dirname(keep), exist_ok=True)
                    shutil.copy2(dst, keep)
                    backed[dst] = keep
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                # 暂存名跟着 config.write_text_atomic 的 `名字.tmp-<pid>-<序号>` 走,
                # 半截文件才不会冒进 git status(开发时程序目录就是仓库根)。
                tmp = "%s.tmp-%d-1" % (dst, os.getpid())
                with open(tmp, "wb") as fh:
                    fh.write(z.read(entry))
                os.replace(tmp, dst)
                tmp = ""
                written.append(dst)
                say("[更新] 已换 %s" % rel)
    except Exception as e:
        _discard(tmp)                # 写了一半的暂存文件不能留在程序目录里
        restored = _roll_back(backed, written, say)
        note = "" if restored else "; 回滚也没弄干净, 备份在 %s" % bundle
        return False, "覆盖失败(%s: %s), 已退回原来的文件%s" % (
            type(e).__name__, str(e)[:80], note)
    _drop_pycache(app)
    return True, ("已换 %d 个文件, 旧文件备份在 %s; 重启工具后生效"
                  % (len(written), bundle))


def _roll_back(backed, written, say):
    """旧文件逐个复原, 新版本才有的新文件删掉。全部干净返回 True。"""
    clean = True
    for dst, keep in backed.items():
        try:
            shutil.copy2(keep, dst)
        except Exception as e:
            clean = False
            say("[更新] %s 复原失败(%s)" % (os.path.basename(dst), str(e)[:50]))
    for dst in written:
        if dst in backed:
            continue
        try:
            if os.path.isfile(dst):
                os.remove(dst)
        except OSError:
            clean = False
    return clean


def _drop_pycache(app):
    for dirpath, dirnames, _files in os.walk(app):
        if "__pycache__" in dirnames:
            dirnames.remove("__pycache__")
            try:
                shutil.rmtree(os.path.join(dirpath, "__pycache__"), ignore_errors=True)
            except Exception:
                pass
