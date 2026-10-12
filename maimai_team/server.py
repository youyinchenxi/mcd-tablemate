"""Local, demo-only UI. No credential handling or order creation routes."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
MAX_BODY = 65536


def demo_data():
    return {name: json.loads((ROOT / 'examples' / f'demo_{name}.json').read_text(encoding='utf-8'))
            for name in ('catalog', 'request')}


def demo_plan(request):
    from .planner import plan
    result = plan(demo_data()['catalog'], request)
    result.update(source='demo', verified=False,
                  evidence={'mode': 'demo', 'note': '模拟演示数据，非实时门店价格；没有发起订单。'})
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _allowed(self):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            return False
        origin = self.headers.get('Origin')
        return origin is None or origin in {f'http://{host}' for host in hosts}

    def _send(self, status, body, content_type='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {'error': '只接受本机同源访问。'})
        path = urlsplit(self.path).path
        if path == '/api/demo':
            return self._send(200, demo_data())
        if path == '/api/health':
            return self._send(200, {'ok': True, 'mode': 'demo'})
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                 '/styles.css': ('styles.css', 'text/css; charset=utf-8')}
        if path not in files:
            return self._send(404, {'error': '页面不存在。'})
        filename, mime = files[path]
        try:
            return self._send(200, (ROOT / 'web' / filename).read_bytes(), mime)
        except FileNotFoundError:
            return self._send(503, {'error': '演示页面尚未就绪。'})

    def do_POST(self):
        if not self._allowed():
            return self._send(403, {'error': '只接受本机同源访问。'})
        if urlsplit(self.path).path != '/api/plan':
            return self._send(404, {'error': '接口不存在。'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self._send(415, {'error': '请求需要 JSON 格式。'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                return self._send(413, {'error': '请求为空或过大。'})
            body = json.loads(self.rfile.read(length).decode('utf-8'))
            if not isinstance(body, dict) or not isinstance(body.get('request'), dict):
                raise ValueError('请提供 request 对象。')
            self._send(200, demo_plan(body['request']))
        except (ValueError, TypeError, UnicodeError) as exc:
            self._send(400, {'error': str(exc)[:250]})
        except Exception:
            self._send(500, {'error': '规划未完成，请重新尝试。'})


def serve(port=8765):
    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        print(f'麦桌搭子 · 模拟演示 http://127.0.0.1:{server.server_port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
