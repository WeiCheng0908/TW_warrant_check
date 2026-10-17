"""
Fetcher: 從 TWSE OpenAPI 抓資料並落地快取。

* :func:`fetch_to_file` — 串流下載單一 URL 到指定路徑，支援 retry。
* :func:`ensure_fresh` — 若快取不存在或過期就重新下載。
* :func:`fetch_all` — 抓兩個 warrant 端點 + 一個 stock 端點。
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (compatible; TWSE-Warrant-Finder/0.1; "
    "+https://github.com/local/twse-warrant-finder)"
)

WARRANT_BASE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap37_L"
WARRANT_TRADE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap42_L"
STOCK_BASE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"

# 上櫃 (TPEX) 端點：JSON 路徑在 openapi.twse.com.tw 沒有對應 _O，
# 政府資料開放平臺 (data.gov.tw) 把這些資料統一由 mopsfin.twse.com.tw 暴露。
# 跟上市端點是同樣的 t187apXX_Y 系列編號，只是：上市= _L、上櫃= _O.csv。
WARRANT_OTC_URL = "https://mopsfin.twse.com.tw/opendata/t187ap37_O.csv"
WARRANT_OTC_TRADE_URL = "https://mopsfin.twse.com.tw/opendata/t187ap42_O.csv"
STOCK_OTC_URL = "https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv"

DEFAULT_TIMEOUT = 60
DEFAULT_RETRIES = 3


def _request_with_retry(
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    user_agent: str = DEFAULT_UA,
    retries: int = DEFAULT_RETRIES,
    stream: bool = True,
) -> requests.Response:
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": user_agent, "Accept": "application/json"},
                stream=stream,
            )
            resp.raise_for_status()
            return resp
        except (requests.RequestException, requests.HTTPError) as exc:
            last_exc = exc
            if attempt >= retries:
                break
            time.sleep(2 ** (attempt - 1))
    # 全部 retry 都失敗
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts: {last_exc}")


def fetch_to_file(
    url: str,
    dest: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    user_agent: str = DEFAULT_UA,
    retries: int = DEFAULT_RETRIES,
) -> str:
    """下載 ``url`` 到 ``dest`` (覆寫)。

    回傳實際寫入的檔路徑。
    """
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    tmp = dest + ".part"
    resp = _request_with_retry(
        url,
        timeout=timeout,
        user_agent=user_agent,
        retries=retries,
        stream=True,
    )
    try:
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        os.replace(tmp, dest)
    finally:
        resp.close()
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return dest


def _file_age_hours(path: str) -> float:
    if not os.path.exists(path):
        return float("inf")
    mtime = os.path.getmtime(path)
    return (time.time() - mtime) / 3600.0


def ensure_fresh(
    url: str,
    dest: str,
    *,
    max_age_hours: float = 12.0,
    force: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> str:
    """若檔案不存在或超過 ``max_age_hours`` 就重新抓；否則回傳原檔路徑。"""
    if not force and _file_age_hours(dest) < max_age_hours:
        return dest
    return fetch_to_file(url, dest, timeout=timeout, retries=retries)


def fetch_all(
    data_dir: str,
    *,
    max_age_hours: float = 12.0,
    force: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> dict:
    """抓全部三個端點，回傳每個檔的絕對路徑與是否重新抓。"""
    targets = [
        (WARRANT_BASE_URL, os.path.join(data_dir, "warrants.json")),
        (WARRANT_TRADE_URL, os.path.join(data_dir, "warrants_trade.json")),
        (STOCK_BASE_URL, os.path.join(data_dir, "stocks.json")),
    ]
    results = {}
    for url, dest in targets:
        refreshed = False
        if force or _file_age_hours(dest) >= max_age_hours:
            try:
                fetch_to_file(url, dest, timeout=timeout, retries=retries)
                refreshed = True
            except Exception:
                # graceful: 若曾有快取就保留
                if not os.path.exists(dest):
                    raise
        results[dest] = {"refreshed": refreshed, "url": url}
    return results


def fetch_otc_all(
    data_dir: str,
    *,
    max_age_hours: float = 12.0,
    force: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> dict:
    """抓全部上櫃端點，回傳每個檔的路徑與是否重新抓。

    跟 :func:`fetch_all` 一樣 graceful：曾有快取就保留，不會因單一檔失敗而拋出。
    目標檔：
    - ``warrants_otc.csv``  上櫃權證基本資料
    - ``warrants_trade_otc.csv`` 上櫃權證每日成交
    - ``stocks_otc.csv``    上櫃公司基本資料
    """
    targets = [
        (WARRANT_OTC_URL, os.path.join(data_dir, "warrants_otc.csv")),
        (WARRANT_OTC_TRADE_URL, os.path.join(data_dir, "warrants_trade_otc.csv")),
        (STOCK_OTC_URL, os.path.join(data_dir, "stocks_otc.csv")),
    ]
    results = {}
    for url, dest in targets:
        refreshed = False
        if force or _file_age_hours(dest) >= max_age_hours:
            try:
                fetch_to_file(url, dest, timeout=timeout, retries=retries)
                refreshed = True
            except Exception:
                if not os.path.exists(dest):
                    raise
        results[dest] = {"refreshed": refreshed, "url": url}
    return results


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    out = fetch_all(args.data_dir, force=args.force)
    for path, info in out.items():
        print(f"{info['url']} -> {path}  (refreshed={info['refreshed']})")
