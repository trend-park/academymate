"""One subprocess per task: no browser globals or tenant files are shared."""
import json
import re
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent/'legacy'))
import rank_check as rc

def collect(cfg):
    results = []
    pid_match = re.search(r'(?:place/|/)(\d{3,30})(?:[/?#]|$)', cfg['place'])
    pid = cfg['place'] if cfg['place'].isdigit() else (pid_match.group(1) if pid_match else '')
    tasks = [('place',k) for k in cfg['place_keywords']] + [('blog',k) for k in cfg['blog_keywords']]
    try:
        if not rc.browser_ready():
            raise RuntimeError('검색용 Chromium이 없습니다. python -m playwright install chromium 을 실행하세요.')
        for index,(kind,kw) in enumerate(tasks):
            print(f'[{index+1}/{len(tasks)}] {kind}: {kw}',flush=True)
            item = dict(kind=kind,keyword=kw,rank=None,status='error',count=0,url='',title='')
            try:
                if kind=='place':
                    places, _ = rc.fetch_place_page(kw,50,log=print)
                    if not places: raise ValueError('플레이스 검색 목록을 읽지 못했습니다.')
                    rank, found = rc.match_place(places,pid,cfg['name'],50)
                    item.update(rank=rank or None,count=len(places),status='ranked' if rank else 'outside',title=(found or {}).get('name',''))
                else:
                    page = rc.fetch_browser(kw)
                    sections = rc.parse_page(page,{})
                    hits,total,_ = rc.measure(sections,[cfg['blog']])
                    # An unparseable/very sparse page must never become a false outside rank.
                    if total < 3:
                        soup = rc.BeautifulSoup(page,'html.parser')
                        for node in soup(['script','style','noscript']): node.decompose()
                        text = soup.get_text(' ',strip=True)
                        if any(s in text for s in ('비정상적인 접근','자동입력 방지','자동 입력 방지')):
                            raise ValueError('네이버가 추가 확인을 요청했습니다.')
                        raise ValueError('검색 문서가 부족해 순위를 확정할 수 없습니다.')
                    hits = [h for h in hits if h['전체순위']<=15]
                    best = hits[0] if hits else {}
                    url = best.get('URL','')
                    item.update(rank=best.get('전체순위'),status='ranked' if hits else 'outside',count=total,
                        url=url if url.startswith('https://') else '',title=best.get('제목',''),
                        ranks=[h['전체순위'] for h in hits],blog_rank=best.get('블로그순위'))
            except Exception as e:
                item['error'] = str(e)[:200]
            results.append(item)
            if index < len(tasks)-1: time.sleep(6)
    finally:
        rc.close_browser()
    return results

if __name__=='__main__':
    folder = Path(sys.argv[1])
    cfg = json.loads((folder/'input.json').read_text(encoding='utf-8'))
    (folder/'output.json').write_text(json.dumps(collect(cfg),ensure_ascii=False),encoding='utf-8')
