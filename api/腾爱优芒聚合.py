# -*- coding: utf-8 -*-
"""
腾爱优芒聚合 —— 影视聚合 Python 源（OK影视 / 蜂蜜影视 / TVBox 通用）
=====================================================================
直连四家官方 API，整合为三级导航：
  一级：平台（腾讯视频 / 爱奇艺 / 优酷视频 / 芒果TV）
  二级：各平台内容类型（电视剧 / 电影 / 动漫 / 综艺 / 纪录片 ...）
  三级：各类型下的筛选条（地区 / 年份 / 类型 / 资费 ...）
        - 爱奇艺：静态内置筛选
        - 腾讯 / 优酷 / 芒果：动态获取筛选
"""

import json
import os
import re
import time
import urllib.parse
import uuid
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed

# 网络请求：优先壳子内置 requests，缺失时降级 urllib
try:
    import requests as _requests
    _HAS_REQUESTS = True
except Exception:  # noqa: BLE001
    _requests = None
    _HAS_REQUESTS = False

# 兼容壳子内置基类
try:
    from base.spider import Spider as _BaseSpider
except Exception:  # noqa: BLE001
    class _BaseSpider:
        pass


def _log(*args):
    try:
        print("腾爱优聚合", *args)
    except Exception:  # noqa: BLE001
        pass


def _enc(value):
    return urllib.parse.quote(str(value), safe="!'()*-._~")


# ----------------------------------------------------------------------
# 一级平台定义
# ----------------------------------------------------------------------
_PLATFORMS = {
    "1":    {"key": "qq",    "name": "腾讯视频"},
    "2":    {"key": "qiyi",  "name": "爱奇艺"},
    "3":    {"key": "youku", "name": "优酷视频"},
    "4":    {"key": "mgtv",  "name": "芒果TV"},
}
# 平台 key -> 平台数字 id（反向索引）
_KEY_TO_PID = {v["key"]: k for k, v in _PLATFORMS.items()}

# 各平台：类型名 -> 真实 channel id（用于请求接口）
_QQ_TYPE_MAP = {
    "电视剧": "100113", "电影": "100173", "综艺": "100109", "纪录片": "100105",
    "动漫": "100119", "少儿": "100150", "短剧": "110755",
}
_IQIYI_TYPE_MAP = {
    "电影": "1", "电视剧": "2", "动漫": "3", "综艺": "4",
    "纪录片": "6", "短片": "7", "少儿": "8",
}
_YOUKU_TYPE_MAP = {n: n for n in ["电视剧", "电影", "综艺", "动漫", "少儿", "纪录片",
                                  "文化", "亲子", "教育", "搞笑", "生活", "体育", "音乐", "游戏"]}
_MGTV_TYPE_MAP = {
    "电影": "3", "电视剧": "2", "综艺": "1", "动画": "50",
    "少儿": "10", "纪录片": "51", "教育": "115",
}

# 兼容旧引用：各平台类型字典（type_id -> 类型名），供动态筛选使用
_QQ_CATEGORY = _QQ_TYPE_MAP
_IQIYI_CATEGORY = _IQIYI_TYPE_MAP
_YOUKU_CATEGORY = list(_YOUKU_TYPE_MAP.keys())
_MGTV_CATEGORY = _MGTV_TYPE_MAP

# 各平台「类型」筛选条（二级下拉）
def _type_filter(pkey):
    if pkey == "qq":
        names = list(_QQ_TYPE_MAP.keys())
    elif pkey == "qiyi":
        names = list(_IQIYI_TYPE_MAP.keys())
    elif pkey == "youku":
        names = list(_YOUKU_TYPE_MAP.keys())
    else:
        names = list(_MGTV_TYPE_MAP.keys())
    return {"key": "class", "name": "类型", "value": [{"n": n, "v": n} for n in names]}

# 通用「地区」筛选条（四家平台通用）
_AREA_FILTER = {"key": "area", "name": "地区", "value": [
    {"n": "全部地区", "v": ""},
    {"n": "内地", "v": "内地"}, {"n": "香港", "v": "香港"}, {"n": "台湾", "v": "台湾"},
    {"n": "美国", "v": "美国"}, {"n": "日本", "v": "日本"}, {"n": "韩国", "v": "韩国"},
    {"n": "泰国", "v": "泰国"}, {"n": "英国", "v": "英国"}, {"n": "其它", "v": "其它"},
]}

# 通用「年份」筛选条
_YEAR_FILTER = {"key": "year", "name": "年份", "value": [
    {"n": "全部年份", "v": ""},
    {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"}, {"n": "2024", "v": "2024"},
    {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"}, {"n": "2021", "v": "2021"},
    {"n": "2020", "v": "2020"}, {"n": "2019", "v": "2019"}, {"n": "2018", "v": "2018"},
    {"n": "2017", "v": "2017"}, {"n": "2016", "v": "2016"}, {"n": "2015", "v": "2015"},
    {"n": "2014", "v": "2014"}, {"n": "2013", "v": "2013"}, {"n": "2012", "v": "2012"},
    {"n": "2011", "v": "2011"}, {"n": "2010", "v": "2010"},
]}

# ----------------------------------------------------------------------
# 爱奇艺静态筛选（按 channel_id 归类）
# ----------------------------------------------------------------------
_IQIYI_FILTERS = {
    "1": [
        {"key": "mode", "name": "排序", "value": [
            {"n": "综合排序", "v": "24"}, {"n": "热播榜", "v": "11"},
            {"n": "新上线", "v": "8"}]},
        {"key": "area", "name": "地区", "value": [
            {"n": "全部地区", "v": "0"}, {"n": "内地", "v": "1"}, {"n": "香港", "v": "2"},
            {"n": "台湾", "v": "3"}, {"n": "美国", "v": "4"}, {"n": "韩国", "v": "5"},
            {"n": "日本", "v": "6"}, {"n": "泰国", "v": "7"}, {"n": "英国", "v": "8"},
            {"n": "其它", "v": "9"}]},
        {"key": "type", "name": "类型", "value": [
            {"n": "全部类型", "v": "0"}, {"n": "动作", "v": "1"}, {"n": "喜剧", "v": "2"},
            {"n": "爱情", "v": "3"}, {"n": "科幻", "v": "4"}, {"n": "恐怖", "v": "5"},
            {"n": "剧情", "v": "6"}, {"n": "战争", "v": "7"}, {"n": "悬疑", "v": "8"},
            {"n": "动画", "v": "9"}, {"n": "奇幻", "v": "10"}, {"n": "冒险", "v": "11"},
            {"n": "犯罪", "v": "12"}, {"n": "惊悚", "v": "13"}]},
        {"key": "year", "name": "年份", "value": [
            {"n": "全部年份", "v": "0"}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
            {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"},
            {"n": "2021", "v": "2021"}, {"n": "2020", "v": "2020"}, {"n": "2019", "v": "2019"},
            {"n": "2018", "v": "2018"}, {"n": "2017", "v": "2017"}, {"n": "2016", "v": "2016"},
            {"n": "2015", "v": "2015"}, {"n": "2011-2014", "v": "2011_2014"},
            {"n": "2000-2010", "v": "2000_2010"}, {"n": "90年代", "v": "1990_1999"},
            {"n": "80年代", "v": "1980_1989"}, {"n": "更早", "v": "-1980"}]},
        {"key": "pay", "name": "资费", "value": [
            {"n": "全部资费", "v": "0"}, {"n": "免费", "v": "1"}, {"n": "付费", "v": "2"}]},
    ],
    "2": [
        {"key": "mode", "name": "排序", "value": [
            {"n": "综合排序", "v": "24"}, {"n": "热播榜", "v": "11"},
            {"n": "新上线", "v": "8"}]},
        {"key": "area", "name": "地区", "value": [
            {"n": "全部地区", "v": "0"}, {"n": "内地", "v": "1"}, {"n": "港剧", "v": "2"},
            {"n": "韩剧", "v": "5"}, {"n": "美剧", "v": "4"}, {"n": "日剧", "v": "6"},
            {"n": "泰剧", "v": "7"}, {"n": "台湾地区", "v": "3"}, {"n": "英剧", "v": "8"},
            {"n": "其它", "v": "9"}]},
        {"key": "type", "name": "类型", "value": [
            {"n": "全部类型", "v": "0"}, {"n": "自制", "v": "1"}, {"n": "古装", "v": "2"},
            {"n": "言情", "v": "3"}, {"n": "武侠", "v": "4"}, {"n": "偶像", "v": "5"},
            {"n": "家庭", "v": "6"}, {"n": "青春", "v": "7"}, {"n": "都市", "v": "8"},
            {"n": "喜剧", "v": "9"}, {"n": "战争", "v": "10"}, {"n": "军旅", "v": "11"},
            {"n": "谍战", "v": "12"}, {"n": "悬疑", "v": "13"}, {"n": "罪案", "v": "14"},
            {"n": "穿越", "v": "15"}, {"n": "宫廷", "v": "16"}, {"n": "历史", "v": "17"},
            {"n": "神话", "v": "18"}, {"n": "科幻", "v": "19"}, {"n": "年代", "v": "20"},
            {"n": "农村", "v": "21"}, {"n": "商战", "v": "22"}, {"n": "剧情", "v": "23"},
            {"n": "奇幻", "v": "24"}, {"n": "网剧", "v": "25"}, {"n": "竖短片", "v": "26"}]},
        {"key": "year", "name": "年份", "value": [
            {"n": "全部年份", "v": "0"}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
            {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"},
            {"n": "2021", "v": "2021"}, {"n": "2020", "v": "2020"}, {"n": "2019", "v": "2019"},
            {"n": "2018", "v": "2018"}, {"n": "2017", "v": "2017"}, {"n": "2016", "v": "2016"},
            {"n": "2015", "v": "2015"}, {"n": "2011-2014", "v": "2011_2014"},
            {"n": "2000-2010", "v": "2000_2010"}, {"n": "90年代", "v": "1990_1999"},
            {"n": "80年代", "v": "1980_1989"}, {"n": "更早", "v": "-1980"}]},
        {"key": "pay", "name": "资费", "value": [
            {"n": "全部资费", "v": "0"}, {"n": "免费", "v": "1"}, {"n": "付费", "v": "2"}]},
    ],
    "3": [
        {"key": "mode", "name": "排序", "value": [
            {"n": "综合排序", "v": "24"}, {"n": "热播榜", "v": "11"},
            {"n": "新上线", "v": "8"}]},
        {"key": "area", "name": "地区", "value": [
            {"n": "全部地区", "v": "0"}, {"n": "国产", "v": "1"}, {"n": "日本", "v": "6"},
            {"n": "欧美", "v": "4"}, {"n": "韩国", "v": "5"}, {"n": "其它", "v": "9"}]},
        {"key": "type", "name": "类型", "value": [
            {"n": "全部类型", "v": "0"}, {"n": "热血", "v": "1"}, {"n": "恋爱", "v": "2"},
            {"n": "科幻", "v": "3"}, {"n": "奇幻", "v": "4"}, {"n": "冒险", "v": "5"},
            {"n": "搞笑", "v": "6"}, {"n": "战斗", "v": "7"}, {"n": "神魔", "v": "8"},
            {"n": "竞技", "v": "9"}, {"n": "日常", "v": "10"}, {"n": "校园", "v": "11"},
            {"n": "治愈", "v": "12"}, {"n": "悬疑", "v": "13"}]},
        {"key": "year", "name": "年份", "value": [
            {"n": "全部年份", "v": "0"}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
            {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"},
            {"n": "2021", "v": "2021"}, {"n": "2020", "v": "2020"}, {"n": "2019", "v": "2019"},
            {"n": "2018", "v": "2018"}, {"n": "2017", "v": "2017"}, {"n": "更早", "v": "-2017"}]},
        {"key": "pay", "name": "资费", "value": [
            {"n": "全部资费", "v": "0"}, {"n": "免费", "v": "1"}, {"n": "付费", "v": "2"}]},
    ],
    "4": [
        {"key": "mode", "name": "排序", "value": [
            {"n": "综合排序", "v": "24"}, {"n": "热播榜", "v": "11"},
            {"n": "新上线", "v": "8"}]},
        {"key": "area", "name": "地区", "value": [
            {"n": "全部地区", "v": "0"}, {"n": "内地", "v": "1"}, {"n": "港台", "v": "2"},
            {"n": "日韩", "v": "5"}, {"n": "欧美", "v": "4"}, {"n": "其它", "v": "9"}]},
        {"key": "type", "name": "类型", "value": [
            {"n": "全部类型", "v": "0"}, {"n": "真人秀", "v": "1"}, {"n": "脱口秀", "v": "2"},
            {"n": "选秀", "v": "3"}, {"n": "访谈", "v": "4"}, {"n": "情感", "v": "5"},
            {"n": "生活", "v": "6"}, {"n": "美食", "v": "7"}, {"n": "旅游", "v": "8"},
            {"n": "游戏", "v": "9"}, {"n": "音乐", "v": "10"}, {"n": "时尚", "v": "11"},
            {"n": "文化", "v": "12"}, {"n": "搞笑", "v": "13"}]},
        {"key": "year", "name": "年份", "value": [
            {"n": "全部年份", "v": "0"}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
            {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"},
            {"n": "2021", "v": "2021"}, {"n": "2020", "v": "2020"}, {"n": "2019", "v": "2019"},
            {"n": "更早", "v": "-2019"}]},
        {"key": "pay", "name": "资费", "value": [
            {"n": "全部资费", "v": "0"}, {"n": "免费", "v": "1"}, {"n": "付费", "v": "2"}]},
    ],
    "6": [
        {"key": "mode", "name": "排序", "value": [
            {"n": "综合排序", "v": "24"}, {"n": "热播榜", "v": "11"},
            {"n": "新上线", "v": "8"}]},
        {"key": "area", "name": "地区", "value": [
            {"n": "全部地区", "v": "0"}, {"n": "内地", "v": "1"}, {"n": "国外", "v": "9"}]},
        {"key": "type", "name": "类型", "value": [
            {"n": "全部类型", "v": "0"}, {"n": "自然", "v": "1"}, {"n": "历史", "v": "2"},
            {"n": "人文", "v": "3"}, {"n": "社会", "v": "4"}, {"n": "科技", "v": "5"},
            {"n": "探险", "v": "6"}, {"n": "军事", "v": "7"}, {"n": "传记", "v": "8"}]},
        {"key": "year", "name": "年份", "value": [
            {"n": "全部年份", "v": "0"}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
            {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"},
            {"n": "2021", "v": "2021"}, {"n": "2020", "v": "2020"}, {"n": "更早", "v": "-2020"}]},
        {"key": "pay", "name": "资费", "value": [
            {"n": "全部资费", "v": "0"}, {"n": "免费", "v": "1"}, {"n": "付费", "v": "2"}]},
    ],
    "7": [
        {"key": "mode", "name": "排序", "value": [
            {"n": "综合排序", "v": "24"}, {"n": "热播榜", "v": "11"},
            {"n": "新上线", "v": "8"}]},
        {"key": "year", "name": "年份", "value": [
            {"n": "全部年份", "v": "0"}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
            {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"}]},
        {"key": "pay", "name": "资费", "value": [
            {"n": "全部资费", "v": "0"}, {"n": "免费", "v": "1"}, {"n": "付费", "v": "2"}]},
    ],
    "8": [
        {"key": "mode", "name": "排序", "value": [
            {"n": "综合排序", "v": "24"}, {"n": "热播榜", "v": "11"},
            {"n": "新上线", "v": "8"}]},
        {"key": "type", "name": "类型", "value": [
            {"n": "全部类型", "v": "0"}, {"n": "动画", "v": "1"}, {"n": "儿歌", "v": "2"},
            {"n": "早教", "v": "3"}, {"n": "益智", "v": "4"}, {"n": "故事", "v": "5"},
            {"n": "科普", "v": "6"}]},
        {"key": "year", "name": "年份", "value": [
            {"n": "全部年份", "v": "0"}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
            {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"},
            {"n": "2021", "v": "2021"}, {"n": "更早", "v": "-2021"}]},
        {"key": "pay", "name": "资费", "value": [
            {"n": "全部资费", "v": "0"}, {"n": "免费", "v": "1"}, {"n": "付费", "v": "2"}]},
    ],
}


class Spider(_BaseSpider):
    """腾爱优芒聚合 —— 直连四家官方 API。"""

    name = "腾爱优聚合"

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def init(self, extend=""):
        _log("init ->", extend)
        self.userAgent = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/150.0.0.0 Safari/537.36")
        self.timeout = 15
        self._youku_typeid = {}
        self._youku_session = None
        self._qq_body = None
        self.extend = extend
        # 跳转搜索优化：限制耗时，避免点击后黑屏
        self._JUMP_TIMEOUT = 3.0       # 总超时预算（秒）
        self._JUMP_WORKERS = 12        # 并发搜索站源数
        self._JUMP_MIN_HITS = 3        # 命中此数量即可提前返回

        if _HAS_REQUESTS:
            self.session = _requests.Session()
            self.session.headers.update({"User-Agent": self.userAgent})
            # 优酷需要独立 session + cookie
            self._youku_headers = {
                "User-Agent": ("Mozilla/5.0 (; Windows 10.0.26100.3194_64 ) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/93.0.4577.82 Electron/14.2.0 Safari/537.36 "
                               "Node/14.17.0 YoukuDesktop/9.2.60"),
                "Referer": "https://www.youku.com/",
            }

    def getName(self):
        return "腾爱优芒"

    def destroy(self):
        pass

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        pass

    def localProxy(self, param):
        return None

    # ------------------------------------------------------------------
    # 通用网络请求
    # ------------------------------------------------------------------
    class _Resp:
        """统一响应包装：兼容 requests 与 urllib。"""
        def __init__(self, raw):
            self._raw = raw

        def json(self):
            if isinstance(self._raw, dict):
                return self._raw
            import json as _json
            return _json.loads(self._raw.read().decode("utf-8", "ignore"))

    def _http_get(self, url, headers=None, params=None):
        try:
            if _HAS_REQUESTS:
                sess = getattr(self, "session", None) or _requests
                return sess.get(url, headers=headers, params=params,
                                timeout=self.timeout, verify=False)
            import urllib.request
            if params:
                url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers=headers or {})
            return self._Resp(urllib.request.urlopen(req, timeout=self.timeout))
        except Exception as exc:  # noqa: BLE001
            _log("GET err", url, exc)
            return None

    def _http_post(self, url, json_body=None, headers=None):
        try:
            if _HAS_REQUESTS:
                sess = getattr(self, "session", None) or _requests
                return sess.post(url, json=json_body, headers=headers,
                                 timeout=self.timeout, verify=False)
            import urllib.request
            data = json.dumps(json_body or {}).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers or {})
            return self._Resp(urllib.request.urlopen(req, timeout=self.timeout))
        except Exception as exc:  # noqa: BLE001
            _log("POST err", url, exc)
            return None

    # ------------------------------------------------------------------
    # 首页：一级平台 + 类型筛选条
    # ------------------------------------------------------------------
    def homeContent(self, filter=False):
        classes = []
        filters = {}

        # 一级：平台（腾讯 -> 爱奇艺 -> 优酷 -> 芒果），数字 type_id
        for pid, pinfo in _PLATFORMS.items():
            classes.append({"type_id": pid, "type_name": pinfo["name"]})

        # 每个平台挂筛选条：类型 + 动态拉取的真实筛选（地区/年份等，参数正确）
        if filter:
            for pid, pinfo in _PLATFORMS.items():
                pkey = pinfo["key"]
                ft = [_type_filter(pkey)]
                # 动态/静态拉取该平台默认主类型的地区/年份等筛选条
                try:
                    extra = self._platform_filters(pkey)
                    if extra:
                        ft.extend(extra)
                except Exception as e:
                    _log("platform filters err", pkey, e)
                filters[pid] = ft

        result = {"class": classes}
        if filter:
            result["filters"] = filters
        return result

    def _platform_filters(self, pkey):
        """返回该平台默认主类型下的真实筛选条（仅保留地区/年份，参数为接口真实 id）。"""
        if pkey == "qiyi":
            raw = _IQIYI_FILTERS.get("2", [])
        elif pkey == "qq":
            raw = self._qq_get_filter("100113")
        elif pkey == "mgtv":
            raw = self._mgtv_get_filter("2")
        elif pkey == "youku":
            raw = self._youku_get_filter("电视剧")
        else:
            raw = []
        # 只保留地区/年份筛选条，删掉排序/资费/剧场/类型细分等
        keep_keys = {"area", "year", "iarea", "iyear", "mainArea"}
        return [f for f in raw if f.get("key") in keep_keys]

    def homeVideoContent(self):
        """首页推荐：聚合各平台首页数据，任一平台失败不影响整体。"""
        vlist = []
        for fn in (self._qiyi_home, self._mgtv_home, self._qq_home):
            try:
                vlist.extend(fn())
            except Exception as e:
                _log("homeVideo err", e)
        return {"list": vlist}

    def _qiyi_home(self):
        vlist = []
        for cid in ("2", "1", "3", "4"):
            params = {"channel_id": cid, "data_type": "1", "mode": "11",
                      "page_id": "1", "ret_num": "12", "session": ""}
            r = self._http_get(f"{self._IQIYI_PCW}/search/recommend/list",
                               params=params,
                               headers={"User-Agent": self.userAgent, "Referer": self._IQIYI_SITE})
            if r:
                data = r.json()
                if data.get("code") == "A00000" and data.get("data", {}).get("list"):
                    for item in data["data"]["list"][:6]:
                        vlist.append(self._qiyi_parse_item(item))
            if len(vlist) >= 24:
                break
        return vlist

    def _mgtv_home(self):
        import time as _t
        url = (f"https://dc.bz.mgtv.com/dynamic/v1/channel/index/0/0/0/1000000/0/0/17/1354"
               f"?type=17&version=5.0&t={str(int(_t.time()*1000))}&_support=10000000")
        r = self._http_get(url, headers=self._MGTV_HEADERS)
        vlist = []
        if r:
            data = r.json()
            for i in data.get("data", []):
                for j in i.get("DSLList", []):
                    for k in (j.get("data", {}) or {}).get("items", []):
                        vlist.append({
                            "vod_id": self._make_vod_id("mgtv", k.get('videoId'), k.get("videoName")),
                            "vod_name": k.get("videoName"),
                            "vod_pic": k.get("img"),
                            "vod_year": k.get("cornerTitle"),
                            "vod_remarks": k.get("time") or k.get("desc"),
                        })
        return vlist

    def _qq_home(self):
        vlist = []
        r = self._http_get(self._QQ_HOST, headers=self._QQ_HEADERS)
        if not r:
            return vlist
        try:
            from pyquery import PyQuery as pq
            import re as _re
            text = r.text if hasattr(r, "text") else r.read().decode("utf-8", "ignore")
            doc = pq(text)
            s = None
            for it in doc("script").items():
                if "window.__INITIAL_STATE__" in it.text():
                    s = it.text()
                    break
            if s:
                idx = s.find("=")
                if idx != -1:
                    sd = json.loads(s[idx + 1:])
                    cards = sd.get("storeModulesData", {}).get("channelsModulesMap", {}).get(
                        "choice", {}).get("cardListData", [])
                    for its in cards:
                        for it in (its or {}).get("children_list", {}).get("list", {}).get("cards", []):
                            if it and it.get("params"):
                                p = it["params"]
                                tag = json.loads(p.get("uni_imgtag", "{}") or p.get("imgtag", "{}") or "{}")
                                cid = it.get("id") or p.get("cid")
                                name = p.get("mz_title") or p.get("title")
                                if name and cid and "http" not in str(cid):
                                    vlist.append({
                                        "vod_id": self._make_vod_id("qq", cid, name),
                                        "vod_name": name,
                                        "vod_pic": p.get("image_url"),
                                        "vod_year": tag.get("tag_2", {}).get("text"),
                                        "vod_remarks": tag.get("tag_4", {}).get("text"),
                                    })
        except Exception as e:
            _log("qq home err", e)
        return vlist

    # ------------------------------------------------------------------
    # 分类列表
    # ------------------------------------------------------------------
    def categoryContent(self, tid, pg, filter=False, extend=None):
        extend = extend or {}
        pkey = self._resolve_pkey(tid)
        type_name = extend.get("class") or ""
        # 未选类型时，默认取该平台第一个类型
        type_map = self._type_map(pkey)
        if type_name not in type_map:
            type_name = next(iter(type_map)) if type_map else ""
        cid = type_map.get(type_name, type_name)
        page = int(pg) if pg else 1

        result = None
        if pkey == "qq":
            result = self._qq_category(cid, page, extend)
        elif pkey == "qiyi":
            result = self._qiyi_category(cid, page, extend)
        elif pkey == "youku":
            result = self._youku_category(cid, page, extend)
        elif pkey == "mgtv":
            result = self._mgtv_category(cid, page, extend)
        if result is None:
            result = {"list": [], "page": page, "pagecount": 1, "limit": 0, "total": 0}
        return result

    def _type_map(self, pkey):
        if pkey == "qq":
            return _QQ_TYPE_MAP
        if pkey == "qiyi":
            return _IQIYI_TYPE_MAP
        if pkey == "youku":
            return _YOUKU_TYPE_MAP
        return _MGTV_TYPE_MAP

    def _third_filters(self, pkey, cid):
        """根据平台和选中的类型，返回三级筛选条（地区/年份/类型等）。"""
        try:
            if pkey == "qiyi":
                ft = _IQIYI_FILTERS.get(str(cid))
                return ft or []
            if pkey == "qq":
                return self._qq_get_filter(cid)
            if pkey == "mgtv":
                return self._mgtv_get_filter(cid)
            if pkey == "youku":
                return self._youku_get_filter(cid)
        except Exception as e:
            _log("third filters err", pkey, cid, e)
        return []

    def _resolve_pkey(self, tid):
        """把 type_id 解析为平台 key（qq/qiyi/youku/mgtv）。兼容数字 id 和字符串 key。"""
        if tid is None:
            return "qq"
        s = str(tid).strip()
        # 数字平台 id（"1"~"4"）
        if s in _PLATFORMS:
            return _PLATFORMS[s]["key"]
        # 平台 key（qq/qiyi/youku/mgtv）
        if s in _KEY_TO_PID:
            return s
        # 复合 id（qq_xxx / 1_xxx）
        if "_" in s:
            head = s.split("_", 1)[0]
            if head in _PLATFORMS:
                return _PLATFORMS[head]["key"]
            if head in _KEY_TO_PID:
                return head
        return "qq"

    # ------------------------------------------------------------------
    # 详情：直接从 vod_id 拆出剧名，去外部站源搜索，命中后组装播放列表
    # ------------------------------------------------------------------
    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        vid = str(ids)
        if isinstance(ids, (list, tuple)):
            vid = str(ids[0])

        # 直接从 vod_id 拆出剧名（vod_id 形如 'youku_xxx@剧名'）
        pkey, real_id, title = self._parse_vod_id(vid)
        if not title:
            # 兼容旧格式：没带剧名时再回退用平台接口取标题
            title = self._title_by_pkey(pkey, real_id)
        if not title:
            return {"list": [{"vod_name": vid, "vod_play_from": "提示",
                              "vod_play_url": "未获取到剧名#0"}]}

        # 拿剧名去所有可搜索站源搜索，命中后组装播放列表（自动可播放）
        return self._jump_search(title)

    def _title_by_pkey(self, pkey, real_id):
        """兼容回退：vod_id 未携带剧名时，通过平台接口取标题。"""
        try:
            if pkey == "qq":
                return self._qq_detail_title(real_id)
            if pkey == "qiyi":
                return self._qiyi_detail_title(real_id)
            if pkey == "youku":
                return self._youku_detail_title(real_id)
            if pkey == "mgtv":
                return self._mgtv_detail_title(real_id)
        except Exception as e:
            _log("title fallback err", e)
        return ""

    def _jump_search(self, title):
        """用剧名去各站源搜索，组装跳转播放列表（站源名 + api$vod_id）。

        关键优化：不等待所有站源返回——只要在超时预算内命中结果就立即返回，
        避免慢站源拖长 detailContent，导致点击后黑屏。
        """
        sites = self._load_sites()
        if not sites:
            return {"list": [{"vod_name": title, "vod_play_from": "提示",
                              "vod_play_url": "未找到可搜索站源#0"}]}
        play_from, play_url = [], []
        deadline = time.time() + self._JUMP_TIMEOUT
        ex = ThreadPoolExecutor(max_workers=self._JUMP_WORKERS)
        try:
            futures = {ex.submit(self._search_one, s, title): s for s in sites}
            for f in as_completed(futures):
                # 每个站源单次搜索最多等 2 秒，超时视为未命中
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                try:
                    res = f.result(timeout=min(2.0, remaining))
                except Exception:
                    res = None
                if not res:
                    # 超时预算用尽且已有结果，不再等剩余慢站源
                    if play_from and time.time() >= deadline:
                        break
                    continue
                name, api, vid, vname = res
                play_from.append(name)
                play_url.append(f"{api}${vid}")
                # 命中足够数量立即返回，不必等所有站源搜完（避免黑屏）
                if len(play_from) >= self._JUMP_MIN_HITS:
                    break
        finally:
            # 关键：不等待未完成的慢站源，立即返回，避免点击后黑屏
            ex.shutdown(wait=False, cancel_futures=True)
        if not play_from:
            return {"list": [{"vod_name": title, "vod_play_from": "提示",
                              "vod_play_url": "未找到播放源#0"}]}
        return {"list": [{
            "vod_id": title,
            "vod_name": title,
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }]}

    # ------------------------------------------------------------------
    # 站源列表：从外置 json（extend 指定）读取可搜索站源
    # ------------------------------------------------------------------
    def _load_sites(self):
        """读取站源 json 的 sites，筛出可搜索(searchable=1)的 csp 站源。

        与 douban 完全一致：extend 指定优先，否则回退扫描候选路径，
        确保即使 extend 未传也能找到站源文件（避免返回空列表导致黑屏）。
        """
        sites = []
        candidates = []
        if isinstance(getattr(self, "extend", ""), str) and self.extend.strip():
            candidates.append(self.extend)
        candidates += ["./sun.json", "sun.json", "../sun.json", "./sites.json", "sites.json"]
        data = None
        for p in candidates:
            try:
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8-sig") as f:
                        data = json.load(f)
                    break
            except Exception:
                continue
        if not data:
            return sites
        seen_api = set()
        for s in data.get("sites", []):
            if s.get("type") != 3:
                continue
            if s.get("searchable") != 1:
                continue
            api = s.get("api") or ""
            if not api:
                continue
            # 同一 api 多个站点只保留一个，减少重复搜索
            if api in seen_api:
                continue
            seen_api.add(api)
            sites.append({
                "api": api,
                "name": s.get("name") or s.get("key") or api,
                "key": s.get("key", ""),
                "ext": s.get("ext", {}),
            })
        return sites

    def _search_one(self, site, title):
        """在单个站源搜索剧名，返回 (站源名, api, 站源vod_id, 站源vod_name)。

        与 douban 完全一致：只拿站源的 vod_id，真实地址在播放时再跳转。
        """
        api = site["api"]
        name = site["name"]
        try:
            sp = self.loadSpider(api)
            ext = site.get("ext")
            try:
                if isinstance(ext, dict):
                    sp.init(json.dumps(ext, ensure_ascii=False))
                elif ext:
                    sp.init(str(ext))
                else:
                    sp.init("")
            except Exception:
                pass
            r = sp.searchContent(title, True, "1")
            items = r.get("list", []) if isinstance(r, dict) else []
            if not items:
                return None
            first = items[0]
            for it in items:
                vname = str(it.get("vod_name") or it.get("name") or "").strip()
                if vname == title:
                    first = it
                    break
            vid = first.get("vod_id") or first.get("id") or ""
            vname = first.get("vod_name") or first.get("name") or ""
            if not vid:
                return None
            return (name, api, str(vid), vname)
        except Exception as e:
            _log("jump search err", name, e)
            return None

    # ------------------------------------------------------------------
    # 搜索：与 douban 一致，把 key 当剧名直接去各站源跳转搜索
    # ------------------------------------------------------------------
    def searchContent(self, key, quick, pg="1"):
        return self.detailContent([key])

    # ------------------------------------------------------------------
    # 播放：与 douban 一致的两段式跳转（api$vod_id -> 站源详情 -> 真实地址）
    # ------------------------------------------------------------------
    def playerContent(self, flag, id, vipFlags):
        api = flag
        if not api or not id:
            return {}
        if "$" in str(id):
            api, vid = str(id).split("$", 1)
        else:
            vid = str(id)
        try:
            sp = self.loadSpider(api)
            try:
                sp.init("")
            except Exception:
                pass
            # 先取详情拿播放列表，再取真实播放地址
            detail = sp.detailContent([vid])
            vods = detail.get("list", []) if isinstance(detail, dict) else []
            if vods:
                vod = vods[0]
                urls = (vod.get("vod_play_url") or "").split("$$$")
                froms = (vod.get("vod_play_from") or "").split("$$$")
                if urls:
                    first_url = urls[0].split("#")[0] if urls[0] else ""
                    first_flag = froms[0] if froms and froms[0] else ""
                    if first_url:
                        return sp.playerContent(first_flag, first_url, vipFlags)
        except Exception as e:
            _log("jump player err", api, e)
        return {}

    # ==================================================================
    # 腾讯视频
    # ==================================================================
    _QQ_HOST = "https://v.qq.com"
    _QQ_API = "https://pbaccess.video.qq.com"
    _QQ_HEADERS = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/109.0.5410.0 Safari/537.36"),
        "origin": "https://v.qq.com",
        "referer": "https://v.qq.com/",
    }

    def _qq_category(self, cid, page, extend):
        # 翻页游标：page=1 时重置，否则沿用上一次的 page_context
        if page == 1 or not hasattr(self, "_qq_body"):
            self._qq_body = {
                "page_params": {
                    "channel_id": cid,
                    "filter_params": "sort=75",
                    "page_type": "channel_operation",
                    "page_id": "channel_list_second_page",
                }
            }
        params = {
            "sort": extend.get("sort", "75"),
            "attr": extend.get("attr", "-1"),
            "itype": extend.get("itype", "-1"),
            "ipay": extend.get("ipay", "-1"),
            "iarea": extend.get("iarea", "-1"),
            "iyear": extend.get("iyear", "-1"),
        }
        self._qq_body["page_params"]["channel_id"] = cid
        self._qq_body["page_params"]["filter_params"] = self._qq_params_to_str(params)
        r = self._http_post(
            f"{self._QQ_API}/trpc.universal_backend_service.page_server_rpc.PageServer/GetPageData"
            "?video_appid=1000005&vplatform=2&vversion_name=8.9.10&new_mark_label_enabled=1",
            json_body=self._qq_body, headers=self._QQ_HEADERS)
        if not r:
            return {"list": [], "page": page, "pagecount": 1, "limit": 0, "total": 0}
        data = r.json().get("data", {})
        # 保存下一页游标，否则翻页会重复返回第一页数据
        if data.get("has_next_page"):
            pagecount = 9999
            self._qq_body["page_context"] = data.get("next_page_context")
        else:
            pagecount = page
        vlist = []
        try:
            items = data["module_list_datas"][-1]["module_datas"][-1]["item_data_lists"]["item_datas"]
            seen = set()
            for its in items:
                cid2 = its.get("item_params", {}).get("cid")
                if not cid2 or cid2 in seen:
                    continue
                seen.add(cid2)
                p = its["item_params"]
                tag = json.loads(p.get("uni_imgtag", "{}") or p.get("imgtag", "{}") or "{}")
                name = p.get("mz_title") or p.get("title")
                pic = p.get("new_pic_hz") or p.get("new_pic_vt")
                vlist.append({
                    "vod_id": self._make_vod_id("qq", cid2, name),
                    "vod_name": name,
                    "vod_pic": pic,
                    "vod_year": tag.get("tag_2", {}).get("text"),
                    "vod_remarks": tag.get("tag_4", {}).get("text"),
                })
        except Exception as e:
            _log("qq category parse err", e)
        return {"list": vlist, "page": page, "pagecount": pagecount, "limit": len(vlist), "total": 999999}

    def _qq_detail(self, cid):
        vbody = {
            "page_params": {"req_from": "web", "cid": cid, "vid": "", "lid": "",
                            "page_type": "detail_operation",
                            "page_id": "detail_page_introduction"},
            "has_cache": 1,
        }
        ebody = {
            "page_params": {"req_from": "web_vsite", "page_id": "vsite_episode_list",
                            "page_type": "detail_operation", "id_type": "1",
                            "page_size": "", "cid": cid, "vid": "", "lid": "",
                            "page_num": "", "page_context": "",
                            "detail_page_type": "1"},
            "has_cache": 1,
        }
        with ThreadPoolExecutor(max_workers=2) as ex:
            fv = ex.submit(self._qq_get_vdata, vbody)
            fe = ex.submit(self._qq_get_vdata, ebody)
            vdata = fv.result()
            edata = fe.result()
        pdata = self._qq_process_tabs(edata, ebody, cid)
        if not pdata:
            return {"list": [{"vod_play_from": "哎呀翻车啦", "vod_play_url": "翻车啦#555"}]}
        try:
            star = vdata["data"]["module_list_datas"][0]["module_datas"][0][
                "item_data_lists"]["item_datas"][0].get("sub_items", {}).get(
                "star_list", {}).get("item_datas", [])
            actors = [s["item_params"]["name"] for s in star]
            plist, ylist = self._qq_process_pdata(pdata, cid)
            names = ["腾讯视频", "预告片"]
            urls = []
            if plist:
                urls.append("#".join(plist))
                if not ylist:
                    names.remove("预告片")
            elif ylist:
                names.remove("腾讯视频")
                urls.append("#".join(ylist))
            if not urls:
                urls = ["", ""]
            d = vdata["data"]["module_list_datas"][0]["module_datas"][0][
                "item_data_lists"]["item_datas"][0]["item_params"]
            vod = {
                "type_name": d.get("sub_genre", ""),
                "vod_name": d.get("title", ""),
                "vod_year": d.get("year", ""),
                "vod_area": d.get("area_name", ""),
                "vod_remarks": d.get("holly_online_time", "") or d.get("hotval", ""),
                "vod_actor": ",".join(actors),
                "vod_content": d.get("cover_description", ""),
                "vod_play_from": "$$$".join(names),
                "vod_play_url": "$$$".join(urls),
            }
            return {"list": [vod]}
        except Exception as e:
            _log("qq detail err", e)
            return {"list": [{"vod_play_from": "哎呀翻车啦", "vod_play_url": "翻车啦#555"}]}

    def _qq_get_vdata(self, body):
        try:
            r = self._http_post(
                f"{self._QQ_API}/trpc.universal_backend_service.page_server_rpc.PageServer/GetPageData"
                "?video_appid=3000010&vplatform=2&vversion_name=8.2.96",
                json_body=body, headers=self._QQ_HEADERS)
            return r.json()
        except Exception as e:
            _log("qq get_vdata err", e)
            return {"data": {"module_list_datas": []}}

    def _qq_process_tabs(self, data, body, cid):
        try:
            pdata = data["data"]["module_list_datas"][-1]["module_datas"][-1]["item_data_lists"]["item_datas"]
            tabs = data["data"]["module_list_datas"][-1]["module_datas"][-1]["module_params"].get("tabs")
            if tabs and len(json.loads(tabs)):
                tabs = json.loads(tabs)
                tasks = []
                for tab in tabs[1:]:
                    nb = copy.deepcopy(body)
                    nb["page_params"]["page_context"] = tab["page_context"]
                    tasks.append(nb)
                with ThreadPoolExecutor(max_workers=10) as ex:
                    results = list(ex.map(self._qq_get_vdata, tasks))
                for r in results:
                    if r:
                        pd = r["data"]["module_list_datas"][-1]["module_datas"][-1]["item_data_lists"]["item_datas"]
                        pdata.extend(pd)
            return pdata
        except Exception as e:
            _log("qq process_tabs err", e)
            return []

    def _qq_process_pdata(self, pdata, cid):
        plist, ylist = [], []
        for k in pdata:
            if k.get("item_id"):
                pid = f"{k['item_params']['union_title']}${cid}@{k['item_id']}"
                if "预告" in k["item_params"]["union_title"]:
                    ylist.append(pid)
                else:
                    plist.append(pid)
        return plist, ylist

    def _qq_player(self, id):
        ids = id.split("@")
        url = f"{self._QQ_HOST}/x/cover/{ids[0]}/{ids[1]}.html"
        return {"parse": 1, "url": f"https://jx.xmflv.com/?url={url}", "header": ""}

    @staticmethod
    def _qq_params_to_str(params):
        return "&".join(f"{k}={v}" for k, v in params.items())

    def _qq_get_filter(self, cid):
        body = {
            "page_params": {
                "channel_id": cid,
                "filter_params": "sort=75",
                "page_type": "channel_operation",
                "page_id": "channel_list_second_page",
            }
        }
        r = self._http_post(
            f"{self._QQ_API}/trpc.universal_backend_service.page_server_rpc.PageServer/GetPageData"
            "?video_appid=1000005&vplatform=2&vversion_name=8.9.10&new_mark_label_enabled=1",
            json_body=body, headers=self._QQ_HEADERS)
        if not r:
            return []
        data = r.json()
        if not data.get("data", {}).get("module_list_datas"):
            return []
        filter_dict = {}
        try:
            items = data["data"]["module_list_datas"][-1]["module_datas"][-1]["item_data_lists"]["item_datas"]
            for item in items:
                if not item.get("item_params", {}).get("index_item_key"):
                    continue
                params = item["item_params"]
                fk = params["index_item_key"]
                if fk not in filter_dict:
                    filter_dict[fk] = {"key": fk, "name": params["index_name"], "value": []}
                filter_dict[fk]["value"].append({"n": params["option_name"], "v": params["option_value"]})
        except (IndexError, KeyError):
            return []
        return list(filter_dict.values())

    # ==================================================================
    # 爱奇艺
    # ==================================================================
    _IQIYI_SITE = "https://m.iqiyi.com"
    _IQIYI_PCW = "https://pcw-api.iqiyi.com"
    _IQIYI_SEARCH = "https://search.video.iqiyi.com/o"

    def _qiyi_category(self, cid, page, extend):
        mode = extend.get("mode", "24")
        params = {
            "channel_id": cid, "data_type": "1", "mode": mode,
            "page_id": str(page), "ret_num": "48", "session": "",
        }
        r = self._http_get(f"{self._IQIYI_PCW}/search/recommend/list", params=params,
                           headers={"User-Agent": self.userAgent, "Referer": self._IQIYI_SITE})
        vlist = []
        pagecount = page
        total = 0
        if r:
            data = r.json()
            if data.get("code") == "A00000" and data.get("data", {}).get("list"):
                for item in data["data"]["list"]:
                    vlist.append(self._qiyi_parse_item(item))
                has_next = data["data"].get("has_next", 0)
                pagecount = page + 1 if has_next else page
                total = len(vlist) * page
        return {"list": vlist, "page": page, "pagecount": pagecount, "limit": 48, "total": total}

    def _qiyi_detail(self, vid):
        album_id = vid.split("_")[0] if "_" in vid else vid
        r = self._http_get(f"{self._IQIYI_PCW}/albums/album/avlistinfo",
                           params={"aid": album_id, "page": "1", "size": "30"},
                           headers={"User-Agent": self.userAgent})
        episodes, first_ep = [], {}
        if r:
            data = r.json()
            if data.get("code") == "A00000" and data.get("data", {}).get("epsodelist"):
                episodes = data["data"]["epsodelist"]
                first_ep = episodes[0] if episodes else {}
        tv_id = first_ep.get("tvId") if first_ep else album_id
        album_name = album_pic = album_desc = ""
        directors, actors = [], []
        if tv_id:
            br = self._http_get(f"{self._IQIYI_PCW}/video/video/baseinfo/{tv_id}",
                                headers={"User-Agent": self.userAgent})
            if br:
                bd = br.json()
                if bd.get("code") == "A00000" and bd.get("data"):
                    bi = bd["data"]
                    album_name = bi.get("albumName", "")
                    album_pic = bi.get("albumImageUrl", "")
                    album_desc = bi.get("description", "")
                    people = bi.get("people", {})
                    directors = [d.get("name", "") for d in people.get("director", []) if d.get("name")]
                    actors = [a.get("name", "") for a in people.get("main_charactor", []) if a.get("name")]
                    if not episodes and bi.get("playUrl"):
                        episodes = [{"shortTitle": bi.get("name", album_name),
                                     "name": bi.get("name", album_name),
                                     "playUrl": bi.get("playUrl", "")}]
        if not album_name and first_ep:
            album_name = self._qiyi_extract_name(first_ep.get("name", ""), first_ep.get("shortTitle", ""))
        if not album_pic and first_ep:
            album_pic = first_ep.get("imageUrl", "")
        if not album_desc and first_ep:
            album_desc = first_ep.get("description", "")
        if not directors and first_ep and first_ep.get("people"):
            directors = [d.get("name", "") for d in first_ep["people"].get("director", []) if d.get("name")]
        if not actors and first_ep and first_ep.get("people"):
            actors = [a.get("name", "") for a in first_ep["people"].get("main_charactor", []) if a.get("name")]

        play_urls = []
        for ep in episodes:
            name = ep.get("shortTitle", ep.get("name", ""))
            url = ep.get("playUrl", "")
            if url:
                url = url.replace("http://www.iqiyi.com/", "https://m.iqiyi.com/")
                url = url.replace("https://www.iqiyi.com/", "https://m.iqiyi.com/")
                play_urls.append(f"{name}${url}")
        play_url = "#".join(play_urls)
        vod = {
            "vod_id": self._make_vod_id("qiyi", vid, album_name),
            "vod_name": album_name,
            "vod_pic": album_pic,
            "vod_remarks": f"共{len(episodes)}集" if len(episodes) > 1 else (first_ep.get("duration", "") or "电影"),
            "vod_actor": " ".join(actors[:8]),
            "vod_director": " ".join(directors[:3]),
            "vod_content": album_desc.replace("\n\n", "\n").strip() if album_desc else "",
            "vod_play_from": "爱奇艺",
            "vod_play_url": play_url,
        }
        return {"list": [vod]}

    def _qiyi_search(self, key, pg):
        r = self._http_get(self._IQIYI_SEARCH, params={"if": "html5", "key": key,
                                                      "pageNum": pg, "pageSize": "25"},
                           headers={"User-Agent": self.userAgent})
        vlist = []
        if r:
            data = r.json()
            for item in data.get("data", {}).get("docinfos", []):
                ai = item.get("albumDocInfo", {})
                if ai and ai.get("albumId") and ai.get("albumTitle"):
                    vlist.append({
                        "vod_id": self._make_vod_id("qiyi", ai['albumId'], ai["albumTitle"]),
                        "vod_name": ai["albumTitle"],
                        "vod_pic": ai.get("albumVImage", "") or ai.get("albumImg", ""),
                        "vod_remarks": ai.get("tvFocus", "") or f"{ai.get('itemTotalNumber', 0)}集",
                    })
        return vlist

    def _qiyi_player(self, id):
        url = id
        if url.startswith("http"):
            url = url.replace("http://www.iqiyi.com/", "https://m.iqiyi.com/")
            url = url.replace("https://www.iqiyi.com/", "https://m.iqiyi.com/")
        if self.isVideoFormat(url):
            return {"parse": 0, "url": url,
                    "header": {"User-Agent": self.userAgent, "Referer": "https://m.iqiyi.com/"}}
        return {"parse": 1, "url": url, "jx": "1",
                "header": {"User-Agent": self.userAgent, "Referer": "https://m.iqiyi.com/"}}

    @staticmethod
    def _qiyi_parse_item(item):
        return {
            "vod_id": Spider._make_vod_id("qiyi", item.get('albumId', ''), item.get("name", "")),
            "vod_name": item.get("name", ""),
            "vod_pic": item.get("imageUrl", ""),
            "vod_remarks": item.get("focus", "") or f"更新至{item.get('latestOrder', 0)}集",
            "vod_year": str(item.get("period", ""))[:4] if item.get("period") else "",
            "vod_area": ",".join(item.get("categories", [])),
        }

    @staticmethod
    def _qiyi_extract_name(name, short_title):
        text = name or short_title or ""
        if not text:
            return ""
        m = re.match(r"^(.*?)(第\d+集|第[一二三四五六七八九十百]+集|预告|片花|花絮|特辑)", text)
        if m and m.group(1).strip():
            return m.group(1).strip()
        return text

    # ==================================================================
    # 优酷
    # ==================================================================
    _YOUKU_HOST = "https://www.youku.com"
    _YOUKU_IHOST = "https://v.youku.com"

    @staticmethod
    def _youku_extract_id(video_link):
        """从优酷 videoLink 提取真实 showId。

        链接形如 https://v.youku.com/v_show/id_XNjQzMTA0OTYw.html?s=cc1e&spm=...
        真实 id 是 id_ 后的那串，必须去掉 ?s= 及其后的参数。
        """
        link = str(video_link)
        # 去掉 ? 及之后的查询参数
        if "?" in link:
            link = link.split("?")[0]
        m = re.search(r"id_([A-Za-z0-9=]+)", link)
        if m:
            return m.group(1)
        # 兜底：取最后一段路径中的 id
        m2 = re.search(r"/([A-Za-z0-9=]{5,})(?:\.html)?$", link)
        if m2:
            return m2.group(1)
        return ""

    def _youku_ensure_session(self):
        if self._youku_session is None:
            self._youku_session = _requests.Session()
            self._youku_session.headers.update(self._youku_headers)
        return self._youku_session

    def _youku_category(self, name, page, extend):
        sess = self._youku_ensure_session()
        params = {"type": name}
        # 剔除 class（类型已通过 name 映射），其余筛选参数透传
        ext = {k: v for k, v in (extend or {}).items() if k != "class"}
        params.update(ext)
        try:
            if page == 1 or name not in self._youku_typeid:
                sid = self._youku_cf(params)
                self._youku_typeid[name] = sid
            sid = self._youku_typeid[name]
            url = f"{self._YOUKU_HOST}/category/data?session={sid}&params={urllib.parse.quote(json.dumps(params))}&pageNo={page}"
            data = sess.get(url).json()
            vlist = []
            fd = data["data"]["filterData"]
            for i in fd["listData"]:
                if i.get("videoLink"):
                    show_id = self._youku_extract_id(i["videoLink"])
                    if not show_id:
                        continue
                    vlist.append({
                        "vod_id": self._make_vod_id("youku", show_id, i.get("title")),
                        "vod_name": i.get("title"),
                        "vod_pic": i.get("img"),
                        "vod_year": i.get("rightTagText"),
                        "vod_remarks": i.get("summary"),
                    })
            self._youku_typeid[name] = urllib.parse.quote(json.dumps(fd["session"]))
            return {"list": vlist, "page": page, "pagecount": 9999, "limit": 90, "total": 999999}
        except Exception as e:
            _log("youku category err", e)
            return {"list": [], "page": page, "pagecount": page, "limit": 0, "total": 0}

    def _youku_detail(self, vid):
        sess = self._youku_ensure_session()
        try:
            data = sess.get(f"{self._YOUKU_IHOST}/v_getvideo_info/?showId={vid}").json()
            v = data["data"]
            vod = {
                "vod_id": self._make_vod_id("youku", vid, v.get("showName", "")),
                "vod_name": v.get("showName", ""),
                "type_name": v.get("showVideotype"),
                "vod_year": v.get("lastUpdate"),
                "vod_remarks": v.get("rc_title"),
                "vod_actor": v.get("_personNameStr"),
                "vod_content": v.get("showdesc"),
                "vod_play_from": "优酷",
                "vod_play_url": "",
            }
            params = {"biz": "new_detail_web2", "videoId": v.get("vid"), "scene": "web_page",
                      "componentVersion": "3", "ip": data.get("ip"), "debug": 0,
                      "utdid": "ZYmGMAAAACkDAMU8hbiMmYdd", "userId": 0, "platform": "pc",
                      "nextSession": "", "gray": 0, "source": "pcNoPrev", "showId": vid}
            sdata, index = self._youku_getinfo(params)
            pdata = sdata["nodes"]
            if index > len(pdata):
                batch = len(pdata)
                ssj = json.loads(sdata["data"]["session"])
                total_batches = ((index + batch - 1) // batch) - 1
                with ThreadPoolExecutor(max_workers=total_batches) as ex:
                    futures = []
                    for b in range(total_batches):
                        start = batch + 1 + (b * batch)
                        end = start + batch - 1
                        ns = ssj.copy()
                        ns.update({"itemStartStage": start, "itemEndStage": min(end, index)})
                        cp = params.copy()
                        cp["nextSession"] = json.dumps(ns)
                        futures.append((start, ex.submit(self._youku_getvinfo, cp)))
                    futures.sort(key=lambda x: x[0])
                    for _, f in futures:
                        try:
                            pdata.extend(f.result()["nodes"])
                        except Exception as e:
                            _log("youku fetch err", e)
            vod["vod_play_url"] = "#".join(
                [f"{i['data'].get('title')}${i['data']['action'].get('value')}" for i in pdata])
            return {"list": [vod]}
        except Exception as e:
            _log("youku detail err", e)
            return {"list": [{"vod_play_from": "哎呀翻车啦", "vod_play_url": f"呜呜呜${self._YOUKU_HOST}"}]}

    def _youku_search(self, key, pg):
        sess = self._youku_ensure_session()
        vlist = []
        try:
            data = sess.get(f"https://search.youku.com/api/search?pg={pg}&keyword={key}").json()
            for i in data.get("pageComponentList", []):
                if i.get("commonData") and (i["commonData"].get("showId") or i["commonData"].get("realShowId")):
                    c = i["commonData"]
                    vlist.append({
                        "vod_id": self._make_vod_id("youku", c.get('showId') or c.get('realShowId'),
                                                    c["titleDTO"].get("displayName")),
                        "vod_name": c["titleDTO"].get("displayName"),
                        "vod_pic": c["posterDTO"].get("vThumbUrl"),
                        "vod_year": c.get("feature"),
                        "vod_remarks": c.get("updateNotice"),
                    })
        except Exception as e:
            _log("youku search err", e)
        return vlist

    def _youku_player(self, id):
        return {"jx": 1, "parse": 1, "url": f"{self._YOUKU_IHOST}/video?vid={id}", "header": ""}

    def _youku_cf(self, params):
        sess = self._youku_ensure_session()
        url = f"{self._YOUKU_HOST}/category/data?params={urllib.parse.quote(json.dumps(params))}&optionRefresh=1&pageNo=1"
        data = sess.get(url).json()
        session = urllib.parse.quote(json.dumps(data["data"]["filterData"]["session"]))
        return session

    def _youku_getvinfo(self, params):
        sess = self._youku_ensure_session()
        body = {"ms_codes": "2019030100", "params": json.dumps(params),
                "system_info": '{"os":"iku","device":"iku","ver":"9.2.9","appPackageKey":"com.youku.iku","appPackageId":"pcweb"}'}
        url = "https://acs.youku.com/h5/mtop.youku.columbus.gateway.new.execute/1.0/"
        data = sess.get(url, params={"appKey": "24679788", "t": int(time.time() * 1000), "data": json.dumps(body)}).json()
        okey = list(data["data"].keys())[0]
        return data["data"][okey]["data"]

    def _youku_getinfo(self, params):
        i = self._youku_getvinfo(params)
        jdata = i["nodes"][0]["nodes"][3]
        info = i["data"]["extra"]["episodeTotal"]
        if i["data"]["extra"]["showCategory"] in ["电影", "游戏"]:
            jdata = i["nodes"][0]["nodes"][4]
        return jdata, info

    def _youku_get_filter(self, name):
        sess = self._youku_ensure_session()
        params = {"type": name}
        url = f"{self._YOUKU_HOST}/category/data?params={urllib.parse.quote(json.dumps(params))}&optionRefresh=1&pageNo=1"
        data = sess.get(url).json()
        fd = data["data"]["filterData"]
        session = urllib.parse.quote(json.dumps(fd["session"]))
        self._youku_typeid[name] = session
        raw = fd["filter"]["filterData"][1:]
        result = []
        for item in raw:
            if not item.get("subFilter"):
                continue
            first_sub = item["subFilter"][0]
            if not first_sub.get("filterType"):
                continue
            fi = {"key": self._youku_process_key(first_sub["filterType"]),
                  "name": first_sub["title"], "value": []}
            for sub in item["subFilter"]:
                if "value" in sub:
                    fi["value"].append({"n": sub["title"], "v": sub["value"]})
            if fi["value"]:
                result.append(fi)
        return result

    @staticmethod
    def _youku_process_key(key):
        if "_" not in key:
            return key
        parts = key.split("_")
        result = parts[0]
        for part in parts[1:]:
            if part:
                result += part[0].upper() + part[1:]
        return result

    # ==================================================================
    # 芒果TV
    # ==================================================================
    _MGTV_RHOST = "https://www.mgtv.com"
    _MGTV_HOST = "https://pianku.api.mgtv.com"
    _MGTV_VHOST = "https://pcweb.api.mgtv.com"
    _MGTV_SHOST = "https://mobileso.bz.mgtv.com"
    _MGTV_HEADERS = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.6478.61 Safari/537.36"),
        "origin": "https://www.mgtv.com",
        "referer": "https://www.mgtv.com/",
    }

    def _mgtv_category(self, cid, page, extend):
        body = {"allowedRC": "1", "platform": "pcweb", "channelId": cid,
                "pn": page, "pc": "80", "hudong": "1", "_support": "10000000"}
        # 剔除 class（类型已通过 cid 映射），其余筛选参数透传
        ext = {k: v for k, v in (extend or {}).items() if k != "class"}
        body.update(ext)
        r = self._http_get(f"{self._MGTV_HOST}/rider/list/pcweb/v3", params=body,
                           headers=self._MGTV_HEADERS)
        vlist = []
        if r:
            data = r.json()
            for i in data.get("data", {}).get("hitDocs", []):
                vlist.append({
                    "vod_id": self._make_vod_id("mgtv", i['playPartId'], i["title"]),
                    "vod_name": i["title"],
                    "vod_pic": i["img"],
                    "vod_year": (i.get("rightCorner", {}) or {}).get("text") or i.get("year"),
                    "vod_remarks": i["updateInfo"],
                })
        return {"list": vlist, "page": page, "pagecount": 9999, "limit": 90, "total": 999999}

    def _mgtv_detail(self, vid):
        vbody = {"allowedRC": "1", "vid": vid, "type": "b", "_support": "10000000"}
        r = self._http_get(f"{self._MGTV_VHOST}/video/info", params=vbody, headers=self._MGTV_HEADERS)
        if not r:
            return {"list": []}
        vdata = r.json()
        d = vdata["data"]["info"]["detail"]
        vod = {
            "vod_id": self._make_vod_id("mgtv", vid, vdata["data"]["info"]["title"]),
            "vod_name": vdata["data"]["info"]["title"],
            "type_name": d.get("kind"),
            "vod_year": d.get("releaseTime"),
            "vod_area": d.get("area"),
            "vod_lang": d.get("language"),
            "vod_remarks": d.get("updateInfo"),
            "vod_actor": d.get("leader"),
            "vod_director": d.get("director"),
            "vod_content": d.get("story"),
            "vod_play_from": "芒果TV",
            "vod_play_url": "",
        }
        data, pdata = self._mgtv_fetch_page("1", vid, True)
        pagecount = data.get("data", {}).get("total_page") or 1
        if int(pagecount) > 1:
            with ThreadPoolExecutor(max_workers=10) as ex:
                results = list(ex.map(lambda p: self._mgtv_fetch_page(str(p), vid), range(2, int(pagecount) + 1)))
            for r in results:
                pdata.extend(r)
        vod["vod_play_url"] = "#".join(pdata)
        return {"list": [vod]}

    def _mgtv_search(self, key, pg):
        url = f"{self._MGTV_SHOST}/applet/search/v1?channelCode=mobile-wxap&q={key}&pn={pg}&pc=10&_support=10000000"
        r = self._http_get(url, headers=self._MGTV_HEADERS)
        vlist = []
        if r:
            data = r.json()
            for i in data.get("data", {}).get("contents", []):
                if i.get("data") and len(i["data"]):
                    k = i["data"][0]
                    if k.get("vid") and k.get("img"):
                        try:
                            vlist.append({
                                "vod_id": self._make_vod_id("mgtv", k['vid'], k["title"]),
                                "vod_name": k["title"],
                                "vod_pic": k["img"],
                                "vod_year": (i.get("rightTopCorner", {}) or {}).get("text") or i.get("year"),
                                "vod_remarks": "/".join(i.get("desc", [])),
                            })
                        except Exception:
                            pass
        return vlist

    def _mgtv_player(self, id):
        url = f"{self._MGTV_RHOST}{id}"
        return {"jx": 1, "parse": 1, "url": url, "header": ""}

    def _mgtv_fetch_page(self, page, vid, b=False):
        body = {"version": "5.5.35", "video_id": vid, "page": page, "size": "30",
                "platform": "4", "src": "mgtv", "allowedRC": "1", "_support": "10000000"}
        r = self._http_get(f"{self._MGTV_VHOST}/episode/list", params=body, headers=self._MGTV_HEADERS)
        if not r:
            return (r, []) if b else []
        data = r.json()
        ldata = [f"{i['t3']}${i['url']}" for i in data.get("data", {}).get("list", [])]
        return (data, ldata) if b else ldata

    def _mgtv_get_filter(self, cid):
        params = {"allowedRC": "1", "channelId": cid, "platform": "pcweb",
                  "_support": "10000000"}
        r = self._http_get(f"{self._MGTV_HOST}/rider/config/channel/v1",
                           params=params, headers=self._MGTV_HEADERS)
        if not r:
            return []
        data = r.json()
        ft = []
        for i in data.get("data", {}).get("listItems", []):
            try:
                value_array = [{"n": v["tagName"], "v": v["tagId"]}
                               for v in i["items"] if v.get("tagName")]
                if value_array:
                    ft.append({"key": i["eName"], "name": i["typeName"], "value": value_array})
            except Exception:
                continue
        return ft

    # ==================================================================
    # 工具
    # ==================================================================
    @staticmethod
    def _make_vod_id(pkey, real_id, name):
        """生成 vod_id：'平台key_真实id@剧名'，剧名 URL 编码避免特殊字符干扰。"""
        return f"{pkey}_{real_id}@{_enc(name or '')}"

    @staticmethod
    def _parse_vod_id(vid):
        """解析 vod_id '平台key_真实id@剧名' -> (平台key, 真实id, 剧名)。

        兼容不带 @ 剧名的旧格式。"""
        s = str(vid).strip()
        title = ""
        if "@" in s:
            s, title = s.rsplit("@", 1)
            title = urllib.parse.unquote(title)
        pkey, real = "", s
        if "_" in s:
            head, real = s.split("_", 1)
            if head in _KEY_TO_PID or head in _PLATFORMS:
                pkey = head
            else:
                pkey, real = "", s
        return pkey, real, title

    @staticmethod
    def _split_id(vid):
        """vod_id '平台key_真实id' -> (平台key, 真实id)。"""
        s = str(vid).strip()
        if "_" in s:
            pkey, real = s.split("_", 1)
            if pkey in _KEY_TO_PID:
                return pkey, real
        return "qiyi", s

    # ------------------------------------------------------------------
    # 各平台「只取剧名」轻量接口（供跳转搜索使用，不解析播放列表）
    # ------------------------------------------------------------------
    def _qq_detail_title(self, cid):
        vbody = {"page_params": {"req_from": "web", "cid": cid, "vid": "", "lid": "",
                                 "page_type": "detail_operation",
                                 "page_id": "detail_page_introduction"},
                 "has_cache": 1}
        d = self._qq_get_vdata(vbody)
        try:
            return d["data"]["module_list_datas"][0]["module_datas"][0][
                "item_data_lists"]["item_datas"][0]["item_params"].get("title", "")
        except Exception:
            return ""

    def _qiyi_detail_title(self, vid):
        album_id = vid.split("_")[0] if "_" in vid else vid
        r = self._http_get(f"{self._IQIYI_PCW}/albums/album/avlistinfo",
                           params={"aid": album_id, "page": "1", "size": "1"},
                           headers={"User-Agent": self.userAgent})
        if not r:
            return ""
        data = r.json()
        eps = data.get("data", {}).get("epsodelist", []) if data.get("code") == "A00000" else []
        if not eps:
            return ""
        name = eps[0].get("name", "") or eps[0].get("shortTitle", "")
        return self._qiyi_extract_name(name, eps[0].get("shortTitle", ""))

    def _youku_detail_title(self, vid):
        try:
            sess = self._youku_ensure_session()
            data = sess.get(f"{self._YOUKU_IHOST}/v_getvideo_info/?showId={vid}").json()
            return data["data"].get("showName", "")
        except Exception:
            return ""

    def _mgtv_detail_title(self, vid):
        vbody = {"allowedRC": "1", "vid": vid, "type": "b", "_support": "10000000"}
        r = self._http_get(f"{self._MGTV_VHOST}/video/info", params=vbody, headers=self._MGTV_HEADERS)
        if not r:
            return ""
        try:
            return r.json()["data"]["info"]["title"]
        except Exception:
            return ""


if __name__ == "__main__":
    s = Spider()
    s.init()
    print(json.dumps(s.homeContent(True), ensure_ascii=False, indent=2))
