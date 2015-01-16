# encoding: utf-8
from __future__ import unicode_literals

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
METACRITIC_SEARCH_URL = 'http://metacritic.com/search/movie/'
ROTTEN_TOMATOES_SEARCH_URL = 'http://rottentomatoes.com/search/?search='

log = None

def main(wf):

    if len(wf.args):
        query = wf.args[0]
    else:
        query = None

    api_key = DEFAULT_TMDB_API_KEY

    m = re.match('([m])\:([0-9]*)', query)
    if query[:2] == 'm:' and m.group(2):
    	try:
        	item = get_tmdb_info(m.group(1), m.group(2), api_key)
        	if m.group(1) == 'm':
	            show_movie_info(item)
    	except AttributeError, e:
    		wf.add_item('The movie was not found.')
    else:
        def wrapper():
            return get_tmdb_configuration(api_key)

        configuration = wf.cached_data('tmdbconfig', wrapper, max_age=604800)
        url = TMDB_API_URL + 'search/movie'
        params = dict(api_key = api_key, query = query, search_type='ngram')
        results = web.get(url, params).json()

        if 'status_code' in results:
            wf.add_item(title = 'No movies were found.')
        elif 'results' in results:
    	    if not results['results']:
        	    wf.add_item(title = 'No movies were found.')
            results['results'].sort(key=extract_popularity, reverse = True)
            for item in results['results']:
                mediaType = 'movie'
                if 'media_type' in item:
                    mediaType = item['media_type']
                if mediaType == 'movie':
                    title = item['title']
                    if item['release_date']:
                        title += ' (' + item['release_date'][:4] + ')'
                    movie = wf.add_item(title = title,
                                    arg = str(item['id']),
                                    valid = False,
                                    autocomplete = 'm:' + str(item['id'])
                                    )
    wf.send_feedback()

def get_tmdb_configuration(api_key):
    url = TMDB_API_URL + 'configuration'
    params = dict(api_key = api_key)
    return web.get(url, params).json()

def get_tmdb_info(item_type, item_id, api_key):
    url = TMDB_API_URL
    if item_type == 'm':
        url += 'movie/' + item_id
    params = dict(api_key = api_key, language = 'en', append_to_response = 'videos')
    return web.get(url, params).json()

def get_omdb_info(imdb_id):
    url = OMDB_API_URL
    params = dict(i = imdb_id, tomatoes = True)
    return web.get(url, params).json()

def show_movie_info(movie):
    omdb_info = get_omdb_info(movie['imdb_id'])

    if omdb_info['Response'] == 'False':
        wf.add_item(title='Movie details not found.')
        return

    #get poster
    urllib.urlretrieve("https://image.tmdb.org/t/p/w92" + movie['poster_path'], "poster.jpg")

    wf.add_item(title = '%s (%s)' % (movie['title'], movie['release_date'][:4]),
                subtitle = get_subtitle(omdb_info),
                valid = True,
                icon = "poster.jpg",
                arg = "file://" + wf.workflowdir + '/movie.html')

    #log.debug(wf.workflowdir)

    if omdb_info['imdbRating'] != 'N/A':
        wf.add_item(title = omdb_info['imdbRating'],
                    subtitle = 'IMDb (' + omdb_info['imdbVotes'] + " votes)",
                    icon = 'img/imdb.png',
                    valid = True,
                    arg = IMDB_URL + 'title/' + omdb_info['imdbID'])

    if omdb_info['tomatoMeter'] != 'N/A':
        tomatoIcon = 'img/fresh.png'
        if omdb_info['tomatoImage'] == 'N/A':
            tomatoIcon = 'img/noidea.png'
        else:
            tomatoIcon = 'img/' + omdb_info['tomatoImage'] + '.png'

        wf.add_item(title = omdb_info['tomatoMeter'] + '%',
                    subtitle = 'Rotten Tomatoes (' + omdb_info['tomatoReviews'] + ' reviews, ' + omdb_info['tomatoFresh'] + ' fresh, ' + omdb_info['tomatoRotten'] + ' rotten)',
                    icon = tomatoIcon,
                    valid = True,
                    arg = ROTTEN_TOMATOES_SEARCH_URL + '%s (%s)' % (movie['title'], movie['release_date'][:4]))

    if omdb_info['Metascore'] != 'N/A':
        wf.add_item(title = omdb_info['Metascore'],
                    subtitle = 'Metacritic',
                    icon = 'img/meta.png',
                    valid = True,
                    arg = METACRITIC_SEARCH_URL + movie['title'] + '/results')

    if movie['videos']['results']:
        trailer = None
        for video in movie['videos']['results']:
            if video['type'] == 'Trailer':
                trailer = video
                break
        if trailer:
            wf.add_item(title = 'Watch Trailer',
                        subtitle = trailer['site'] + ' \u2022 ' + str(trailer['size']) + 'p',
                        valid = True,
                        arg = YOUTUBE_WATCH_URL + trailer['key'],
                        icon = 'img/youtube.png')

    wf.add_item(title=omdb_info['Director'],
                      subtitle='Director',
                      icon = ICON_USER)

    wf.add_item(title=omdb_info['Writer'],
                      subtitle='Writer',
                      icon = ICON_USER)

    wf.add_item(title=omdb_info['Actors'],
                      subtitle='Actors',
                      icon = ICON_GROUP)

    generate_movie_html(omdb_info, movie)

    return

def generate_movie_html(omdb_info, tmdb_info):
    movie_template = Template(filename='templates/movie.html', output_encoding = 'utf-8')

    production_company = 'N/A'
    if tmdb_info['production_companies']:
        production_company = tmdb_info['production_companies'][0]['name']

    html = movie_template.render(
            title = omdb_info['Title'],
            backdrop_path = tmdb_info['backdrop_path'],
            poster = tmdb_info['poster_path'],
            production_company = production_company,
            overview = tmdb_info['overview'],
            release_date = omdb_info['Released'],
            director = omdb_info['Director'],
            actors = omdb_info['Actors'],
            genre = get_subtitle(omdb_info)
        )
    with open("movie.html", "w+") as text_file:
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
