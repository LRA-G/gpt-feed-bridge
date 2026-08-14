# GPT Feed Bridge

把多個公開 RSS／Atom 來源交給 OpenAI API 產生繁體中文摘要，再發布成一個可供 Inoreader 免費版訂閱的標準 RSS。

## 產出的文章內容

- 繁體中文標題與摘要
- 三至五項重點
- 與個人興趣的關聯
- 專有名詞說明與標籤
- 原文連結、作者及日期
- 可選的全文翻譯；預設關閉，避免不必要的成本與全文重製

## 本機設定

1. 複製 `.env.example` 為 `.env`，但不要提交真正的金鑰。
2. 在執行環境設定 `OPENAI_API_KEY`。
3. 編輯 `config.json` 的 `sources`：

```json
"sources": [
  {
    "name": "來源名稱",
    "url": "https://example.com/feed.xml",
    "enabled": true
  }
]
```

4. 執行：

```powershell
python -m src.gpt_feed_bridge --config config.json
```

首次執行預設每個來源只處理最新一篇，避免突然消耗大量 API 用量；可由 `initial_backfill_per_source` 調整。

不使用 API 的格式測試：

```powershell
python -m src.gpt_feed_bridge --config tests/fixtures/config.json --mock-ai
```

## GitHub 排程與公開網址

專案附有 `.github/workflows/update-feed.yml`，每小時第 17 分鐘執行一次。

1. 建立 GitHub 儲存庫並上傳專案。
2. 在儲存庫 `Settings → Secrets and variables → Actions` 建立 `OPENAI_API_KEY`。
3. 在 `Settings → Pages` 選擇從 `main` 分支的 `/docs` 發布。
4. 修改 `config.json` 的 `public_url` 與 `home_url`。
5. 在 Actions 手動執行一次 `Update GPT reading feed`。
6. 將 `https://帳號.github.io/儲存庫/feed.xml` 加入 Inoreader。

## 隱私與來源限制

- GitHub Pages 的網址是公開的，不要放入私人通訊或機密資料。
- 即使使用難猜的網址，也不等於真正的存取控制。
- 來源只提供摘要時，本工具無法合法或可靠地取得付費牆後的全文。
- 第三方內容建議只保存摘要與原文連結；全文翻譯適合自己擁有或已獲授權的內容。

## 失敗處理

- 來源失效或單篇 OpenAI 請求失敗時，其餘來源仍會繼續。
- 失敗項目不會被標記為完成，下次排程會重試。
- `data/state.json` 保存已處理文章，避免每次更新重複花費 API 用量。
- `docs/status.json` 記錄最近執行狀態與錯誤。
