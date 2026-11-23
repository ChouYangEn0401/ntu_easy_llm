# 0.6.0a3：訂閱登入來源的選裝 extras 與「沒有工具（MCP）」標示

- 日期：2026-11-23
- 狀態：已完成，在分支 `feat/subscription-extras`，等待驗收與合併（尚未打 tag、尚未發佈）
- 用途：交接這一版做了什麼、為什麼、怎麼裝、怎麼驗

## 這版做了什麼

1. **選裝 extras**（`pyproject.toml` 的 `[project.optional-dependencies]`）：

   | extra | 裝的套件 | 來源 |
   |---|---|---|
   | `claude-login` | `claude-subscription` 0.5.1（ClaudeLogin） | ClaudeLogin 的 GitHub Release `v0.5.1` wheel |
   | `copilot` | `github-copilot-login` 0.2.0（GithubCopilotLogin） | GithubCopilotLogin 的 GitHub Release `v0.2.0` wheel |
   | `chatgpt` | `chatgpt-login` 0.2.0（ChatGPTLogin） | ChatGPTLogin 的 GitHub Release `v0.2.0` wheel |
   | `subscriptions` | 上面三個 | 同上 |

   寫法比照 `[llop]`：`<套件名> @ <release wheel 的完整網址>`，**不寫裸套件名**。這些套件不在 PyPI 上，
   寫裸名的話 pip 會去 PyPI 找，別人搶先註冊同名套件就會被裝進來。

2. **「沒有工具（MCP）」到處看得到**：
   - catalog 每個來源多一個 `tools` 欄位：`True`＝支援工具呼叫、`False`＝只能聊天、`None`＝看伺服器與模型。
     `catalog --json` 的葉節點都有 `"tools"`；`ntu_easy_llm catalog` 每行最後印 `[工具 ✓]`／`[工具：看伺服器與模型]`／`[工具 ✗ 只能聊天]`（英文介面對應 `tools ✓`／`tools: depends on server and model`／`tools ✗ chat only`）。
   - 訂閱來源的說明文字加上「只能聊天（文字進出），不支援工具（MCP）」。
   - 訂閱來源帶 `tools` 呼叫，錯誤種類照舊是 `tools_unsupported`，訊息改成白話：
     「這個來源只能聊天，不支援工具（MCP）；要用工具請改用 API 金鑰或本地來源」（後面附英文，跟既有的安裝提示一樣中英並列）。
   - 沒裝登入套件時的提示改成指向 extra：「要先安裝 ntu_easy_llm[claude-login]（claude-subscription 套件）」。
   - README：來源總表加「另外要裝」與「工具（MCP）」兩欄；訂閱來源一段加安裝指令與三個登入套件的關係說明。

3. 版本升到 `0.6.0a3`，CHANGELOG、`VerifyList.json`、`HumanTestList.md` 都已更新。

## 三個登入套件跟 ntu_easy_llm 的關係

- ClaudeLogin、GithubCopilotLogin、ChatGPTLogin 是**獨立的專案**，各自包一個官方 CLI，各自保有那個 CLI 本身的 MCP／agent 能力，照自己的節奏發展。
- ntu_easy_llm **不把它們的程式碼收進來**：`adapters/subscription.py` 在第一次用到時才用 `importlib` 載入，只呼叫它們共同介面（`INTERFACE_VERSION == "1"`）的 `ask()`，文字進、文字出。
- 所以經 ntu_easy_llm 使用時**沒有工具（MCP）**；它們往後加功能，不影響這邊。要用那些 CLI 的工具或 agent 能力，直接用那幾個套件或官方 CLI。
- catalog 只**顯示**訂閱來源需要的 extra；這台能不能用仍由 adapter 自己的檢查決定。這樣別人用 `register_adapter` 或 entry point 替同一個路徑註冊自己的 adapter 時，不會因為沒裝登入套件被擋（既有測試 `test_factory_check_blocks_use` 守這一點）。

## 怎麼裝

```bash
pip install "ntu_easy_llm[claude-login]"     # 只要 Claude 訂閱
pip install "ntu_easy_llm[copilot]"          # 只要 GitHub Copilot 訂閱
pip install "ntu_easy_llm[chatgpt]"          # 只要 ChatGPT 訂閱
pip install "ntu_easy_llm[subscriptions]"    # 三個都要

# 從 wheel 檔
pip install "ntu_easy_llm-0.6.0a3-py3-none-any.whl[subscriptions]"
```

裝完還要在同一台電腦裝好對應的官方 CLI 並登入；`ntu_easy_llm catalog` 會顯示哪幾個來源可用。

## 已知限制

- 只能文字進出：沒有工具（MCP）、沒有圖片、沒有 `stop`；`max_tokens`／`temperature`／`effort` 會被忽略並記在 `ChatReply.warnings`。
- 只給本人使用：用的是這台電腦上你自己登入的個人訂閱，不要拿來服務別人；完整的使用聲明在 README「訂閱登入來源的使用聲明」。
- extras 釘的是固定版本的 release wheel；登入套件出新版時，要在這邊改網址、跑測試、出新的 ntu_easy_llm 版本。
- 真的 CLI 與登入只能由人測（見 `HumanTestList.md`）；自動測試用假的登入套件。

## 怎麼驗

```bash
# 離線：全套測試（Windows 先設 PYTHONIOENCODING=utf-8）
python -m pytest -q
python -m pytest -q tests/test_extras.py tests/test_subscription_adapters.py   # 這版新增與修改的部分

# 看 catalog 的標示
ntu_easy_llm catalog
ntu_easy_llm catalog --json      # 訂閱來源的葉節點："tools": false、"extra": "claude-login" 等

# 要網路：每個 extra 的網址都下載得到（只下載、不安裝）
pip download --no-deps <extra 裡的網址> -d <暫存資料夾>

# 建好的 wheel：METADATA 要有
#   Provides-Extra: claude-login / copilot / chatgpt / subscriptions
#   Requires-Dist: claude-subscription@ https://...; extra == "claude-login"（其他同理）
python -m build --outdir <暫存資料夾>
```

`VerifyList.json` 的 `v06a3-extras-and-no-tools-mark`（離線）與 `v06a3-extras-urls-download`（要網路）就是上面兩項。
