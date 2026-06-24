# 測試紀錄 (TEST_LOG)

本檔如實記錄 `ntu_easy_llm` 在部署前的煙霧測試結果，供後續維護與部署判斷參考。

- **套件版本**：0.3.0
- **測試日期**：2026-06-24
- **測試環境**：Windows 10 Pro / Python 3.11+ / 主控台編碼 cp950
- **測試工具**：[tests/smoke_test.py](tests/smoke_test.py)
- **執行者**：Claude (Opus 4.8) 協助執行，結果原樣記錄

---

## 測試範圍與方法

| 層級 | 說明 | 是否需金鑰 / 連網 |
|------|------|------------------|
| 離線段 | 公開 API import、PEP 562 lazy 載入、response parser、AES/RSA 金鑰加解密、`LLMSession` 存讀/壓縮/async、Adapter 建構、錯誤處理 | 否（用臨時 `.env` 與假金鑰自給自足） |
| 線上段 (`--live`) | 各平台 `list_models` / `ask` / `ask_async` / `Adapter.ask` / 統一 `ask()`、多輪對話記憶 + save/resume | 是（讀專案 `.env`，產生少量 token） |
| Web 搜尋 (`--web`) | ChatGPT / Gemini 的 `web_search=True` | 是 |
| 乾淨安裝 (clean-room) | `python -m build` 打包成 wheel → 全新隔離 venv 安裝 → 在沒有 `src/` 的目錄執行離線段，驗證打包/依賴/免 `src.` 前綴 import | 否 |

> `__all__` 共 31 個公開名稱全部被涵蓋；型別別名 `ChatGPTModel` / `GeminiModel` / `AnthropicModel` / `AnyModel` 為 `typing.Literal`，僅驗證可 import（本身無 runtime 行為）。

---

## 1. 離線段（原始碼 in-repo）

指令：`python tests/smoke_test.py`
結果：**通過 11 / 失敗 0 / 略過 0**，退出碼 0

| 項目 | 結果 |
|------|------|
| `__version__` 可讀且為字串 | PASS（v0.3.0） |
| `__all__` 每個公開名稱皆可 lazy 載入 | PASS（31 個全部可載入） |
| response parser 正確萃取 + 處理 None/空值 | PASS（4 種 parser） |
| `load_api_key` / `KeyMaterial.plain` / `.aes` 端對端還原 | PASS（經由 `.env` 探索 → resolve） |
| `rsa_encrypt` / `RSADecryptStrategy` 對稱還原 | PASS |
| `KeyMaterial` 是 lazy 的（建構 ≠ 取值） | PASS（first-touch） |
| `LLMSession.save` / `resume` 往返保真 | PASS（id / history / provider 一致） |
| `LLMSession.compress_history` 壓縮邏輯 | PASS（12 則 → keep_last=2 → 5 則，含 system 摘要） |
| `LLMSession.ask_async`（背景執行緒 + callback） | PASS（Future + end_callback、歷史正確更新） |
| Adapter 建構（不連網） | PASS（三家皆為 `LLMAdapter` 子類） |
| 未知 provider 明確報錯 | PASS（`LLMSession` 與 `ask()` 皆丟 `ValueError`） |

---

## 2. 乾淨安裝（clean-room wheel install）

步驟：
1. `python -m build --wheel` → 產出 `dist/ntu_easy_llm-0.3.0-py3-none-any.whl`
   （註：先前 `dist/` 內為過時的 0.1.3，本次已重建為 0.3.0）
2. 建立全新隔離 venv，僅 `pip install` 該 wheel（連帶安裝依賴）
3. 將 `smoke_test.py` 複製到「沒有 `src/` 同層」的目錄後執行離線段

驗證 import 來源：

```
ntu_easy_llm.__file__ =
  ...\scratchpad\cleanroom\venv\Lib\site-packages\ntu_easy_llm\__init__.py
```

確認 import 的是**已安裝的套件**（非原始碼），且**不需 `src.` 前綴**。

結果：**通過 11 / 失敗 0 / 略過 0**，退出碼 0。
→ wheel 可正確打包、依賴可安裝、公開 import 路徑正確。

---

## 3. 線上段 + Web 搜尋（`python tests/smoke_test.py --live --web`）

偵測到金鑰：`chatgpt`, `gemini`, `anthropic`
結果：**通過 23 / 失敗 6 / 略過 0**，退出碼 1

### ChatGPT — 全數通過

| 項目 | 結果 |
|------|------|
| `list_chatgpt_models` | PASS（120 個模型） |
| `ask_chatgpt`（阻斷） | PASS（回應 "OK"） |
| `ask_chatgpt_async` + end_callback | PASS（Future.result() 與 callback 一致） |
| `ChatGPTAdapter.ask` | PASS（回應 "OK"） |
| 統一 `ask("CHATGPT", ...)` | PASS（回應 "OK"） |
| `web_search=True` | PASS（回傳含實時連結的 grounded 內容，如 `## [台北市](https://www.google.com/maps/...)`） |

### Anthropic — 全數通過

| 項目 | 結果 |
|------|------|
| `list_anthropic_models` | PASS（9 個模型） |
| `ask_anthropic`（阻斷） | PASS（回應 "OK"） |
| `ask_anthropic_async` + end_callback | PASS |
| `AnthropicAdapter.ask` | PASS（回應 "OK"） |
| 統一 `ask("ANTHROPIC", ...)` | PASS（回應 "OK"） |
| （web_search 不適用，Anthropic 不支援，依設計略過） | — |

### LLMSession 多輪對話（以 chatgpt 進行）

| 項目 | 結果 |
|------|------|
| 跨輪記憶 + 真實 save/resume | PASS（跨輪記得 "42"，4 則歷史，save/resume OK） |

### Gemini — 全數失敗（**原因：金鑰失效，遭使用者刪除，非套件 bug**）

| 項目 | 結果 | 錯誤 |
|------|------|------|
| `list_gemini_models` | FAIL | `RuntimeError: Cannot send a request, as the client has been closed.` |
| `ask_gemini`（阻斷） | FAIL | `400 INVALID_ARGUMENT — API Key not found.`（`API_KEY_INVALID`） |
| `ask_gemini_async` | FAIL | 同上 |
| `GeminiAdapter.ask` | FAIL | 同上 |
| 統一 `ask("GEMINI", ...)` | FAIL | 同上 |
| `web_search=True` | FAIL | 同上 |

**失敗根因**：使用者已自行刪除 `.env` 中的 `gemini=` 金鑰；Google API 回傳 `API_KEY_INVALID`。
本次測試流程**未改動** `.env`（smoke test 只讀取、不寫入）。

**待釐清項**：`list_gemini_models` 的錯誤訊息是 `client has been closed`，與其他 5 項的 `API_KEY_INVALID` 不同。這很可能只是 `google-genai` 在認證失敗時，pager 延遲取頁時 client 已關閉的呈現方式。**待換上有效 gemini 金鑰重跑後**：
- 若該項轉為 PASS → 確認只是無效金鑰的副作用。
- 若仍報 `client has been closed` → 即為 [list_gemini_models](src/ntu_easy_llm/core/utils.py#L336) 的潛在 bug（client 在 pager 迭代完成前被回收），屆時需修正（在 client 存活期間就把清單具現化）。

---

## 結論

- **套件本身（待部署標的）功能正確**：離線段（原始碼與乾淨安裝 wheel）皆 11/11 通過；ChatGPT 與 Anthropic 線上全綠，含 ChatGPT web 搜尋與多輪 Session 記憶 / save / resume。
- **打包與安裝已驗證**：wheel 可建置、可安裝、依賴齊全、免 `src.` 前綴 import。
- **唯一未綠項為 Gemini**，且根因為缺少有效金鑰（使用者已刪除），非程式碼缺陷。補上有效 `gemini=` 金鑰後重跑 `--live --web` 即可完整收尾，同時驗證上述「待釐清項」。

### 重現方式

```bash
python tests/smoke_test.py              # 離線段（免金鑰、免費）
python tests/smoke_test.py --live       # 線上段（需 .env 金鑰）
python tests/smoke_test.py --live --web # 額外測 web_search
python tests/smoke_test.py --live --only anthropic   # 只測單一平台
```

退出碼 0 = 全過；非 0 = 有失敗（可作為部署 gate / CI 判斷）。
