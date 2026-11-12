"""Real HTTP app-shell bursts, including concurrent installed-app downloads."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import threading
import unittest

from test_security import onda


class AppShellTests(unittest.TestCase):
    def test_concurrent_shell_connections(self):
        server = onda.OndaHTTPServer(('127.0.0.1', 0), onda.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        barrier = threading.Barrier(32)
        assets = ['/app.js', '/online.js', '/sheet.js', '/pwa.js',
                  '/style.css', '/player.css', '/fonts/inter.woff2', '/service-worker.js']

        def get_asset(index):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                barrier.wait(timeout=10)
                connection.request('GET', assets[index % len(assets)])
                response = connection.getresponse()
                return response.status, len(response.read())
            finally:
                connection.close()

        try:
            with ThreadPoolExecutor(max_workers=32) as pool:
                responses = list(pool.map(get_asset, range(32)))
            self.assertEqual(len(responses), 32)
            self.assertTrue(all(status == 200 and size > 0 for status, size in responses), responses)
        finally:
            server.shutdown()
            server.server_close()
