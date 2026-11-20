# encoding: utf-8
from __future__ import unicode_literals, print_function

import os
import sys
import argparse
import pathlib
import urllib
import urllib.parse
import urllib.request
import re
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from mako.template import Template
from unicodedata import normalize

ICON_ROOT = '/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources'
ICON_USER = os.path.join(ICON_ROOT, 'UserIcon.icns')
ICON_GROUP = os.path.join(ICON_ROOT, 'GroupIcon.icns')

DEFAULT_TMDB_API_KEY = '0ebad901a16d3bf7f947b0a8d1808c44'
TMDB_API_URL = 'https://api.themoviedb.org/3/'
OMDB_API_URL = 'https://www.omdbapi.com/'
IMDB_URL = 'https://imdb.com/'
TVDB_SERIES_URL = 'https://thetvdb.com/dereferrer/series/'
YOUTUBE_WATCH_URL = 'https://youtube.com/watch?v='
METACRITIC_SEARCH_URL = 'https://metacritic.com/search/'
ROTTEN_TOMATOES_SEARCH_URL = 'https://rottentomatoes.com/search/?search='
LETTERBOXD_URL = 'https://letterboxd.com/tmdb/'
SIMKL_URL = 'https://simkl.com/search'
MOVIECHAT_URL = 'https://moviechat.org/'
TMDB_URL = 'https://www.themoviedb.org/'
CACHEDIR = os.path.expanduser(
    '~/Library/Caches/com.runningwithcrayons.Alfred/Workflow Data/com.mcknight.movies')
HTML_SUMMARY_FILE = os.path.join(CACHEDIR, "item.html")
MAX_SAVED_TITLES = 500
# Search results show a small poster as their icon, at its own 2:3 shape.
RESULT_POSTER_SIZE = 'w92'
RESULT_POSTER_TIMEOUT = 3
NO_POSTER_ICON = 'img/no-poster.png'

INCLUDE_LETTERBOXD = os.environ.get('include_letterboxd', '0') == "1"
INCLUDE_SIMKL = os.environ.get('include_simkl', '0') == "1"
INCLUDE_JUSTWATCH = os.environ.get('include_justwatch', '0') == "1"
INCLUDE_MOVIECHAT = os.environ.get('include_moviechat', '0') == "1"

if not os.path.exists(CACHEDIR):
    os.makedirs(CACHEDIR)

items = []


def main(media_type, query):

    api_key = DEFAULT_TMDB_API_KEY
    language = os.environ["search_language"]
    m = re.match(r'([m|t]):([0-9]*)', query)
    # ⇥/↩ on a search result puts its title in Alfred's search field; map it
    # back to the item the search found.
    ref = read_titles(media_type).get(normalize('NFC', query).strip())
    if ref:
        m = re.match(r'([m|t]):([0-9]*)', ref)
        query = ref
    if query[:2] == 'm:' or query[:2] == 't:' and m.group(2):
        try:
            item = get_tmdb_info(m.group(1), m.group(2), api_key, language)
            log('TMDb info retrieved.')
            if m.group(1) == 'm' or m.group(1) == 't':
                show_item_info(item, media_type)
                warm_details_window(m.group(1) + ':' + m.group(2), item)
        except AttributeError as e:
            log(e)
            items.append({
                "title": "The item was not found"
            })
    else:
        def wrapper():
            return get_tmdb_configuration(api_key)

        try:
            url = f'{TMDB_API_URL}search/{media_type}'
            query = normalize('NFC', query)
            params = {"api_key": api_key,
                      "query": query,
                      "search_type": 'ngram',
                      "language": language}
            results = get_json(url, params)
        except Exception as e:
            log(e)
            items.append({
                "title": "Uh oh... something went wrong",
                "subtitle": "Please check your internet connection."
            })
            output_items()
            return 0

        if 'status_code' in results:
            items.append({"title": "Nothing was found."})
        elif 'results' in results:
            if not results['results']:
                items.append({"title": "Nothing was found."})
            results['results'].sort(key=extract_popularity, reverse=True)
            titles = {}
            seen = {}
            posters = result_posters(results['results'])
            for item in results['results']:
                poster = 'https://image.tmdb.org/t/p/w500' + item['poster_path'] if item['poster_path'] else ''

                if media_type == 'movie':
                    title = item['title']
                    if item.get('release_date', 0):
                        title += ' (' + item['release_date'][:4] + ')'
                else:
                    title = item['name']
                    if item.get('first_air_date', 0):
                        title += ' (' + item['first_air_date'][:4] + ')'
                # Results that share a title and year get numbered, so each
                # one autocompletes a label that maps back to it.
                seen[title] = seen.get(title, 0) + 1
                if seen[title] > 1:
                    title += ' · ' + str(seen[title])
                titles[title] = media_type[:1] + ':' + str(item['id'])
                items.append({
                    "title": title,
                    "arg": str(item['id']),
                    "quicklookurl": poster,
                    "valid": False,
                    "autocomplete": title
                })
                # "fit" keeps the poster's shape in Alfred's square icon; a
                # blank poster shape stands in for a missing one.
                items[-1]["icon"] = {"path": posters.get(item['id']) or NO_POSTER_ICON,
                                     "resize": "fit"}
            save_titles(media_type, titles)
    output_items()


def result_posters(results):
    """Small posters for search results, by TMDb id: Alfred's icons must be
    local files, so download the ones not cached yet, all at once."""
    folder = os.path.join(CACHEDIR, 'posters')
    os.makedirs(folder, exist_ok=True)

    def fetch(result):
        path = result.get('poster_path')
        if not path:
            return result['id'], None
        target = os.path.join(folder, RESULT_POSTER_SIZE + path.replace('/', '-'))
        if not os.path.exists(target):
            try:
                url = 'https://image.tmdb.org/t/p/' + RESULT_POSTER_SIZE + path
                with urllib.request.urlopen(url, timeout=RESULT_POSTER_TIMEOUT) as r:
                    data = r.read()
                with open(target + '.tmp', 'wb') as f:
                    f.write(data)
                os.replace(target + '.tmp', target)
            except Exception as e:
                log(repr(e))
                return result['id'], None
        return result['id'], target

    with ThreadPoolExecutor(max_workers=10) as pool:
        return dict(pool.map(fetch, results))


def titles_cache(media_type):
    return os.path.join(CACHEDIR, 'titles-' + media_type + '.json')


def read_titles(media_type):
    try:
        with open(titles_cache(media_type)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_titles(media_type, titles):
    """Remember which item each autocompleted title stands for. Keep earlier
    searches' titles too (newest last, capped), so a title still resolves
    after searching for something else and coming back."""
    saved = {t: r for t, r in read_titles(media_type).items() if t not in titles}
    saved.update(titles)
    saved = dict(list(saved.items())[-MAX_SAVED_TITLES:])
    tmp = titles_cache(media_type) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(saved, f)
    os.replace(tmp, titles_cache(media_type))


def details_cache(ref):
    return os.path.join(CACHEDIR, 'details-' + ref.replace(':', '-') + '.json')


def warm_details_window(ref, item):
    """Hand this item to the Alfred 6 details window's script in the
    background, so the window opens already filled in on ↩."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'details.alfredui', 'scripts', 'load.py')
    if not os.path.exists(script):
        return
    try:
        with open(details_cache(ref), 'w') as f:
            json.dump({"item": item, "rows": items}, f)
        subprocess.Popen([sys.executable, script, ref, '--warm'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        log(repr(e))


def get_tmdb_configuration(api_key):
    url = TMDB_API_URL + 'configuration'
    params = dict(api_key=api_key)
    return get_json(url, params)


def get_tmdb_info(item_type, item_id, api_key, language):
    url = TMDB_API_URL
    if item_type == 'm':
        url += 'movie/' + item_id
    elif item_type == 't':
        url += 'tv/' + item_id
    params = dict(
        api_key=api_key, language=language,
        append_to_response='videos,external_ids,watch/providers,releases,content_ratings,images',
        # Logos for the details window: the search language, then English,
        # then language-less ones.
        include_image_language=f'{language},en,null')

    return get_json(url, params)


def get_omdb_info(imdb_id):
    url = OMDB_API_URL
    params = dict(i=imdb_id, tomatoes=True, apikey=os.environ['omdb_api_key'])
    return get_json(url, params)


def show_item_info(item, media_type):

    title_key = 'title'
    release_date_key = 'release_date'
    if media_type == 'movie':
        imdb_id = item.get('imdb_id')
    elif media_type == 'tv':
        imdb_id = item['external_ids'].get('imdb_id')
        title_key = 'name'
        release_date_key = 'first_air_date'

    # Titles without an OMDb record (usually no IMDb id) still get a details
    # list from TMDb data alone: no ratings or credits, but the links work.
    omdb_info = {}
    if imdb_id:
        omdb_info = get_omdb_info(imdb_id)
        log('OMDb info retrieved.')
        if omdb_info.get('Response') == 'False':
            omdb_info = {}
    imdb_id = get_omdb_value(omdb_info, 'imdbID')

    title = item[title_key]
    if item[release_date_key]:
        title += ' (' + item[release_date_key][:4] + ')'

    items.append({"title": title,
                  "subtitle": get_subtitle(item, omdb_info, media_type),
                  "valid": True,
                  # ↩ opens the Alfred 6 details window; every other row's arg
                  # is a URL, which the window's script opens instead.
                  "arg": media_type[:1] + ':' + str(item['id']),
                  "quicklookurl": pathlib.Path(HTML_SUMMARY_FILE).as_uri(),
                  "text": {
                      "copy": str(item['id'])}
                  })

    search = urllib.parse.quote(item[title_key].encode(
        'utf-8'), safe=':'.encode('utf-8'))

    all_search_sites = []

    # IMDb, or TMDb when there is no IMDb record
    if not imdb_id:
        search_url = TMDB_URL + media_type + '/' + str(item['id'])
        all_search_sites.append(search_url)
        items.append({"title": 'TMDb',
                      "subtitle": f"View '{item[title_key]}' on TMDb",
                      "valid": True,
                      "arg": search_url,
                      "text": {
                          "copy": str(item['id'])}
                      })
    elif get_omdb_value(omdb_info, 'imdbRating'):
        search_url = IMDB_URL + 'title/' + imdb_id
        all_search_sites.append(search_url)
        items.append({"title": omdb_info['imdbRating'],
                      "subtitle": 'IMDb (' + omdb_info['imdbVotes'] + " votes)",
                      "icon": {"path": 'img/imdb.png'},
                      "valid": True,
                      "arg": search_url,
                      "text": {
                          "copy": imdb_id}
                      })
    else:
        search_url = IMDB_URL + 'title/' + imdb_id
        all_search_sites.append(search_url)
        items.append({"title": 'IMDb',
                      "subtitle": f"Search IMDb for '{item[title_key]}'",
                      "icon": {"path": 'img/imdb.png'},
                      "valid": True,
                      "arg": search_url,
                      "text": {
                          "copy": imdb_id}
                      })

    # TheTVDB
    tvdb_id = item['external_ids']['tvdb_id'] if media_type == 'tv' else None
    if tvdb_id:
        search_url = TVDB_SERIES_URL + str(tvdb_id)
        all_search_sites.append(search_url)
        items.append({"title": 'TheTVDB',
                      "subtitle": f"View '{item[title_key]}' on TheTVDB",
                      "icon": {"path": 'img/tvdb.png'},
                      "valid": True,
                      "arg": search_url,
                      "text": {
                          "copy": str(tvdb_id)}
                      })

    # Rotten Tomatoes
    search_url = ROTTEN_TOMATOES_SEARCH_URL + search
    if get_omdb_value(omdb_info, 'tomatoURL'):
        search_url = omdb_info['tomatoURL']
    all_search_sites.append(search_url)

    tomato_meter = get_omdb_value(omdb_info, 'tomatoMeter')
    if not tomato_meter:
        tomato_meter = get_rating(omdb_info, 'Rotten Tomatoes')
    if tomato_meter:
        items.append({"title": format_percentage(tomato_meter),
                      "subtitle": get_tomato_subtitle(omdb_info),
                      "icon": {"path": get_tomato_icon(omdb_info, tomato_meter)},
                      "valid": True,
                      "arg": search_url})
    else:
        items.append({"title": 'Rotten Tomatoes',
                      "subtitle": f"Search Rotten Tomatoes for '{item[title_key]}'",
                      "icon": {"path": 'img/fresh.png'},
                      "valid": True,
                      "arg": search_url})

    tomato_user_meter = get_omdb_value(omdb_info, 'tomatoUserMeter')
    if tomato_user_meter:
        tomatoUserIcon = 'img/rtliked.png'
        if get_score(tomato_user_meter) < 60:
            tomatoUserIcon = 'img/rtdisliked.png'

        items.append({"title": format_percentage(tomato_user_meter),
                      "subtitle": get_tomato_user_subtitle(omdb_info),
                      "icon": {"path": tomatoUserIcon},
                      "valid": True,
                      "arg": search_url})

    # Metacritic
    search_url = METACRITIC_SEARCH_URL + search
    if media_type == 'movie':
        search_url += '/?category=2'
    elif media_type == 'tv':
        search_url += '/?category=1'
    all_search_sites.append(search_url)
    if get_omdb_value(omdb_info, 'Metascore'):
        items.append({"title": omdb_info['Metascore'],
                      "subtitle": 'Metacritic',
                      "icon": {"path": 'img/meta.png'},
                      "valid": True,
                      "arg": search_url})
    else:
        items.append({"title": 'Metacritic',
                      "subtitle": f"Search Metacritic for '{item[title_key]}'",
                      "icon": {"path": 'img/meta.png'},
                      "valid": True,
                      "arg": search_url})

    # Letterboxd
    if INCLUDE_LETTERBOXD:
        search_url = LETTERBOXD_URL + str(item['id'])
        all_search_sites.append(search_url)
        items.append({"title": 'Letterboxd',
                      "subtitle": f"View '{item[title_key]}' on Letterboxd",
                      "icon": {"path": 'img/letterboxd.png'},
                      "valid": True,
                      "arg": search_url})

    # Simkl
    if INCLUDE_SIMKL:
        simkl_media_type = 'movies' if media_type == 'movie' else 'tv'
        search_url = f"{SIMKL_URL}/?type={simkl_media_type}&q={search}"
        all_search_sites.append(search_url)
        items.append({"title": 'Simkl',
                      "subtitle": f"View '{item[title_key]}' on Simkl",
                      "icon": {"path": 'img/simkl.png'},
                      "valid": True,
                      "arg": search_url})

    # MovieChat
    if INCLUDE_MOVIECHAT and imdb_id:
        search_url = MOVIECHAT_URL + imdb_id
        all_search_sites.append(search_url)
        items.append({"title": 'MovieChat',
                      "subtitle": f"View '{item[title_key]}' on MovieChat",
                      "icon": {"path": 'img/moviechat.png'},
                      "valid": True,
                      "arg": search_url})

    # JustWatch
    if INCLUDE_JUSTWATCH:
        locale = os.environ['locale']
        if locale in item['watch/providers']['results'].keys():
            watchproviders = item['watch/providers']['results'][locale]
            search_url = watchproviders['link']
            justwatchstring = ''
            if 'flatrate' in watchproviders.keys():
                justwatchstring += 'Stream: ' + \
                    watchproviders['flatrate'][0]['provider_name'] + ' | '
            if 'buy' in watchproviders.keys():
                justwatchstring += 'Buy: ' + \
                    watchproviders['buy'][0]['provider_name'] + ' | '
            if 'rent' in watchproviders.keys():
                justwatchstring += 'Rent: ' + \
                    watchproviders['rent'][0]['provider_name'] + ' | '
            # remove first and last pipe
            justwatchstring = justwatchstring[:-3]

            all_search_sites.append(search_url)
            items.append({"title": 'JustWatch',
                          "subtitle": justwatchstring,
                          "icon": {"path": 'img/justwatch.png'},
                          "valid": True,
                          "arg": search_url})

    if item['videos']['results']:
        trailer = None
        for video in item['videos']['results']:
            if video['type'] == 'Trailer':
                trailer = video
                break
        if trailer:
            items.append({"title": 'Watch Trailer',
                          "subtitle": trailer['site'] + ' \u2022 ' + str(trailer['size']) + 'p',
                          "valid": True,
                          "arg": YOUTUBE_WATCH_URL + trailer['key'],
                          "icon": {"path": 'img/youtube.png'}})

            all_search_sites.append(YOUTUBE_WATCH_URL + trailer['key'])

    items.append({"title": 'Search',
                  "subtitle": f"Search for '{item[title_key]}' on all rating sites.",
                  "icon": {"path": 'img/ratingsites.png'},
                  "valid": True,
                  "arg": '||'.join(all_search_sites)})

    for key, icon in (('Director', ICON_USER), ('Writer', ICON_USER),
                      ('Actors', ICON_GROUP)):
        people = get_omdb_value(omdb_info, key)
        if people:
            items.append({"title": people,
                          "subtitle": key,
                          "icon": {"path": icon},
                          "text": {"copy": people}})

    generate_item_html(omdb_info, item, media_type)

    return


def get_omdb_value(omdb_info, key):
    """Return the value for key, or None when OMDb omits it or reports 'N/A'."""
    value = omdb_info.get(key)
    if not value or value == 'N/A':
        return None
    return value


def get_rating(omdb_info, source):
    """Return the score reported by source in OMDb's Ratings list, if present."""
    for rating in omdb_info.get('Ratings', []):
        if rating['Source'] == source:
            return rating['Value']
    return None


def get_score(value):
    """Parse a score like '89', '89%' or '8.9/10' into a number out of 100."""
    match = re.match(r'\s*([0-9]+(?:\.[0-9]+)?)\s*(%|/\s*([0-9]+(?:\.[0-9]+)?))?', value)
    if not match:
        return 0
    score = float(match.group(1))
    if match.group(3):
        score = score / float(match.group(3)) * 100
    return score


def format_percentage(value):
    return value if '%' in value or '/' in value else value + '%'


def get_tomato_icon(omdb_info, tomato_meter):
    tomato_image = get_omdb_value(omdb_info, 'tomatoImage')
    if tomato_image:
        return 'img/' + tomato_image + '.png'
    return 'img/fresh.png' if get_score(tomato_meter) >= 60 else 'img/rotten.png'


def get_tomato_subtitle(omdb_info):
    details = []
    reviews = get_omdb_value(omdb_info, 'tomatoReviews')
    fresh = get_omdb_value(omdb_info, 'tomatoFresh')
    rotten = get_omdb_value(omdb_info, 'tomatoRotten')
    if reviews:
        details.append(reviews + ' reviews')
    if fresh:
        details.append(fresh + ' fresh')
    if rotten:
        details.append(rotten + ' rotten')
    if details:
        return 'Rotten Tomatoes (' + ', '.join(details) + ')'
    return 'Rotten Tomatoes'


def get_tomato_user_subtitle(omdb_info):
    details = []
    reviews = get_omdb_value(omdb_info, 'tomatoUserReviews')
    rating = get_omdb_value(omdb_info, 'tomatoUserRating')
    if reviews:
        details.append(reviews + ' reviews')
    if rating:
        details.append(rating + ' avg rating')
    if details:
        return 'Rotten Tomatoes Audience Score (' + ', '.join(details) + ')'
    return 'Rotten Tomatoes Audience Score'


def generate_item_html(omdb_info, tmdb_info, media_type):
    item_template = Template(
        filename='templates/media.html', output_encoding='utf-8')

    production_company = 'N/A'
    if tmdb_info['production_companies']:
        production_company = tmdb_info['production_companies'][0]['name']

    html = item_template.render(
        title=get_omdb_value(omdb_info, 'Title') or tmdb_info.get('title') or tmdb_info.get('name'),
        backdrop_path=tmdb_info['backdrop_path'],
        poster=tmdb_info['poster_path'],
        production_company=production_company,
        overview=tmdb_info['overview'],
        release_date=get_omdb_value(omdb_info, 'Released') or tmdb_info.get(
            'release_date') or tmdb_info.get('first_air_date') or 'N/A',
        director=omdb_info.get('Director', 'N/A'),
        actors=omdb_info.get('Actors', 'N/A'),
        genre=get_subtitle(tmdb_info, omdb_info, media_type)
    )
    with open(HTML_SUMMARY_FILE, "wb") as text_file:
        text_file.write(html)

    return


def get_subtitle(tmdb_info, omdb_info, media_type):
    locale = os.environ['locale']
    certification = get_omdb_value(omdb_info, 'Rated') or 'N/A'
    if media_type == 'movie' and 'releases' in tmdb_info.keys():
        for release in tmdb_info['releases']['countries']:
            if release['iso_3166_1'] == locale:
                certification = release['certification']
                break
    elif media_type == 'tv' and 'content_ratings' in tmdb_info.keys():
        for release in tmdb_info['content_ratings']['results']:
            if release['iso_3166_1'] == locale:
                certification = release['rating']
                break
    runtime = get_omdb_value(omdb_info, 'Runtime')
    if not runtime:
        minutes = tmdb_info.get('runtime') or next(
            iter(tmdb_info.get('episode_run_time') or []), None)
        runtime = f'{minutes} min' if minutes else None
    genre = get_omdb_value(omdb_info, 'Genre') or ', '.join(
        g['name'] for g in tmdb_info.get('genres') or [])
    subtitleItems = []
    if runtime:
        subtitleItems.append(format_runtime(runtime))
    if genre:
        subtitleItems.append(genre)
    if certification and certification != 'N/A':
        if "rated" not in certification.lower():
            certification = 'Rated ' + certification
        subtitleItems.append(certification)
    return ' \u2022 '.join(subtitleItems)


def format_runtime(runtime):
    match = re.match(r'^(\d+)\s*min$', runtime.strip())
    if not match:
        return runtime

    minutes = int(match.group(1))
    if minutes < 60:
        return '{}m'.format(minutes)

    hours, minutes = divmod(minutes, 60)
    if minutes == 0:
        return '{}h'.format(hours)
    return '{}h {}m'.format(hours, minutes)


def extract_popularity(result):
    if not result['popularity']:
        return 0
    return result['popularity']


def output_items():
    output = {"items": items}
    output_str = json.dumps(output)
    sys.stdout.write(output_str)
    sys.stdout.flush()


def log(s, *args):
    if args:
        s = s % args
    print(s, file=sys.stderr)


def get_json(url, params):
    qstr = urllib.parse.urlencode(params)
    url = f"{url}?{qstr}"
    log(url)
    with urllib.request.urlopen(url) as url_return:
        data = json.load(url_return)
        return data


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description='Get some info on media. This is called from an AlfredApp workflow.')
    parser.add_argument("-t", type=str, default="movie",
                        help="Media type ('movie' or 'tv')")
    parser.add_argument("-q", type=str, default=None, help="Query")

    args = parser.parse_args()
    sys.exit(main(args.t, args.q))
