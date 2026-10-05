#!/bin/sh
cd "$(dirname "$0")" || exit 1
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || { echo "Python 3.10+ required."; exit 1; }
exec python3 -m creativity_lab serve --open
