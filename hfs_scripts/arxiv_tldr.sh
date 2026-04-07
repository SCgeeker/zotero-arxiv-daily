#!/bin/bash
# arxiv_tldr.sh
# 在 TWCC CCS 容器內執行：啟動 Ollama + 跑完整 zotero-arxiv-daily 流程
set -e

LOG="/work/tcpsr001/logs/arxiv_$(date +%Y%m%d_%H%M%S).log"
mkdir -p /work/tcpsr001/logs
exec > >(tee -a "$LOG") 2>&1

echo "=== 開始 $(date) ==="

# 載入 TWCC 環境變數（HF_TOKEN, OLLAMA_MODELS 等）
if [ -f "/home/$(whoami)/env.sh" ]; then
    source /home/$(whoami)/env.sh
fi

# 安裝 uv（若未存在）
if ! command -v uv &> /dev/null; then
    echo "[uv] 安裝中..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# 安裝 Ollama（若未存在）
if ! command -v ollama &> /dev/null; then
    echo "[ollama] 安裝 zstd 依賴..."
    apt-get update -qq
    apt-get install -y zstd
    echo "[ollama] 安裝中..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# 啟動 Ollama server（背景）
echo "[ollama] 啟動 server..."
ollama serve &
OLLAMA_PID=$!
sleep 15

# 建立模型（若未存在）
if ! ollama list | grep -q crystalmind; then
    echo "[ollama] 建立 crystalmind 模型..."
    ollama create crystalmind -f /work/tcpsr001/models/modelfile.txt
fi

# 更新或 clone repo
REPO_DIR="/work/tcpsr001/auto-bibxiv"
if [ -d "$REPO_DIR/.git" ]; then
    echo "[git] 更新 repo..."
    cd "$REPO_DIR"
    git fetch origin 2>&1 && git reset --hard origin/main 2>&1 || {
        echo "[git] fetch/reset 失敗，嘗試重新 clone..."
        cd /work/tcpsr001
        rm -rf "$REPO_DIR"
        git clone https://github.com/SCgeeker/zotero-arxiv-daily.git "$REPO_DIR"
        cd "$REPO_DIR"
    }
else
    echo "[git] Clone repo..."
    git clone https://github.com/SCgeeker/zotero-arxiv-daily.git "$REPO_DIR"
    cd "$REPO_DIR"
fi

# 安裝 Python 依賴（先清除殘留 .venv 避免 uv sync 刪除失敗）
# 固定 Python 3.12 以符合容器系統 torch（避免 3.13 與 3.12 torch 符號衝突）
# 刪除 uv.lock 讓 uv 重新解析適合 cp312 的 torch wheel 版本
echo "[uv] 同步依賴..."
rm -rf .venv 2>/dev/null || true
rm -f uv.lock
uv sync --python python3.12
# TWCC 容器有 V100 GPU，覆蓋 CPU torch 為 CUDA 12.1 版
# CPU-only torch 2.11 缺少 linalg__powsum，CUDA 2.5.1 正常
echo "[torch] 覆蓋安裝 CUDA 12.1 torch (v2.5.1)..."
uv pip install torch==2.5.1+cu121 --index-url https://download.pytorch.org/whl/cu121 --force-reinstall

# 設定 Ollama 作為 OpenAI-compatible endpoint
# custom.yaml 直接使用 repo 的設定（含 include_path、ignore_path、新來源）
export OPENAI_API_BASE=http://localhost:11434/v1
export OPENAI_API_KEY=ollama

echo "[main] 執行主程式..."
# 直接用 venv python 執行，避免 uv run 自動 sync 覆寫剛安裝的 CUDA torch
.venv/bin/python src/zotero_arxiv_daily/main.py

echo "=== 完成 $(date) ==="

# 關閉 Ollama
kill $OLLAMA_PID 2>/dev/null || true
