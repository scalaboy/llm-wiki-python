# Wiki 巡检报告 — 2026-07-05

共扫描 32 个页面。

## 结构问题

### 空页 / 桩页 (1)
- 🟡 `wiki/concepts/积分体系.md` — 正文 97 字符

### 孤儿页（无入链, 4）
- `wiki/sources/wandaoda-commodity-platform.md`
- `wiki/sources/yidaobao-logistics-platform.md`
- `wiki/sources/wanlianjingxi-consumer-platform-duplicate.md`
- `wiki/sources/wanyoutong-platform.md`

### 坏链（指向不存在页面, 47）
- `wiki/overview.md` → `[[AI驱动]]`
- `wiki/overview.md` → `[[智能匹配]]`
- `wiki/overview.md` → `[[优选熟车]]`
- `wiki/overview.md` → `[[优选熟车]]`
- `wiki/overview.md` → `[[企业钱包]]`
- `wiki/overview.md` → `[[议价模式]]`
- `wiki/overview.md` → `[[企业钱包]]`
- `wiki/overview.md` → `[[优选熟车]]`
- `wiki/overview.md` → `[[智能匹配]]`
- `wiki/concepts/物流撮合交易.md` → `[[智能匹配]]`
- `wiki/concepts/物流撮合交易.md` → `[[议价模式]]`
- `wiki/concepts/物流撮合交易.md` → `[[企业钱包]]`
- `wiki/concepts/物流撮合交易.md` → `[[智能匹配]]`
- `wiki/concepts/物流撮合交易.md` → `[[议价模式|一口价+议价]]`
- `wiki/concepts/物流撮合交易.md` → `[[企业钱包]]`
- `wiki/concepts/物流撮合交易.md` → `[[智能匹配]]`
- `wiki/concepts/AI外呼.md` → `[[智能匹配]]`
- `wiki/concepts/AI外呼.md` → `[[议价模式]]`
- `wiki/concepts/AI外呼.md` → `[[智能匹配]]`
- `wiki/concepts/AI外呼.md` → `[[优选熟车]]`
- `wiki/concepts/AI外呼.md` → `[[智能匹配]]`
- `wiki/concepts/AI外呼.md` → `[[智能匹配]]`
- `wiki/sources/wandaoda-commodity-platform.md` → `[[AI驱动]]`
- `wiki/sources/yidaobao-logistics-platform.md` → `[[智能匹配]]`
- `wiki/sources/yidaobao-logistics-platform.md` → `[[优选熟车]]`
- `wiki/sources/yidaobao-logistics-platform.md` → `[[议价模式|一口价+议价]]`
- `wiki/sources/yidaobao-logistics-platform.md` → `[[企业钱包]]`
- `wiki/sources/yidaobao-logistics-platform.md` → `[[智能匹配]]`
- `wiki/sources/yidaobao-logistics-platform.md` → `[[优选熟车]]`
- `wiki/sources/yidaobao-logistics-platform.md` → `[[企业钱包]]`
- `wiki/sources/yidaobao-logistics-platform.md` → `[[智能匹配]]`
- `wiki/sources/yidaobao-logistics-platform.md` → `[[优选熟车]]`
- `wiki/sources/yidaobao-logistics-platform.md` → `[[企业钱包]]`
- `wiki/sources/yidaobao-logistics-platform.md` → `[[议价模式]]`
- `wiki/sources/wanyoutong-platform.md` → `[[数字化能源管理]]`
- `wiki/entities/易达宝.md` → `[[智能匹配]]`
- `wiki/entities/易达宝.md` → `[[优选熟车]]`
- `wiki/entities/易达宝.md` → `[[智能匹配]]`
- `wiki/entities/易达宝.md` → `[[优选熟车]]`
- `wiki/entities/易达宝.md` → `[[议价模式|一口价+议价]]`
- `wiki/entities/易达宝.md` → `[[企业钱包]]`
- `wiki/entities/易达宝.md` → `[[智能匹配]]`
- `wiki/entities/易达宝.md` → `[[优选熟车]]`
- `wiki/entities/易达宝.md` → `[[企业钱包]]`
- `wiki/entities/易达宝.md` → `[[议价模式]]`
- `wiki/entities/万油通.md` → `[[数字化能源管理]]`
- `wiki/entities/万贸达.md` → `[[AI驱动]]`

### 缺失实体页（被提及 >= 3 次却无页, 5）
> 提示：可写一个 heal 脚本自动补建这些实体页。
- `[[AI驱动]]`
- `[[智能匹配]]`
- `[[优选熟车]]`
- `[[企业钱包]]`
- `[[议价模式]]`

### 稀疏页（出链 < 2, 4）
- `wiki/concepts/品质消费品.md` — 出链 1：`[[万联鲸禧]]`
- `wiki/concepts/双轨会员制.md` — 出链 1：`[[万联鲸禧]]`
- `wiki/concepts/产地直溯.md` — 出链 1：`[[万联鲸禧]]`
- `wiki/concepts/助农.md` — 出链 1：`[[万联鲸禧]]`

## 图感知问题

> 未找到 graph/graph.json，跳过图感知检查。先构建知识图谱再巡检可获得更全的结论。

---

## 语义巡检（deepseek-v4）

## Contradictions
No direct factual contradictions were found among the 20 pages sampled. All platforms and concepts reference each other consistently (e.g., 万油通’s 20000+ stations, 易达宝’s 400万+ capacity, the 5.2% fuel cost reduction case). However, one potential tension exists between the claims of a “unified” ecosystem and the absence of described cross-platform technical or membership integration:

- **Overview** presents the four platforms as part of a “统一的产品矩阵与交叉导流” and a “全链条数字化服务版图,” while **concept pages** (e.g., 集采服务, 物贸一体化) only describe loose, directional synergies (e.g., 万油通 “can” serve 易达宝’s drivers, 万贸达 “can” embed 易达宝). No concrete integration mechanics (e.g., shared login, unified wallet, data pipeline) are documented, which creates an implicit contradiction between the claimed tight integration and the actual vague descriptions.

*Recommendation:* Clarify the integration depth with specific mechanisms, or tone down the unification claim.

## Stale Content
- **Uniform future `last_updated` dates**: All pages show `last_updated: 2026-07-05`. This is either a future date (assuming year ≥2025) or a placeholder, which makes freshness unverifiable. None of the pages cite original source publication or statistics vintage (e.g., “20000+ sites” – as of when?). Without source timestamps, this wiki cannot demonstrate currency.
- **Thin concept definitions**: For example, **品质消费品** is a one-paragraph placeholder with no evolving standards, no reference to recent consumer protection regulations, and no mention of any certification body. If this page has remained untouched since mid-2026, it likely doesn’t reflect newer industry quality frameworks (e.g., updated organic/provenance standards).
- **易达宝’s “0会员费/0抽佣” claim** (Overview) is binary and static; if the business model shifts to include premium/value-added fees, this page would be immediately stale, yet no monetization evolution is discussed.

*Recommendation:* Add `source_date` or `data_as_of` fields, and institute periodic review against primary documentation.

## Data Gaps & Suggested Sources
1. **Corporate structure & ownership**
   - Gap: “万联母品牌或控股公司组织架构” is listed as 待探索 in Overview. No entity chart, no regulatory relationships.
   - Suggested sources: National Enterprise Credit Information Publicity System (中国国家企业信用信息公示系统), Wanlian’s own “About Us” filings, industry association directories.

2. **Cross-platform membership & authentication**
   - Gap: Overview mentions “跨平台会员体系” unexplored; 双轨会员制 only applies to 万联鲸禧. No shared ID or wallet across 万油通, 万贸达, 易达宝.
   - Suggested sources: Wanlian’s developer portal API docs (if any), privacy policies, unified account terms of service.

3. **Financial product parameters**
   - Gap: 订单e贷 mentions “低利率、高额度、放款快” but gives no APRs, loan-to-value ratios, default rates, or total credit issued.
   - Suggested sources: Wandaoda platform’s financial disclosure page, partner bank product sheets, P2P lending regulatory filings if applicable.

4. **AI performance metrics**
   - Gap: AI外呼 and 多智能体协同 claim efficiency improvements (e.g., “调度找车效率提升50%”) but lack precision/recall, call success rate, fallback-to-human ratio, or model drift monitoring.
   - Suggested sources: Wanlian AI product white papers, third-party benchmark reports, driver satisfaction surveys.

5. **云仓 network scale & technology**
   - Gap: Mentions “物联网、区块链” but no warehouse count, geographic coverage, sensor types, blockchain platform (e.g., Hyperledger, AntChain), or audit trail details.
   - Suggested sources: Wandaoda’s annual operations report, logistics partner site lists, blockchain explorer references (if public).

6. **能源补给 energy mix**
   - Gap: 万油通 claims 油、气、电 but gives no split (percent diesel vs. electricity vs. LNG) or growth trends in new energy charging.
   - Suggested sources: Internal fleet energy reports, China Petrol Station Association data, new energy vehicle infrastructure white papers.

7. **万联鲸禧 supplier & catalog depth**
   - Gap: Describes “产业源头直供” and “地标特产” but no supplier count, SKU breadth, or sales volume. No data on “助农” impact.
   - Suggested sources: Marketplace seller registration pages, annual “助农” impact summaries, third-party e-commerce tracking (e.g., Analysys, EO Intelligence).

8. **Regulatory & compliance scope beyond tax**
   - Gap: 税务合规 covers VAT invoicing, but no data on fuel trading licenses, data privacy (PIPL compliance), logistics platform liability, or financial market registration.
   - Suggested sources: Platform legal notices, ICP filings, financial regulatory licenses (if needed for 供应链金融).

## Concepts Needing More Depth
- **云仓**: Lacks specification of IoT data types (temperature, humidity, weight), smart contract logic for automatic lien/release, integration with third-party WMS (e.g., SAP, Blue Yonder).
- **仓贸融一体化**: Missing process flow diagrams detailing how a trade triggers a finance request, risk control thresholds (e.g., LTV ratios), and dispute resolution mechanisms.
- **数字化能源补给综合服务平台**: No technical architecture (microservices, CDN for peak-hour pricing), API economy for station onboarding, or offline fallback when connectivity drops.
- **集采服务**: Static 5.2% case study; needs demand aggregation algorithms, dynamic pricing models, contract templates, and supplier management lifecycle.
- **品质消费品**: No quality tiers, testing lab partnerships, organic/geographical indication certifications, or return/after-sales SLAs.
- **物流撮合交易**: No explanation of “议价模式” negotiation rounds, timeouts, or deadlock resolution; 双向信用评级 lacks dimension details (punctuality, damage rate, dispute ratio).
- **电子油卡**: Needs mention of anti-fraud measures (geofencing, license-plate matching), offline QR-code fallback, and fleet card API for TMS integration.
- **积分体系**: Absence of point economy (earn rate per RMB, expire policy, partner redemption catalog), gamification elements, or cross-platform point pooling.
- **双轨会员制**: Unclear how enterprise admin manages individual sub-accounts, privacy walls between personal and corporate consumption data, and invoicing split.
- **产地直溯**: Lacks traceability proof (e.g., blockchain hash, QR code scan journey), audit by third-party certifiers (SGS, China Certification & Inspection Group), and supply chain mapping visualization.
- **AI外呼**: Needs detail on NLP stack (intent classification, entity extraction), accent/dialect support, compliance with do-not-call regulations, and human takeover thresholds.
- **多智能体协同**: Missing topology (hierarchical vs. flat negotiation), message protocol (FIPA-ACL, gRPC), agent specialization (e.g., pricing bot, news scraper), and reinforcement learning feedback loop.
- **能源加注管理**: Gaps in IoT device integration (pump controllers, level sensors), edge computing for offline scenarios, and integration with station POS systems.
- **供应链金融**: No mention of risk model (supplier credit scoring, dynamic limit adjustment), data sources beyond platform (ERP, tax filings), or cross-border trade finance.
- **物贸一体化**: Needs API contract examples, minimal message dataset, and handling of liability when carrier damages trade goods during transit.
- **企业员工福利采购**: Lacks product personalization, budget management dashboards, tax-optimized benefit compliance (免税额度), and integration with existing HR/benefits platforms (e.g., WeCom salary features).