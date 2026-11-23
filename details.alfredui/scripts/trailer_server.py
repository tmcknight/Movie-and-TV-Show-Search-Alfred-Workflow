#!/usr/bin/python3
"""A tiny local web server for the details window's trailer player.

YouTube's embed player won't play without a referring site (Error 153), and
an Alfred UI webkitview pointed straight at the embed sends none. Serving a
page that embeds the player from http://localhost gives it one.

- GET /trailer?v=<YouTube id>  page with just the player, filling the view
- GET /ping                    "movies-trailer", so load.py knows it's ours

Listens on localhost only. The player page pings it every HEARTBEAT_SECONDS
while open, so it exits IDLE_SECONDS after the trailer window closes.
Playback itself talks to YouTube, not to this server.
"""
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = '127.0.0.1'
HEARTBEAT_SECONDS = 5
IDLE_SECONDS = 15
PING_REPLY = b'movies-trailer'
# Height of YouTube's title and logo strips, cropped off top and bottom.
CROP = 60
VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{6,20}$')

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="referrer" content="strict-origin-when-cross-origin">
<style>
html,body{{margin:0;height:100%;background:#000;overflow:hidden;
  font:13px -apple-system,sans-serif;color:#fff;user-select:none;cursor:default}}
/* YouTube no longer lets embeds hide the title, channel and logo overlays,
   which sit at the player's top and bottom edges. Make the player taller
   than the window so the video (centred, letterboxed) fills it and those
   overlays fall outside, and ignore the mouse so hover overlays never show. */
#player{{position:absolute;left:0;width:100%;top:-{crop}px;height:calc(100% + {crop2}px);
  border:0;pointer-events:none}}
#shield{{position:absolute;inset:0}}
#big{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  font-size:64px;background:rgba(0,0,0,.45);opacity:0;transition:opacity .2s}}
body.paused #big,body.ended #big{{opacity:1}}
body.ended #big{{background:#000}}
#bar{{position:absolute;left:0;right:0;bottom:0;padding:24px 16px 12px;display:flex;
  align-items:center;gap:12px;background:linear-gradient(transparent,rgba(0,0,0,.7));
  opacity:0;transition:opacity .25s}}
body.active #bar,body.paused #bar{{opacity:1}}
#track{{flex:1;height:4px;background:rgba(255,255,255,.3);border-radius:2px;cursor:pointer;position:relative}}
#track:hover{{height:6px}}
#fill{{position:absolute;left:0;top:0;bottom:0;width:0;background:#fff;border-radius:2px}}
button{{background:none;border:0;color:#fff;font-size:15px;cursor:pointer;padding:0}}
#time{{font-variant-numeric:tabular-nums;opacity:.85}}
</style></head>
<body class="paused">
<div id="player"></div>
<div id="shield"></div>
<div id="big">&#9654;</div>
<div id="bar">
  <button id="play" title="Play/pause (Space)">&#9654;</button>
  <div id="track"><div id="fill"></div></div>
  <span id="time">0:00 / 0:00</span>
  <button id="mute" title="Mute (M)">&#128266;</button>
</div>
<script>
var player, idle;
var $ = function (id) {{ return document.getElementById(id); }};
function fmt(t) {{ t = Math.floor(t || 0); return Math.floor(t / 60) + ':' + ('0' + t % 60).slice(-2); }}
function onYouTubeIframeAPIReady() {{
  player = new YT.Player('player', {{
    videoId: '{id}', host: 'https://www.youtube.com',
    playerVars: {{autoplay: 1, controls: 0, fs: 0, rel: 0, iv_load_policy: 3,
                  disablekb: 1, playsinline: 1, origin: '{origin}'}},
    events: {{onStateChange: function (e) {{
      document.body.classList.toggle('paused', [YT.PlayerState.PAUSED, YT.PlayerState.CUED, YT.PlayerState.UNSTARTED].indexOf(e.data) >= 0);
      document.body.classList.toggle('ended', e.data === YT.PlayerState.ENDED);
      $('play').innerHTML = e.data === YT.PlayerState.PLAYING ? '&#10074;&#10074;' : '&#9654;';
      $('big').innerHTML = e.data === YT.PlayerState.ENDED ? '&#8635;' : '&#9654;';
    }}}}
  }});
  setInterval(function () {{
    if (!player || !player.getDuration) return;
    var d = player.getDuration(), t = player.getCurrentTime();
    $('fill').style.width = d ? (100 * t / d) + '%' : 0;
    $('time').textContent = fmt(t) + ' / ' + fmt(d);
  }}, 250);
}}
function toggle() {{
  if (!player || !player.getPlayerState) return;
  var s = player.getPlayerState();
  if (s === YT.PlayerState.PLAYING) player.pauseVideo();
  else {{ if (s === YT.PlayerState.ENDED) player.seekTo(0); player.playVideo(); }}
}}
function toggleMute() {{
  if (!player) return;
  if (player.isMuted()) player.unMute(); else player.mute();
  $('mute').innerHTML = player.isMuted() ? '&#128263;' : '&#128266;';
}}
function wake() {{
  document.body.classList.add('active');
  clearTimeout(idle);
  idle = setTimeout(function () {{ document.body.classList.remove('active'); }}, 2000);
}}
$('shield').onclick = toggle; $('big').onclick = toggle; $('play').onclick = toggle;
$('mute').onclick = toggleMute;
setInterval(function () {{ fetch('/ping', {{cache: 'no-store'}}).catch(function () {{}}); }}, {heartbeat});
$('track').onclick = function (e) {{
  var r = this.getBoundingClientRect();
  player.seekTo(player.getDuration() * (e.clientX - r.left) / r.width, true);
}};
document.onmousemove = wake;
document.onkeydown = function (e) {{
  // The web view has the keyboard, so the window's own ⎋ never fires; ask
  // Alfred to close it through the URL hash (page_event.zsh).
  if (e.key === 'Escape') {{ location.hash = 'close&t=' + Date.now(); return; }}
  if (e.key === ' ') {{ toggle(); e.preventDefault(); }}
  else if (e.key === 'm') toggleMute();
  else if (e.key === 'ArrowRight') player.seekTo(player.getCurrentTime() + 5, true);
  else if (e.key === 'ArrowLeft') player.seekTo(player.getCurrentTime() - 5, true);
  wake();
}};
</script>
<script src="https://www.youtube.com/iframe_api"></script>
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
                page = PAGE.format(id=video, origin=origin, crop=CROP, crop2=2 * CROP,
                                   heartbeat=HEARTBEAT_SECONDS * 1000)
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
            time.sleep(1)
        server.shutdown()

    threading.Thread(target=stop_when_idle, daemon=True).start()
    server.serve_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main(int(sys.argv[1])))
