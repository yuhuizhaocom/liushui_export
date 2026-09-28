"""程序版本号与发布坐标。

只用标准库、单独成模块, 为的是三处都能廉价读它: 界面标题、「检查更新」(core/update.py)、
以及 CI 打 tag 时核"tag 与常量是否同一个版本"。以前版本只活在 `vportable_0.0.6` 这种 tag 里,
代码里一处都没有, 用户问"手上这份是哪版"没人答得出。
公开 release 仓与代码仓分开是必要的: 代码仓私有, 匿名读不到它的 Release(实测 api 与网页都
是 404), 而业务用户手里没有 GitHub 权限 —— 更新包必须放在匿名可下载的地方。
"""

APP_VERSION = "0.0.6"

# tag 的前缀。历史上带 "portable" 是为了区分"绿色包"和其他 tag, 解析时要剥掉。
TAG_PREFIX = "vportable_"

# 只放成品 zip 的公开仓库(代码仓保持私有)。改名要连 CI 里那一步一起改。
RELEASE_REPO = "yuhuizhaocom/liushui-export-releases"


def parse(text):
    """把"0.0.6"/"vportable_0.0.6"这类写成可比较的元组; 认不出来返回 None。

    只取数字段: 后缀(-beta 之类)一律忽略, 因为发布页上出现的永远是纯三段号。
    """
    if not text:
        return None
    core = str(text).strip()
    if core.startswith(TAG_PREFIX):
        core = core[len(TAG_PREFIX):]
    elif core.startswith("v"):
        core = core[1:]
    parts = core.replace("-", ".").split(".")
    nums = []
    for p in parts:
        if not p.isdigit():
            break
        nums.append(int(p))
    return tuple(nums) if nums else None


def from_tag(tag):
    """Release 的 tag → 版本元组(vportable_0.0.7 → (0, 0, 7))。"""
    return parse(tag)


def is_newer(remote, local):
    """远端是否比本地新。任一版本读不出 → False(宁可不提示, 也不拿脏数据让用户覆盖)。"""
    r, l = parse(remote), parse(local)
    if r is None or l is None:
        return False
    n = max(len(r), len(l))
    return r + (0,) * (n - len(r)) > l + (0,) * (n - len(l))
