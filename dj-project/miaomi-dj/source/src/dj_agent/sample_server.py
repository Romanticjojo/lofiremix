"""Read-only loopback preview of one sample; byte ranges allow immediate seeking."""

import mimetypes
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def create_server(directory, port=0):
    root = Path(directory).resolve(strict=True)
    if not (root/'index.html').is_file():
        raise ValueError('sample visualization is missing')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            port = self.server.server_address[1]
            if self.headers.get('Host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
                self.send_error(403)
                return
            url = urlsplit(self.path).path
            name = 'index.html' if url == '/' else url.lstrip('/')
            if (name not in {'index.html', 'visualization.json', 'sample-report.json', 'README.md',
                             'audio/master.wav', 'audio/master.mp3', 'audio/metrics.json', 'audio/plan.json'}
                    and not re.fullmatch(r'audio/transition-[0-9]{2,4}\.wav', name)
                    and not re.fullmatch(r'covers/[a-z0-9-]+\.(jpg|png|webp)', name)):
                self.send_error(404)
                return
            path = root/name
            if not path.is_file() or not path.resolve().is_relative_to(root):
                self.send_error(404)
                return
            size = path.stat().st_size
            start, end, status = 0, size-1, 200
            requested = self.headers.get('Range')
            if requested:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
                if not match or not any(match.groups()):
                    self.send_error(416)
                    return
                if match[1]:
                    start = int(match[1])
                    end = min(end, int(match[2])) if match[2] else end
                else:
                    start = max(0, size-int(match[2]))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                status = 206
            self.send_response(status)
            mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            self.send_header('Content-Type', mime+('; charset=utf-8' if mime.startswith('text/') else ''))
            self.send_header('Content-Length', str(end-start+1))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-cache')
            if status == 206:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            if self.command != 'HEAD':
                try:
                    with path.open('rb') as stream:
                        stream.seek(start)
                        remaining = end-start+1
                        while remaining:
                            chunk = stream.read(min(65536, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def serve(directory, port=0, open_browser=False):
    server = create_server(directory, port)
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    print(f'样例可视化：{url}', flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
