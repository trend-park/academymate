# -*- coding: utf-8 -*-
"""
네이버 통합검색 노출 순위 체커

config.json 에 적은 업체별로 키워드를 검색해서,
통합검색 첫 화면(광고·플레이스·뉴스·이미지 제외) 몇 위에 그 업체 블로그 글이
있는지 기록하고, 업체별 순위판 HTML 을 만듭니다.

실행:  python rank_check.py
옵션:
  --only "키워드"   그 키워드 하나만 상세 목록으로 확인 (기록 저장 안 함)
  --biz  "업체이름"  그 업체만 실행
  --browser / --fast  수집 방식 강제
  --html-only        수집 없이 순위판만 다시 만들기
  --debug            찾은 문서 목록을 전부 출력
  --save-html        수집한 검색결과 원본을 raw 폴더에 저장
"""

import csv
import json
import os
import re
import sys
import time
import html as html_mod
from datetime import datetime
from urllib.parse import quote, unquote

try:
    import requests
except ImportError:
    print("[설치 필요] requests 모듈이 없습니다. 명령 창에서 pip install requests beautifulsoup4 를 먼저 실행해 주세요.")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("[설치 필요] beautifulsoup4 모듈이 없습니다. 명령 창에서 pip install requests beautifulsoup4 를 먼저 실행해 주세요.")
    sys.exit(1)

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")


def safe_name(s):
    return re.sub(r"[^\w가-힣 .-]", "_", str(s)).strip() or "업체"


def csv_path(biz):
    return os.path.join(DATA_DIR, f"{biz['slug']}.csv")


def inq_path(biz):
    return os.path.join(DATA_DIR, f"{biz['slug']}_문의량.csv")


def board_path(biz):
    return os.path.join(BASE, f"네이버상위노출체크_{biz['slug']}.html")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# 통합검색은 15위까지, 플레이스는 50위까지만 '노출' 로 보고 그 밖은 '밖' 으로 적습니다.
TOP_RANK = 15
PLACE_TOP_RANK = 50

CSV_HEADER = [
    "수집일", "수집시각", "키워드", "블로그ID", "노출여부",
    "전체순위", "전체순위목록", "블로그순위", "영역순번", "영역명", "영역내순위",
    "노출건수", "대표제목", "대표URL", "전체문서총수", "블로그문서총수", "수집방식",
]

# 광고/비검색 영역으로 취급할 제목 패턴
AD_TITLE = re.compile(r"(파워링크|비즈사이트|파워컨텐츠|파워콘텐츠|브랜드검색|광고|쇼핑|네이버쇼핑|플레이스광고)")
# 블로그 URL 이 아닌 예약 경로
RESERVED = {
    "postview", "postlist", "prologue", "guestbook", "blogview",
    "postthumbnaillist", "rss", "sectionsub", "mylog", "recommendation",
    # 네이버 상단 메뉴·기능 링크 (검색결과가 아님)
    "myblog", "bloghome", "blogtop", "blogtagview", "thumbnaillist",
    "category", "widget", "sympathyhistorylist", "nblogtop", "postwrite",
    "notifier", "buddy", "neighbor", "followlist", "blogpeople", "mytopic",
}


# ---------------------------------------------------------------- 설정 읽기
def load_config():
    path = os.path.join(BASE, "config.json")
    if not os.path.exists(path):
        print("[오류] config.json 이 없습니다.")
        sys.exit(1)
    with open(path, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    raw = cfg.get("업체")
    if not raw:                       # 업체가 1곳인 예전 형식도 그대로 읽습니다.
        raw = [{"이름": cfg.get("블로그이름", ""), "블로그ID": cfg.get("블로그ID", ""),
                "추가블로그ID": cfg.get("추가블로그ID", []), "키워드": cfg.get("키워드", [])}]

    biz_list, slugs = [], set()
    for i, b in enumerate(raw, start=1):
        ids = [str(b.get("블로그ID", "")).strip()]
        ids += [str(x).strip() for x in b.get("추가블로그ID", []) or []]
        ids = [x.lower() for x in ids if x]
        kws = [str(k).strip() for k in b.get("키워드", []) or [] if str(k).strip()]
        name = str(b.get("이름", "")).strip() or (ids[0] if ids else f"업체{i}")
        pkws = [str(k).strip() for k in b.get("플레이스키워드", []) or [] if str(k).strip()]
        pid = parse_place_id(b.get("플레이스URL") or b.get("플레이스ID") or "")

        if not ids and not (pid or pkws):
            print(f"[건너뜀] {name}: '블로그ID' 가 비어 있습니다.")
            continue
        if not kws and not pkws:
            print(f"[건너뜀] {name}: '키워드' 가 비어 있습니다.")
            continue

        slug = safe_name(name)
        while slug in slugs:
            slug += "_2"
        slugs.add(slug)
        biz_list.append({"이름": name, "ids": ids, "키워드": kws, "slug": slug,
                         "플레이스키워드": pkws, "place_id": pid,
                         "place_name": str(b.get("플레이스명", "")).strip()})

    if not biz_list:
        print("[오류] config.json 에 '블로그ID' 와 '키워드' 가 채워진 업체가 없습니다.")
        print("       예) \"블로그ID\": \"jungangsbi\"  ← blog.naver.com/jungangsbi 의 뒷부분")
        sys.exit(1)

    cfg["_biz"] = biz_list
    return cfg


# ---------------------------------------------------------------- 페이지 수집
def search_url(keyword):
    return ("https://search.naver.com/search.naver?"
            "where=nexearch&sm=top_hty&fbm=0&ie=utf8&query=" + quote(keyword))


def fetch_fast(keyword):
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Referer": "https://www.naver.com/",
        "Upgrade-Insecure-Requests": "1",
    }
    r = requests.get(search_url(keyword), headers=headers, timeout=20)
    r.encoding = "utf-8"
    return r.text


_PW = {"pw": None, "browser": None}


def fetch_browser(keyword):
    """Playwright 로 실제 브라우저처럼 열어 렌더링된 HTML 을 가져옵니다."""
    from playwright.sync_api import sync_playwright

    if _PW["browser"] is None:
        _PW["pw"] = sync_playwright().start()
        _PW["browser"] = _PW["pw"].chromium.launch(headless=True)

    ctx = _PW["browser"].new_context(user_agent=UA, locale="ko-KR",
                                     viewport={"width": 1440, "height": 1000})
    page = ctx.new_page()
    try:
        page.goto(search_url(keyword), wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1800)
        for _ in range(4):                      # 지연 로딩 영역까지 펼치기
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(500)
        page.wait_for_timeout(700)
        return page.content()
    finally:
        page.close()
        ctx.close()


def close_browser():
    if _PW["browser"] is not None:
        try:
            _PW["browser"].close()
            _PW["pw"].stop()
        except Exception:
            pass
        _PW["browser"] = None


def browser_ready():
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------- 플레이스
PLACE_CSV_HEADER = [
    "수집일", "수집시각", "키워드", "플레이스ID", "업체명",
    "노출여부", "순위", "목록총수", "수집방식",
]
PLACE_LIST_URL = "https://pcmap.place.naver.com/place/list?query={}"
# 목록 항목의 업체 링크에서 플레이스ID 를 뽑는 패턴
PLACE_ID_PAT = [
    re.compile(r"/place/(\d{5,})"),
    re.compile(r"place\.naver\.com/[a-z]+/(\d{5,})", re.I),
    re.compile(r"/(\d{8,})(?:/home|/menu|/review|[?/]|$)"),
]


def place_csv_path(biz):
    return os.path.join(DATA_DIR, f"{biz['slug']}_플레이스.csv")


def parse_place_id(text):
    """플레이스 주소나 ID 문자열에서 숫자 ID 만 뽑습니다."""
    s = str(text or "").strip()
    if not s:
        return ""
    if s.isdigit():
        return s
    s = unquote(html_mod.unescape(s))
    for pat in PLACE_ID_PAT:
        m = pat.search(s)
        if m:
            return m.group(1)
    return ""


EXTRACT_JS = """() => {
    const out = [], seen = new Set();
    // 1) 페이지가 들고 있는 검색결과 데이터에서 바로 뽑기 (가장 정확)
    try {
        const st = window.__APOLLO_STATE__;
        if (st) {
            const refs = [];
            const walk = o => {
                if (!o || typeof o !== 'object') return;
                if (Array.isArray(o)) { o.forEach(walk); return; }
                if (typeof o.__ref === 'string') refs.push(o.__ref);
                Object.values(o).forEach(walk);
            };
            walk(st.ROOT_QUERY || st);
            for (const r of refs) {
                const e = st[r];
                if (!e || !e.name) continue;
                const id = String(e.id || r.split(':').pop() || '');
                const key = id || e.name;
                if (seen.has(key)) continue;
                seen.add(key);
                out.push({id: id, name: String(e.name).slice(0, 60), via: 'data'});
            }
            if (out.length) return out;
        }
    } catch (err) {}
    // 2) 화면 목록에서 순서대로 뽑기
    const lists = document.querySelectorAll('ul');
    let best = null, bestN = 0;
    lists.forEach(ul => {
        const n = ul.querySelectorAll(':scope > li').length;
        if (n > bestN) { bestN = n; best = ul; }
    });
    const lis = best ? best.querySelectorAll(':scope > li') : document.querySelectorAll('li');
    lis.forEach(li => {
        let cand = null;
        for (const s of ['a[role="button"] span', '.place_bluelink', 'a span', 'span', 'a']) {
            cand = li.querySelector(s);
            if (cand && (cand.innerText || cand.textContent || '').trim().length > 1) break;
            cand = null;
        }
        let name = ((cand && (cand.innerText || cand.textContent)) || '').trim();
        if (!name) name = (li.innerText || '').trim();
        name = name.split('\\n')[0].trim().slice(0, 60);
        if (name.length < 2 || name.length > 60) return;
        if (/^(운영|현재 위치|출발|도착|거리뷰|예약|이전페이지|다음페이지|광고)/.test(name)) return;
        let id = '';
        for (const a of li.querySelectorAll('a[href]')) {
            const m = (a.getAttribute('href') || '').match(/\\/(\\d{6,})/);
            if (m) { id = m[1]; break; }
        }
        const key = id || name;
        if (seen.has(key)) return;
        seen.add(key);
        out.push({id: id, name: name, via: 'dom'});
    });
    if (out.length) return out;

    // 3) 화면 글자만 읽어 업체 블록으로 나누기 (링크도 목록 구조도 없을 때)
    const SKIP = /^(운영 중|운영 종료|영업|오늘 휴무|현재 위치에서|상세주소|알림받기|휠체어|쿠폰|톡톡|리뷰|블로그리뷰|방문자리뷰|저장|공유|전화|길찾기|내 업체|플레이스)/;
    const CUT  = /^(출발|도착|거리뷰|예약|길찾기|공유|저장|전화)+$/;
    let cur = null;
    for (let ln of (document.body.innerText || '').split('\\n')) {
        ln = ln.trim();
        if (!ln) continue;
        if (/^(이전페이지|다음페이지|더보기)/.test(ln)) break;
        if (CUT.test(ln)) { cur = null; continue; }
        if (SKIP.test(ln)) continue;
        if (cur) continue;
        cur = ln.slice(0, 60);
        if (cur.length < 2 || seen.has(cur)) { continue; }
        seen.add(cur);
        out.push({id: '', name: cur, via: 'text'});
    }
    return out;
}"""

SCROLL_JS = """() => {
    const sel = ['#_pcmap_list_scroll_container', '.Ryr1F', '#_list_scroll_container',
                 '[class*=scroll_container]', '[role=main]'];
    for (const s of sel) {
        const el = document.querySelector(s);
        if (el && el.scrollHeight > el.clientHeight) { el.scrollTop = el.scrollHeight; return s; }
    }
    (document.scrollingElement || document.body).scrollTop = 99999;
    window.scrollTo(0, document.body.scrollHeight);
    return 'window';
}"""


NEXT_PAGE_JS = """() => {
    const els = [...document.querySelectorAll('a, button, span[role=button]')];
    const b = els.find(x => (x.innerText || '').trim().includes('다음페이지'));
    if (!b) return false;
    if (b.getAttribute('aria-disabled') === 'true' || b.disabled) return false;
    b.click();
    return true;
}"""


def _collect_pages(target, page, max_rank, max_pages=8):
    """한 페이지씩 읽고 '다음페이지' 를 눌러가며 목록을 모읍니다.
    (플레이스 목록은 스크롤이 아니라 페이지 넘김 방식입니다)"""
    items, seen = [], set()

    def grab():
        try:
            target.evaluate(SCROLL_JS)
        except Exception:
            pass
        page.wait_for_timeout(600)
        try:
            return target.evaluate(EXTRACT_JS) or []
        except Exception:
            return []

    for p in range(max_pages):
        got = grab()
        if not got and p == 0:              # 첫 화면이 늦게 그려지는 경우 한 번 더
            page.wait_for_timeout(1800)
            got = grab()
        new = 0
        for it in got:
            key = it.get("id") or it.get("name")
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(it)
            new += 1
        if len(items) >= max_rank or new == 0:
            break
        try:
            if not target.evaluate(NEXT_PAGE_JS):
                break
        except Exception:
            break
        page.wait_for_timeout(1600)

    return items[:max_rank]


def fetch_place_page(keyword, max_rank=50, log=None):
    """플레이스 목록을 세 가지 경로로 시도합니다.
    네이버가 주소 체계를 바꿔도 하나는 걸리도록 대비한 것입니다."""
    from playwright.sync_api import sync_playwright

    if _PW["browser"] is None:
        _PW["pw"] = sync_playwright().start()
        _PW["browser"] = _PW["pw"].chromium.launch(headless=True)

    q = quote(keyword)
    attempts = [
        ("지도 검색(iframe)", f"https://map.naver.com/p/search/{q}", True),
        ("플레이스 목록", f"https://pcmap.place.naver.com/place/list?query={q}", False),
        ("모바일 플레이스", f"https://m.place.naver.com/search?query={q}", False),
    ]
    best_items, best_way = [], ""

    for way, url, use_iframe in attempts:
        ctx = _PW["browser"].new_context(
            user_agent=UA, locale="ko-KR",
            viewport={"width": 1400, "height": 1000},
            extra_http_headers={"Referer": "https://map.naver.com/"})
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            target = page
            if use_iframe:
                fr = None
                for _ in range(10):
                    fr = next((f for f in page.frames
                               if "pcmap" in (f.url or "") or "searchIframe" in (f.name or "")), None)
                    if fr:
                        break
                    page.wait_for_timeout(700)
                if fr is None:
                    if log:
                        log(f"       · {way}: 목록 영역을 못 찾음")
                    continue
                target = fr

            items = _collect_pages(target, page, max_rank)
            via = items[0].get("via", "") if items else ""
            if log:
                log(f"       · {way}: 업체 {len(items)}곳{' (' + via + ')' if via else ''}")
            if len(items) > len(best_items):
                best_items, best_way = items, way
            if len(items) >= 10:
                return best_items, best_way
        except Exception as e:
            if log:
                log(f"       · {way}: 실패 ({str(e)[:60]})")
        finally:
            page.close()
            ctx.close()

    return best_items, best_way


def parse_place_list(page_html, diag=None):
    """플레이스 목록 HTML → [{'id':…, 'name':…}] 을 화면 순서대로."""
    soup = BeautifulSoup(page_html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    seen, items = set(), []
    for a in soup.find_all("a", href=True):
        pid = parse_place_id(a["href"])
        if not pid or pid in seen:
            continue
        name = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        if len(name) < 2 or len(name) > 60:
            p = a.find_parent(["li", "div"])
            if p:
                cand = re.sub(r"\s+", " ", p.get_text(" ", strip=True))
                name = cand[:40] if cand else name
        seen.add(pid)
        items.append({"id": pid, "name": name[:60]})

    if diag is not None:
        diag.update({"페이지길이": len(page_html),
                     "페이지제목": (soup.title.get_text(strip=True)[:50] if soup.title else "(없음)"),
                     "업체링크": len(items)})
    return items


def norm_name(s):
    return re.sub(r"[\s()\[\]·.,'\"-]", "", str(s or "")).lower()


def match_place(items, my_id, my_name, top):
    """목록에서 내 업체를 찾아 (순위, 항목) 을 돌려줍니다. 못 찾으면 (0, None)."""
    target = norm_name(my_name)
    for n, it in enumerate(items[:top], start=1):
        if my_id and it.get("id") and str(it["id"]) == str(my_id):
            return n, it
    if not target:
        return 0, None
    for n, it in enumerate(items[:top], start=1):     # 이름이 서로 포함되면 같은 업체로
        nm = norm_name(it.get("name"))
        if nm and (target in nm or nm in target):
            return n, it
    return 0, None


def run_place(cfg, biz, log=print, debug=False, only=None):
    """플레이스 목록에서 이 업체가 몇 위인지 키워드별로 확인합니다."""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%H:%M:%S")
    my_id = biz.get("place_id", "")
    my_name = (biz.get("place_name") or biz["이름"]).strip()
    keywords = [only] if only else biz.get("플레이스키워드", [])
    gap = float(cfg.get("검색간격초", 4))
    top = int(cfg.get("플레이스최대순위", PLACE_TOP_RANK))
    rows = []

    if not keywords:
        return rows
    if not browser_ready():
        log("[안내] 플레이스 순위는 브라우저 방식이 필요합니다. [지금 설치] 를 먼저 눌러 주세요.")
        return rows
    if not my_id:
        log(f"    (플레이스 주소가 비어 있어 업체명 \"{my_name}\" 으로 찾습니다. "
            f"[설정] 탭에 지도 주소를 넣으면 더 정확합니다)")

    for i, kw in enumerate(keywords, start=1):
        try:
            items, way = fetch_place_page(kw, top, log=(log if (debug or i == 1) else None))
        except Exception as e:
            log(f"  [{i}/{len(keywords)}] {kw} → 수집 실패: {str(e)[:80]}")
            continue

        rank, found = match_place(items, my_id, my_name, top)

        if debug or (not rank and i == 1):
            log("  " + "-" * 58)
            log(f"  [플레이스 목록] {kw} — {len(items)}곳 ({way})")
            for n, it in enumerate(items[:15], start=1):
                mark = "  ★내업체" if found and it is found else ""
                log(f"    {n:>3}위 {str(it.get('id') or '-'):<12} {it.get('name', '')[:30]}{mark}")
            if not rank and items:
                log(f"    → 이 목록에서 \"{my_name}\" 을 못 찾았습니다. "
                    f"위 이름과 다르면 [설정] 탭의 업체명이나 지도 주소를 맞춰 주세요.")
            log("  " + "-" * 58)

        rows.append({
            "수집일": today, "수집시각": now, "키워드": kw,
            "플레이스ID": (found or {}).get("id", my_id),
            "업체명": (found or {}).get("name", my_name),
            "노출여부": "노출" if rank else "밖",
            "순위": rank or "", "목록총수": len(items), "수집방식": "브라우저",
        })
        if rank:
            log(f"  [{i}/{len(keywords)}] {kw} → 플레이스 {rank}위 (목록 {len(items)}곳 중)")
        elif items:
            log(f"  [{i}/{len(keywords)}] {kw} → {top}위 밖 (목록 {len(items)}곳 확인)")
        else:
            log(f"  [{i}/{len(keywords)}] {kw} → 목록을 못 읽었습니다")

        if i < len(keywords):
            time.sleep(gap)

    close_browser()
    return rows


def append_place_rows(biz, rows):
    os.makedirs(DATA_DIR, exist_ok=True)
    old = read_place_rows(biz)
    with open(place_csv_path(biz), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=PLACE_CSV_HEADER, extrasaction="ignore", restval="")
        w.writeheader()
        for r in old + rows:
            w.writerow(r)


def read_place_rows(biz):
    p = place_csv_path(biz)
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- 파싱
def parse_blog_link(href):
    """네이버 블로그 링크에서 (블로그ID, 글번호) 를 뽑습니다. 아니면 None."""
    if not href:
        return None
    u = unquote(html_mod.unescape(href))
    if "blog.naver.com" not in u.lower():
        return None

    m = re.search(r"blogId=([A-Za-z0-9_\-]+)", u)
    n = re.search(r"logNo=(\d+)", u)
    if m:
        if m.group(1).lower() in RESERVED:
            return None
        return m.group(1).lower(), (n.group(1) if n else None)

    m = re.search(r"blog\.naver\.com/([A-Za-z0-9_\-]+)(?:/(\d+))?", u)
    if not m:
        return None
    bid = m.group(1)
    if bid.lower() in RESERVED:
        return None
    return bid.lower(), m.group(2)


# ----- 통합검색 '문서' 판정 -------------------------------------------------
# 순위에서 빼는 네이버 기능·비문서 영역 (광고 / 플레이스 / 뉴스 / 이미지 / 동영상 등)
SKIP_HOST = re.compile(
    r"^(?:m\.)?(?:search|place|map|news|n\.news|entertain|sports|tv|shopping|smartstore|"
    r"brand|dict|ko\.dict|en\.dict|terms|kin\.search|image|nid|help|weather|order|pay|talk|"
    r"mail|cafe\.search|adcr|ader|veta|siape|ssl|static|pstatic|comic|series|book|"
    r"finance|land|auto|game|kids|campaign|event|clip|clova|contents|vibe|now|band)\.",
    re.I)
# 검색결과 문서가 아닌 외부 도메인 (지도·SNS 위젯·정책 페이지 등)
SKIP_WEB = re.compile(
    r"(openstreetmap|google\.com/maps|instagram\.com$|facebook\.com$|youtube\.com$|"
    r"youtu\.be$|twitter\.com$|x\.com$|tiktok\.com$|apple\.com$|microsoft\.com$|"
    r"whatap|wcs\.naver|doubleclick|googletagmanager|gstatic|navercorp|naver\.me)", re.I)
# 순위에서 빼는 검색 영역 (제목으로 판정)
SKIP_SECTION = re.compile(
    r"(플레이스|지도|길찾기|업체 ?등록|신규장소|뉴스|이미지|동영상|클립|쇼핑|지식백과|"
    r"어학사전|사전|연관 ?검색어|함께 ?많이 ?찾는|자동완성|인기주제|실시간|날씨|증권|"
    r"파워링크|비즈사이트|브랜드검색|파워컨텐츠|파워콘텐츠|관련 ?광고|광고$|AI ?브리핑)")
KIND_NAME = {"blog": "블로그", "cafe": "카페", "kin": "지식iN",
             "in": "인플루언서", "post": "포스트", "web": "웹사이트"}


def parse_doc_link(href):
    """검색결과 '문서' 링크면 (종류, 중복제거키, 블로그ID) 를 돌려줍니다. 아니면 None.

    네이버는 결과 링크를 search.naver.com/p/crd/rd?...&u=<진짜주소> 처럼 감싸므로
    주소를 풀어 쓴 문자열 전체를 보고 판정합니다.
    """
    if not href:
        return None
    u = unquote(unquote(html_mod.unescape(href))).strip()
    if not u:
        return None

    # 1) 네이버 블로그 (가장 먼저)
    b = parse_blog_link(u)
    if b:
        return "blog", f"blog:{b[0]}:{b[1] or 'home'}", b[0]

    low = u.lower()

    # 2) 그 밖의 네이버 문서형 서비스
    c = re.search(r"cafe\.naver\.com/(?:ca-fe/cafes/\d+/articles/(\d+)|([^/?#&]+)(?:/(\d+))?)", low)
    if c:
        key = c.group(1) or f"{c.group(2)}:{c.group(3) or 'home'}"
        return "cafe", f"cafe:{key}", None
    if "kin.naver.com" in low:
        k = re.search(r"docid=(\d+)", low) or re.search(r"kin\.naver\.com\S*?/(\d{6,})", low)
        return "kin", f"kin:{k.group(1) if k else low[-40:]}", None
    if "in.naver.com" in low:
        k = re.search(r"in\.naver\.com/([^/?#&]+)(?:/contents/status/(\d+))?", low)
        return "in", f"in:{k.group(1)}:{(k.group(2) or 'home') if k else ''}", None
    if "post.naver.com" in low:
        k = re.search(r"volumeno=(\d+)", low)
        return "post", f"post:{k.group(1) if k else low[-40:]}", None

    # 3) 외부 웹사이트 — 감싼 주소 안의 '마지막' 진짜 주소를 목적지로 봅니다.
    starts = [mm.start() for mm in re.finditer(r"https?://", u, re.I)]
    if not starts:
        return None
    dest = u[starts[-1]:]
    m = re.match(r"https?://([^/?#&\s]+)", dest, re.I)
    if not m:
        return None
    host = m.group(1).lower().replace("www.", "")

    if (SKIP_HOST.search(host) or host.endswith("naver.net") or host.endswith("pstatic.net")
            or host.endswith("naver.com") or SKIP_WEB.search(host)):
        return None
    return "web", f"web:{host}", None


def outermost(nodes):
    keep = []
    for n in nodes:
        if not any(p is not n and p in nodes for p in n.parents):
            keep.append(n)
    return keep


def section_title(node):
    """(제목, 진짜 제목요소에서 찾았는지) 를 돌려줍니다."""
    for sel in (".api_title", ".mod_title_area", ".title_area", "h2", "h3", ".tit", ".sub_tit"):
        el = node.select_one(sel)
        if el:
            t = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
            if t:
                return t[:40], True
    t = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
    return (t[:40] if t else "이름없는 영역"), False


AD_ATTR = re.compile(
    r"(sp_nad|sp_nnad|power_link|powerlink|_fe_root_nad|link_ad|sp_nshop|shop_?ad|"
    r"ad_area|ad_section|_pl_section|_rl_section|brand_search|spw_ad)", re.I)


def is_ad_section(node, title, titled):
    """순위에서 빼는 영역(광고·플레이스·뉴스·이미지·AI브리핑 등)인지 판정."""
    attrs = " ".join(node.get("class", []) or []) + " " + (node.get("id") or "")
    if AD_ATTR.search(attrs):
        return True
    # AI 브리핑(및 그 출처 묶음)은 검색결과 문서가 아닙니다.
    if node.select_one("[class*=fds-aib-header], [class*=aib-answer]"):
        return True
    return bool(titled and SKIP_SECTION.search(title))


NOISE = re.compile(r"(새 ?창 ?열림?|Keep에 저장|Keep에 바로가기|blog\.naver\.com\s*›?)")
TITLE_SEL = (".title_link", ".total_tit", ".api_txt_lines", "[class*=headline]",
             "[class*=type-headline]", ".title", ".sub_tit")


def _clean(t):
    return re.sub(r"\s+", " ", NOISE.sub(" ", t or "")).strip()


PROFILE = re.compile(r"(blog\.naver\.com|›|새 ?창 ?열)")


def link_title(a):
    raw = a.get_text(" ", strip=True)
    t = _clean(raw)
    if len(t) >= 8 and not PROFILE.search(raw):
        return t[:120]
    # 앵커 텍스트가 프로필·출처 문구라면 같은 카드에서 제목을 찾습니다.
    p = a
    for _ in range(4):
        p = p.parent
        if p is None:
            break
        for sel in TITLE_SEL:
            el = p.select_one(sel)
            if el:
                t2 = _clean(el.get_text(" ", strip=True))
                if len(t2) >= 8 and "blog.naver.com" not in t2:
                    return t2[:120]
    return (t or "(제목 없음)")[:120]


# 결과 카드 안에서 '덧붙은' 영역 — 관련글·댓글·이웃글은 그 결과의 일부이지 별개 결과가 아닙니다.
EXTRA_IN_CARD = ("[class*=after-article], [class*=reply], [class*=related], "
                 "[class*=conveyer], [class*=pagination], [class*=fds-aib]")


def doc_source(kind, key):
    """같은 출처(같은 블로그·같은 카페)인지 비교할 때 쓰는 키."""
    if kind in ("blog", "cafe") and key.count(":") >= 2:
        return key.rsplit(":", 1)[0]
    return key


# 한 영역 안에서 결과 1건에 해당하는 묶음(카드)
ITEM_SEL = ("li.bx, li[class*=bx], div.sc_new, div.api_subject_bx, "
            "li.item, div.total_wrap, li.type_blog, li.type_cafe")


def _collect(node, seen_doc):
    """영역 하나에서 문서를 모읍니다. 결과 카드가 여러 개면 카드별로 셉니다."""
    items = outermost(node.select(ITEM_SEL))
    if len(items) >= 2:
        docs = []
        for it in items:
            docs += _collect_card(it, seen_doc)
        return docs
    return _collect_card(node, seen_doc)


def _collect_card(node, seen_doc, one_per_source=True):
    """결과 카드 하나에서 문서를 모읍니다.

    한 카드 안에 같은 블로그/카페 링크가 여러 개 있어도(프로필 링크, 관련글 링크)
    화면에서는 결과 1건이므로 1건으로 셉니다. 카드가 다르면 따로 셉니다.
    """
    docs = []
    for a in node.find_all("a", href=True):
        info = parse_doc_link(a["href"])
        if not info:
            continue
        kind, key, blog_id = info
        if key in seen_doc:
            continue
        title = link_title(a)
        # 외부 웹사이트는 제목이 제대로 있는 '결과 카드'만 셉니다(배너·버튼 제외).
        if kind == "web" and len(title) < 8:
            continue
        log_no = key.rsplit(":", 1)[-1] if kind in ("blog", "cafe") else None
        if log_no == "home":
            log_no = None
        docs.append({"kind": kind, "blog_id": blog_id, "key": key,
                     "source": doc_source(kind, key), "log_no": log_no,
                     "title": title, "url": a["href"]})

    if one_per_source:
        best = {}
        order = []
        for d in docs:
            src = d["source"]
            if src not in best:
                best[src] = d
                order.append(src)
            elif not best[src]["log_no"] and d["log_no"]:
                # 프로필(홈) 링크보다 실제 글 링크를 그 결과의 대표로 씁니다.
                best[src] = d
        docs = [best[s] for s in order]

    out = []
    for d in docs:
        if d["key"] in seen_doc:
            continue
        seen_doc.add(d["key"])
        out.append(d)
    return out


def parse_page(page_html, diag=None):
    """통합검색 HTML → 광고를 뺀 영역 목록과, 영역별 네이버 블로그 문서 목록."""
    soup = BeautifulSoup(page_html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # 상단 메뉴·하단 푸터·배너는 검색결과가 아니므로 제거합니다.
    for bad in soup.select("#gnb, #nx_gnb, #nx_footer, #footer, [id*=footer], "
                           "[class*=footer], [class*=foot_], [class*=banner], "
                           "[class*=promotion], [class*=_ad_], .related_srch, .sp_keyword"):
        bad.decompose()

    # 광고 컨테이너는 먼저 통째로 걷어냅니다.
    ad_removed = 0
    for bad in soup.select("[class*=sp_nad], [class*=power_link], [id*=power_link], "
                           "[class*=powerlink], [class*=link_ad], [class*=_fe_root_nad]"):
        bad.decompose()
        ad_removed += 1

    # 결과 '카드' 단위로 세는 것이 화면 순서와 가장 잘 맞습니다.
    # (section 은 페이지 전체를 한 덩어리로 잡아버려서 카드 경계를 잃습니다)
    nodes = outermost(soup.select("div.sc_new, div.api_subject_bx, div.sc"))
    picked = "div.sc_new"
    if len(nodes) < 2:
        nodes = outermost(soup.select("section"))
        picked = "section"
    if not nodes:
        nodes = [soup.body or soup]
        picked = "body"

    sections, seen_doc, dropped_ads = [], set(), 0
    for node in nodes:
        title, titled = section_title(node)
        if is_ad_section(node, title, titled):
            dropped_ads += 1
            continue
        # 카드 안에 붙은 관련글·댓글·AI브리핑은 별개 결과가 아니므로 걷어냅니다.
        for extra in node.select(EXTRA_IN_CARD):
            extra.decompose()
        docs = _collect(node, seen_doc)
        if docs:
            sections.append({"title": title if titled else "검색결과", "docs": docs})

    # 안전망: 영역 단위로 하나도 못 찾았는데 페이지에는 블로그 링크가 있으면
    # 광고 컨테이너만 걷어내고 문서 전체를 한 영역으로 훑습니다.
    if not sections:
        for node in nodes:                      # 제외 대상 영역을 먼저 걷어내고
            t, titled = section_title(node)
            if is_ad_section(node, t, titled):
                node.decompose()
        docs = _collect(soup.body or soup, set())
        if docs:
            sections = [{"title": "통합검색 전체", "docs": docs}]

    if diag is not None:
        diag.update({
            "영역선택자": picked,
            "영역후보": len(nodes),
            "광고제외": dropped_ads + ad_removed,
            "사용영역": len(sections),
            "페이지길이": len(page_html),
            "페이지제목": (soup.title.get_text(strip=True)[:60] if soup.title else "(없음)"),
            "원본블로그링크": len(re.findall(r"blog\.naver\.com", page_html)),
        })
    return sections


def measure(sections, my_ids):
    """내 블로그의 순위 지표를 계산합니다.

    전체순위  = 광고·플레이스·뉴스·이미지·동영상을 뺀 검색결과 문서 중 몇 번째인지
    블로그순위 = 그중 네이버 블로그 글만 따로 셌을 때 몇 번째인지
    """
    doc_rank = blog_rank = 0
    hits = []
    for s_i, sec in enumerate(sections, start=1):
        for d_i, doc in enumerate(sec["docs"], start=1):
            doc_rank += 1
            if doc["kind"] == "blog":
                blog_rank += 1
            if doc["kind"] == "blog" and doc["blog_id"] in my_ids:
                hits.append({
                    "전체순위": doc_rank,
                    "블로그순위": blog_rank,
                    "영역순번": s_i,
                    "영역명": sec["title"],
                    "영역내순위": d_i,
                    "제목": doc["title"],
                    "URL": doc["url"],
                    "블로그ID": doc["blog_id"],
                })
    return hits, doc_rank, blog_rank


# ---------------------------------------------------------------- 저장
def append_rows(biz, rows):
    """실행할 때마다 모든 결과를 그대로 덧붙입니다. 지워지는 기록은 없습니다."""
    os.makedirs(DATA_DIR, exist_ok=True)
    old = read_rows(biz)
    with open(csv_path(biz), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADER, extrasaction="ignore", restval="")
        w.writeheader()
        for r in old + rows:
            w.writerow(r)


def latest_rows(rows):
    """같은 날짜·같은 키워드가 여러 번 있으면 그날 '마지막 실행' 것만 남깁니다.
    (표와 그래프는 하루 한 값으로 그리기 위함 — 원본 CSV 에는 전부 남아 있습니다)"""
    best = {}
    for r in rows:
        k = (r.get("수집일", ""), r.get("키워드", ""))
        cur = best.get(k)
        if cur is None or (r.get("수집시각", "") or "") >= (cur.get("수집시각", "") or ""):
            best[k] = r
    return list(best.values())


def read_rows(biz):
    p = csv_path(biz)
    # 예전 단일 파일(rankings.csv)이 있으면 한 번만 옮겨 씁니다.
    legacy = os.path.join(DATA_DIR, "rankings.csv")
    if not os.path.exists(p) and os.path.exists(legacy):
        os.replace(legacy, p)
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_inquiries(biz):
    p = inq_path(biz)
    if not os.path.exists(p):
        legacy = os.path.join(DATA_DIR, "inquiries.csv")
        if not os.path.exists(legacy):
            return {}
        p = legacy
    out = {}
    with open(p, "r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d = (r.get("수집일") or r.get("날짜") or "").strip()
            v = (r.get("문의량") or "").strip()
            if d and v.isdigit():
                out[d] = int(v)
    return out


# ---------------------------------------------------------------- 실행
def run(cfg, biz, mode, debug=False, save_html=False, only=None, log=print):
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%H:%M:%S")
    my_ids = biz["ids"]
    keywords = [only] if only else biz["키워드"]
    rows = []
    gap = float(cfg.get("검색간격초", 4))

    use_browser = (mode == "browser")
    if use_browser and not browser_ready():
        log("[안내] 브라우저 방식을 쓰려면 pip install playwright 와 python -m playwright install chromium 을 먼저 실행해 주세요. 빠른 방식으로 진행합니다.")
        use_browser = False

    for i, kw in enumerate(keywords, start=1):
        engine = "브라우저" if use_browser else "빠름"
        try:
            diag = {}
            page = fetch_browser(kw) if use_browser else fetch_fast(kw)
            sections = parse_page(page, diag)
            hits, total, btotal = measure(sections, my_ids)

            # 빠른 방식인데 결과가 거의 없으면 브라우저 방식으로 한 번 더
            if (not use_browser) and total < 3 and mode == "auto" and browser_ready():
                diag = {}
                page = fetch_browser(kw)
                sections = parse_page(page, diag)
                hits, total, btotal = measure(sections, my_ids)
                engine = "브라우저"

            if save_html:
                os.makedirs(os.path.join(BASE, "raw"), exist_ok=True)
                safe = re.sub(r"[^\w가-힣]", "_", kw)
                with open(os.path.join(BASE, "raw", f"{today}_{safe}.html"), "w",
                          encoding="utf-8") as f:
                    f.write(page)

            if debug:
                log("\n" + "=" * 68)
                log("[디버그] 키워드:", kw, "| 수집방식:", engine)
                n = 0
                for s_i, sec in enumerate(sections, start=1):
                    log(f"  {s_i:>2}. [{sec['title']}] 문서 {len(sec['docs'])}건")
                    for d in sec["docs"]:
                        n += 1
                        mark = "  ★내블로그" if (d["kind"] == "blog"
                                             and d["blog_id"] in my_ids) else ""
                        who = d["blog_id"] or ""
                        log(f"     {n:>3}위 {KIND_NAME.get(d['kind'], d['kind']):<6}"
                              f" {who:<16} {d['title'][:34]}{mark}")
                log("=" * 68)

            top_hits = [h for h in hits if h["전체순위"] <= TOP_RANK]

            if top_hits:
                best = top_hits[0]
                ranks = [h["전체순위"] for h in top_hits]
                rows.append({
                    "수집일": today, "수집시각": now, "키워드": kw, "블로그ID": best["블로그ID"],
                    "노출여부": "노출", "전체순위": best["전체순위"],
                    "전체순위목록": ",".join(str(r) for r in ranks),
                    "블로그순위": best["블로그순위"],
                    "영역순번": best["영역순번"], "영역명": best["영역명"],
                    "영역내순위": best["영역내순위"], "노출건수": len(top_hits),
                    "대표제목": best["제목"], "대표URL": best["URL"],
                    "전체문서총수": total, "블로그문서총수": btotal, "수집방식": engine,
                })
                log(f"  [{i}/{len(keywords)}] {kw} → "
                    + "·".join(f"{r}위" for r in ranks)
                    + f" (블로그끼리는 {best['블로그순위']}위, 1페이지 문서 {total}건 중 "
                      f"내 글 {len(top_hits)}건)")
            else:
                rows.append({
                    "수집일": today, "수집시각": now, "키워드": kw, "블로그ID": my_ids[0],
                    "노출여부": "밖", "전체순위": "", "전체순위목록": "", "블로그순위": "",
                    "영역순번": "", "영역명": "",
                    "영역내순위": "", "노출건수": 0, "대표제목": "", "대표URL": "",
                    "전체문서총수": total, "블로그문서총수": btotal, "수집방식": engine,
                })
                near = f" (가장 가까운 순위 {hits[0]['전체순위']}위)" if hits else ""
                log(f"  [{i}/{len(keywords)}] {kw} → {TOP_RANK}위 밖{near} "
                    f"· 1페이지 문서 {total}건")
                if total < 3:
                    log(f"       ※ 검색결과가 거의 안 잡혔습니다. 진단: "
                          f"페이지길이 {diag.get('페이지길이', 0):,} / "
                          f"제목 \"{diag.get('페이지제목', '?')}\" / "
                          f"원본 blog.naver.com {diag.get('원본블로그링크', 0)}회 / "
                          f"영역후보 {diag.get('영역후보', 0)}({diag.get('영역선택자', '?')}) / "
                          f"광고제외 {diag.get('광고제외', 0)} / 사용영역 {diag.get('사용영역', 0)}")

        except Exception as e:
            log(f"  [{i}/{len(keywords)}] {kw} → 수집 실패: {e}")
            log("       10초 쉬었다가 한 번 더 시도합니다…")
            time.sleep(10)
            try:
                page = fetch_browser(kw) if use_browser else fetch_fast(kw)
                sections = parse_page(page, {})
                hits, total, btotal = measure(sections, my_ids)
                hits = [h for h in hits if h["전체순위"] <= TOP_RANK]
                best = hits[0] if hits else None
                rows.append({
                    "수집일": today, "수집시각": now, "키워드": kw,
                    "블로그ID": (best or {}).get("블로그ID", my_ids[0]),
                    "노출여부": "노출" if best else "밖",
                    "전체순위": (best or {}).get("전체순위", ""),
                    "전체순위목록": ",".join(str(h["전체순위"]) for h in hits),
                    "블로그순위": (best or {}).get("블로그순위", ""),
                    "영역순번": (best or {}).get("영역순번", ""),
                    "영역명": (best or {}).get("영역명", ""),
                    "영역내순위": (best or {}).get("영역내순위", ""),
                    "노출건수": len(hits), "대표제목": (best or {}).get("제목", ""),
                    "대표URL": (best or {}).get("URL", ""),
                    "전체문서총수": total, "블로그문서총수": btotal, "수집방식": engine,
                })
                log(f"       다시 시도 성공 → {(best or {}).get('전체순위', '밖')}"
                    + ("위" if best else ""))
                if i < len(keywords):
                    time.sleep(gap)
                continue
            except Exception as e2:
                log(f"       다시 시도도 실패: {e2}")
            rows.append({
                "수집일": today, "수집시각": now, "키워드": kw, "블로그ID": my_ids[0], "노출여부": "수집실패",
                "전체순위": "", "블로그순위": "", "영역순번": "", "영역명": "", "영역내순위": "",
                "노출건수": "", "대표제목": "", "대표URL": "", "전체문서총수": "",
                "블로그문서총수": "", "수집방식": engine,
            })

        if i < len(keywords):
            time.sleep(gap)

    close_browser()
    return rows


# ---------------------------------------------------------------- 순위판 HTML
def build_board(cfg, biz):
    rows = latest_rows(read_rows(biz))
    dates = sorted({r["수집일"] for r in rows})[-10:]

    # 지금 설정에 있는 키워드만 보여줍니다.
    # (설정에서 뺀 키워드는 표에서 사라지지만, 기록은 data 폴더 CSV 에 그대로 남습니다)
    keywords = list(biz["키워드"])

    table = {}
    for r in rows:
        if r["수집일"] not in dates:
            continue
        table[(r["키워드"], r["수집일"])] = r

    payload = {
        "title": f"{biz['이름']} 네이버 상위노출 체크",
        "blogName": biz["이름"],
        "blogId": biz["ids"][0],
        "dates": dates,
        "keywords": keywords,
        "cells": {f"{k}||{d}": {
            "상태": v.get("노출여부", ""),
            "순위": v.get("전체순위") or v.get("블로그순위", ""),
            "순위목록": [x for x in (v.get("전체순위목록", "") or "").split(",") if x],
            "블로그순위": v.get("블로그순위", ""),
            "문서총수": v.get("전체문서총수", ""),
            "건수": v.get("노출건수", ""),
            "제목": v.get("대표제목", ""),
            "url": v.get("대표URL", ""),
        } for (k, d), v in table.items()},
        "inquiries": read_inquiries(biz),
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # ---- 플레이스 ----
    prows = latest_rows(read_place_rows(biz))
    pdates = sorted({r["수집일"] for r in prows})[-10:]
    payload["place"] = {
        "dates": pdates,
        "keywords": list(biz.get("플레이스키워드", [])),
        "cells": {f"{r['키워드']}||{r['수집일']}": {
            "시각": r.get("수집시각", ""),
            "상태": r.get("노출여부", ""),
            "순위": r.get("순위", ""),
            "업체명": r.get("업체명", ""),
            "목록총수": r.get("목록총수", ""),
        } for r in prows if r.get("수집일") in pdates},
        "hasData": bool(prows),
    }

    with open(os.path.join(BASE, "_board_template.html"), "r", encoding="utf-8") as f:
        tpl = f.read()
    out = tpl.replace("/*__DATA__*/null", json.dumps(payload, ensure_ascii=False))
    with open(board_path(biz), "w", encoding="utf-8") as f:
        f.write(out)
    return board_path(biz)


# ---------------------------------------------------------------- main
def pick_mode(cfg, args):
    mode = str(cfg.get("수집방식", "browser")).strip().lower()
    if mode not in ("browser", "fast", "auto"):
        mode = "browser"
    if "--browser" in args:
        mode = "browser"
    elif "--fast" in args:
        mode = "fast"
    return mode


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    argv = sys.argv[1:]
    args = set(argv)
    cfg = load_config()
    biz_list = cfg["_biz"]

    # --biz "업체이름" : 특정 업체만
    if "--biz" in argv:
        i = argv.index("--biz")
        if i + 1 < len(argv):
            want = argv[i + 1].strip()
            picked = [b for b in biz_list if want in b["이름"] or want in b["ids"]]
            if not picked:
                print(f"[오류] '{want}' 에 해당하는 업체가 config.json 에 없습니다.")
                sys.exit(1)
            biz_list = picked

    # --only "키워드" : 그 키워드 하나만 상세 목록 출력 (기록 저장 안 함)
    only = None
    if "--only" in argv:
        i = argv.index("--only")
        if i + 1 < len(argv):
            only = argv[i + 1].strip()

    if only:
        target = next((b for b in biz_list
                       if any(only in k or k in only for k in b["키워드"])), biz_list[0])
        kw = next((k for k in target["키워드"] if only in k or k in only), only)
        print(f"\n[점검 모드] {target['이름']} / 키워드 \"{kw}\" 하나만 확인합니다. 기록은 저장하지 않습니다.")
        run(cfg, target, pick_mode(cfg, args), debug=True, only=kw)
        print("\n위 목록에서 ★내블로그 가 몇 번째인지 실제 네이버 화면과 비교해 보세요.\n")
        return

    made = []
    for n, biz in enumerate(biz_list, start=1):
        if "--html-only" not in args:
            mode = pick_mode(cfg, args)
            print(f"\n[{n}/{len(biz_list)}] {biz['이름']} "
                  f"(blog.naver.com/{biz['ids'][0] if biz['ids'] else '-'}) "
                  f"— 블로그 키워드 {len(biz['키워드'])}개 / {mode}")
            if biz["키워드"] and "--place-only" not in args:
                rows = run(cfg, biz, mode, debug=("--debug" in args),
                           save_html=("--save-html" in args))
                append_rows(biz, rows)
            if biz.get("플레이스키워드") and "--blog-only" not in args:
                print(f"    플레이스 키워드 {len(biz['플레이스키워드'])}개")
                prows = run_place(cfg, biz, debug=("--debug" in args))
                if prows:
                    append_place_rows(biz, prows)
        made.append(build_board(cfg, biz))

    print("\n기록 저장 — data 폴더")
    print("순위판 생성")
    for p in made:
        print("  ·", os.path.basename(p))
    print("→ 파일을 더블클릭하면 결과를 볼 수 있습니다.\n")


if __name__ == "__main__":
    main()
