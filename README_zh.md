# Google Flights Search

繁體中文 | [English](README.md)

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

輕量級 Google Flights 查詢套件，**真正支援小型與地區性機場**——不需要瀏覽器、不需要 Playwright、不需要 Google 帳號。

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

```bash
pip install google-flights-search
```

本地開發安裝：

```bash
git clone https://github.com/NYCU-Chung/google-flights-search
cd google-flights-search
pip install -e .
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
)
```

**回傳值：** `list[dict]`，每筆格式如下：

```python
{
    "airlines": ["星宇航空"],              # 航空公司名稱列表
    "price": "TWD 8900",                  # 票價字串，無資料時為 ""
    "stops": 0,                           # 轉機次數
    "segments": [
        {
            "from": "RMQ",
            "to": "KMJ",
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

重啟 Claude Desktop 後即可使用，Claude 將擁有兩個工具：

- **`search_flights`** — 單段 / 來回查詢
- **`search_multi_city_flights`** — 多段行程 / open-jaw / 四段票

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

Google Flights 將航班資料透過 Server-Side Rendering 嵌入 `<script class="ds:1">` 標籤。`gf-search` 的流程：

1. 用正確的 protobuf 格式建立 `tfs` 參數（上表三個關鍵欄位）
2. 透過 `primp` 發送請求——這是一個 Rust HTTP 客戶端，可模擬 Chrome 的 TLS 指紋，不觸發 bot 偵測
3. 解析 `data[3]` 的**所有段落**（Best flights + Other flights），而非只有第一段，確保低流量航空也能出現
4. 最多 retry 3 次（間隔 1.5 秒）——Google SSR 具有非確定性，cold edge cache 第一次可能回傳 `null`

單程查詢使用 `field_19 = 2`（單程）搭配 `Info.field_16 = INT64_MAX` 強制進入完整 on-demand 模式，不需要假回程日期。

多段查詢（`search_multi_city`）額外流程：
- batchexecute 先暖機（同時用於 fallback 分票）
- GetShoppingResults 取得完整聯票定價（含環程票優惠）

---

## 限制

- **非官方 API：** Google 可能隨時更改回應格式。
- **SSR 非確定性：** 即使使用正確的 protobuf，cold cache 偶爾仍會回傳 `null`，內建 retry 邏輯能處理大多數情況，極冷門路線可能仍有偶發空結果。
- **票價幣別：** 預設使用 `hl=zh-TW`，幣別依 Google 從 IP 推斷的地區而定。
- **非訂位 API：** 僅抓取搜尋結果頁，不含艙位庫存或訂位層級資料。

---

## 貢獻

歡迎 PR！目前最有價值的貢獻方向：

- **補充 `CITY_ENTITIES`**（任何 Google 以城市 entity 而非 IATA 代碼索引的機場）
- 擴充 `_SEAT_MAP` 別名
- 票價幣別處理改善
- 加入 type stubs / `py.typed` 標記

新增 entity ID 請參考上方說明取得值後，加入 `gf_search/builder.py`。

---

## 授權

MIT — 詳見 [LICENSE](LICENSE)。
