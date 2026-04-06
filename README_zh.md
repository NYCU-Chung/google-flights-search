# Google Flights Search

繁體中文 | [English](README.md)

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

輕量級 Google Flights 查詢套件，**真正支援小型與地區性機場**。主流路線使用快速 SSR 路徑；地區性機場（如台中 RMQ、熊本 KMJ）自動切換 Playwright 模式。

## 為什麼不用 fast-flights？

`fast-flights` 等現有套件對低流量機場（如台中 RMQ、熊本 KMJ）會靜默回傳空結果。根本原因是 **protobuf URL 編碼不完整**：Google 收到格式錯誤的請求後跳過 on-demand 計算，直接回傳 `data[3] = null`。

`gf-search` 逆向了 Chrome 實際發送的 protobuf 格式，修正了三個關鍵欄位：

| 欄位 | fast-flights | gf-search |
|------|-------------|-----------|
| `Airport.field_1`（entity type）| 缺少 | `1` = IATA 機場，`2` = 城市 entity ID |
| `Info.field_1`、`Info.field_2` | 缺少 | `28`、`2`（查詢類型旗標）|
| `Info.field_16` | 缺少 | `INT64_MAX`——觸發小機場 on-demand 計算 |

**實測結果：** 查詢 `RMQ → KMJ` 可正確回傳包含星宇航空（JX）直飛班次的完整資料，而 fast-flights 回傳 `data[3] = null`。

## 安裝

### 基本（SSR，主流路線）

```bash
pip install google-flights-search
```

### + Playwright 支援（地區性小機場，如 RMQ、KMJ 必裝）

```bash
pip install "google-flights-search[playwright]"
playwright install chromium      # 下載瀏覽器二進位（約 130MB），只需一次
gf-search-setup                  # 一次性 Google 登入 → 儲存 session
```

`gf-search-setup` 會開啟瀏覽器視窗，登入 Google 帳號後自動偵測並儲存 session 到 `~/.flight_agent/session_cookies.json`。後續所有搜尋自動使用，不需再次設定。

### Windows：自動提取 Chrome session（Chrome 未開啟時免登入）

```bash
pip install "google-flights-search[playwright,windows]"
playwright install chromium
```

Chrome **未執行**時，session 可直接從 Chrome cookie 資料庫自動提取，不需手動 `gf-search-setup`。

### 全功能安裝

```bash
pip install "google-flights-search[full]"
playwright install chromium
gf-search-setup
```

### 本地開發安裝

```bash
git clone https://github.com/NYCU-Chung/google-flights-search
cd google-flights-search
pip install -e ".[playwright,windows]"
playwright install chromium
gf-search-setup
```

## 快速開始

```python
from gf_search import search

# 查桃園（TPE）→ 東京成田（NRT）
results = search("TPE", "NRT", "2026-08-08")
for r in results:
    print(r["airlines"], r["price"], r["stops"], "轉")
```

```python
# 小機場範例——這正是 gf-search 的強項
# fast-flights 回傳空結果；gf-search 回傳星宇航空直飛
results = search("RMQ", "KMJ", "2026-08-08")
for r in results:
    print(r["airlines"], r["price"])
```

```python
# 多段行程（open-jaw、外站四段等）
from gf_search import search_multi_city

results = search_multi_city([
    {"from": "OKA", "to": "TPE", "date": "2026-09-01"},
    {"from": "TPE", "to": "HKG", "date": "2026-09-03"},
    {"from": "HKG", "to": "TPE", "date": "2026-09-07"},
    {"from": "TPE", "to": "OKA", "date": "2026-09-08"},
], travel_class="economy", max_results=5)

for r in results:
    is_combined = bool(r.get("booking_token"))
    print(r["airlines"], r["price"], "聯票" if is_combined else "分票")
```

## API 文件

### `search()`

```python
from gf_search import search

results = search(
    origin="TPE",                # IATA 出發機場代碼
    destination="NRT",           # IATA 抵達機場代碼
    departure_date="2026-08-08", # "YYYY-MM-DD"
    return_date=None,            # 來回票填回程日期；單程填 None
    adults=1,                    # 成人人數
    travel_class="economy",      # "economy" | "premium-economy" | "business" | "first"
    max_results=5,               # 最多回傳幾筆
    currency="TWD",              # 票價幣別（如 "TWD"、"USD"、"JPY"）
    max_stops=None,              # 轉機次數上限：0=直飛、1=最多1轉、None=不限
)
```

**回傳值：** `list[dict]`，每筆格式如下：

```python
{
    "airlines": ["JX"],                   # 各航段實際承運航空 IATA 代碼（可多個）
    "price": "TWD 8900",                  # 票價字串，無資料時為 ""
    "stops": 0,                           # 轉機次數
    "segments": [
        {
            "from": "RMQ",
            "to": "KMJ",
            "flight_no": "JX317",         # 航班號（如 "JX317"、"CI002"），無資料時為 ""
            "departure": "2026-08-08 15:00",
            "arrival": "2026-08-08 18:15",
            "duration_min": 95,
            "plane": "Airbus A321neo",
        }
    ],
    "source": "gf_search",
}
```

retry 後仍無結果時回傳 `[]`。

---

### `session_status()`

檢查已儲存的 Google session 健康狀態（供階段 5 Playwright fallback 使用）。

```python
from gf_search import session_status

status = session_status()
print(status)
# {
#     "valid": True,           # session 存在且未過期
#     "exists": True,          # session_cookies.json 是否存在
#     "age_hours": 72.5,       # 距上次設定的小時數（無檔案時為 -1）
#     "stale": False,          # 超過 72 小時為 True
#     "cookie_count": 10,      # session cookie 數量
#     "message": "Google session 有效（10 cookies，72 小時前更新）。"
# }
```

---

### `search_multi_city()`

查詢多段行程（open-jaw、外站定位、四段票等）。

```python
from gf_search import search_multi_city

results = search_multi_city(
    segments=[                          # 至少 2 段，最多 5 段
        {"from": "TPE", "to": "NRT", "date": "2026-05-01"},
        {"from": "NRT", "to": "LHR", "date": "2026-05-03"},
        {"from": "LHR", "to": "TPE", "date": "2026-05-10"},
    ],
    adults=1,
    travel_class="business",
    max_results=5,
)
```

同時回傳兩種結果：

| source | 說明 |
|--------|------|
| `gf_search_multi_city_gsr` | **聯票**（GetShoppingResults，可能含環程票優惠，有 `booking_token`）|
| `gf_search_multi_city` | **分票**（batchexecute 各段加總，不保證同一 PNR）|

有 `booking_token` 的結果可直接在 Google Flights 完成訂購。

---

### `build_tfs()`

建立 Google Flights `tfs` URL 參數（URL-safe base64 編碼的 protobuf）。適合想自行發送 HTTP 請求或驗證編碼的使用者。

```python
from gf_search import build_tfs

tfs = build_tfs(
    origin="RMQ",
    destination="KMJ",
    departure_date="2026-08-08",
    return_date="2026-08-15",   # 選填
    seat=1,                     # 1=經濟 2=豪華經濟 3=商務 4=頭等
    adults=1,
)

url = f"https://www.google.com/travel/flights/search?tfs={tfs}&tfu=EgIIACIA&hl=zh-TW"
print(url)  # 可直接貼到 Chrome 驗證結果
```

---

### `build_tfs_multi_city()`

多段版本的 `build_tfs()`，field 19 設為 MULTI_CITY（3）。

```python
from gf_search import build_tfs_multi_city

tfs = build_tfs_multi_city(
    segments=[
        {"from": "OKA", "to": "TPE", "date": "2026-09-01"},
        {"from": "TPE", "to": "HKG", "date": "2026-09-03"},
    ],
    seat=1,
    adults=1,
)
```

---

### `CITY_ENTITIES`

IATA 代碼對應 Google 城市 entity ID 的字典。一般機場使用 `entity_type=1`（自動處理）；部分機場 Google 以城市層級索引，需用 `entity_type=2` 搭配特殊 entity ID。

```python
from gf_search import CITY_ENTITIES

print(CITY_ENTITIES)
# {
#     "RMQ": "/m/01r8pt",   # 台中（城市 entity）
#     "KHH": "/m/0h7h6",    # 高雄
#     "TSA": "/m/02kg86",   # 台北松山
# }

# 自行新增：
CITY_ENTITIES["OKA"] = "/m/0h7r_"  # 沖繩那霸
```

查詢 entity ID 方法：在 Chrome 開啟 Google Flights，觸發目標機場的搜尋，在 DevTools → Network 裡找 `tfs` 查詢參數，base64 解碼後讀取 `Airport.field_2` 的值。

---

## MCP Server（供 Claude 及 AI 助理使用）

`gf-search` 內建 MCP server。上傳 PyPI 後，任何人都可以一行設定加進 Claude Desktop，不需要預先安裝。

**Claude Desktop 設定檔**（Windows：`%APPDATA%\Claude\claude_desktop_config.json`，macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "google-flights": {
      "command": "uvx",
      "args": ["--from", "google-flights-search", "gf-search-mcp"]
    }
  }
}
```

重啟 Claude Desktop 後即可使用，Claude 將擁有四個工具：

- **`search_flights`** — 單段 / 來回查詢（支援 `max_stops` 過濾；結果為空或無票價時附帶提示訊息）
- **`search_multi_city_flights`** — 多段行程 / open-jaw / 四段票
- **`search_cheapest_dates`** — 在日期範圍內搜尋並依票價排序（適合彈性日期旅客）
- **`generate_search_urls`** — 產生 Google Flights URL，可直接在瀏覽器開啟（適用於 API 結果不完整的冷門路線）

若偏好手動安裝：

```bash
pip install "google-flights-search[mcp]"
```

```json
{
  "mcpServers": {
    "google-flights": {
      "command": "gf-search-mcp"
    }
  }
}
```

---

## 技術原理

`gf-search` 使用多階段 pipeline，找到結果就停止：

| 階段 | 方式 | 需求 |
|------|------|------|
| 0 | Chrome 認證快取（`~/.gf_search/chrome_cache.json`）| 預先填充的快取檔 |
| 1–3 | `primp` SSR + `tfu`/`batchexecute` fallback | 無（純 HTTP）|
| 5 | Playwright：真實 Chrome/Chromium + 網路攔截 | `playwright` + `gf-search-setup` |
| 4 | 補充班表（`schedules.json`）| 無 |

**階段 1–3（快速路徑）：** Google Flights 透過 SSR 將航班資料嵌入 `<script class="ds:1">` 標籤。`gf-search`：

1. 建立正確的 protobuf `tfs` 參數（三個關鍵欄位是核心修正）
2. 透過 `primp`（模擬 Chrome TLS 指紋的 Rust HTTP 客戶端）發送請求
3. 最多 retry 3×；若仍空，改用 `tfu`-based 回程抓取與 `batchexecute` chain

**階段 5（地區性機場與來回票）：** 對 SSR 快取為空的路線（如 RMQ→KMJ），或所有 SSR 結果皆無票價時，透過 Playwright 啟動真實 Chrome/Chromium。來回票查詢會先嘗試原生來回票 URL（保留來回票價）；Playwright 失敗時才拆為兩段單程（結果附帶 `direction` 欄位及 `_price_note`）。網路回應（`GetShoppingResults`）直接攔截解析——無航空公司特判，Google 索引的所有路線皆適用。`session_cookies.json` 的 session cookies 會自動注入 Playwright 及 SSR 請求。

多段查詢（`search_multi_city`）額外流程：
- batchexecute 先暖機（同時作為 fallback 分票）
- GetShoppingResults 取得完整聯票定價（含環程票優惠）

---

## 限制

- **非官方 API：** Google 可能隨時更改回應格式。
- **SSR 非確定性：** 即使使用正確的 protobuf，cold cache 偶爾仍會回傳 `null`，內建 retry 邏輯能處理大多數情況。
- **地區性機場需要 Playwright：** SSR 快取為空的路線（小機場）需安裝 `pip install "google-flights-search[playwright]"` + `playwright install chromium` + `gf-search-setup`。
- **Google session：** 階段 5 在無 session 時仍可運作，但回傳結果較少。執行 `gf-search-setup` 一次可獲得完整覆蓋。Setup 主要影響冷門路線（階段 5）；主流路線透過 SSR 查詢，有無 session 結果相同。
- **票價幣別：** 可透過 `currency` 參數設定（預設 `"TWD"`）。
- **非訂位 API：** 僅抓取搜尋結果頁，不含艙位庫存或訂位層級資料。

---

## 貢獻

歡迎 PR！目前最有價值的貢獻方向：

- **補充 `CITY_ENTITIES`**（任何 Google 以城市 entity 而非 IATA 代碼索引的機場）
- 擴充 `_SEAT_MAP` 別名

新增 entity ID 請參考上方說明取得值後，加入 `gf_search/builder.py`。

---

## 授權

MIT — 詳見 [LICENSE](LICENSE)。
