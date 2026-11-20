#!/usr/bin/python3
"""A tiny local web server for the details window's trailer player.

YouTube's embed player won't play without a referring site (Error 153), and
an Alfred UI webkitview pointed straight at the embed sends none. Serving a
page that embeds the player from http://localhost gives it one.

- GET /trailer?v=<YouTube id>  page with just the player, filling the view
- GET /ping                    "movies-trailer", so load.py knows it's ours

Listens on localhost only and exits after IDLE_SECONDS without a request.
Playback itself talks to YouTube, not to this server.
"""
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = '127.0.0.1'
IDLE_SECONDS = 300
PING_REPLY = b'movies-trailer'
VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{6,20}$')

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="referrer" content="strict-origin-when-cross-origin">
<style>html,body{{margin:0;height:100%;background:#000;overflow:hidden}}
iframe{{border:0;width:100%;height:100%}}</style></head>
<body><iframe src="https://www.youtube.com/embed/{id}?autoplay=1&amp;rel=0&amp;playsinline=1&amp;origin={origin}"
allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe>
</body></html>
"""

last_request = time.monotonic()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global last_request
        last_request = time.monotonic()
        url = urllib.parse.urlsplit(self.path)
        if url.path == '/ping':
            return self.reply(200, 'text/plain', PING_REPLY)
        if url.path == '/trailer':
            video = urllib.parse.parse_qs(url.query).get('v', [''])[0]
            if VIDEO_ID.match(video):
                origin = f'http://localhost:{self.server.server_port}'
                page = PAGE.format(id=video, origin=urllib.parse.quote(origin, safe=''))
                return self.reply(200, 'text/html; charset=utf-8', page.encode())
        self.reply(404, 'text/plain', b'not found')

    def reply(self, status, kind, body):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main(port):
    server = ThreadingHTTPServer((HOST, port), Handler)

    def stop_when_idle():
        while time.monotonic() - last_request < IDLE_SECONDS:
            time.sleep(5)
        server.shutdown()

    threading.Thread(target=stop_when_idle, daemon=True).start()
    server.serve_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main(int(sys.argv[1])))
