"""子商户清单与归属比对 —— 给"一个主账号下切着导多个子商户"的平台用(银联这类)。

为什么要有这个模块: 主账号一次登录能省下 N-1 次扫码, 但省下登录的同时也就丢掉了
"人眼看着切对了才点导出"这道人工校验。所以对账场景里最危险的不是多写几行配置, 而是
**切换没成功却照样下载** —— A 的账单归到 B 名下, 文件是真的、日期是真的、校验也过得去,
只有归属是错的, 查起来最费工夫。本模块的 `matches()` 就是那道机器版的"看一眼当前是谁"。

清单存在工作空间的 `sub_merchants.json` 里(键 = "平台key/商户名"), 不放 settings.json:
那个文件每次点勾都会被整体重写, 把业务档案塞进设置里只会扩大坏文件的爆炸半径。
"""
import os
import re

# 页面读回的商户几乎从来不会和用户录的那串一模一样("商户号:8234000540"、全角括号、
# 多一个空格), 所以比对前先归一化。**归一化只用于比对**: 落盘与目录名仍用用户写的那份。
_LABEL_WORDS = ("子商户号", "子商户名称", "子商户", "商户号", "商户名称", "商户编号",
                "商户", "机构号", "站点号", "门店编号", "当前商户", "登录商户")
# 全角到半角逐对写明, 不用 zip 两个字符串 —— 两边长度对不齐会**静默错位**,
# 结果是 "8234000540" 被翻译成另一个数字串, 归属比对永远对不上, 而且错得毫无征兆。
_FULLWIDTH_MAP = str.maketrans({
    "（": "(", "）": ")", "：": ":", "，": ",", "；": ";", "、": ",",
    "－": "-", "—": "-", "．": ".", "　": " ",
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
})

_CJK_AND_ALNUM = "[^0-9a-zA-Z一-鿿]"


def normalize(text):
    """比对用的归一化: 全角转半角、去掉标签词/标点/空白、统一小写。"""
    s = str(text or "").translate(_FULLWIDTH_MAP)
    for word in _LABEL_WORDS:
        s = s.replace(word, "")
    s = re.sub(_CJK_AND_ALNUM, "", s)
    return s.casefold()


def _glues(ch):
    """这个字符会不会把紧邻的片段"粘"进更长的一串里。

    只认 ASCII 字母数字: 汉字是词不是数, "8234000540已激活" 里的"已"应当算边界;
    而 "98234000540" 里那个 "9" 不算 —— 短号蒙中长号正是必须拦住的那种错。
    """
    return bool(ch) and ch.isascii() and ch.isalnum()


def _standalone(needle, hay):
    """needle 作为**独立片段**出现在 hay 里(两侧不粘着字母数字)才算命中。

    少了这条边界判定, 录错的短号 "8234" 会因为页面显示 "8234000540" 而"匹配通过",
    然后带着错的归属去点导出 —— 那正是本模块要拦住的事。
    """
    if not needle:
        return False
    for m in re.finditer(re.escape(needle), hay):
        if not _glues(hay[m.start() - 1:m.start()]) and not _glues(hay[m.end():m.end() + 1]):
            return True
    return False


def matches(expected, on_page):
    """页面上读回的当前商户是不是我们要的那个。任何一侧为空都算"不确定"= 不匹配。"""
    a, b = normalize(expected), normalize(on_page)
    if not a or not b:
        return False
    if a == b:
        return True
    return _standalone(a, b) or _standalone(b, a)


def clean_list(raw):
    """把用户写的一堆子商户(列表或换行/逗号/顿号分隔的一整段)清成一份清单。

    清洗顺序有讲究: 先按分隔符拆, 再用 `outputs.sanitize_name` 去掉路径非法字符 ——
    这些名字会变成 downloads 下的一层目录名, 带着 `..` 或 `\\` 就是目录逃逸。
    """
    from .outputs import sanitize_name
    if isinstance(raw, str):
        parts = re.split(r"[\r\n,，、;；]+", raw)
    else:
        parts = list(raw or [])
    out = []
    for p in parts:
        name = sanitize_name(str(p).strip(), fallback="")
        # sanitize 会把 `/` `\` 换成下划线, 但 "." 和 ".." 本身没有非法字符 —— 它们会变成
        # downloads/平台/商户/ 下一层"跳出父目录"的名字。整串只有点/下划线的同理:
        # Windows 还会把结尾的点吃掉, 建出来是什么位置没人说得清。
        if not name.strip("._"):
            continue
        if name not in out:
            out.append(name)
    return out


def entry_key(platform_key, merchant):
    """清单的键。商户名先过一遍清洗, 与 profile 目录用同一套算法, 免得三处漂移。"""
    from .outputs import sanitize_name
    return "%s/%s" % (str(platform_key or "").strip(), sanitize_name(merchant, fallback=""))


def load(path=None):
    """读清单。返回 (dict, note) —— 读不到/坏文件一律回退空清单, 但要把话说清。

    坏文件不能抛: 导出流程拿不到子商户清单时, 最多是"这一家按没配子商户处理",
    不该把整批账单挡掉(全仓的附加检查都是这条线)。
    """
    from .config import SUB_MERCHANTS_FILE
    path = path or SUB_MERCHANTS_FILE
    if not os.path.isfile(path):
        return {}, ""
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {}, "子商户清单读不出来(%s), 本次按未配置处理: %s" % (type(e).__name__, path)
    if not isinstance(data, dict):
        return {}, "子商户清单格式不对(顶层不是对象), 本次按未配置处理: %s" % path
    return data, ""


def get(platform_key, merchant, data=None, path=None):
    """某个商户下的子商户清单(永远是清洗过的列表)。"""
    store, note = (data, "") if data is not None else load(path)
    subs = store.get(entry_key(platform_key, merchant)) or []
    return clean_list(subs), note


def save_one(platform_key, merchant, subs, path=None):
    """写一个商户的子商户清单; 空清单等于删掉这一条。返回 (成功?, 清单, 失败原因)。

    整份重写走 `config.write_json_atomic`: 这是业务档案, 写一半等于把用户录过的所有子
    商户抹掉, 所以宁可失败退回原样也不裸 `open("w")`。
    """
    from .config import SUB_MERCHANTS_FILE, write_json_atomic
    path = path or SUB_MERCHANTS_FILE
    cleaned = clean_list(subs)
    store, _note = load(path)
    store = dict(store)
    key = entry_key(platform_key, merchant)
    if cleaned:
        store[key] = cleaned
    else:
        store.pop(key, None)
    try:
        write_json_atomic(path, store)      # 它自己会建目录, 失败时抛出来
    except Exception as e:
        return False, cleaned, "没能保存(权限或磁盘满了?): %s" % (e,)
    return True, cleaned, ""


def count_for(platform_key, merchant, path=None):
    n, _ = get(platform_key, merchant, path=path)
    return len(n)
