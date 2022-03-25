import webbrowser
import sys
import argparse


def main(double_pipe_delimited_urls):
    urls = double_pipe_delimited_urls.split("||")
    for url in urls:
        webbrowser.open_new_tab(url)


def log(s, *args):
    if args:
        s = s % args
    print(s, file=sys.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Get some info on media. This is called from an AlfredApp workflow.')
    parser.add_argument("-u", type=str, help="Double-pipe-delimited urls")

    args = parser.parse_args()
    main(args.u)
