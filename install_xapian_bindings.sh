#!/bin/bash
#
# install_xapian_bindings.sh - Install Xapian Python bindings into
# current virtualenv.

set -e

if [[ -z "$VIRTUAL_ENV" ]]; then
    echo >&2 "error: you're not inside a virtual env, aborting"
    exit 1
fi

# Install prerequisites.
# https://github.com/sphinx-doc/sphinx/issues/6524
pip install Sphinx

VERSION=$(xapian-config --version | sed -r 's/.*core (([0-9]+[.]?)+)/\1/')
URL="https://oligarchy.co.uk/xapian/$VERSION/xapian-bindings-$VERSION.tar.xz"

WORKDIR=$(mktemp -d)

(
    set -e
    cd $WORKDIR
    wget $URL
    tar -xvaf *.tar.xz
    cd "xapian-bindings-$VERSION"
    ./configure --with-python3 --prefix="$VIRTUAL_ENV"
    make install
)
