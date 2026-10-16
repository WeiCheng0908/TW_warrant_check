"""上市公司基本資料 (t187ap03_L) → 代號↔名稱對照表。"""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

from .parser import loads, iter_loads, clean_text


def _read_file(path: str):
    with open(path, "rb") as f:
        return f.read()


def build_stock_map(rows: List[dict]) -> List[dict]:
    """從 t187ap03_L 解析出的 list[dict] 建立股票清單。

    欄位:
    - ``出表日期`` ROC
    - ``公司代號`` (例 "2330")
    - ``公司簡稱`` (例 "台積電")
    - ``公司名稱`` (全名)
    """
    out: List[dict] = []
    for row in rows:
        code = (row.get("公司代號") or "").strip()
        name = (row.get("公司簡稱") or "").strip()
        full = (row.get("公司名稱") or "").strip()
        if not code or not name:
            continue
        out.append({"code": code, "name": name, "full_name": full})
    # 用名稱當 fallback search key
    out.sort(key=lambda r: r["code"])
    return out


def load_stock_map(path: str) -> List[dict]:
    raw = _read_file(path)
    rows = list(iter_loads(raw))
    return build_stock_map(rows)


def save_stock_map(stocks: List[dict], dest: str) -> str:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    import json

    with open(dest, "w", encoding="utf-8") as f:
        json.dump(stocks, f, ensure_ascii=False)
    return dest


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="path to t187ap03_L json")
    ap.add_argument("--dest", required=True)
    args = ap.parse_args()
    rows = load_stock_map(args.src)
    save_stock_map(rows, args.dest)
    print(f"wrote {len(rows)} stocks -> {args.dest}")
