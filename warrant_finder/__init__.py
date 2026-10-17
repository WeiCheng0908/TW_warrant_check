"""TWSE 權證查詢系統 — 核心模組。

子模組：
- ``fetcher``  : 抓 TWSE OpenAPI 三個端點
- ``parser``   : 寬鬆 JSON 解析（處理 \\r\\n 與 NUL）
- ``stock_map``: 代號↔名稱對照表
- ``data``     : 合併母檔與成交檔
- ``search``   : 查詢邏輯
- ``dates``    : ROC ↔ ISO 日期轉換
- ``quote``    : TWSE 5 分鐘延遲報價
- ``iv``       : Black-Scholes-Merton 價格 + IV 反解 + Greeks
"""

__version__ = "0.2.0"
