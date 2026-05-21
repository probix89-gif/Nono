"""
╔══════════════════════════════════════════════════════════════════╗
║   DORK PARSER BOT v22.0 — YAHOO UNBLOCKABLE EDITION              ║
║   • 10-layer evasion stack for Yahoo (TLS+JA3+cookies+behavior)  ║
║   • Identity pool: warmed browsing personas with cookie state    ║
║   • Mirror health tracking (19 Yahoo regional endpoints)         ║
║   • Human-timing engine with Gaussian + bimodal delays           ║
║   • Soft/hard block diagnosis → identity rotation                ║
║   • All prior v21 features preserved                             ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import random
import re
import os
import time
import hashlib
import logging
import tempfile
import itertools
import collections as _collections
from collections import deque, defaultdict, Counter
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote, quote_plus, urlencode

from curl_cffi.requests import AsyncSession
from curl_cffi import CurlError

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

load_dotenv(override=False)

# ─── LOGGING ─────────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
log_file = f"logs/bot_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────
BOT_TOKEN             = os.environ.get("BOT_TOKEN", "")
N_CHUNKS              = int(os.environ.get("N_CHUNKS", 4))
WORKERS_PER_CHUNK     = int(os.environ.get("WORKERS_PER_CHUNK", 25))
MAX_WORKERS_PER_CHUNK = 60
MIN_DELAY             = float(os.environ.get("MIN_DELAY", 0.2))
MAX_DELAY             = float(os.environ.get("MAX_DELAY", 0.6))
FAST_MIN_DELAY        = 0.05
FAST_MAX_DELAY        = 0.15
FAST_STREAK_THRESHOLD = 2
MAX_RESULTS           = int(os.environ.get("MAX_RESULTS", 10))
TOR_PROXY             = os.environ.get("TOR_PROXY", "socks5://127.0.0.1:9050")
OUTPUT_DIR            = Path("results")
OUTPUT_DIR.mkdir(exist_ok=True)

ENGINES   = ["bing", "yahoo", "duckduckgo"]
MAX_PAGES = 70

WORKER_FETCH_TIMEOUT = 60
JOB_TIMEOUT          = 30 * 60
MAX_RETRIES          = 2
CHUNK_STALL_TIMEOUT  = 30.0
EMPTY_RATE_SLOWDOWN  = 0.60
EMPTY_RATE_RECOVER   = 0.40
CHUNK_STAGGER_DELAY  = (0.1, 0.4)

# Yahoo Unblockable Engine config
YAHOO_UB_POOL_SIZE        = int(os.environ.get("YAHOO_UB_POOL_SIZE", 12))
YAHOO_UB_MAX_ATTEMPTS     = 5
YAHOO_UB_IDENTITY_REQUESTS = (35, 60)   # burn identity after N requests
YAHOO_UB_IDENTITY_AGE     = (240, 420)  # burn identity after N seconds

# XTREAM MODE CONFIG
XTREAM_WORKERS_PER_CHUNK   = 50
XTREAM_CHUNKS              = 8
XTREAM_MIN_DELAY           = 0.01
XTREAM_MAX_DELAY           = 0.05
XTREAM_TIMEOUT             = 20
XTREAM_MAX_RETRIES         = 2
XTREAM_TARGET_RPS          = 1000
XTREAM_PAGES_PER_DORK      = 8
XTREAM_SESSION_POOL_SIZE   = 200
XTREAM_SESSION_MAX_USES    = 30
XTREAM_SESSION_MAX_AGE     = 240
XTREAM_POOL_BATCH_SIZE     = 20
XTREAM_CAPTCHA_RATE_LIMIT  = 0.25
XTREAM_PRESEED_COOKIES     = True

DEFAULT_SESSION = {
    "workers":       WORKERS_PER_CHUNK,
    "chunks":        N_CHUNKS,
    "engines":       list(ENGINES),
    "max_results":   MAX_RESULTS,
    "pages":         [1],
    "tor":           False,
    "min_score":     30,
    "xtream":        False,
    "xtream_engine": "yahoo",
    "yahoo_ub":      True,   # NEW: unblockable Yahoo by default
}

user_sessions:   dict = {}
active_jobs:     dict = {}
active_stop_evs: dict = {}


# ══════════════════════════════════════════════════════════════════════════════
# ─── TLS FINGERPRINT ROTATION (22 profiles) ──────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

_LANG_POOL = [
    "en-US,en;q=0.9",
    "en-US,en;q=0.9,es;q=0.8",
    "en-GB,en;q=0.9",
    "en-CA,en;q=0.9,fr-CA;q=0.8",
    "en-AU,en;q=0.9",
    "en-US,en;q=0.8,de;q=0.7",
    "en-US,en;q=0.9,fr;q=0.8",
    "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "es-ES,es;q=0.9,en;q=0.8",
    "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "en-IN,en-GB;q=0.9,en;q=0.8",
    "en-SG,en;q=0.9",
    "en-NZ,en;q=0.9",
]

_ACCEPT_CHROME  = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
_ACCEPT_FIREFOX = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
_ACCEPT_SAFARI  = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
_ACCEPT_EDGE    = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"

TLS_PROFILES = [
    {"impersonate":"chrome110","browser":"chrome","version":110,
     "ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
     "sec_ch_ua":'"Chromium";v="110", "Not A(Brand";v="24", "Google Chrome";v="110"',
     "platform":'"Windows"',"accept":_ACCEPT_CHROME,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br"},
    {"impersonate":"chrome116","browser":"chrome","version":116,
     "ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
     "sec_ch_ua":'"Not)A;Brand";v="24", "Chromium";v="116", "Google Chrome";v="116"',
     "platform":'"Windows"',"accept":_ACCEPT_CHROME,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br"},
    {"impersonate":"chrome119","browser":"chrome","version":119,
     "ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
     "sec_ch_ua":'"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
     "platform":'"Windows"',"accept":_ACCEPT_CHROME,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br"},
    {"impersonate":"chrome120","browser":"chrome","version":120,
     "ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
     "sec_ch_ua":'"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
     "platform":'"Windows"',"accept":_ACCEPT_CHROME,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br"},
    {"impersonate":"chrome123","browser":"chrome","version":123,
     "ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
     "sec_ch_ua":'"Google Chrome";v="123", "Not:A-Brand";v="8", "Chromium";v="123"',
     "platform":'"Windows"',"accept":_ACCEPT_CHROME,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br, zstd"},
    {"impersonate":"chrome124","browser":"chrome","version":124,
     "ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
     "sec_ch_ua":'"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
     "platform":'"Windows"',"accept":_ACCEPT_CHROME,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br, zstd","priority":"u=0, i"},
    {"impersonate":"chrome126","browser":"chrome","version":126,
     "ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
     "sec_ch_ua":'"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="8"',
     "platform":'"Windows"',"accept":_ACCEPT_CHROME,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br, zstd","priority":"u=0, i"},
    {"impersonate":"chrome131","browser":"chrome","version":131,
     "ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
     "sec_ch_ua":'"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
     "platform":'"Windows"',"accept":_ACCEPT_CHROME,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br, zstd","priority":"u=0, i"},
    {"impersonate":"chrome131","browser":"chrome","version":131,
     "ua":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
     "sec_ch_ua":'"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
     "platform":'"macOS"',"accept":_ACCEPT_CHROME,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br, zstd","priority":"u=0, i"},
    {"impersonate":"chrome131","browser":"chrome","version":131,
     "ua":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
     "sec_ch_ua":'"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
     "platform":'"Linux"',"accept":_ACCEPT_CHROME,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br, zstd","priority":"u=0, i"},
    {"impersonate":"chrome120","browser":"chrome","version":120,
     "ua":"Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
     "sec_ch_ua":'"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
     "platform":'"macOS"',"accept":_ACCEPT_CHROME,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br"},
    {"impersonate":"chrome131","browser":"chrome","version":131,
     "ua":"Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
     "sec_ch_ua":'"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
     "platform":'"Android"',"accept":_ACCEPT_CHROME,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br, zstd","mobile":True,"priority":"u=0, i"},
    {"impersonate":"chrome120","browser":"chrome","version":120,
     "ua":"Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
     "sec_ch_ua":'"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
     "platform":'"Android"',"accept":_ACCEPT_CHROME,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br","mobile":True},
    {"impersonate":"edge99","browser":"edge","version":99,
     "ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.84 Safari/537.36 Edg/99.0.1150.55",
     "sec_ch_ua":'"Microsoft Edge";v="99", "Chromium";v="99", "Not;A=Brand";v="24"',
     "platform":'"Windows"',"accept":_ACCEPT_EDGE,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br"},
    {"impersonate":"edge101","browser":"edge","version":101,
     "ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.4951.54 Safari/537.36 Edg/101.0.1210.39",
     "sec_ch_ua":'"Microsoft Edge";v="101", "Chromium";v="101", "Not;A=Brand";v="24"',
     "platform":'"Windows"',"accept":_ACCEPT_EDGE,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br","priority":"u=0, i"},
    {"impersonate":"safari15_5","browser":"safari","version":155,
     "ua":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.5 Safari/605.1.15",
     "sec_ch_ua":None,"platform":'"macOS"',"accept":_ACCEPT_SAFARI,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br"},
    {"impersonate":"safari17_0","browser":"safari","version":170,
     "ua":"Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
     "sec_ch_ua":None,"platform":'"macOS"',"accept":_ACCEPT_SAFARI,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br"},
    {"impersonate":"safari18_0","browser":"safari","version":180,
     "ua":"Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
     "sec_ch_ua":None,"platform":'"macOS"',"accept":_ACCEPT_SAFARI,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br"},
    {"impersonate":"safari17_2_ios","browser":"safari","version":172,
     "ua":"Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
     "sec_ch_ua":None,"platform":'"iOS"',"accept":_ACCEPT_SAFARI,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br","mobile":True},
    {"impersonate":"safari18_0","browser":"safari","version":180,
     "ua":"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
     "sec_ch_ua":None,"platform":'"iOS"',"accept":_ACCEPT_SAFARI,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br","mobile":True},
    {"impersonate":"firefox133","browser":"firefox","version":133,
     "ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
     "sec_ch_ua":None,"platform":None,"accept":_ACCEPT_FIREFOX,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br, zstd","firefox":True},
    {"impersonate":"firefox133","browser":"firefox","version":133,
     "ua":"Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
     "sec_ch_ua":None,"platform":None,"accept":_ACCEPT_FIREFOX,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br, zstd","firefox":True},
    {"impersonate":"firefox133","browser":"firefox","version":133,
     "ua":"Mozilla/5.0 (Macintosh; Intel Mac OS X 14.2; rv:133.0) Gecko/20100101 Firefox/133.0",
     "sec_ch_ua":None,"platform":None,"accept":_ACCEPT_FIREFOX,"accept_lang":random.choice(_LANG_POOL),"accept_enc":"gzip, deflate, br, zstd","firefox":True},
]

_tls_cycle = itertools.cycle(TLS_PROFILES)
_tls_lock  = asyncio.Lock()
_tls_last  = []
_TLS_ANTI_REPEAT = 3


def get_tls_profile(strategy="random"):
    global _tls_last
    if strategy == "round":
        return next(_tls_cycle)
    if strategy == "weighted":
        r = random.random()
        if r < 0.62:
            pool = [p for p in TLS_PROFILES if p["browser"]=="chrome" and not p.get("mobile")]
        elif r < 0.72:
            pool = [p for p in TLS_PROFILES if p["browser"]=="firefox"]
        elif r < 0.80:
            pool = [p for p in TLS_PROFILES if p["browser"]=="edge"]
        elif r < 0.90:
            pool = [p for p in TLS_PROFILES if p["browser"]=="safari" and not p.get("mobile")]
        else:
            pool = [p for p in TLS_PROFILES if p.get("mobile")]
        candidates = pool or TLS_PROFILES
    else:
        candidates = TLS_PROFILES
    recent = set(_tls_last[-_TLS_ANTI_REPEAT:])
    filtered = [p for p in candidates if p["impersonate"] not in recent]
    chosen = random.choice(filtered if filtered else candidates)
    _tls_last.append(chosen["impersonate"])
    if len(_tls_last) > _TLS_ANTI_REPEAT * 2:
        _tls_last = _tls_last[-_TLS_ANTI_REPEAT:]
    return chosen


def build_headers_from_profile(profile, referer=None, origin=None, context="navigate"):
    is_firefox = profile.get("firefox", False)
    is_mobile  = profile.get("mobile", False)
    version    = profile.get("version", 120)
    browser    = profile.get("browser", "chrome")
    cache_ctrl = random.choice(["max-age=0","max-age=0","no-cache","max-age=0"])
    if is_firefox:
        h = {
            "User-Agent": profile["ua"],
            "Accept": profile.get("accept", _ACCEPT_FIREFOX),
            "Accept-Language": profile["accept_lang"],
            "Accept-Encoding": profile.get("accept_enc","gzip, deflate, br, zstd"),
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site" if referer else "none",
            "Sec-Fetch-User": "?1",
            "Te": "trailers",
            "Cache-Control": cache_ctrl,
        }
    else:
        h = {
            "User-Agent": profile["ua"],
            "Accept": profile.get("accept", _ACCEPT_CHROME),
            "Accept-Language": profile["accept_lang"],
            "Accept-Encoding": profile.get("accept_enc","gzip, deflate, br"),
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": cache_ctrl,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin" if referer else "none",
            "Sec-Fetch-User": "?1",
        }
        if version >= 101 and browser in ("chrome","edge") and "priority" in profile:
            h["Priority"] = profile["priority"]
    if profile.get("sec_ch_ua"):
        h["Sec-Ch-Ua"]          = profile["sec_ch_ua"]
        h["Sec-Ch-Ua-Mobile"]   = "?1" if is_mobile else "?0"
        h["Sec-Ch-Ua-Platform"] = profile["platform"]
        if version >= 120 and random.random() < 0.40:
            h["Sec-Ch-Ua-Arch"]              = '"x86"' if not is_mobile else '"arm"'
            h["Sec-Ch-Ua-Bitness"]           = '"64"'
            h["Sec-Ch-Ua-Full-Version-List"] = profile["sec_ch_ua"]
    if referer: h["Referer"] = referer
    if origin:  h["Origin"]  = origin
    dnt_prob = 0.25 if is_firefox else 0.05
    if random.random() < dnt_prob:
        h["DNT"] = "1"
    if random.random() < 0.02:
        h["Save-Data"] = "on"
    return h


# ══════════════════════════════════════════════════════════════════════════════
# ─── ANTI-BLOCK SYSTEM ───────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class DomainCircuitBreaker:
    WINDOW        = 20
    THRESHOLD     = 0.55
    COOLDOWN_BASE = 45.0
    COOLDOWN_MAX  = 480.0

    def __init__(self):
        self._lock     = asyncio.Lock()
        self._history  = {}
        self._state    = {}
        self._until    = {}
        self._cooldown = {}

    def _domain(self, url):
        try: return urlparse(url).netloc.lower()
        except Exception: return url

    async def check(self, url):
        domain = self._domain(url)
        async with self._lock:
            state = self._state.get(domain, "closed")
            if state == "closed": return 0.0
            if state == "open":
                remaining = self._until.get(domain, 0) - time.time()
                if remaining > 0: return remaining
                self._state[domain] = "half"
                return 0.0
            return 2.0

    async def record(self, url, blocked):
        domain = self._domain(url)
        async with self._lock:
            if domain not in self._history:
                self._history[domain]  = _collections.deque(maxlen=self.WINDOW)
                self._state[domain]    = "closed"
                self._cooldown[domain] = self.COOLDOWN_BASE
            hist  = self._history[domain]
            state = self._state.get(domain, "closed")
            hist.append(1 if blocked else 0)
            if state == "half":
                if blocked:
                    cd = min(self._cooldown[domain] * 2, self.COOLDOWN_MAX)
                    self._cooldown[domain] = cd
                    self._state[domain]    = "open"
                    self._until[domain]    = time.time() + cd
                else:
                    self._state[domain]    = "closed"
                    self._cooldown[domain] = self.COOLDOWN_BASE
                    hist.clear()
                return
            if len(hist) >= self.WINDOW // 2:
                rate = sum(hist) / len(hist)
                if rate >= self.THRESHOLD and state == "closed":
                    cd = self._cooldown[domain]
                    self._state[domain] = "open"
                    self._until[domain] = time.time() + cd
                    log.warning(f"[CB] {domain}: block rate {rate:.0%} → OPEN for {cd:.0f}s")


circuit_breaker = DomainCircuitBreaker()


def humanize_delay(base, sigma_ratio=0.30, distraction_prob=0.04, distraction_extra=3.0):
    delay = random.gauss(base, base * sigma_ratio)
    delay = max(base * 0.2, min(base * 4.0, delay))
    if random.random() < distraction_prob:
        delay += random.uniform(distraction_extra, distraction_extra * 3)
    return delay


async def async_humanize_sleep(base, **kw):
    await asyncio.sleep(humanize_delay(base, **kw))


_COMMON_ISP_RANGES = [
    ("24.0.0.0","24.255.255.255"),
    ("71.0.0.0","71.127.255.255"),
    ("98.0.0.0","98.255.255.255"),
    ("173.0.0.0","173.79.255.255"),
    ("67.40.0.0","67.63.255.255"),
    ("50.0.0.0","50.127.255.255"),
    ("86.0.0.0","86.255.255.255"),
    ("82.0.0.0","82.127.255.255"),
    ("90.0.0.0","90.127.255.255"),
]

def _random_public_ip():
    r1, r2 = random.choice(_COMMON_ISP_RANGES)
    p1 = [int(x) for x in r1.split(".")]
    p2 = [int(x) for x in r2.split(".")]
    return ".".join(str(random.randint(a,b)) for a,b in zip(p1,p2))

def spoof_xff_headers(h, probability=0.35):
    if random.random() < probability:
        ip = _random_public_ip()
        h["X-Forwarded-For"] = ip
        if random.random() < 0.5:
            h["X-Real-Ip"] = ip
    return h


# ══════════════════════════════════════════════════════════════════════════════
# ─── PROXY SYSTEM ────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

PROXY_ENABLED = os.environ.get("PROXY_ENABLED","true").lower() not in ("false","0","no")
PROXY_PROBE_ORDER = ("socks5","socks4","http","https")
PROXY_TEST_URLS = [
    "https://api.ipify.org?format=json",
    "https://httpbin.org/ip",
    "https://ifconfig.me/ip",
    "http://ip-api.com/json/",
]
PROXY_CHECK_TIMEOUT     = 10
PROXY_CHECK_CONCURRENCY = 30
PROXY_HEALTH_INTERVAL   = 600
PROXY_MAX_FAILS         = 3

_proxy_pool_lock = asyncio.Lock()
_proxy_pool      = []
_proxy_health_task = None

_IP_PORT_RE      = re.compile(r"^([\w\-\.]+):(\d{1,5})$")
_IP_PORT_AUTH_RE = re.compile(r"^([\w\-\.]+):(\d{1,5}):([^:\s]+):([^:\s]+)$")
_URL_RE          = re.compile(
    r"^(https?|socks4a?|socks5h?)://(?:([^:@/\s]+):([^:@/\s]+)@)?([\w\-\.]+):(\d{1,5})/?$",
    re.IGNORECASE)


def parse_proxy_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = _URL_RE.match(line)
    if m:
        scheme, user, pwd, host, port = m.groups()
        scheme = scheme.lower()
        if scheme == "socks5h": scheme = "socks5"
        elif scheme == "socks4a": scheme = "socks4"
        return {"host":host,"port":int(port),"user":user or None,"pass":pwd or None,
                "protocol":scheme,"url":_build_proxy_url(scheme,host,int(port),user,pwd),
                "alive":False,"latency":None,"last_check":0.0,"fail_count":0,"explicit":True}
    m = _IP_PORT_AUTH_RE.match(line)
    if m:
        host, port, user, pwd = m.groups()
        return {"host":host,"port":int(port),"user":user,"pass":pwd,"protocol":None,"url":None,
                "alive":False,"latency":None,"last_check":0.0,"fail_count":0,"explicit":False}
    m = _IP_PORT_RE.match(line)
    if m:
        host, port = m.groups()
        return {"host":host,"port":int(port),"user":None,"pass":None,"protocol":None,"url":None,
                "alive":False,"latency":None,"last_check":0.0,"fail_count":0,"explicit":False}
    return None


def _build_proxy_url(scheme, host, port, user, pwd):
    auth = f"{user}:{pwd}@" if user and pwd else ""
    return f"{scheme}://{auth}{host}:{port}"


def proxy_key(p): return f"{p['host']}:{p['port']}:{p.get('user') or ''}"
def proxy_display(p):
    proto = p["protocol"].upper() if p["protocol"] else "?"
    auth  = " 🔐" if p.get("user") else ""
    return f"[{proto:6s}] {p['host']}:{p['port']}{auth}"


async def _probe_single(host, port, user, pwd, scheme):
    proxy_url = _build_proxy_url(scheme, host, port, user, pwd)
    test_url  = random.choice(PROXY_TEST_URLS)
    sess = AsyncSession(impersonate="chrome120", verify=False,
                        timeout=PROXY_CHECK_TIMEOUT, proxy=proxy_url)
    try:
        t0 = time.monotonic()
        resp = await sess.get(test_url, timeout=PROXY_CHECK_TIMEOUT)
        latency = (time.monotonic() - t0) * 1000.0
        if resp.status_code != 200:
            return False, None, None
        text = resp.text.strip()
        ext_ip = None
        try:
            import json as _json
            data = _json.loads(text)
            ext_ip = data.get("ip") or data.get("origin") or data.get("query")
        except Exception:
            if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", text):
                ext_ip = text
        if not ext_ip and len(text) < 5:
            return False, None, None
        return True, latency, ext_ip
    except (CurlError, asyncio.TimeoutError, Exception):
        return False, None, None
    finally:
        try: await sess.close()
        except Exception: pass


async def detect_proxy_protocol(p):
    host, port = p["host"], p["port"]
    user, pwd  = p.get("user"), p.get("pass")
    if p.get("explicit") and p.get("protocol"):
        ok, latency, _ = await _probe_single(host, port, user, pwd, p["protocol"])
        if ok:
            p.update({"alive":True,"latency":latency,"last_check":time.time(),"fail_count":0})
            return True
        p.update({"alive":False,"last_check":time.time()})
        p["fail_count"] = p.get("fail_count",0)+1
        return False
    for scheme in PROXY_PROBE_ORDER:
        ok, latency, _ = await _probe_single(host, port, user, pwd, scheme)
        if ok:
            p["protocol"] = scheme
            p["url"]      = _build_proxy_url(scheme, host, port, user, pwd)
            p.update({"alive":True,"latency":latency,"last_check":time.time(),"fail_count":0})
            log.info(f"[PROXY] {scheme.upper()} {host}:{port} ({latency:.0f}ms)")
            return True
    p.update({"alive":False,"protocol":None,"last_check":time.time()})
    p["fail_count"] = p.get("fail_count",0)+1
    return False


async def check_proxies_bulk(proxies, concurrency=PROXY_CHECK_CONCURRENCY, progress_cb=None):
    sem = asyncio.Semaphore(concurrency)
    done = [0]; total = len(proxies); alive = 0
    async def _one(p):
        nonlocal alive
        async with sem:
            ok = await detect_proxy_protocol(p)
            if ok: alive += 1
            done[0] += 1
            if progress_cb and done[0] % 5 == 0:
                try: await progress_cb(done[0], total, alive)
                except Exception: pass
    await asyncio.gather(*[_one(p) for p in proxies], return_exceptions=True)
    return alive, total - alive


def _persist_proxies():
    try:
        with open("proxies.txt","w",encoding="utf-8") as f:
            f.write(f"# Proxy pool — v22\n# Updated: {datetime.now()}\n# Total: {len(_proxy_pool)}\n\n")
            for p in _proxy_pool:
                line = p["url"] if p.get("url") else (
                    f"{p['host']}:{p['port']}:{p['user']}:{p['pass']}" if p.get("user")
                    else f"{p['host']}:{p['port']}")
                tag = f"  # alive={'Y' if p['alive'] else 'N'} latency={int(p['latency']) if p['latency'] else 'NA'}ms"
                f.write(line + tag + "\n")
    except Exception as exc:
        log.warning(f"[PROXY] persist fail: {exc}")


def _load_proxies():
    proxies = []
    env_list = os.environ.get("PROXY_LIST","").strip()
    if env_list:
        for line in [p.strip() for p in env_list.split(",") if p.strip()]:
            p = parse_proxy_line(line)
            if p: proxies.append(p)
        return proxies
    proxy_file = Path("proxies.txt")
    if proxy_file.exists():
        with open(proxy_file, encoding="utf-8") as f:
            for line in f:
                clean = line.split("#",1)[0].strip()
                if not clean: continue
                p = parse_proxy_line(clean)
                if p: proxies.append(p)
    return proxies


_proxy_pool = _load_proxies()


def get_random_proxy_url(exclude_url=None, alive_only=True):
    if not PROXY_ENABLED or not _proxy_pool:
        return None
    cands = [p["url"] for p in _proxy_pool
             if p.get("url") and (not alive_only or p["alive"]) and p["url"] != exclude_url]
    if not cands:
        cands = [p["url"] for p in _proxy_pool if p.get("url") and p["url"] != exclude_url]
    return random.choice(cands) if cands else None


def _is_proxy_error(exc):
    msg = str(exc).lower()
    return any(kw in msg for kw in (
        "proxy","tunnel","407","socks","authentication","connection refused",
        "network unreachable","no route to host","could not connect to proxy",
        "unable to connect to proxy","recv failure","ssl handshake","timed out"))


async def _proxy_health_loop():
    while True:
        await asyncio.sleep(PROXY_HEALTH_INTERVAL)
        if not _proxy_pool: continue
        async with _proxy_pool_lock:
            snapshot = list(_proxy_pool)
        try:
            alive, dead = await check_proxies_bulk(snapshot)
            log.info(f"[HEALTH] alive={alive} dead={dead}")
            async with _proxy_pool_lock:
                _proxy_pool[:] = [p for p in _proxy_pool if p.get("fail_count",0) < PROXY_MAX_FAILS]
                _persist_proxies()
        except Exception as exc:
            log.error(f"[HEALTH] {exc}")


def start_proxy_health_monitor():
    global _proxy_health_task
    if _proxy_health_task is None or _proxy_health_task.done():
        _proxy_health_task = asyncio.create_task(_proxy_health_loop())


# ══════════════════════════════════════════════════════════════════════════════
# ─── DORK PARSER ─────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

KNOWN_OPERATORS = {
    "inurl","intitle","intext","inanchor","site","filetype","ext",
    "cache","link","related","info","allinurl","allintitle","allintext",
}

ENGINE_OPERATOR_SUPPORT = {
    "bing":       {"inurl","intitle","site","filetype","ext","ip","contains","inbody"},
    "yahoo":      {"inurl","intitle","site","filetype","ext"},
    "duckduckgo": {"inurl","intitle","site","filetype","ext","intext"},
    "google":     KNOWN_OPERATORS,
}

ENGINE_OPERATOR_ALIAS = {
    "bing":  {"intext":"inbody"},
    "yahoo": {"intext":None,"inanchor":None},
}


class DorkToken:
    __slots__ = ("kind","op","value","negate","quoted")
    def __init__(self, kind, op, value, negate=False, quoted=False):
        self.kind=kind; self.op=op; self.value=value; self.negate=negate; self.quoted=quoted
    def __repr__(self):
        n = "-" if self.negate else ""; q = '"' if self.quoted else ""
        return f"{n}{self.op}:{q}{self.value}{q}" if self.op else f"{n}{q}{self.value}{q}"


class DorkAST:
    def __init__(self, tokens, raw):
        self.tokens = tokens; self.raw = raw
    @property
    def operators(self):
        out = {}
        for t in self.tokens:
            if t.op: out.setdefault(t.op, []).append(t.value)
        return out
    @property
    def free_terms(self):
        return [t.value for t in self.tokens if not t.op and t.kind in ("term","phrase")]
    def __repr__(self): return " ".join(repr(t) for t in self.tokens)


_DORK_TOKEN_RE = re.compile(
    r"""(?P<neg>-)?(?:(?P<op>[a-zA-Z]+):)?(?:"(?P<phrase>[^"]+)"|\((?P<group>[^)]+)\)|(?P<term>[^\s"()]+))""",
    re.VERBOSE)


def parse_dork(dork):
    tokens = []
    for m in _DORK_TOKEN_RE.finditer(dork.strip()):
        neg = bool(m.group("neg")); op = m.group("op")
        phrase = m.group("phrase"); group = m.group("group"); term = m.group("term")
        if op: op = op.lower()
        if phrase is not None: tokens.append(DorkToken("phrase",op,phrase,negate=neg,quoted=True))
        elif group is not None: tokens.append(DorkToken("group",op,group,negate=neg))
        elif term is not None:
            if term.upper()=="OR" or term=="|":
                tokens.append(DorkToken("or",None,"OR"))
            else:
                tokens.append(DorkToken("term",op,term,negate=neg))
    return DorkAST(tokens, dork.strip())


def validate_dork(dork):
    if not dork or not dork.strip(): return False,"Empty dork"
    if dork.count('"')%2!=0: return False,"Unbalanced double-quotes"
    if dork.count("(")!=dork.count(")"): return False,"Unbalanced parentheses"
    ast = parse_dork(dork)
    if not ast.tokens: return False,"No tokens parsed"
    unknown = [t.op for t in ast.tokens if t.op and t.op not in KNOWN_OPERATORS]
    if unknown: return True,f"OK (unknown operators: {', '.join(set(unknown))})"
    if not any(t.kind in ("term","phrase","group") for t in ast.tokens):
        return False,"No search terms"
    return True,"OK"


def normalize_dork(dork):
    ast = parse_dork(dork); seen=set(); out=[]
    for t in ast.tokens:
        key = (t.op, t.value.lower(), t.negate, t.quoted)
        if key in seen: continue
        seen.add(key); out.append(repr(t))
    return " ".join(out)


def translate_dork(dork, engine):
    if engine not in ENGINE_OPERATOR_SUPPORT: return dork
    supported = ENGINE_OPERATOR_SUPPORT[engine]
    aliases   = ENGINE_OPERATOR_ALIAS.get(engine, {})
    ast = parse_dork(dork); out = []
    for t in ast.tokens:
        if t.op:
            new_op = aliases.get(t.op, t.op)
            if new_op is None:
                if t.value: out.append(f'{"-" if t.negate else ""}{t.value}')
                continue
            if new_op not in supported:
                if t.value:
                    prefix = "-" if t.negate else ""
                    q = '"' if t.quoted else ""
                    out.append(f"{prefix}{q}{t.value}{q}")
                continue
            t2 = DorkToken(t.kind, new_op, t.value, t.negate, t.quoted)
            out.append(repr(t2))
        else:
            out.append(repr(t))
    return " ".join(out)


def mutate_dork(dork, n=5):
    variations = {dork}
    ast = parse_dork(dork); ops = ast.operators
    if "filetype" in ops:
        for v in ops["filetype"]: variations.add(dork.replace(f"filetype:{v}", f"ext:{v}"))
    if "ext" in ops:
        for v in ops["ext"]: variations.add(dork.replace(f"ext:{v}", f"filetype:{v}"))
    SQL_EXTS = ["php","asp","aspx","jsp","cfm"]
    for op in ("filetype","ext"):
        for v in ops.get(op, []):
            if v.lower() in SQL_EXTS:
                for alt in SQL_EXTS:
                    if alt != v.lower(): variations.add(dork.replace(f"{op}:{v}", f"{op}:{alt}"))
    if "inurl" in ops:
        hints = ["id=","pid=","cat=","page=","uid=","product=","article="]
        for v in ops["inurl"]:
            for h in hints:
                if h not in v.lower():
                    variations.add(dork.replace(f"inurl:{v}", f"inurl:{v}{h}"))
    out = list(variations - {dork})
    random.shuffle(out)
    return ([dork] + out)[:max(1, n)]


def dedupe_dorks(dorks):
    seen=set(); out=[]
    for d in dorks:
        norm = normalize_dork(d).lower()
        if not norm or norm in seen: continue
        seen.add(norm); out.append(d.strip())
    return out


# ══════════════════════════════════════════════════════════════════════════════
# ─── URL FILTER / SCORER ─────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

BLACKLISTED_DOMAINS = {
    "yahoo.uservoice.com","uservoice.com","bing.com","google.com","googleapis.com",
    "gstatic.com","youtube.com","facebook.com","instagram.com","twitter.com","x.com",
    "linkedin.com","pinterest.com","reddit.com","wikipedia.org","amazon.com",
    "amazon.co","ebay.com","shopify.com","wordpress.com","blogspot.com","medium.com",
    "github.com","stackoverflow.com","w3schools.com","microsoft.com","apple.com",
    "cloudflare.com","yahoo.com","msn.com","live.com","outlook.com","mercadolibre.com",
    "aliexpress.com","alibaba.com","etsy.com","walmart.com","bestbuy.com",
    "capitaloneshopping.com","onetonline.org","moodle.","lyrics.fi","verkkouutiset.fi",
    "iltalehti.fi","sapo.pt","iol.pt","idealo.","zalando.","trovaprezzi.","whatsapp.com",
}

SQL_HIGH_PARAMS = {
    "id","uid","user_id","userid","pid","product_id","productid","cid","cat_id","catid",
    "category_id","aid","article_id","nid","news_id","bid","blog_id","sid","fid","forum_id",
    "tid","topic_id","mid","msg_id","oid","order_id","rid","page_id","item_id","itemid",
    "post_id","gid","lid","vid","did","doc_id",
}

SQL_MED_PARAMS = {
    "q","query","search","name","username","email","page","p","type","action","do","module",
    "view","mode","from","date","code","ref","file","path","url","data","value","param",
    "price","tag","section","content","lang",
}

VULN_EXTENSIONS = {".php",".asp",".aspx",".cfm",".jsf",".do",".cgi",".pl",".jsp"}

_JUNK_RE = re.compile(
    r"aclick\?|uservoice\.com|utm_source=|\.pdf$$|\.jpg$$|\.jpeg$$|\.png$$|\.gif$$|\.webp$$|\.avif$|"
    r"\.svg$$|\.ico$$|\.css$$|\.js$$|\.mp4$$|\.mp3$$|\.zip$|/static/|/assets/|/images/|/img/|"
    r"/fonts/|/media/|/cdn-cgi/|/wp-content/uploads/", re.IGNORECASE)


def score_url(url):
    try: parsed = urlparse(url)
    except Exception: return 0
    if not url.startswith("http"): return 0
    domain = parsed.netloc.lower()
    for bd in BLACKLISTED_DOMAINS:
        if bd in domain: return 0
    if _JUNK_RE.search(url): return 0
    query = parsed.query; path = parsed.path.lower()
    has_vuln_ext = any(path.endswith(ext) for ext in VULN_EXTENSIONS)
    if not query: return 25 if has_vuln_ext else 5
    score = 15
    params = parse_qs(query, keep_blank_values=True)
    pkeys = {k.lower() for k in params}
    if has_vuln_ext: score += 20
    score += len(pkeys & SQL_HIGH_PARAMS) * 15
    score += len(pkeys & SQL_MED_PARAMS) * 5
    for vals in params.values():
        for v in vals:
            if v.isdigit(): score += 10; break
    if len(url) > 300: score -= 10
    elif len(url) > 200: score -= 5
    if len(params) > 8: score -= 5
    return max(0, min(score, 100))


def filter_scored(urls, min_score):
    result = [(score_url(u), u) for u in urls]
    result = [(s,u) for s,u in result if s >= min_score]
    result.sort(reverse=True)
    return result


MAX_URL_LENGTH = 200

def extract_domain(url):
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception: return ""

def is_blocked(domain):
    for bd in BLACKLISTED_DOMAINS:
        if bd in domain: return True
    return False

def has_query_params(url):
    try: return bool(urlparse(url).query)
    except Exception: return False

def is_valid_url(url):
    try:
        p = urlparse(url); return p.scheme in ("http","https") and bool(p.netloc)
    except Exception: return False


def filter_urls(urls):
    total = len(urls); rm_invalid=rm_blocked=rm_no_query=rm_too_long=0
    seen=set(); kept=[]
    for url in urls:
        url = url.strip()
        if not url or url.startswith("#"): rm_invalid += 1; continue
        if not is_valid_url(url): rm_invalid += 1; continue
        if len(url) > MAX_URL_LENGTH: rm_too_long += 1; continue
        domain = extract_domain(url)
        if is_blocked(domain): rm_blocked += 1; continue
        if not has_query_params(url): rm_no_query += 1; continue
        if url in seen: continue
        seen.add(url); kept.append(url)
    return {"total":total,"kept":kept,"rm_invalid":rm_invalid,"rm_blocked":rm_blocked,
            "rm_no_query":rm_no_query,"rm_too_long":rm_too_long,
            "duplicates": total-rm_invalid-rm_blocked-rm_no_query-rm_too_long-len(kept)}


_TRACKING_PARAM_RE = re.compile(
    r"^(utm_\w+|fbclid|gclid|msclkid|yclid|mc_\w+|_ga|ref|source|medium|campaign|"
    r"affiliate|clickid|cid|sid_?|zanpid|dclid|twclid|igshid|s_kwcid)$", re.IGNORECASE)


def _normalize_url_for_dedup(url):
    try:
        p = urlparse(url)
        if not p.query: return url
        params = parse_qs(p.query, keep_blank_values=True)
        cleaned = {k:v for k,v in params.items() if not _TRACKING_PARAM_RE.match(k)}
        if cleaned == params: return url
        new_q = urlencode(cleaned, doseq=True)
        return p._replace(query=new_q).geturl()
    except Exception: return url


async def process_chunk_urls(chunk, semaphore, stop_ev):
    async with semaphore:
        if stop_ev.is_set(): return []
        await asyncio.sleep(0)
        return filter_urls(chunk)["kept"]


async def run_url_clean_job(chat_id, raw_lines, context):
    CLEAN_CHUNK_SIZE = 500; MAX_CONCURRENT = 4
    stop_ev = asyncio.Event()
    active_stop_evs[chat_id] = stop_ev
    total_input = len(raw_lines)
    status_msg = await context.bot.send_message(
        chat_id, f"🧹 URL CLEANER STARTED\n{'━'*30}\n📥 Input: {total_input}\n⏳ Processing...")
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    chunks = [raw_lines[i:i+CLEAN_CHUNK_SIZE] for i in range(0, total_input, CLEAN_CHUNK_SIZE)]
    tasks = [asyncio.create_task(process_chunk_urls(c, semaphore, stop_ev)) for c in chunks]
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        stop_ev.set()
        for t in tasks: t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        results = []
    seen_final=set(); final_urls=[]
    for r in results:
        if isinstance(r, list):
            for u in r:
                if u not in seen_final:
                    seen_final.add(u); final_urls.append(u)
    removed = total_input - len(final_urls)
    stopped = stop_ev.is_set()
    output_path = Path("results") / "cleaned_urls.txt"
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path,"w",encoding="utf-8") as f:
        f.write(f"# URL Cleaner — {datetime.now()}\n")
        f.write(f"# Input: {total_input} | Kept: {len(final_urls)} | Removed: {removed}\n\n")
        for u in final_urls: f.write(u + "\n")
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=status_msg.message_id,
            text=(f"{'⏹' if stopped else '✅'} URL CLEANER DONE\n{'━'*30}\n"
                  f"📥 Input  : {total_input}\n✅ Kept   : {len(final_urls)}\n"
                  f"🗑 Removed: {removed}\n{'━'*30}"))
    except Exception: pass
    if final_urls:
        with open(output_path,"rb") as f:
            await context.bot.send_document(chat_id, f, filename="cleaned_urls.txt",
                caption=f"🧹 {len(final_urls)} kept from {total_input}")
    else:
        await context.bot.send_message(chat_id, "⚠️ No URLs passed the filters.")
    active_stop_evs.pop(chat_id, None)
    active_jobs.pop(chat_id, None)


# ══════════════════════════════════════════════════════════════════════════════
# ─── SESSION FACTORY ─────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _make_isolated_session(use_tor=False, proxy=None, profile=None, http2=True):
    chosen_proxy = None
    if use_tor:
        chosen_proxy = TOR_PROXY
    elif proxy:
        chosen_proxy = proxy
    elif PROXY_ENABLED and _proxy_pool:
        chosen_proxy = get_random_proxy_url()
    if profile is None:
        profile = get_tls_profile("weighted")
    kwargs = {
        "impersonate":     profile["impersonate"],
        "verify":          False,
        "timeout":         20,
        "default_headers": False,
    }
    if chosen_proxy:
        kwargs["proxy"] = chosen_proxy
    sess = AsyncSession(**kwargs)
    sess._cur_proxy   = chosen_proxy
    sess._tls_profile = profile
    return sess


def _make_fallback_session(exclude_proxy=None):
    fb_proxy = get_random_proxy_url(exclude_url=exclude_proxy)
    return _make_isolated_session(proxy=fb_proxy)


# ══════════════════════════════════════════════════════════════════════════════
# ─── HTML EXTRACTION ─────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class _LinkExtractor(HTMLParser):
    __slots__ = ("links","_in_cite","_buf")
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links=[]; self._in_cite=False; self._buf=[]
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            adict = dict(attrs)
            for key in ("href","data-u"):
                val = adict.get(key, "")
                if val.startswith("http"): self.links.append(val)
        elif tag == "cite":
            self._in_cite = True; self._buf.clear()
    def handle_endtag(self, tag):
        if tag == "cite" and self._in_cite:
            text = "".join(self._buf).strip()
            if text.startswith("http"): self.links.append(text)
            self._in_cite = False; self._buf.clear()
    def handle_data(self, data):
        if self._in_cite: self._buf.append(data)


def _extract_links(html):
    p = _LinkExtractor()
    try: p.feed(html)
    except Exception: pass
    return p.links


_DDG_LINK_RE    = re.compile(r'class="result__a"[^>]*href="(https?://[^"]+)"', re.IGNORECASE)
_DDG_SNIPPET_RE = re.compile(r'uddg=(https?[^&"]+)', re.IGNORECASE)


def _extract_ddg_links(html):
    links = [unquote(m.group(1)) for m in _DDG_LINK_RE.finditer(html)]
    links += [unquote(m.group(1)) for m in _DDG_SNIPPET_RE.finditer(html)]
    return links


_BING_NOISE    = re.compile(r"bing\.com", re.IGNORECASE)
_YAHOO_NOISE   = re.compile(r"yimg\.com|yahoo\.com|doubleclick\.net|googleadservices", re.IGNORECASE)
_STATIC_EXT    = re.compile(r"\.(css|js|png|jpg|jpeg|gif|svg|ico|webp|woff2?|ttf|eot)(\?|$)", re.IGNORECASE)
_YAHOO_RU_PATH = re.compile(r"/RU=([^/&]+)")
_DDG_NOISE     = re.compile(r"duckduckgo\.com|duck\.com", re.IGNORECASE)


def _yahoo_link_extractor(html):
    raw = _extract_links(html)
    out = []
    for u in raw:
        if "r.search.yahoo.com" in u or "/r/" in u:
            parsed = urlparse(u)
            qs = parse_qs(parsed.query)
            if "RU" in qs:
                real = unquote(qs["RU"][0])
                if real.startswith(("http://","https://")): u = real
            else:
                m = _YAHOO_RU_PATH.search(parsed.path)
                if m:
                    real = unquote(m.group(1))
                    if real.startswith(("http://","https://")): u = real
        out.append(u)
    return out


_YAHOO_RU_PATH_V2     = re.compile(r"/RU=([^/&]+)")
_YAHOO_REDIRECT_HOSTS = frozenset(["r.search.yahoo.com","rd.yahoo.com","search.yahoo.com"])


def _yahoo_link_extractor_v2(html):
    raw = _extract_links(html)
    out = []
    for u in raw:
        u = u.strip()
        if not u.startswith("http"):
            continue
        try:
            parsed = urlparse(u)
            host   = parsed.netloc.lower()
            is_redirect = (
                host in _YAHOO_REDIRECT_HOSTS
                or "r.search.yahoo.com" in host
                or "rd.yahoo.com" in host
                or ("/r/" in parsed.path and "yahoo" in host)
            )
            if is_redirect:
                qs = parse_qs(parsed.query)
                if "RU" in qs:
                    real = unquote(qs["RU"][0])
                    if real.startswith(("http://","https://")):
                        u = real
                else:
                    m = _YAHOO_RU_PATH_V2.search(parsed.path)
                    if m:
                        real = unquote(m.group(1))
                        if real.startswith(("http://","https://")):
                            u = real
        except Exception: pass
        out.append(u)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# ─── CAPTCHA / DEGRADED / HEAT DETECTION ─────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

_CAPTCHA_RE = re.compile(
    r"captcha|are you a robot|unusual traffic|access denied|verify you are human|"
    r"please verify|too many requests|blocked|forbidden|rate limit|temporarily unavailable|"
    r"cf-error|error 429|request denied|robot check|human verification|"
    r"your ip|ip address|automated|bot detection|security check|"
    r"503 service|502 bad gateway|pardon our interruption", re.IGNORECASE)

_YAHOO_RESULT_SIGNALS = re.compile(
    r'id="results"|searchCenterMiddle|class="algo|class="Sr|data-b="algo|'
    r'"algo-sr"|"dd algo"|uh3_id|"compTitle"', re.IGNORECASE)


def _is_degraded(html, engine):
    if len(html) < 400: return True
    if _CAPTCHA_RE.search(html[:4096]): return True
    if engine == "bing" and 'id="b_results"' not in html and "b_algo" not in html: return True
    if engine == "yahoo" and not _YAHOO_RESULT_SIGNALS.search(html): return True
    if engine == "duckduckgo" and "result__a" not in html and "results--main" not in html: return True
    return False


def _is_captcha(html):
    return bool(_CAPTCHA_RE.search(html[:4096]))


async def _on_captcha_detected(engine, chunk_id, session_proxy):
    log.warning(f"[C{chunk_id}][{engine.upper()}] 🔴 CAPTCHA")
    await asyncio.sleep(random.uniform(8.0, 18.0))


# Yahoo-specific heat signals (unblockable engine)
_YAHOO_SOFT_BLOCK_SIGNALS = [
    re.compile(r"unusual\s+(?:traffic|activity)", re.I),
    re.compile(r"verify\s+you\s+are\s+(?:human|not\s+a\s+robot)", re.I),
    re.compile(r"too\s+many\s+requests", re.I),
    re.compile(r"please\s+enable\s+(?:cookies|javascript)", re.I),
    re.compile(r"access\s+(?:denied|blocked)", re.I),
    re.compile(r'<meta\s+http-equiv="refresh"[^>]*captcha', re.I),
    re.compile(r"hcaptcha|recaptcha|funcaptcha|arkose", re.I),
    re.compile(r"consent\.yahoo\.com.*collectConsent", re.I),
    re.compile(r'id="challenge-form"', re.I),
]

_YAHOO_HEALTHY_SIGNALS = [
    re.compile(r'id="(?:web|results)"', re.I),
    re.compile(r'class="?(?:algo|Sr|dd algo|compTitle)', re.I),
    re.compile(r'data-b="?algo', re.I),
    re.compile(r"searchCenterMiddle", re.I),
]


def diagnose_yahoo_response(status, html, headers):
    """Returns: 'ok' | 'soft' | 'hard' | 'empty' | 'redirect'."""
    if status == 429: return "hard"
    if status in (403, 503): return "hard"
    if status in (301, 302, 307):
        loc = ""
        try:
            loc = (headers.get("Location") or headers.get("location") or "").lower()
        except Exception:
            loc = ""
        if "consent" in loc or "login" in loc:
            return "redirect"
    if status != 200: return "empty"
    head = html[:8192]
    for rx in _YAHOO_SOFT_BLOCK_SIGNALS:
        if rx.search(head):
            return "hard" if "captcha" in rx.pattern.lower() else "soft"
    for rx in _YAHOO_HEALTHY_SIGNALS:
        if rx.search(head):
            return "ok"
    return "empty"


# ══════════════════════════════════════════════════════════════════════════════
# ─── YAHOO UNBLOCKABLE ENGINE v22 ────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

YAHOO_MIRRORS_V2 = [
    {"url":"https://search.yahoo.com/search",    "weight":1.0,"tld":"com","geo":"US"},
    {"url":"https://uk.search.yahoo.com/search", "weight":1.0,"tld":"uk", "geo":"GB"},
    {"url":"https://ca.search.yahoo.com/search", "weight":1.0,"tld":"ca", "geo":"CA"},
    {"url":"https://au.search.yahoo.com/search", "weight":1.0,"tld":"au", "geo":"AU"},
    {"url":"https://in.search.yahoo.com/search", "weight":1.0,"tld":"in", "geo":"IN"},
    {"url":"https://sg.search.yahoo.com/search", "weight":1.0,"tld":"sg", "geo":"SG"},
    {"url":"https://de.search.yahoo.com/search", "weight":0.8,"tld":"de", "geo":"DE"},
    {"url":"https://fr.search.yahoo.com/search", "weight":0.8,"tld":"fr", "geo":"FR"},
    {"url":"https://es.search.yahoo.com/search", "weight":0.8,"tld":"es", "geo":"ES"},
    {"url":"https://br.search.yahoo.com/search", "weight":0.8,"tld":"br", "geo":"BR"},
    {"url":"https://it.search.yahoo.com/search", "weight":0.8,"tld":"it", "geo":"IT"},
    {"url":"https://nl.search.yahoo.com/search", "weight":0.8,"tld":"nl", "geo":"NL"},
    {"url":"https://mx.search.yahoo.com/search", "weight":0.7,"tld":"mx", "geo":"MX"},
    {"url":"https://nz.search.yahoo.com/search", "weight":0.7,"tld":"nz", "geo":"NZ"},
    {"url":"https://za.search.yahoo.com/search", "weight":0.7,"tld":"za", "geo":"ZA"},
    {"url":"https://hk.search.yahoo.com/search", "weight":0.6,"tld":"hk", "geo":"HK"},
    {"url":"https://tw.search.yahoo.com/search", "weight":0.6,"tld":"tw", "geo":"TW"},
    {"url":"https://ph.search.yahoo.com/search", "weight":0.6,"tld":"ph", "geo":"PH"},
    {"url":"https://malaysia.search.yahoo.com/search","weight":0.5,"tld":"my","geo":"MY"},
]


@dataclass
class MirrorHealth:
    success: int = 0
    blocked: int = 0
    last_block_ts: float = 0.0
    cooldown_until: float = 0.0
    @property
    def score(self):
        total = self.success + self.blocked
        if total < 5: return 1.0
        base = self.success / total
        age = time.time() - self.last_block_ts
        decay = max(0.0, 1.0 - age/240.0)
        return max(0.05, base - 0.3*decay)


class MirrorPool:
    def __init__(self):
        self.health = defaultdict(MirrorHealth)
        self._lock = asyncio.Lock()
    async def pick(self):
        async with self._lock:
            now = time.time()
            candidates = [m for m in YAHOO_MIRRORS_V2
                          if self.health[m["url"]].cooldown_until <= now]
            if not candidates: candidates = YAHOO_MIRRORS_V2
            weights = [m["weight"] * self.health[m["url"]].score for m in candidates]
            return random.choices(candidates, weights=weights, k=1)[0]
    async def report(self, url, blocked):
        async with self._lock:
            h = self.health[url]
            if blocked:
                h.blocked += 1
                h.last_block_ts = time.time()
                cd = min(30 * (1.5 ** min(h.blocked, 6)), 600)
                h.cooldown_until = time.time() + cd
            else:
                h.success += 1


YAHOO_MIRROR_POOL = MirrorPool()


@dataclass
class BrowsingIdentity:
    profile: dict
    accept_lang: str
    timezone_offset: int
    viewport: tuple
    color_depth: int
    born_at: float = field(default_factory=time.time)
    requests_made: int = 0
    blocks_received: int = 0
    fingerprint_hash: str = ""
    max_requests: int = field(default_factory=lambda: random.randint(*YAHOO_UB_IDENTITY_REQUESTS))
    max_age: int = field(default_factory=lambda: random.randint(*YAHOO_UB_IDENTITY_AGE))

    def __post_init__(self):
        seed = f"{self.profile['impersonate']}{self.accept_lang}{self.viewport}"
        self.fingerprint_hash = hashlib.md5(seed.encode()).hexdigest()[:12]

    @property
    def is_burned(self):
        if self.blocks_received >= 2: return True
        if self.requests_made >= self.max_requests: return True
        if time.time() - self.born_at > self.max_age: return True
        return False


_LOCALE_BUNDLES = [
    ("en-US,en;q=0.9",                       -300, "US"),
    ("en-US,en;q=0.9,es;q=0.8",              -360, "US"),
    ("en-GB,en;q=0.9",                          0, "GB"),
    ("en-CA,en;q=0.9,fr-CA;q=0.8",           -300, "CA"),
    ("en-AU,en;q=0.9",                        600, "AU"),
    ("de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",    60, "DE"),
    ("fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",    60, "FR"),
    ("es-ES,es;q=0.9,en;q=0.8",                60, "ES"),
    ("pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",  -180, "BR"),
    ("nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",    60, "NL"),
    ("it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",    60, "IT"),
    ("en-IN,en-GB;q=0.9,en;q=0.8",            330, "IN"),
    ("en-SG,en;q=0.9",                        480, "SG"),
]

_VIEWPORTS = [
    (1920,1080),(1536,864),(1440,900),(1366,768),
    (1680,1050),(2560,1440),(1280,720),(1600,900),
    (390,844),(414,896),
]


def forge_identity():
    profile = get_tls_profile("weighted")
    lang, tz, geo = random.choice(_LOCALE_BUNDLES)
    if profile.get("mobile"):
        vp = random.choice([(390,844),(414,896),(360,800),(412,915)])
    else:
        vp = random.choice([v for v in _VIEWPORTS if v[0] >= 1280])
    return BrowsingIdentity(
        profile=profile,
        accept_lang=lang,
        timezone_offset=tz,
        viewport=vp,
        color_depth=random.choice([24,24,24,30,32]),
    )


class HumanTimer:
    @staticmethod
    def read_serp_delay(result_count=10):
        base = random.gauss(3.5, 1.2) + result_count * random.uniform(0.15, 0.35)
        if random.random() < 0.08:
            base += random.uniform(15, 45)
        elif random.random() < 0.02:
            base += random.uniform(60, 180)
        return max(1.5, base)
    @staticmethod
    def between_dorks_delay():
        if random.random() < 0.65:
            return max(1.0, random.gauss(5.5, 1.8))
        return max(5.0, random.gauss(22, 8))
    @staticmethod
    def typing_delay(query_len):
        return query_len * random.uniform(0.18, 0.32) + random.uniform(0.5, 1.5)
    @staticmethod
    def click_delay():
        return max(0.3, random.gauss(1.2, 0.4))


YAHOO_WARMUP_PATHS = {
    "com": [("https://www.yahoo.com/",  "https://www.google.com/"),
            ("https://search.yahoo.com/","https://www.yahoo.com/")],
    "uk":  [("https://uk.yahoo.com/",  "https://www.google.co.uk/"),
            ("https://uk.search.yahoo.com/","https://uk.yahoo.com/")],
    "ca":  [("https://ca.yahoo.com/",  "https://www.google.ca/"),
            ("https://ca.search.yahoo.com/","https://ca.yahoo.com/")],
    "au":  [("https://au.yahoo.com/",  "https://www.google.com.au/"),
            ("https://au.search.yahoo.com/","https://au.yahoo.com/")],
    "in":  [("https://in.yahoo.com/",  "https://www.google.co.in/"),
            ("https://in.search.yahoo.com/","https://in.yahoo.com/")],
    "de":  [("https://de.yahoo.com/",  "https://www.google.de/"),
            ("https://de.search.yahoo.com/","https://de.yahoo.com/")],
    "fr":  [("https://fr.yahoo.com/",  "https://www.google.fr/"),
            ("https://fr.search.yahoo.com/","https://fr.yahoo.com/")],
    "br":  [("https://br.yahoo.com/",  "https://www.google.com.br/"),
            ("https://br.search.yahoo.com/","https://br.yahoo.com/")],
    "sg":  [("https://sg.yahoo.com/",  "https://www.google.com.sg/"),
            ("https://sg.search.yahoo.com/","https://sg.yahoo.com/")],
}


async def warm_yahoo_session(sess, identity, tld="com"):
    path = YAHOO_WARMUP_PATHS.get(tld, YAHOO_WARMUP_PATHS["com"])
    try:
        for i, (url, referer) in enumerate(path):
            headers = build_headers_from_profile(
                identity.profile,
                referer=referer if i==0 else path[i-1][0],
            )
            headers["Accept-Language"] = identity.accept_lang
            if identity.profile.get("sec_ch_ua") and random.random() < 0.3:
                headers["Sec-Ch-Viewport-Width"] = str(identity.viewport[0])
            spoof_xff_headers(headers, probability=0.25)
            resp = await sess.get(url, headers=headers, timeout=12)
            if resp.status_code in (429, 403):
                return False
            await asyncio.sleep(random.uniform(0.6, 1.8))
            # Consent banner handling (~5-20% of EU geos)
            try:
                resp_url = str(getattr(resp, "url", "") or "")
            except Exception:
                resp_url = ""
            if i == 0 and "consent.yahoo" in resp_url.lower():
                try:
                    await sess.post(
                        "https://consent.yahoo.com/v2/collectConsent",
                        headers=headers,
                        data={"agree":"1","originalDoneUrl":url},
                        timeout=10,
                    )
                    await asyncio.sleep(random.uniform(0.5, 1.2))
                except Exception: pass
        return True
    except Exception:
        return False


_YAHOO_FR_LARGE = [
    "yfp-t","yfp-t-s","yfp-t-902","yfp-t-501","yfp-t-152","yfp-t-900",
    "uh3_search_web","uh3_finance_vert","uh3_finance_vert_gs","uh3_new_design",
    "fp-tts","sfp","sb-top","v9","free","p2",
    "yhs-Lkry-newnew","yhs-default","iry-syc-default","appsfch1","ush-ytff1",
]
_YAHOO_INTENT_POOL  = ["", "go","pr","img","fp"]
_YAHOO_AGE_POOL     = ["","","","1d","1w","1m","1y"]
_YAHOO_VS_POOL      = ["","","1"]
_YAHOO_TOGGLE_POOL  = ["","","1"]
_YAHOO_NORM_POOL    = ["","","1"]


def forge_yahoo_query(dork, page, max_res, identity):
    p_value = translate_dork(dork, "yahoo")
    b_value = (page - 1) * 10 + 1
    if page > 1 and random.random() < 0.10:
        b_value = max(1, b_value + random.choice([-1, 1]))
    ordered = [
        ("p",  p_value),
        ("b",  b_value),
        ("pz", min(max_res, 10)),
    ]
    if random.random() < 0.95:
        ordered.append(("fr", random.choice(_YAHOO_FR_LARGE)))
    if random.random() < 0.85:
        ordered.append(("ei", random.choice(["UTF-8","utf-8"])))
    if random.random() < 0.70:
        ordered.append(("vl", "lang_en"))
    optional = [
        ("age",    random.choice(_YAHOO_AGE_POOL),    0.12),
        ("vs",     random.choice(_YAHOO_VS_POOL),     0.08),
        ("toggle", random.choice(_YAHOO_TOGGLE_POOL), 0.07),
        ("nrm",    random.choice(_YAHOO_NORM_POOL),   0.06),
        ("intent", random.choice(_YAHOO_INTENT_POOL), 0.08),
        ("fr2",    "p:s,v:web,m:sb",                  0.15),
    ]
    for key, val, prob in optional:
        if val and random.random() < prob:
            ordered.append((key, val))
    head, tail = ordered[:3], ordered[3:]
    random.shuffle(tail)
    ordered = head + tail
    extra = {}
    if identity.profile.get("version", 0) >= 120 and random.random() < 0.30:
        extra["Sec-Ch-Viewport-Width"] = str(identity.viewport[0])
    return ordered, extra


class IdentityPool:
    """Pool of fully-warmed Yahoo browsing identities."""
    def __init__(self, target_size=YAHOO_UB_POOL_SIZE, use_tor=False):
        self.target_size = target_size
        self.use_tor     = use_tor
        self.entries     = deque()
        self._lock       = asyncio.Lock()
        self._closed     = False
        self._warming    = 0

    async def _build_one(self):
        identity = forge_identity()
        sess = _make_isolated_session(use_tor=self.use_tor, profile=identity.profile)
        mirror = await YAHOO_MIRROR_POOL.pick()
        ok = await warm_yahoo_session(sess, identity, tld=mirror["tld"])
        if not ok:
            try: await sess.close()
            except Exception: pass
            return
        async with self._lock:
            self.entries.append((identity, sess))

    async def initialize(self):
        log.info(f"[YAHOO-UB] Warming {self.target_size} identities...")
        remaining = self.target_size
        while remaining > 0:
            batch = min(4, remaining)
            await asyncio.gather(*[self._build_one() for _ in range(batch)],
                                  return_exceptions=True)
            remaining -= batch
            await asyncio.sleep(random.uniform(0.3, 0.8))
        log.info(f"[YAHOO-UB] Pool ready: {len(self.entries)} identities")

    async def _maybe_refill(self):
        async with self._lock:
            need = self.target_size - len(self.entries) - self._warming
        if need > 0:
            self._warming += 1
            async def _refill():
                try: await self._build_one()
                finally:
                    async with self._lock:
                        self._warming -= 1
            asyncio.create_task(_refill())

    async def acquire(self):
        async with self._lock:
            while self.entries:
                identity, sess = self.entries.popleft()
                if identity.is_burned:
                    try: await sess.close()
                    except Exception: pass
                    continue
                return identity, sess
        # Cold-build on demand
        identity = forge_identity()
        sess = _make_isolated_session(use_tor=self.use_tor, profile=identity.profile)
        mirror = await YAHOO_MIRROR_POOL.pick()
        await warm_yahoo_session(sess, identity, tld=mirror["tld"])
        return identity, sess

    async def release(self, identity, sess, *, blocked=False):
        if self._closed:
            try: await sess.close()
            except Exception: pass
            return
        if blocked:
            identity.blocks_received += 1
        identity.requests_made += 1
        if identity.is_burned:
            try: await sess.close()
            except Exception: pass
            await self._maybe_refill()
            return
        async with self._lock:
            self.entries.append((identity, sess))

    async def close_all(self):
        self._closed = True
        async with self._lock:
            while self.entries:
                _, s = self.entries.popleft()
                try: await s.close()
                except Exception: pass


async def fetch_yahoo_unblockable(pool, dork, page, max_res, worker_id=0):
    """Returns (urls, status)  status ∈ {'ok','empty','blocked','error'}."""
    last_diag = "error"
    for attempt in range(YAHOO_UB_MAX_ATTEMPTS):
        identity, sess = await pool.acquire()
        mirror = await YAHOO_MIRROR_POOL.pick()
        mirror_url = mirror["url"]
        mirror_host = urlparse(mirror_url).netloc

        wait_secs = await circuit_breaker.check(mirror_url)
        if wait_secs > 0:
            await asyncio.sleep(min(wait_secs, 20))

        ordered_params, extra_headers = forge_yahoo_query(dork, page, max_res, identity)
        query_string = urlencode(ordered_params)

        # Referer chain: 60% from home, 30% from /search root, 10% synthetic prev page
        r_choice = random.random()
        if r_choice < 0.60:
            referer = f"https://{mirror_host.replace('search.','')}/"
        elif r_choice < 0.90:
            referer = f"https://{mirror_host}/"
        else:
            prev_b = max(1, (page - 2) * 10 + 1)
            referer = (f"https://{mirror_host}/search?p="
                       f"{quote_plus(translate_dork(dork,'yahoo'))}&b={prev_b}")

        headers = build_headers_from_profile(identity.profile, referer=referer)
        headers["Accept-Language"] = identity.accept_lang
        headers.update(extra_headers)
        spoof_xff_headers(headers, probability=0.40)

        # Typing delay on first page of new dork
        if page == 1 and attempt == 0 and random.random() < 0.30:
            await asyncio.sleep(HumanTimer.typing_delay(len(dork)))

        try:
            full_url = f"{mirror_url}?{query_string}"
            resp = await sess.get(full_url, headers=headers, timeout=22,
                                   allow_redirects=False)
            try:
                resp_headers = dict(resp.headers)
            except Exception:
                resp_headers = {}
            diag = diagnose_yahoo_response(resp.status_code, resp.text, resp_headers)
            last_diag = diag

            if diag == "ok":
                urls = _yahoo_link_extractor_v2(resp.text)
                urls = [u for u in urls if u.startswith("http")
                        and not _YAHOO_NOISE.search(u) and not _STATIC_EXT.search(u)]
                urls = list(dict.fromkeys(urls))[:max_res]
                await circuit_breaker.record(mirror_url, blocked=False)
                await YAHOO_MIRROR_POOL.report(mirror_url, blocked=False)
                await pool.release(identity, sess, blocked=False)
                return urls, "ok"

            if diag == "empty":
                await circuit_breaker.record(mirror_url, blocked=False)
                await YAHOO_MIRROR_POOL.report(mirror_url, blocked=False)
                await pool.release(identity, sess, blocked=False)
                return [], "empty"

            if diag == "soft":
                await circuit_breaker.record(mirror_url, blocked=True)
                await YAHOO_MIRROR_POOL.report(mirror_url, blocked=True)
                await pool.release(identity, sess, blocked=True)
                await asyncio.sleep(random.uniform(2.5, 6.0))
                continue

            if diag == "hard":
                await circuit_breaker.record(mirror_url, blocked=True)
                await YAHOO_MIRROR_POOL.report(mirror_url, blocked=True)
                identity.blocks_received = 99   # force burn
                await pool.release(identity, sess, blocked=True)
                await asyncio.sleep(random.uniform(6.0, 14.0) + attempt * 3)
                continue

            if diag == "redirect":
                await YAHOO_MIRROR_POOL.report(mirror_url, blocked=True)
                identity.blocks_received = 99
                await pool.release(identity, sess, blocked=True)
                await asyncio.sleep(random.uniform(3.0, 7.0))
                continue

        except asyncio.TimeoutError:
            await circuit_breaker.record(mirror_url, blocked=True)
            await pool.release(identity, sess, blocked=False)
            await asyncio.sleep(random.uniform(1.5, 3.5))
        except CurlError as exc:
            if _is_proxy_error(exc):
                await pool.release(identity, sess, blocked=True)
                await asyncio.sleep(random.uniform(0.8, 2.0))
            else:
                await pool.release(identity, sess, blocked=False)
                await asyncio.sleep(random.uniform(1.0, 2.5))
        except Exception as exc:
            log.debug(f"[YAHOO-UB:W{worker_id}] {exc}")
            await pool.release(identity, sess, blocked=False)
            await asyncio.sleep(random.uniform(0.5, 1.5))

    return [], "blocked" if last_diag == "hard" else "error"


async def fetch_all_pages_yahoo_ub(pool, dork, pages, max_res, chunk_id=0):
    """Multi-page Yahoo fetch using the unblockable engine."""
    sorted_pages = sorted(pages)

    async def _fetch_one(page, idx):
        if idx > 0:
            await asyncio.sleep(humanize_delay(0.12 * idx, sigma_ratio=0.4))
        return await fetch_yahoo_unblockable(pool, dork, page, max_res, chunk_id)

    tasks   = [_fetch_one(p, i) for i, p in enumerate(sorted_pages)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_urls = []; degraded_total = 0
    for res in results:
        if isinstance(res, Exception): continue
        urls, status = res
        if status in ("blocked","error"): degraded_total += 1
        all_urls.extend(urls)
    return all_urls, degraded_total


# ══════════════════════════════════════════════════════════════════════════════
# ─── STANDARD FETCH (bing, ddg) ──────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def vary_bing_params(base_params):
    p = dict(base_params)
    p["form"]  = random.choice(["QBLH","QBRE","SBSC","QBHL","PERE","ANAB01"])
    p["count"] = random.choice([10,10,10,15,20])
    if random.random() < 0.12:
        p["msbqf"] = random.choice(["0","1",""])
    if random.random() < 0.08:
        p["qpvt"] = p.get("q","")[:20]
    if random.random() < 0.10:
        p["sc"] = f"8-{random.randint(10,40)}"
    return p


async def _generic_engine_fetch(session, method, url, *, params=None, data=None,
                                  engine, page, max_res, chunk_id, referer,
                                  link_extractor, noise_filter, max_retries=None):
    if max_retries is None:
        max_retries = MAX_RETRIES
    active_session = session
    fallback_session = None
    try:
        for attempt in range(max_retries):
            wait_secs = await circuit_breaker.check(url)
            if wait_secs > 0:
                await asyncio.sleep(min(wait_secs, 30.0))
            profile = getattr(active_session, "_tls_profile", None) or get_tls_profile()
            origin = referer.rstrip("/") if data is not None else None
            headers = build_headers_from_profile(profile, referer=referer, origin=origin)
            spoof_xff_headers(headers, probability=0.35)
            if data is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            try:
                if method == "GET":
                    resp = await active_session.get(url, params=params, headers=headers, timeout=20)
                else:
                    resp = await active_session.post(url, data=data, headers=headers, timeout=20)
                status = resp.status_code; html = resp.text
                if status == 429:
                    await circuit_breaker.record(url, blocked=True)
                    await asyncio.sleep(humanize_delay((2**attempt) * 3.0))
                    continue
                if status in (403, 503):
                    await circuit_breaker.record(url, blocked=True)
                    await asyncio.sleep(humanize_delay((2**attempt) * 1.5))
                    continue
                if status != 200:
                    await circuit_breaker.record(url, blocked=False)
                    return [], False
                if _is_captcha(html):
                    await circuit_breaker.record(url, blocked=True)
                    await _on_captcha_detected(engine, chunk_id, getattr(active_session,"_cur_proxy",None))
                    continue
                if _is_degraded(html, engine):
                    await circuit_breaker.record(url, blocked=True)
                    if attempt < max_retries - 1:
                        await asyncio.sleep(humanize_delay((2**attempt) * 1.5))
                        continue
                    return [], True
                raw = link_extractor(html)
                urls = [u for u in raw if u.startswith("http")
                        and not noise_filter(u) and not _STATIC_EXT.search(u)]
                urls = list(dict.fromkeys(urls))[:max_res]
                await circuit_breaker.record(url, blocked=False)
                return urls, False
            except asyncio.TimeoutError:
                await circuit_breaker.record(url, blocked=True)
                await asyncio.sleep(humanize_delay((2**attempt) * 1.2))
            except CurlError as exc:
                if (_is_proxy_error(exc) and PROXY_ENABLED and len(_proxy_pool) > 1
                        and attempt < max_retries - 1):
                    cur_proxy = getattr(active_session, "_cur_proxy", None)
                    if fallback_session is not None: await fallback_session.close()
                    fallback_session = _make_fallback_session(exclude_proxy=cur_proxy)
                    active_session = fallback_session
                    await asyncio.sleep(humanize_delay(0.8))
                    continue
                await asyncio.sleep(humanize_delay((2**attempt) * 1.2))
            except Exception as exc:
                log.error(f"[C{chunk_id}][{engine.upper()}] err: {exc}")
                return [], False
        return [], True
    finally:
        if fallback_session is not None:
            await fallback_session.close()


async def fetch_page_bing(session, dork, page, max_res, chunk_id=0):
    base_params = {"q": translate_dork(dork,"bing"), "count": min(max_res,10),
                   "first": (page-1)*10+1, "setlang": "en"}
    return await _generic_engine_fetch(
        session, "GET", "https://www.bing.com/search",
        params=vary_bing_params(base_params),
        engine="bing", page=page, max_res=max_res, chunk_id=chunk_id,
        referer="https://www.bing.com/",
        link_extractor=_extract_links,
        noise_filter=lambda u: bool(_BING_NOISE.search(u)),
    )


async def fetch_page_duckduckgo(session, dork, page, max_res, chunk_id=0):
    if page > 1: return [], False
    return await _generic_engine_fetch(
        session, "POST", "https://html.duckduckgo.com/html/",
        data={"q": translate_dork(dork,"duckduckgo"), "b":"", "kl":"us-en", "df":""},
        engine="duckduckgo", page=page, max_res=max_res, chunk_id=chunk_id,
        referer="https://duckduckgo.com/",
        link_extractor=_extract_ddg_links,
        noise_filter=lambda u: bool(_DDG_NOISE.search(u)),
    )


async def fetch_all_pages_generic(session, dork, engine, pages, max_res, chunk_id=0):
    sorted_pages = [min(pages)] if engine == "duckduckgo" else sorted(pages)
    fetch_fn = {"bing": fetch_page_bing, "duckduckgo": fetch_page_duckduckgo}[engine]
    async def _fetch_with_stagger(page, idx):
        if idx > 0:
            await asyncio.sleep(humanize_delay(0.05 * idx, sigma_ratio=0.4))
        return await fetch_fn(session, dork, page, max_res, chunk_id)
    tasks = [_fetch_with_stagger(p, i) for i, p in enumerate(sorted_pages)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_urls = []; degraded_total = 0
    for res in results:
        if isinstance(res, Exception): continue
        urls, degraded = res
        if degraded: degraded_total += 1
        all_urls.extend(urls)
    return all_urls, degraded_total


# ─── TOR ROTATION ────────────────────────────────────────────────────────────
_tor_rotation_task = None
tor_enabled_users = 0

async def rotate_tor_identity():
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 9051)
        await reader.readuntil(b"250 ")
        writer.write(b'AUTHENTICATE ""\r\n'); await writer.drain()
        resp = await reader.readuntil(b"250 ")
        if b"250" not in resp: writer.close(); return
        writer.write(b"SIGNAL NEWNYM\r\n"); await writer.drain()
        await reader.readuntil(b"250 ")
        writer.close(); await writer.wait_closed()
    except Exception as exc:
        log.warning(f"Tor rotation: {exc}")

async def _tor_rotation_loop():
    while tor_enabled_users > 0:
        await rotate_tor_identity()
        await asyncio.sleep(120)

def start_tor_rotation():
    global _tor_rotation_task
    if _tor_rotation_task is None or _tor_rotation_task.done():
        _tor_rotation_task = asyncio.create_task(_tor_rotation_loop())

def stop_tor_rotation():
    global _tor_rotation_task
    if _tor_rotation_task and not _tor_rotation_task.done():
        _tor_rotation_task.cancel()
        _tor_rotation_task = None


# ══════════════════════════════════════════════════════════════════════════════
# ─── XTREAM MODE (preserved) ─────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

YAHOO_ENDPOINTS = [m["url"] for m in YAHOO_MIRRORS_V2[:15]]
YAHOO_REFERERS  = [f"https://{urlparse(u).netloc}/" for u in YAHOO_ENDPOINTS[:9]]
YAHOO_HOMEPAGES = [
    "https://www.yahoo.com/", "https://search.yahoo.com/",
    "https://uk.yahoo.com/",  "https://au.yahoo.com/", "https://ca.yahoo.com/",
]
BING_XTREAM_ENDPOINTS = [
    "https://www.bing.com/search","https://cn.bing.com/search","https://global.bing.com/search",
]
BING_XTREAM_REFERERS = [
    "https://www.bing.com/","https://www.bing.com/search","https://cn.bing.com/",
]
BING_HOMEPAGES = ["https://www.bing.com/","https://cn.bing.com/"]
BING_XTREAM_MARKETS = ["en-US","en-GB","en-CA","en-AU","en-IN","en-SG","en-NZ"]


async def _preseed_session_cookies(sess, engine="yahoo"):
    try:
        url = random.choice(BING_HOMEPAGES if engine=="bing" else YAHOO_HOMEPAGES)
        profile = getattr(sess, "_tls_profile", None) or get_tls_profile("weighted")
        headers = build_headers_from_profile(profile)
        await sess.get(url, headers=headers, timeout=10)
        await asyncio.sleep(random.uniform(0.15, 0.4))
    except Exception: pass


class XtreamSessionPool:
    def __init__(self, size=XTREAM_SESSION_POOL_SIZE, engine="yahoo"):
        self.size = size; self.engine = engine
        self.sessions = deque()
        self._usage = {}; self._age = {}
        self._lock = asyncio.Lock(); self._closed = False

    async def _make_one(self, use_tor):
        profile = get_tls_profile("weighted")
        sess = _make_isolated_session(use_tor=use_tor, profile=profile)
        if XTREAM_PRESEED_COOKIES:
            seed_engine = "bing" if self.engine=="bing" else "yahoo"
            await _preseed_session_cookies(sess, engine=seed_engine)
        async with self._lock:
            sid = id(sess)
            self.sessions.append(sess)
            self._usage[sid] = 0
            self._age[sid]   = time.time()

    async def initialize(self, use_tor=False):
        log.info(f"[XTREAM] Building pool of {self.size}...")
        tasks_left = self.size
        while tasks_left > 0:
            batch = min(XTREAM_POOL_BATCH_SIZE, tasks_left)
            await asyncio.gather(*[self._make_one(use_tor) for _ in range(batch)],
                                  return_exceptions=True)
            tasks_left -= batch
        log.info(f"[XTREAM] Pool ready: {len(self.sessions)}")

    async def acquire(self):
        async with self._lock:
            if not self.sessions:
                profile = get_tls_profile("weighted")
                sess = _make_isolated_session(profile=profile)
                self._usage[id(sess)] = 0
                self._age[id(sess)]   = time.time()
                return sess
            return self.sessions.popleft()

    async def release(self, sess, burned=False):
        if self._closed:
            try: await sess.close()
            except Exception: pass
            return
        async with self._lock:
            sid = id(sess)
            self._usage[sid] = self._usage.get(sid, 0) + 1
            too_old  = (time.time() - self._age.get(sid, 0)) > XTREAM_SESSION_MAX_AGE
            too_used = self._usage[sid] > XTREAM_SESSION_MAX_USES
            if burned or too_old or too_used:
                try: await sess.close()
                except Exception: pass
                self._usage.pop(sid, None); self._age.pop(sid, None)
                profile = get_tls_profile("weighted")
                new_sess = _make_isolated_session(profile=profile)
                self._usage[id(new_sess)] = 0
                self._age[id(new_sess)]   = time.time()
                self.sessions.append(new_sess)
            else:
                self.sessions.append(sess)

    async def close_all(self):
        self._closed = True
        async with self._lock:
            while self.sessions:
                s = self.sessions.popleft()
                try: await s.close()
                except Exception: pass


async def xtream_fetch_yahoo(pool, dork, page, max_res, worker_id):
    sess = await pool.acquire()
    burned = False; captcha = False
    try:
        endpoint = random.choice(YAHOO_ENDPOINTS)
        referer  = random.choice(YAHOO_REFERERS)
        profile  = getattr(sess,"_tls_profile",None) or get_tls_profile("weighted")
        headers  = build_headers_from_profile(profile, referer=referer)
        params   = {
            "p": translate_dork(dork,"yahoo"),
            "b": (page-1)*10+1, "pz": min(max_res,10), "vl": "lang_en",
            "fr": random.choice(["yfp-t","uh3_search_web","sfp","yfp-t-s"]),
        }
        for attempt in range(XTREAM_MAX_RETRIES + 1):
            try:
                resp = await sess.get(endpoint, params=params, headers=headers, timeout=XTREAM_TIMEOUT)
                html = resp.text
                if resp.status_code == 429:
                    burned = True
                    return [], True, False
                if resp.status_code != 200:
                    return [], False, False
                if _is_captcha(html):
                    captcha = True; burned = True
                    return [], True, True
                if _is_degraded(html, "yahoo"):
                    if attempt < XTREAM_MAX_RETRIES: continue
                    return [], False, False
                urls = _yahoo_link_extractor(html)
                urls = [u for u in urls if u.startswith("http")
                        and not _YAHOO_NOISE.search(u) and not _STATIC_EXT.search(u)]
                return list(dict.fromkeys(urls))[:max_res], False, False
            except (asyncio.TimeoutError, CurlError):
                if attempt < XTREAM_MAX_RETRIES: continue
                return [], False, False
            except Exception as exc:
                log.debug(f"[XTREAM:W{worker_id}] {exc}")
                return [], False, False
        return [], False, False
    finally:
        await pool.release(sess, burned=burned)


async def xtream_fetch_bing(pool, dork, page, max_res, worker_id):
    sess = await pool.acquire()
    burned = False; captcha = False
    try:
        endpoint = random.choice(BING_XTREAM_ENDPOINTS)
        referer  = random.choice(BING_XTREAM_REFERERS)
        profile  = getattr(sess,"_tls_profile",None) or get_tls_profile("weighted")
        headers  = build_headers_from_profile(profile, referer=referer)
        params   = {
            "q": translate_dork(dork,"bing"), "count": min(max_res,10),
            "first": (page-1)*10+1, "setlang": "en",
            "mkt": random.choice(BING_XTREAM_MARKETS),
            "form": random.choice(["QBLH","QBRE","SBSD","NMSP"]),
        }
        for attempt in range(XTREAM_MAX_RETRIES + 1):
            try:
                resp = await sess.get(endpoint, params=params, headers=headers, timeout=XTREAM_TIMEOUT)
                html = resp.text
                if resp.status_code == 429:
                    burned = True
                    return [], True, False
                if resp.status_code != 200:
                    if attempt < XTREAM_MAX_RETRIES: continue
                    return [], False, False
                if _is_captcha(html):
                    captcha = True; burned = True
                    return [], True, True
                if _is_degraded(html, "bing"):
                    if attempt < XTREAM_MAX_RETRIES: continue
                    return [], False, False
                urls = _extract_links(html)
                urls = [u for u in urls if u.startswith("http")
                        and not _BING_NOISE.search(u) and not _STATIC_EXT.search(u)]
                return list(dict.fromkeys(urls))[:max_res], False, False
            except (asyncio.TimeoutError, CurlError):
                if attempt < XTREAM_MAX_RETRIES: continue
                return [], False, False
            except Exception as exc:
                log.debug(f"[XTREAM:BING:W{worker_id}] {exc}")
                return [], False, False
        return [], False, False
    finally:
        await pool.release(sess, burned=burned)


async def xtream_worker(wid, queue, results_q, pool, max_res, pages_per_dork,
                          min_score, stop_ev, rate_limiter, xtream_engine, captcha_counter):
    consecutive_fails = 0
    cooldown_until    = 0.0
    engine_toggle     = wid % 2
    while not stop_ev.is_set():
        try:
            dork = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        now = time.time()
        if cooldown_until > now:
            await asyncio.sleep(cooldown_until - now)
        if xtream_engine == "both":
            use_engine = "yahoo" if engine_toggle % 2 == 0 else "bing"
            engine_toggle += 1
        else:
            use_engine = xtream_engine
        fetch_fn = xtream_fetch_yahoo if use_engine == "yahoo" else xtream_fetch_bing
        tag = f"{use_engine}-xtream"
        page_tasks = []
        for page in range(1, pages_per_dork + 1):
            async def _do(p=page, fn=fetch_fn):
                async with rate_limiter:
                    return await fn(pool, dork, p, max_res, wid)
            page_tasks.append(asyncio.create_task(_do()))
        all_urls = []; any_burned = False; any_captcha = False
        try:
            page_results = await asyncio.wait_for(
                asyncio.gather(*page_tasks, return_exceptions=True),
                timeout=XTREAM_TIMEOUT * 3,
            )
            for r in page_results:
                if isinstance(r, tuple):
                    urls, burned, captcha = r
                    all_urls.extend(urls)
                    if burned: any_burned = True
                    if captcha: any_captcha = True
        except asyncio.TimeoutError:
            for t in page_tasks: t.cancel()
        scored = filter_scored(all_urls, min_score)
        try: results_q.put_nowait((dork, tag, scored, len(all_urls), any_captcha))
        except asyncio.QueueFull: await results_q.put((dork, tag, scored, len(all_urls), any_captcha))
        queue.task_done()
        if any_captcha:
            captcha_counter[0] += 1
        if any_burned:
            consecutive_fails += 1
            backoff = min(consecutive_fails * 1.5, 15.0)
            cooldown_until = time.time() + backoff
            await asyncio.sleep(random.uniform(backoff*0.5, backoff))
        elif all_urls:
            consecutive_fails = 0
            await asyncio.sleep(random.uniform(XTREAM_MIN_DELAY, XTREAM_MAX_DELAY))
        else:
            consecutive_fails += 1
            await asyncio.sleep(random.uniform(0.05, 0.2))


async def run_xtream_job(chat_id, dorks, context):
    sess_cfg      = get_session(chat_id)
    use_tor       = sess_cfg.get("tor", False)
    min_score     = sess_cfg.get("min_score", 30)
    max_res       = sess_cfg.get("max_results", 10)
    xtream_engine = sess_cfg.get("xtream_engine", "yahoo")
    cleaned     = dedupe_dorks(dorks)
    valid_dorks = [d for d in cleaned if validate_dork(d)[0]]
    total_dorks = len(valid_dorks)
    if total_dorks == 0:
        await context.bot.send_message(chat_id, "⚠️ No valid dorks.")
        active_jobs.pop(chat_id, None); return
    start_time = time.time()
    n_chunks   = XTREAM_CHUNKS
    workers_n  = XTREAM_WORKERS_PER_CHUNK
    total_workers = n_chunks * workers_n
    rate_limiter    = asyncio.Semaphore(total_workers)
    captcha_counter = [0]
    alive_proxies = sum(1 for p in _proxy_pool if p["alive"])
    proxy_info = (
        "🧅 TOR" if use_tor else
        f"🔄 {alive_proxies}/{len(_proxy_pool)} alive proxies" if PROXY_ENABLED and alive_proxies else
        "🔓 Direct"
    )
    engine_display = {"yahoo":"YAHOO (15 mirrors)","bing":"BING (3 mirrors)",
                      "both":"YAHOO + BING"}.get(xtream_engine, xtream_engine.upper())
    status_msg = await context.bot.send_message(
        chat_id,
        f"⚡⚡⚡ XTREAM MODE ENGAGED ⚡⚡⚡\n{'━'*30}\n"
        f"📋 Dorks      : {total_dorks}\n"
        f"🎯 Engine     : {engine_display}\n"
        f"🚀 Target RPS : {XTREAM_TARGET_RPS}/sec\n"
        f"📄 Pages/dork : {XTREAM_PAGES_PER_DORK}\n"
        f"⚙️ Workers    : {total_workers} ({n_chunks}×{workers_n})\n"
        f"🔄 Session pool: {XTREAM_SESSION_POOL_SIZE}\n"
        f"🛡 TLS profiles: {len(TLS_PROFILES)} rotating\n"
        f"🌐 Network    : {proxy_info}\n{'━'*30}\n⏳ Warming sessions...",
    )
    pool = XtreamSessionPool(size=XTREAM_SESSION_POOL_SIZE, engine=xtream_engine)
    await pool.initialize(use_tor=use_tor)
    queue     = asyncio.Queue(maxsize=total_dorks + 10)
    results_q = asyncio.Queue(maxsize=total_dorks * 2)
    stop_ev   = asyncio.Event()
    active_stop_evs[chat_id] = stop_ev
    for d in valid_dorks:
        await queue.put(d)
    worker_tasks = [
        asyncio.create_task(xtream_worker(
            i, queue, results_q, pool, max_res, XTREAM_PAGES_PER_DORK,
            min_score, stop_ev, rate_limiter, xtream_engine, captcha_counter,
        )) for i in range(total_workers)
    ]
    processed = 0; total_raw = 0; total_captcha = 0
    seen_norm = set(); seen_urls = set(); all_scored = []
    last_edit = 0.0; peak_rps = 0.0
    last_rps_t = time.time(); rps_count = 0; current_rps = 0.0
    tmp_file = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                            prefix=f"xtream_{chat_id}_", suffix=".txt")
    tmp_path = tmp_file.name
    tmp_file.write(f"# XTREAM Mode v22 — {engine_display}\n# {datetime.now()}\n")
    tmp_file.write(f"# Dorks: {total_dorks} | Pages: {XTREAM_PAGES_PER_DORK} | Workers: {total_workers}\n\n")
    tmp_file.close()
    incremental_f = open(tmp_path, "a", encoding="utf-8")
    async def _job_timeout():
        await asyncio.sleep(JOB_TIMEOUT)
        stop_ev.set()
    timeout_task = asyncio.create_task(_job_timeout())
    try:
        while processed < total_dorks and not stop_ev.is_set():
            try:
                dork, engine, scored, raw_cnt, was_captcha = await asyncio.wait_for(
                    results_q.get(), timeout=CHUNK_STALL_TIMEOUT)
            except asyncio.TimeoutError:
                if all(t.done() for t in worker_tasks): break
                continue
            processed += 1; total_raw += raw_cnt; rps_count += raw_cnt
            if was_captcha: total_captcha += 1
            for sc, url in scored:
                norm = _normalize_url_for_dedup(url)
                if norm not in seen_norm:
                    seen_norm.add(norm); seen_urls.add(url)
                    all_scored.append((sc, url))
                    try: incremental_f.write(f"{url}\n")
                    except Exception: pass
            if processed > 0 and processed % 20 == 0:
                captcha_rate = captcha_counter[0] / max(processed, 1)
                if captcha_rate > XTREAM_CAPTCHA_RATE_LIMIT:
                    log.warning(f"[XTREAM] High captcha rate {captcha_rate:.0%}")
                    await asyncio.sleep(random.uniform(1.0, 2.5))
            now = time.time()
            if now - last_rps_t >= 2.0:
                current_rps = rps_count / (now - last_rps_t)
                if current_rps > peak_rps: peak_rps = current_rps
                rps_count = 0; last_rps_t = now
            if time.time() - last_edit > 3.5:
                pct = int(processed/total_dorks*100)
                bar = "█"*(pct//10) + "░"*(10-pct//10)
                elapsed = int(time.time() - start_time)
                eta = int((elapsed/processed)*(total_dorks-processed)) if processed else 0
                captcha_rate = captcha_counter[0] / max(processed, 1)
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=status_msg.message_id,
                        text=(f"⚡⚡⚡ XTREAM RUNNING ⚡⚡⚡\n{'━'*30}\n"
                              f"[{bar}] {pct}%\n"
                              f"✅ Dorks    : {processed}/{total_dorks}\n"
                              f"🔍 Raw URLs : {total_raw}\n"
                              f"🎯 Targets  : {len(all_scored)}\n"
                              f"📊 RPS      : {current_rps:.0f} (peak {peak_rps:.0f})\n"
                              f"🛡 Captchas : {total_captcha} ({captcha_rate:.0%})\n"
                              f"⏱ {elapsed}s | ETA {eta}s\n{'━'*30}"))
                    last_edit = time.time()
                except Exception: pass
        stop_ev.set()
        for t in worker_tasks: t.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
    except asyncio.CancelledError:
        stop_ev.set()
        for t in worker_tasks: t.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        raise
    finally:
        try: incremental_f.close()
        except Exception: pass
        timeout_task.cancel()
        try: await timeout_task
        except Exception: pass
        await pool.close_all()
        active_jobs.pop(chat_id, None)
        active_stop_evs.pop(chat_id, None)
    all_scored.sort(reverse=True)
    elapsed = int(time.time() - start_time)
    avg_rps = total_raw / max(elapsed, 1)
    high = [(s,u) for s,u in all_scored if s>=70]
    med  = [(s,u) for s,u in all_scored if 40<=s<70]
    low  = [(s,u) for s,u in all_scored if s<40]
    domain_counts = Counter(extract_domain(u) for _,u in all_scored)
    top_domains   = domain_counts.most_common(10)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(f"# XTREAM Mode v22 — {engine_display}\n# {datetime.now()}\n")
        f.write(f"# Dorks: {total_dorks} | Raw: {total_raw} | Targets: {len(all_scored)}\n")
        f.write(f"# Avg RPS: {avg_rps:.0f} | Peak RPS: {peak_rps:.0f} | Time: {elapsed}s\n")
        f.write(f"# Captchas: {total_captcha} | Min-score: {min_score}\n\n")
        if top_domains:
            f.write("# ── TOP DOMAINS ──\n")
            for dom, cnt in top_domains:
                f.write(f"# {cnt:>4}  {dom}\n")
            f.write("\n")
        if high:
            f.write(f"# ── HIGH (≥70) — {len(high)} ──\n")
            for _,u in high: f.write(f"{u}\n")
        if med:
            f.write(f"\n# ── MEDIUM (40-69) — {len(med)} ──\n")
            for _,u in med: f.write(f"{u}\n")
        if low and min_score < 40:
            f.write(f"\n# ── LOW (<40) — {len(low)} ──\n")
            for _,u in low: f.write(f"{u}\n")
    dom_summary = "\n".join(f"  {cnt}× {d}" for d,cnt in top_domains[:5]) if top_domains else "  (none)"
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=status_msg.message_id,
            text=(f"🏁 XTREAM COMPLETE!\n{'━'*30}\n"
                  f"📋 Dorks       : {total_dorks}\n"
                  f"🔍 Raw URLs    : {total_raw}\n"
                  f"🎯 Targets     : {len(all_scored)}\n"
                  f"📊 Avg RPS     : {avg_rps:.0f} | Peak: {peak_rps:.0f}\n"
                  f"🛡 Captchas    : {total_captcha}\n"
                  f"⏱ Total time  : {elapsed}s\n"
                  f"{'━'*30}\n"
                  f"🏆 Top domains:\n{dom_summary}"))
    except Exception: pass
    if all_scored:
        with open(tmp_path,"rb") as f:
            await context.bot.send_document(chat_id, f,
                filename=f"xtream_{total_dorks}d_{len(all_scored)}u.txt",
                caption=(f"⚡ XTREAM v22 RESULTS\n"
                         f"🎯 {len(all_scored)} URLs | 📊 {avg_rps:.0f} avg / {peak_rps:.0f} peak RPS\n"
                         f"⏱ {elapsed}s | 🛡 {total_captcha} captchas"))
    else:
        await context.bot.send_message(chat_id, "⚠️ No URLs matched filter.")
    try: os.unlink(tmp_path)
    except OSError: pass


# ══════════════════════════════════════════════════════════════════════════════
# ─── STANDARD WORKER / CHUNK / JOB ───────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

async def dork_worker(wid, chunk_id, queue, results_q, engines, pages, max_res,
                       session, min_score, stop_ev, slowdown_ev, yahoo_ub_pool=None):
    """
    Normal-mode worker.
    When the engine is Yahoo and a yahoo_ub_pool is provided, uses the
    UNBLOCKABLE engine (identity pool, mirror health, human timing).
    """
    eidx = wid % len(engines)
    empty_streak = consecutive_hits = 0
    while not stop_ev.is_set():
        try:
            dork = await asyncio.wait_for(queue.get(), timeout=2.0)
        except asyncio.TimeoutError:
            continue
        engine = engines[eidx % len(engines)]; eidx += 1
        raw, degraded_cnt = [], 0
        try:
            if engine == "yahoo" and yahoo_ub_pool is not None:
                # ── UNBLOCKABLE YAHOO PATH ──
                raw, degraded_cnt = await asyncio.wait_for(
                    fetch_all_pages_yahoo_ub(yahoo_ub_pool, dork, pages, max_res, chunk_id),
                    timeout=WORKER_FETCH_TIMEOUT)
            else:
                raw, degraded_cnt = await asyncio.wait_for(
                    fetch_all_pages_generic(session, dork, engine, pages, max_res, chunk_id),
                    timeout=WORKER_FETCH_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning(f"[C{chunk_id}][W{wid}] timeout: {dork[:50]}")
        except asyncio.CancelledError:
            try: results_q.put_nowait((dork, engine, [], 0, 0))
            except asyncio.QueueFull: pass
            queue.task_done(); raise
        except Exception as exc:
            log.warning(f"[C{chunk_id}][W{wid}] err: {exc}")
        scored = filter_scored(raw, min_score)
        try: results_q.put_nowait((dork, engine, scored, len(raw), degraded_cnt))
        except asyncio.QueueFull: await results_q.put((dork, engine, scored, len(raw), degraded_cnt))
        queue.task_done()
        # Human-like inter-dork timing
        if raw:
            consecutive_hits += 1; empty_streak = 0
            if engine == "yahoo" and yahoo_ub_pool is not None:
                # Use HumanTimer for Yahoo-UB (longer between-dork delays)
                delay = HumanTimer.between_dorks_delay() * 0.5  # halved for throughput
            elif consecutive_hits >= FAST_STREAK_THRESHOLD:
                delay = random.uniform(FAST_MIN_DELAY, FAST_MAX_DELAY)
            else:
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
        else:
            consecutive_hits = 0; empty_streak += 1
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            if empty_streak >= 3:
                delay += min(empty_streak * 1.0, 8.0)
        if slowdown_ev.is_set():
            delay += random.uniform(1.0, 2.5)
        await asyncio.sleep(delay)


async def run_chunk(chunk_id, dorks, engines, pages, max_res, use_tor, min_score,
                     workers_n, progress_q, global_stop_ev, proxy=None,
                     yahoo_ub_enabled=True):
    session = _make_isolated_session(use_tor=use_tor, proxy=proxy)
    # Build YAHOO UB pool if Yahoo is in engines and feature enabled
    yahoo_ub_pool = None
    if yahoo_ub_enabled and "yahoo" in engines:
        pool_size = max(workers_n // 2, 4)  # rough heuristic
        yahoo_ub_pool = IdentityPool(target_size=pool_size, use_tor=use_tor)
        await yahoo_ub_pool.initialize()
    queue     = asyncio.Queue(maxsize=len(dorks) * 2)
    results_q = asyncio.Queue(maxsize=500)
    stop_ev = asyncio.Event(); slowdown_ev = asyncio.Event()
    for d in dorks: await queue.put(d)
    total = len(dorks); processed = empty_count = chunk_raw = chunk_degraded = 0
    chunk_scored = []
    async def _watch_global():
        while not stop_ev.is_set():
            if global_stop_ev.is_set(): stop_ev.set()
            await asyncio.sleep(0.5)
    worker_tasks = [
        asyncio.create_task(dork_worker(
            i, chunk_id, queue, results_q, engines, pages, max_res, session,
            min_score, stop_ev, slowdown_ev, yahoo_ub_pool=yahoo_ub_pool))
        for i in range(workers_n)
    ]
    global_watcher = asyncio.create_task(_watch_global())
    try:
        while processed < total and not stop_ev.is_set():
            try:
                dork, engine, scored, raw_cnt, deg_cnt = await asyncio.wait_for(
                    results_q.get(), timeout=CHUNK_STALL_TIMEOUT)
            except asyncio.TimeoutError:
                if all(t.done() for t in worker_tasks): break
                continue
            processed += 1; chunk_raw += raw_cnt; chunk_degraded += deg_cnt
            if raw_cnt == 0: empty_count += 1
            chunk_scored.extend(scored)
            empty_rate = empty_count / max(processed, 1)
            if empty_rate >= EMPTY_RATE_SLOWDOWN and not slowdown_ev.is_set():
                slowdown_ev.set()
            elif empty_rate < EMPTY_RATE_RECOVER and slowdown_ev.is_set():
                slowdown_ev.clear()
            try: progress_q.put_nowait({"chunk_id":chunk_id,"processed":processed,
                                         "total":total,"raw":raw_cnt,"kept":len(scored)})
            except asyncio.QueueFull: pass
        for t in worker_tasks:
            if not t.done(): t.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
    except asyncio.CancelledError:
        stop_ev.set()
        for t in worker_tasks: t.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        raise
    finally:
        global_watcher.cancel()
        await asyncio.gather(global_watcher, return_exceptions=True)
        await session.close()
        if yahoo_ub_pool is not None:
            await yahoo_ub_pool.close_all()
    return {"chunk_id":chunk_id,"scored":chunk_scored,"raw_count":chunk_raw,
            "degraded_count":chunk_degraded,"processed":processed,"empty_count":empty_count}


async def run_dork_job(chat_id, dorks, context):
    sess = get_session(chat_id)
    if sess.get("xtream", False):
        await run_xtream_job(chat_id, dorks, context)
        return
    engines = sess.get("engines", list(ENGINES))
    workers_n = min(sess.get("workers", WORKERS_PER_CHUNK), MAX_WORKERS_PER_CHUNK)
    max_res = sess.get("max_results", MAX_RESULTS)
    pages = sess.get("pages", [1])
    use_tor = sess.get("tor", False)
    min_score = sess.get("min_score", 30)
    n_chunks = max(1, sess.get("chunks", N_CHUNKS))
    yahoo_ub_enabled = sess.get("yahoo_ub", True)
    cleaned = dedupe_dorks(dorks)
    valid_dorks = []; invalid_dorks = []
    for d in cleaned:
        ok, msg = validate_dork(d)
        if ok: valid_dorks.append(d)
        else: invalid_dorks.append((d, msg))
    dorks = valid_dorks; total_dorks = len(dorks)
    if total_dorks == 0:
        await context.bot.send_message(chat_id, "⚠️ No valid dorks.")
        active_jobs.pop(chat_id, None); return
    pages_str = ", ".join(str(p) for p in pages)
    start_time = time.time()
    chunk_size = max(1, -(-total_dorks // n_chunks))
    chunks = [dorks[i:i+chunk_size] for i in range(0, total_dorks, chunk_size)]
    actual_chunks = len(chunks)
    tmp_file = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                            prefix=f"dork_{chat_id}_", suffix=".txt")
    tmp_path = tmp_file.name
    tmp_file.write(f"# Dork Parser v22.0 — Yahoo Unblockable\n")
    tmp_file.write(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    tmp_file.write(f"# Dorks: {total_dorks} | Pages: {pages_str} | Chunks: {actual_chunks}\n\n")
    tmp_file.close()
    alive_proxies = sum(1 for p in _proxy_pool if p["alive"])
    if use_tor: proxy_info = "🧅 TOR"
    elif PROXY_ENABLED and alive_proxies:
        proxy_info = f"🔄 {alive_proxies}/{len(_proxy_pool)} alive"
    elif PROXY_ENABLED and _proxy_pool:
        proxy_info = f"⚠️ {len(_proxy_pool)} 0-alive"
    elif not PROXY_ENABLED and _proxy_pool:
        proxy_info = f"⏸ DISABLED"
    else: proxy_info = "🔓 Direct"
    yahoo_active = ("yahoo" in engines) and yahoo_ub_enabled
    yahoo_line = (f"⚡ Yahoo    : UNBLOCKABLE | identity-pool | {len(YAHOO_MIRRORS_V2)} mirrors\n"
                  if yahoo_active else "")
    status_msg = await context.bot.send_message(
        chat_id,
        f"🕷 DORK PARSER v22.0 — STARTED\n{'━'*30}\n"
        f"📋 Dorks    : {total_dorks}"
        + (f" (⚠️ {len(invalid_dorks)} skip)" if invalid_dorks else "")
        + f"\n📄 Pages    : {pages_str}\n"
        f"⚡ Chunks   : {actual_chunks}\n"
        f"⚙️ Workers  : {workers_n}/chunk (total {workers_n*actual_chunks})\n"
        f"🔍 Engines  : {' + '.join(e.upper() for e in engines)}\n"
        f"🛡 Filter   : SQL ≥{min_score}\n"
        f"🌐 Network  : {proxy_info}\n"
        f"🔒 TLS      : {len(TLS_PROFILES)} profiles rotating\n"
        + yahoo_line +
        f"🎯 Target   : ~200 URLs/sec\n{'━'*30}\n⏳ Starting...",
    )
    global_stop_ev = asyncio.Event()
    active_stop_evs[chat_id] = global_stop_ev
    progress_q = asyncio.Queue(maxsize=total_dorks * 2)
    chunk_counters = {i: {"processed":0,"total":len(chunks[i])} for i in range(actual_chunks)}
    agg_raw=[0]; agg_kept=[0]; last_edit=[0.0]; total_processed=[0]
    rps_window=[time.time(), 0, 0.0]
    async def _status_updater():
        while not global_stop_ev.is_set():
            drained = False
            while True:
                try:
                    ev = progress_q.get_nowait()
                    chunk_counters[ev["chunk_id"]]["processed"] = ev["processed"]
                    agg_raw[0] += ev["raw"]; agg_kept[0] += ev["kept"]
                    total_processed[0] += 1
                    rps_window[1] += ev["raw"]
                    drained = True
                except asyncio.QueueEmpty: break
            now = time.time()
            if now - rps_window[0] >= 2.0:
                rps_window[2] = rps_window[1] / (now - rps_window[0])
                rps_window[1] = 0; rps_window[0] = now
            if drained and time.time() - last_edit[0] > 4.0:
                proc = total_processed[0]
                pct = int(proc/total_dorks*100) if total_dorks else 100
                bar = "█"*(pct//10) + "░"*(10-pct//10)
                elapsed = int(time.time() - start_time)
                eta = int((elapsed/proc)*(total_dorks-proc)) if proc else 0
                cinfo = " | ".join(f"C{i}:{chunk_counters[i]['processed']}/{chunk_counters[i]['total']}"
                                    for i in range(actual_chunks))
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=status_msg.message_id,
                        text=(f"⚡ PARSING [{actual_chunks}c]\n{'━'*30}\n"
                              f"[{bar}] {pct}%\n"
                              f"✅ Done: {proc}/{total_dorks}\n"
                              f"🎯 SQL: {agg_kept[0]} | 🗑 {agg_raw[0]-agg_kept[0]}\n"
                              f"📊 RPS: {rps_window[2]:.0f}/sec\n"
                              f"⏱ {elapsed}s | ETA {eta}s\n📦 {cinfo}\n{'━'*30}"))
                    last_edit[0] = time.time()
                except Exception: pass
            await asyncio.sleep(0.5)
    async def _job_timeout():
        await asyncio.sleep(JOB_TIMEOUT); global_stop_ev.set()
    status_task = asyncio.create_task(_status_updater())
    timeout_task = asyncio.create_task(_job_timeout())
    chunk_proxies = [get_random_proxy_url() if not use_tor else None for _ in range(actual_chunks)]
    chunk_results = []
    try:
        chunk_tasks = []
        for i, chunk_dorks in enumerate(chunks):
            if i > 0: await asyncio.sleep(random.uniform(*CHUNK_STAGGER_DELAY))
            task = asyncio.create_task(run_chunk(
                i, chunk_dorks, engines, pages, max_res, use_tor, min_score,
                workers_n, progress_q, global_stop_ev, proxy=chunk_proxies[i],
                yahoo_ub_enabled=yahoo_ub_enabled))
            chunk_tasks.append(task)
        chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)
    except asyncio.CancelledError:
        global_stop_ev.set()
        for t in chunk_tasks: t.cancel()
        await asyncio.gather(*chunk_tasks, return_exceptions=True)
        raise
    finally:
        global_stop_ev.set()
        timeout_task.cancel(); status_task.cancel()
        await asyncio.gather(timeout_task, status_task, return_exceptions=True)
        active_jobs.pop(chat_id, None)
        active_stop_evs.pop(chat_id, None)
    seen_urls=set(); all_scored=[]; total_raw=total_degraded=failed_chunks=0
    for result in chunk_results:
        if isinstance(result, Exception): failed_chunks += 1; continue
        for sc, url in result["scored"]:
            if url not in seen_urls:
                seen_urls.add(url); all_scored.append((sc, url))
        total_raw += result["raw_count"]
        total_degraded += result["degraded_count"]
    all_scored.sort(reverse=True)
    unique_cnt = len(all_scored)
    elapsed = int(time.time() - start_time)
    avg_rps = total_raw / max(elapsed, 1)
    high = [(s,u) for s,u in all_scored if s>=70]
    med  = [(s,u) for s,u in all_scored if 40<=s<70]
    low  = [(s,u) for s,u in all_scored if s<40]
    with open(tmp_path,"a",encoding="utf-8") as f:
        if high:
            f.write(f"# HIGH (≥70) — {len(high)}\n")
            for _,u in high: f.write(f"{u}\n")
        if med:
            f.write(f"\n# MEDIUM — {len(med)}\n")
            for _,u in med: f.write(f"{u}\n")
        if low and min_score < 40:
            f.write(f"\n# LOW — {len(low)}\n")
            for _,u in low: f.write(f"{u}\n")
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=status_msg.message_id,
            text=(f"🏁 JOB COMPLETE!\n{'━'*30}\n"
                  f"📋 Dorks   : {total_dorks}\n📄 Pages   : {pages_str}\n"
                  f"⚡ Chunks  : {actual_chunks}\n🔍 Raw     : {total_raw}\n"
                  f"🎯 SQL     : {unique_cnt}\n🗑 Drop    : {total_raw-unique_cnt}\n"
                  f"⚠️ Degraded: {total_degraded}\n"
                  f"📊 Avg RPS : {avg_rps:.0f}/sec\n"
                  f"⏱ Time    : {elapsed}s\n{'━'*30}"))
    except Exception: pass
    if all_scored:
        with open(tmp_path,"rb") as f:
            await context.bot.send_document(chat_id, f,
                filename=f"sql_{total_dorks}d_{unique_cnt}u.txt",
                caption=f"🎯 {unique_cnt} URLs | 📊 {avg_rps:.0f} RPS | ⏱ {elapsed}s")
    else:
        await context.bot.send_message(chat_id, "⚠️ No URLs matched filter.")
    try: os.unlink(tmp_path)
    except OSError: pass


# ─── UI HELPERS ──────────────────────────────────────────────────────────────
def get_session(chat_id):
    if chat_id not in user_sessions:
        user_sessions[chat_id] = dict(DEFAULT_SESSION)
    return user_sessions[chat_id]


def page_keyboard(selected):
    rows, row = [], []
    for p in range(1, 71):
        row.append(InlineKeyboardButton(f"✅{p}" if p in selected else str(p),
                                         callback_data=f"pg_{p}"))
        if len(row) == 5: rows.append(row); row = []
    if row: rows.append(row)
    rows.append([
        InlineKeyboardButton("🔁 All (1-70)", callback_data="pg_all"),
        InlineKeyboardButton("❌ Clear", callback_data="pg_clear"),
        InlineKeyboardButton("✅ Confirm", callback_data="pg_confirm"),
    ])
    return InlineKeyboardMarkup(rows)


def main_menu_keyboard(sess):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Bulk Upload", callback_data="m_bulk"),
         InlineKeyboardButton("🔍 Single Dork", callback_data="m_single")],
        [InlineKeyboardButton("📄 Select Pages", callback_data="m_pages"),
         InlineKeyboardButton("⚙️ Settings", callback_data="m_settings")],
        [InlineKeyboardButton(f"🧅 Tor {'ON' if sess.get('tor') else 'OFF'}", callback_data="m_tor"),
         InlineKeyboardButton(f"🛡 SQL ≥{sess.get('min_score',30)}", callback_data="m_filter")],
        [InlineKeyboardButton(f"⚡ Xtream {'ON' if sess.get('xtream') else 'OFF'}",
                              callback_data="m_xtream"),
         InlineKeyboardButton(f"🛡 Y-UB {'ON' if sess.get('yahoo_ub') else 'OFF'}",
                              callback_data="m_yahoo_ub")],
        [InlineKeyboardButton("🧹 URL Cleaner", callback_data="m_clean"),
         InlineKeyboardButton("📋 Proxy List", callback_data="m_proxylist")],
        [InlineKeyboardButton("🔍 Proxy Check", callback_data="m_proxycheck"),
         InlineKeyboardButton("📊 Status", callback_data="m_status")],
        [InlineKeyboardButton("📖 Help", callback_data="m_help")],
    ])


def filter_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("0", callback_data="f_0"),
         InlineKeyboardButton("20", callback_data="f_20"),
         InlineKeyboardButton("30", callback_data="f_30"),
         InlineKeyboardButton("40", callback_data="f_40")],
        [InlineKeyboardButton("50", callback_data="f_50"),
         InlineKeyboardButton("60", callback_data="f_60"),
         InlineKeyboardButton("70", callback_data="f_70"),
         InlineKeyboardButton("80", callback_data="f_80")],
        [InlineKeyboardButton("🔙 Back", callback_data="m_back")],
    ])


# ══════════════════════════════════════════════════════════════════════════════
# ─── COMMAND HANDLERS ────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update, context):
    chat_id = update.effective_chat.id
    sess = get_session(chat_id)
    alive = sum(1 for p in _proxy_pool if p["alive"])
    if PROXY_ENABLED and alive:
        proxy_status = f"🔄 {alive}/{len(_proxy_pool)} alive proxies"
    elif PROXY_ENABLED and _proxy_pool:
        proxy_status = f"⚠️ {len(_proxy_pool)} (0 alive — /proxycheck)"
    elif not PROXY_ENABLED and _proxy_pool:
        proxy_status = f"⏸ {len(_proxy_pool)} DISABLED"
    else:
        proxy_status = "🔓 No proxies"
    await update.message.reply_text(
        "🕷 DORK PARSER v22.0 — YAHOO UNBLOCKABLE EDITION\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🆕 NEW in v22.0:\n"
        f"  🛡 Yahoo UB: identity pool + {len(YAHOO_MIRRORS_V2)} mirrors\n"
        "     • TLS rotation per identity (22 profiles)\n"
        "     • Cookie-warmed via natural home→search nav\n"
        "     • Human timing (Gaussian + bimodal)\n"
        "     • Soft/hard heat diagnosis → auto rotation\n"
        "     • Mirror health-weighted selection\n"
        f"  ⚡ /xtream — 1000 URLs/sec Yahoo bruteforce\n\n"
        f"{proxy_status}\n\n"
        "📌 Core Commands:\n"
        "  /dork <q>     — single dork search\n"
        "  /xtream on|off — toggle XTREAM mode\n"
        "  /yahoo_ub on|off — toggle Yahoo Unblockable\n"
        "  /dorkcheck <q>— validate dork\n"
        "  /mutate <q>   — generate variations\n"
        "  /clean        — URL cleaner\n"
        "  /pages [N|1-10|1,3,5] — set pages\n"
        "  /workers N    — workers/chunk (1-60)\n"
        "  /chunks N     — parallel chunks (1-8)\n"
        "  /engine X     — bing|yahoo|ddg|all\n"
        "  /tor          — toggle Tor\n"
        "  /filter N     — SQL score 0-100\n"
        "  /stop         — stop & get partial\n\n"
        "🔄 Proxy: /addproxy /addproxies /proxylist\n"
        "         /proxycheck /proxyclean /testproxy\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=main_menu_keyboard(sess),
    )


async def cmd_dork(update, context):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Usage: /dork inurl:login.php?id=")
        return
    if chat_id in active_jobs and not active_jobs[chat_id].done():
        await update.message.reply_text("⚠️ Job running! /stop first."); return
    dork = " ".join(context.args)
    ok, msg = validate_dork(dork)
    if not ok:
        await update.message.reply_text(f"❌ Invalid: {msg}"); return
    s = get_session(chat_id)
    mode_tag = " ⚡XTREAM" if s.get("xtream") else (" 🛡Y-UB" if s.get("yahoo_ub") else "")
    await update.message.reply_text(
        f"🔍 {dork[:60]}{mode_tag}\n"
        f"📄 Pages: {', '.join(str(p) for p in s.get('pages',[1]))}"
        f"{' 🧅TOR' if s.get('tor') else ''}\n💡 {msg}"
    )
    active_jobs[chat_id] = asyncio.create_task(run_dork_job(chat_id, [dork], context))


async def cmd_xtream(update, context):
    chat_id = update.effective_chat.id
    sess = get_session(chat_id)
    if context.args:
        arg0 = context.args[0].lower()
        if arg0 == "engine" and len(context.args) >= 2:
            engine = context.args[1].lower()
            if engine not in ("yahoo","bing","both"):
                await update.message.reply_text("⚠️ Invalid engine. Use: yahoo|bing|both")
                return
            sess["xtream_engine"] = engine
            labels = {"yahoo":"YAHOO (15 mirrors)","bing":"BING (3 mirrors)",
                      "both":"YAHOO + BING (dual engine)"}
            await update.message.reply_text(f"🎯 XTREAM engine set to: {labels[engine]}")
            return
        elif arg0 in ("on","true","1","enable"):
            sess["xtream"] = True
        elif arg0 in ("off","false","0","disable"):
            sess["xtream"] = False
        else:
            sess["xtream"] = not sess.get("xtream", False)
    else:
        sess["xtream"] = not sess.get("xtream", False)
    engine = sess.get("xtream_engine","yahoo")
    eng_labels = {"yahoo":"YAHOO (15 mirrors)","bing":"BING (3 mirrors)","both":"YAHOO + BING"}
    if sess["xtream"]:
        await update.message.reply_text(
            f"⚡⚡⚡ XTREAM MODE ENABLED ⚡⚡⚡\n{'━'*30}\n"
            f"🎯 Engine     : {eng_labels.get(engine, engine.upper())}\n"
            f"🚀 Target RPS : {XTREAM_TARGET_RPS}/sec\n"
            f"⚙️ Workers    : {XTREAM_WORKERS_PER_CHUNK*XTREAM_CHUNKS} total\n"
            f"📄 Pages/dork : {XTREAM_PAGES_PER_DORK}\n"
            f"🔄 Sessions   : {XTREAM_SESSION_POOL_SIZE} pre-warmed pool\n"
            f"🛡 TLS profiles: {len(TLS_PROFILES)} rotating per-request\n"
            f"{'━'*30}\n"
            f"🔧 Change engine: /xtream engine yahoo|bing|both\n"
            f"💡 Use /dork <q> or upload a .txt to run XTREAM\n"
            f"💡 /xtream off to disable"
        )
    else:
        await update.message.reply_text(f"⏸ XTREAM MODE DISABLED")


async def cmd_yahoo_ub(update, context):
    chat_id = update.effective_chat.id
    sess = get_session(chat_id)
    if context.args:
        a = context.args[0].lower()
        if a in ("on","true","1","enable"):  sess["yahoo_ub"] = True
        elif a in ("off","false","0","disable"): sess["yahoo_ub"] = False
        else: sess["yahoo_ub"] = not sess.get("yahoo_ub", True)
    else:
        sess["yahoo_ub"] = not sess.get("yahoo_ub", True)
    if sess["yahoo_ub"]:
        await update.message.reply_text(
            f"🛡 YAHOO UNBLOCKABLE ENABLED\n{'━'*28}\n"
            f"🔒 {len(TLS_PROFILES)} TLS profiles rotating\n"
            f"🌐 {len(YAHOO_MIRRORS_V2)} mirrors with health tracking\n"
            f"🍪 Cookie-warmed identities ({YAHOO_UB_POOL_SIZE} pool)\n"
            f"⏱ Human timing (Gaussian + bimodal)\n"
            f"🔁 Auto identity rotation on heat signals"
        )
    else:
        await update.message.reply_text("⏸ Yahoo Unblockable DISABLED (using standard fetch)")


async def cmd_dorkcheck(update, context):
    if not context.args:
        await update.message.reply_text(
            "🧠 DORK CHECKER\nUsage: /dorkcheck <dork>\n\n"
            "Example: /dorkcheck inurl:login.php?id= filetype:php")
        return
    dork = " ".join(context.args)
    ok, msg = validate_dork(dork)
    ast = parse_dork(dork)
    normd = normalize_dork(dork)
    lines = [f"🧠 DORK ANALYSIS","━"*22,
             f"📝 Raw   : {dork}", f"✨ Norm  : {normd}",
             f"✅ Status: {'OK' if ok else 'FAIL'} — {msg}",
             f"🔢 Tokens: {len(ast.tokens)}", f"🎯 Operators:"]
    if ast.operators:
        for op, vals in ast.operators.items():
            lines.append(f"   • {op}: {', '.join(vals)}")
    else: lines.append("   (none)")
    if ast.free_terms:
        lines.append(f"🔤 Free terms: {', '.join(ast.free_terms)}")
    lines += ["", "🔁 Engine translations:"]
    for engine in ENGINES:
        translated = translate_dork(dork, engine)
        lines.append(f"   {engine.upper():12s}: {translated[:80]}")
    await update.message.reply_text("\n".join(lines))


async def cmd_mutate(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /mutate <dork> [n=10]"); return
    args = list(context.args); n = 10
    if args[-1].isdigit():
        n = max(1, min(int(args[-1]), 50))
        args = args[:-1]
    dork = " ".join(args)
    variations = mutate_dork(dork, n=n)
    lines = [f"🧬 DORK MUTATIONS ({len(variations)})","━"*22]
    for i, v in enumerate(variations, 1): lines.append(f"{i:>2}. {v}")
    await update.message.reply_text("\n".join(lines))


async def cmd_pages(update, context):
    chat_id = update.effective_chat.id
    sess = get_session(chat_id)
    if not context.args:
        selected = sess.get("pages", [1])
        await update.message.reply_text(
            f"📄 SELECT PAGES (1–70)\n"
            f"Currently: {', '.join(str(p) for p in sorted(selected))}\n\n"
            f"💡 Tip: /pages <N> sets 1–N | /pages 1-10 | /pages 1,3,5",
            reply_markup=page_keyboard(selected))
        return
    raw = " ".join(context.args).strip()
    pages = []
    try:
        if "-" in raw and "," not in raw:
            parts = raw.split("-", 1)
            start = max(1, min(int(parts[0].strip()), 70))
            end   = max(1, min(int(parts[1].strip()), 70))
            if start > end: start, end = end, start
            pages = list(range(start, end+1))
        elif "," in raw:
            pages = sorted(set(max(1, min(int(x.strip()), 70))
                               for x in raw.split(",") if x.strip().isdigit()))
        else:
            n = max(1, min(int(raw), 70))
            pages = list(range(1, n+1))
    except Exception:
        await update.message.reply_text(
            "⚠️ Invalid format.\n"
            "Usage: /pages 5 | /pages 3-10 | /pages 1,3,5 | /pages")
        return
    if not pages: pages = [1]
    sess["pages"] = pages
    label = (f"1–{pages[-1]}" if pages == list(range(1, pages[-1]+1))
             else ", ".join(str(p) for p in pages))
    await update.message.reply_text(f"✅ Pages set: {label}\n📄 Total: {len(pages)}")


async def cmd_tor(update, context):
    global tor_enabled_users
    chat_id = update.effective_chat.id
    sess = get_session(chat_id)
    new_val = (context.args[0].lower()=="on") if context.args and context.args[0].lower() in ("on","off") else not sess.get("tor",False)
    old_val = sess.get("tor", False)
    sess["tor"] = new_val
    if new_val and not old_val:
        tor_enabled_users += 1
        if tor_enabled_users == 1: start_tor_rotation()
        await update.message.reply_text("🧅 TOR ENABLED — rotates every 2 min.")
    elif not new_val and old_val:
        tor_enabled_users = max(0, tor_enabled_users - 1)
        if tor_enabled_users == 0: stop_tor_rotation()
        await update.message.reply_text("🔓 TOR DISABLED.")
    else:
        await update.message.reply_text(f"Tor is already {'ON' if new_val else 'OFF'}.")


async def cmd_filter(update, context):
    chat_id = update.effective_chat.id
    sess = get_session(chat_id)
    try:
        n = max(0, min(int(context.args[0]), 100))
        sess["min_score"] = n
        await update.message.reply_text(f"🛡 SQL Filter: ≥{n}")
    except Exception:
        await update.message.reply_text(
            f"Current: ≥{sess.get('min_score', 30)}\nPick:",
            reply_markup=filter_keyboard())


async def cmd_settings(update, context):
    chat_id = update.effective_chat.id
    s = get_session(chat_id)
    alive = sum(1 for p in _proxy_pool if p["alive"])
    if PROXY_ENABLED and _proxy_pool:
        proxy_line = f"🔄 Proxies  : {alive}/{len(_proxy_pool)} alive\n"
    elif not PROXY_ENABLED and _proxy_pool:
        proxy_line = f"⏸ Proxies  : {len(_proxy_pool)} DISABLED\n"
    else: proxy_line = "🔓 Proxies  : none\n"
    await update.message.reply_text(
        f"⚙️ SETTINGS\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Chunks   : {s.get('chunks', N_CHUNKS)}\n"
        f"🔧 Workers  : {s.get('workers', WORKERS_PER_CHUNK)}/chunk\n"
        f"📄 Pages    : {', '.join(str(p) for p in s.get('pages', [1]))}\n"
        f"🔍 Engines  : {'+'.join(e.upper() for e in s.get('engines', ENGINES))}\n"
        f"📊 Max/Page : {s.get('max_results', MAX_RESULTS)}\n"
        f"🛡 SQL ≥    : {s.get('min_score', 30)}\n"
        f"🧅 Tor      : {'ON' if s.get('tor') else 'OFF'}\n"
        f"⚡ Xtream   : {'ON 🚀' if s.get('xtream') else 'OFF'}\n"
        f"🛡 Y-UB     : {'ON 🛡' if s.get('yahoo_ub') else 'OFF'}\n"
        f"{proxy_line}🔒 TLS pool : {len(TLS_PROFILES)} profiles\n"
        f"━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=main_menu_keyboard(s))


async def cmd_workers(update, context):
    chat_id = update.effective_chat.id
    try:
        n = max(1, min(int(context.args[0]), MAX_WORKERS_PER_CHUNK))
        get_session(chat_id)["workers"] = n
        await update.message.reply_text(f"✅ Workers/chunk: {n}")
    except Exception:
        await update.message.reply_text(f"Usage: /workers N (1-{MAX_WORKERS_PER_CHUNK})")


async def cmd_chunks(update, context):
    chat_id = update.effective_chat.id
    try:
        n = max(1, min(int(context.args[0]), 8))
        get_session(chat_id)["chunks"] = n
        await update.message.reply_text(f"✅ Chunks: {n}")
    except Exception:
        await update.message.reply_text("Usage: /chunks N (1-8)")


async def cmd_maxres(update, context):
    chat_id = update.effective_chat.id
    try:
        n = max(1, min(int(context.args[0]), 50))
        get_session(chat_id)["max_results"] = n
        await update.message.reply_text(f"✅ Max/page: {n}")
    except Exception:
        await update.message.reply_text("Usage: /maxres N (1-50)")


async def cmd_engine(update, context):
    chat_id = update.effective_chat.id
    try:
        choice = context.args[0].lower()
        m = {"bing":["bing"],"yahoo":["yahoo"],"duckduckgo":["duckduckgo"],
             "ddg":["duckduckgo"],"all":list(ENGINES),"both":["bing","yahoo"]}
        engines = m.get(choice, list(ENGINES))
        get_session(chat_id)["engines"] = engines
        await update.message.reply_text(f"✅ Engines: {'+'.join(e.upper() for e in engines)}")
    except Exception:
        await update.message.reply_text("Usage: /engine bing|yahoo|duckduckgo|all")


async def cmd_clean(update, context):
    await update.message.reply_text("🧹 Upload a .txt with URLs (one per line).")


async def cmd_stop(update, context):
    chat_id = update.effective_chat.id
    stop_ev = active_stop_evs.get(chat_id)
    job = active_jobs.get(chat_id)
    if stop_ev and job and not job.done():
        stop_ev.set()
        await update.message.reply_text("⏹ STOP REQUESTED — partial results coming.")
    elif job and not job.done():
        job.cancel(); active_jobs.pop(chat_id, None)
        await update.message.reply_text("🛑 Force-stopped.")
    else:
        await update.message.reply_text("💤 No active job.")


async def cmd_status(update, context):
    chat_id = update.effective_chat.id
    job = active_jobs.get(chat_id)
    sess = get_session(chat_id)
    if sess.get("xtream"):  mode = "⚡ XTREAM (1000 RPS)"
    elif sess.get("yahoo_ub"): mode = "🛡 Yahoo Unblockable"
    else: mode = "🕷 Standard"
    await update.message.reply_text(
        f"{'⚡ Running' if job and not job.done() else '💤 Idle'}\nMode: {mode}")


# ─── PROXY HANDLERS ──────────────────────────────────────────────────────────
_awaiting_bulk_proxy = set()


async def cmd_addproxy(update, context):
    if not context.args:
        await update.message.reply_text(
            "➕ ADD PROXY\nUsage: /addproxy <proxy>\n\n"
            "Formats: ip:port | ip:port:user:pass | scheme://user:pass@host:port")
        return
    line = " ".join(context.args).strip()
    p = parse_proxy_line(line)
    if not p:
        await update.message.reply_text("❌ Invalid format."); return
    key = proxy_key(p)
    async with _proxy_pool_lock:
        if any(proxy_key(x) == key for x in _proxy_pool):
            await update.message.reply_text("⚠️ Already in pool."); return
    wait_msg = await update.message.reply_text(f"🔍 Auto-detecting {p['host']}:{p['port']}...")
    ok = await detect_proxy_protocol(p)
    if not ok:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, message_id=wait_msg.message_id,
            text=f"❌ FAILED\n{p['host']}:{p['port']}\nNot added.")
        return
    async with _proxy_pool_lock:
        _proxy_pool.append(p); _persist_proxies()
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id, message_id=wait_msg.message_id,
        text=(f"✅ ADDED\n🔌 {p['protocol'].upper()}\n"
              f"🌐 {p['host']}:{p['port']}\n"
              f"⏱ {int(p['latency'])} ms\n📦 Pool: {len(_proxy_pool)}"))


async def cmd_addproxies(update, context):
    chat_id = update.effective_chat.id
    _awaiting_bulk_proxy.add(chat_id)
    await update.message.reply_text(
        "📥 BULK PROXY IMPORT\nSend list as NEXT message (one per line).\n"
        "Or upload a .txt file. Auto-detects SOCKS5/4/HTTP/HTTPS.")


async def _bulk_add_proxies(chat_id, lines, context):
    parsed = []; invalid = 0
    for line in lines:
        if not line.strip() or line.startswith("#"): continue
        line = line.split("#", 1)[0].strip()
        if not line: continue
        p = parse_proxy_line(line)
        if p: parsed.append(p)
        else: invalid += 1
    seen_keys = {proxy_key(p) for p in _proxy_pool}
    unique = []; dup_count = 0
    for p in parsed:
        k = proxy_key(p)
        if k in seen_keys: dup_count += 1; continue
        seen_keys.add(k); unique.append(p)
    if not unique:
        await context.bot.send_message(chat_id,
            f"⚠️ Nothing to add.\n❌ Invalid: {invalid}\n🔁 Dup: {dup_count}")
        return
    status_msg = await context.bot.send_message(
        chat_id,
        f"🔍 BULK CHECK\n📥 {len(lines)} | ✅ {len(parsed)} | ❌ {invalid} | 🔁 {dup_count}\n"
        f"🆕 To check: {len(unique)}\n⏳ Auto-detecting...")
    last_edit = [0.0]
    async def _progress(done, total, alive):
        if time.monotonic() - last_edit[0] < 2.5: return
        pct = int(done/total*100) if total else 100
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=status_msg.message_id,
                text=f"🔍 {pct}%\n✅ {done}/{total}\n💚 Alive: {alive}")
            last_edit[0] = time.monotonic()
        except Exception: pass
    alive, dead = await check_proxies_bulk(unique, progress_cb=_progress)
    added = []
    async with _proxy_pool_lock:
        for p in unique:
            if p["alive"]: _proxy_pool.append(p); added.append(p)
        _persist_proxies()
    breakdown = {}
    for p in added: breakdown[p["protocol"]] = breakdown.get(p["protocol"], 0) + 1
    bd = "\n".join(f"   • {k.upper()}: {v}" for k,v in breakdown.items()) or "   (none)"
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=status_msg.message_id,
            text=(f"✅ COMPLETE\n📥 {len(lines)} | ❌ {invalid} | 🔁 {dup_count}\n"
                  f"💀 Dead: {dead}\n💚 Added: {len(added)}\n"
                  f"🔌 Breakdown:\n{bd}\n📦 Pool: {len(_proxy_pool)}"))
    except Exception: pass


async def cmd_proxycheck(update, context):
    if not _proxy_pool:
        await update.message.reply_text("📭 Empty."); return
    status_msg = await update.message.reply_text(f"🔍 Re-checking {len(_proxy_pool)}...")
    last_edit = [0.0]
    async def _progress(done, total, alive):
        if time.monotonic() - last_edit[0] < 2.5: return
        pct = int(done/total*100) if total else 100
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id, message_id=status_msg.message_id,
                text=f"🔍 {pct}%\n✅ {done}/{total}\n💚 Alive: {alive}")
            last_edit[0] = time.monotonic()
        except Exception: pass
    alive, dead = await check_proxies_bulk(list(_proxy_pool), progress_cb=_progress)
    _persist_proxies()
    try:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, message_id=status_msg.message_id,
            text=f"✅ DONE\n📦 {len(_proxy_pool)} | 💚 {alive} | 💀 {dead}")
    except Exception: pass


async def cmd_proxyclean(update, context):
    async with _proxy_pool_lock:
        before = len(_proxy_pool)
        _proxy_pool[:] = [p for p in _proxy_pool if p["alive"]]
        removed = before - len(_proxy_pool)
        _persist_proxies()
    await update.message.reply_text(f"🧹 Removed {removed}\n💚 Remaining: {len(_proxy_pool)}")


async def cmd_removeproxy(update, context):
    if not context.args:
        if not _proxy_pool:
            await update.message.reply_text("📭 Empty."); return
        lines = ["📋 POOL"]
        for i, p in enumerate(_proxy_pool, start=1):
            mark = "💚" if p["alive"] else "💀"
            lines.append(f"{i:>2}. {mark} {proxy_display(p)}")
        lines.append("\n/removeproxy <index>")
        await update.message.reply_text("\n".join(lines)); return
    arg = context.args[0].strip()
    async with _proxy_pool_lock:
        try:
            idx = int(arg) - 1
            if not (0 <= idx < len(_proxy_pool)):
                await update.message.reply_text(f"❌ Range 1-{len(_proxy_pool)}"); return
            removed = _proxy_pool.pop(idx); _persist_proxies()
            await update.message.reply_text(f"🗑 {proxy_display(removed)}"); return
        except ValueError: pass
        for i, p in enumerate(_proxy_pool):
            if f"{p['host']}:{p['port']}" == arg or p.get("url") == arg:
                _proxy_pool.pop(i); _persist_proxies()
                await update.message.reply_text(f"🗑 {arg}"); return
    await update.message.reply_text("❌ Not found.")


async def cmd_proxylist(update, context):
    if not _proxy_pool:
        await update.message.reply_text("📭 Empty.\nUse /addproxy or /addproxies.")
        return
    alive = sum(1 for p in _proxy_pool if p["alive"])
    breakdown = {}
    for p in _proxy_pool:
        k = (p["protocol"] or "?").upper()
        breakdown[k] = breakdown.get(k, 0) + 1
    lines = [f"🔄 POOL — {len(_proxy_pool)} ({alive} alive)",
             "📊 " + ", ".join(f"{k}:{v}" for k,v in breakdown.items()),
             "━"*22]
    for i, p in enumerate(_proxy_pool[:50], start=1):
        mark = "💚" if p["alive"] else "💀"
        lat = f"{int(p['latency'])}ms" if p.get("latency") else "—"
        lines.append(f"{i:>2}. {mark} {proxy_display(p)}  {lat}")
    if len(_proxy_pool) > 50: lines.append(f"… +{len(_proxy_pool)-50}")
    await update.message.reply_text("\n".join(lines))


async def cmd_testproxy(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /testproxy <line>"); return
    line = " ".join(context.args).strip()
    p = parse_proxy_line(line)
    if not p:
        await update.message.reply_text("❌ Invalid."); return
    wait = await update.message.reply_text(f"🧪 Testing {p['host']}:{p['port']}...")
    ok = await detect_proxy_protocol(p)
    if ok:
        msg = (f"✅ WORKS\n🔌 {p['protocol'].upper()}\n"
               f"🌐 {p['host']}:{p['port']}\n⏱ {int(p['latency'])} ms")
    else:
        msg = f"❌ FAILED\n{p['host']}:{p['port']}"
    try:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, message_id=wait.message_id, text=msg)
    except Exception:
        await update.message.reply_text(msg)


# ─── FILE DETECTION ──────────────────────────────────────────────────────────
def _looks_like_url_list(lines):
    non_empty = [l for l in lines if l.strip() and not l.startswith("#")]
    if not non_empty: return False
    return sum(1 for l in non_empty if l.strip().startswith("http")) / len(non_empty) >= 0.5


def _looks_like_proxy_list(lines):
    non_empty = [l for l in lines if l.strip() and not l.startswith("#")]
    if not non_empty: return False
    proxy_count = sum(1 for l in non_empty if parse_proxy_line(l.split("#",1)[0].strip()))
    return proxy_count / len(non_empty) >= 0.6


async def handle_document(update, context):
    chat_id = update.effective_chat.id
    doc = update.message.document
    if chat_id in active_jobs and not active_jobs[chat_id].done():
        await update.message.reply_text("⚠️ Job running! /stop first."); return
    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("❌ Send a .txt file."); return
    await update.message.reply_text("📥 Reading...")
    try:
        content = await (await context.bot.get_file(doc.file_id)).download_as_bytearray()
        lines = content.decode("utf-8", errors="replace").splitlines()
        if _looks_like_proxy_list(lines):
            await update.message.reply_text(f"🔄 PROXY LIST — {len(lines)} lines\n🚀 Checking...")
            await _bulk_add_proxies(chat_id, lines, context); return
        if _looks_like_url_list(lines):
            raw_urls = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
            if not raw_urls:
                await update.message.reply_text("❌ No URLs."); return
            await update.message.reply_text(f"🧹 URL LIST — {len(raw_urls)}")
            active_jobs[chat_id] = asyncio.create_task(run_url_clean_job(chat_id, raw_urls, context))
        else:
            dorks = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
            if not dorks:
                await update.message.reply_text("❌ No dorks."); return
            s = get_session(chat_id)
            mode_tag = " ⚡XTREAM" if s.get("xtream") else (" 🛡Y-UB" if s.get("yahoo_ub") else "")
            await update.message.reply_text(
                f"✅ {len(dorks)} dorks{mode_tag} | Pages: {', '.join(str(p) for p in s.get('pages',[1]))}\n🚀 Starting...")
            active_jobs[chat_id] = asyncio.create_task(run_dork_job(chat_id, dorks, context))
    except Exception as exc:
        await update.message.reply_text(f"❌ Error: {exc}")


async def handle_text(update, context):
    chat_id = update.effective_chat.id
    if chat_id in _awaiting_bulk_proxy:
        _awaiting_bulk_proxy.discard(chat_id)
        lines = update.message.text.splitlines()
        if not lines:
            await update.message.reply_text("❌ No lines."); return
        await _bulk_add_proxies(chat_id, lines, context); return
    lines = [l.strip() for l in update.message.text.splitlines()
             if l.strip() and not l.startswith("#")]
    if len(lines) > 1:
        if chat_id in active_jobs and not active_jobs[chat_id].done():
            await update.message.reply_text("⚠️ Job running! /stop first."); return
        s = get_session(chat_id)
        mode_tag = " ⚡XTREAM" if s.get("xtream") else (" 🛡Y-UB" if s.get("yahoo_ub") else "")
        await update.message.reply_text(f"✅ {len(lines)} dorks{mode_tag}\n🚀 Starting...")
        active_jobs[chat_id] = asyncio.create_task(run_dork_job(chat_id, lines, context))
    else:
        await update.message.reply_text(
            "Use /dork <q> or upload .txt\n"
            "/xtream — 1000 RPS mode | /yahoo_ub — unblockable Yahoo\n"
            "/dorkcheck — validate | /mutate — variations")


# ══════════════════════════════════════════════════════════════════════════════
# ─── CALLBACK HANDLER ────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

async def handle_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    sess = get_session(chat_id)

    if data.startswith("pg_"):
        cmd = data[3:]
        selected = list(sess.get("pages", [1]))
        if cmd == "all":     selected = list(range(1, 71))
        elif cmd == "clear": selected = []
        elif cmd == "confirm":
            sess["pages"] = selected or [1]
            try:
                await query.edit_message_text(
                    f"✅ Pages saved: {', '.join(str(p) for p in sorted(sess['pages']))}")
            except Exception: pass
            return
        else:
            try:
                p = int(cmd)
                if p in selected: selected.remove(p)
                else: selected.append(p)
                selected = sorted(set(selected))
            except ValueError: pass
        sess["pages"] = selected
        try:
            await query.edit_message_text(
                f"📄 SELECT PAGES\nSelected: {', '.join(str(p) for p in selected) or 'none'}",
                reply_markup=page_keyboard(selected))
        except Exception: pass
        return

    if data.startswith("f_"):
        try:
            n = int(data[2:])
            sess["min_score"] = n
            await query.edit_message_text(
                f"🛡 SQL Filter set: ≥{n}", reply_markup=main_menu_keyboard(sess))
        except (ValueError, Exception): pass
        return

    if data == "m_bulk":
        try:
            await query.edit_message_text(
                "📂 BULK UPLOAD\n━━━━━━━━━━━━━━━\n"
                "Send a .txt file. Auto-detected:\n"
                "  • Dork list → run search\n"
                "  • URL list  → run cleaner\n"
                "  • Proxy list → import to pool\n\n"
                "Or paste multiple lines directly in chat.",
                reply_markup=main_menu_keyboard(sess))
        except Exception: pass
        return

    if data == "m_single":
        try:
            await query.edit_message_text(
                "🔍 SINGLE DORK SEARCH\n━━━━━━━━━━━━━━━\n"
                "Usage: /dork <query>\n\n"
                "Examples:\n"
                "  /dork inurl:login.php?id=\n"
                "  /dork intitle:\"index of\" filetype:php\n",
                reply_markup=main_menu_keyboard(sess))
        except Exception: pass
        return

    if data == "m_pages":
        try:
            await query.edit_message_text(
                f"📄 SELECT PAGES (1–70)\nCurrently: {', '.join(str(p) for p in sess.get('pages', [1]))}",
                reply_markup=page_keyboard(sess.get("pages", [1])))
        except Exception: pass
        return

    if data == "m_settings":
        alive = sum(1 for p in _proxy_pool if p["alive"])
        try:
            await query.edit_message_text(
                f"⚙️ SETTINGS\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ Chunks   : {sess.get('chunks', N_CHUNKS)}\n"
                f"🔧 Workers  : {sess.get('workers', WORKERS_PER_CHUNK)}/chunk\n"
                f"📄 Pages    : {', '.join(str(p) for p in sess.get('pages',[1]))}\n"
                f"🔍 Engines  : {'+'.join(e.upper() for e in sess.get('engines',ENGINES))}\n"
                f"📊 Max/page : {sess.get('max_results', MAX_RESULTS)}\n"
                f"🛡 SQL ≥    : {sess.get('min_score', 30)}\n"
                f"🧅 Tor      : {'ON' if sess.get('tor') else 'OFF'}\n"
                f"⚡ Xtream   : {'ON 🚀' if sess.get('xtream') else 'OFF'}\n"
                f"🛡 Y-UB     : {'ON 🛡' if sess.get('yahoo_ub') else 'OFF'}\n"
                f"🔄 Proxies  : {alive}/{len(_proxy_pool)} alive\n"
                f"🔒 TLS pool : {len(TLS_PROFILES)} profiles\n"
                f"━━━━━━━━━━━━━━━━━━━━━━",
                reply_markup=main_menu_keyboard(sess))
        except Exception: pass
        return

    if data == "m_tor":
        global tor_enabled_users
        old_val = sess.get("tor", False)
        sess["tor"] = not old_val
        if sess["tor"] and not old_val:
            tor_enabled_users += 1
            if tor_enabled_users == 1: start_tor_rotation()
        elif not sess["tor"] and old_val:
            tor_enabled_users = max(0, tor_enabled_users - 1)
            if tor_enabled_users == 0: stop_tor_rotation()
        try:
            await query.edit_message_text(
                f"🧅 TOR {'ENABLED — rotates every 2 min' if sess['tor'] else 'DISABLED'}",
                reply_markup=main_menu_keyboard(sess))
        except Exception: pass
        return

    if data == "m_xtream":
        sess["xtream"] = not sess.get("xtream", False)
        msg = (f"⚡ XTREAM ENABLED" if sess["xtream"] else "⏸ XTREAM DISABLED")
        try:
            await query.edit_message_text(msg, reply_markup=main_menu_keyboard(sess))
        except Exception: pass
        return

    if__name__ == "__main__":
    main()
