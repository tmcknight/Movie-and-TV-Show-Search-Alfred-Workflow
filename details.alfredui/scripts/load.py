#!/usr/bin/python3
"""Fill the details window with TMDb/OMDb data.

The window is one webkitview showing a page built from page.html. Every row
of the details list is connected straight to the Alfred UI object (a
Conditional in between would stop ⎋ returning to the list), so the row's arg
arrives here and decides what happens:

- "load.py <arg> --route" runs on the window's "preload" event, which holds
  the window back until it returns, so it must be fast. A "||"-delimited list
  of URLs (every row but the title row) is opened and the UI finishes before
  it ever shows. For a title, the page is the cached details page if the
  details list warmed it up, else the loading skeleton.
- "load.py m:<tmdb id>" / "t:<tmdb id>" runs on "launched" (the title row):
  build the details page, fetching the details first unless they are cached.
- "load.py <ref> --warm" is started in the background by the details list,
  which has just fetched the item, so the page is ready before ↩.

The page talks back through its URL hash; see page.html and page_event.zsh.
"""
import base64
import html
import json
import os
import plistlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

WORKFLOW_DIR = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
PAGE_TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'page.html')

# media.py loads templates and icons by paths relative to the workflow folder.
os.chdir(WORKFLOW_DIR)
sys.path.insert(0, WORKFLOW_DIR)

import media  # noqa: E402
import openurls  # noqa: E402

IMAGE_URL = 'https://image.tmdb.org/t/p/'
POSTER_SIZE = 'w342'
# The page blurs the backdrop, so the smallest size is plenty.
BACKDROP_SIZE = 'w300'
LOGO_SIZE = 'w500'
TRAILER_PORT = 47826
TRAILER_PAGE_URL = 'http://localhost:{port}/trailer?v={key}'
# Fallback when the local player server can't start: YouTube's own page.
TRAILER_FALLBACK_URL = 'https://m.youtube.com/watch?v={key}'
VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{6,20}$')
TRAILER_TRIGGER_DELAY = 0.8
# Workflow setting: play trailers in the trailer window instead of on YouTube.
TRAILERS_IN_ALFRED = os.environ.get('trailers_in_alfred', '0') == '1'
OVERVIEW_MAX_CHARS = 550
CACHE_SECONDS = 600
CREDIT_NAMES = {'Actors': 'Cast'}
SITE_NAMES = {'Rotten Tomatoes Audience Score': 'Audience'}
MIME_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}
FILM_ICON = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3">'
             '<rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/>'
             '<path d="M4.5 2.5v11M11.5 2.5v11M1.5 6h3M1.5 10h3M11.5 6h3M11.5 10h3"/></svg>')
# Link rows that have no icon file.
LINK_SVGS = {'TMDb': FILM_ICON}


def route(ref):
    ref = ref or ''
    if TRAILERS_IN_ALFRED and ref.startswith(media.YOUTUBE_WATCH_URL) and '||' not in ref:
        # The details list's Watch Trailer row: hand the video to the trailer
        # window (connected after this UI) instead of opening the browser.
        emit(play_trailer(ref))
    elif '://' in ref:
        openurls.main(ref)
        emit({"finish": {"args": ""}})
    else:
        # Show the details page straight away if the details list warmed it
        # up, else the loading skeleton until "launched" builds it.
        emit(read_cache(window_cache(ref)) or show_page(skeleton_page()))
    return 0


def main(ref):
    if '://' in (ref or ''):
        return 0  # already opened by route() on preload

    cached = read_cache(window_cache(ref))
    if cached:
        emit(cached)
        return 0

    kind, _, tmdb_id = (ref or '').partition(':')
    if kind not in ('m', 't') or not tmdb_id.isdigit():
        return error(f"Unknown item '{ref}'.")

    media_type = 'movie' if kind == 'm' else 'tv'
    try:
        item = media.get_tmdb_info(kind, tmdb_id, media.DEFAULT_TMDB_API_KEY,
                                   os.environ.get('search_language', 'en'))
        media.show_item_info(item, media_type)
    except urllib.error.HTTPError as e:
        media.log(repr(e))
        if e.code == 404:
            return error('Details not found.')
        return error('Something went wrong. Please try again later.')
    except Exception as e:
        media.log(repr(e))
        return error('Something went wrong. Please check your internet connection.')

    actions = build(item, media.items)
    write_cache(window_cache(ref), actions)
    emit(actions)
    return 0


def warm(ref):
    """Run in the background by the details list (media.py), which has just
    fetched this item: build the window from its data and download the poster
    now, so the window opens already filled in."""
    data = read_cache(media.details_cache(ref))
    if data:
        write_cache(window_cache(ref), build(data['item'], data['rows']))
    return 0


def build(item, rows):
    """JSON actions that show the details page, from a TMDb item and the
    details list rows that media.show_item_info made for it."""
    header, rest = rows[0], rows[1:]
    links = [row for row in rest if row.get('arg') and '||' not in row['arg']]
    people = [row for row in rest
              if not row.get('arg') and row['title'] and row['title'] != 'N/A']

    poster = download_image(item.get('poster_path'), POSTER_SIZE)
    backdrop = download_image(item.get('backdrop_path'), BACKDROP_SIZE)
    logo = title_logo(item)
    title = esc(header['title'])
    body = [
        f'<div class="backdrop" style="background-image: url({data_uri(backdrop)})"></div>'
        if backdrop else '',
        poster_html(poster),
        '<div class="info">',
        '<div class="heading">',
        f'<img class="logo" src="{data_uri(logo)}" alt="{title}" title="{title}">' if logo
        else f'<h1 class="selectable">{title}</h1>',
        f'<div class="subtitle">{esc(header.get("subtitle") or "")}</div>',
        '</div>',
        '<p class="overview selectable">'
        + esc(clip(item.get('overview') or 'No overview available.', OVERVIEW_MAX_CHARS))
        + '</p>',
        credits_html(people),
        links_html(links),
        '</div>',
    ]
    ref = header.get('arg') or f"m:{item.get('id')}"
    return show_page(write_page('page-' + ref.replace(':', '-'), '', ''.join(body)))


def show_page(path):
    return {"update": {"ids": {"page": {"src": path}}}}


def write_page(name, page_class, body):
    with open(PAGE_TEMPLATE) as f:
        page = f.read().replace('{{class}}', page_class).replace('{{body}}', body)
    path = os.path.join(media.CACHEDIR, name + '.html')
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        f.write(page)
    os.replace(tmp, path)
    return path


def skeleton_page():
    """The page the window opens on while the details load: shimmering bars
    where the poster, title, overview, credits and links will go."""
    path = os.path.join(media.CACHEDIR, 'skeleton.html')
    try:
        if os.path.getmtime(path) >= os.path.getmtime(PAGE_TEMPLATE):
            return path
    except OSError:
        pass
    bone = '<div class="bone" style="width: {}; height: {}px"></div>'.format
    lines = ''.join(bone(w, 13) for w in ('100%', '96%', '100%', '62%'))
    links = ''.join(bone('60%', 14) for _ in range(6))
    body = ('<div class="poster"></div><div class="info">'
            f'<div class="heading" style="gap: 8px">{bone("55%", 26)}{bone("40%", 14)}</div>'
            f'<div class="heading" style="gap: 7px">{lines}</div>'
            f'<div class="heading" style="gap: 8px; margin-top: 6px">{bone("30%", 14)}{bone("48%", 14)}</div>'
            f'<div class="links">{links}</div></div>')
    return write_page('skeleton', 'skeleton', body)


def poster_html(path):
    if not path:
        return f'<div class="poster"><div class="placeholder" style="width: 48px; margin: auto">{FILM_ICON}</div></div>'
    name = os.path.basename(path)
    return (f'<div class="poster"><img src="{data_uri(path)}" alt="" draggable="false">'
            f'<button data-hash="copy={esc(name)}">Copy</button></div>')


def credits_html(people):
    if not people:
        return ''
    rows = ''.join(
        f'<dt>{esc(CREDIT_NAMES.get(row["subtitle"], row["subtitle"]))}</dt>'
        f'<dd class="selectable" title="{esc(row["title"])}">{esc(row["title"])}</dd>'
        for row in people)
    return f'<dl class="credits">{rows}</dl>'


def links_html(links):
    """Two columns of links, filled down the first column, then the second."""
    if not links:
        return ''
    cells = [link_html(i, row) for i, row in enumerate(links)]
    half = -(-len(cells) // 2)
    order = [cell for pair in zip(cells[:half], cells[half:] + ['<span></span>']) for cell in pair]
    return f'<nav class="links">{"".join(order)}</nav>'


def link_html(index, row):
    url = row['arg']
    title = row['title']
    icon = LINK_SVGS.get(title)
    if not icon and row.get('icon', {}).get('path'):
        icon = f'<img src="{data_uri(os.path.join(WORKFLOW_DIR, row["icon"]["path"]))}" alt="">'
    label = esc(link_text(row)).replace('  ·  ', ' <span class="dot">·</span> ')
    key = f'<span class="key">⌘{index + 1}</span>' if index < 9 else ''
    if TRAILERS_IN_ALFRED and url.startswith(media.YOUTUBE_WATCH_URL):
        # Opens the trailer window through page_event.zsh instead.
        target = f'href="#" data-hash="trailer={esc(url[len(media.YOUTUBE_WATCH_URL):])}"'
    else:
        target = f'href="{esc(url)}"'
    return f'<a {target} title="{esc(url)}">{icon or ""}<span class="label">{label}</span>{key}</a>'


def esc(text):
    return html.escape(str(text), quote=True)


_data_uris = {}


def data_uri(path):
    """The page's images are inlined, so it loads in one go and needs no
    file access beyond itself."""
    if path not in _data_uris:
        mime = MIME_TYPES.get(os.path.splitext(path)[1].lower(), 'image/png')
        with open(path, 'rb') as f:
            _data_uris[path] = f'data:{mime};base64,' + base64.b64encode(f.read()).decode()
    return _data_uris[path]


def title_logo(item):
    """Path of the film's clear logo (the transparent PNGs Plex shows), or
    None."""
    logos = [logo for logo in item.get('images', {}).get('logos', [])
             if logo.get('file_path', '').endswith('.png')]
    if not logos:
        return None
    # TMDb lists the search language, English and language-less logos mixed;
    # take the first in that order of preference.
    language = os.environ.get('search_language', 'en')
    rank = {language: 0, 'en': 1, None: 2}
    logo = min(logos, key=lambda logo: rank.get(logo.get('iso_639_1'), 3))
    return download_image(logo['file_path'], LOGO_SIZE)


def error(message):
    # Exit 0: Alfred UI ignores stdout from a script that exits non-zero, which
    # would leave the window stuck on its skeleton.
    body = (poster_html(None) + '<div class="info"><div class="heading">'
            "<h1>Couldn't load details</h1></div>"
            f'<p class="overview selectable">{esc(message)}</p></div>')
    emit(show_page(write_page('error', '', body)))
    return 0


def window_cache(ref):
    # The trailer button depends on the trailer setting, so cache per setting.
    suffix = '-trailers' if TRAILERS_IN_ALFRED else ''
    return os.path.join(media.CACHEDIR, 'window-' + (ref or '').replace(':', '-') + suffix + '.json')


def read_cache(path):
    try:
        if time.time() - os.path.getmtime(path) < CACHE_SECONDS:
            with open(path) as f:
                actions = json.load(f)
            # The cached actions point at a page file; make sure it's there.
            if os.path.exists(actions["update"]["ids"]["page"]["src"]):
                return actions
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def write_cache(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, path)


def clip(text, limit):
    """Trim to a word boundary; a wrapping label has no ellipsis of its own."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(' ', 1)[0].rstrip(',.;:—-') + '…'


def link_text(row):
    """Short label for a details row, e.g. 'IMDb 8.4 · 1.3M votes'."""
    title, subtitle = row['title'], row.get('subtitle', '')
    if title[:1].isdigit():
        # Rating rows: score in the title, "Site (details)" in the subtitle.
        site, _, details = subtitle.partition(' (')
        site = SITE_NAMES.get(site, site)
        votes = re.match(r'([\d,]+) votes', details)
        if votes:
            return f"{site} {title}  ·  {compact(int(votes.group(1).replace(',', '')))} votes"
        return f"{site} {title}"
    if title == 'JustWatch' and subtitle:
        # "Stream: X | Buy: Y | Rent: Z" → "X"
        return f"JustWatch  ·  {subtitle.split(' | ')[0].split(': ', 1)[-1]}"
    return title


def compact(n):
    for size, suffix in ((1_000_000, 'M'), (1_000, 'K')):
        if n >= size:
            return f"{n / size:.1f}".rstrip('0').rstrip('.') + suffix
    return str(n)


def play_trailer(url):
    """Close this window and open the trailer window, which is connected
    after it: an Alfred UI can't go full screen inside Alfred, and Alfred's
    web view can't take the YouTube player full screen itself."""
    return {"finish": {"args": url[len(media.YOUTUBE_WATCH_URL):]}}


def open_trailer(key):
    """Trailer button in the details window. A finish action with args sent
    after this window shows doesn't reach the connected trailer window, so
    start it through the workflow's "trailer" External Trigger instead. This
    window's own (empty) finish also starts the trailer window, which closes
    straight away without an id; trigger once that's gone, or Alfred drops
    the second launch."""
    if VIDEO_ID.match(key or ''):
        with open(os.path.join(WORKFLOW_DIR, 'info.plist'), 'rb') as f:
            bundle = plistlib.load(f)['bundleid']
        script = (f'tell application id "com.runningwithcrayons.Alfred" to run trigger "trailer" '
                  f'in workflow "{bundle}" with argument "{key}"')
        subprocess.Popen(['/bin/sh', '-c', f'sleep {TRAILER_TRIGGER_DELAY}; exec /usr/bin/osascript -e "$1"',
                          'sh', script],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    emit({"finish": {}})
    return 0


def player_check(key):
    """Trailer window, preload: it is also connected to every other way this
    window finishes (e.g. opening a link), so close without showing unless
    it was handed a YouTube id."""
    emit({} if VIDEO_ID.match(key or '') else {"finish": {}})
    return 0


def player(key):
    """Trailer window, launched: load the player page (starting the local
    server can take a moment, so this runs async)."""
    if VIDEO_ID.match(key or ''):
        emit({"update": {"ids": {"player": {"src": trailer_page(key)}}}})
    return 0


def trailer_page(key):
    """The local clean player page for a YouTube id, starting its server if
    needed, or YouTube's own page if that fails."""
    if VIDEO_ID.match(key or '') and trailer_server():
        return TRAILER_PAGE_URL.format(port=TRAILER_PORT, key=key)
    return TRAILER_FALLBACK_URL.format(key=key)


def trailer_server():
    """True once our player server answers on TRAILER_PORT, starting it if
    nothing is listening there."""
    if ping_trailer_server():
        return True
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trailer_server.py')
    try:
        subprocess.Popen([sys.executable, script, str(TRAILER_PORT)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        media.log(repr(e))
        return False
    for _ in range(20):
        time.sleep(0.1)
        if ping_trailer_server():
            return True
    return False


def ping_trailer_server():
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{TRAILER_PORT}/ping', timeout=0.5) as r:
            return r.read() == b'movies-trailer'
    except OSError:
        return False


def download_image(path, size):
    """Alfred UI images load from files only, so cache TMDb images locally."""
    if not path:
        return None
    target = os.path.join(media.CACHEDIR, size + path.replace('/', '-'))
    if not os.path.exists(target):
        try:
            urllib.request.urlretrieve(IMAGE_URL + size + path, target)
        except Exception as e:
            media.log(repr(e))
            return None
    return target


def emit(actions):
    sys.stdout.write(json.dumps(actions))
    sys.stdout.flush()


if __name__ == '__main__':
    ref = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('item')
    mode = sys.argv[2] if len(sys.argv) > 2 else ''
    modes = {'--route': route, '--warm': warm, '--open-trailer': open_trailer,
             '--player-check': player_check, '--player': player}
    sys.exit(modes.get(mode, main)(ref))
