"""Strip the public /demo prefix and stream requests to the ICT8 app."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.forward()

    def do_HEAD(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def do_OPTIONS(self):
        self.forward()

    def forward(self):
        path = self.path
        if path == "/demo":
            self.send_response(308)
            self.send_header("Location", "/demo/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.startswith("/demo/"):
            path = path[len("/demo"):]

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else None
        headers = {key: value for key, value in self.headers.items()
                   if key.lower() not in {"host", "connection", "content-length", "transfer-encoding"}}
        request = Request("http://127.0.0.1:8031" + path, data=body, headers=headers,
                          method=self.command)
        try:
            response = urlopen(request, timeout=300)
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError, OSError):
            self.send_error(502, "ICT8 origin is unavailable")
            return

        with response:
            self.send_response(response.status)
            for key, value in response.headers.items():
                if key.lower() not in {"connection", "transfer-encoding", "keep-alive", "upgrade"}:
                    self.send_header(key, value)
            if not response.headers.get("Content-Length"):
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command != "HEAD":
                while True:
                    chunk = response.read1(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()

    def log_message(self, _format, *_args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8032), Handler).serve_forever()
