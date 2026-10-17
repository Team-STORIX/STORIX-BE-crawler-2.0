"""keepalive 모드: 로그인 쿠키 갱신."""
import pickle

from crawler.modes.keepalive import save_cookies
from modules.crawler.cookies import merge_cookies


def test_merge_takes_fresh_values_of_same_site_only():
    old = [{'name': 'NID_AUT', 'value': 'a', 'domain': '.naver.com'},
           {'name': 'NID_SES', 'value': 'old', 'domain': 'nid.naver.com'}]
    fresh = [{'name': 'NID_SES', 'value': 'new', 'domain': '.naver.com', 'expiry': 2000000000},
             {'name': 'NNB', 'value': 'n', 'domain': '.naver.com'},
             {'name': '_ga', 'value': 'g', 'domain': '.google.com'}]
    merged = {c['name']: c for c in merge_cookies(old, fresh)}
    assert set(merged) == {'NID_AUT', 'NID_SES', 'NNB'}
    assert merged['NID_SES']['value'] == 'new' and merged['NID_AUT']['value'] == 'a'


def test_save_cookies_merges_into_file(tmp_path):
    path = tmp_path / 'naver_cookies.pkl'
    pickle.dump([{'name': 'NID_SES', 'value': 'old', 'domain': '.naver.com'}], open(path, 'wb'))

    class Driver:
        def execute_cdp_cmd(self, cmd, params):
            assert cmd == 'Network.getAllCookies'
            return {'cookies': [{'name': 'NID_SES', 'value': 'new', 'domain': '.naver.com', 'path': '/',
                                 'expires': 2000000000.5, 'session': False, 'secure': True, 'httpOnly': True,
                                 'sameSite': 'Lax'}]}

    assert save_cookies(Driver(), path) == 1
    saved = pickle.load(open(path, 'rb'))
    assert saved == [{'name': 'NID_SES', 'value': 'new', 'domain': '.naver.com', 'path': '/', 'secure': True,
                      'httpOnly': True, 'sameSite': 'Lax', 'expiry': 2000000000}]
    assert oct(path.stat().st_mode)[-3:] == '600'
