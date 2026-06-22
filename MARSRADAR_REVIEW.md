# MarsRadar App Store Review Risk Assessment

日期：2026-06-23  
範圍：MarsRadar iOS 上架審查風險、整改方案、產品功能 roadmap  
定位：非官方粉絲 App，聚合 Elon Musk / Tesla / SpaceX / xAI / X 動態與相關新聞，產出中英雙語 AI digest。  

> 免責：這是 App Store 審查與產品合規角度的工程/政策評估，不是正式法律意見。涉及商標、肖像權、著作權、X Developer Terms、新聞授權與 fair use，送審前仍建議由熟悉美國與目標上架地區 IP/媒體法的律師快速 review。

## 依據

- Apple App Review Guidelines: https://developer.apple.com/app-store/review/guidelines/
- X Developer Policy: https://docs.x.com/developer-terms/policy
- X robots.txt: https://x.com/robots.txt
- Repo 現況：`README.md`、`latest.json`、`legal/privacy.html`、`legal/terms.html`

## Executive Summary

MarsRadar 不是不能上 App Store，但「直接把 GitHub Pages 網頁用 Capacitor/WKWebView 包起來」的拒審風險偏高。主要風險不是技術包裝，而是三件事：

1. `5.2.2 Third-Party Sites/Services`：App 會顯示 X 內容、推文 quote、官方帳號內容與新聞摘要。Apple 明確要求使用/顯示第三方服務內容時必須符合該服務條款，且 Apple 可要求提供授權證明。
2. `4.2 Minimum Functionality` / `4.2.2`：目前描述像「內容聚合 + 外部連結 + digest」。若 iOS 版只是網頁殼，會被認為沒有超出 repackaged website 或 content aggregator。
3. `5.2.1` / `4.1`：名稱、metadata、icon、截圖、內文若大量使用 Elon Musk、Tesla、SpaceX、xAI、X 的商標、logo、肖像或造成官方關聯錯覺，容易被要求改名、改圖、補授權，甚至拒審。

建議送審定位不要寫成「Elon/Tesla/X 最新動態轉貼器」，而要改成「AI-assisted news analysis and commentary app」。核心產品價值應是原創摘要、時間線、主題追蹤、可信度/來源標註、影響分析、個人化提醒、離線閱讀、widget，而不是重製推文。

## 1. 被拒風險逐條盤點

| 風險 | 機率 | 條款 | 為何可能中招 | 建議修正 |
|---|---:|---|---|---|
| X/Twitter 內容授權與展示 | 高，75-85% | `5.2.2 Third-Party Sites/Services`、`5.2.1 Generally` | App 顯示 X 原文 quote、X 官方/個人帳號內容，且資料來源是「grok 讀 X」。Apple 可要求證明你被 X 條款允許使用、存取、顯示、快取與商業化這些內容。X Developer Policy 對 public display、content redistribution、service authenticity、X name/logo 都有具體限制。 | 優先改成：只顯示短摘錄、來源連結、Post ID/URL、自己的分析；不要大量存全文 quote；若用 X 內容，改走 X API 或 X for Websites 的合規方式；保留 takedown 機制；App Review Notes 說明資料來源與授權路徑。 |
| 抓取 X / robots / 非 API 取得 | 高，70-85% | `5.2.2` | `x.com/robots.txt` 對一般 bot 是 `Disallow: /`；X Policy 要求使用 API/X tools 時遵守 policy，並對非 API republishing、離線儲存、刪文更新有要求。即使 Grok 可讀 X，App 將其結果商品化展示仍可能被審查員問授權。 | 不要在審查材料中描述為 scrape。描述為「AI-curated editorial summaries from publicly available sources」仍不夠，最好建立可證明的合規資料管線：X API / 合法授權 / 只保存自寫摘要 + URL，不保存完整內容。 |
| Elon Musk 姓名/肖像/名人權利 | 中高，55-70% | `5.2.1`、`4.1`、`2.3.7` | 名人姓名可用於新聞評論語境，但 App 名稱、icon、subtitle、截圖若把 Elon 作為主品牌，會被懷疑借名人流量或暗示背書。若使用肖像照片，還有 publicity rights / copyright / metadata 誤導問題。 | App 名稱維持 `MarsRadar`，副標避免 `Elon Musk Tracker` 這種強依附。不要用 Elon 肖像當 icon/splash。App Store metadata 用「Musk-related companies and public tech news analysis」之類描述，不要把名人姓名塞滿 keywords。 |
| Tesla / SpaceX / xAI / X 商標與 logo | 高，65-80% | `5.2.1`、`4.1(c)`、`2.3.7` | Apple 禁止未授權使用他人品牌、產品名、icon 造成混淆。X Policy 也要求 X 名稱/logo 只能以不造成背書錯覺的方式識別來源。Tesla/SpaceX/xAI logo 若出現在 icon、首頁主視覺、App Store 截圖，風險會升高。 | App icon 不使用任何第三方 logo、火箭/車標近似圖形也要避開。分頁可用純文字 `Tesla`、`SpaceX` 作為新聞主題標籤，但加來源/商標歸屬聲明。避免 `for Tesla`、`for SpaceX` 這類可能被解讀成相容/官方產品的命名。 |
| 可能被視為官方或 impersonation | 中高，50-70% | `4.1 Copycats`、`5.2.1`、`5.2.2` | 雖已有 disclaimer，但如果 UI、icon、metadata 第一眼很像官方動態中心，disclaimer 放在 legal 深處不夠。審查看 icon、名稱、截圖、首屏。 | 在 onboarding、About、App Store description 首段、設定頁都清楚放「Unofficial independent news analysis app」。不要使用官方色彩/品牌系統作為主視覺。 |
| 純 WKWebView / Capacitor 網頁殼 | 高，70-85% | `4.2 Minimum Functionality`、`4.2.2` | Apple 明確要求 App 要超越 repackaged website，且不應主要是 web clippings、content aggregators 或 link collection。GitHub Pages 純前端若原封不動包成 iOS App，這是最典型拒審點。 | 做原生功能：推播、離線快取、iOS widget、Share Extension、Search/Spotlight、深色模式、Dynamic Type、App Intents、收藏、閱讀進度、通知偏好；App Review Notes 明確列出 iOS-only features。 |
| 低原創度內容聚合 / spam app | 中，40-60% | `4.3 Spam`、`4.2.2` | App Store 已有大量新聞、Twitter/X tracker、Tesla news app。若 MarsRadar 只是把公開貼文摘要成列表，容易被視為 widely available aggregator 或蹭熱點。 | 強化不可替代的 editorial layer：跨來源合併、時間線、事件演化、立場/可信度標註、影響分析、雙語 QA、歷史脈絡、重要性解釋。每則 item 至少有「原創分析」欄位，而不只是 quote + summary。 |
| 大量引用推文原話 | 中高，55-75% | `5.2.1`、`5.2.2` | 推文也是著作權內容；短句引用通常風險低，但完整重製長推文、保存多則原文、翻譯全文，會讓 App 像內容替代品而不是評論。X Policy 對 public display、離線內容更新/刪除也有要求。 | 每則最多保留 1-2 句短 quote，必要時截斷並標明來源；以自己的摘要與評論為主；不要把完整推文翻譯成中文；點擊原文才看完整內容。建立 24 小時內刪除/更新機制，處理原文刪除或權利人要求。 |
| 政治、仇恨、誹謗與敏感內容 | 中，35-55% | `1.1 Objectionable Content`、`1.1.6 False Information`、`2.3.6 Age Rating` | Elon 內容常涉及政治、戰爭、種族、恐怖主義、指控他人犯罪。`latest.json` 內已有「evil liar」「stock insider trader」「anti-white racism」「terrorist」等高風險描述。若 App 直接放大未驗證指控，可能觸發內容與名譽風險。 | 加「原話/指控/未經法院認定」等語氣隔離；AI 摘要不得把他人犯罪指控寫成事實；敏感政治內容加 category/label；年齡分級誠實回答。加入人工或規則審核：誹謗、仇恨、暴力、成人、金融建議。 |
| 錯誤資訊與 AI hallucination | 中，35-50% | `1.1.6`、`2.3.1`、`2.3 Accurate Metadata` | App 以 AI 自動生成 digest，且更新頻繁。若摘要把推文脈絡寫錯、錯引來源，可能被認為內容不可靠，尤其是涉及政治、金融、公司消息時。 | 每則顯示來源清單、生成時間、更新時間、可信度/確認程度；加入「Report correction」；高熱度或高風險內容進人工審核；明確聲明非投資建議。 |
| 金融/投資建議誤解 | 中，30-45% | `3.2.1(viii)`、`2.3.1` | Tesla 是上市公司，App 有「火焰熱度」和可能的市場影響。若加股價關聯、投資預測、訂閱制，可能被解讀成投資建議或交易工具。 | 明確定位為新聞/教育/分析，不提供買賣建議；若做股價關聯，避免「買/賣/目標價」；加入 financial disclaimer；不要提供交易功能。 |
| 隱私政策與 App Privacy Label | 中低，20-35% | `5.1.1`、`1.6` | 現有 `legal/privacy.html` 做得不錯，聲明無登入、無廣告/分析 SDK、StoreKit 由 Apple 處理。但 iOS 版若加推播、訂閱、analytics、crash reporting、SFSafariViewController、X embeds，App Store Connect privacy label 必須一致。 | 送審前逐項盤點 SDK、網路請求、Device ID、diagnostics、purchases、notification token。若使用 X embed，要揭露 X cookie/追蹤與外部網站資料實務。 |
| Pro 訂閱資訊 | 中低，20-35% | `3.1.2`、`2.3.2` | Terms/Privacy 已提到 Pro，但 App Store 版若實際有訂閱，必須清楚列出價格、週期、解鎖內容、取消方式；不能把主要功能過度鎖住造成 bait-and-switch。 | Paywall 以 StoreKit 標準資訊顯示；免費版保留足夠核心功能；App Store description、screenshot、IAP metadata 一致。 |
| Push Notifications | 低到中，15-30% | `4.5.4`、`5.1.1` | 若後續加重大動態通知，不能要求開通知才能使用，且不能未經同意發促銷通知。 | 明確 opt-in，提供 topic-level opt-out：Elon / Tesla / SpaceX / xAI / breaking only。通知內容避免敏感個資和誤導標題。 |
| 使用者討論/社群功能 | 目前低；加入後高 | `1.2 User-Generated Content` | 目前沒有 UGC。若加留言、社群討論，必須有過濾、檢舉、封鎖、聯絡資訊與處理流程。政治/名人主題的 UGC 審核負擔很重。 | 第一版不要加開放留言。若加社群，用受控回饋/投票/emoji reaction，或導向外部社群；真正留言需 moderation pipeline。 |

## 2. 過審改進方案

### 2.1 命名與品牌

建議保留：

- App name：`MarsRadar`
- Subtitle：`AI tech news brief`、`Musk-era tech digest`、`Bilingual tech brief`
- Category：News 或 Reference，依 App 主要體驗決定

避免：

- `Elon Musk Radar`
- `Tesla SpaceX X Tracker`
- `Official Tesla / SpaceX News`
- 在 icon、splash、App Store 截圖中使用 Elon 肖像、Tesla T、SpaceX wordmark、xAI logo、X logo
- App Store keywords 塞滿 `Tesla, SpaceX, xAI, Elon Musk, X, Twitter`

可接受但要克制：

- 內文主題標籤使用 `Tesla`、`SpaceX`、`xAI`、`X` 作為新聞分類文字。
- 每個分類頁下方或 About 頁提供商標歸屬聲明：「All trademarks are property of their respective owners. MarsRadar is not affiliated with or endorsed by Elon Musk, Tesla, SpaceX, xAI, or X.」

### 2.2 Disclaimer 放置

現有 privacy/terms 已有 disclaimer，但送審需要更醒目：

- App Store description 第一段就放：`MarsRadar is an independent, unofficial news analysis app. It is not affiliated with, endorsed by, or sponsored by Elon Musk, Tesla, SpaceX, xAI, or X.`
- 首次開啟 onboarding 放一次簡短 disclaimer，不要藏在 legal。
- Settings / About 固定放完整 disclaimer。
- 每個 X 原文連結旁用小字標「Source: X.com」或「Original post on X」，不要讓使用者以為內容由 MarsRadar 或官方直接發布。
- App Review Notes 主動說明：非官方、無商標/logo/肖像、內容是自寫摘要與評論、每則附來源連結、可受理 takedown。

### 2.3 內容策略：從轉貼改成評論/分析

目前 JSON 內容含 `musk_quote` 與 `musk_quote_zh`，長 quote 是高風險點。建議改資料模型與 UI：

- `short_quote`：限制 280 字元內，且只在必要時顯示。
- `quote_excerpt_reason`：說明為何引用這句，例如「關鍵原話」「反映政策立場」。
- `analysis_en` / `analysis_zh`：每則 2-4 句原創分析，說明背景、影響、未確認點。
- `source_links`：只放 URL、source label、published_at，不做完整內容替代。
- `verification_status`：`confirmed` / `single-source` / `developing` / `opinion`。
- `risk_labels`：`politics`、`market-sensitive`、`legal-claim`、`war/conflict` 等。
- `correction_url` 或 app 內「Report issue」。

Fair use 實務上不能只靠聲明。可執行標準：

- 不重製完整新聞文章。
- 不重製完整長推文串。
- quote 只保留評論所必需的短片段。
- 摘要與分析比例高於引用內容，建議每則可見內容至少 70% 是 MarsRadar 原創摘要/分析。
- 對短回覆如 `True`、`McCarthy was right` 可引用全文，但仍要補上下文與來源。
- 翻譯不要變成完整替代品；長原文只翻譯重點而不是全文。

### 2.4 X 合規與 takedown

送審最硬的問題是 Apple `5.2.2`：「你是否被第三方服務允許這樣使用內容？」建議先做最保守版本：

- 不在 App 內 embed X timeline。
- 不提供 X 內容下載、匯出、批量複製。
- 不保存完整推文全文；保存 Post URL、Post ID、短摘錄、MarsRadar 自寫摘要。
- 若來源原文刪除、帳號變 protected、收到權利人/X 要求，24 小時內移除對應 excerpt 和連結。
- 建立 `legal/takedown.html` 或在 Terms 補上 takedown email 與流程。
- 後端記錄每則來源的 `checked_at`，定期驗證原文是否仍可公開存取。
- 若可行，改用 X API 或 X 官方 embed/display requirement；若不可行，降低 X 原文展示比例，把 App 的可見價值轉向新聞分析。

送審 Notes 可寫：

```text
MarsRadar is an unofficial independent news analysis app. It does not impersonate or claim affiliation with Elon Musk, Tesla, SpaceX, xAI, or X. The app displays original bilingual summaries and editorial analysis with links to public source pages. It does not reproduce full articles or full X posts. The app includes a takedown/correction contact at apple@jikker.net and can remove third-party excerpts upon request.
```

若實際沒有 X 授權，避免寫「authorized」「official」「partner」「powered by X」。

### 2.5 iOS 原生功能，降低 4.2 被拒

如果用 Capacitor/WKWebView，至少補以下原生功能，並在 App Review Notes 和截圖中展示：

必做：

- Push notifications：重大動態、分類訂閱、每日 brief，使用者可逐項 opt-in/out。
- Offline reading：最近 24-72 小時 digest 本機快取，無網路仍可閱讀。
- Native settings：語言、分類、通知、外觀、資料清除、法律文件。
- Bookmark / Saved items：本機收藏、稍後閱讀。
- Share sheet：分享摘要卡片或原文連結，避免分享整段第三方內容。
- Native detail view：不要只是 web page iframe；至少有 iOS 導航、搜尋、字級、深色模式。

強烈建議：

- Home Screen / Lock Screen widget：今日一分鐘 brief、最高熱度事件。
- Spotlight indexing：收藏或重要事件可被 iOS 搜尋。
- App Intents / Siri Shortcuts：查今日 Tesla / SpaceX / xAI brief。
- Background refresh：定期更新摘要，但要節制電量與資料使用。
- Live Activity 只在真正「事件進行中」才用，例如 launch countdown；不要濫用新聞推播。

審查說法要聚焦「iOS app-like utility」，不要說「這是我們網站的 app 版」。

### 2.6 政治與敏感內容控制

MarsRadar 追蹤 Elon 必然會碰到政治、種族、戰爭、犯罪指控。建議加內容安全層：

- 對政治/族群/犯罪指控加 `sensitive_label`。
- 摘要語氣使用 attribution：「Musk claimed」「Musk alleged」「該貼文主張」，不要直接寫成事實。
- 涉及個人犯罪、詐欺、內線交易、恐怖主義等，用「未經法院認定」「公開指控」語境。
- 避免 inflammatory headline。不要把原文罵人詞當 App 自己標題的主要賣點。
- 加 Correction/Report。這對 `1.1.6` 和名譽風險很重要。

### 2.7 Privacy / Legal

現有 `legal/privacy.html` 和 `legal/terms.html` 基本方向正確，但 iOS 送審前要補：

- App Store Connect Privacy Nutrition Labels 與實際 SDK 一致。
- 若使用 crash reporting，揭露 diagnostics。
- 若使用 push，揭露 device token 的用途、保存、刪除。
- 若使用 analytics，揭露 usage data，並提供 opt-out。
- 若使用 X embed 或外部 web view，說明第三方網站可能收集資料。
- Terms 補「No investment advice」「No affiliation」「Takedown / correction process」「AI-generated content may be inaccurate」。
- 若訂閱制上線，paywall 和 Terms 要列週期、價格、取消方式、免費試用規則。

### 2.8 上架前 Checklist

- [ ] App name/icon 不含 Elon 肖像、Tesla/SpaceX/xAI/X logo 或近似官方標誌。
- [ ] App Store subtitle/description 第一段明確寫非官方、未背書。
- [ ] App Store keywords 不濫用商標詞。
- [ ] 首次開啟、About、Settings、legal 都有 disclaimer。
- [ ] 每則內容以 MarsRadar 原創摘要/分析為主，quote 只是短摘錄。
- [ ] 不顯示完整新聞全文，不顯示完整長推文串。
- [ ] 建立 correction/takedown 流程，並在 Terms 或獨立頁面公開。
- [ ] 後端可移除特定 story/excerpt，且能處理來源刪除。
- [ ] iOS 版至少有推播、離線、收藏、分享、native settings、widget 中的 3-4 項。
- [ ] App 不只是 WKWebView 載入 GitHub Pages；首屏與核心互動有原生或明顯 app-like 體驗。
- [ ] App Review Notes 說明資料來源、非官方定位、原創內容比例、iOS-only features。
- [ ] Privacy Policy、Terms、Support URL、Marketing URL 在 App Store Connect 都可訪問。
- [ ] App Privacy Label 與 SDK/資料流一致。
- [ ] Age Rating 誠實回答政治、新聞、可能不受控外部內容。
- [ ] 若有訂閱，IAP 商品、價格、週期、paywall、restore purchase 全部可審。
- [ ] 所有外部連結用 SFSafariViewController 或清楚標示會離開 App。

## 3. 程式功能持續加強 Roadmap

| 優先級 | 功能 | 價值 |
|---|---|---|
| P0 | 原創分析欄位與引用縮短 | 同時降低 `5.2` 內容重製風險，並提高產品差異化。 |
| P0 | iOS 推播通知 | 重大動態、SpaceX launch、Tesla 事件可即時提醒，是最有力的 native utility。 |
| P0 | 離線快取與收藏 | 讓 App 超越網站，支援通勤閱讀、稍後讀、無網路瀏覽。 |
| P0 | Correction / Takedown / Report issue | 降低錯誤資訊、誹謗、IP 投訴風險，也讓內容管線可運營。 |
| P1 | Widget / Lock Screen brief | 每日一句 brief 或最高熱度事件，提升留存並強化 iOS 原生感。 |
| P1 | 事件時間軸 | 把零散貼文合併成事件演化，形成不可被 X 搜尋直接替代的分析價值。 |
| P1 | 可信度與來源標註 | 標示官方帳號、媒體、單一來源、未確認、AI 推論，降低誤導風險。 |
| P1 | 多語言擴充 | 先從繁中/英文擴到日文、韓文、西文，增加市場但保留同一資料管線。 |
| P1 | 影響分析 / What changed | 每則提供「對 Tesla、SpaceX、xAI、政策、市場可能影響」，把 App 定位為分析工具。 |
| P2 | 股價/市場關聯視圖 | 用時間軸對照 TSLA 股價、成交量、重大新聞，但必須明確非投資建議。 |
| P2 | 個人化 watchlist | 使用者選擇只看 Tesla / SpaceX / AI / politics，提升通知精準度與留存。 |
| P2 | 分享卡片生成 | 產生含摘要、來源、非官方標記的圖片卡，方便社群分享且避免全文複製。 |
| P3 | Pro 訂閱 | 可把歷史搜尋、深度分析、進階通知、主題 watchlist、匯出收藏放 Pro；不要鎖掉基本新聞閱讀。 |
| P3 | 受控社群回饋 | 先做 emoji reaction、poll、submit correction，不建議第一版開留言，避免 `1.2` moderation 負擔。 |

## 建議送審版本策略

第一版不要貪功能，目標是降低拒審面積：

1. 品牌乾淨：`MarsRadar`，無官方 logo/肖像，醒目 disclaimer。
2. 內容保守：短 quote + 原創中英摘要 + 原創分析 + source link，不做完整轉載。
3. 原生足夠：推播、離線、收藏、分享、設定、widget 至少完成一組明顯 iOS-only features。
4. 法務可回應：Privacy、Terms、Takedown、Support、App Review Notes 完整。
5. 審查敘事一致：從 metadata 到 UI 到 legal 都說「independent news analysis」，不要一處寫 fan tracker、一處寫 official feed、一處寫 X-powered。

如果只能做最小改動，最關鍵的三項是：

1. 拿掉 logo/肖像與過度商標化 metadata。
2. 把完整推文 quote 改成短摘錄，增加原創分析欄位。
3. 不要直接提交純 WKWebView；至少補離線、收藏、推播、原生設定，並在 Review Notes 明確列出。
