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
| `ntu_easy_llm.waterfall` | 備援鏈的**型別與 JSON 設定格式**（路由邏輯在之後的 `ntu_easy_llm.router`） |

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
- `available`：有 adapter、而且它的 `check` 說這台能用才是 True；不能用時 `unavailable_reason` 寫原因（例如「要先安裝 ntu_easy_llm[llop]」「這個版本還沒有這個來源的 adapter」）。這個預發布版只有 `local/fake` 有 adapter，所以目錄上的來源目前都顯示不可用，等 HTTP、LLOP、訂閱 adapter 加進來就會自動變成可用。

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

## 4. 相容與版本

- 版本 `0.6.0.dev0`（semver minor：只加東西）。
- 必裝相依不變（openai、google-genai、anthropic 留給舊的 `ask*`）。新增 extra：`yaml`（PyYAML）。
- 授權維持 MIT；使用、修改請保留 `LICENSE` 裡的版權聲明。
