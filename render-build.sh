#!/usr/bin/env bash
# Render 构建脚本 — 安装系统依赖（ffmpeg）
set -e
echo ">>> 安装 ffmpeg..."
apt-get update -qq && apt-get install -y -qq ffmpeg
echo ">>> ffmpeg 安装完成: $(which ffmpeg)"
