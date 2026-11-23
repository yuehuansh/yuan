# -*- coding: utf-8 -*-
# 可可影视 - OK影视(PyramidStore) Python 源
# 站点: https://www.kkys20.com
# 说明: 适配 OK影视(PyramidStore) 的 base.spider.Spider 基类。
#       基类 fetch(url, headers={}, cookies="") 只支持3参数、不支持 timeout/verify/stream,
#       因此本脚本自行用 requests.Session 发请求(带 timeout/verify=False 兜底),
#       避免向 fetch 多传参数导致 TypeError 而被 try/except 吞掉 → 列表全空。
import re
import sys
import json
import socket
from urllib.parse import quote, urlparse

import requests
from lxml import etree

try:
    import urllib3
    urllib3.disable_warnings()
except Exception:
    pass

sys.path.append('..')
from base.spider import Spider


class Spider(Spider):
    def getName(self):
        return "可可影视"

    def init(self, extend=""):
        self.host = "https://www.kkys20.com"
        self.image_host = "https://vres.wkcyvs.cn"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Referer": self.host + "/",
        }
        self.image_hosts = [
            "https://vres.wkcyvs.cn",
            "https://103.39.111.180:51050",
            "https://103.39.111.184:51050",
            "https://vres.enbymae.com",
            "https://vres.cyscyy.com",
        ]
        # 自建 Session,精确控制 timeout/verify(基类 fetch 不支持这些参数)
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.verify = False
        # 动态探测图片域名(站点静态资源域名已迁移到 wkcyvs.cn)
        try:
            r = self.session.get(
                "https://vf.wkcyvs.cn/vod_pc_static_kkdy/js/rdul.js?ver=122323423",
                timeout=10,
            )
            if r.status_code == 200 and r.text and "vres." in r.text:
                hs = re.findall(r'"(https?://[^"]+)"', r.text)
                hs = [h.rstrip("/") for h in hs if "vres." in h or ":51050" in h]
                if hs:
                    seen, uniq = set(), []
                    for h in self.image_hosts + hs:
                        if h not in seen:
                            seen.add(h)
                            uniq.append(h)
                    self.image_hosts = uniq
                    self.image_host = uniq[0]
        except Exception:
            pass
        self._img_ok = {}
        self.categories = [
            {"type_id": "1", "type_name": "电影"},
            {"type_id": "2", "type_name": "连续剧"},
            {"type_id": "3", "type_name": "动漫"},
            {"type_id": "4", "type_name": "综艺"},
            {"type_id": "6", "type_name": "短剧"},
        ]
        self.filters = self._build_filters()

    def _build_filters(self):
        types = {
            "1": ["剧情", "喜剧", "动作", "爱情", "恐怖", "惊悚", "犯罪", "科幻", "悬疑", "奇幻", "冒险", "战争", "历史", "古装", "家庭", "传记", "武侠", "歌舞", "短片", "动画", "儿童", "职场"],
            "2": ["剧情", "爱情", "喜剧", "犯罪", "悬疑", "古装", "动作", "家庭", "惊悚", "奇幻", "美剧", "科幻", "历史", "战争", "韩剧", "武侠", "言情", "恐怖", "冒险", "都市", "职场"],
            "3": ["动态漫画", "剧情", "动画", "喜剧", "冒险", "动作", "奇幻", "科幻", "儿童", "搞笑", "爱情", "家庭", "短片", "热血", "益智", "悬疑", "经典", "校园", "Anime", "运动", "亲子", "青春", "恋爱", "武侠", "惊悚"],
            "4": ["纪录", "真人秀", "记录", "脱口秀", "剧情", "历史", "喜剧", "传记", "相声", "节目", "歌舞", "冒险", "运动", "Season", "犯罪", "短片", "搞笑", "晚会"],
            "6": ["王爷太子", "霸道总裁", "屌丝逆袭", "赘婿系列", "重生系列", "穿越短剧", "美女总裁", "娇妻系列", "龙王系列", "都市言情", "逆袭", "甜宠", "虐恋", "穿越", "重生", "剧情", "科幻", "武侠", "爱情", "动作", "战争", "冒险", "其它"],
        }
        areas = {
            "1": [("大陆", "中国大陆"), ("香港", "中国香港"), ("台湾", "中国台湾"), ("美国", "美国"), ("日本", "日本"), ("韩国", "韩国"), ("英国", "英国"), ("法国", "法国"), ("德国", "德国"), ("印度", "印度"), ("泰国", "泰国"), ("丹麦", "丹麦"), ("瑞典", "瑞典"), ("巴西", "巴西"), ("加拿大", "加拿大"), ("俄罗斯", "俄罗斯"), ("意大利", "意大利"), ("比利时", "比利时"), ("爱尔兰", "爱尔兰"), ("西班牙", "西班牙"), ("澳大利亚", "澳大利亚"), ("其他", "其他")],
            "2": [("大陆", "中国大陆"), ("香港", "中国香港"), ("韩国", "韩国"), ("美国", "美国"), ("日本", "日本"), ("法国", "法国"), ("英国", "英国"), ("德国", "德国"), ("台湾", "中国台湾"), ("泰国", "泰国"), ("印度", "印度"), ("其他", "其他")],
            "3": [("日本", "日本"), ("大陆", "中国大陆"), ("台湾", "中国台湾"), ("美国", "美国"), ("香港", "中国香港"), ("韩国", "韩国"), ("英国", "英国"), ("法国", "法国"), ("德国", "德国"), ("印度", "印度"), ("泰国", "泰国"), ("丹麦", "丹麦"), ("瑞典", "瑞典"), ("巴西", "巴西"), ("加拿大", "加拿大"), ("俄罗斯", "俄罗斯"), ("意大利", "意大利"), ("比利时", "比利时"), ("爱尔兰", "爱尔兰"), ("西班牙", "西班牙"), ("澳大利亚", "澳大利亚"), ("其他", "其他")],
            "4": [("大陆", "中国大陆"), ("香港", "中国香港"), ("台湾", "中国台湾"), ("美国", "美国"), ("日本", "日本"), ("韩国", "韩国"), ("其他", "其他")],
        }
        langs = ["国语", "粤语", "英语", "日语", "韩语", "法语", "其他"]
        years = [("2026", "2026"), ("2025", "2025"), ("2024", "2024"), ("2023", "2023"), ("2022", "2022"), ("2021", "2021"), ("2020", "2020"), ("10年代", "2010_2019"), ("00年代", "2000_2009"), ("90年代", "1990_1999"), ("80年代", "1980_1989"), ("更早", "0_1979")]
        sorts = {"1": [("综合", "1"), ("最新", "2"), ("最热", "3"), ("评分", "4")], "2": [("综合", "1"), ("最新", "2"), ("最热", "3"), ("评分", "4")], "3": [("综合", "1"), ("最新", "2"), ("最热", "3"), ("评分", "4")], "4": [("综合", "1"), ("最新", "2"), ("最热", "3"), ("评分", "4")], "6": [("综合", "1"), ("最新", "2"), ("最热", "3")]}
        result = {}
        for c in self.categories:
            tid = c["type_id"]
            items = [{"key": "type", "name": "类型", "value": [{"n": "全部", "v": ""}] + [{"n": t, "v": t} for t in types.get(tid, [])]}]
            if tid != "6":
                items += [
                    {"key": "area", "name": "地区", "value": [{"n": "全部", "v": ""}] + [{"n": n, "v": v} for n, v in areas.get(tid, [])]},
                    {"key": "lang", "name": "语言", "value": [{"n": "全部", "v": ""}] + [{"n": l, "v": l} for l in langs]},
                    {"key": "year", "name": "年份", "value": [{"n": "全部", "v": ""}] + [{"n": n, "v": v} for n, v in years]},
                ]
            items.append({"key": "sort", "name": "排序", "value": [{"n": n, "v": v} for n, v in sorts.get(tid, sorts["1"])]})
            result[tid] = items
        return result

    # ===== 网络封装(用 requests.Session,基类 fetch 不支持 timeout/verify) =====
    def _get(self, url):
        try:
            r = self.session.get(url, timeout=15)
            if r.status_code != 200:
                return None
            r.encoding = "utf-8"  # 站点固定 UTF-8
            return r.text
        except Exception:
            return None

    def _fix(self, u):
        if not u:
            return ""
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("/"):
            return self.host + u
        return u

    def _pic(self, u):
        if not u:
            return ""
        u = str(u).strip()
        if not u or "logo_placeholder" in u.lower():
            return ""
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("http"):
            try:
                host = (urlparse(u).hostname or "").lower()
                if "vres." in host or ":51050" in u:
                    return self._img(u)
            except Exception:
                pass
            return u
        if u.startswith("/"):
            return self._img(self.image_host + u)
        return self._img(self.image_host + "/" + u.lstrip("/"))

    def _img_ok_host(self, host_url):
        try:
            if host_url in self._img_ok:
                return self._img_ok[host_url]
            host = urlparse(host_url).hostname or ""
            if not host:
                return False
            try:
                ips = socket.gethostbyname_ex(host)[2] or []
            except Exception:
                self._img_ok[host_url] = True
                return True
            if not ips:
                self._img_ok[host_url] = True
                return True
            ok = any(ip and not ip.startswith("127.") for ip in ips)
            self._img_ok[host_url] = ok
            return ok
        except Exception:
            return True

    def _img(self, url):
        try:
            path = urlparse(url).path or ""
            if not path:
                return url
            for h in (getattr(self, "image_hosts", None) or [getattr(self, "image_host", "")]):
                try:
                    if not h:
                        continue
                    if not self._img_ok_host(h):
                        continue
                    return h.rstrip("/") + path
                except Exception:
                    continue
            return url
        except Exception:
            return url

    def _html(self, content):
        if not content:
            return None
        return etree.HTML(content.encode("utf-8"))

    def _referer(self, url):
        return self.host + "/"

    def _play_lines(self, tree):
        sources = []
        for a in tree.xpath('//div[contains(@class,"source-list-box-main")]//a[contains(@class,"source-item")]'):
            name = "".join(a.xpath('.//span[contains(@class,"source-item-label")]//text()')).strip()
            if name:
                sources.append(name)
        lines = []
        for li in tree.xpath('//div[contains(@class,"episode-list-box-main")]/div[contains(@class,"episode-list")]'):
            eps = [f'{"".join(a.xpath(".//text()")).strip() or "播放"}${self._fix(a.get("href", ""))}' for a in li.xpath('.//a[contains(@href,"/play/")]')]
            if eps:
                lines.append(eps)
        if not lines:
            return []
        if not sources:
            sources = [f"线路{i + 1}" for i in range(len(lines))]
        while len(lines) > len(sources):
            sources.append(f"线路{len(sources) + 1}")
        pairs = list(zip(sources[:len(lines)], lines))
        return [(name, eps) for name, eps in pairs if "4k" not in name.lower()]

    def _parse_list(self, html):
        if not html:
            return []
        tree = self._html(html)
        if tree is None:
            return []
        out, seen = [], set()
        for a in tree.xpath('//div[contains(@class,"module-item")]//a[contains(@class,"v-item")]'):
            href = a.get("href", "")
            m = re.search(r'/detail/(\d+)\.html', href)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            name = "".join(a.xpath('.//div[contains(@class,"v-item-title")][not(@style)]//text()')).strip()
            if not name:
                continue
            pic = (a.xpath('.//img[not(contains(@data-original,"logo_placeholder"))]/@data-original') or [""])[0]
            rem = "".join(a.xpath('.//div[contains(@class,"v-item-bottom")]//text()')).strip()
            out.append({"vod_id": m.group(1), "vod_name": name, "vod_pic": self._pic(pic), "vod_remarks": rem})
        return out

    def homeContent(self, filter):
        return {"class": self.categories, "list": self._parse_list(self._get(self.host + "/")), "filters": self.filters}

    def homeVideoContent(self):
        return {"list": self._parse_list(self._get(self.host + "/"))}

    def categoryContent(self, tid, pg, filter, extend):
        extend = extend or {}
        typ = quote(str(extend.get("type", "")))
        area = quote(str(extend.get("area", "")))
        lang = quote(str(extend.get("lang", "")))
        year = quote(str(extend.get("year", "")))
        sort = str(extend.get("sort", "3") or "3")
        page = int(pg or 1)
        html = self._get(f"{self.host}/show/{tid}-{typ}-{area}-{lang}-{year}-{sort}-{page}.html")
        items = self._parse_list(html)
        pagecount = page + 1 if html and "page-item-next" in html else page
        return {"page": page, "pagecount": pagecount, "limit": len(items), "total": len(items), "list": items}

    def _names(self, txt):
        txt = re.sub(r'\s*/\s*', '/', txt)
        return re.sub(r'\s+', ' ', txt).strip(' /')

    def detailContent(self, ids):
        vid = str(ids[0]) if ids else ""
        html = self._get(f"{self.host}/detail/{vid}.html")
        result = {"list": []}
        if not html:
            return result
        tree = self._html(html)
        if tree is None:
            return result
        name = "".join(tree.xpath('//div[contains(@class,"detail-title")]//strong[position() mod 2 = 0]/text()')).strip()
        if not name:
            name = "".join(tree.xpath('//div[contains(@class,"detail-title")]//strong[1]/text()')).strip()
        pic = (tree.xpath('//div[contains(@class,"detail-pic")]//img[not(contains(@data-original,"logo_placeholder"))]/@data-original') or [""])[0]
        desc = "".join(tree.xpath('//div[contains(@class,"detail-desc")]//text()')).strip()
        vod = {"vod_id": vid, "vod_name": name, "vod_pic": self._pic(pic), "vod_content": desc}
        rows = {}
        for r in tree.xpath('//div[contains(@class,"detail-info-row")]'):
            k = "".join(r.xpath('.//*[contains(@class,"detail-info-row-side")]//text()')).strip().rstrip(":")
            v = "".join(r.xpath('.//*[contains(@class,"detail-info-row-main")]//text()')).strip()
            if k and v:
                rows[k] = v
        if "导演" in rows:
            vod["vod_director"] = self._names(rows["导演"])
        if "演员" in rows:
            vod["vod_actor"] = self._names(rows["演员"])
        if "首映" in rows:
            ym = re.search(r'(19|20)\d{2}', rows["首映"])
            if ym:
                vod["vod_year"] = ym.group(0)
        if "备注" in rows:
            vod["vod_remarks"] = rows["备注"]
        tags = ["".join(a.xpath('.//text()')).strip() for a in tree.xpath('//a[contains(@class,"detail-tags-item")]')]
        tags = [t for t in tags if t]
        if tags:
            ym = re.search(r'(19|20)\d{2}', tags[0])
            if "vod_year" not in vod and ym:
                vod["vod_year"] = ym.group(0)
            if len(tags) > 1:
                vod["vod_area"] = tags[1]
            if len(tags) > 2:
                vod["vod_type"] = "/".join(tags[2:])
        lines = self._play_lines(tree)
        if not lines:
            return result
        vod["vod_play_from"] = "$$$".join(name for name, _ in lines)
        vod["vod_play_url"] = "$$$".join("#".join(eps) for _, eps in lines)
        result["list"].append(vod)
        return result

    def searchContent(self, key, quick, pg="1"):
        k = quote(key)
        items = []
        h = self._get(f"{self.host}/search?k={k}")
        m = re.search(r'name="t"\s+value="([^"]+)"', h or "")
        if m:
            url = f"{self.host}/search?k={k}&t={quote(m.group(1))}"
            if pg and pg != "1":
                url += f"&page={pg}"
            h2 = self._get(url)
            if h2:
                tree = self._html(h2)
                if tree is not None:
                    for a in tree.xpath('//a[contains(@class,"search-result-item")]'):
                        m2 = re.search(r'/detail/(\d+)\.html', a.get("href", ""))
                        if not m2:
                            continue
                        name = "".join(a.xpath('.//*[contains(@class,"title")]//text()')).strip()
                        if not name:
                            continue
                        pic = (a.xpath('.//img[not(contains(@data-original,"logo_placeholder"))]/@data-original') or [""])[0]
                        items.append({"vod_id": m2.group(1), "vod_name": name, "vod_pic": self._pic(pic)})
        return {"list": items, "page": int(pg or 1)}

    def playerContent(self, flag, id, vipFlags):
        url = self._fix(id)
        play = ""
        html = self._get(url)
        if html:
            for pat in [r'playSource\s*=\s*\{[^}]*?src:\s*"([^"]+)"',
                        r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
                        r'(https?://[^\s"\'<>]+\.(?:mp4|flv|mkv|webm)[^\s"\'<>]*)']:
                m = re.search(pat, html)
                if m:
                    play = m.group(1)
                    break
        play = self._fix(play)
        header = {"User-Agent": self.headers["User-Agent"], "Referer": self._referer(play or url)}
        if not play:
            return {"parse": 1, "url": url, "header": header}
        return {"parse": 0, "url": play, "header": header}

    def isVideoFormat(self, url):
        return ".m3u8" in url or ".mp4" in url

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        return None

    def destroy(self):
        return None
