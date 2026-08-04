#!/usr/bin/env python3
"""
test/test_addCompanyDoc.py — addCompanyDoc 全链路端到端测试

测试步骤：
  Step1  发布消息 → RabbitMQ（API 优先，不可达则直发 MQ）
  Step2  消费消息 → 下载文件 + LLM 生成 wiki
  Step3  验证 raw 文件 → maindir/raw/<companyId>/ 下是否保存了原始文件
  Step4  验证 wiki 文件 → maindir/<companyId>/ 下 wiki 目录结构

用法：
    python test/test_addCompanyDoc.py                # 完整测试（API → MQ → wiki）
    python test/test_addCompanyDoc.py --local         # 跳过 API，直发 MQ
    python test/test_addCompanyDoc.py --direct-wiki   # 绕过网络，直接调 wiki 构建
    python test/test_addCompanyDoc.py --api http://x  # 自定义 API 地址
"""

import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote

import pika

# ============================================================
#  配置
# ============================================================

API_BASE = "http://10.3.16.249:30309"
RABBITMQ_URL = "amqp://admin:bHw8JUmY9O4z@10.3.1.117:5672/"
QUEUE_NAME = "addCompanyDoc_queue"

REPO_ROOT = Path(__file__).resolve().parent.parent
MAINDIR = REPO_ROOT / "maindir"
RAW_DIR = MAINDIR / "raw"
CONSUMER_SCRIPT = REPO_ROOT / "utils" / "addComDocFromMQ.py"

PARAMS = {
    "user": "605115987536122376",
    "companyId": "60324",
    "companyName": "中国农业银行股份有限公司",
    "docAddress": "https://cdn.wanmol.com/enterprise_portal/10/70/20260708/ade54d45-9852-4099-920b-30c6db3605b7.pdf",
}

CID = PARAMS["companyId"]
WIKI_DIR = MAINDIR / CID
RAW_COMPANY_DIR = RAW_DIR / CID

WIKI_FILES = ["index.md", "overview.md", "log.md", ".wiki_meta.json"]
WIKI_DIRS = ["sources", "entities", "concepts"]

# ============================================================
#  辅助
# ============================================================

SEP = "=" * 60

_results: dict[str, bool] = {}


def mark(step: str, ok: bool):
    _results[step] = ok


def ok(msg: str):
    print(f"  ✅ {msg}")


def warn(msg: str):
    print(f"  ⚠️  {msg}")


def fail(msg: str):
    print(f"  ❌ {msg}")


# ============================================================
#  Step1  发布消息到 RabbitMQ
# ============================================================

def step1(api_base: str, local_mode: bool) -> str | None:
    """发布消息。API 优先，失败降级直发 MQ。返回 task_id。"""
    print(SEP)
    print("Step1  发布消息到 RabbitMQ")
    print(SEP)

    if not local_mode:
        query = urlencode(PARAMS, quote_via=quote)
        url = f"{api_base}/api/addCompanyDoc?{query}"
        print(f"  调 API: {url}")
        try:
            req = Request(url, method="GET")
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            print(f"  响应: retcode={data.get('retcode')}, task_id={data.get('task_id','?')}")
            if data.get("retcode") == 1:
                ok("API 调用成功")
                mark("Step1", True)
                return data["task_id"]
            else:
                warn(f"API retcode={data.get('retcode')}，降级直发 MQ")
        except Exception as e:
            warn(f"API 不可达 ({e})，降级直发 MQ")

    # 直发 RabbitMQ
    task_id = str(uuid.uuid4())[:8]
    msg = {
        "task_id": task_id,
        "user": PARAMS["user"],
        "companyId": PARAMS["companyId"],
        "companyName": PARAMS["companyName"],
        "docAddress": PARAMS["docAddress"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        print(f"  直发 MQ ({RABBITMQ_URL})")
        params = pika.URLParameters(RABBITMQ_URL)
        params.socket_timeout = 10
        conn = pika.BlockingConnection(params)
        ch = conn.channel()
        ch.queue_declare(queue=QUEUE_NAME, durable=True)
        ch.basic_publish(
            exchange="", routing_key=QUEUE_NAME,
            body=json.dumps(msg, ensure_ascii=False),
            properties=pika.BasicProperties(delivery_mode=2),
        )
        conn.close()
        ok(f"MQ 直发成功, task_id={task_id}")
        mark("Step1", True)
        return task_id
    except Exception as e:
        fail(f"MQ 不可达: {e}")
        mark("Step1", False)
        return None


# ============================================================
#  Step2  消费消息
# ============================================================

def step2() -> bool:
    """运行消费者，下载文件 + LLM 生成 wiki。"""
    print(f"\n{SEP}")
    print("Step2  消费 RabbitMQ 消息并生成 wiki")
    print(SEP)

    cmd = [sys.executable, str(CONSUMER_SCRIPT), "--once"]
    print(f"  执行: {' '.join(cmd)}")

    try:
        r = subprocess.run(cmd, cwd=str(REPO_ROOT),
                           capture_output=True, text=True, timeout=300)
        print(f"  exit={r.returncode}")

        keywords = ("ERROR", "FAIL", "wrote:", "wiki", "✅", "❌",
                     "下载", "pdftotext", "markitdown", "转换", "LLM", "完成")
        for line in r.stdout.splitlines():
            if any(k in line for k in keywords):
                print(f"    {line.strip()}")

        for line in r.stderr.splitlines():
            if any(k in line for k in ("ERROR", "FAIL", "Traceback")):
                print(f"    [stderr] {line.strip()}")

        ok_ = r.returncode == 0
        mark("Step2", ok_)
        if not ok_:
            warn("消费者返回非零退出码")
        return ok_
    except subprocess.TimeoutExpired:
        fail("消费者超时 (>5min)")
        mark("Step2", False)
        return False
    except Exception as e:
        fail(f"消费者异常: {e}")
        mark("Step2", False)
        return False


# ============================================================
#  Step3  验证 raw 文件
# ============================================================

def step3() -> bool:
    """验证 maindir/raw/<companyId>/ 下是否保存了原始文件。"""
    print(f"\n{SEP}")
    print(f"Step3  验证 raw 文件 ({RAW_COMPANY_DIR})")
    print(SEP)

    if not RAW_COMPANY_DIR.exists():
        warn("raw 目录不存在（可能下载失败或消费者未执行）")
        mark("Step3", False)
        return False

    raw_files = sorted(RAW_COMPANY_DIR.glob("*"))
    if not raw_files:
        warn("raw 目录为空，无下载文件")
        mark("Step3", False)
        return False

    ok_ = True
    for f in raw_files:
        if f.is_file():
            size = f.stat().st_size
            if size > 0:
                ok(f"{f.name}  ({size:,} bytes)")
            else:
                warn(f"{f.name}  (空文件)")
                ok_ = False

    mark("Step3", ok_)
    return ok_


# ============================================================
#  Step4  验证 wiki 文件
# ============================================================

def step4() -> bool:
    """验证 maindir/<companyId>/ 下 wiki 文件。"""
    print(f"\n{SEP}")
    print(f"Step4  验证 wiki 文件 ({WIKI_DIR})")
    print(SEP)

    if not WIKI_DIR.exists():
        fail(f"目录不存在: {WIKI_DIR}")
        mark("Step4", False)
        return False

    all_ok = True

    for fn in WIKI_FILES:
        fp = WIKI_DIR / fn
        if fp.exists() and fp.stat().st_size > 0:
            ok(f"{fn}  ({fp.stat().st_size:,} bytes)")
        else:
            fail(f"{fn} 缺失或为空")
            if fn != ".wiki_meta.json":
                all_ok = False

    for dn in WIKI_DIRS:
        dp = WIKI_DIR / dn
        if dp.is_dir():
            mds = list(dp.glob("*.md"))
            for m in mds:
                ok(f"{dn}/{m.name}  ({m.stat().st_size:,} bytes)")
            if not mds:
                ok(f"{dn}/ (空)")
        else:
            ok(f"{dn}/ (未创建)")

    # 内容预览
    idx = WIKI_DIR / "index.md"
    if idx.exists():
        c = idx.read_text(encoding="utf-8").strip()
        if c:
            print(f"\n  ── index.md 预览 ──")
            for line in c[:500].splitlines()[:12]:
                print(f"  {line}")
        else:
            fail("index.md 内容为空")
            all_ok = False

    ov = WIKI_DIR / "overview.md"
    if ov.exists():
        c = ov.read_text(encoding="utf-8").strip()
        if len(c) > 50:
            print(f"\n  ── overview.md 预览 ──")
            for line in c[:300].splitlines()[:8]:
                print(f"  {line}")
        elif len(c) > 0:
            warn(f"overview.md 内容过短 ({len(c)} 字符)")
        else:
            fail("overview.md 内容为空")
            all_ok = False

    mark("Step4", all_ok)
    return all_ok


# ============================================================
#  Direct-wiki（跳过网络）
# ============================================================

def run_direct_wiki() -> int:
    """直接调 create_company_wiki，绕过 API 和 MQ。"""
    print(SEP)
    print("  Direct-wiki 模式：直接构建 wiki")
    print(SEP)
    sys.path.insert(0, str(REPO_ROOT / "utils"))
    from addComDocFromMQ import create_company_wiki

    ok_ = create_company_wiki(CID, PARAMS["companyName"], PARAMS["docAddress"])
    mark("DirectWiki", ok_)

    if not ok_:
        print("\n❌ wiki 构建失败"); return 1

    step3()
    step4()
    return _summary()


# ============================================================
#  汇总
# ============================================================

def _summary() -> int:
    print(f"\n{SEP}")
    print("  测试结果")
    print(SEP)
    for step, ok_ in _results.items():
        status = "✅ PASS" if ok_ else "❌ FAIL"
        print(f"  {step:15s} {status}")
    all_pass = all(_results.values())
    print()
    if all_pass:
        ok("全部通过")
        return 0
    else:
        fail("部分步骤失败")
        return 2


# ============================================================
#  Main
# ============================================================

def main():
    args = sys.argv[1:]
    direct = "--direct-wiki" in args
    local = "--local" in args
    api_base = API_BASE

    for i, a in enumerate(args):
        if a == "--api" and i + 1 < len(args):
            api_base = args[i + 1]

    print(SEP)
    print("  addCompanyDoc 全链路端到端测试")
    if direct:
        print("  (direct-wiki 模式)")
    elif local:
        print("  (local 模式：跳过 API)")
    print(SEP)
    print(f"  企业: {PARAMS['companyName']} (ID={CID})")
    print(f"  文档: {PARAMS['docAddress']}")
    print()

    if direct:
        return run_direct_wiki()

    # Step1  发布消息
    tid = step1(api_base, local)
    if tid is None:
        print("\n❌ Step1 失败，终止测试")
        print("  提示: --local 跳过 API  /  --direct-wiki 绕过网络")
        return 1

    print("\n  等待 2s ...")
    time.sleep(2)

    # Step2  消费
    step2()

    # Step3  验证 raw
    step3()

    # Step4  验证 wiki
    step4()

    return _summary()


if __name__ == "__main__":
    sys.exit(main())
