#!/bin/zsh
# Runs on the poster webkitview's "changed" event: $1 is the page's URL, $2
# the poster image. The page's Copy button sets #copy-<time> on its URL;
# any other change (the page loading) is ignored.
[[ $1 == *'#copy-'* && -f $2 ]] || exit 0
osascript -l JavaScript - "$2" <<'JXA' >/dev/null
function run(argv) {
  ObjC.import('AppKit');
  const image = $.NSImage.alloc.initWithContentsOfFile(argv[0]);
  const board = $.NSPasteboard.generalPasteboard;
  board.clearContents;
  board.writeObjects($([image]));
}
JXA
