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
    git pull --ff-only 2>/dev/null || true
else
    echo "[git] Clone repo..."
    git clone https://github.com/SCgeeker/zotero-arxiv-daily.git "$REPO_DIR"
    cd "$REPO_DIR"
fi

# 安裝 Python 依賴（先清除殘留 .venv 避免 uv sync 刪除失敗）
# 固定 Python 3.12 以符合容器系統 torch（避免 3.13 與 3.12 torch 符號衝突）
echo "[uv] 同步依賴..."
rm -rf .venv 2>/dev/null || true
uv sync

# 設定 Ollama 作為 OpenAI-compatible endpoint
# custom.yaml 直接使用 repo 的設定（含 include_path、ignore_path、新來源）
export OPENAI_API_BASE=http://localhost:11434/v1
export OPENAI_API_KEY=ollama

echo "[main] 執行主程式..."
uv run src/zotero_arxiv_daily/main.py

echo "=== 完成 $(date) ==="

# 關閉 Ollama
kill $OLLAMA_PID 2>/dev/null || true
