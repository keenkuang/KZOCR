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
    --add-data "kzocr/proofread/static:kzocr/proofread/static"
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

# ── 单文件 vs 目录分发取舍（默认 onedir，勿强行改默认以免回归）──────────────
# onedir（默认）：
#   + 启动快（无需解压），文件已铺开；改配置/排查友好
#   - 产物为多文件目录（~154MB），分发需整目录拷贝
# onefile（--onefile）：
#   + 单一可执行文件，分发最干净
#   - 每次启动需把全部依赖解压到临时目录（首次较慢，占用磁盘 I/O），
#     且进程退出才清理临时文件
# 结论：日常交付用 onedir；如需「一个 exe 发给用户」再显式传 --onefile。
#
# ── 图标（--icon）────────────────────────────────────────────────────────
# 当前仓库无 .ico 资源，故不启用 --icon（缺资源会让 pyinstaller 报错）。
# 若有图标资源（如 assets/proofread.ico），可取消下一行注释：
#   [ "$MODE" != "--clean" ] && OPTS+=(--icon "assets/proofread.ico")
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
