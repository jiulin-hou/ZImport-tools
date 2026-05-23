#!/usr/bin/env bash
# 打包 ZImport-tools Zimlet 成可用 zmzimletctl deploy 的 zip。
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"
ZIP=com_msauto_zimport_tools.zip
rm -f "$ZIP"
zip -q "$ZIP" \
    com_msauto_zimport_tools.xml \
    com_msauto_zimport_tools.js \
    com_msauto_zimport_tools.css
echo "[zimlet] built $SCRIPT_DIR/$ZIP"
