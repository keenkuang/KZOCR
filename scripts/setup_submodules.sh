#!/usr/bin/env bash
# 拉取两个上游子模块（kimi 引擎 + zai 控制台）。
# 注意：本机直连 github.com HTTPS 被拦截，已配置 SSH 远程可用，故这里用 SSH URL。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

echo "==> 初始化 submodule..."
git submodule update --init --recursive

echo "==> 目录结构:"
ls -d engines/* console/* 2>/dev/null || true

echo "==> 若需本地开发但不想 clone 全量，可在 config 里直接指向现有目录："
echo "    export KIMI_ENGINE_DIR=/home/keen/kimi_agent_ocr/tcm_ocr_system_v1.1"
echo "    export ZAI_DIR=/home/keen/tcm_ocr_zai"
