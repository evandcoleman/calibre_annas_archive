#!/usr/bin/env bash
set -euo pipefail
version=$(python3 -c "import re,sys; m=re.search(r'version\s*=\s*\((\d+),\s*(\d+),\s*(\d+)\)', open('__init__.py').read()); print('.'.join(m.groups()))")
out="calibre_annas_archive-v${version}.zip"
rm -f "$out"
zip "$out" README.md plugin-import-name-store_annas_archive.txt __init__.py annas_archive.py config.py constants.py
echo "built $out"
