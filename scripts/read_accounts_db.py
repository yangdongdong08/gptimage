#!/usr/bin/env python3
"""
只读查看 chatgpt2api SQLite 账号库。

默认读取: data/accounts.db
示例:
  python3 scripts/read_accounts_db.py
  python3 scripts/read_accounts_db.py --db data/accounts.db --only-with-password
  python3 scripts/read_accounts_db.py --format json
  python3 scripts/read_accounts_db.py --format tsv > /tmp/accounts.tsv

说明:
- 使用 SQLite read-only URI: file:<db>?mode=ro
- 只执行 SELECT，不会修改数据库
- accounts 表的完整账号信息保存在 data JSON 字段中
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("data/accounts.db")

BASE_COLUMNS = [
    "id",
    "email",
    "password",
    "status",
    "source_type",
    "type",
    "created_at",
    "last_used_at",
    "quota",
    "default_model_slug",
]

TOKEN_COLUMNS = ["access_token", "refresh_token", "id_token"]


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"DB 文件不存在: {db_path}")
    # resolve 后可避免相对路径里包含特殊字符导致 URI 歧义
    uri = f"file:{db_path.resolve()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def mask_secret(value: Any, keep_start: int = 8, keep_end: int = 6) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep_start + keep_end + 3:
        return "*" * len(text)
    return f"{text[:keep_start]}...{text[-keep_end:]}"


def load_accounts(db_path: Path) -> list[dict[str, Any]]:
    with connect_readonly(db_path) as con:
        cur = con.execute("SELECT id, data FROM accounts ORDER BY id")
        rows: list[dict[str, Any]] = []
        for row_id, raw_data in cur.fetchall():
            try:
                data = json.loads(raw_data)
            except Exception as exc:  # noqa: BLE001
                rows.append({"id": row_id, "_error": f"invalid json: {exc}"})
                continue
            if not isinstance(data, dict):
                rows.append({"id": row_id, "_error": "data is not object"})
                continue
            item = {"id": row_id, **data}
            rows.append(item)
        return rows


def normalize_rows(
    accounts: list[dict[str, Any]],
    *,
    include_tokens: bool,
    full_tokens: bool,
    mask_password: bool,
    only_with_password: bool,
) -> list[dict[str, Any]]:
    columns = [*BASE_COLUMNS, *(TOKEN_COLUMNS if include_tokens else [])]
    output: list[dict[str, Any]] = []
    for account in accounts:
        password = str(account.get("password") or "")
        if only_with_password and not password:
            continue
        row: dict[str, Any] = {}
        for col in columns:
            value = account.get(col, "")
            if col == "password" and mask_password:
                value = mask_secret(value, 3, 3)
            elif col in TOKEN_COLUMNS and not full_tokens:
                value = mask_secret(value)
            elif isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            row[col] = "" if value is None else value
        if account.get("_error"):
            row["error"] = account.get("_error")
        output.append(row)
    return output


def print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("没有匹配的账号记录")
        return
    columns = list(rows[0].keys())
    widths: dict[str, int] = {}
    for col in columns:
        widths[col] = max(len(col), *(len(str(row.get(col, ""))) for row in rows))
        widths[col] = min(widths[col], 48)

    def cell(value: Any, width: int) -> str:
        text = str(value or "")
        if len(text) > width:
            text = text[: max(0, width - 1)] + "…"
        return text.ljust(width)

    print("  ".join(cell(col, widths[col]) for col in columns))
    print("  ".join("-" * widths[col] for col in columns))
    for row in rows:
        print("  ".join(cell(row.get(col, ""), widths[col]) for col in columns))


def print_tsv(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].keys()), dialect="excel-tab")
    writer.writeheader()
    writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读查看 chatgpt2api 的 data/accounts.db 账号信息")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB 路径，默认: data/accounts.db")
    parser.add_argument(
        "--format",
        choices=("table", "json", "tsv"),
        default="table",
        help="输出格式，默认: table",
    )
    parser.add_argument("--only-with-password", action="store_true", help="只显示保存了 password 的账号")
    parser.add_argument("--mask-password", action="store_true", help="掩码显示 password")
    parser.add_argument("--include-tokens", action="store_true", help="额外显示 access/refresh/id token，默认掩码")
    parser.add_argument("--full-tokens", action="store_true", help="配合 --include-tokens 使用，完整显示 token")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        accounts = load_accounts(Path(args.db))
        rows = normalize_rows(
            accounts,
            include_tokens=args.include_tokens,
            full_tokens=args.full_tokens,
            mask_password=args.mask_password,
            only_with_password=args.only_with_password,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"读取失败: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif args.format == "tsv":
        print_tsv(rows)
    else:
        print_table(rows)
        print(f"\n共 {len(rows)} 条记录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
