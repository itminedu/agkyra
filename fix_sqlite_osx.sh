#!/usr/bin/env bash
# Copyright (C) 2015 GRNET S.A.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

cd "$(dirname "$0")"
if [ -z "$1" ]
then
    echo "Usage: $0 <path_to_libsqlite3.dylib>"
    exit
fi

DIST=dist
LIBDIR=$DIST/agkyra/lib
cp $(python -c "import _sqlite3; print _sqlite3.__file__") $LIBDIR
cp $1 $LIBDIR

cd $LIBDIR
install_name_tool -change '/usr/lib/libsqlite3.dylib' '@loader_path/libsqlite3.dylib' _sqlite3.so
