#!/bin/sh

if [ ! -s /flag.txt ]; then
    echo 'missing or empty /flag.txt' >&2
    exit 1
fi

exec "$@"
