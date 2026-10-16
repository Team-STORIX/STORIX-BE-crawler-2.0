"""prod 작품 링크(works_platform.landing_url) 빈 행 채우기. 운영자 PC 의 fill-links.sh 가 부른다.

    python tools/fill_links.py <null_links.jsonl> <artists.jsonl> <results.jsonl> [플랫폼,…] [최대 행 수] [used_links.jsonl]

입력
  null_links.jsonl  링크가 빈 행 (id, works_id, platform, works_type, works_name, age)
  artists.jsonl     작가 원본값 (같은 이름 후보를 작가로 가릴 때)
  used_links.jsonl  이미 들어간 링크 (works_id, landing_url). 다른 작품에 들어간 링크는 확정하지 않는다
출력: results.jsonl 에 한 행씩 이어 쓴다. 이미 있는 행은 건너뛴다 (이어서 돌릴 수 있게). status
  OK     제목 정확 · 유형 맞음 · 후보 하나 (여럿이면 상세의 작가로 하나를 고름)
  AMBIG  같은 조건 후보가 여럿이고 작가로도 못 가림 · 그 링크가 이미 다른 작품에 있음
  TYPE   제목은 같지만 유형이 다름
  NONE   검색 결과 없음

후보 고르기는 제목 검색 수집(crawler/modes/search_titles._crawl_for_type)과 같은 규칙을 쓴다
  - 유형 순서는 pick_candidates: 원하는 유형 → 힌트 없음 → (웹소설이면) 단행본. 단행본은 연재판이 없을 때만
  - 판본(is_edition: 19세 완전판 · 개정판)은 다른 작품. DB 이름에 판본 표기가 없으면 판본 후보는 고르지 않는다
  - 단, DB 이름에 표기 없이 판본만 들어온 옛 작품('응급 플러팅' = 카카오 [19세 완전판])은 플랫폼에 일반판이 없을 때
    DB 연령으로 판본을 고른다: 18세 → 완전판, 그 아래 → 개정판
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.crawler.base_crawler import BaseCrawler, is_edition, pick_candidates, strip_title_labels  # noqa: E402
from review.landing import canonical_landing_url, platform_work_key  # noqa: E402

EDITION_LABEL = re.compile(r'\s*[\[(][^\])]*(?:완전판|개정판)[^\])]*[\])]')
LABEL = re.compile(r'\s*\[[^\]]*\]\s*$')


def norm(s):
    return ''.join((s or '').split()).lower()


def name_key(n):
    """작가명 비교용: 괄호 · 공백 · 대소문자 무시"""
    return norm(re.sub(r'\([^)]*\)', '', n or ''))


def is_adult(r):
    age = r.get('age') or ''
    return '18' in age or '19' in age


def text_of(c):
    return c.get('raw') or c.get('text') or ''


def edition_of(name, cands, adult):
    """DB 이름에 판본 표기가 없는데 플랫폼엔 판본만 있을 때. 표기를 뗀 제목이 같고 연령에 맞는 판본만"""
    want = '완전판' if adult else '개정판'
    key = BaseCrawler._norm_title(strip_title_labels(name))
    # 원래 제목(raw)엔 시리즈 '(총 102화/완결)' 같은 꼬리가 붙어 있어, 라벨을 정리한 제목(text)으로 비교한다
    return [c for c in cands if is_edition(c.get('text')) and want in c.get('text', '')
            and BaseCrawler._norm_title(strip_title_labels(EDITION_LABEL.sub('', c['text']))) == key]


def by_author(crawler, want_names, cands):
    """후보 상세를 열어 DB 작가와 겹치는 후보만."""
    want = {name_key(n) for n in want_names}
    hit = []
    for c in cands:
        try:
            d = crawler.crawl_detail(c['url']) or {}
        except Exception as e:
            print('   상세 실패', c['url'], type(e).__name__, flush=True)
            continue
        got = set()
        for k in ('artist_name', 'author', 'illustrator', 'original_author'):
            for n in (d.get(k) or '').split(','):
                if n.strip():
                    got.add(name_key(n))
        if want & got:
            hit.append(c)
    return hit


def pick(crawler, r, cands, artists=None):
    """후보 → (status, url, 기록할 후보, 메모). 링크 중복은 decide 가 본다."""
    platform, wtype, name = r['platform'], r['works_type'], r['works_name']
    mine = [c for c in cands if (platform_work_key(c['url']) or ('',))[0] == platform]
    exact = [c for c in mine if c.get('kind') == '정확' and is_edition(text_of(c)) == is_edition(name)]
    picked = pick_candidates(exact, wtype)   # 원하는 유형 → 힌트 없음 → (웹소설이면) 단행본
    main = [c for c in picked if c.get('type_hint') in (wtype, None)]
    same = main or picked                    # 단행본은 연재판이 없을 때만
    if platform == 'NAVER_SERIES':
        serial = [c for c in same if not c.get('book')]   # 시리즈 단행본 · 연재판이 같이 나오면 연재판
        same = serial or same
    note = ''
    if not same and not is_edition(name):
        editions = edition_of(name, pick_candidates(mine, wtype), is_adult(r))
        same = [c for c in editions if c.get('type_hint') in (wtype, None)] or editions   # 여기서도 단행본은 연재판이 없을 때만
        note = '판본을 연령으로 고름'
    uniq = {}
    for c in same:
        uniq.setdefault(canonical_landing_url(c['url']), c)
    if len(uniq) == 1:
        return 'OK', next(iter(uniq)), list(uniq.values()), note
    if len(uniq) > 1:
        if crawler is not None:
            hit = by_author(crawler, (artists or {}).get(r['works_id'], set()), list(uniq.values()))
            if len(hit) == 1:
                return 'OK', canonical_landing_url(hit[0]['url']), list(uniq.values()), ' · '.join(filter(None, [note, '작가로 고름']))
            return 'AMBIG', None, list(uniq.values()), f'작가 일치 {len(hit)}'
        return 'AMBIG', None, list(uniq.values()), ''
    if any(c.get('kind') == '정확' for c in mine):
        return 'TYPE', None, mine, ''
    return 'NONE', None, mine or cands[:3], ''


def decide(crawler, r, cands, used, artists=None):
    """pick + 링크 중복 확인. used: {정규화 링크: works_id}. 확정한 링크는 used 에 더한다"""
    status, url, kept, note = pick(crawler, r, cands, artists)
    if status == 'OK':
        other = used.get(url)
        if other is not None and other != r['works_id']:
            return 'AMBIG', None, kept, f'이미 작품 {other} 의 링크'
        used[url] = r['works_id']
    return status, url, kept, note


def naver_webtoon(name):
    q = urllib.parse.quote(name.replace('%', ' ').strip())
    req = urllib.request.Request(f'https://comic.naver.com/api/search/all?keyword={q}',
                                 headers={'User-Agent': 'Mozilla/5.0'})
    d = json.load(urllib.request.urlopen(req, timeout=10))
    out = []
    for x in d['searchWebtoonResult']['searchViewList']:
        t = x.get('titleName') or ''
        out.append({'kind': '정확' if norm(LABEL.sub('', t)) == norm(name) else '', 'type_hint': '웹툰', 'text': t,
                    'url': f"https://comic.naver.com/webtoon/list?titleId={x.get('titleId')}"})
    return out


def naver_novel(c, name):
    from selenium.webdriver.common.by import By
    c.driver.get(f'https://novel.naver.com/search?keyword={urllib.parse.quote(name)}')
    time.sleep(2)
    raw = []
    for a in c.driver.find_elements(By.CSS_SELECTOR, ".component_section a[href*='novelId=']"):
        href = a.get_attribute('href') or ''
        if '/webnovel/list' not in href:
            continue
        try:
            text = a.find_element(By.CSS_SELECTOR, '.title').text.strip()
        except Exception:
            text = (a.get_attribute('title') or a.text or '').strip()
        if text:
            raw.append((href, text, '웹소설'))
    return c.rank_candidates(name, raw)


def naver_series(c, name):
    """시리즈 검색. 원래 제목의 [단행본] · '총 ○권' 으로 단행본을 표시한다 (_clean_title 이 라벨을 떼서 못 본다)."""
    from selenium.webdriver.common.by import By
    c.driver.get(c._SEARCH_URL.format(urllib.parse.quote(name)))
    time.sleep(2)
    raw, raws = [], {}
    for a in c.driver.find_elements(By.CSS_SELECTOR, "a[href*='detail.series'][class*='title']"):
        href = a.get_attribute('href') or ''
        full = (a.get_attribute('title') or a.text or '').strip()
        text = c._clean_title(full)
        if text:
            raw.append((href, text, '웹소설' if '/novel/' in href else '웹툰' if '/comic/' in href else None))
            raws[href] = full
    cands = c.rank_candidates(name, raw)
    for cand in cands:
        full = raws.get(cand['url'], '')
        cand['raw'] = full
        cand['book'] = '[단행본]' in full or bool(re.search(r'총\s*\d+\s*권', full))
    return cands


def kakao(c, name):
    cands = c.search_candidates(name)
    if not any(x.get('kind') == '정확' for x in cands):
        alt = ' '.join(re.sub(r'[,:;!?~·]', ' ', name).split())
        if alt != name:
            cands = c.rank_candidates(name, [(x['url'], x['text'], x['type_hint']) for x in c.search_candidates(alt)]) or cands
    return cands


def load_artists(path):
    artists = {}
    for line in open(path, encoding='utf-8'):
        a = json.loads(line)
        names = set()
        for k in ('artistName', 'author', 'illustrator', 'originalAuthor'):
            for n in (a.get(k) or '').split(','):
                if n.strip():
                    names.add(n.strip())
        artists[a['worksId']] = names
    return artists


def load_used(used_path, results_path):
    """DB 에 이미 들어간 링크 + 지난 실행에서 확정한 링크 → {정규화 링크: works_id}"""
    used = {}
    for path, key_id, key_url in ((used_path, 'works_id', 'landing_url'), (results_path, 'works_id', 'url')):
        if not path or not os.path.exists(path):
            continue
        for line in open(path, encoding='utf-8'):
            x = json.loads(line)
            if x.get(key_url) and x.get('status', 'OK') == 'OK':
                used.setdefault(canonical_landing_url(x[key_url]), x[key_id])
    return used


def main(argv):
    src, artists_path, out = argv[1], argv[2], argv[3]
    only = set(argv[4].split(',')) if len(argv) > 4 and argv[4] else None
    limit = int(argv[5]) if len(argv) > 5 and argv[5] else None  # 이번 실행에서 처리할 최대 행 수 (전체)
    used_path = argv[6] if len(argv) > 6 else None

    artists = load_artists(artists_path)
    used = load_used(used_path, out)
    rows = [json.loads(line) for line in open(src, encoding='utf-8')]
    done = set()
    if os.path.exists(out):
        done = {json.loads(line)['id'] for line in open(out, encoding='utf-8')}
    fo = open(out, 'a', encoding='utf-8')

    def write(r, status, url, cands, note=''):
        fo.write(json.dumps({**r, 'status': status, 'url': url, 'note': note,
                             'cands': [(c.get('kind'), c.get('type_hint'), text_of(c),
                                        canonical_landing_url(c['url'])) for c in cands[:6]]},
                            ensure_ascii=False) + '\n')
        fo.flush()
        print(r['platform'], r['works_id'], status, r['works_name'], url or '', note, flush=True)

    todo = {}
    for r in rows:
        if r['id'] in done or (only and r['platform'] not in only):
            continue
        todo.setdefault(r['platform'], []).append(r)
    if limit:
        left, capped = limit, {}
        for p, v in todo.items():   # 앞 플랫폼부터 채워 전체 limit 건까지만
            if left <= 0:
                break
            capped[p], left = v[:left], left - len(v[:left])
        todo = capped

    for platform, items in todo.items():
        if platform == 'NAVER_WEBTOON':
            for r in items:
                try:
                    write(r, *decide(None, r, naver_webtoon(r['works_name']), used, artists))
                except Exception as e:
                    print('ERR', r['works_id'], e, flush=True)
            continue
        if platform == 'KAKAO_PAGE':
            from modules.crawler.kakao_crawler import KakaoCrawler as C
            search = kakao
        elif platform == 'NAVER_SERIES':
            from modules.crawler.naver_series_crawler import NaverSeriesCrawler as C
            search = naver_series
        elif platform == 'NAVER_NOVEL':
            from modules.crawler.naver_novel_crawler import NaverNovelCrawler as C
            search = naver_novel
        elif platform == 'RIDIBOOKS':
            from modules.crawler.ridibooks_crawler import RidibooksCrawler as C
            search = lambda c, n: c.search_candidates(n)  # noqa: E731
        else:
            continue
        c = C(headless=True)
        c.start_driver()
        try:
            for r in items:
                try:
                    write(r, *decide(c, r, search(c, r['works_name']), used, artists))
                except Exception as e:
                    print('ERR', r['works_id'], type(e).__name__, str(e)[:120], flush=True)
                    if 'RateLimited' in type(e).__name__:
                        break
                    try:
                        c._restart_driver()
                    except Exception:
                        pass
        finally:
            c.close_driver()


if __name__ == '__main__':
    main(sys.argv)
