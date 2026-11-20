#!/bin/zsh
# Runs on the details page's "changed" event with the page's URL in $1. The
# page asks for things through its URL hash (see page.html); a change
# without one (a page loading) is ignored.
setopt extendedglob
[[ $1 == *'#'* ]] || exit 0
hash=${${1#*\#}%%\&t=*}
cache=~/Library/Caches/com.runningwithcrayons.Alfred/Workflow\ Data/com.mcknight.movies

case $hash in
  h=[0-9]##)
    # Fit the window's height to the page.
    print -r -- "{\"window\":{\"resize\":{\"height\":${hash#h=}}}}"
    ;;
  copy=[A-Za-z0-9._-]##)
    # Copy the poster as an image (the copy action only does text, files
    # and URLs).
    file=$cache/${hash#copy=}
    [[ -f $file ]] || exit 0
    osascript -l JavaScript - "$file" <<'JXA' >/dev/null
function run(argv) {
  ObjC.import('AppKit');
  const image = $.NSImage.alloc.initWithContentsOfFile(argv[0]);
  const board = $.NSPasteboard.generalPasteboard;
  board.clearContents;
  board.writeObjects($([image]));
}
JXA
    ;;
  trailer=[A-Za-z0-9_-]##)
    exec "${0:A:h}/load.py" "${hash#trailer=}" --open-trailer
    ;;
esac
exit 0
