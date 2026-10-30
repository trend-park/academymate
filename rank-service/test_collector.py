import unittest
from unittest.mock import patch
import collector

class CollectorTests(unittest.TestCase):
    def run_blog(self,page,total):
        cfg=dict(name='테스트',blog='myacademy',place='',blog_keywords=['태권도'],place_keywords=[])
        with patch.object(collector.rc,'browser_ready',return_value=True), patch.object(collector.rc,'fetch_browser',return_value=page), patch.object(collector.rc,'parse_page',return_value=[]), patch.object(collector.rc,'measure',return_value=([],total,0)), patch.object(collector.rc,'close_browser'):
            return collector.collect(cfg)[0]

    def test_embedded_captcha_script_is_not_a_challenge(self):
        result=self.run_blog('<html><script>captchaApi="https://captcha.nid.naver.com";</script><body>정상 검색 결과</body></html>',21)
        self.assertEqual(result['status'],'outside')

    def test_visible_challenge_and_empty_results_are_errors(self):
        self.assertEqual(self.run_blog('<body>자동입력 방지</body>',0)['status'],'error')
        self.assertEqual(self.run_blog('<body>알 수 없는 페이지</body>',0)['status'],'error')

    def test_empty_place_list_is_not_outside(self):
        cfg=dict(name='테스트',blog='',place='123456789',blog_keywords=[],place_keywords=['태권도'])
        with patch.object(collector.rc,'browser_ready',return_value=True), patch.object(collector.rc,'fetch_place_page',return_value=([],'browser')), patch.object(collector.rc,'close_browser'):
            self.assertEqual(collector.collect(cfg)[0]['status'],'error')

if __name__=='__main__': unittest.main(verbosity=2)
