#!/usr/bin/python3
"""Fill the details window with TMDb/OMDb data.

Every row of the details list is connected straight to the Alfred UI object
(a Conditional in between would stop ⎋ returning to the list), so the row's
arg arrives here and decides what happens:

- "load.py <arg> --route" runs on the window's "preload" event, which holds
  the window back until it returns, so it must be fast. A "||"-delimited list
  of URLs (every row but the title row) is opened and the UI finishes before
  it ever shows. Anything else prints {}.
- "load.py m:<tmdb id>" / "t:<tmdb id>" runs on "launched" (the title row):
  print JSON actions that fill the window, fetching the details first unless
  they are cached.
- "load.py <ref> --warm" is started in the background by the details list,
  which has just fetched the item, so the window is cached before ↩. When it
  is, the preload step fills the window before it shows.
"""
import json
import os
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib

WORKFLOW_DIR = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

# media.py loads templates and icons by paths relative to the workflow folder.
os.chdir(WORKFLOW_DIR)
sys.path.insert(0, WORKFLOW_DIR)

import media  # noqa: E402
import openurls  # noqa: E402

IMAGE_URL = 'https://image.tmdb.org/t/p/'
POSTER_SIZE = 'w342'
# The backdrop sits dimmed behind a scrim, so the smallest size is plenty, and
# a small image can't push the window taller when it resizes to fit.
BACKDROP_SIZE = 'w300'
# Alfred UI has no blur or text shadow. Shrinking the backdrop this far and
# letting the imageview scale it up blurs it, so no detail fights the text.
BACKDROP_BLUR_WIDTH = 120
# The backdrop sits right of the poster and fades in over its left third.
BACKDROP_FADE = 0.35
LOGO_SIZE = 'w500'
LOGO_MAX = {"width": 420, "height": 64}
LINK_ICON_SIZE = {"width": 16, "height": 16}
LINK_ROW_HEIGHT = 24
LINK_COL_WIDTH = 250
LINK_GAP = 12
CREDIT_KEY_WIDTH = 64
BLANK_LABEL = {"type": "label", "text": " "}
OVERVIEW_MAX_CHARS = 550
CACHE_SECONDS = 600
CREDIT_NAMES = {'Actors': 'Cast'}
SITE_NAMES = {'Rotten Tomatoes Audience Score': 'Audience'}
# SF Symbols for link rows that have no icon file.
LINK_SYMBOLS = {'TMDb': 'film'}


def route(ref):
    if '://' in (ref or ''):
        openurls.main(ref)
        emit({"finish": {"args": ""}})
    else:
        # Fill the window before it shows if the details list warmed it up;
        # an empty result is logged as invalid JSON, so send {} instead.
        emit(read_cache(window_cache(ref)) or {})
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
    """JSON actions that fill the window, from a TMDb item and the details
    list rows that media.show_item_info made for it."""
    header, rest = rows[0], rows[1:]
    links = [row for row in rest if row.get('arg') and '||' not in row['arg']]
    people = [row for row in rest if not row.get('arg')]

    update = {
        "title": {"text": header['title']},
        "subtitle": {"text": header['subtitle']},
        "overview": {"text": clip(item.get('overview') or 'No overview available.',
                                  OVERVIEW_MAX_CHARS)},
    }
    poster = download_image(item.get('poster_path'), POSTER_SIZE)
    if poster:
        update["poster"] = {"image": {"type": "file", "src": poster}}
    backdrop = blurred(download_image(item.get('backdrop_path'), BACKDROP_SIZE))
    if backdrop:
        update["backdrop"] = {"image": {"type": "file", "src": backdrop}}

    replace = {
        "credits": credits_form(people),
        "links": links_grid(links),
    }
    logo = title_logo(item, header['title'])
    if logo:
        replace["title"] = logo

    return {
        "update": {"ids": update, "relayout": True},
        "replace": {"ids": replace},
        # Processed after update/replace: fit the window's height to them.
        "window": {"resize": {"height": "auto"}},
    }


def window_cache(ref):
    return os.path.join(media.CACHEDIR, 'window-' + (ref or '').replace(':', '-') + '.json')


def read_cache(path):
    try:
        if time.time() - os.path.getmtime(path) < CACHE_SECONDS:
            with open(path) as f:
                return json.load(f)
    except (OSError, ValueError):
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


def credits_form(people):
    rows = []
    for row in people:
        if not row['title'] or row['title'] == 'N/A':
            continue
        rows.append(hrow([
            {"type": "label", "text": CREDIT_NAMES.get(row['subtitle'], row['subtitle']),
             "colour": "secondaryLabelColor", "textalign": "left",
             "constraints": {"size": {"width": CREDIT_KEY_WIDTH}}},
            {"type": "label", "text": row['title'], "tooltip": row['title'],
             "linebreak": "truncatetail", "selectable": True},
        ]))
    return vstack("credits", rows, spacing=4)


def links_grid(links):
    """Two left-aligned columns of links. Alfred UI centres content in grid
    cells and in fixed-width horizontal rows, but a vertical inline column
    with left-pinned rows lines up cleanly."""
    cells = [link_cell(i, row) for i, row in enumerate(links)]
    half = -(-len(cells) // 2)
    columns = [cells[:half], cells[half:]]
    if len(columns[1]) < len(columns[0]):
        # The row centres a shorter column vertically; pad it to keep the
        # two columns' rows level.
        spacer = hrow([BLANK_LABEL])
        spacer["constraints"] = {"position": "left", "size": {"height": LINK_ROW_HEIGHT}}
        columns[1].append(spacer)
    return {"type": "view", "id": "links", "layout": "inline", "direction": "horizontal",
            "spacing": LINK_GAP, "constraints": {"position": "left"},
            "components": [{"type": "view", "layout": "inline", "direction": "vertical",
                            "spacing": 2,
                            "constraints": {"size": {"width": LINK_COL_WIDTH}},
                            "components": column or [BLANK_LABEL]}
                           for column in columns]}


def hrow(components, spacing=8):
    # A vertical inline layout centres narrower rows; pin them left.
    return {"type": "view", "layout": "inline", "direction": "horizontal",
            "spacing": spacing, "constraints": {"position": "left"},
            "components": components}


def vstack(view_id, components, spacing):
    # Never leave a view empty: an empty inline view inside a border region
    # gets a NaN height and crashes Alfred 6.0.2 (AUIInlineLayout).
    return {"type": "view", "id": view_id, "layout": "inline", "direction": "vertical",
            "spacing": spacing, "components": components or [BLANK_LABEL]}


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


def link_cell(index, row):
    """Icon + borderless button. A button's own image overlaps its text, so
    the icon is a separate imageview beside it."""
    components = []
    icon = row.get('icon', {}).get('path')
    symbol = LINK_SYMBOLS.get(row['title'])
    if symbol:
        components.append({
            "type": "imageview",
            "image": {"type": "symbol", "src": symbol},
            "constraints": {"size": LINK_ICON_SIZE, "position": "centre"},
        })
    elif icon:
        components.append({
            "type": "imageview",
            "image": {"type": "file", "src": os.path.join(WORKFLOW_DIR, icon)},
            "constraints": {"size": LINK_ICON_SIZE, "position": "centre"},
        })
    components.append({
        "type": "button",
        "id": f"link{index}",
        "text": link_text(row),
        "bordered": False,
        "tooltip": row['arg'] + (f"  (⌘{index + 1})" if index < 9 else ''),
        "events": [{
            "triggers": "actioned",
            "action": {"type": "json", "src": {"open": {"type": "url", "src": row['arg']}}},
        }],
    })
    if index < 9:
        # ⌘1–⌘9 open the links in order, like Alfred's own result shortcuts.
        components[-1]["shortcut"] = f"⌘{index + 1}"
    view = hrow(components, spacing=6)
    view["constraints"] = {"position": "left", "size": {"height": LINK_ROW_HEIGHT}}
    return view


def title_logo(item, title):
    """The title as the film's clear logo (the transparent PNGs Plex shows),
    sized to the logo's own aspect ratio: an imageview centres an image that
    doesn't fill it, which would push the logo off the left edge."""
    logos = [logo for logo in item.get('images', {}).get('logos', [])
             if logo.get('file_path', '').endswith('.png') and logo.get('aspect_ratio')]
    if not logos:
        return None
    # TMDb lists the search language, English and language-less logos mixed;
    # take the first in that order of preference.
    language = os.environ.get('search_language', 'en')
    rank = {language: 0, 'en': 1, None: 2}
    logo = min(logos, key=lambda logo: rank.get(logo.get('iso_639_1'), 3))
    path = download_image(logo['file_path'], LOGO_SIZE)
    if not path:
        return None
    ratio = logo['aspect_ratio']
    height = min(LOGO_MAX['height'], LOGO_MAX['width'] / ratio)
    return {"type": "imageview", "id": "title", "resize": "fit", "tooltip": title,
            "image": {"type": "file", "src": path},
            "constraints": {"position": "left",
                            "size": {"width": round(height * ratio), "height": round(height)}}}


def blurred(path):
    """A tiny copy of the image at path whose left edge fades out, or path
    itself if that fails. The imageview scales it up, which blurs it; Alfred
    UI draws no gradients, so the fade is baked into the image's alpha."""
    if not path:
        return None
    target = path[:-4] + f'-blur{BACKDROP_BLUR_WIDTH}-fade.png'
    if not os.path.exists(target):
        bmp = target[:-4] + '.bmp'
        try:
            subprocess.run(['/usr/bin/sips', '-s', 'format', 'bmp',
                            '--resampleWidth', str(BACKDROP_BLUR_WIDTH), path, '--out', bmp],
                           check=True, capture_output=True, timeout=5)
            with open(bmp, 'rb') as f:
                width, height, rows = read_bmp(f.read())
            with open(target, 'wb') as f:
                f.write(png_with_fade(width, height, rows))
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            media.log(repr(e))
            return path
        finally:
            if os.path.exists(bmp):
                os.remove(bmp)
    return target


def read_bmp(data):
    """Pixel rows of an uncompressed 24/32-bit BMP (what sips writes), top
    row first, as lists of (r, g, b)."""
    if data[:2] != b'BM':
        raise ValueError('not a BMP')
    offset, = struct.unpack_from('<I', data, 10)
    width, height, _, bpp, compression = struct.unpack_from('<iiHHI', data, 18)
    if bpp not in (24, 32) or compression not in (0, 3):
        raise ValueError(f'unsupported BMP: {bpp} bpp, compression {compression}')
    step = bpp // 8
    stride = (width * step + 3) & ~3
    rows = []
    for y in range(abs(height)):
        start = offset + y * stride
        rows.append([(data[i + 2], data[i + 1], data[i])
                     for i in range(start, start + width * step, step)])
    if height > 0:  # stored bottom-up
        rows.reverse()
    return width, abs(height), rows


def png_with_fade(width, height, rows):
    """An RGBA PNG of rows whose alpha rises smoothly from 0 at the left edge
    to opaque BACKDROP_FADE of the way across."""
    fade = max(1, round(width * BACKDROP_FADE))
    alphas = []
    for x in range(width):
        t = min(1.0, x / fade)
        alphas.append(round(255 * t * t * (3 - 2 * t)))  # smoothstep
    raw = b''.join(b'\x00' + bytes(v for (r, g, b), a in zip(row, alphas) for v in (r, g, b, a))
                   for row in rows)

    def chunk(kind, body):
        return (struct.pack('>I', len(body)) + kind + body
                + struct.pack('>I', zlib.crc32(kind + body)))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9))
            + chunk(b'IEND', b''))


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


def error(message):
    # Exit 0: Alfred UI ignores stdout from a script that exits non-zero, which
    # would leave the window stuck on "Loading…".
    # The title is one truncating line, so the message goes in the overview.
    emit({"update": {"ids": {"title": {"text": "Couldn't load details"},
                             "overview": {"text": message}}, "relayout": True},
          "window": {"resize": {"height": "auto"}}})
    return 0


def emit(actions):
    sys.stdout.write(json.dumps(actions))
    sys.stdout.flush()


if __name__ == '__main__':
    ref = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('item')
    mode = sys.argv[2] if len(sys.argv) > 2 else ''
    sys.exit({'--route': route, '--warm': warm}.get(mode, main)(ref))
