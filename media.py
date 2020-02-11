# encoding: utf-8
from __future__ import unicode_literals

import os
import sys
import urllib
import re
from workflow import Workflow, ICON_WEB, ICON_USER, ICON_WARNING, ICON_GROUP, web
from mako.template import Template

DEFAULT_TMDB_API_KEY = '0ebad901a16d3bf7f947b0a8d1808c44'
TMDB_API_URL = 'http://api.themoviedb.org/3/'
OMDB_API_URL = 'http://www.omdbapi.com/'
IMDB_URL = 'http://imdb.com/'
YOUTUBE_WATCH_URL = 'http://youtube.com/watch?v='
METACRITIC_SEARCH_URL = 'http://metacritic.com/search/'
ROTTEN_TOMATOES_SEARCH_URL = 'http://rottentomatoes.com/search/?search='

log = None


def main(wf):

    if len(wf.args):
        media_type = wf.args[0]
        query = wf.args[1]
    else:
        media_type = "movie"
        query = None

    global METACRITIC_SEARCH_URL
    METACRITIC_SEARCH_URL += media_type + '/'

    api_key = DEFAULT_TMDB_API_KEY

    m = re.match('([m|t])\:([0-9]*)', query)
    if query[:2] == 'm:' or query[:2] == 't:' and m.group(2):
        try:
            item = get_tmdb_info(m.group(1), m.group(2), api_key)
            log.debug('TMDb info retrieved.')
            if m.group(1) == 'm' or m.group(1) == 't':
                show_item_info(item, media_type)
        except AttributeError, e:
            wf.add_item('The item was not found.')
    else:
        def wrapper():
            return get_tmdb_configuration(api_key)

        try:
            configuration = wf.cached_data('tmdbconfig', wrapper, max_age=604800)
            url = TMDB_API_URL + 'search/' + media_type
            params = dict(api_key=api_key, query=query, search_type='ngram')
            results = web.get(url, params).json()
        except Exception, e:
            wf.add_item('Uh oh... something went wrong',
                subtitle='Please check your internet connection.')
            wf.send_feedback()
            return 0

        if 'status_code' in results:
            wf.add_item(title='Nothing was found.')
        elif 'results' in results:
            if not results['results']:
                wf.add_item(title='Nothing was found.')
            results['results'].sort(key=extract_popularity, reverse=True)
            for item in results['results']:
                if media_type == 'movie':
                    title = item['title']
                    if item.get('release_date', 0):
                        title += ' (' + item['release_date'][:4] + ')'
                else:
                    title = item['name']
                    if item.get('first_air_date', 0):
                        title += ' (' + item['first_air_date'][:4] + ')'
                item = wf.add_item(title=title,
                                   arg=str(item['id']),
                                   valid=False,
                                   autocomplete=media_type[
                                       :1] + ':' + str(item['id'])
                                   )
    wf.send_feedback()


def get_tmdb_configuration(api_key):
    url = TMDB_API_URL + 'configuration'
    params = dict(api_key=api_key)
    return web.get(url, params).json()


def get_tmdb_info(item_type, item_id, api_key):
    url = TMDB_API_URL
    if item_type == 'm':
        url += 'movie/' + item_id
    elif item_type == 't':
        url += 'tv/' + item_id
    params = dict(
        api_key=api_key, language='en', append_to_response='videos,external_ids')

    return web.get(url, params).json()


def get_omdb_info(imdb_id):
    url = OMDB_API_URL
    params = dict(i=imdb_id, tomatoes=True, apikey=os.environ['omdb_api_key'])
    return web.get(url, params).json()


def show_item_info(item, media_type):
    title_key = 'title'
    release_date_key = 'release_date'
    if media_type == 'movie':
        imdb_id = item['imdb_id']
    elif media_type == 'tv':
        imdb_id = item['external_ids']['imdb_id']
        title_key = 'name'
        release_date_key = 'first_air_date'
    omdb_info = get_omdb_info(imdb_id)
    log.debug('OMDb info retrieved.')

    if omdb_info['Response'] == 'False':
        wf.add_item(title='Details not found.')
        return

    # get poster
    # urllib.urlretrieve("https://image.tmdb.org/t/p/w92" +
    # movie['poster_path'], "poster.jpg")

    title = item[title_key]
    if item[release_date_key]:
        title += ' (' + item[release_date_key][:4] + ')'

    wf.add_item(title=title,
                subtitle=get_subtitle(omdb_info),
                valid=True,
                # icon = "poster.jpg",
                arg="file://" + urllib.pathname2url(wf.cachefile('item.html')))

    search = urllib.quote(item[title_key].encode('utf-8'), safe=':'.encode('utf-8'))

    all_search_sites = []

    #IMDb
    search_url = IMDB_URL + 'title/' + omdb_info['imdbID']
    all_search_sites.append(search_url)
    if omdb_info['imdbRating'] != 'N/A':
        wf.add_item(title=omdb_info['imdbRating'],
                    subtitle='IMDb (' + omdb_info['imdbVotes'] + " votes)",
                    icon='img/imdb.png',
                    valid=True,
                    arg=search_url,
                    copytext=omdb_info['imdbID'])
    else:
        wf.add_item(title='IMDb',
                    subtitle='Search IMDb for \'' + item[title_key] + '\'',
                    icon='img/imdb.png',
                    valid=True,
                    arg=search_url)

    #Rotten Tomatoes
    search_url = ROTTEN_TOMATOES_SEARCH_URL + search
    all_search_sites.append(search_url)
    if omdb_info['tomatoMeter'] != 'N/A':
        tomatoIcon = 'img/fresh.png'
        if omdb_info['tomatoImage'] == 'N/A':
            tomatoIcon = 'img/noidea.png'
        else:
            tomatoIcon = 'img/' + omdb_info['tomatoImage'] + '.png'

        wf.add_item(title=omdb_info['tomatoMeter'] + '%',
                    subtitle='Rotten Tomatoes (' + omdb_info['tomatoReviews'] + ' reviews, ' + omdb_info['tomatoFresh'] + ' fresh, ' + omdb_info['tomatoRotten'] + ' rotten)',
                    icon=tomatoIcon,
                    valid=True,
                    arg=search_url)
    else:
        for rating in omdb_info['Ratings']:
            if rating['Source'] == 'Rotten Tomatoes':
                wf.add_item(title=rating['Value'],
                            subtitle='Rotten Tomatoes',
                            icon='img/fresh.png',
                            valid=True,
                            arg=search_url)

    if omdb_info['tomatoUserMeter'] != 'N/A':
        tomatoUserIcon = 'img/rtliked.png'
        if int(omdb_info['tomatoUserMeter']) < 60:
            tomatoUserIcon = 'img/rtdisliked.png'

        wf.add_item(title=omdb_info['tomatoUserMeter'] + '%',
                    subtitle='Rotten Tomatoes Audience Score (' + omdb_info['tomatoUserReviews'] + ' reviews, ' + omdb_info['tomatoUserRating'] + ' avg rating)',
                    icon=tomatoUserIcon,
                    valid=True,
                    arg=search_url)

    #Metacritic
    search_url = METACRITIC_SEARCH_URL + search + '/results'
    all_search_sites.append(search_url)
    if omdb_info['Metascore'] != 'N/A':
        wf.add_item(title=omdb_info['Metascore'],
                    subtitle='Metacritic',
                    icon='img/meta.png',
                    valid=True,
                    arg=search_url)
    else:
        wf.add_item(title='Metacritic',
                    subtitle='Search Metacritic for \'' + item[title_key] + '\'',
                    icon='img/meta.png',
                    valid=True,
                    arg=search_url)

    if item['videos']['results']:
        trailer = None
        for video in item['videos']['results']:
            if video['type'] == 'Trailer':
                trailer = video
                break
        if trailer:
            wf.add_item(title='Watch Trailer',
                        subtitle=trailer[
                            'site'] + ' \u2022 ' + str(trailer['size']) + 'p',
                        valid=True,
                        arg=YOUTUBE_WATCH_URL + trailer['key'],
                        icon='img/youtube.png')

            all_search_sites.append(YOUTUBE_WATCH_URL + trailer['key'])

    wf.add_item(title='Search',
                subtitle='Search for \'' + item[title_key] + '\' on all rating sites.',
                icon='img/ratingsites.png',
                valid=True,
                arg='||'.join(all_search_sites))

    wf.add_item(title=omdb_info['Director'],
                subtitle='Director',
                icon=ICON_USER)

    wf.add_item(title=omdb_info['Writer'],
                subtitle='Writer',
                icon=ICON_USER)

    wf.add_item(title=omdb_info['Actors'],
                subtitle='Actors',
                icon=ICON_GROUP)

    generate_item_html(omdb_info, item)

    return


def generate_item_html(omdb_info, tmdb_info):
    item_template = Template(
        filename='templates/media.html', output_encoding='utf-8')

    production_company = 'N/A'
    if tmdb_info['production_companies']:
        production_company = tmdb_info['production_companies'][0]['name']

    html = item_template.render(
        title=omdb_info['Title'],
        backdrop_path=tmdb_info['backdrop_path'],
        poster=tmdb_info['poster_path'],
        production_company=production_company,
        overview=tmdb_info['overview'],
        release_date=omdb_info['Released'],
        director=omdb_info['Director'],
        actors=omdb_info['Actors'],
        genre=get_subtitle(omdb_info)
    )
    with open(wf.cachefile("item.html"), "w+") as text_file:
        text_file.write(html)

    return


def get_subtitle(omdb_info):
    subtitleItems = []
    if omdb_info['Runtime'] != 'N/A':
        subtitleItems.append(omdb_info['Runtime'])
    if omdb_info['Genre'] != 'N/A':
        subtitleItems.append(omdb_info['Genre'])
    if omdb_info['Rated'] != 'N/A':
        rating_string = omdb_info['Rated']
        if "rated" not in rating_string.lower():
            rating_string = 'Rated ' + rating_string
        subtitleItems.append(rating_string)
    return ' \u2022 '.join(subtitleItems)


def extract_popularity(result):
    result.get('popularity', '0')

if __name__ == "__main__":
    wf = Workflow()
    log = wf.logger
    sys.exit(wf.run(main))
