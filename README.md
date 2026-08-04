# Aira Agent WebPortal

基于 **DeepSeek LLM** 的企业智能体问答服务。将企业文档（PDF / DOCX / XLSX / PPTX 等）自动转化为结构化 Wiki 知识库，提供流式对话问答、知识图谱构建与巡检能力。

---

## 从这里开始

三步跑通一个企业知识库问答系统：

### 1. 把文档放进 raw/

将企业文档（PDF、DOCX、PPTX、Excel、Markdown 等）放入 `raw/` 目录：

```bash
cp ~/Downloads/企业产品手册.pdf raw/
cp ~/Downloads/报价单.xlsx raw/
```

### 2. 构建 Wiki

运行 `getwiki.py`，LLM 会自动把文档拆解为结构化知识库：

```bash
# 摄取单个文档
python baseAtoml/getwiki.py raw/企业产品手册.pdf

# 或一次摄取 raw/ 下所有文档
python baseAtoml/getwiki.py raw/

# 查看已摄取的文档清单
python baseAtoml/getwiki.py --status
```

执行后会在 `wiki/` 下生成 `index.md`、`overview.md`、`sources/`、`entities/`、`concepts/` 等页面。

### 3. 启动问答服务

```bash
python baseAtoml/online.py --port 8000
```

打开浏览器访问 `http://localhost:8000/`，即可在对话界面中基于 Wiki 知识库提问。

API 方式调用：

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "你们的核心产品有哪些？"}'
```

---

> **前置条件**：需要在 `env/llm` 中配置 DeepSeek API 密钥，详见下方 [环境配置](#环境配置) 一节。

---

### 完整项目化：python app.py

上面的流程适合本地快速体验。如果需要**多企业、多用户**的完整服务，启动 `app.py` 即可。它会自动为每个企业构建 Wiki 知识库，并基于知识库提供流式问答。

#### 架构说明

```
客户端 ──→ app.py ──→ RabbitMQ ──→ addComDocFromMQ.py（后台构建 Wiki）
              │                           │
              ├── MySQL（对话历史）        └── maindir/<companyId>/（企业 wiki）
              │
              └── DeepSeek API（流式回答）
```

两个核心 API：

- **`/api/addCompanyDoc`** — 提交企业文档 URL → 入 MQ → 后台异步下载、转换、LLM 生成 Wiki
- **`/api/chatbywiki`** — 用户提问 → 查 MySQL 对话历史 → 检索企业 wiki → DeepSeek 流式回答

#### 第一步：在 env/ 下创建配置文件

`app.py` 依赖 **MySQL**（存储对话历史）和 **RabbitMQ**（异步文档处理）。在 `env/` 下创建对应的配置文件。

目录结构：

```
env/
├── llm              # LLM API 密钥（getwiki.py / online.py 也需要）
├── env_test         # 测试环境
├── env_uat          # UAT 环境
└── env_prod         # 生产环境（按需创建）
```

**`env/llm`**（LLM 配置）：

```ini
# DeepSeek 官方 API
deepseek_key=sk-xxxxxxxxxxxxxxxx
deepseek_url=https://api.deepseek.com

# 360 代理 API（备用）
360_key=xxxxxxxx
360Url=https://xxxxxxxx
```

**`env/env_test`**（以 test 环境为例）：

```ini
# MySQL 数据库配置
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DB=agent

# RabbitMQ 配置
MQ_HOST=127.0.0.1
MQ_PORT=5672
MQ_USERNAME=admin
MQ_PASSWORD=your_password
MQ_QUEUE=addCompanyDoc_queue
```

#### 第二步：启动服务

```bash
# 设置环境变量，指定使用哪个配置
APP_ENV=test python app.py
```

服务启动后，通过 API 提交企业文档、进行问答。

#### 第三步：启动 MQ 消费者（Wiki 构建 Worker）

`app.py` 只负责接收请求和入队列，真正的 Wiki 构建由消费者完成：

```bash
APP_ENV=test python utils/addComDocFromMQ.py
```

#### 快速验证

```bash
# 1. 提交一个企业文档（后台自动构建 wiki）
curl "http://localhost:8000/api/addCompanyDoc?user=admin&companyId=c001&companyName=示例公司&docAddress=https://example.com/product.pdf"

# 2. 基于 wiki 提问
curl "http://localhost:8000/api/chatbywiki?question=你们有哪些产品&userid=u001&companyid=c001&companyname=示例公司"
```

---

## 项目结构

```
llm-wiki-python/
├── app.py                    # 主 FastAPI 服务（门户 API）
├── Dockerfile                # Docker 镜像构建
├── deployment.yaml           # Kubernetes Deployment + Service
│
├── baseAtoml/                # 核心：Wiki 引擎 + LLM 调用底座
│   ├── llmdeepseek.py        #   DeepSeek 官方 API 客户端（OpenAI 兼容）
│   ├── llmbase.py            #   360 代理版 DeepSeek 客户端（备用）
│   ├── online.py             #   在线问答服务（独立 FastAPI + HTML 界面）
│   ├── getwiki.py            #   文档摄取 → Wiki 页面生成
│   ├── getanswer.py          #   CLI 问答工具（检索+合成）
│   ├── getgraph.py           #   知识图谱构建（vis.js 可视化）
│   └── check.py              #   Wiki 巡检（坏链/孤儿页/语义矛盾）
│
├── utils/                    # 工具层
│   ├── env_config.py         #   多环境配置加载（test / uat / prod）
│   ├── addComDocFromMQ.py    #   RabbitMQ 消费者：异步创建企业 Wiki
│   ├── select.py             #   MySQL 查询（对话历史）
│   ├── insertTable.py        #   MySQL 写入（文档记录/用户问题）
│   └── createTable.py        #   数据库建表
│
├── tools/                    # 辅助脚本集
│   ├── Basemq.py             #   RabbitMQ 基础操作
│   ├── getCompany.py         #   企业信息获取
│   ├── getproduct.py         #   产品信息获取
│   ├── getxls2table.py       #   Excel → 数据库导入
│   ├── fillAgentDesc.py      #   智能体描述填充
│   └── ...                   #   其他数据对接工具
│
├── wiki/                     # 内置知识库（通用）
│   ├── index.md              #   索引页
│   ├── overview.md           #   全局概述
│   ├── concepts/             #   概念页
│   ├── entities/             #   实体页
│   └── sources/              #   来源文档页
│
├── maindir/                  # 企业专属 Wiki（K8s PVC 挂载）
│   └── <companyId>/          #   每个企业独立目录
│       ├── index.md
│       ├── overview.md
│       ├── sources/
│       ├── entities/
│       └── concepts/
│
├── graph/                    # 知识图谱输出
│   ├── graph.json            #   图谱数据
│   └── graph.html            #   交互式可视化
│
├── thirdPart/                # 上游参考框架（ingest / query 等）
├── env/                      # 环境配置文件
├── assets/                   # 静态资源（baseAgent.json 等）
├── htmls/                    # 前端页面
├── raw/                      # 原始文档存放
└── test/                     # 测试脚本
```

---

## 核心能力

### 1. 企业 Wiki 自动构建

上传企业文档，自动转化为结构化 Wiki 知识库，支持多种格式的多层解析降级策略。

```bash
# 摄取单个文档
python baseAtoml/getwiki.py raw/product-manual.pdf

# 批量摄取目录
python baseAtoml/getwiki.py raw/万联易达产品手册-初版/

# 强制重摄已变更的文档
python baseAtoml/getwiki.py raw/ --force

# 查看摄取清单
python baseAtoml/getwiki.py --status
```

**支持的文档格式**：PDF / DOCX / XLSX / XLS / PPTX / HTML / EPUB / TXT / CSV / JSON / XML / YAML / Markdown 等。

**转换降级链**（以 PDF 为例）：`pdftotext → PyPDF2 → markitdown`，每一层失败自动降级，确保最大兼容性。

### 2. 企业经纪人问答

基于企业专属 Wiki 的流式对话服务，自动检索相关页面并合成回答。

```bash
# 启动独立问答服务
python baseAtoml/online.py --port 8000

# 访问内置对话界面
open http://localhost:8000/
```

API 端点：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/chat` | POST | 非流式问答，返回完整答案 |
| `/chat/stream` | POST | SSE 流式问答，逐 token 推送 |
| `/health` | GET | 健康检查 |

### 3. 知识图谱

从 Wiki 页面自动构建知识图谱，支持交互式可视化。

```bash
# 构建图谱（含语义推断）
python baseAtoml/getgraph.py

# 构建后自动打开浏览器
python baseAtoml/getgraph.py --open

# 附带健康报告
python baseAtoml/getgraph.py --report --save
```

图谱包含：
- **确定性边**：从 `[[wikilink]]` 解析
- **语义推断边**：LLM 分析隐含关系
- **Louvain 社区检测**：自动发现知识聚类

### 4. Wiki 巡检

定期检查知识库健康度，识别结构和语义问题。

```bash
# 完整巡检（含 LLM 语义分析）
python baseAtoml/check.py

# 仅确定性检查（便宜、可高频运行）
python baseAtoml/check.py --no-llm

# 输出 JSON 供自动化消费
python baseAtoml/check.py --json

# 保存巡检报告
python baseAtoml/check.py --save
```

巡检项：
- 空页 / 桩页 / 孤儿页 / 坏链
- 缺失实体页（被频繁引用但无独立页面）
- hub 桩、脆弱桥、孤立社区（需 graph.json）
- LLM 语义：矛盾、过时内容、数据缺口

---

## 主服务 API（app.py）

`app.py` 是部署在 Kubernetes 上的主 FastAPI 服务，聚合了门户所需的核心接口。

### 端点一览

| 端点 | 方法 | 说明 |
|------|------|------|
| `/testfirst` | GET | 连通性测试 |
| `/api/addCompanyDoc` | GET | 提交企业文档 → 入 MQ → 后台异步构建 Wiki |
| `/api/addCompanyDoc/status/{task_id}` | GET | 查询异步任务状态 |
| `/api/getDataByWanmol` | GET | 分页查询智能体基础信息（MySQL） |
| `/api/getDataByWanmol1` | GET | 返回 baseAgent.json（静态） |
| `/api/chatbywiki` | GET | **企业 Wiki 流式问答**（SSE，多轮对话） |

### `/api/chatbywiki` 流程

```
用户提问 → 读取历史对话(MySQL userComQuest) → 写入当前问题
         → 检索企业 wiki(maindir/<companyId>/) → 构建 prompt
         → DeepSeek 流式生成 → SSE 返回 + 推送来源页
```

### `/api/addCompanyDoc` 流程

```
接收请求 → 入 RabbitMQ（持久化） → 后台异步：
           ① 下载文档
           ② 多策略格式转换
           ③ LLM 生成 Wiki 页面（index / overview / sources / entities / concepts）
           ④ 代码级完整性兜底（重建 index.md + 追加覆盖清单）
```

---

## 部署

### 环境配置

通过 `APP_ENV` 环境变量选择配置：

| APP_ENV | 配置文件 |
|---------|----------|
| `test` | `env/env_test` |
| `uat` | `env/env_uat` |
| `prod` | `env/env_prod` |

每个 env 文件需包含：

```ini
# MySQL
MYSQL_HOST=xxx
MYSQL_PORT=3306
MYSQL_USER=xxx
MYSQL_PASSWORD=xxx
MYSQL_DB=xxx

# RabbitMQ
MQ_HOST=xxx
MQ_PORT=5672
MQ_USERNAME=xxx
MQ_PASSWORD=xxx
MQ_QUEUE=addCompanyDoc_queue
```

LLM 配置放在 `env/llm`：

```ini
# DeepSeek 官方 API（baseAtoml/llmdeepseek.py 使用）
deepseek_key=sk-xxx
deepseek_url=https://api.deepseek.com

# 360 代理 API（baseAtoml/llmbase.py 使用）
360_key=xxx
360Url=https://xxx
```

### Docker 部署

```bash
docker build --build-arg ENV=test -t aira-agent-webportal .
docker run -p 8000:8000 \
  -e APP_ENV=test \
  -v $(pwd)/maindir:/app/maindir \
  aira-agent-webportal
```

### Kubernetes 部署

```bash
# 替换镜像地址后 apply
kubectl apply -f deployment.yaml
```

`maindir/` 目录通过 PersistentVolumeClaim 挂载，确保企业 Wiki 数据持久化。

### MQ 消费者（Wiki 构建 Worker）

```bash
# 持续消费
python utils/addComDocFromMQ.py

# 只消费一条（调试用）
APP_ENV=test python utils/addComDocFromMQ.py --once
```

---

## 依赖

核心依赖见 `Dockerfile`，主要包括：

- **FastAPI** + **Uvicorn** — Web 框架
- **openai** — DeepSeek API 调用（OpenAI 兼容接口）
- **pymysql** — MySQL 连接
- **pika** — RabbitMQ 客户端
- **PyPDF2** / **python-docx** / **openpyxl** / **xlrd** — 文档解析
- **markitdown** — 通用格式转换
- **networkx** — 知识图谱社区检测
- **langgraph-checkpoint-mysql** — LangGraph 状态持久化

`Dockerfile` 中已配置阿里云镜像源加速安装。

---

## 两个 LLM 底座

项目提供两套 LLM 调用底座，按需选用：

| 模块 | API 来源 | 默认模型 | 适用场景 |
|------|----------|----------|----------|
| `baseAtoml/llmdeepseek.py` | DeepSeek 官方 | `deepseek-chat` / `deepseek-reasoner` | 问答、Wiki 生成、流式输出 |
| `baseAtoml/llmbase.py` | 360 代理 | `deepseek/deepseek-v4-pro` | 图谱语义推断、巡检 |

接口一致：`call_llm(prompt, ...)` 和 `call_llm_stream(prompt, ...)`，切换时只需改 import。

---

## 相关项目

`thirdPart/` 目录包含上游参考实现（[llm-wiki](https://github.com/anthropics/llm-wiki) 风格框架），本项目在此基础上增加了：

- 多企业隔离的 Wiki 目录结构（`maindir/<companyId>/`）
- RabbitMQ 异步文档处理管道
- MySQL 对话历史持久化
- 多策略文档解析降级链
- Kubernetes 部署支持
- 智能体信息管理 API
