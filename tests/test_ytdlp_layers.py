"""bot_check defenses: PO-token provider gating, account cookies, IPv4, pacing."""
import importlib
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def fresh(**env):
    keys=('POT_PROVIDER_URL','IVOX_YTDLP_COOKIES','IVOX_YTDLP_COOKIES_FILE','IVOX_YTDLP_SLEEP_REQUESTS')
    clean={k:v for k,v in os.environ.items() if k not in keys}
    with patch.dict(os.environ,{**clean,**env},clear=True):
        import audio_resolver
        return importlib.reload(audio_resolver)


class LayerTests(unittest.TestCase):
    def tearDown(self):fresh()

    def test_defaults_force_ipv4_without_tokens_cookies_or_pacing(self):
        a=fresh()
        self.assertEqual(a.OPTIONS['source_address'],'0.0.0.0')
        self.assertNotIn('youtubepot-bgutilhttp',a.OPTIONS['extractor_args'])
        self.assertNotIn('cookiefile',a.OPTIONS);self.assertNotIn('sleep_interval_requests',a.OPTIONS)
        self.assertFalse(a.pot_available())

    def test_provider_url_configures_plugin_and_unreachable_provider_is_skipped_fast(self):
        a=fresh(POT_PROVIDER_URL='http://127.0.0.1:9/')
        self.assertEqual(a.OPTIONS['extractor_args']['youtubepot-bgutilhttp'],{'base_url':['http://127.0.0.1:9']})
        started=time.perf_counter();self.assertFalse(a.pot_available());self.assertLess(time.perf_counter()-started,1.5)
        # Cached for a minute: no second network check per song.
        with patch('http.client.HTTPConnection') as conn:self.assertFalse(a.pot_available());conn.assert_not_called()

    def test_worker_tries_po_token_clients_first_only_when_provider_is_up(self):
        a=fresh()
        self.assertIn("if pot:profiles.insert(0,('yt-dlp-pot',['mweb','android_vr']))",a.WORKER)
        self.assertIn("pot=options.pop('ivox_pot',False)",a.WORKER)

    def test_pasted_secret_cookies_become_private_netscape_file(self):
        a=fresh(IVOX_YTDLP_COOKIES='.youtube.com\tTRUE\t/\tTRUE\t2000000000\tSID\tsecret')
        text=Path(a.COOKIE_FILE).read_text(encoding='utf-8')
        self.assertTrue(text.startswith('# Netscape HTTP Cookie File\n'))
        self.assertIn('.youtube.com\tTRUE\t/\tTRUE\t2000000000\tSID\tsecret\n',text)
        self.assertEqual(a.OPTIONS['cookiefile'],a.COOKIE_FILE)
        os.unlink(a.COOKIE_FILE)

    def test_rejected_cookies_are_reported_once_per_window_without_contents(self):
        a=fresh()
        with patch.object(a,'metric') as metric:
            a.cookie_warning('cookies_expired');a.cookie_warning('cookies_expired')
        metric.assert_called_once()
        self.assertEqual(metric.call_args.kwargs['status'],'rejected')

    def test_pacing_is_opt_in_and_bounded(self):
        self.assertEqual(fresh(IVOX_YTDLP_SLEEP_REQUESTS='5').OPTIONS['sleep_interval_requests'],2)


if __name__=='__main__':unittest.main()
