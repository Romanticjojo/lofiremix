"""Loopback-only listening UI; serves known audio and stores explicit feedback."""

import json
import re
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .listening_feedback import FeedbackStore


def create_server(session, port=0):
    store = FeedbackStore(session)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send_data(self, status, body, mime='application/json; charset=utf-8', extra=None):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline'; media-src 'self'; frame-ancestors 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def result(self, status, data):
            self.send_data(status, json.dumps(data, ensure_ascii=False).encode('utf-8'))

        def valid_host(self):
            port = self.server.server_address[1]
            return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self.valid_host():
                return self.result(403, {'error': '请使用本机试听地址'})
            path = urlsplit(self.path).path
            try:
                if path == '/api/state':
                    return self.result(200, {**store.state(), 'token': token})
                if path in ('/', '/comparison.html') or path in {i['page'] for i in store.state()['items']}:
                    return self.send_data(200, Path(__file__).with_name('listening.html').read_bytes(),
                                          'text/html; charset=utf-8')
                match = re.fullmatch(r'/media/([0-9a-f]{64})/([AB])\.wav', path)
                if match:
                    return self.audio(store.media(match[1], match[2]))
                self.result(404, {'error': '没有这个试听文件'})
            except (ValueError, KeyError, IndexError, OSError) as exc:
                self.result(400, {'error': str(exc)})

        def audio(self, path):
            size = path.stat().st_size
            start, end, status = 0, size-1, 200
            requested = self.headers.get('Range')
            if requested:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
                if not match or not any(match.groups()):
                    return self.send_data(416, b'', extra={'Content-Range': f'bytes */{size}'})
                if match[1]:
                    start = int(match[1])
                    end = min(end, int(match[2])) if match[2] else end
                else:
                    start = max(0, size-int(match[2]))
                if start > end or start >= size:
                    return self.send_data(416, b'', extra={'Content-Range': f'bytes */{size}'})
                status = 206
            self.send_response(status)
            self.send_header('Content-Type', 'audio/wav')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(end-start+1))
            if status == 206:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            if self.command != 'HEAD':
                try:
                    with path.open('rb') as stream:
                        stream.seek(start)
                        left = end-start+1
                        while left:
                            chunk = stream.read(min(left, 65536))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            left -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_POST(self):
            if (not self.valid_host() or self.headers.get('Origin') != f'http://{self.headers.get("Host")}'
                    or not secrets.compare_digest(self.headers.get('X-DJ-Token', ''), token)):
                return self.result(403, {'error': '请从本机试听页保存反馈'})
            if urlsplit(self.path).path != '/api/feedback':
                return self.result(404, {'error': '未知保存入口'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384 or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('提交内容无效或过长')
                payload = json.loads(self.rfile.read(length))
                self.result(200, store.submit(payload))
            except (ValueError, TypeError, KeyError) as exc:
                self.result(409 if '更新' in str(exc) else 400, {'error': str(exc)})
            except OSError:
                self.result(500, {'error': '未能写入本地文件；请保留页面并重试'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def serve(session, port=0, open_browser=False):
    server = create_server(session, port)
    url = f'http://127.0.0.1:{server.server_address[1]}/comparison.html'
    print(f'试听与反馈：{url}', flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
