> 🌐 [中文版](./README_zh.md)

# Aira Agent WebPortal

An enterprise intelligent Q&A service powered by **DeepSeek LLM**. Automatically transforms enterprise documents (PDF / DOCX / XLSX / PPTX, etc.) into a structured Wiki knowledge base, delivering streaming conversational Q&A, knowledge graph construction, and health inspection capabilities.

---

> **About the LLM Wiki Methodology**
>
> This project is built on the [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) methodology proposed by former Tesla AI Director **Andrej Karpathy**. The core insight: rather than re-retrieving answers from raw documents each time, the LLM maintains a structured, cross-linked Markdown Wiki — knowledge is compiled once, and queries synthesize answers directly from the curated knowledge base. Every time a new document is ingested, the LLM automatically updates entity pages, concept pages, and cross-references, allowing the knowledge base to accumulate and refine over time.
>
> This methodology is now live in our core products, and its real-world results far exceed traditional RAG approaches. Since it works well, we're open-sourcing the code. The repo also includes [Karpathy's original document](karpathy-llm-wiki.md) for reference.

---

## Getting Started

Three steps to run an enterprise knowledge base Q&A system:

### 1. Drop Documents into raw/

Place enterprise documents (PDF, DOCX, PPTX, Excel, Markdown, etc.) into the `raw/` directory:

```bash
cp ~/Downloads/product-manual.pdf raw/
cp ~/Downloads/pricing.xlsx raw/
```

### 2. Build the Wiki

Run `getwiki.py` — the LLM will automatically decompose documents into a structured knowledge base:

```bash
# Ingest a single document
python baseAtoml/getwiki.py raw/product-manual.pdf

# Or ingest everything under raw/ at once
python baseAtoml/getwiki.py raw/

# Check ingestion status
python baseAtoml/getwiki.py --status
```

After execution, `wiki/` will contain `index.md`, `overview.md`, `sources/`, `entities/`, `concepts/`, and more.

### 3. Start the Q&A Service

```bash
python baseAtoml/online.py --port 8000
```

Open your browser to `http://localhost:8000/` and start asking questions against the Wiki knowledge base.

API access:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "What are your core products?"}'
```

---

> **Prerequisites**: You need to configure your DeepSeek API key in `env/llm`. See the [Environment Configuration](#environment-configuration) section below.

---

### Full Deployment: python app.py

The workflow above is for quick local experimentation. For a **multi-enterprise, multi-user** production service, launch `app.py`. It automatically builds a Wiki knowledge base per enterprise and provides streaming Q&A on top of it.

#### Architecture

```
Client ──→ app.py ──→ RabbitMQ ──→ addComDocFromMQ.py (background Wiki builder)
              │                           │
              ├── MySQL (chat history)    └── maindir/<companyId>/ (enterprise wiki)
              │
              └── DeepSeek API (streaming answers)
```

Two core APIs:

- **`/api/addCompanyDoc`** — Submit enterprise document URL → enqueue → async download, convert, LLM-generated Wiki
- **`/api/chatbywiki`** — User question → query MySQL chat history → retrieve enterprise wiki → DeepSeek streaming answer

#### Step 1: Create Config Files in env/

`app.py` depends on **MySQL** (chat history) and **RabbitMQ** (async document processing). Create the corresponding config files under `env/`.

Directory structure:

```
env/
├── llm              # LLM API key (also used by getwiki.py / online.py)
├── env_test         # Test environment
├── env_uat          # UAT environment
└── env_prod         # Production environment (create as needed)
```

**`env/llm`** (LLM configuration):

```ini
# DeepSeek official API
deepseek_key=sk-xxxxxxxxxxxxxxxx
deepseek_url=https://api.deepseek.com

# 360 proxy API (fallback)
360_key=xxxxxxxx
360Url=https://xxxxxxxx
```

**`env/env_test`** (example for test environment):

```ini
# MySQL database configuration
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DB=agent

# RabbitMQ configuration
MQ_HOST=127.0.0.1
MQ_PORT=5672
MQ_USERNAME=admin
MQ_PASSWORD=your_password
MQ_QUEUE=addCompanyDoc_queue
```

#### Step 2: Start the Service

```bash
# Set the environment variable to select a config profile
APP_ENV=test python app.py
```

Once running, submit enterprise documents and start Q&A via the API.

#### Step 3: Start the MQ Consumer (Wiki Builder Worker)

`app.py` only handles request intake and queueing. The actual Wiki construction is done by the consumer:

```bash
APP_ENV=test python utils/addComDocFromMQ.py
```

#### Quick Verification

```bash
# 1. Submit an enterprise document (Wiki builds in the background)
curl "http://localhost:8000/api/addCompanyDoc?user=admin&companyId=c001&companyName=ExampleCorp&docAddress=https://example.com/product.pdf"

# 2. Ask a question against the wiki
curl "http://localhost:8000/api/chatbywiki?question=What+products+do+you+have&userid=u001&companyid=c001&companyname=ExampleCorp"
```

---

## Project Structure

```
llm-wiki-python/
├── app.py                    # Main FastAPI service (portal API)
├── Dockerfile                # Docker image build
├── deployment.yaml           # Kubernetes Deployment + Service
│
├── baseAtoml/                # Core: Wiki engine + LLM call foundation
│   ├── llmdeepseek.py        #   DeepSeek official API client (OpenAI-compatible)
│   ├── llmbase.py            #   360-proxy DeepSeek client (fallback)
│   ├── online.py             #   Online Q&A service (standalone FastAPI + HTML UI)
│   ├── getwiki.py            #   Document ingestion → Wiki page generation
│   ├── getanswer.py          #   CLI Q&A tool (retrieve + synthesize)
│   ├── getgraph.py           #   Knowledge graph construction (vis.js visualization)
│   └── check.py              #   Wiki health inspection (dead links / orphans / semantic conflicts)
│
├── utils/                    # Utility layer
│   ├── env_config.py         #   Multi-environment config loader (test / uat / prod)
│   ├── addComDocFromMQ.py    #   RabbitMQ consumer: async enterprise Wiki creation
│   ├── select.py             #   MySQL query (chat history)
│   ├── insertTable.py        #   MySQL write (document records / user questions)
│   └── createTable.py        #   Database table creation
│
├── tools/                    # Auxiliary scripts
│   ├── Basemq.py             #   RabbitMQ base operations
│   ├── getCompany.py         #   Enterprise info retrieval
│   ├── getproduct.py         #   Product info retrieval
│   ├── getxls2table.py       #   Excel → database import
│   ├── fillAgentDesc.py      #   Agent description filler
│   └── ...                   #   Other data integration tools
│
├── wiki/                     # Built-in knowledge base (general)
│   ├── index.md              #   Index page
│   ├── overview.md           #   Global overview
│   ├── concepts/             #   Concept pages
│   ├── entities/             #   Entity pages
│   └── sources/              #   Source document pages
│
├── maindir/                  # Enterprise-specific Wikis (K8s PVC mount)
│   └── <companyId>/          #   Isolated directory per enterprise
│       ├── index.md
│       ├── overview.md
│       ├── sources/
│       ├── entities/
│       └── concepts/
│
├── graph/                    # Knowledge graph output
│   ├── graph.json            #   Graph data
│   └── graph.html            #   Interactive visualization
│
├── thirdPart/                # Upstream reference framework (ingest / query, etc.)
├── env/                      # Environment configuration files
├── assets/                   # Static assets (baseAgent.json, etc.)
├── htmls/                    # Frontend pages
├── raw/                      # Raw document storage
└── test/                     # Test scripts
```

---

## Core Capabilities

### 1. Enterprise Wiki Auto-Construction

Upload enterprise documents and they are automatically transformed into a structured Wiki knowledge base, with a multi-format, multi-layer parsing fallback strategy.

```bash
# Ingest a single document
python baseAtoml/getwiki.py raw/product-manual.pdf

# Batch ingest a directory
python baseAtoml/getwiki.py raw/enterprise-product-manual/

# Force re-ingestion of changed documents
python baseAtoml/getwiki.py raw/ --force

# View ingestion manifest
python baseAtoml/getwiki.py --status
```

**Supported document formats**: PDF / DOCX / XLSX / XLS / PPTX / HTML / EPUB / TXT / CSV / JSON / XML / YAML / Markdown, and more.

**Conversion fallback chain** (PDF example): `pdftotext → PyPDF2 → markitdown` — each layer falls back automatically on failure, ensuring maximum compatibility.

### 2. Enterprise Broker Q&A

Streaming conversational service based on enterprise-specific Wikis, automatically retrieving relevant pages and synthesizing answers.

```bash
# Start the standalone Q&A service
python baseAtoml/online.py --port 8000

# Open the built-in chat interface
open http://localhost:8000/
```

API endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Non-streaming Q&A, returns full answer |
| `/chat/stream` | POST | SSE streaming Q&A, pushes token by token |
| `/health` | GET | Health check |

### 3. Knowledge Graph

Automatically construct a knowledge graph from Wiki pages, with interactive visualization.

```bash
# Build the graph (includes semantic inference)
python baseAtoml/getgraph.py

# Build and auto-open browser
python baseAtoml/getgraph.py --open

# Include a health report
python baseAtoml/getgraph.py --report --save
```

The graph includes:
- **Deterministic edges**: Parsed from `[[wikilink]]` syntax
- **Semantic inference edges**: LLM-analyzed implicit relationships
- **Louvain community detection**: Automatic knowledge cluster discovery

### 4. Wiki Health Inspection

Periodically check knowledge base health, identifying structural and semantic issues.

```bash
# Full inspection (includes LLM semantic analysis)
python baseAtoml/check.py

# Deterministic-only checks (cheap, can run frequently)
python baseAtoml/check.py --no-llm

# Output JSON for automated consumption
python baseAtoml/check.py --json

# Save the inspection report
python baseAtoml/check.py --save
```

Inspection items:
- Empty pages / stub pages / orphan pages / broken links
- Missing entity pages (frequently referenced but no standalone page)
- Hub stubs, fragile bridges, isolated communities (requires graph.json)
- LLM semantic: contradictions, stale content, data gaps

---

## Main Service API (app.py)

`app.py` is the primary FastAPI service deployed on Kubernetes, aggregating the core interfaces needed by the portal.

### Endpoint Overview

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/testfirst` | GET | Connectivity test |
| `/api/addCompanyDoc` | GET | Submit enterprise doc → enqueue → async Wiki build |
| `/api/addCompanyDoc/status/{task_id}` | GET | Query async task status |
| `/api/getDataByWanmol` | GET | Paginated query of agent base info (MySQL) |
| `/api/getDataByWanmol1` | GET | Return baseAgent.json (static) |
| `/api/chatbywiki` | GET | **Enterprise Wiki streaming Q&A** (SSE, multi-turn) |

### `/api/chatbywiki` Flow

```
User question → read chat history (MySQL userComQuest) → write current question
              → retrieve enterprise wiki (maindir/<companyId>/) → build prompt
              → DeepSeek streaming generation → SSE response + source page references
```

### `/api/addCompanyDoc` Flow

```
Receive request → enqueue RabbitMQ (persistent) → async background:
           ① Download document
           ② Multi-strategy format conversion
           ③ LLM generates Wiki pages (index / overview / sources / entities / concepts)
           ④ Code-level completeness guard (rebuild index.md + append coverage manifest)
```

---

## Deployment

### Environment Configuration

Select a configuration profile via the `APP_ENV` environment variable:

| APP_ENV | Config File |
|---------|-------------|
| `test` | `env/env_test` |
| `uat` | `env/env_uat` |
| `prod` | `env/env_prod` |

Each env file must contain:

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

LLM configuration goes in `env/llm`:

```ini
# DeepSeek official API (used by baseAtoml/llmdeepseek.py)
deepseek_key=sk-xxx
deepseek_url=https://api.deepseek.com

# 360 proxy API (used by baseAtoml/llmbase.py)
360_key=xxx
360Url=https://xxx
```

### Docker Deployment

```bash
docker build --build-arg ENV=test -t aira-agent-webportal .
docker run -p 8000:8000 \
  -e APP_ENV=test \
  -v $(pwd)/maindir:/app/maindir \
  aira-agent-webportal
```

### Kubernetes Deployment

```bash
# Update image reference, then apply
kubectl apply -f deployment.yaml
```

The `maindir/` directory is mounted via PersistentVolumeClaim to ensure enterprise Wiki data persistence.

### MQ Consumer (Wiki Builder Worker)

```bash
# Persistent consumption
python utils/addComDocFromMQ.py

# Consume a single message (debugging)
APP_ENV=test python utils/addComDocFromMQ.py --once
```

---

## Dependencies

Core dependencies are listed in the `Dockerfile`. Key ones include:

- **FastAPI** + **Uvicorn** — Web framework
- **openai** — DeepSeek API calls (OpenAI-compatible interface)
- **pymysql** — MySQL connector
- **pika** — RabbitMQ client
- **PyPDF2** / **python-docx** / **openpyxl** / **xlrd** — Document parsing
- **markitdown** — Universal format conversion
- **networkx** — Knowledge graph community detection
- **langgraph-checkpoint-mysql** — LangGraph state persistence

The `Dockerfile` is configured with Alibaba Cloud mirror sources for faster installation.

<img width="1203" height="1683" alt="82438833dd466ac95b2fd4a2f74f59d5" src="https://github.com/user-attachments/assets/53511db0-c52a-4382-a167-a4601da76114" />

---

## Dual LLM Backends

The project provides two LLM call foundations — choose as needed:

| Module | API Source | Default Model | Use Case |
|--------|-----------|---------------|----------|
| `baseAtoml/llmdeepseek.py` | DeepSeek official | `deepseek-chat` / `deepseek-reasoner` | Q&A, Wiki generation, streaming output |
| `baseAtoml/llmbase.py` | 360 proxy | `deepseek/deepseek-v4-pro` | Graph semantic inference, health inspection |

Both share the same interface: `call_llm(prompt, ...)` and `call_llm_stream(prompt, ...)`. Switching only requires changing the import.

---

## Related Projects

The `thirdPart/` directory contains upstream reference implementations (in the [llm-wiki](https://github.com/anthropics/llm-wiki) style framework). This project adds:

- Multi-enterprise isolated Wiki directory structure (`maindir/<companyId>/`)
- RabbitMQ async document processing pipeline
- MySQL chat history persistence
- Multi-strategy document parsing fallback chain
- Kubernetes deployment support
- Agent information management API
