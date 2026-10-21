"""测试用的本地拍卖站：/list 列表页（需要登录时校验 cookie），/login 登录表单。"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tests.conftest import fixture_html

LOGIN_PAGE = """<html><body><form method="post" action="/login">
<input name="u" id="u"><input name="p" id="p" type="password"><button id="go">登录</button>
</form></body></html>"""


class Handler(BaseHTTPRequestHandler):
    require_login = False

    def log_message(self, *args):  # 静默
        pass

    def _send(self, body: str, status: int = 200, headers: dict | None = None):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/login":
            return self._send(LOGIN_PAGE)
        if url.path == "/list":
            if self.server.require_login and "sid=ok" not in (self.headers.get("Cookie") or ""):
                return self._send("", 302, {"Location": "/login"})
            page = parse_qs(url.query).get("page", ["1"])[0]
            html = fixture_html(f"list_page{page}.html").replace("<body>", '<body><div id="user">我的</div>', 1)
            return self._send(html)
        self._send("not found", 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode())
        if form.get("u") == ["alice"] and form.get("p") == ["secret"]:
            return self._send("", 302, {"Location": "/list", "Set-Cookie": "sid=ok; Path=/"})
        self._send(LOGIN_PAGE, 401)


def start(require_login: bool = False) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.require_login = require_login  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"
