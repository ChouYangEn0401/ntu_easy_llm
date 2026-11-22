# ntu_easy_llm 0.6 架構（介面凍結版）

- 日期：2026-11-23
- 狀態：介面已凍結（`0.6.0.dev0`）；HTTP adapter、waterfall 路由、LLOP 與訂閱 adapter 在後續的 0.6 預發布版補上
- 用途：給要在 ntu_easy_llm 上面寫 adapter、或拿它當 LLM 底層的程式看的說明。這裡列的公開名稱與簽名，之後只會**加**（有預設值的欄位、kw-only 參數），不會改或刪；真的要改，先發提案。

---

## 1. 這個套件負責什麼

ntu_easy_llm 是**乾淨的 LLM 底層**，只做兩件事：

1. **怎麼跟各家 LLM 講話**：一個 adapter 對一種來源，全部長得一樣（同樣的 `chat`／`list_models`／`probe`、同樣的錯誤分類）。
2. **把對話稍微整理好**（可選）：`ChatSession` 與儲存介面，不用也完全可以。

它**不做** agent 的東西：沒有 agent loop、沒有技能、沒有工具執行、沒有 UI。那些屬於上層應用（agent 外殼）；上層只透過這裡取得模型。

依賴方向是單向的：

```
上層應用（agent 外殼、UI）
        │  import
        ▼
ntu_easy_llm  ── 只用標準庫；選裝 extra 才有 PyYAML（[yaml]）、LLOP SDK（[llop]）
        │  （extra，延遲 import）
        ▼
各家 HTTP API ／ 本機或區網的模型伺服器 ／ 這台已登入的官方 CLI
```

- `import ntu_easy_llm`（以及 `types`、`errors`、`profiles`、`catalog`、`conversation`、`adapters`、`waterfall`）**不載入任何 SDK**（openai、anthropic、google-genai、llmop），有測試鎖住。
- 沒裝 `[llop]` 時完全不 import `llmop`；目錄只用 `importlib.util.find_spec("llmop")` 判斷有沒有裝（不會執行它）。
- 0.4／0.5 的舊 API（`ask*`、`ask_many`、`LLMSession`、`LLMAdapter` 家族、`KeyMaterial`、`agent.chat／probe`）名字與簽名一字不改。

## 2. 來源分類樹

每個來源有一個路徑，就是 `Profile.source`：

```
local/                       這台或區網的伺服器；沒有帳號級的驗證、不計費
  ollama  lmstudio  vllm  llamacpp  openai_compatible
remote/
  api_key/                   向服務商拿的金鑰
    openai  anthropic  gemini  openai_compatible
    llop                     LLOP（本地模型發布服務）；要安裝 ntu_easy_llm[llop]
  subscription/              這台已登入的官方 CLI
    claude  github_copilot  chatgpt
```

- 路徑格式固定：`local/<p>`、`remote/api_key/<p>`、`remote/subscription/<p>`（`<p>` 是小寫英數與底線）。
- `local/fake` 是測試用的來源（`FakeAdapter`），不出現在目錄裡。
- LLOP 放在 `remote/api_key`：對使用的一方來說它有金鑰、有額度、會被撤銷，錯誤碼也照遠端的方式分類。在跑模型那台機器上直接打它免金鑰的本機端點時，算 `local/openai_compatible`。

## 3. 模組與凍結的公開介面

| 模組 | 內容 |
|---|---|
| `ntu_easy_llm.types` | 中性訊息 `Message`、`ImagePart`；re-export `Tool`／`ToolCall`／`Usage`／`ChatReply`（與 `agent` 是同一批類別） |
| `ntu_easy_llm.errors` | `ErrorKind`、`classify()`、`ConfigError`、`AdapterNotFound`；錯誤類別上的 `.kind`／`.retry_after`／`.profile_id` |
| `ntu_easy_llm.profiles` | `Profile`、`ProfileSet`、`ChainSpec`、`SecretRef`、`load()` |
| `ntu_easy_llm.adapters` | `Adapter` Protocol、`Capabilities`、`ModelInfo`、`ProbeResult`、registry、entry point；`FakeAdapter` |
| `ntu_easy_llm.catalog` | 給 UI 畫「大類 → 認證 → 服務 → 連線欄位 → 細節」的樹 |
| `ntu_easy_llm.conversation` | 可選的 `ChatSession`、`SessionStore`、`MemorySessionStore`、`JsonlSessionStore` |
| `ntu_easy_llm.waterfall` | 備援鏈的**型別與 JSON 設定格式**（路由邏輯在 `ntu_easy_llm.router`） |
| `ntu_easy_llm.router` | 備援路由 `Router`：包住多個成員、對外跟單一 adapter 一樣（見 3.9） |

套件頂層另外延遲匯出：`Message`、`ImagePart`、`ErrorKind`、`ConfigError`、`Profile`、`ProfileSet`、`load_profiles`（＝`profiles.load`）、`get_adapter`、`ChatSession`、`WaterfallPolicy`。

### 3.1 types

```python
@dataclass(frozen=True)
class ImagePart:                       # repr 不印位元組
    mime: str                          # 必須是 image/*，自動轉小寫
    data: bytes
    size: int; sha256: str             # property
    def to_base64(self) -> str; def to_data_url(self) -> str; def describe(self) -> dict  # {"mime","size","sha256"}
    @classmethod from_base64(cls, mime, b64) -> ImagePart
    @classmethod from_data_url(cls, url) -> ImagePart      # 只收 data:<mime>;base64,...；不下載網址

@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = []    # 只有 assistant
    tool_call_id: str = ""; name: str = ""                 # tool 結果用
    images: list[ImagePart] = []       # 只有 user
    meta: dict = {}                    # 不送給模型（profile、model、usage…）
    def to_dict(self) -> dict          # 0.5 agent 的 dict 形狀

def as_message(m) -> Message           # Message／dict／有同名屬性的物件；list content（OpenAI、Anthropic 的 parts）拆成文字＋圖片，不認得的 part 丟 ValueError
def as_messages(ms) -> list[Message]
def normalize_finish(raw, *, text="", has_tool_calls=False) -> str   # "" | "stop" | "length" | "thinking" | "tool_calls"

# ChatReply（agent.py 的同一個類別）新增兩個有預設值的欄位：
#   finish: str = ""           正規化後的結束原因（"thinking"＝還沒寫出字就用完 token）
#   warnings: list[str] = []   不致命的提醒（例如 stop 不支援、已忽略）
```

### 3.2 errors

```python
class ErrorKind(StrEnum):
    QUOTA_EXHAUSTED  AUTH_INVALID  RATE_LIMITED  PERMISSION  OVERLOADED  CONTEXT_TOO_LONG
    MODEL_NOT_FOUND  TOOLS_UNSUPPORTED  BAD_REQUEST  TIMEOUT  CONNECTION  LINK_CHANGED
    TRUST  SERVER_ERROR  BAD_RESPONSE  UNKNOWN          # 值是小寫底線字串，str(kind) == 值

DEFAULT_TRIGGERS = {QUOTA_EXHAUSTED, AUTH_INVALID, RATE_LIMITED}
UNTRIGGERABLE   = {TRUST}

class ConfigError(ValueError)          # 設定問題；訊息只寫欄位路徑與變數名，絕不寫值
class AdapterNotFound(ConfigError)     # 沒有這個來源的 adapter，或這台不能用

def classify(provider="", *, status=None, body="", headers=None, exc=None,
             tools_sent=False) -> tuple[ErrorKind, float | None]
def register_classifier(provider: str, fn | None) -> None
      # fn(*, status, body, headers, exc, tools_sent) -> (ErrorKind, retry_after) | None；先跑，回 None 才走通用規則
def kind_of(exc: BaseException) -> ErrorKind
def parse_retry_after(headers, *, now=None) -> float | None     # retry-after-ms、Retry-After（秒數或 HTTP 日期）
def describe_kind(kind, locale="zh-TW") -> str                  # 「額度用完」「金鑰失效」…（zh-TW／en）
```

既有的錯誤類別**沒有換**（`agent.LLMError` 家族），只加了類別預設屬性與 kw-only 參數：

| 類別 | 新增 |
|---|---|
| `LLMError(*args, kind=None, retry_after=None, profile_id="")` | `.kind`（預設 `UNKNOWN`）、`.retry_after`（預設 `None`）、`.profile_id`（預設 `""`） |
| `LLMHTTPError(status, body="", provider="", *, headers=None, kind=None, retry_after=None, profile_id="")` | 建立時用 `classify` 自動判 `kind` 與 `retry_after`；`.headers`（dict） |
| `LLMConnectionError` | `kind` 預設 `CONNECTION`；`agent.chat` 遇到逾時會設成 `TIMEOUT` |
| `LLMResponseError` | `kind` 預設 `BAD_RESPONSE` |

`agent.chat` 的錯誤現在都帶 `kind`；`Retry-After` 會從回應標頭讀出來。

通用分類規則（各家的細節由之後的 adapter 用 `register_classifier` 補）：

| kind | 通用判定 | 預設觸發備援 |
|---|---|---|
| QUOTA_EXHAUSTED | 4xx 且內文有 `insufficient_quota`／`quota_exceeded`／`key_exhausted`／`credit balance`…；402 | 是 |
| AUTH_INVALID | 401；內文有 `invalid_api_key`／`API_KEY_INVALID`／`key_expired`… | 是 |
| RATE_LIMITED | 其他 429（`retry_after` 從標頭來） | 重試用完仍失敗才觸發 |
| PERMISSION | 403 | 否 |
| TIMEOUT | 408、504、524、逾時例外 | 否 |
| CONTEXT_TOO_LONG | 413；內文有 `context_length_exceeded`／`maximum context length`／`Context size has been exceeded`… | 否 |
| MODEL_NOT_FOUND | 內文有 `model_not_found`…；404 且內文提到 model | 否 |
| TOOLS_UNSUPPORTED | `tools_sent=True` 且 400／422 且內文提到 tool／function | 否 |
| BAD_REQUEST | 其他 4xx | 否 |
| OVERLOADED | 503、529 | 否 |
| CONNECTION | 520–523、530；連線類例外 | 否 |
| SERVER_ERROR | 其他 5xx | 否 |
| BAD_RESPONSE | 200 但格式不對；解析失敗 | 否 |
| LINK_CHANGED | 由該來源的 adapter 自己判（通用規則不判） | 否 |
| TRUST | 由加密來源的 adapter 自己判 | **永遠不能**設成觸發條件 |

### 3.3 profiles

```python
@dataclass(frozen=True)
class SecretRef:                       # "env:NAME" 或 "<scheme>:<ref>"（例：vault:main）
    ref: str
    scheme: str; name: str             # property
    @classmethod env(cls, name) -> SecretRef

@dataclass(frozen=True)
class Profile:
    id: str
    source: str                        # 分類樹路徑
    model: str = ""
    base_url: str | None = None        # 結尾的 / 會去掉；不准帶帳密或金鑰
    secret: SecretRef | None = None
    options: Mapping[str, Any] = {}    # 唯讀；max_tokens、temperature、timeout、context_size、tools、effort、transport、llop_profile…
    label: str = ""
    base_url_env: str = ""             # base_url 從哪個環境變數來（錯誤訊息用）
    family: str; auth: str; provider: str          # property："local"/"remote"；"api_key"/"subscription"/""；最後一段
    def option(self, name, default=None); def replace(self, **changes) -> Profile; def to_dict(self) -> dict

@dataclass(frozen=True)
class ChainSpec:
    id: str; members: tuple[str, ...]; waterfall: WaterfallPolicy = WaterfallPolicy(); label: str = ""

@dataclass(frozen=True)
class ProfileSet:
    profiles: Mapping[str, Profile]; chains: Mapping[str, ChainSpec]; default: str = ""
    def names(self) -> list[str]
    def get(self, profile_id) -> Profile                     # 沒有 → ConfigError
    def chain(self, chain_id) -> ChainSpec
    def resolve(self, name=None) -> tuple[list[Profile], WaterfallPolicy]   # 單一 profile → ([p], 關閉的 policy)
    def secret_resolver(self) -> Callable[[SecretRef | str], str]
    def adapter(self, name=None, *, http=None) -> Adapter    # 只限單一 profile；chain 交給 router
    def to_dict(self) -> dict                                # 可以再丟回 load()

def load(source: str | PathLike | Mapping, *, environ=None, resolve_secret=None) -> ProfileSet
def make_secret_resolver(*, environ=None, resolve_secret=None) -> Callable[[SecretRef | str], str]
def looks_like_secret(value: str) -> bool
```

`load()` 吃 dict、`.json`／`.yaml`／`.yml` 檔案路徑、或 JSON 字串；YAML 要裝 `ntu_easy_llm[yaml]`（沒裝會給安裝提示）。

**金鑰規則**：設定（檔案或 dict 都一樣）裡**只能寫金鑰在哪裡**，不能寫金鑰本身：

- `api_key_env: <環境變數名>` 或 `secret: "<scheme>:<名稱>"`；`env:` 內建，其他 scheme 交給呼叫端的 `resolve_secret(ref字串)`。
- 欄位名稱是 `api_key`／`token`／`password`／`authorization`… → `ConfigError`。
- 任何值看起來像金鑰（`sk-…`、Google、GitHub、Slack、AWS、私鑰 PEM、`Bearer …`）→ `ConfigError`。
- 網址帶帳密或金鑰 → `ConfigError`。
- 錯誤訊息只寫欄位路徑，不印出值。
- 金鑰在 adapter 真的要用時才讀（`resolve_secret`），不在 `load()` 時讀。

已經拿到金鑰的程式，傳 `resolve_secret=` 進來就好，不要寫進設定。

### 3.4 adapters

```python
@dataclass(frozen=True)
class Capabilities: tools: bool|None = None; vision: bool|None = None; stop: bool = False; stream: bool = False; usage: bool = False
@dataclass(frozen=True)
class ModelInfo: id: str; label: str = ""; context: int|None = None; loaded: bool|None = None
                 vision: bool|None = None; tools: bool|None = None; kind: str = "chat"    # chat|embedding|unknown
@dataclass(frozen=True)
class ProbeResult: ok: bool; reason: str = ""; kind: ErrorKind|None = None
                   models: tuple[ModelInfo, ...] = (); latency_s: float|None = None

@runtime_checkable
class Adapter(Protocol):
    profile: Profile
    def capabilities(self) -> Capabilities
    def chat(self, messages, tools=None, *, max_tokens=None, temperature=None, stop=(),
             timeout=None, on_delta=None) -> ChatReply
    def list_models(self, *, timeout=15.0) -> list[ModelInfo]
    def probe(self, *, send=False, timeout=30.0) -> ProbeResult

ENTRY_POINT_GROUP = "ntu_easy_llm.adapters"
def register_adapter(source, factory | "module:attr", *, replace=False) -> None
def unregister_adapter(source) -> None
def get_adapter(profile, *, http=None, resolve_secret=None, environ=None) -> Adapter   # 沒有 → AdapterNotFound
def adapter_status(source) -> tuple[bool, str]       # 這台能不能用、為什麼
def registered_sources() -> list[str]
# factory(profile, *, http=None, resolve_secret) -> Adapter；可帶屬性 check: () -> (bool, reason)
```

每個 adapter 都守的約定：

- `chat` 收 `Message` 或 0.5 的 dict；回 `ChatReply`，`finish` 要填。
- 所有失敗都是 `LLMError`，`kind` 有值、`profile_id` 是自己 profile 的 id。
- 不支援的功能：`tools` 在 `capabilities().tools is False` 時丟 `TOOLS_UNSUPPORTED`；圖片在 `vision is False` 時丟 `BAD_REQUEST`；`stop` 在不支援時忽略並在 `warnings` 記一行。`None`＝不知道，照送、讓伺服器決定。
- `probe(send=False)` 只讀（列模型、確認連得到），不載入模型、不花 token；`send=True` 才送一句。`probe` 不因供應商或網路問題丟例外。
- 金鑰透過 factory 拿到的 `resolve_secret` 在用的時候才取，不寫進紀錄或錯誤訊息。
- adapter 模組被 import 時不載入任何 SDK。

第三方套件用 entry point 註冊：名稱是來源路徑，用 `.` 或 `/` 分隔（例：`remote.api_key.example`），值是 factory。

`FakeAdapter`（`local/fake`）給所有下游的測試用：

```python
FakeAdapter(script=(), *, profile=None, default=None, capabilities=None, models=("fake-1",),
            probe_result=None, delay_s=0.0)
# 劇本每一步：文字、ChatReply、ErrorKind、例外物件、callable(messages, tools, **kw)、
#   {"text", "tool_calls", "usage", "finish", "warnings"}、{"error": "<kind>", "retry_after", "status", "message"}
# .calls 記錄每次呼叫；.push(*steps)；.remaining；劇本用完而沒有 default → FakeScriptExhausted（AssertionError）
fake_error(kind, *, message="", retry_after=None, status=None, profile_id="") -> LLMError   # 對的類別、kind 有值
```

設定檔也能驅動它：

```json
{"profiles": {"t": {"source": "local/fake", "model": "fake-1",
                     "options": {"script": ["第一句", {"error": "quota_exhausted"}], "default": "之後都回這句"}}}}
```

### 3.5 catalog

```python
@dataclass(frozen=True)
class FieldSpec: name: str; kind: str; required: bool = False; secret: bool = False
                 default: Any = None; choices: tuple = (); label: str = ""; help: str = ""
FIELD_KINDS = ("text", "url", "secret", "int", "float", "bool", "choice", "model", "llop_profile")

@dataclass(frozen=True)
class SourceNode: path: str; label: str; children: tuple = (); fields: tuple = (); detail_fields: tuple = ()
                  available: bool = True; unavailable_reason: str = ""; description: str = ""; extra: str = ""
    is_leaf; walk(); find(path); leaves(); to_dict()

def catalog(*, locale="zh-TW") -> SourceNode       # 根節點 path == ""；每次重算可用性
def find(path, *, locale="zh-TW") -> SourceNode | None
def leaves(*, locale="zh-TW") -> list[SourceNode]
def list_llop_profiles() -> list[str]              # 沒裝 [llop] → []
```

- `fields` 是連線那一步要問的（`base_url`、`model`、`secret`、`llop_profile`），`detail_fields` 是細節（放進 `options`：`max_tokens`、`temperature`、`timeout`、本地的 `context_size`／`tools`、訂閱的 `effort`、LLOP 的 `transport`）。
- `secret` 類欄位：UI 收下金鑰後由**呼叫端**存進保管箱或環境變數，profile 只寫引用。
- `available`：有 adapter、而且它的 `check` 說這台能用才是 True；不能用時 `unavailable_reason` 寫原因（例如「要先安裝 ntu_easy_llm[llop]」「這個版本還沒有這個來源的 adapter」）。HTTP adapter（`local/*` 全部、`remote/api_key/{openai, anthropic, gemini, openai_compatible}`）只用標準函式庫，一律可用（見第 5 節）；LLOP、訂閱 adapter 加進來後也會自動變成可用。

### 3.6 conversation（可選）

```python
class ChatSession:
    def __init__(self, id=None, messages=(), *, meta=None, created_at=None)
    id: str; messages: list[Message]; events: list[dict]; meta: dict; created_at: float; skipped_records: int
    store -> SessionStore | None                      # property
    def bind(self, store, *, write=True) -> ChatSession
    def add(self, m) -> Message; def extend(self, ms) -> None; def add_event(self, event) -> None
    def window(self, budget_tokens=None, *, estimate=default_estimate) -> list[Message]
    def to_records(self) -> Iterator[dict]; @classmethod from_records(cls, records, *, session_id=None)

class SessionStore(Protocol):
    def load(self, session_id) -> ChatSession | None
    def append(self, session_id, record: dict) -> None
    def save(self, session) -> None                   # 整份重寫
    def delete(self, session_id) -> None
    def list(self, limit=50) -> list[SessionInfo]
@dataclass(frozen=True)
class SessionInfo: id: str; updated_at: float; message_count: int | None = None; meta: Mapping = {}
class MemorySessionStore; class JsonlSessionStore(root=None)   # 預設 ~/.ntu_easy_llm/chats/<id>.jsonl
def default_estimate(m: Message) -> int                         # 約 3 字元 1 token
```

- 綁了 store 的 session，每次 `add`／`add_event` 立刻追加寫入；`JsonlSessionStore.save` 用暫存檔＋`os.replace` 整份換掉。
- `window()`：開頭的 system 一定留；從最舊的**整輪**丟（一輪從 user 開始，工具呼叫和它的結果不會被拆開）；不做摘要；最新一輪本身就超過預算時照樣回傳。
- session id 只准英數與 `_ . -`（最長 128），擋掉路徑穿越。
- 呼叫端可以自己實作 `SessionStore`（例如接自己的資料庫或原子寫入工具）。

JSONL 每行一筆：

```json
{"v": 1, "type": "session", "id": "demo-1", "created_at": 1700000000.0, "meta": {"title": "範例"}}
{"v": 1, "type": "message", "role": "user", "content": "你好", "tool_calls": [], "tool_call_id": "", "name": "",
 "images": [{"mime": "image/png", "size": 1234, "sha256": "…"}], "meta": {}}
{"v": 1, "type": "event", "event": "switched", "profile_id": "main", "to_id": "backup", "reason": "quota_exhausted", "at": 1700000001.0}
```

- 圖片不存位元組，只存型別、大小、雜湊；讀回來時放在 `Message.meta["images"]`。
- 不認得的 `type` 直接略過（新版寫的檔，舊版也讀得動）。
- 寫到一半當掉留下的殘行，讀取時略過並計入 `skipped_records`；之後的追加會從新的一行開始。

### 3.7 waterfall（型別與設定格式）

```python
MODES = ("off", "auto", "ask")
@dataclass(frozen=True)
class WaterfallPolicy:
    mode: str = "off"                                   # 沒設定＝off
    triggers: frozenset[ErrorKind] = DEFAULT_TRIGGERS   # 不能含 trust
    retry_429: int = 2; max_retry_wait_s: float = 30.0
    cooldown_s: float = 900.0                           # 額度／限流的成員在沒有 Retry-After 時跳過多久
    ask_ttl_s: float = 600.0
    enabled -> bool
    @classmethod from_dict(cls, d | None) -> WaterfallPolicy    # 不認得的鍵 → ConfigError；_ 開頭的鍵是註解
    def to_dict(self) -> dict

# 事件（frozen dataclass；to_record() 給 session 記錄；event_from_record() 讀回）
Event(profile_id="", at=time.time())
AttemptStarted(attempt, model) · Retrying(attempt, wait_s, reason) · Switched(to_id, reason)
Skipped(until, reason) · FallbackNeeded(reason, candidates, pending_id) · Succeeded(switched)
Failed(reason, message) · Exhausted(reasons=((profile_id, kind), ...))
EVENT_TYPES: dict[str, type[Event]]

@dataclass
class ChatResult(ChatReply):   profile_id: str = ""; model: str = ""; switched: bool = False
                               notice: str = ""; events: list[Event] = []
    @classmethod from_reply(cls, reply, **extra) -> ChatResult
@dataclass(frozen=True)
class PendingFallback:         profile_id: str; reason: ErrorKind; candidates: tuple[str, ...]
                               message: str = ""; chain_id: str = ""; session_id: str = ""
                               id: str = <uuid>; created_at: float = <now>; expires_at: float = 0.0
                               error: BaseException | None = None      # 不進 repr、不進 to_dict
    def expired(self, now=None) -> bool; def to_dict(self) -> dict
@dataclass(frozen=True)
class MemberStatus:            profile_id: str; cooling_until: float | None = None
                               last_error: ErrorKind | None = None; last_message: str = ""
class FallbackRequired(LLMError):   .pending；kind＝失敗原因              # ask 模式丟出
class WaterfallExhausted(LLMError): .errors＝((profile_id, 例外), ...)；kind＝最後一個錯誤的
def switch_notice(to_id, reason, locale="zh-TW", *, to_label="") -> str   # 「已改用 X（原因：額度用完）」
```

設定格式（放在 profiles 設定的 `chains` 底下；JSON 與 YAML 同一份 schema）：

```json
{
  "version": 1,
  "default": "chat",
  "profiles": {
    "primary": {"source": "remote/api_key/openai", "api_key_env": "EXAMPLE_PRIMARY_KEY", "model": "example-model"},
    "backup":  {"source": "remote/api_key/anthropic", "secret": "vault:backup", "model": "example-model"},
    "home":    {"source": "local/ollama", "base_url": "http://127.0.0.1:11434", "model": "example-model"}
  },
  "chains": {
    "chat": {
      "members": ["primary", "backup", "home"],
      "waterfall": {
        "mode": "auto",
        "triggers": ["quota_exhausted", "auth_invalid", "rate_limited"],
        "retry_429": 2,
        "max_retry_wait_s": 30,
        "cooldown_s": 900,
        "ask_ttl_s": 600
      }
    },
    "single": ["primary"]
  }
}
```

- 沒有 `waterfall` 鍵（或 `"mode": "off"`）＝不啟動：只用第一個成員，跟單獨選那一個完全一樣。
- `auto`：觸發時換下一個沒在冷卻的成員，回覆的 `notice` 說明換了誰、為什麼。
- `ask`：觸發時丟 `FallbackRequired(pending)`，由呼叫端（例如 UI 的按鈕）決定要不要換、換哪一個。
- 預設觸發只有三種：額度用完、金鑰失效、429 重試後仍失敗；一般錯誤不換。`trust`（加密或金鑰核對失敗）永遠不能設成觸發條件。
- 鏈的順序由使用者的設定決定；函式庫不預設任何供應商的先後。
- 冷卻狀態只放記憶體，程式重開就清掉。

### 3.8 之後的預發布版會加的（介面已預留，不在這一版）

| 項目 | 會放在 | 依賴本版的什麼 |
|---|---|---|
| OpenAI 風格（openai、gemini、openai_compatible、lmstudio、vllm、llamacpp）、Anthropic、Ollama 的 HTTP adapter；各家的 `register_classifier` 規則；命令列 `catalog`／`models`／`probe`／`chat` | `adapters/` | `Adapter`、`Capabilities`、`ModelInfo`、`ProbeResult`、`classify`、`register_adapter` |
| 備援路由：一個包住多個成員的類別，對外看起來跟單一 adapter 一樣（`chat` 回 `ChatResult`，另有 `resume(pending, use=...)`、`status()`），以及 `ProfileSet` 取得它的方法 | `ntu_easy_llm.router` | `waterfall` 的所有型別、`ProfileSet.resolve()` |
| LLOP adapter（extra `[llop]`，延遲 import LLOP SDK）與訂閱 adapter（claude、github_copilot、chatgpt；只回文字） | `adapters/` | `Adapter`、factory 的 `check`、`ErrorKind.LINK_CHANGED`／`TRUST` |

### 3.9 備援路由、LLOP adapter、訂閱 adapter（0.6 預發布加入）

**`ntu_easy_llm.router`**

```python
class Router:                                   # 別名 LLM
    def __init__(self, members, *, waterfall=None, profiles=None, chain_id="", http=None,
                 resolve_secret=None, environ=None, on_event=None, locale="zh-TW",
                 sleep=time.sleep, clock=time.time)
        # members：adapter、Profile 或 profile id（id 要給 profiles=）；Profile 在第一次用到時才建 adapter
        # waterfall：WaterfallPolicy、它的 JSON dict 或 None（＝off）
    @classmethod from_profiles(cls, profiles, name=None, **kw) -> Router
    profile: Profile                            # 第一個成員的（property）
    members: tuple[str, ...]                    # property
    def capabilities(self) / list_models(*, timeout) / probe(*, send, timeout)   # 都是第一個成員的
    def chat(self, messages | ChatSession, tools=None, *, max_tokens=None, temperature=None, stop=(),
             timeout=None, on_delta=None, window_tokens=None) -> ChatResult
    def resume(self, pending, *, use=None, messages=None) -> ChatResult
    def status(self) -> list[MemberStatus]
    def reset(self, profile_id=None) -> None    # 清掉冷卻
    def pending(self) -> list[PendingFallback]  # 還在等決定的 ask
    def adapter(self, profile_id) -> Adapter

ProfileSet.router(name=None, *, http=None, **kw) -> Router      # chain 或單一 profile
```

狀態機（每一次 `chat`）：

| 情況 | 行為 |
|---|---|
| `off`（沒設定、單一 profile） | 只叫第一個成員；錯誤原樣丟出；不重試、不冷卻 |
| `rate_limited` | 同一個成員照 `Retry-After` 重試 `retry_429` 次（沒給就 1、2、4… 秒，最多 `max_retry_wait_s`）；`Retry-After` 超過上限就不等、直接算重試用完 |
| 錯誤的 kind 不在 `triggers` | 丟出原錯誤（事件 `Failed`）。`trust` 永遠在這一類；`link_changed` 也是（不重試） |
| 在 `triggers`，`auto` | 這個成員冷卻（`Retry-After`，沒有就 `cooldown_s`；金鑰失效一律 `cooldown_s`）；換下一個沒在冷卻、而且做得到這次呼叫的成員（帶 `tools` 時跳過 `tools=False` 的、有圖片時跳過 `vision=False` 的、這台裝不起來的）；事件 `Switched`；結果 `switched=True`、`notice`＝「已改用 X（原因：…）」。沒有下一個 → `WaterfallExhausted` |
| 在 `triggers`，`ask` | 丟 `FallbackRequired(pending)`（候選、原因、到期時間）；`resume(pending, use="<id>")` 從那個成員繼續；`use=None` 或過期 → 丟原本的錯 |
| 一開始第一個成員在冷卻 | `auto`：跳過（事件 `Skipped`）、結果標成已換；`ask`：先問，不默默換；沒有別的候選就照樣試第一個 |

- 冷卻只在記憶體；成功一次就清掉那個成員的冷卻。
- 給 `ChatSession` 時：送 `session.window(window_tokens)`，所有成員看到同一段對話；換成員時 `session.add_event(Switched)`；回覆以 assistant 訊息加進 session（`meta` 有 `profile_id`、`model`、`usage`）。
- `on_event` 收到每一個事件（它自己丟例外不影響呼叫）；同一批事件也在 `ChatResult.events`。
- 函式庫沒有任何供應商的預設順序；不做「隱私降級保護」。
- `import ntu_easy_llm.router` 跟其他 0.6 模組一樣不載入任何 SDK（有測試鎖住）。

設定範例（profile id、網址、模型都是假的；順序由使用者決定）：

```json
{
  "version": 1,
  "default": "chat",
  "profiles": {
    "first":  {"source": "remote/api_key/llop", "llop_profile": "default", "model": "any"},
    "second": {"source": "remote/api_key/openai_compatible", "base_url": "https://llm.example.test/v1",
               "api_key_env": "EXAMPLE_SECOND_KEY", "model": "example-model"},
    "third":  {"source": "local/ollama", "base_url": "http://127.0.0.1:11434", "model": "example-model"}
  },
  "chains": {
    "chat": {"members": ["first", "second", "third"],
             "waterfall": {"mode": "auto", "triggers": ["quota_exhausted", "auth_invalid", "rate_limited"],
                           "retry_429": 2, "max_retry_wait_s": 30, "cooldown_s": 900}},
    "careful": {"members": ["first", "third"], "waterfall": {"mode": "ask", "ask_ttl_s": 600}}
  }
}
```

**`adapters/llop.py`（`remote/api_key/llop`，extra `[llop]`）**

- 延遲 import `llmop.sdk`；`import ntu_easy_llm`、目錄、`check()` 都不載入它（`check()` 只用 `find_spec("llmop")`，沒裝回「要先安裝 ntu_easy_llm[llop]」）。
- `llop_profile`（預設 `default`）＝LLOP SDK 在這台的設定檔名；API key、簽章私鑰、釘選都在 SDK 那邊，EasyLLM 的設定不放。
- `model` 空或 `any`＝伺服器決定；`transport`（`auto`／`e2e`／`plain`）、`max_tokens`、`temperature`、`timeout`（當成 SDK 的 `deadline`）照傳。
- `SdkError.kind_hint` → `ErrorKind`：`quota`→QUOTA_EXHAUSTED、`auth`→AUTH_INVALID、`rate`→RATE_LIMITED、`link_changed`→LINK_CHANGED、`offline`→CONNECTION、`timeout`→TIMEOUT、`trust`→TRUST、`bad_request`→BAD_REQUEST、`server`→SERVER_ERROR、`unknown`→UNKNOWN；後三種再用通用規則細分（`queue_full`／`gateway_busy`→OVERLOADED、context 太長、找不到模型、工具被拒）。`retry_after` 照帶。
- `link_changed` 直接丟出（不重試、不自己找新連結），訊息附 `llmop client set-link --profile <名稱> <新連結>`。
- `capabilities()`：`tools`／`vision` 是 `None`（看伺服器的模型）、`stop=True`、沒有串流；`list_models()` 用 `Client.models()`；使用者訊息的圖片轉成 `image_url` data URL。
- 安裝：LLOP 還沒上 PyPI，pyproject 先寫 `localllm-outsider-provider[sdk]`；目前從 LLOP 的 GitHub release wheel 或 `pip install -e <LLOP 資料夾>[sdk]` 安裝。沒裝 extra 的人不受影響。

**`adapters/subscription.py`（`remote/subscription/claude`／`github_copilot`／`chatgpt`）**

| 來源 | 套件（import） | pip 名稱 |
|---|---|---|
| `remote/subscription/claude` | `claude_subscription` | `claude-subscription` |
| `remote/subscription/github_copilot` | `github_copilot_login` | `github_copilot_login` |
| `remote/subscription/chatgpt` | `chatgpt_login` | `chatgpt_login` |

- 三個登入套件同一組介面（`INTERFACE_VERSION == "1"`）：`ask(訊息清單, model=, timeout=)`、`LoginError.kind`、`CAPABILITIES`；套件在第一次用到時才 import。
- **只回文字**：帶 `tools` → `TOOLS_UNSUPPORTED`（不默默忽略）；圖片、工具結果訊息、最後一則不是 user → `BAD_REQUEST`；`stop`、`max_tokens`、`temperature`、`effort` 這幾個 CLI 不支援，忽略並在 `warnings` 記一行。
- `LoginError.kind` → `ErrorKind`：`quota_exhausted`→QUOTA_EXHAUSTED（帶 `retry_after`）、`auth_invalid`→AUTH_INVALID、`timeout`→TIMEOUT、`not_installed`→CONNECTION、`bad_request`／`unsupported`→BAD_REQUEST（提示太長→CONTEXT_TOO_LONG）、其他→UNKNOWN。
- `check()`：沒裝套件 → 「要先安裝 <pip 名稱> 套件」。有沒有登入要真的呼叫才知道：`probe(send=False)` 只確認找得到官方 CLI，`probe(send=True)` 送一句；沒登入時呼叫會得到 `AUTH_INVALID`。
- 目錄上這三個來源的說明標「只給本人使用」（用的是這台電腦上個人的訂閱）；函式庫不實作擋人的邏輯，要不要開給別人由上層應用決定。

## 4. 相容與版本

- 版本 `0.6.0.dev0`（semver minor：只加東西）。
- 必裝相依不變（openai、google-genai、anthropic 留給舊的 `ask*`）。新增 extra：`yaml`（PyYAML）。
- 授權維持 MIT；使用、修改請保留 `LICENSE` 裡的版權聲明。

## 5. 各 adapter 的差異與限制（HTTP adapter）

> 標【推測】的是照公開文件或社群回報寫的，還沒有對真的服務實測；實測不一樣時改 `adapters/classifiers.py` 或對應的 adapter，並更新這裡。

### 5.1 共同行為

| 項目 | 行為 |
|---|---|
| 模組 | `adapters/openai_style.py`（openai、gemini、openai_compatible、lmstudio、vllm、llamacpp）、`adapters/anthropic.py`、`adapters/ollama.py`；共用 `adapters/_http.py`。類別本身就是 factory，登記在 `base._BUILTIN`（第一次用到才 import） |
| HTTP | 標準函式庫 `urllib`；不載入任何供應商 SDK。`http=` 可以注入任何有 `request(method, url, json=, headers=, timeout=, stream=)` 的物件（`requests.Session` 直接可用），或 0.5 那種只有 `post()` 的假 session |
| 訊息轉換 | 共用 `agent.to_openai_messages`／`agent.to_anthropic_messages`（0.6 起會帶 `ImagePart`：OpenAI 風格是 `image_url` data URL，Anthropic 是 base64 `image` 區塊）；回覆解析也共用 `agent` 的同一份 |
| proxy | 本機與區網（loopback、私有網段、link-local、100.64/10、`localhost`、`*.local`／`*.lan`／`*.internal`／`*.home.arpa`、沒有點的主機名）與所有 `local/*` 來源**直連**（`ProxyHandler({})`）；遠端照系統設定。`options.proxy`：`auto`（預設）／`none`／`system` |
| 逾時 | 呼叫的 `timeout` → `options.timeout` → 預設（遠端 90 秒、本機 300 秒）。urllib 的逾時是「每一次讀寫」：串流時只要還在吐字就不會被砍 |
| 金鑰 | 每次送出前才用 `resolve_secret` 取；不存在物件上、不進錯誤訊息。環境變數沒設＝`LLMError(kind=AUTH_INVALID)`，訊息只寫變數名稱 |
| 參數 | `max_tokens`、`temperature`：呼叫的參數優先，其次 `options`。`options.extra_body`（物件）原樣併進每一個請求內文（給各家特有的欄位）。`options.tools`／`options.vision` 設 `false` 會在送出前就擋（`TOOLS_UNSUPPORTED`／`BAD_REQUEST`），設 `true` 是保證有 |
| 串流 | 給 `on_delta` 才串流（OpenAI 風格與 Anthropic 是 SSE、Ollama 原生是 NDJSON）。伺服器不理 `stream` 直接回整包 JSON 時照樣能用（`on_delta` 收到整段一次）。串流中途的錯誤事件照一般錯誤分類 |
| 錯誤 | 非 2xx → `LLMHTTPError`（`kind` 用 `classify(<provider>, tools_sent=…)` 判、`retry_after` 讀 `Retry-After`／`retry-after-ms`）；連不上 → `LLMConnectionError`（`CONNECTION` 或 `TIMEOUT`）；200 但格式不對 → `LLMResponseError`；訊息或工具格式不對 → `LLMError(BAD_REQUEST)`。全部帶 `profile_id`。adapter **不重試**，429 重試交給之後的 router |
| `probe(send=False)` | 只送 GET（列模型），不載入模型、不花 token。列模型的端點回 404＝通了但不能列（`ok=True`、`reason` 說明）。設定的模型不在清單裡：具名的來源回 `MODEL_NOT_FOUND`；`openai_compatible`（本機與遠端）只在 `reason` 註明、仍算通（閘道常用別名） |
| `probe(send=True)` | 先列模型，再送一句「Reply with one word: ok」（`max_tokens=256`，推理模型要先想）；空回覆＝`BAD_RESPONSE`（用光 token 在想時 reason 會說） |
| `finish` | 用 `normalize_finish` 填；`length` 但一個字都沒寫＝`thinking` |

### 5.2 能力表

`None`＝看模型／伺服器，照送、讓伺服器決定。

| 來源 | 端點 | tools | vision | stop | stream | usage | 列模型 | `loaded` |
|---|---|---|---|---|---|---|---|---|
| `remote/api_key/openai` | `/v1/chat/completions` | True | None | True | True | True | `/v1/models` | — |
| `remote/api_key/anthropic` | `/v1/messages` | True | None | True | True | True | `/v1/models` | — |
| `remote/api_key/gemini` | OpenAI 相容端點 `/chat/completions` | True | None | True | True | True | `/models`（去掉 `models/` 前綴） | — |
| `remote/api_key/openai_compatible` | `<base_url>/chat/completions` | None | None | True | True | False | `/models` | — |
| `local/lmstudio` | `/v1/chat/completions` | None | None | True | True | True | `/api/v1/models` → `/api/v0/models` → `/v1/models` | 有（前兩個） |
| `local/ollama` | 原生 `/api/chat`（或 `/v1`） | None | None | True | True | True | `/api/tags`＋`/api/ps` | 有 |
| `local/vllm` | `/v1/chat/completions` | None | None | True | True | True | `/v1/models`（`max_model_len`→context） | 全部 True |
| `local/llamacpp` | `/v1/chat/completions` | None | None | True | True | True | `/v1/models` | 全部 True |
| `local/openai_compatible` | `<base_url>/chat/completions` | None | None | True | True | False | `/models` | — |

本機來源的 tools／vision 可用 `options.tools`／`options.vision` 改成 True／False。

### 5.3 各家的差異

- **openai**：長度上限送 `max_completion_tokens`（新模型不收 `max_tokens`）；串流加 `stream_options.include_usage` 拿用量。推理模型可能不收 `stop`、`temperature`，會回 400 → `BAD_REQUEST`【推測】：這類模型不要設這兩個。不支援 Responses API。
- **anthropic**：`system` 拆到最上層；連續的工具結果併成一則 user；`stop`→`stop_sequences`；`max_tokens` 必填，沒給時送 4096。extended thinking、prompt caching 等請用 `options.extra_body` 自己帶。
- **gemini**：走 Google 的 OpenAI 相容端點，同一把 Gemini API key；錯誤內文是 Google 的格式（可能包在一個元素的陣列裡）。列模型的 id 有 `models/` 前綴【推測】，這裡會去掉。不支援原生 API 才有的功能。串流不送 `stream_options`【推測：相容端點是否支援未實測】。
- **openai_compatible（遠端）**：`base_url` 必填，金鑰可以不給（有些閘道不用）。
- **lmstudio**：聊天走 `/v1`。`context_size` 是載入模型時決定的，聊天請求改不了（不送）。列模型先試 LM Studio 自己的 REST API（`/api/v1/models`，再 `/api/v0/models`）才知道哪些已載入、context、vision、tool use；舊版沒有這些端點時退回 `/v1/models`（沒有 `loaded`）【推測：欄位名照公開文件】。沒開 JIT 載入時，對沒載入的模型聊天會失敗（`MODEL_NOT_FOUND`【推測：錯誤字串未實測】）。不做載入、卸載。
- **ollama**：預設原生 `/api/chat`：`context_size`→`num_ctx`、`max_tokens`→`num_predict`、`stop`、`temperature` 放在 `options`；`options.think`（true／false）有設才送（舊版或不會思考的模型可能不收）。圖片是 base64 字串陣列、工具參數是物件、工具結果帶 `tool_name`；回覆沒有 tool call id，給 `call_<n>`。`base_url` 結尾是 `/v1` 或 `options.api: "openai"` 時改走 `/v1`；原生端點回 404 且內文沒提到 model（前面有只開 `/v1` 的代理）時自動改走 `/v1` 一次。
- **vllm**：工具呼叫要伺服器開 `--enable-auto-tool-choice` 與 `--tool-call-parser`，沒開時回 400 → `TOOLS_UNSUPPORTED`。串流送 `stream_options.include_usage`。
- **llamacpp**：工具呼叫要伺服器開 `--jinja`，沒開時的錯誤 → `TOOLS_UNSUPPORTED`（狀態碼可能是 500【推測】）。
- **openai_compatible（本機）**：`base_url` 必填；其他同 OpenAI 風格。

### 5.4 各家的錯誤分類（`adapters/classifiers.py`，先跑、回 `None` 才走第 3.2 節的通用規則）

| provider（`classify` 的第一個參數） | 規則 |
|---|---|
| `openai` | `insufficient_quota`（code 或 type）→ QUOTA；其他 429 → RATE；401 或 `invalid_api_key` → AUTH；403 → PERMISSION；`model_not_found` → MODEL；`context_length_exceeded` → CONTEXT；`invalid_function_parameters`／「Invalid schema for function」→ BAD_REQUEST（工具格式寫錯是呼叫端的錯，不是不支援工具）【推測】 |
| `anthropic` | 529 → OVERLOADED；內文「credit balance」→ QUOTA【推測：確切字串待實測】；`error.type`：`authentication_error`→AUTH、`permission_error`→PERMISSION、`rate_limit_error`→RATE、`overloaded_error`→OVERLOADED、`request_too_large`→CONTEXT、`not_found_error`（提到 model）→MODEL、`api_error`→SERVER_ERROR、`billing_error`→QUOTA【推測】、`timeout_error`→TIMEOUT【推測】；「prompt is too long」→ CONTEXT |
| `gemini` | `API_KEY_INVALID`／「API key not valid」／「API key expired」／401 → AUTH；429 或 `RESOURCE_EXHAUSTED`：內文有每日配額（`PerDay`、per day）或 billing → QUOTA，否則 → RATE；等待秒數取 `retryDelay`（`"37s"`）【推測：每次 429 都寫「exceeded your current quota」，要靠 quotaId 分】；403／`PERMISSION_DENIED`、「location is not supported」【推測】→ PERMISSION；404 提到 model → MODEL；「input token count … exceeds」→ CONTEXT【推測】 |
| `lmstudio`、`ollama`、`vllm`、`llamacpp`、`openai_compatible` | context 超過：「Context size has been exceeded」（LM Studio）、「greater than the context length」（LM Studio【推測】）、「exceeds the available context size」／`exceed_context_size_error`（llama.cpp【推測】）、「maximum context length」（vLLM）、「input length exceeds the context length」（Ollama【推測】）→ CONTEXT（5xx 也算）；模型不存在：「try pulling it first」（Ollama）、「No models loaded」（LM Studio【推測】）、「invalid model identifier」（LM Studio【推測】）、「does not exist」／`model_not_found` 且提到 model → MODEL；帶 tools 且內文有 `--jinja` → TOOLS_UNSUPPORTED。其他照通用規則（帶 tools 時 400／422 提到 tool／function → TOOLS_UNSUPPORTED） |

這些規則在第一次呼叫 `classify` 或 `register_classifier` 時才載入；呼叫端自己 `register_classifier("openai", ...)` 會蓋過內建的。0.5 的 `agent.chat` 丟的 `LLMHTTPError` 也用同一套。

### 5.5 不做的事

- 不載入、不卸載本機模型（LM Studio、Ollama 都一樣）；`probe(send=False)` 也不會觸發載入。
- 不重試、不換來源（交給 router）；不做 LLOP 與訂閱來源（另外的 adapter）。
- 不碰供應商 SDK；不讀 `.env` 或任何設定檔以外的地方拿金鑰。
