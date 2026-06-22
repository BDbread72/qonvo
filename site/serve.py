#!/usr/bin/env python3
"""
Qonvo 사이트 정적 서버 — 의존성 0 (stdlib만).
home 서버에서 빌드된 dist/ 를 그대로 서빙한다.

    python serve.py            # 0.0.0.0:8080, ./dist 서빙
    python serve.py 80         # 포트 지정
    python serve.py 8080 dist  # 포트 + 디렉토리 지정

큰 다운로드(qonvo.exe 등)도 Range 요청을 지원하도록
SimpleHTTPRequestHandler 를 그대로 사용한다(Py3.7+ 부분요청 지원).
"""
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
ROOT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "dist")


class Handler(SimpleHTTPRequestHandler):
    # 단일 페이지라 라우팅은 없지만, 없는 경로는 index.html 로 폴백한다.
    def send_head(self):
        path = self.translate_path(self.path)
        if not os.path.exists(path) and not self.path.startswith("/downloads"):
            self.path = "/index.html"
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), format % args))
        sys.stdout.flush()


if __name__ == "__main__":
    if not os.path.isdir(ROOT):
        sys.exit(f"[error] 서빙할 디렉토리가 없습니다: {ROOT}\n  먼저 `npm run build` 로 dist/ 를 만드세요.")
    handler = partial(Handler, directory=ROOT)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), handler)
    print(f"[qonvo-site] serving {ROOT}  ->  http://0.0.0.0:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[qonvo-site] stopped")
