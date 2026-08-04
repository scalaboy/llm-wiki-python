"""Demo: HTTP GET 拉取企业微官网内容。

等价 curl:
    curl --location --request GET \\
      'https://test-cioh.wanlianyida.com/gateway/portal/v1/companies/12/content' \\
      --header 'Authorization: <token>'

运行:
    python -m service.demo_company_content_http
    python -m service.demo_company_content_http --company-id 12
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import requests

DEFAULT_BASE_URL = "https://test-cioh.wanlianyida.com/gateway/portal/v1"
DEFAULT_COMPANY_ID = 12
DEFAULT_AUTHORIZATION = "09c3aef7-4e43-4071-8c4f-2602cf6b7dfe.d0001101aa"
DEFAULT_TIMEOUT_SEC = 30.0


def fetch_company_content(
    company_id: int,
    authorization: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """GET /companies/{company_id}/content"""
    url = f"{base_url.rstrip('/')}/companies/{company_id}/content"
    headers = {"Authorization": authorization.strip()}

    response = requests.get(url, headers=headers, timeout=timeout_sec, allow_redirects=True)
    response.raise_for_status()

    try:
        payload: Any = response.json()
    except ValueError:
        payload = {"raw_text": response.text}

    if not isinstance(payload, dict):
        return {"data": payload}

    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Demo: 拉取企业微官网 content 接口")
    parser.add_argument(
        "--company-id",
        type=int,
        default=int(os.getenv("COMPANY_CONTENT_COMPANY_ID", DEFAULT_COMPANY_ID)),
        help=f"企业 ID，默认 {DEFAULT_COMPANY_ID}",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("COMPANY_CONTENT_BASE_URL", DEFAULT_BASE_URL),
        help="Portal API 根路径",
    )
    parser.add_argument(
        "--authorization",
        default=os.getenv("COMPANY_CONTENT_AUTHORIZATION", DEFAULT_AUTHORIZATION),
        help="Authorization 请求头（不含 Bearer 前缀）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("COMPANY_CONTENT_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC)),
        help="请求超时秒数",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    print(f"GET {args.base_url.rstrip('/')}/companies/{args.company_id}/content")
    try:
        result = fetch_company_content(
            company_id=args.company_id,
            authorization=args.authorization,
            base_url=args.base_url,
            timeout_sec=args.timeout,
        )
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        body = (exc.response.text or "")[:500] if exc.response is not None else ""
        print(f"HTTP 错误: status={status}\n{body}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"请求失败: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
