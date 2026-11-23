#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
4K影视网 (4kvms.org) 爬虫 - TVBox/影视仓 Spider 插件
站点模板: 4kvm 系列, data-vod-id 卡片 + 签名播放

接口说明:
- 首页:      /                      (data-vod-id 卡片)
- 分类列表:  /filter?classify=N     (1电影/2电视剧/3动漫/4综艺) + areas/types/years/tags/sort_by/page
- 详情:      /play/{id}            (分集 dataid + nb-st/userlink)
- 搜索:      /search?q={key}
- 播放:      纯 Python 签名 -> /video/play?p=&v=&q=&s=&t=&k= -> m3u8

播放签名算法 (已逆向, 纯标准库实现, 无 node/wasm 依赖):
  s = HMAC-SHA256(secret_key, dataid + ":" + timestamp + ":" + secret_key) 的前 16 字节 hex
  k = base64( userlink 逐字节 XOR "nbmovie2024secretkey"(循环) )
  t = 当前毫秒时间戳
"""

import re
import json
import logging
import urllib.parse
import os
import sys
import time
import base64
import hmac
import hashlib
import requests
from bs4 import BeautifulSoup

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from base.spider import Spider as BaseSpider
except ImportError:
    BaseSpider = object

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 脚本所在目录 (用于 sys.path 追加 base 框架路径)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


class Spider(BaseSpider):
    """4K影视网 (4kvms.org) 爬虫"""

    BASE_URL = "https://www.4kvms.org"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    # 一级分类 (classify -> 名称)
    CLASSIFY_MAP = {
        "1": "电影",
        "2": "电视剧",
        "3": "动漫",
        "4": "综艺",
    }

    def __init__(self):
        try:
            super().__init__()
        except Exception:
            pass
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def init(self, extend=''):
        pass

    def getName(self):
        return "4K影视网"

    # ========== 工具方法 ==========
    def _get_html(self, url, referer=None):
        headers = {}
        if referer:
            headers['Referer'] = referer
        r = self.session.get(url, timeout=30, headers=headers)
        r.raise_for_status()
        return r.text

    def _parse_cards(self, html):
        """解析视频卡片列表 (兼容首页 data-vod-id 和搜索 group.relative 两种结构)"""
        soup = BeautifulSoup(html, 'html.parser')
        cards = soup.select('div[data-vod-id]')
        out = []
        seen = set()

        # 方式1: data-vod-id 卡片
        for c in cards:
            a = c.select_one('a.block[href^="/play/"]') or c.select_one('a[href^="/play/"]')
            if not a:
                continue
            vod_id = c.get('data-vod-id', '').strip() or a['href'].replace('/play/', '').strip()
            h3 = c.select_one('h3')
            name = h3.get_text(strip=True) if h3 else ''
            img = c.select_one('img[data-src]')
            pic = ''
            if img:
                src = img.get('data-src', '') or img.get('src', '')
                if src and not src.startswith('data:') and 'placeholder' not in src:
                    pic = src if src.startswith('http') else 'https:' + src
            remarks = self._extract_remarks(c)
            if vod_id and name and vod_id not in seen:
                seen.add(vod_id)
                out.append({'vod_id': vod_id, 'vod_name': name, 'vod_pic': pic, 'vod_remarks': remarks})

        # 方式2: 搜索页 group.relative 卡片 (无 data-vod-id)
        if not out:
            for a in soup.select('div.group.relative a[href^="/play/"]'):
                vod_id = a['href'].replace('/play/', '').strip()
                h3 = a.select_one('h3')
                name = h3.get_text(strip=True) if h3 else (a.select_one('img') and a.select_one('img').get('alt', '')) or ''
                img = a.select_one('img[data-src]') or a.select_one('img[src]')
                pic = ''
                if img:
                    src = img.get('data-src', '') or img.get('src', '')
                    if src and not src.startswith('data:') and 'placeholder' not in src:
                        pic = src if src.startswith('http') else 'https:' + src
                if vod_id and name and vod_id not in seen:
                    seen.add(vod_id)
                    out.append({'vod_id': vod_id, 'vod_name': name, 'vod_pic': pic, 'vod_remarks': ''})
        return out

    def _extract_remarks(self, card):
        """从卡片提取备注 (更新状态/评分)"""
        t = card.get_text(' ', strip=True)
        # 常见备注: "更新至26集", "HD", "全12集", "8.9"
        for m in re.findall(r'(更新至\d+集|全\d+集|已完结|HD|TC|TS|4K|\d+\.\d+)', t):
            return m
        return ''

    # ========== 首页 ==========
    def homeContent(self, filter=False):
        try:
            html = self._get_html(self.BASE_URL + '/')
            classes = [{'type_id': cid, 'type_name': name} for cid, name in self.CLASSIFY_MAP.items()]
            return {
                'class': classes,
                'filters': self._get_filters(),
                'list': self._parse_cards(html),
            }
        except Exception as e:
            logger.error(f"获取首页失败: {e}")
            return {'class': [{'type_id': cid, 'type_name': n} for cid, n in self.CLASSIFY_MAP.items()], 'list': []}

    def homeVideoContent(self):
        home = self.homeContent()
        return {'list': home.get('list', [])}

    def _get_filters(self):
        """筛选配置: 地区/类型/年份/排序"""
        areas = {'5': '美国', '6': '法国', '7': '中国', '11': '日本', '12': '韩国', '14': '中国香港',
                 '16': '俄罗斯', '18': '德国', '19': '意大利', '21': '中国台湾', '22': '澳大利亚',
                 '24': '西班牙', '30': '英国', '32': '加拿大', '33': '泰国', '34': '印度',
                 '52': '中国大陆', '78': '其他', '80': '挪威', '84': '爱尔兰'}
        types = {'1': '剧情', '2': '悬疑', '3': '恐怖', '4': '惊悚', '5': '喜剧', '6': '爱情',
                 '9': '犯罪', '10': '动作', '11': '动画', '12': '奇幻', '13': '音乐', '14': '科幻',
                 '15': '历史', '16': '战争', '18': '冒险', '19': '家庭', '20': '纪录', '27': '古装',
                 '28': '传记', '31': '武侠', '33': '纪录片', '34': '灾难'}
        filters = {}
        for cid in self.CLASSIFY_MAP:
            filters[cid] = [
                {'key': 'areas', 'name': '地区',
                 'value': [{'n': '全部', 'v': ''}] + [{'n': n, 'v': k} for k, n in sorted(areas.items(), key=lambda x: int(x[0]))]},
                {'key': 'types', 'name': '类型',
                 'value': [{'n': '全部', 'v': ''}] + [{'n': n, 'v': k} for k, n in sorted(types.items(), key=lambda x: int(x[0]))]},
                {'key': 'sort_by', 'name': '排序',
                 'value': [{'n': '最新上映', 'v': 'update_time'}, {'n': '最受欢迎', 'v': 'hits'}, {'n': '评分最高', 'v': 'score'}]},
            ]
        return filters

    # ========== 分类 ==========
    def categoryContent(self, tid, pg, filter=False, ext=''):
        try:
            page = int(pg) if pg else 1
            classify = str(tid).split('|')[0]
            ext_dict = self._parse_ext(ext)
            params = {'classify': classify, 'page': page}
            if ext_dict.get('areas'):
                params['areas'] = ext_dict['areas']
            if ext_dict.get('types'):
                params['types'] = ext_dict['types']
            if ext_dict.get('sort_by'):
                params['sort_by'] = ext_dict['sort_by']
                params['order'] = 'desc'
            url = self.BASE_URL + '/filter?' + urllib.parse.urlencode(params)
            html = self._get_html(url)
            soup = BeautifulSoup(html, 'html.parser')
            vod_list = self._parse_cards(html)
            total = self._extract_total(soup)
            total_page = self._extract_total_page(soup)
            return {
                'list': vod_list,
                'page': page,
                'pagecount': max(total_page, 1),
                'limit': len(vod_list) or 24,
                'total': total,
            }
        except Exception as e:
            logger.error(f"获取分类内容失败: {e}")
            return {'list': [], 'page': 1, 'pagecount': 1, 'limit': 24, 'total': 0}

    def _extract_total(self, soup):
        for m in soup.find_all(string=re.compile(r'共\s*[\d,]+\s*部|共\s*[\d,]+\s*个|[\d,]+\s*部影视')):
            n = re.search(r'[\d,]+', m)
            if n:
                return int(n.group().replace(',', ''))
        return 0

    def _extract_total_page(self, soup):
        # 找分页链接里的最大页码
        mx = 1
        for a in soup.select('a[href*="page="]'):
            m = re.search(r'page=(\d+)', a['href'])
            if m:
                mx = max(mx, int(m.group(1)))
        # 也找 "共N页"
        for t in soup.find_all(string=re.compile(r'共\s*(\d+)\s*页')):
            m = re.search(r'(\d+)', t)
            if m:
                mx = max(mx, int(m.group(1)))
        return mx

    def _parse_ext(self, ext):
        if not ext:
            return {}
        if isinstance(ext, dict):
            return ext
        if isinstance(ext, str):
            try:
                return json.loads(ext)
            except Exception:
                return {}
        return {}

    # ========== 详情 ==========
    def detailContent(self, ids):
        try:
            vod_id = ids[0] if isinstance(ids, list) else str(ids)
            url = f"{self.BASE_URL}/play/{vod_id}"
            html = self._get_html(url)
            soup = BeautifulSoup(html, 'html.parser')

            # 标题
            h1 = soup.select_one('h1')
            name = h1.get_text(strip=True) if h1 else vod_id

            # 元信息 (grid 布局: col-span-1 标签 + col-span-2 值)
            meta = {}
            labels = soup.select('div.col-span-1.text-gray-500')
            for lab in labels:
                key = lab.get_text(strip=True)
                val_div = lab.find_next_sibling('div')
                val = val_div.get_text(strip=True) if val_div else ''
                if key and val:
                    meta[key] = val

            # 分集 (dataid + href)
            episodes = []
            for a in soup.select('a[dataid]'):
                dataid = a.get('dataid', '').strip()
                ep_num = a.get('data-episode', '').strip()
                ep_href = a.get('href', '').strip()
                if dataid and ep_href:
                    episodes.append({'dataid': dataid, 'episode': ep_num, 'href': ep_href})

            # 播放源 (线路) - 从 episodeManager 的 lineName 提取
            lines = []
            m = re.search(r'episodeManager\([^,]+,[^,]+,\s*\[(.*?)\]\)', html, re.DOTALL)
            if m:
                for lm in re.finditer(r"lineName\s*:\s*'([^']+)'", m.group(1)):
                    lines.append(lm.group(1))

            if not lines:
                lines = ['默认']

            # 构建播放列表: 每集生成 "集名$占位" (真实地址在 playerContent 动态解析)
            # 这里用 dataid 作为播放标识, playerContent 收到后重新走 wasm 签名
            play_url_list = []
            play_from_list = []
            for line in lines:
                ep_items = []
                for ep in episodes:
                    ep_name = f"第{ep['episode']}集" if ep['episode'] else "正片"
                    # 播放标识: dataid@@vod_id (与文才一致的 @@ 分隔, 不含 $ # |)
                    play_id = f"{ep['dataid']}@@{ep['href'].replace('/play/', '')}"
                    ep_items.append(f"{ep_name}${play_id}")
                if ep_items:
                    play_from_list.append(line)
                    play_url_list.append('#'.join(ep_items))

            vod_item = {
                'vod_id': vod_id,
                'vod_name': name,
                'vod_pic': self._get_detail_pic(soup),
                'type_name': meta.get('类型', ''),
                'vod_year': self._extract_year(meta, soup),
                'vod_area': meta.get('地区', ''),
                'vod_lang': meta.get('语言', ''),
                'vod_remarks': self._get_detail_remarks(soup),
                'vod_actor': meta.get('主演', '') or meta.get('演员', ''),
                'vod_director': meta.get('导演', ''),
                'vod_content': self._get_detail_content(soup),
                'vod_play_from': '$$$'.join(play_from_list),
                'vod_play_url': '$$$'.join(play_url_list),
            }
            return {'list': [vod_item]}
        except Exception as e:
            logger.error(f"获取详情失败: {e}")
            return {'list': []}

    def _extract_year(self, meta, soup):
        """从 '上映' 字段或 keywords 提取年份"""
        shangyin = meta.get('上映', '') or meta.get('年份', '')
        m = re.search(r'(\d{4})', shangyin)
        if m:
            return m.group(1)
        km = soup.select_one('meta[name="keywords"]')
        if km:
            m = re.search(r'\b(19\d{2}|20\d{2})\b', km.get('content', ''))
            if m:
                return m.group(1)
        return ''

    def _get_detail_pic(self, soup):
        img = soup.select_one('img[data-poster]') or soup.select_one('.video-player img') or soup.select_one('img[alt]')
        if img:
            src = img.get('data-poster', '') or img.get('src', '')
            if src and not src.startswith('data:') and 'placeholder' not in src:
                return src if src.startswith('http') else 'https:' + src
        return ''

    def _get_detail_remarks(self, soup):
        # 更新状态: "更新至26/30集"
        t = soup.get_text(' ', strip=True)
        m = re.search(r'更新至\s*([\d/]+集)', t)
        if m:
            return '更新至' + m.group(1)
        m = re.search(r'(\d+\.\d+)', t)
        if m:
            return m.group(1)
        return ''

    def _get_detail_content(self, soup):
        # 简介: meta description 或 页面里的简介文本
        m = soup.select_one('meta[name="description"]')
        if m:
            return m.get('content', '')[:500]
        # 找 "简介" 标签后的文本
        for lab in soup.select('div.col-span-1.text-gray-500'):
            if '简介' in lab.get_text():
                v = lab.find_next_sibling('div')
                if v:
                    return v.get_text(strip=True)[:500]
        return ''

    # ========== 搜索 ==========
    def searchContent(self, key, quick=False, pg='1'):
        try:
            page = int(pg) if pg else 1
            url = f"{self.BASE_URL}/search?q={urllib.parse.quote(key)}"
            if page > 1:
                url += f"&page={page}"
            html = self._get_html(url)
            vod_list = self._parse_cards(html)
            # 总数: "找到 N 个结果"
            total = 0
            m = re.search(r'找到\s*<span[^>]*>\s*(\d+)\s*</span>\s*个结果', html)
            if m:
                total = int(m.group(1))
            return {'list': vod_list, 'page': page, 'pagecount': 1, 'limit': len(vod_list), 'total': total}
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return {'list': [], 'page': 1, 'pagecount': 1, 'limit': 20, 'total': 0}

    # ========== 播放 ==========
    def playerContent(self, flag, id, vipFlags=None):
        """播放: 纯 Python 生成签名, 请求 /video/play 拿真实 m3u8

        返回 parse=0 + url 列表 [分辨率名, 地址, ...] (与文才一致的多画质格式)
        """
        # 注意: 播放流 header 不要带 Referer/Origin!
        # 实测 douyinvod.com 的 mp4 直链带 4kvms Referer 会返回 403,
        # 而不带 Referer 则正常(200)。m3u8 和 byteimg 直链则两者都正常。
        header = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.61 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        try:
            play_id = urllib.parse.unquote(str(id or ''))
            # 播放标识格式: dataid@@vod_id (详情页构造的)
            if '@@' in play_id:
                dataid, secret_key = play_id.split('@@', 1)
            else:
                # 兼容: 直接是 dataid (旧格式), 此时用 dataid 同时作为详情页 id
                dataid, secret_key = play_id, play_id

            # 1. 抓详情页拿 nb-st 和 userlink (动态值, 必须实时)
            detail_url = f"{self.BASE_URL}/play/{secret_key}"
            html = self._get_html(detail_url, referer=self.BASE_URL + '/')
            nb_st = ''
            m = re.search(r'<meta id="nb-st" content="([^"]*)"', html)
            if m:
                nb_st = m.group(1)
            userlink = '0'
            m2 = re.search(r"userlink:'([^']*)'", html)
            if m2:
                userlink = m2.group(1)

            if not nb_st or not dataid:
                return {'parse': 0, 'url': [], 'header': header}

            # 2. 纯 Python 生成签名 URL (无 node/wasm 依赖)
            sign_url = self._sign(nb_st, userlink, dataid, secret_key or dataid)

            if not sign_url:
                return {'parse': 0, 'url': [], 'header': header}

            # 3. 请求签名 URL 拿 m3u8
            full = self.BASE_URL + sign_url
            r = self.session.get(full, timeout=30, headers={'Referer': detail_url})
            data = r.json()
            if data.get('code') != 200 or not data.get('data'):
                return {'parse': 0, 'url': [], 'header': header}

            # 4. 组装 [分辨率名, url] 交替列表 (与文才一致)
            quality_urls = data['data'].get('quality_urls', [])
            vlist = []
            for q in quality_urls:
                if q.get('locked') or q.get('isvip'):
                    continue
                url = q.get('url', '')
                if not url or url == '1':
                    continue
                title = q.get('title', '').strip()
                desc = q.get('description', '').strip()
                name = f"{title} {desc}".strip() if title else (desc or '高清')
                vlist.extend([name, url])

            # 兜底: 一个画质都没有时取第一个非锁定
            if not vlist:
                for q in quality_urls:
                    url = q.get('url', '')
                    if url and url != '1' and not q.get('locked'):
                        title = q.get('title', '').strip()
                        desc = q.get('description', '').strip()
                        name = f"{title} {desc}".strip() if title else (desc or '高清')
                        vlist.extend([name, url])
                        break

            return {'parse': 0, 'url': vlist, 'header': header}
        except Exception as e:
            logger.error(f"播放解析失败: {e}")
            return {'parse': 0, 'url': [], 'header': header}

    def _sign(self, nb_st, userlink, dataid, secret_key, quality='1080'):
        """纯 Python 生成签名 URL (已逆向 wasm 算法, 无 node 依赖)

        签名算法:
          t = 当前毫秒时间戳
          s = HMAC-SHA256(secret_key, dataid+":"+t+":"+secret_key) 前 16 字节 hex
          k = base64( userlink 逐字节 XOR "nbmovie2024secretkey"(循环) )
          返回: /video/play?p={dataid}&v={secret}&q={quality}&s={s}&t={t}&k={k}
        """
        try:
            timestamp = str(int(time.time() * 1000))
            # s: HMAC-SHA256(key=secret_key, msg=dataid:timestamp:secret_key)[:16] hex
            msg = f"{dataid}:{timestamp}:{secret_key}"
            s = hmac.new(secret_key.encode('utf-8'), msg.encode('utf-8'), hashlib.sha256).hexdigest()[:32]
            # k: base64(userlink XOR key循环)
            key = "nbmovie2024secretkey"
            xor_bytes = bytes(ord(c) ^ ord(key[i % len(key)]) for i, c in enumerate(userlink or ''))
            k = base64.b64encode(xor_bytes).decode('ascii')
            # 组装签名 URL
            return (
                f"/video/play?p={dataid}&v={secret_key}&q={quality}"
                f"&s={s}&t={timestamp}&k={k}"
            )
        except Exception as e:
            logger.error(f"签名生成失败: {e}")
            return ''


def main():
    spider = Spider()
    print("=" * 60)
    print("【1】测试首页")
    home = spider.homeContent()
    print(f"分类数: {len(home.get('class', []))}")
    for c in home.get('class', []):
        print(f"  - {c['type_name']} ({c['type_id']})")
    print(f"推荐视频数: {len(home.get('list', []))}")
    for v in home.get('list', [])[:3]:
        print(f"  - {v['vod_name']} [{v['vod_remarks']}] ({v['vod_id']})")

    print("\n" + "=" * 60)
    print("【2】测试分类列表 (电视剧 classify=2)")
    cat = spider.categoryContent("2", "1", False, {})
    print(f"总数: {cat.get('total')}, 本页: {len(cat.get('list', []))}")
    for v in cat.get('list', [])[:3]:
        print(f"  - {v['vod_name']} [{v['vod_remarks']}]")

    print("\n" + "=" * 60)
    print("【3】测试详情")
    if cat.get('list'):
        first_id = cat['list'][0]['vod_id']
        detail = spider.detailContent([first_id])
        if detail.get('list'):
            d = detail['list'][0]
            print(f"标题: {d['vod_name']}")
            print(f"年份: {d['vod_year']}, 地区: {d['vod_area']}")
            print(f"导演: {d['vod_director']}")
            print(f"播放源: {d['vod_play_from']}")

    print("\n" + "=" * 60)
    print("【4】测试搜索 (谷雨)")
    search = spider.searchContent("谷雨", False, "1")
    print(f"结果数: {search.get('total')}")
    for v in search.get('list', [])[:3]:
        print(f"  - {v['vod_name']}")

    print("\n测试完成!")


if __name__ == "__main__":
    main()
