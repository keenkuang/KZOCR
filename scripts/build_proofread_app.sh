#!/usr/bin/env bash
# KZOCR 交付式校对台 · PyInstaller 桌面打包脚本
#
# 用法：
#   bash scripts/build_proofread_app.sh              # 默认打包为 onedir
#   bash scripts/build_proofread_app.sh --onefile     # 打包为单文件
#   bash scripts/build_proofread_app.sh --clean       # 清除构建产物
#
# 产物：dist/KZOCR-校对台/ （或 dist/KZOCR-校对台.exe）
# 最小依赖：仅含 FastAPI + kzocr + PyMuPDF + numpy
# 排除：torch / paddleocr / paddle / glm / tcm_ocr（Postgres）

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

MODE="${1:-onedir}"
DIST_DIR="$REPO_DIR/dist"

echo "=== KZOCR 校对台 桌面打包 ==="
echo "模式：$MODE"
echo "源码：$REPO_DIR"
echo "输出：$DIST_DIR"

if [ "$MODE" = "--clean" ]; then
    echo "清除构建产物..."
    rm -rf build dist *.spec
    echo "完成"
    exit 0
fi

# 确定 PyInstaller 参数
OPTS=(
    --name "KZOCR-校对台"
    --add-data "kzocr/proofread/templates:kzocr/proofread/templates"
    --add-data "kzocr/proofread:templates"
    --hidden-import "uvicorn"
    --hidden-import "uvicorn.logging"
    --hidden-import "uvicorn.loops.auto"
    --hidden-import "uvicorn.protocols.http.auto"
    --hidden-import "uvicorn.protocols.websockets.auto"
    --hidden-import "fastapi"
    --hidden-import "starlette"
    --hidden-import "starlette.templating"
    --hidden-import "starlette.staticfiles"
    --hidden-import "jinja2"
    --hidden-import "jinja2.ext"
    --hidden-import "multipart"
    --hidden-import "kzocr"
    --hidden-import "kzocr.doc"
    --hidden-import "kzocr.proofread"
    --hidden-import "kzocr.proofread.api"
    --hidden-import "kzocr.proofread.app"
    # 排除重型引擎依赖
    --exclude-module "torch"
    --exclude-module "paddleocr"
    --exclude-module "paddle"
    --exclude-module "paddlepaddle"
    --exclude-module "glm"
    --exclude-module "tcm_ocr"
    --exclude-module "tcm_ocr.database"
    --exclude-module "psycopg2"
    --exclude-module "kzocr.tcm_ocr"
    --exclude-module "PIL.ImageQt"        # Qt 不需要
    --exclude-module "PyQt5"
    --exclude-module "PyQt6"
    --exclude-module "matplotlib"
    --exclude-module "notebook"
    --exclude-module "ipykernel"
    --exclude-module "jupyter"
    --clean
    --noconfirm
)

if [ "$MODE" = "--onefile" ]; then
    OPTS+=(--onefile)
else
    OPTS+=(--onedir)
fi

echo ""
echo "运行 PyInstaller..."
echo ""

pyinstaller "${OPTS[@]}" scripts/proofread_entry.py

echo ""
echo "=== 打包完成 ==="
echo "产物：$DIST_DIR/KZOCR-校对台"
echo ""
echo "使用说明："
echo "  1. 将 custom.db 放在产物目录"
echo "  2. 双击 KZOCR-校对台（或 KZOCR-校对台.exe）"
echo "  3. 浏览器自动打开 http://127.0.0.1:9090"
echo ""
echo "高级用法："
echo "  ./KZOCR-校对台 --db /path/to/custom.db    # 指定校对包路径"
echo "  ./KZOCR-校对台 --port 8080                 # 更改端口"
