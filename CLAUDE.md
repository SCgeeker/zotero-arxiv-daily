# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 我的專案目標

以心理語言學（psycholinguistics）研究者身份，使用此工具每日自動從 arXiv 收集相關論文，依據我的 Zotero 文獻庫進行相似度排序，並透過 email 接收推薦摘要。整個流程部署於 GitHub Actions，裝置關機也能運作。

### 研究領域關鍵字

- 主要 arXiv 類別：`cs.CL`（Computation and Language）、`q-bio.NC`（Neurons and Cognition）
- 相關類別（可選）：`cs.AI`、`cs.HC`（Human-Computer Interaction）
- 研究主題：sortal classifiers、mental simulation、embodied cognition、language comprehension、bilingual processing

### 我的環境

- **作業系統**：Windows（PowerShell）
- **CLI 工具**：`gh`（GitHub CLI）、`uv`、`claude`（均已在 PATH，位於 `~/.local/bin`）
- **Python**：透過 `uv` 管理
- **Zotero**：已有文獻庫，需取得 API key 和 User ID
- **編碼注意**：PowerShell 需設定 UTF-8（`[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`）

### 部署步驟（使用 gh CLI）

```bash
# 1. 認證 GitHub
gh auth login

# 2. 設定 repo secrets（實際值由使用者提供，勿硬編碼）
gh secret set ZOTERO_ID
gh secret set ZOTERO_API_KEY
gh secret set SENDER_EMAIL
gh secret set SENDER_PASSWORD
gh secret set RECEIVER_EMAIL
gh secret set SMTP_SERVER
gh secret set SMTP_PORT

# 3. 調整設定後推送
git add .
git commit -m "Configure for psycholinguistics paper collection"
git push

# 4. 手動觸發測試
gh workflow run main.yml

# 5. 監控執行狀態
gh run watch
gh run list
gh run view <run-id> --log-failed
```

### 待完成設定項目

- [x] 在 `config/custom.yaml` 中設定 arXiv 類別 → 已設定 `["cs.CL","q-bio.NC","cs.AI"]`，`source: ['arxiv']`
- [x] 調整 cron 排程 → `0 22 * * *`（UTC 22:00 = 台灣 06:00），無需更動
- [x] 設定 Gmail SMTP → `smtp.gmail.com:587`，App Password 已設定
- [ ] **設定 Zotero secrets**：`ZOTERO_ID`（數字 ID，非帳號名）和 `ZOTERO_KEY`（API key）尚未設定，導致 404 錯誤
- [ ] 設定 LLM secrets：`OPENAI_API_KEY` 和 `OPENAI_API_BASE` 尚未設定
- [ ] 設定 `ignore_path` 排除不相關的 Zotero 集合（可選）
- [ ] 選擇 reranker 模式（預設 `local`，可選）
- [x] 完整測試：觸發 `test.yml` 並確認收到 email

---

## 專案概述

**Zotero-arXiv-Daily**：依據使用者的 Zotero 文獻庫，每日自動從 arXiv/biorxiv/medrxiv 推薦相關論文並發送 email。設計為可無償部署於 GitHub Actions。

## 常用指令

```bash
# 安裝依賴
uv sync

# 本機執行（需先設好環境變數）
uv run src/zotero_arxiv_daily/main.py

# 執行所有測試（排除需要外部服務的 ci 標記）
uv run pytest

# 執行所有測試（含 ci 標記，需要 mock 服務）
uv run pytest -m ""

# 執行單一測試檔
uv run pytest tests/test_glob_match.py

# 執行單一測試函式
uv run pytest tests/test_glob_match.py::test_function_name
```

### gh CLI 管理指令

```bash
# 工作流程管理
gh workflow list                        # 列出所有 workflows
gh workflow run main.yml                # 手動觸發
gh workflow enable main.yml             # 啟用排程
gh workflow disable main.yml            # 停用排程

# 執行紀錄
gh run list                             # 列出近期執行
gh run watch                            # 即時監控
gh run view <run-id> --log-failed       # 查看失敗 log

# Secrets 管理
gh secret list                          # 列出已設定的 secrets
gh secret set <NAME> --body "<value>"   # 設定 secret
gh secret delete <NAME>                 # 刪除 secret

# Repo 維護
gh repo view --web                      # 瀏覽器開啟 repo
gh issue list                           # 檢視上游 issues
```

## 架構說明

### 核心流程（`executor.py`）

`Executor.run()` 是主要流程：
1. `fetch_zotero_corpus()` → 從 Zotero API 拉取使用者文獻庫（`CorpusPaper` 列表）
2. `filter_corpus()` → 以 `include_path` / `ignore_path` glob patterns 過濾集合路徑
3. 各 `Retriever.retrieve_papers()` → 抓取今日論文（`Paper` 列表）
4. `Reranker.rerank()` → 用 embedding 相似度 × 時間衰減加權排序
5. 對每篇論文呼叫 LLM 產生 TL;DR 及機構列表
6. `render_email()` + `send_email()` → 發送 HTML 郵件

### 資料型別（`protocol.py`）

- `Paper`：候選論文，含 `title`, `abstract`, `url`, `pdf_url`, `full_text`, `tldr`, `affiliations`, `score`
- `CorpusPaper`：Zotero 文獻庫中的論文，含 `added_date`（用於時間衰減）與 `paths`（集合路徑）

### Retriever 擴充機制（`retriever/`）

繼承 `BaseRetriever`，實作 `_retrieve_raw_papers()` 與 `convert_to_paper()`，並用 `@register_retriever("name")` 裝飾器註冊，名稱需對應 `config.source.<name>`。

### Reranker 擴充機制（`reranker/`）

繼承 `BaseReranker`，實作 `get_similarity_score(s1, s2) -> np.ndarray`，並用 `@register_reranker("name")` 裝飾器註冊。目前支援 `local`（sentence-transformers）與 `api`（OpenAI embedding API）。

### 設定管理（Hydra + OmegaConf）

設定以 `config/base.yaml`（完整 schema）為基底，`config/default.yaml` 組合覆寫，`config/custom.yaml` 為使用者自訂層。支援 `${oc.env:VAR,default}` 語法讀取環境變數。

進入點 `main.py` 使用 `@hydra.main(config_path="../../config", config_name="default")`，設定路徑相對於 `src/zotero_arxiv_daily/`。

### 路徑過濾（`utils.glob_match`）

`glob_match(path, pattern)` 使用 `glob.translate()` 將 glob pattern 轉為 regex，比對 Zotero 集合路徑（格式：`ParentCollection/ChildCollection`）。

## 測試架構

- `tests/conftest.py`：package 層級的 `config` fixture，使用 localhost mock 服務
- 標記為 `@pytest.mark.ci` 的測試需要 Docker 服務（mock_openai:30000、mailhog:1025），本機預設跳過
- CI 使用 `tidedra/mock_openai` Docker image 模擬 OpenAI API

## 貢獻規則

PR 必須合併至 `dev` 分支，不是 `main`。
