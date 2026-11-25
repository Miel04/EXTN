# -*- coding: utf-8 -*-
"""
咪咕视频 (miguvideo.com) TVBox 爬虫 —— 仅直播频道, 只保留「央视」「卫视」两个分类

数据来源(全部为咪咕官方公开接口, 实时请求, 不落盘):
  1. 频道分组 + 频道列表:
       https://webapi.miguvideo.com/gateway/live/v2/tv-data/{vomsID}
       根分组 vomsID = e7716fea6aa1483c80cfc10b7795fcb8
       返回 body.liveList = 全部分组(本插件只取 央视 / 卫视, 其余分组已过滤)
            body.dataList = 该分组下的频道(name/pID/pics/h5pics/nowPlaying)
  2. 正在播/下一档:
       https://webapi.miguvideo.com/gateway/live/v2/nowAndNextPlaybill/{pID}
  3. 当天节目单:
       https://webapi.miguvideo.com/gateway/live/v2/tv-programs-data/{pID}/{yyyyMMdd}
  4. 播放地址:
       https://webapi.miguvideo.com/gateway/playurl/v3/play/playurl?contId={pID}&rateType=3&clientId=1&timestamp=ms
       body.urlInfo.url 即 m3u8 直链(部分地址需追加 ddCalcu 校验参数, 已按站点 JS 还原)

说明:
  * 咪咕直播对境外 IP 返回 403 COPYRIGHT_AREA_INVALID(版权地区限制), 国内网络正常;
    因此每个频道同时挂着内置的公共备用直链, 官方线路拿不到 m3u8 时自动切备用线路。
  * 海报使用官方 1080 竖版剧照(h5pics.highResolutionV), 缺失时回退高清台标。
"""
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.error

from concurrent.futures import ThreadPoolExecutor

try:
    from base.spider import Spider
except Exception:
    try:
        from base import Spider
    except Exception:
        class Spider(object):
            def getName(self): return '咪咕视频'
            def init(self, e=''): return None
            def homeContent(self, filter=False): return {}
            def homeVideoContent(self): return {}
            def categoryContent(self, tid, pg=1, filter=False, extend=None): return {}
            def detailContent(self, ids): return {}
            def searchContent(self, key, quick=False, pg=''): return {}
            def playerContent(self, flag, id, vipFlags=None): return {}
            def isVideoFormat(self, url): return False
            def manualVideoCheck(self): return False
            def getDependence(self): return []
            def getProxyUrl(self): return 'http://127.0.0.1:9978/proxy?do=py'
            def localProxy(self, param): return [404, '', b'']
            def action(self, a): return {}
            def destroy(self): return None


# ==================== 常量 ====================
API = 'https://webapi.miguvideo.com/gateway'
ROOT_VOMS = 'e7716fea6aa1483c80cfc10b7795fcb8'

UA_MOBILE = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
             'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 '
             'Mobile/15E148 Safari/604.1')

HEADERS = {
    'User-Agent': UA_MOBILE,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Referer': 'https://m.miguvideo.com/',
    'Origin': 'https://m.miguvideo.com',
}

# 只保留这两个分类(其余分组在 _load_groups 里被过滤掉)
KEEP_CATS = ('央视', '卫视')

# 分组兜底(接口拿不到 liveList 时用), 顺序即 TVBox 里看到的分类顺序
CATS_FALLBACK = [
    ('1ff892f2b5ab4a79be6e25b69d2f5d05', '央视'),
    ('0847b3f6c08a4ca28f85ba5701268424', '卫视'),
]

# 分类排序偏好(接口 liveList 顺序不完全适合展示)
CAT_ORDER = ['央视', '卫视']

# 分组 ID 失效时的回退分组
FALLBACK_VOMS = '1ff892f2b5ab4a79be6e25b69d2f5d05'

PAGE_SIZE = 30

# 公共备用直链(与 央视频.py 同一套已跑通的源), 官方线路不可播时兜底
LIVE_MAP = {
    'CCTV1': [
        'http://74.91.26.218:82/live/cctv1hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226895/1.m3u8?',
    ],
    'CCTV2': [
        'http://74.91.26.218:82/live/cctv2hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226893/1.m3u8?',
    ],
    'CCTV3': [
        'http://74.91.26.218:82/live/cctv3hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226471/1.m3u8?',
    ],
    'CCTV4': [
        'http://74.91.26.218:82/live/cctv4hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226335/1.m3u8?',
    ],
    'CCTV5': [
        'http://74.91.26.218:82/live/cctv5hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226469/1.m3u8?',
    ],
    'CCTV6': [
        'http://74.91.26.218:82/live/cctv6hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226468/1.m3u8?',
    ],
    'CCTV7': [
        'http://74.91.26.218:82/live/cctv7hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226946/1.m3u8?',
    ],
    'CCTV8': [
        'http://74.91.26.218:82/live/cctv8hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226485/1.m3u8?',
    ],
    'CCTV9': [
        'http://74.91.26.218:82/live/cctv9hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226944/1.m3u8?',
    ],
    'CCTV10': [
        'http://74.91.26.218:82/live/cctv10hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226937/1.m3u8?',
    ],
    'CCTV11': [
        'http://74.91.26.218:82/live/cctv11hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226334/1.m3u8?',
    ],
    'CCTV12': [
        'http://74.91.26.218:82/live/cctv12hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226942/1.m3u8?',
    ],
    'CCTV13': [
        'http://74.91.26.218:82/live/cctv13hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226316/1.m3u8?',
    ],
    'CCTV14': [
        'http://74.91.26.218:82/live/cctv14hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226947/1.m3u8?',
    ],
    'CCTV15': [
        'http://74.91.26.218:82/live/cctv15hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226333/1.m3u8?',
    ],
    'CCTV16': [
        'http://74.91.26.218:82/live/cctv16hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221227002/1.m3u8?',
    ],
    'CCTV17': [
        'http://74.91.26.218:82/live/cctv17hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226318/1.m3u8?',
    ],
    'CCTV16-HD': [
        'http://74.91.26.218:82/live/cctv16hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221227002/1.m3u8?',
    ],
    'CCTV5+': [
        'http://74.91.26.218:82/live/cctv5p.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226894/1.m3u8?',
    ],
    '北京卫视': [
        'http://74.91.26.218:82/live/beijinghd2.m3u8',
        'http://ivi.bupt.edu.cn/hls/btv1.m3u8',
        'http://14.152.88.77/liveplay-kk.rtxapp.com/live/program/live/bjwshd/4000000/mnf.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226900/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7EcYPi33WFyhvd6SjmqUKhJg%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '江苏卫视': [
        'http://74.91.26.218:82/live/jswshd.m3u8',
        'http://39.134.24.166/dbiptv.sn.chinamobile.com/PLTV/88888890/224/3221226200/index.m3u8',
        'http://39.134.115.163:8080/PLTV/88888910/224/3221225702/index.m3u8',
        'http://39.134.115.163:8080/PLTV/88888910/224/3221225743/index.m3u8',
        'http://112.25.48.68/live/program/live/jswshd/4000000/mnf.m3u8',
    ],
    '湖北卫视': [
        'http://74.91.26.218:82/live/hubeitv.m3u8',
        'http://74.91.26.218:82/live/hubeihd2.m3u8',
        'http://74.91.26.218:82/live/hubws1.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226952/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7EREB40lnZnCEwjRy7LZuhIQ%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '河南卫视': [
        'http://74.91.26.218:82/live/hentv.m3u8',
        'http://74.91.26.218:82/live/hn3wshd.m3u8',
    ],
    '海南卫视': [
        'http://74.91.26.218:82/live/hainanwshd.m3u8',
        'http://74.91.26.218:82/live/hainsd.m3u8',
        'http://74.91.26.218:82/live/hainwshd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221227029/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7EEuBMjt2kLMD8fAO7QYER7Q%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '重庆卫视': [
        'http://74.91.26.218:82/live/chongqingtvhd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226963/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7EjTXsJprEx2nE38tdvu5lhA%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '深圳卫视': [
        'http://74.91.26.218:82/live/szwshd.m3u8',
        'https://livepull-tcms.sztv.com.cn/live/sz4Kpgm.m3u8',
        'http://39.134.24.166/dbiptv.sn.chinamobile.com/PLTV/88888890/224/3221226205/index.m3u8',
        'http://39.134.39.39/PLTV/88888888/224/3221226199/index.m3u8',
        'http://39.134.115.163:8080/PLTV/88888910/224/3221225700/index.m3u8',
    ],
    '贵州卫视': [
        'http://74.91.26.218:82/live/gztv.m3u8',
        'http://183.207.248.71/gitv/live1/G_GUIZHOU/G_GUIZHOU',
        'http://223.110.245.149/ott.js.chinamobile.com/PLTV/3/224/3221225787/index.m3u8',
        'http://125.210.152.18:9090/live/GZWSHD_H265.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221227012/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7EnqBF03rFwPucF8ODtWxLQQ%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '辽宁卫视': [
        'http://74.91.26.218:82/live/lnhd.m3u8',
        'http://39.134.39.37/PLTV/88888888/224/3221226209/index.m3u8',
        'http://39.134.115.163:8080/PLTV/88888910/224/3221225735/index.m3u8',
        'http://39.135.138.59:18890/PLTV/88888910/224/3221225696/index.m3u8',
        'http://112.25.48.68/live/program/live/lnwshd/4000000/mnf.m3u8',
    ],
    '山东卫视': [
        'http://74.91.26.218:82/live/shandonghd.m3u8',
        'http://125.210.152.18:9090/live/SDWSHD_H265.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226957/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7EjW26v5VaHGy1jQuIA-4EbA%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '天津卫视': [
        'http://74.91.26.218:82/live/tianjinsd.m3u8',
        'http://39.134.115.163:8080/PLTV/88888910/224/3221225698/index.m3u8',
        'http://39.134.115.163:8080/PLTV/88888910/224/3221225739/index.m3u8',
        'http://112.25.48.68/live/program/live/tjwshd/4000000/mnf.m3u8',
        'http://117.169.120.140:8080/live/hdtianjinstv/.m3u8',
    ],
    '东方卫视': [
        'http://bp-resource-dfl.bestv.cn/155/3/video.m3u8',
        'http://74.91.26.218:82/live/dfwshd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226898/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7E0uh4lyjjBCCN7TCq21vSIQ%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '湖南卫视': [
        'http://hlsal-ldvt.qing.mgtv.com/nn_live/nn_x64/Y2RuZXhfaWQ9YWxfaGxzX2xkdnQmZT02OTE0NjA0JnY9MSZpZD1ITldTWkdTVCZzPTcwN2RiYTc2YzJjNmJmMTQ4MmUyZGYzOWU2NWM3YWFi/HNWSZGST.m3u8',
        'http://74.91.26.218:82/live/hnwshd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226509/1.m3u8?zoneoffset=480&servicetype=1&icpid=&limitflux=-1&limitdur=-1&tenantId=8601&accountinfo=%7E%7EV2.0%7EIGb_ib7rnBX9_sANRKq9sg%7EpK8OKM5JoJWWbLRyLfPcUMkwWZ90MzSI9S9PDltJsYzd0ZGcS3Tkh7syciNKJa-w60mDOdwtDyoNwCx9aRgzHNH9AUREV_qvNJtXHRPzYw0%7EExtInfo9bj61dxzlMXrsixrqcFYPg%3D%3D%3A20221022013542%2C915973%2C119.123.71.209%2C20221022013542%2C10000100000000050000000003873458%2C915973%2C-1%2C0%2C1%2C%2C%2C2%2C%2C%2C%2C2%2C%2C10000100000000060000000007253514_0%2CEND&GuardEncType=2',
    ],
    '浙江卫视': [
        'https://ali-m-l.cztv.com/channels/lantian/channel001/1080p.m3u8',
        'http://74.91.26.218:82/live/zjwshd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226899/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7ETYfTgTra_pUx2cPrgZ_BDw%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '安徽卫视': [
        'http://dspdhls.appcoo.com:8888/live/dspd/index.m3u8',
        'http://74.91.26.218:82/live/anhuitv.m3u8',
        'http://74.91.26.218:82/live/ahhd2.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226943/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7E0QmKQ_slRCwvVVUUfxPVbw%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '河北卫视': [
        'https://event.pull.hebtv.com/live/live101.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221227014/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7ErSGjhI3DMaaAASPrbQJYTg%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '江西卫视': [
        'http://112.27.5.218:9901/tsfile/live/faacts/0138_1.m3u8?key=txiptv&playlive=1&authid=0',
        'http://74.91.26.218:82/live/jxws_hd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226956/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7Ei6ZIpVizXlewg-YfGvH8dA%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '新疆卫视': [
        'http://218.84.12.186:8001/hls/main/playlist.m3u8',
        'http://74.91.26.218:82/live/xinjiangtvhd.m3u8',
        'http://74.91.26.218:82/live/xjws.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221227011/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7ExAUu61iVvo_xYbANWJhgXw%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '福建东南卫视': [
        'http://live.zohi.tv/video/s10001-fztv-3/index.m3u8',
        'http://74.91.26.218:82/live/dongnanws.m3u8',
        'http://74.91.26.218:82/live/fujianwshd.m3u8',
        'http://74.91.26.218:82/live/fujianws1.m3u8',
    ],
    '广东卫视': [
        'https://h5cul1yar48um3t.wcetv.com/hls/gdsatellite.m3u8',
        'http://74.91.26.218:82/live/gdwshd.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226961/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7E2MGyx659D_aaDPP0qt3NgA%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '四川卫视': [
        'http://182.150.115.21:8030/pcgacg/pcgacg_0.m3u8',
        'http://74.91.26.218:82/live/sctv.m3u8',
        'http://39.134.115.163:8080/PLTV/88888910/224/3221225733/index.m3u8',
        'http://183.207.248.71/gitv/live1/SCWS/SCWS',
        'http://223.110.245.145/ott.js.chinamobile.com/PLTV/3/224/3221225814/index.m3u8',
    ],
    '广西卫视': [
        'https://hlscdn.liangtv.cn/live/0234c48e0bc24fe1b41b9999a253e581/1075ee38e04f490690f6a36a16e09c79.m3u8',
        'http://74.91.26.218:82/live/gxhd.m3u8',
        'http://74.91.26.218:82/live/gxws.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221227010/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7EkAhb-89sxdm9fz6-heXCuw%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '黑龙江卫视': [
        'https://idclive.hljtv.com:4430/live/hljws_own.m3u8',
        'http://74.91.26.218:82/live/hljwshd.m3u8',
        'http://223.110.245.161/ott.js.chinamobile.com/PLTV/3/224/3221227492/index.m3u8',
        'http://183.207.248.71/gitv/live1/G_HEILONGJIANG/G_HEILONGJIANG',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226965/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7E7UiKL56-L86ihmTWaZ6csw%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
    '中国教育电视台1频道': [
        'http://74.91.26.218:82/live/cetvhd2.m3u8',
        'http://74.91.26.218:82/live/cetvws2.m3u8',
    ],
    '陕西卫视': [
        'http://112.46.105.20:8009/hls/18/index.m3u8',
        'http://[2409:8087:8:21::18]:6610/otttv.bj.chinamobile.com/PLTV/88888888/224/3221226999/1.m3u8?GuardEncType=2&accountinfo=%7E%7EV2.0%7EsGLKaSqf0wDZMbAjeQtfyw%7EtP4-l0lmSfjwLWEfK_el1vH_mv-s1zo4AQJwdedaVwG9xkuFTDg8J26cwOrNJzn20BErrHdLhuZ9EzLUCD3PMW-OMx4MGteHV2vLeW6BqoY%2CEND',
    ],
}

# 高清台标(CCTV 640x320, 卫视 300x180), 官方海报缺失时兜底
LOGO_MAP = {
    'CCTV1': 'https://live.fanmingming.cn/tv/CCTV1.png',
    'CCTV2': 'https://live.fanmingming.cn/tv/CCTV2.png',
    'CCTV3': 'https://live.fanmingming.cn/tv/CCTV3.png',
    'CCTV4': 'https://live.fanmingming.cn/tv/CCTV4.png',
    'CCTV5': 'https://live.fanmingming.cn/tv/CCTV5.png',
    'CCTV6': 'https://live.fanmingming.cn/tv/CCTV6.png',
    'CCTV7': 'https://live.fanmingming.cn/tv/CCTV7.png',
    'CCTV8': 'https://live.fanmingming.cn/tv/CCTV8.png',
    'CCTV9': 'https://live.fanmingming.cn/tv/CCTV9.png',
    'CCTV10': 'https://live.fanmingming.cn/tv/CCTV10.png',
    'CCTV11': 'https://live.fanmingming.cn/tv/CCTV11.png',
    'CCTV12': 'https://live.fanmingming.cn/tv/CCTV12.png',
    'CCTV13': 'https://live.fanmingming.cn/tv/CCTV13.png',
    'CCTV14': 'https://live.fanmingming.cn/tv/CCTV14.png',
    'CCTV15': 'https://live.fanmingming.cn/tv/CCTV15.png',
    'CCTV16': 'https://live.fanmingming.cn/tv/CCTV16.png',
    'CCTV17': 'https://live.fanmingming.cn/tv/CCTV17.png',
    '北京卫视': 'https://live.fanmingming.cn/tv/北京卫视.png',
    '东方卫视': 'https://live.fanmingming.cn/tv/东方卫视.png',
    '浙江卫视': 'https://live.fanmingming.cn/tv/浙江卫视.png',
    '江苏卫视': 'https://live.fanmingming.cn/tv/江苏卫视.png',
    '广东卫视': 'https://live.fanmingming.cn/tv/广东卫视.png',
    '湖南卫视': 'https://live.fanmingming.cn/tv/湖南卫视.png',
    '深圳卫视': 'https://live.fanmingming.cn/tv/深圳卫视.png',
    '四川卫视': 'https://live.fanmingming.cn/tv/四川卫视.png',
    '天津卫视': 'https://live.fanmingming.cn/tv/天津卫视.png',
    '辽宁卫视': 'https://live.fanmingming.cn/tv/辽宁卫视.png',
    '黑龙江卫视': 'https://live.fanmingming.cn/tv/黑龙江卫视.png',
    '吉林卫视': 'https://live.fanmingming.cn/tv/吉林卫视.png',
    '海南卫视': 'https://live.fanmingming.cn/tv/海南卫视.png',
    '广西卫视': 'https://live.fanmingming.cn/tv/广西卫视.png',
    '河北卫视': 'https://live.fanmingming.cn/tv/河北卫视.png',
    '山东卫视': 'https://live.fanmingming.cn/tv/山东卫视.png',
    '山西卫视': 'https://live.fanmingming.cn/tv/山西卫视.png',
    '湖北卫视': 'https://live.fanmingming.cn/tv/湖北卫视.png',
    '江西卫视': 'https://live.fanmingming.cn/tv/江西卫视.png',
    '重庆卫视': 'https://live.fanmingming.cn/tv/重庆卫视.png',
    '东南卫视': 'https://live.fanmingming.cn/tv/东南卫视.png',
    '陕西卫视': 'https://live.fanmingming.cn/tv/陕西卫视.png',
    '青海卫视': 'https://live.fanmingming.cn/tv/青海卫视.png',
    '安徽卫视': 'https://live.fanmingming.cn/tv/安徽卫视.png',
    '云南卫视': 'https://live.fanmingming.cn/tv/云南卫视.png',
    '贵州卫视': 'https://live.fanmingming.cn/tv/贵州卫视.png',
    '宁夏卫视': 'https://live.fanmingming.cn/tv/宁夏卫视.png',
    '甘肃卫视': 'https://live.fanmingming.cn/tv/甘肃卫视.png',
    '内蒙古卫视': 'https://live.fanmingming.cn/tv/内蒙古卫视.png',
    '新疆卫视': 'https://live.fanmingming.cn/tv/新疆卫视.png',
    '西藏卫视': 'https://live.fanmingming.cn/tv/西藏卫视.png',
    '延边卫视': 'https://live.fanmingming.cn/tv/延边卫视.png',
    '康巴卫视': 'https://live.fanmingming.cn/tv/康巴卫视.png',
    '中国教育1台': 'https://live.fanmingming.cn/tv/中国教育1台.png',
    '中国教育2台': 'https://live.fanmingming.cn/tv/中国教育2台.png',
    '中国教育3台': 'https://live.fanmingming.cn/tv/中国教育3台.png',
    '中国教育4台': 'https://live.fanmingming.cn/tv/中国教育4台.png',
    '浙江公共新闻': 'https://live.fanmingming.cn/tv/浙江新闻.png',
    '浙江国际': 'https://live.fanmingming.cn/tv/浙江国际.png',
    '浙江少儿': 'https://live.fanmingming.cn/tv/浙江少儿.png',
    '浙江教科影视': 'https://live.fanmingming.cn/tv/浙江教科影视.png',
    '之江纪录': 'https://live.fanmingming.cn/tv/之江纪录.png',
    '浙江民生休闲': 'https://live.fanmingming.cn/tv/浙江民生休闲.png',
    '浙江经视': 'https://live.fanmingming.cn/tv/浙江经济生活.png',
    '浙江钱江都市': 'https://live.fanmingming.cn/tv/钱江都市.png',
    '内蒙古综合': 'https://live.fanmingming.cn/tv/内蒙古新闻综合.png',
    '内蒙古经济生活': 'https://live.fanmingming.cn/tv/内蒙古经济生活.png',
    '内蒙古少儿': 'https://live.fanmingming.cn/tv/内蒙古少儿.png',
    '内蒙古文体娱乐': 'https://live.fanmingming.cn/tv/内蒙古文体娱乐.png',
    '内蒙古农牧': 'https://live.fanmingming.cn/tv/内蒙古农牧.png',
    '内蒙古蒙语卫视': 'https://live.fanmingming.cn/tv/内蒙古蒙语卫视.png',
    '内蒙古蒙语文化': 'https://live.fanmingming.cn/tv/内蒙古蒙语文化.png',
    '呼和浩特新闻综合': 'https://live.fanmingming.cn/tv/呼和浩特新闻综合.png',
    '包头新闻综合': 'https://live.fanmingming.cn/tv/包头新闻综合.png',
    '鄂尔多斯新闻综合': 'https://live.fanmingming.cn/tv/鄂尔多斯新闻综合.png',
    '巴彦淖尔新闻综合': 'https://live.fanmingming.cn/tv/巴彦淖尔新闻综合.png',
    '赤峰新闻综合': 'https://live.fanmingming.cn/tv/赤峰新闻综合.png',
    '乌兰察布新闻综合': 'https://live.fanmingming.cn/tv/乌兰察布.png',
    'CGTN': 'https://live.fanmingming.cn/tv/CGTN.png',
    'CGTN纪录': 'https://live.fanmingming.cn/tv/CGTN纪录.png',
    'CGTN俄语': 'https://live.fanmingming.cn/tv/CGTN俄语.png',
    'CGTN法语': 'https://live.fanmingming.cn/tv/CGTN法语.png',
    'CGTN西语': 'https://live.fanmingming.cn/tv/CGTN西语.png',
    'CGTN阿语': 'https://live.fanmingming.cn/tv/CGTN阿语.png',
}


# ==================== 工具 ====================
def _today():
    """北京时间当天 yyyyMMdd(服务器时区可能是 UTC)"""
    return time.strftime('%Y%m%d', time.gmtime(time.time() + 8 * 3600))


def _clean(s):
    if s is None:
        return ''
    if isinstance(s, bytes):
        try:
            return s.decode('utf-8', 'ignore')
        except Exception:
            return ''
    return str(s).strip()


class Spider(Spider):
    name = '咪咕视频'

    searchable = 1
    quickSearch = 1
    filterable = 0
    changeable = 1

    # 可选: 登录后把浏览器里 miguvideo.com 的 cookie 粘进来(仅本人合法账号登录态)
    COOKIE = ''

    def __init__(self):
        super(Spider, self).__init__()
        self._groups = None
        self._groups_ts = 0
        self._ch_cache = {}
        self._info = {}

    # ---------------- 网络 ----------------
    def _get(self, url, headers=None, timeout=15):
        hh = dict(HEADERS)
        if self.COOKIE:
            hh['Cookie'] = self.COOKIE
        if headers:
            hh.update(headers)
        req = urllib.request.Request(url, headers=hh)
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            return r.read()
        except urllib.error.HTTPError as e:
            try:
                return e.read()
            except Exception:
                return b''
        except Exception:
            return b''

    def _get_json(self, url, timeout=15):
        raw = self._get(url, timeout=timeout)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode('utf-8', 'ignore'))
        except Exception:
            try:
                return json.loads(raw)
            except Exception:
                return {}

    # ---------------- 频道数据 ----------------
    def _load_groups(self):
        """分组列表(带 10 分钟缓存)"""
        if self._groups and (time.time() - self._groups_ts) < 600:
            return self._groups
        groups = []
        js = self._get_json(API + '/live/v2/tv-data/' + ROOT_VOMS)
        body = (js or {}).get('body') or {}
        for g in (body.get('liveList') or []):
            name = _clean(g.get('name'))
            vid = _clean(g.get('vomsID'))
            if name and vid and name in KEEP_CATS:
                groups.append((vid, name))
        if not groups:
            groups = list(CATS_FALLBACK)
        else:
            order = {n: i for i, n in enumerate(CAT_ORDER)}
            groups.sort(key=lambda x: (order.get(x[1], 99), x[1]))
        self._groups = groups
        self._groups_ts = time.time()
        # 根分组的 dataList 顺手缓存
        if body.get('dataList'):
            self._ch_cache[ROOT_VOMS] = (time.time(), body.get('dataList'))
        return groups

    def _group_channels(self, voms_id):
        """某分组的频道原始列表(带 10 分钟缓存)"""
        hit = self._ch_cache.get(voms_id)
        if hit and (time.time() - hit[0]) < 600:
            return hit[1]
        js = self._get_json(API + '/live/v2/tv-data/' + voms_id)
        body = (js or {}).get('body') or {}
        lst = body.get('dataList') or []
        if lst:
            self._ch_cache[voms_id] = (time.time(), lst)
        return lst

    def _all_channels(self):
        """全部分组频道(去重, 用于搜索), 并发拉取"""
        groups = self._load_groups()
        out = []
        seen = set()
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(self._group_channels, vid): name for vid, name in groups}
            for f in futs:
                try:
                    lst = f.result()
                except Exception:
                    lst = []
                for it in lst:
                    pid = _clean(it.get('pID'))
                    if not pid or pid in seen:
                        continue
                    seen.add(pid)
                    out.append(it)
        return out

    # ---------------- 字段处理 ----------------
    @staticmethod
    def _pic(it):
        """频道海报: 优先竖版高清剧场照, 且尽量避开 webp(部分 TVBox 不渲染)"""
        h5 = it.get('h5pics') or {}
        pc = it.get('pics') or {}
        cands = []
        for d in (h5, pc):
            for k in ('highResolutionV', 'lowResolutionV', 'highResolutionH', 'lowResolutionH', 'pic'):
                u = _clean(d.get(k))
                if u:
                    cands.append(u.replace('http://', 'https://'))
        if not cands:
            return ''
        for u in cands:
            if not u.lower().split('?')[0].endswith('.webp'):
                return u
        return cands[0]

    @staticmethod
    def _logo(name):
        """频道名 -> 高清台标"""
        n = _clean(name)
        if not n:
            return ''
        if n in LOGO_MAP:
            return LOGO_MAP[n]
        m = re.match(r'^(CCTV)(\d+)(?:\+)?', n.replace(' ', '').upper())
        if m:
            key = m.group(1) + m.group(2)
            if 'PLUS' in n.upper() or '5+' in n:
                key = 'CCTV5+'
            if key in LOGO_MAP:
                return LOGO_MAP[key]
        core = n.replace('频道', '').replace('高清', '')
        if core in LOGO_MAP:
            return LOGO_MAP[core]
        return ''

    def _item(self, it):
        pid = _clean(it.get('pID'))
        name = _clean(it.get('name')) or pid
        pic = self._pic(it) or self._logo(name)
        now = _clean(it.get('nowPlaying'))
        st = _clean(it.get('startTime'))
        et = _clean(it.get('endTime'))
        remark = '\u76f4\u64ad'
        if now:
            remark = '\u6b63\u5728\u64ad: ' + now
        if st and et:
            remark += ' (%s-%s)' % (st, et)
        vod = {
            'vod_id': 'live:' + pid,
            'vod_name': name,
            'vod_pic': pic,
            'vod_remarks': remark,
        }
        self._info[pid] = {'name': name, 'pic': pic}
        return vod

    # ---------------- TVBox 接口 ----------------
    def init(self, extend=''):
        pass

    def getName(self):
        return self.name

    def homeContent(self, filter=False):
        try:
            groups = self._load_groups()
        except Exception:
            groups = list(CATS_FALLBACK)
        cats = [{'type_id': vid, 'type_name': name} for vid, name in groups]
        return {'class': cats, 'list': []}

    def homeVideoContent(self):
        """首页推荐: 央视 + 卫视 合并展示"""
        lst = []
        seen = set()
        try:
            groups = self._load_groups()
        except Exception:
            groups = list(CATS_FALLBACK)
        for vid, _ in groups:
            try:
                raw = self._group_channels(vid)
            except Exception:
                raw = []
            for it in raw:
                pid = _clean(it.get('pID'))
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                lst.append(it)
        items = []
        for it in lst[:PAGE_SIZE]:
            try:
                items.append(self._item(it))
            except Exception:
                continue
        return {'list': items}

    def categoryContent(self, tid, pg=1, filter=False, extend=None):
        tid = _clean(tid) or FALLBACK_VOMS
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        try:
            raw = self._group_channels(tid)
        except Exception:
            raw = []
        if not raw:
            # 分组 ID 失效时退回央视
            try:
                raw = self._group_channels(FALLBACK_VOMS)
            except Exception:
                raw = []
        total = len(raw)
        start = (pg - 1) * PAGE_SIZE
        chunk = raw[start:start + PAGE_SIZE]
        items = []
        for it in chunk:
            try:
                items.append(self._item(it))
            except Exception:
                continue
        pagecount = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        return {'page': pg, 'pagecount': pagecount, 'limit': PAGE_SIZE,
                'total': total, 'list': items}

    def _epg(self, pid):
        """当天节目单 -> [(时间, 节目名)]"""
        try:
            js = self._get_json(API + '/live/v2/tv-programs-data/%s/%s' % (pid, _today()), timeout=12)
            prog = ((js or {}).get('body') or {}).get('program') or []
            out = []
            for p in prog:
                for c in (p.get('content') or []):
                    nm = _clean(c.get('contName'))
                    if not nm:
                        continue
                    st = _clean(c.get('startHours'))
                    et = _clean(c.get('endHours'))
                    out.append(((st + '-' + et).strip('-'), nm))
            return out
        except Exception:
            return []

    def _now_next(self, pid):
        """(频道名, 海报, 正在播, 下一档)"""
        try:
            js = self._get_json(API + '/live/v2/nowAndNextPlaybill/' + pid, timeout=12)
            body = (js or {}).get('body') or {}
            tv = body.get('tvData') or {}
            name = _clean(tv.get('name'))
            pic = ''
            try:
                raw = tv.get('pic')
                if isinstance(raw, str):
                    raw = raw.replace("'", '"')
                d = json.loads(raw) if isinstance(raw, str) else (raw or {})
                cands = [_clean(d.get(k)).replace('http://', 'https://')
                         for k in ('lowResolutionV', 'highResolutionV', 'lowResolutionH', 'highResolutionH')]
                cands = [u for u in cands if u]
                for u in cands:
                    if not u.lower().split('?')[0].endswith('.webp'):
                        pic = u
                        break
                if not pic and cands:
                    pic = cands[0]
            except Exception:
                pic = ''
            now = _clean((body.get('now') or {}).get('contName'))
            nxt = _clean((body.get('next') or {}).get('contName'))
            return name, pic, now, nxt
        except Exception:
            return '', '', '', ''

    def _live_urls(self, name):
        """频道名 -> 公共备用直链"""
        n = _clean(name)
        if not n:
            return []
        if n in LIVE_MAP:
            return list(LIVE_MAP[n])
        u = n.upper().replace(' ', '')
        m = re.match(r'^CCTV(\d+)', u)
        if m:
            key = 'CCTV' + m.group(1)
            if '5+' in u or 'PLUS' in u:
                key = 'CCTV5+'
            if key in LIVE_MAP:
                return list(LIVE_MAP[key])
        core = n.replace('频道', '').replace('高清', '')
        if core in LIVE_MAP:
            return list(LIVE_MAP[core])
        return []

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) else ids
        vid = _clean(vid)
        if not vid:
            return {'list': []}
        pid = vid.split(':', 1)[1] if vid.startswith('live:') else vid
        cache = self._info.get(pid) or {}
        name, pic, now, nxt = self._now_next(pid)
        name = name or cache.get('name') or ('\u9891\u9053' + pid)
        # 列表里的海报(官方 h5 jpg)优先, 详情接口的 webp 只做兜底
        pic = cache.get('pic') or pic or self._logo(name)
        if pic.lower().split('?')[0].endswith('.webp'):
            pic = self._logo(name) or pic
        epg = self._epg(pid)

        lines = []
        urls = []
        # 线路1: 咪咕官方
        lines.append('\u54aa\u5495\u5b98\u65b9')
        urls.append(name + '$live:' + pid)
        # 线路2..: 公共直链备用
        for i, u in enumerate(self._live_urls(name)[:5]):
            lines.append(('\u5907\u7528%d' % (i + 1)))
            urls.append(name + '$' + u)

        content = []
        if now:
            content.append('\u6b63\u5728\u64ad\u653e: ' + now)
        if nxt:
            content.append('\u63a5\u4e0b\u6765: ' + nxt)
        if epg:
            content.append('')
            content.append('\u4eca\u65e5\u8282\u76ee\u5355:')
            for t, nm in epg:
                content.append(('%s  %s' % (t, nm)).strip())
        if not content:
            content.append('\u54aa\u5495\u89c6\u9891\u76f4\u64ad\u9891\u9053: ' + name)

        remark = '\u76f4\u64ad'
        if now:
            remark = '\u6b63\u5728\u64ad: ' + now

        vod = {
            'vod_id': vid,
            'vod_name': name,
            'vod_pic': pic,
            'type_name': '\u76f4\u64ad\u9891\u9053',
            'vod_year': '',
            'vod_area': '\u4e2d\u56fd',
            'vod_remarks': remark,
            'vod_actor': '',
            'vod_director': '',
            'vod_content': '\n'.join(content),
            'vod_play_from': '$$$'.join(lines),
            'vod_play_url': '$$$'.join(urls),
        }
        return {'list': [vod]}

    def searchContent(self, key, quick=False, pg='1'):
        key = _clean(key)
        if not key:
            return {'list': [], 'page': 1, 'pagecount': 1, 'limit': 0, 'total': 0}
        try:
            raw = self._all_channels()
        except Exception:
            raw = []
        k = key.lower().replace(' ', '')
        items = []
        for it in raw:
            nm = _clean(it.get('name'))
            if not nm:
                continue
            if k in nm.lower().replace(' ', '') or k in _clean(it.get('nowPlaying')).lower():
                try:
                    items.append(self._item(it))
                except Exception:
                    continue
        return {'list': items, 'page': 1, 'pagecount': 1,
                'limit': len(items), 'total': len(items)}

    # ---------------- 播放 ----------------
    @staticmethod
    def _ddcalcu(url):
        """咪咕 PC 播放器 ddCalcu 校验参数(站点 JS 还原), 不需要时原样返回"""
        try:
            if 'ddCalcu=' in url:
                return url
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            for k in ('timestamp', 'ProgramID', 'Channel_ID', 'puData'):
                if k not in q:
                    return url
            r = q['timestamp'][0]
            n = q['ProgramID'][0]
            a = q['Channel_ID'][0]
            o = q['puData'][0]
            t = 'eeeeeeeee'
            u = t[2] or 'e'
            ll = (r[6] if len(r) > 6 else '') or 't'
            c = (n[2] if len(n) > 2 else '') or 'c'
            f = (a[len(a) - 4] if len(a) >= 4 else '') or 'n'
            h = []
            for p in range(0, int(len(o) / 2)):
                h.append(o[len(o) - p - 1])
                if p < len(o) - p - 1:
                    h.append(o[p])
                x = {1: u, 2: ll, 3: c, 4: f}
                h.append(x[p]) if p in (1, 2, 3, 4) else ''
            return url + '&ddCalcu=' + ''.join(h)
        except Exception:
            return url

    @staticmethod
    def _pick_url(js):
        """从 playurl 响应里取 m3u8(兼容 urlInfo/urlInfos/mediaFiles 多种结构)"""
        body = (js or {}).get('body') or {}
        cands = []

        def walk(obj, depth=0):
            if depth > 6:
                return
            if isinstance(obj, dict):
                for k in ('url', 'playUrl', 'mediaUrl', 'm3u8Url'):
                    v = obj.get(k)
                    if isinstance(v, str) and v.startswith('http'):
                        cands.append(v)
                for v in obj.values():
                    if isinstance(v, (dict, list)):
                        walk(v, depth + 1)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v, depth + 1)

        walk(body)
        if not cands:
            return ''
        m3u8 = [u for u in cands if '.m3u8' in u.lower()]
        if m3u8:
            return m3u8[0]
        bad = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.css', '.js', '.ico')
        for u in cands:
            if not any(u.lower().split('?')[0].endswith(b) for b in bad):
                return u
        return ''

    def _migu_play(self, pid):
        """(url, 说明): 官方 playurl 取 m3u8, 多种清晰度/多端点依次试"""
        last = ''
        attempts = [('/playurl/v3/play/playurl', r) for r in ('3', '4', '2', '1')]
        attempts += [('/playurl/v1/play/playurlh5', '3'), ('/playurl/v1/play/playurlh5', '2')]
        for ep, rate in attempts:
            ts = str(int(time.time() * 1000))
            url = (API + '%s?contId=%s&rateType=%s&clientId=1&timestamp=%s'
                   % (ep, pid, rate, ts))
            js = self._get_json(url, timeout=15)
            code = _clean((js or {}).get('code'))
            u = self._pick_url(js) if js else ''
            if u:
                return self._ddcalcu(u), ep.rsplit('/', 1)[-1] + ' rate' + rate
            last = _clean((js or {}).get('message')) or _clean((js or {}).get('rid')) or code
            if code.startswith('403') or 'OFFLINE' in last.upper():
                # 版权/地区/下线类错误, 换清晰度或端点也没用
                break
        return '', last

    def playerContent(self, flag, id, vipFlags=None):
        raw_id = _clean(id)
        hdr = {'User-Agent': UA_MOBILE, 'Referer': 'https://m.miguvideo.com/'}
        if not raw_id:
            return {'parse': 0, 'url': '', 'header': hdr}
        # 备用线路本身就是直链
        if raw_id.startswith('http'):
            return {'parse': 0, 'url': raw_id, 'header': hdr}

        pid = raw_id.split(':', 1)[1] if raw_id.startswith('live:') else raw_id
        url, note = self._migu_play(pid)
        if url:
            return {'parse': 0, 'url': url, 'header': hdr}

        # 官方拿不到(常见: 境外 IP 版权限制) -> 公共直链兜底
        name = (self._info.get(pid) or {}).get('name') or ''
        if not name:
            name = self._now_next(pid)[0]
        for u in self._live_urls(name):
            return {'parse': 0, 'url': u, 'header': hdr}

        # 都没有: 交回官方直播页(由壳子尝试嗅探)
        return {'parse': 1, 'url': 'https://m.miguvideo.com/mgs/msite/prd/liveDetail/index.html?pID=' + pid,
                'header': hdr}

    def isVideoFormat(self, url):
        u = _clean(url)
        return u.endswith('.m3u8') or '.m3u8?' in u

    def manualVideoCheck(self):
        return False
