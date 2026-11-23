# 新手教學：用一份設定檔，切換本地模型、API 金鑰、LLOP 與訂閱

這份教學寫給**沒什麼電腦概念的人**。照著一步一步做，每一步都寫了「要打什麼」「會看到什麼」「不是這樣怎麼辦」。
完整的說明在 [README](../README.md)；設計細節在 [docs/design/20261123-v06-architecture.md](design/20261123-v06-architecture.md)。
這份教學以 **0.6.0a3**（預發布版）為準。

---

## 這是什麼

- `ntu_easy_llm` 是一個 Python 套件，讓你**用同一種寫法**跟很多種 AI 模型講話。
- 你寫一份**設定檔**（一個文字檔），裡面列出「怎麼連到哪個模型」，每一種連法叫一個 **profile**（設定組）。之後只要換 profile 的名字，就換了模型。
- 能接的來源（**來源**＝模型從哪裡來）：

  | 來源 | 例子 | 能不能用工具（MCP） |
  |---|---|---|
  | 本地伺服器 | 你自己電腦上的 LM Studio、Ollama | 看伺服器與模型 |
  | API 金鑰 | OpenAI、Anthropic、Gemini | 可以 |
  | LLOP | 別人用 LLOP 分享給你的模型 | 看服務端的模型 |
  | 訂閱登入 | 你電腦上已登入的 Claude、GitHub Copilot、ChatGPT 官方工具 | **不行，只能聊天** |

- 還可以設定**備援**：第一個來源不能用（額度用完、金鑰失效）時，自動或問過你之後換下一個。

## 你需要準備什麼

| 要準備的 | 說明 |
|---|---|
| 一台 Windows 電腦 | 這份教學以 Windows 10／11 為例 |
| Python 3.11 或更新 | 步驟 2 會檢查 |
| 至少一個來源 | 下面四種挑你有的就好，不用全部都有 |
| 網路 | 安裝要連網 |

四種來源各要準備：

- **本地 LM Studio**：已經裝好 LM Studio、下載好一個模型。
- **API 金鑰**：例如 OpenAI 的 API key（`sk-` 開頭的一串字）。
- **LLOP**：別人給你的 API 連結和 key，並且已經照 LLOP 的教學建好設定檔。
- **訂閱登入**：已經照 ClaudeLogin／GithubCopilotLogin／ChatGPTLogin 的教學，在這台電腦裝好官方工具並登入。

先認識幾個詞：

- **終端機**＝打指令的視窗。Windows 內建的叫 **PowerShell**。
- **指令**＝在終端機裡打的一行字，打完按 Enter 執行。
- **環境變數**＝存在這個終端機視窗裡的一個「名字＝值」，程式可以讀到。金鑰放這裡，就不用寫進檔案。

---

## 步驟

### 步驟 1：打開 PowerShell

1. **做什麼**：開一個終端機視窗。
2. **打什麼**：按鍵盤左下角的 Windows 鍵，打 `PowerShell`，按 Enter。
3. **會看到什麼**：最後一行是 `PS` 開頭、`>` 結尾，中間是你目前所在的資料夾（每台電腦不同）：

   ```
   PS ...>
   ```

4. **不是這樣怎麼辦**：在「開始」按右鍵，選「Windows PowerShell」或「終端機」。

之後每一步的指令都在這個視窗裡打。**一次複製一行**，在視窗裡按右鍵貼上，按 Enter。

### 步驟 2：確認 Python 是 3.11 或更新

1. **做什麼**：看 Python 的版本。
2. **打什麼**：

   ```powershell
   python --version
   ```

3. **會看到什麼**：

   ```
   Python 3.12.8
   ```

   `3.` 後面的數字要 **11 或更大**。
4. **不是這樣怎麼辦**：沒裝或太舊，到 <https://www.python.org/downloads/> 下載 Windows 安裝檔，安裝畫面第一頁**一定要勾「Add python.exe to PATH」**。
   裝好後**關掉 PowerShell 再開一個新的**，重打一次。

### 步驟 3：開資料夾、建虛擬環境

**虛擬環境**＝一個獨立的 Python 小房間，裝在裡面的套件不會影響電腦其他程式。

1. **做什麼**：開一個練習用的資料夾，在裡面建虛擬環境並啟用。
2. **打什麼**（一次一行）：

   ```powershell
   mkdir $HOME\easyllm-demo
   ```

   ```powershell
   cd $HOME\easyllm-demo
   ```

   ```powershell
   python -m venv .venv
   ```

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

3. **會看到什麼**：最後提示字最前面多了 `(.venv)`，結尾是資料夾名稱：

   ```
   (.venv) PS ...\easyllm-demo>
   ```

4. **不是這樣怎麼辦**：
   - 最後一行出現紅字，裡面有「**因為這個系統上已停用指令碼執行**」：先打下面這行（只對這個視窗有效），再打一次 `.\.venv\Scripts\Activate.ps1`：

     ```powershell
     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
     ```

   - `mkdir` 說已經存在：沒關係，接著打下一行。
   - **以後每次重開 PowerShell**，都要先 `cd $HOME\easyllm-demo`，再打 `.\.venv\Scripts\Activate.ps1`。

### 步驟 4：安裝 ntu_easy_llm

這個套件還沒上 PyPI（Python 的公開套件庫），要從 GitHub Release 的網址裝。
網址前面的 `[...]` 叫 **extras**（選裝的附加功能），依你要用的來源挑：

| 你要用 | 寫在 `[...]` 裡 |
|---|---|
| 只用本地模型或 API 金鑰 | 不用寫（整個 `[...]` 拿掉） |
| LLOP | `llop` |
| Claude 訂閱 | `claude-login` |
| GitHub Copilot 訂閱 | `copilot` |
| ChatGPT 訂閱 | `chatgpt` |
| 三種訂閱都要 | `subscriptions` |
| 設定檔想寫成 YAML（另一種設定檔格式） | `yaml` |

好幾個用逗號隔開，例如 `[llop,subscriptions]`。

> `[llop]` 裝到的 LLOP SDK 是 **0.3.0a3**，可以連 0.3.0a4 的 LLOP 伺服器：0.3.0a4 只改了伺服器的啟動畫面（不再印完整的 API 連結），SDK 沒有變（見 LLOP 的 CHANGELOG）。

1. **做什麼**：安裝套件。下面這行是「LLOP＋三種訂閱」的寫法，照上表改 `[...]` 裡的字。
2. **打什麼**：

   ```powershell
   python -m pip install "ntu_easy_llm[llop,subscriptions] @ https://github.com/ChouYangEn0401/ntu_easy_llm/releases/download/ntu_easy_llm-0.6.0a3/ntu_easy_llm-0.6.0a3-py3-none-any.whl"
   ```

   裝好後確認版本：

   ```powershell
   ntu_easy_llm -V
   ```

3. **會看到什麼**：安裝最後一行是 `Successfully installed` 開頭的一長串套件名。確認版本那行印出：

   ```
   0.6.0a3
   ```

4. **不是這樣怎麼辦**：
   - 一定要有**前後的雙引號**，不然 PowerShell 會看不懂 `[` `]` 和 `@`。
   - 只用本地或 API 金鑰時，寫成 `python -m pip install https://github.com/ChouYangEn0401/ntu_easy_llm/releases/download/ntu_easy_llm-0.6.0a3/ntu_easy_llm-0.6.0a3-py3-none-any.whl`（不用引號）。
   - `ntu_easy_llm` 這個字「無法辨識」：提示字前面沒有 `(.venv)`，回到步驟 3 啟用虛擬環境。也可以改打 `python -m ntu_easy_llm.cli -V`，之後的指令都可以把 `ntu_easy_llm` 換成 `python -m ntu_easy_llm.cli`。

### 步驟 5：看有哪些來源可以用

1. **做什麼**：列出所有來源，以及這台電腦現在能不能用。這一步不連網、不花錢。
2. **打什麼**：

   ```powershell
   ntu_easy_llm catalog
   ```

3. **會看到什麼**：一棵樹。每一行是一個來源，後面寫 `[可用]` 或 `[不可用: 原因]`，最後是能不能用工具。下面只節錄幾行（實際會列出全部來源）：

   ```
   local  本地（這台或區網的伺服器）
     local/lmstudio  LM Studio  [可用]  [工具：看伺服器與模型]
   remote  遠端服務
     remote/api_key  API 金鑰
       remote/api_key/openai  OpenAI  [可用]  [工具 ✓]
       remote/api_key/llop  LLOP（本地模型發布服務）  [可用]  [工具：看伺服器與模型]
     remote/subscription  訂閱登入（這台已登入的官方 CLI）
       remote/subscription/claude  Claude（訂閱）  [可用]  [工具 ✗ 只能聊天]
   ```

   每一行最前面那串（例如 `local/lmstudio`）就是等一下設定檔裡 `source` 要填的字。
4. **不是這樣怎麼辦**：你要用的來源寫 `[不可用: 要先安裝 ntu_easy_llm[...]…]`，例如：

   ```
   remote/api_key/llop  LLOP（本地模型發布服務）  [不可用: 要先安裝 ntu_easy_llm[llop]]  [工具：看伺服器與模型]
   ```

   照方括號裡的名字回到步驟 4，把它加進 `[...]` 重裝一次。想看英文：`ntu_easy_llm catalog --locale en`。

### 步驟 6：寫設定檔

設定檔是一個叫 `profiles.json` 的文字檔，裡面每一組設定叫一個 **profile**，有自己的名字（下面用 `lmstudio`、`openai`、`friend`、`claude_sub`）。
**設定檔裡永遠不寫金鑰本身**，只寫「金鑰放在哪個環境變數」。就算你不小心把金鑰寫進去，程式也會拒絕讀取。

1. **做什麼**：用記事本建立設定檔。下面有五份範本，**挑一份最符合你的，整份複製**，不用刪任何東西。
2. **打什麼**：

   ```powershell
   notepad profiles.json
   ```

   記事本問「要建立新檔案嗎？」按「是」。把你挑的那一份**整份**貼上，照表格改掉要改的地方，按 Ctrl+S 存檔，關掉記事本。

   **範本一：只有 LM Studio**

   ```json
   {
     "version": 1,
     "default": "lmstudio",
     "profiles": {
       "lmstudio": {
         "source": "local/lmstudio",
         "base_url": "http://127.0.0.1:1234/v1",
         "model": "example-model"
       }
     }
   }
   ```

   **範本二：只有 API 金鑰（OpenAI）**

   ```json
   {
     "version": 1,
     "default": "openai",
     "profiles": {
       "openai": {
         "source": "remote/api_key/openai",
         "api_key_env": "MY_OPENAI_KEY",
         "model": "gpt-4.1-mini"
       }
     }
   }
   ```

   **範本三：只有 LLOP**

   ```json
   {
     "version": 1,
     "default": "friend",
     "profiles": {
       "friend": {
         "source": "remote/api_key/llop",
         "llop_profile": "tiny",
         "model": "any"
       }
     }
   }
   ```

   **範本四：只有訂閱（Claude）**

   ```json
   {
     "version": 1,
     "default": "claude_sub",
     "profiles": {
       "claude_sub": {
         "source": "remote/subscription/claude",
         "model": "haiku"
       }
     }
   }
   ```

   **範本五：全部都有**

   ```json
   {
     "version": 1,
     "default": "lmstudio",
     "profiles": {
       "lmstudio": {
         "source": "local/lmstudio",
         "base_url": "http://127.0.0.1:1234/v1",
         "model": "example-model"
       },
       "openai": {
         "source": "remote/api_key/openai",
         "api_key_env": "MY_OPENAI_KEY",
         "model": "gpt-4.1-mini"
       },
       "friend": {
         "source": "remote/api_key/llop",
         "llop_profile": "tiny",
         "model": "any"
       },
       "claude_sub": {
         "source": "remote/subscription/claude",
         "model": "haiku"
       }
     }
   }
   ```

   要改的地方：

   | profile | 來源 | 你要改的地方 |
   |---|---|---|
   | `lmstudio` | 本地 LM Studio | `1234` 換成 LM Studio 畫面上顯示的埠號；`example-model` 等一下（步驟 7）換成真的模型名稱 |
   | `openai` | OpenAI 的 API 金鑰 | 金鑰放在環境變數 `MY_OPENAI_KEY`（步驟 8）；`model` 換成你要的模型 |
   | `friend` | LLOP | `llop_profile` 填**你在 LLOP 那邊建的設定檔名稱**（見下面說明）；`any`＝讓對方伺服器決定模型 |
   | `claude_sub` | Claude 訂閱 | `haiku` 可以換 `sonnet`、`opus`。GitHub Copilot 把 `source` 換成 `remote/subscription/github_copilot`、ChatGPT 換成 `remote/subscription/chatgpt`，並把 `"model": "haiku"` 那行刪掉（用那個工具的預設模型） |

   **`llop_profile` 填什麼**：照 LLOP 的教學，B 段會在你電腦上建一個 LLOP 自己的設定檔：
   - 用 API 連結＋key 建的**明文**設定檔（`llmop client add-plain`），教學裡叫 `tiny`；
   - 用帳號建的**加密**設定檔（`llmop client enroll`），教學裡叫 `home`。

   範本裡寫的是 `tiny`；你用加密的話改成 `home`。這是 LLOP 設定檔的名字，跟 `profiles.json` 裡的 profile 名字（`friend`）是兩回事。

   `"default"` 是「沒指定時用哪一組」，要寫成這份檔案裡有的其中一個 profile 名字。
3. **會看到什麼**：資料夾裡多了 `profiles.json`。打 `dir` 可以看到它。
4. **不是這樣怎麼辦**：後面的指令如果出現 `config error: config is not valid JSON: …`（例如 `Expecting property name enclosed in double quotes`），
   是貼的時候少貼或多貼了字。重新 `notepad profiles.json`，全部刪掉，再整份貼一次範本。

### 步驟 7：（本地 LM Studio）找出模型名稱、確認連得上

1. **做什麼**：先打開 LM Studio、載入模型、打開它的本機伺服器（在 LM Studio 的開發者分頁，畫面會顯示網址與埠號）。再讓 ntu_easy_llm 列出模型名稱。
2. **打什麼**：

   ```powershell
   ntu_easy_llm models --config profiles.json --profile lmstudio
   ```

3. **會看到什麼**：每行一個模型名稱，已載入的後面有 `[loaded]`，例如：

   ```
   example-model  [loaded]  ctx=32768
   ```

   把你要用的那個名字，填回 `profiles.json` 裡 `lmstudio` 的 `"model"`（`notepad profiles.json` 改完存檔）。然後確認連得上：

   ```powershell
   ntu_easy_llm probe --config profiles.json --profile lmstudio
   ```

   成功會印出 `ok` 開頭的一行，例如 `ok (0.05s); 1 model(s) listed`（秒數和數量每台不同）。
4. **不是這樣怎麼辦**：
   - `failed [connection]` 開頭，或一行紅字的連線錯誤：LM Studio 的伺服器沒開，或埠號不對。對照 LM Studio 畫面上的網址改 `base_url`。
   - `config error: no profile named 'lmstudio'`：設定檔裡沒有叫 `lmstudio` 的組，`--profile` 後面要寫設定檔裡真的有的名字。

### 步驟 8：（API 金鑰）把金鑰放進環境變數

1. **做什麼**：把金鑰存到這個視窗的環境變數 `MY_OPENAI_KEY`。設定檔只寫這個名字，不寫金鑰。
2. **打什麼**（`sk-xxxx` 換成你自己的金鑰）：

   ```powershell
   $env:MY_OPENAI_KEY = "sk-xxxx"
   ```

3. **會看到什麼**：沒有任何訊息，直接回到提示字，這樣就對了。
4. **不是這樣怎麼辦**：
   - 這個設定**只對這個視窗有效**，關掉就沒了，下次要重打。
   - 想讓它一直有效：打 `setx MY_OPENAI_KEY "sk-xxxx"`，然後**關掉 PowerShell 重開**。注意金鑰會存在這台電腦的使用者設定裡。
   - 千萬不要把金鑰貼進 `profiles.json`、聊天或截圖。

### 步驟 9：（LLOP）確認 LLOP 設定檔在

1. **做什麼**：LLOP 的 key 與連結不放在 ntu_easy_llm 的設定檔，而是放在 LLOP 自己的設定檔。先確認它在。
2. **打什麼**：

   ```powershell
   llmop client profiles
   ```

3. **會看到什麼**：每行一個 LLOP 設定檔，第一欄是名字，第二欄是明文（`plain`）或加密（`e2e`），例如：

   ```
   tiny	plain	ready	https://example.trycloudflare.com/…	釘選 0
   ```

   第一欄的名字要跟 `profiles.json` 裡 `friend` 的 `"llop_profile"` 一樣。
4. **不是這樣怎麼辦**：
   - `還沒有任何設定檔。用 llmop client enroll 或 llmop client add-plain 建立。`：照 LLOP 教學的「B：別人給了我連結和 key」做完再回來。
   - `llmop` 這個字「無法辨識」：步驟 4 沒有加 `llop`，或虛擬環境沒啟用。

### 步驟 10：（訂閱）確認官方工具找得到

1. **做什麼**：確認這台電腦找得到官方工具（這一步不登入、不花錢、不送問題）。
2. **打什麼**：

   ```powershell
   ntu_easy_llm probe --config profiles.json --profile claude_sub
   ```

3. **會看到什麼**：

   ```
   ok (0.06s): CLI found; sign-in is only checked when a prompt is sent (send=True); 1 model(s) listed
   ```

   意思是「找到官方工具了；有沒有登入，要真的送一句話才知道」。
4. **不是這樣怎麼辦**：
   - `config error: profile claude_sub: remote/subscription/claude is not usable here: 要先安裝 ntu_easy_llm[claude-login]…`：步驟 4 沒有加對應的 extras。
   - `failed [connection] …: claude: the official CLI was not found on this machine`：官方工具沒裝，照 ClaudeLogin 的教學裝好、登入。

### 步驟 11：問一句話

1. **做什麼**：用設定檔裡的一組，真的送一句話。（API 金鑰與訂閱會用掉一點額度。）
2. **打什麼**（`lmstudio` 換成你要用的那組名字）：

   ```powershell
   ntu_easy_llm chat --config profiles.json --profile lmstudio "用一句話介紹你自己"
   ```

3. **會看到什麼**：等一下，印出模型的回答，然後回到提示字。想邊收邊印，在最後加 `--stream`。
4. **不是這樣怎麼辦**：

   | 看到 | 意思 | 怎麼辦 |
   |---|---|---|
   | `error [auth_invalid]: profile openai: environment variable MY_OPENAI_KEY is not set` | 環境變數沒設 | 做步驟 8（同一個視窗） |
   | `config error: profiles.openai.api_key: a config may not contain the key itself — …` | 把金鑰寫進設定檔了 | 從設定檔刪掉，改用 `api_key_env` |
   | `config error: cannot read config file profiles.json: No such file or directory` | 不在對的資料夾 | 先 `cd $HOME\easyllm-demo` |
   | `failed [bad_request] …: llop: profile_not_found: 找不到設定檔「tiny」。…`（probe 時） | `llop_profile` 跟 LLOP 設定檔名稱對不上 | 做步驟 9 |
   | `error [link_changed]: …` | LLOP 的 API 連結換了 | 向提供者拿新連結，`llmop client set-link --profile tiny <新連結>`（`tiny` 換成你的 LLOP 設定檔名稱） |
   | `error [quota_exhausted]: …` | 額度用完 | 等額度回來，或設定備援（步驟 12） |

   結束碼：0＝成功、1＝供應商或網路失敗、2＝設定有問題。

### 步驟 12：設定備援（可選）

**備援**＝把好幾組排成一隊（叫 **chain**），第一組不能用（額度用完、金鑰失效、太頻繁且重試後仍失敗）時換下一組。
一般的錯誤（例如問題太長）不會換。沒寫備援就不會換。

1. **做什麼**：在設定檔加一段 `chains`。有兩種模式：
   - `auto`：自動換，並告訴你換成誰。
   - `ask`：不自動換，停下來讓你的程式決定。
2. **打什麼**：`notepad profiles.json`，把內容**整份換成**下面這份（這是範本五加上兩條 chain：`backup` 是 auto、`careful` 是 ask；用不到的 profile 留著沒關係，只有被叫到才會用），存檔：

   ```json
   {
     "version": 1,
     "default": "lmstudio",
     "profiles": {
       "lmstudio": {
         "source": "local/lmstudio",
         "base_url": "http://127.0.0.1:1234/v1",
         "model": "example-model"
       },
       "openai": {
         "source": "remote/api_key/openai",
         "api_key_env": "MY_OPENAI_KEY",
         "model": "gpt-4.1-mini"
       },
       "friend": {
         "source": "remote/api_key/llop",
         "llop_profile": "tiny",
         "model": "any"
       },
       "claude_sub": {
         "source": "remote/subscription/claude",
         "model": "haiku"
       }
     },
     "chains": {
       "backup": {
         "members": ["openai", "lmstudio"],
         "waterfall": {"mode": "auto"}
       },
       "careful": {
         "members": ["openai", "lmstudio"],
         "waterfall": {"mode": "ask"}
       }
     }
   }
   ```

   記得把 `1234`、`example-model`、`tiny` 這些照步驟 6 的表格改成你的。然後用 `--chain` 跑：

   ```powershell
   ntu_easy_llm chat --config profiles.json --chain backup "用一句話介紹你自己"
   ```

3. **會看到什麼**：
   - 第一組正常：印出回答，下面一行 `[openai]`（是哪一組回答的）。
   - 第一組不能用：印出回答，並多一行 `notice: 已改用 lmstudio（原因：金鑰失效）`，下一行 `[lmstudio]`。
   - 用 `--chain careful`（`ask` 模式）時第一組不能用：不會換，印出
     `error [auth_invalid]: openai: key invalid; fallback candidates: lmstudio`。在命令列只能看到這句；要「問過再換」，得在自己的程式裡處理（README 的「備援路由」有範例）。
4. **不是這樣怎麼辦**：
   - `config error: chains.backup.members: unknown profiles …`：`members` 裡的名字要跟 `profiles` 裡的一樣。
   - `config error: ids used for both a profile and a chain: …`：chain 的名字不能跟 profile 的名字重複。
   - `config error: chains.backup.waterfall.mode must be one of off, auto, ask, got …`：`mode` 只能寫 `off`、`auto`、`ask`。

---

## 常見問題

**訂閱來源可以用工具（MCP）嗎？**
不行。訂閱來源（Claude、GitHub Copilot、ChatGPT 訂閱）**只能聊天**，文字進、文字出。程式帶工具去呼叫時，會收到 `tools_unsupported` 的錯誤：
「這個來源只能聊天，不支援工具（MCP）；要用工具請改用 API 金鑰或本地來源」，不會默默忽略。要用工具，請改用 API 金鑰或本地來源。

**`max_tokens`、`temperature` 對訂閱來源有用嗎？**
沒有。官方工具不支援，會被忽略，並在回覆的 warnings 裡留一句說明。

**金鑰到底放哪？**
放環境變數（步驟 8），設定檔只寫 `"api_key_env": "變數名"`。寫程式的人也可以用 `"secret": "<scheme>:<名稱>"` 接自己的保管箱，見 README。

**LLOP 的 key 放哪？**
放在 LLOP 自己的設定檔（`llmop client add-plain`／`enroll` 建的），ntu_easy_llm 的設定檔只寫 `"llop_profile": "名字"`。

**我想在 Python 程式裡用，不是用指令？**

```python
from ntu_easy_llm import load_profiles

llm = load_profiles("profiles.json").adapter("lmstudio")   # 換成你的 profile 名字
print(llm.chat([{"role": "user", "content": "你好"}]).text)
```

---

## 訂閱來源的使用聲明（一定要看）

- 訂閱來源用的是**你自己在這台電腦上登入的個人訂閱**，目錄上標「只給本人使用」。
- **不推薦拿個人訂閱服務別人**：不要轉售、共用，也不要把它放進給別人用的服務或備援鏈（例如經 LLOP 之類的通道分給別人）。
- **條款你自己看**：每個訂閱服務都有自己的使用條款，某種用法可不可以，請自己讀過、自己負責。
- 完整聲明：README 的〈[訂閱登入來源的使用聲明](../README.md#訂閱登入來源的使用聲明)〉。

---

## 全部做完了，接下來可以…

- 讀 README 的「0.6 預發布：設定檔＋各家 adapter＋命令列」：`probe --send`、串流、`list_models()`。
- 讀 README 的「備援路由（waterfall）」：在程式裡處理 `ask` 模式（`FallbackRequired`、`resume`）。
- 讀架構文件第 5 節：各家來源的差異與限制。
- 要分享自己電腦上的模型給朋友：看 LocalLLMOutsiderProvider（LLOP）的教學。
