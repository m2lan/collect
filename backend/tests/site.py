"""测试用的本地拍卖站：/list 列表页（需要登录时校验 cookie），/login 登录表单。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tests.conftest import fixture_html

LOGIN_PAGE = """<html><body><form method="post" action="/login">
<input name="u" id="u"><input name="p" id="p" type="password"><button id="go">登录</button>
</form></body></html>"""


LOTS = [
    {"id": 1001, "lotName": "清乾隆 青花缠枝莲纹瓶", "currentPrice": "12500.00", "bidCount": 18, "status": "进行中",
     "endTime": "2026-10-30 20:00:00", "pic": "/img/1001.jpg", "detailUrl": "/lot/1001"},
    {"id": 1002, "lotName": "民国 粉彩人物盘", "currentPrice": "32000", "bidCount": 7, "status": "已成交",
     "endTime": "2026-10-20 18:00:00", "pic": "/img/1002.jpg", "detailUrl": "/lot/1002"},
    {"id": 1003, "lotName": "宋 建窑兔毫盏", "currentPrice": "800", "bidCount": 0, "status": "即将开始",
     "endTime": "2026-11-01 10:00:00", "pic": "/img/1003.jpg", "detailUrl": "/lot/1003"},
]
SPA_PAGE = """<html><body><div id="app">加载中…</div><script>
fetch('/api/lots?page=1').then(r => r.json()).then(d => {
  document.getElementById('app').innerHTML = d.data.list.map(x =>
    `<div class="card"><a href="${x.detailUrl}">${x.lotName}</a><b>${x.currentPrice}</b></div>`).join('');
});
</script></body></html>"""
JS_LIST_PAGE = """<html><body><div id="list"></div>
<div class="pager"><button class="next-btn" type="button">下一页</button></div>
<script>
let page = 1;
function load() {
  fetch('/api/lots?page=' + page).then(r => r.json()).then(d => {
    document.getElementById('list').innerHTML = d.data.list.map(x =>
      `<div class="lot"><a class="name" href="${x.detailUrl}">${x.lotName}</a><span class="cur">¥${x.currentPrice}</span></div>`).join('');
    document.querySelector('.next-btn').disabled = page * 2 >= d.data.total;
  });
}
document.querySelector('.next-btn').addEventListener('click', () => { page += 1; load(); });
load();
</script></body></html>"""
DETAIL_PAGE = """<html><body><h1 class="lot-title">{title}</h1>
<div class="price-box">当前价 <span class="now">¥{price}</span></div>
<div class="estimate">估价：RMB 80,000-120,000</div><div class="deposit">保证金 ¥5,000</div></body></html>"""


def lots_page(page: int, size: int = 2) -> dict:
    chunk = LOTS[(page - 1) * size : page * size]
    return {"code": 0, "data": {"list": chunk, "total": len(LOTS), "page": page}}


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

    def _json(self, data: dict):
        body = json.dumps(data, ensure_ascii=False)
        data_bytes = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data_bytes)))
        self.end_headers()
        self.wfile.write(data_bytes)

    def do_GET(self):
        url = urlparse(self.path)
        self.server.requests.append(self.path)  # type: ignore[attr-defined]
        if url.path == "/api/lots":
            return self._json(lots_page(int(parse_qs(url.query).get("page", ["1"])[0])))
        if url.path == "/spa":
            return self._send(SPA_PAGE)
        if url.path == "/jslist":
            return self._send(JS_LIST_PAGE)
        if url.path.startswith("/lot/"):
            lot = next((x for x in LOTS if f"/lot/{x['id']}" == url.path), None)
            if lot is None:
                return self._send("not found", 404)
            price = float(lot["currentPrice"])
            if getattr(self.server, "dynamic", False):
                self.server.detail_hits += 1  # type: ignore[attr-defined]
                price += 1000 * self.server.detail_hits  # type: ignore[attr-defined]
            return self._send(DETAIL_PAGE.format(title=lot["lotName"], price=f"{price:,.0f}"))
        if url.path == "/login":
            return self._send(LOGIN_PAGE)
        if url.path == "/list":
            sid = getattr(self.server, "valid_sid", "ok")
            if self.server.require_login and f"sid={sid}" not in (self.headers.get("Cookie") or ""):
                return self._send("", 302, {"Location": "/login"})
            page = parse_qs(url.query).get("page", ["1"])[0]
            html = fixture_html(f"list_page{page}.html").replace("<body>", '<body><div id="user">我的</div>', 1)
            if getattr(self.server, "dynamic", False) and page == "1":
                # 每次访问第 1 页，进行中拍品价格上涨 500、出价次数 +1
                self.server.hits += 1
                n = self.server.hits
                html = html.replace("¥12,500.00", f"¥{12500 + 500 * n:,}.00").replace("出价 18 次", f"出价 {18 + n} 次")
            return self._send(html)
        self._send("not found", 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode()
        if self.path == "/api/search":
            body = json.loads(raw or "{}")
            return self._json(lots_page(int(body.get("pageNo", 1)), int(body.get("pageSize", 2))))
        form = parse_qs(raw)
        if form.get("u") == ["alice"] and form.get("p") == ["secret"]:
            sid = getattr(self.server, "valid_sid", "ok")
            return self._send("", 302, {"Location": "/list", "Set-Cookie": f"sid={sid}; Path=/"})
        self._send(LOGIN_PAGE, 401)


def start(require_login: bool = False, dynamic: bool = False, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.require_login = require_login  # type: ignore[attr-defined]
    server.dynamic = dynamic  # type: ignore[attr-defined]
    server.hits = 0  # type: ignore[attr-defined]
    server.detail_hits = 0  # type: ignore[attr-defined]
    server.requests = []  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


class ProxyHandler(BaseHTTPRequestHandler):
    """极简 HTTP 正向代理（仅 http://），记录经过的请求。"""

    def log_message(self, *args):
        pass

    def do_GET(self):
        import urllib.request

        self.server.seen.append(self.path)  # type: ignore[attr-defined]
        req = urllib.request.Request(self.path, headers={k: v for k, v in self.headers.items() if k.lower() not in ("proxy-connection", "host")})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=10) as resp:
            data = resp.read()
            self.send_response(resp.status)
            for k, v in resp.headers.items():
                if k.lower() not in ("transfer-encoding", "connection"):
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)


def start_proxy() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    server.seen = []  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"
