import sys
import urllib
import re
import argparse
from workflow import Workflow, ICON_WEB, ICON_USER, ICON_WARNING, ICON_GROUP, web, PasswordNotFound
from mako.template import Template

DEFAULT_TMDB_API_KEY = '0ebad901a16d3bf7f947b0a8d1808c44'
TMDB_API_URL = 'http://api.themoviedb.org/3/'
OMDB_API_URL = 'http://www.omdbapi.com/'
IMDB_URL = 'http://imdb.com/'
YOUTUBE_WATCH_URL = 'http://youtube.com/watch?v='
METACRITIC_SEARCH_URL = 'http://www.metacritic.com/search/movie/'
ROTTEN_TOMATOES_SEARCH_URL = 'http://www.rottentomatoes.com/search/?search='

log = None

def main(wf):

    if len(wf.args):
        query = wf.args[0]
    else:
        query = None


    # build argument parser to parse script args and collect their
    # values
    parser = argparse.ArgumentParser()
    # add an optional (nargs='?') --apikey argument and save its
    # value to 'apikey' (dest). This will be called from a separate "Run Script"
    # action with the API key
    parser.add_argument('--setkey', dest='apikey', nargs='?', default=None)
    # add an optional query and save it to 'query'
    parser.add_argument('query', nargs='?', default=None)
    # parse the script's arguments
    args = parser.parse_args(wf.args)

    ####################################################################
    # Save the provided API key
    ####################################################################

    # decide what to do based on arguments
    if args.apikey:  # Script was passed an API key
        # save the key
        wf.save_password('tmdb_api_key', args.apikey)
        return 0  # 0 means script exited cleanly

    ####################################################################
    # Check that we have an API key saved
    ####################################################################

    try:
        api_key = wf.get_password('tmdb_api_key')
    except PasswordNotFound:  # API key has not yet been set
        api_key = DEFAULT_TMDB_API_KEY
        #wf.add_item('No TMDb API key set.',
        #            'Please use \'movieapi\' to set your TMDb API key.',
        #            valid=False,
        #            icon=ICON_WARNING)
        #wf.send_feedback()
        #return 0


    m = re.match('([mp])\:([0-9]*)', query)
    if query[:2] == 'm:' and m.group(2):
        item = get_tmdb_info(m.group(1), m.group(2), api_key)
        if m.group(1) == 'm':
            show_movie_info(item)
        elif m.group(1) == 'p':
            show_person_info(item)
    else:
        def wrapper():
            return get_tmdb_configuration(api_key)

        configuration = wf.cached_data('tmdbconfig', wrapper, max_age=604800)
        url = TMDB_API_URL + 'search/movie'
        params = dict(api_key = api_key, query = urllib.quote(query), search_type='ngram')
        results = web.get(url, params).json()

        if 'status_code' in results:
            wf.add_item(title = 'No movie was found.')
        elif 'results' in results:
    	    if not results['results']:
        	    wf.add_item(title = 'No movie was found.')
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
                    #if item['poster_path']:
                    #    movie.icon = configuration['images']['base_url'] + configuration['images']['poster_sizes'][0] + item['poster_path']
                elif mediaType == 'person':
                    person = wf.add_item(title = '%s' % (item['name']),
                                     arg = str(item['id']),
                                     valid = False,
                                     autocomplete = 'p:' + str(item['id'])
                                     )
                    #if item['profile_path']:
                    #    person.icon = configuration['images']['base_url'] + configuration['images']['poster_sizes'][0] + item['profile_path']
                    #else:
                    person.icon = ICON_USER

    wf.send_feedback()

def get_tmdb_configuration(api_key):
    url = TMDB_API_URL + 'configuration'
    params = dict(api_key = api_key)
    return web.get(url, params).json()

def get_tmdb_info(item_type, item_id, api_key):
    url = TMDB_API_URL
    if item_type == 'm':
        url += 'movie/' + item_id
    elif item_type == 'p':
        url += 'person/' + item_id
    params = dict(api_key = api_key, language = 'en', append_to_response = 'videos')
    return web.get(url, params).json()

def get_omdb_info(imdb_id):
    url = OMDB_API_URL
    params = dict(i = imdb_id, tomatoes = True)
    return web.get(url, params).json()

def show_movie_info(movie):
    omdb_info = get_omdb_info(movie['imdb_id'])

    subtitleItems = []
    if omdb_info['Runtime'] != 'N/A':
        subtitleItems.append(omdb_info['Runtime'])
    if omdb_info['Genre'] != 'N/A':
        subtitleItems.append(omdb_info['Genre'])
    if omdb_info['Rated'] != 'N/A':
        subtitleItems.append('Rated ' + omdb_info['Rated'])

    wf.add_item(title = '%s (%s)' % (movie['title'], movie['release_date'][:4]),
                subtitle = u' \u2022 '.join(subtitleItems),
                valid = True,
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
                        subtitle = trailer['site'] + u' \u2022 ' + str(trailer['size']) + 'p',
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
    html = movie_template.render(
            title = omdb_info['Title'],
            backdrop_path = tmdb_info['backdrop_path'],
            poster = tmdb_info['poster_path'],
            production_company = tmdb_info['production_companies'][0]['name'],
            genre = omdb_info['Genre'],
            overview = tmdb_info['overview'],
            release_date = omdb_info['Released'],
            director = omdb_info['Director'],
            actors = omdb_info['Actors']
        )
    with open("movie.html", "w") as text_file:
        text_file.write(html)

    return

def show_person_info(person):
    return

def extract_popularity(result):
    try:
        return result['popularity']
    except KeyError:
        return '0'

if __name__ == u"__main__":
    wf = Workflow()
    log = wf.logger
    sys.exit(wf.run(main))
