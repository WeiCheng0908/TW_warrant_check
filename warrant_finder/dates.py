"""ROC 民國年月日 <-> ISO date util。

TWSE 的日期欄位格式為 ``1151015``（民國 115 年 10 月 15 日 = 2026/10/15）。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# 6~7 碼民國日期 (例 1151015 / 0980101)
_ROC_DATE = re.compile(r"^(\d{2,3})(\d{2})(\d{2})$")


def roc_to_date(value) -> date | None:
    """把民國日期字串或整數轉成 :class:`datetime.date`。

    接受 ``"1151015"``、``1151015``、``"115/10/15"``、``"115-10-15"`` 等形式。
    無法解析時回傳 ``None``。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        s = str(int(value))
    else:
        s = str(value).strip()
    s = s.replace("/", "").replace("-", "")
    m = _ROC_DATE.match(s)
    if not m:
        return None
    year, month, day = (int(g) for g in m.groups())
    if year < 1000:  # 3-digit 民國年
        year += 1911
    try:
        return date(year, month, day)
    except ValueError:
        return None


def iso_to_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def today_taipei() -> date:
    return datetime.now(TAIPEI_TZ).date()


def format_iso(d: date | None) -> str | None:
    return d.isoformat() if d else None
