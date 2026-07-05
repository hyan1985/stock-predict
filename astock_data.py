"""
astock_data.py — A股数据获取层

优先级（按环境自动选择）：
  行情 / 日K：
    Tushare（需 TUSHARE_TOKEN）→ mootdx（仅本地）→ 腾讯
  分钟 K（形态分析，需 OHLCV）：
    mootdx bars(frequency=8)（仅本地）→ 腾讯 mkline（GitHub/CI 兜底）
  资金流（可选，不参与形态）：
    东财 push2（单独接口 get_fund_flow_minute）

环境变量：
  TUSHARE_TOKEN   Tushare Pro Token
  CI=true         GitHub Actions 等 CI 环境（跳过 mootdx）

使用前: pip install tushare mootdx pandas
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ── 环境 ──────────────────────────────────────────────────────────────

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
IS_CI = os.getenv("CI", "").lower() in ("1", "true", "yes")
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")

_tushare_pro = None
_tdx_client = None


def is_ci() -> bool:
    return IS_CI


def _tushare_api():
    global _tushare_pro
    if not TUSHARE_TOKEN:
        return None
    if _tushare_pro is None:
        try:
            import tushare as ts
            _tushare_pro = ts.pro_api(TUSHARE_TOKEN)
        except Exception as e:
            print(f"[WARN] Tushare 初始化失败: {e}")
            return None
    return _tushare_pro


def to_ts_code(code: str) -> str:
    code = str(code).strip().split(".")[0]
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("8", "4")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _tencent_prefix(code: str) -> str:
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith("8"):
        return "bj"
    return "sz"


def tdx_client():
    """mootdx 客户端（CI 环境不初始化）。"""
    global _tdx_client
    if IS_CI:
        return None
    if _tdx_client is not None:
        return _tdx_client
    try:
        from mootdx.quotes import Quotes
        _tdx_client = Quotes.factory(market="std")
    except Exception as e:
        print(f"[WARN] mootdx 连接失败: {e}")
        _tdx_client = None
    return _tdx_client


def _empty_quote() -> dict:
    return {
        "name": "", "price": 0, "last_close": 0, "open": 0, "change_pct": 0,
        "pe_ttm": None, "pb": None, "mcap_yi": 0, "circ_mcap_yi": 0,
        "turnover_yi": 0, "turnover_rate": 0, "volume_ratio": 0, "volume_hands": 0,
        "_source": "none",
    }


# ═══════════════════════════════════════════════════════════════════════
# 腾讯行情（盘中实时，GitHub 可用）
# ═══════════════════════════════════════════════════════════════════════

def get_quote_tencent(code: str) -> dict:
    """腾讯实时行情。"""
    code = str(code).strip().split(".")[0]
    url = f"https://qt.gtimg.cn/q={_tencent_prefix(code)}{code}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode("gbk")
        vals = data.split('"')[1].split("~")
    except Exception:
        return _empty_quote()
    if len(vals) < 50 or not vals[1]:
        return _empty_quote()
    return {
        "name": vals[1],
        "price": float(vals[3]) if vals[3] else 0,
        "last_close": float(vals[4]) if vals[4] else 0,
        "open": float(vals[5]) if vals[5] else 0,
        "change_pct": float(vals[32]) if vals[32] else 0,
        "pe_ttm": float(vals[39]) if vals[39] else None,
        "pb": float(vals[46]) if vals[46] else None,
        "mcap_yi": float(vals[45]) if vals[45] else 0,
        "circ_mcap_yi": float(vals[44]) if vals[44] else 0,
        "turnover_yi": float(vals[37]) / 10000 if vals[37] else 0,
        "turnover_rate": float(vals[38]) if vals[38] else 0,
        "volume_ratio": float(vals[49]) if vals[49] else 0,
        "volume_hands": float(vals[6]) if vals[6] else 0,
        "_source": "tencent",
    }


def _enrich_quote_from_tushare(code: str, quote: dict) -> dict:
    """用 Tushare daily_basic 补充/校验字段（日级，盘后更准）。"""
    pro = _tushare_api()
    if pro is None:
        return quote
    ts_code = to_ts_code(code)
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        df = pro.daily_basic(
            ts_code=ts_code, start_date=start, end_date=end,
            fields="ts_code,trade_date,turnover_rate,volume_ratio,circ_mv,pe_ttm,pb",
        )
        if df is None or df.empty:
            return quote
        row = df.sort_values("trade_date").iloc[-1]
        if quote.get("turnover_rate", 0) <= 0 and row.get("turnover_rate"):
            quote["turnover_rate"] = float(row["turnover_rate"])
        if quote.get("volume_ratio", 0) <= 0 and row.get("volume_ratio"):
            quote["volume_ratio"] = float(row["volume_ratio"])
        if row.get("circ_mv"):
            quote["circ_mcap_yi"] = float(row["circ_mv"]) / 10000  # 万元 → 亿
        if quote.get("pe_ttm") is None and row.get("pe_ttm"):
            quote["pe_ttm"] = float(row["pe_ttm"])
        if quote.get("pb") is None and row.get("pb"):
            quote["pb"] = float(row["pb"])
        quote["_source"] = quote.get("_source", "tencent") + "+tushare"
    except Exception as e:
        print(f"[WARN] Tushare daily_basic 失败: {e}")
    return quote


def get_quote(code: str) -> dict:
    """
    统一行情入口。
    盘中：腾讯实时价为主；Tushare 补充换手/量比/市值（若有 Token）。
    """
    code = str(code).strip().split(".")[0]
    quote = get_quote_tencent(code)
    if quote.get("price", 0) <= 0:
        return quote
    return _enrich_quote_from_tushare(code, quote)


# ═══════════════════════════════════════════════════════════════════════
# 日 K
# ═══════════════════════════════════════════════════════════════════════

def _daily_from_tushare(code: str, n_days: int = 120) -> List[dict]:
    pro = _tushare_api()
    if pro is None:
        return []
    ts_code = to_ts_code(code)
    try:
        df = pro.daily(ts_code=ts_code, limit=n_days)
        if df is None or df.empty:
            return []
        df = df.sort_values("trade_date")
        rows = []
        for _, row in df.iterrows():
            d = str(row["trade_date"])
            date_str = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            rows.append({
                "date": date_str,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(float(row["vol"])),
            })
        return rows
    except Exception as e:
        print(f"[WARN] Tushare daily 失败: {e}")
        return []


def get_daily_mootdx(code: str, n_days: int = 120) -> List[dict]:
    """mootdx 日线（frequency=9，本地补充）。"""
    client = tdx_client()
    if client is None:
        return []
    try:
        df = client.bars(symbol=code, frequency=9, offset=n_days)
    except Exception as e:
        print(f"[WARN] mootdx daily 失败: {e}")
        return []
    if df is None or df.empty:
        return []
    if "vol" in df.columns:
        df = df.drop(columns=["vol"])
    rows = []
    for idx, row in df.iterrows():
        if "datetime" in df.columns:
            dt = pd.to_datetime(row["datetime"])
            date_str = dt.strftime("%Y-%m-%d")
        elif hasattr(idx, "strftime"):
            date_str = idx.strftime("%Y-%m-%d")
        else:
            date_str = str(idx)[:10]
        vol = row.get("volume", row.get("vol", 0))
        rows.append({
            "date": date_str,
            "open": float(row.get("open", 0) or 0),
            "high": float(row.get("high", 0) or 0),
            "low": float(row.get("low", 0) or 0),
            "close": float(row.get("close", 0) or 0),
            "volume": int(vol or 0),
        })
    return rows


def get_daily(code: str, n_days: int = 60) -> List[dict]:
    """日 K：Tushare → mootdx。"""
    rows = _daily_from_tushare(code, n_days)
    if rows:
        return rows
    return get_daily_mootdx(code, n_days)


# ═══════════════════════════════════════════════════════════════════════
# 分钟 K（仅 OHLCV，供形态分析）
# ═══════════════════════════════════════════════════════════════════════

def get_minute_tencent(code: str) -> List[dict]:
    """腾讯 1 分钟 K（GitHub/CI 兜底）。"""
    code = str(code).strip().split(".")[0]
    prefix = _tencent_prefix(code)
    url = f"http://ifzq.gtimg.cn/appstock/app/kline/mkline?param={prefix}{code},m1,,240"
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
        rows_raw = data.get("data", {}).get(f"{prefix}{code}", {}).get("m1", [])
    except Exception as e:
        print(f"[WARN] 腾讯分钟K线失败: {e}")
        return []
    rows = []
    for r in rows_raw:
        if len(r) < 6:
            continue
        ts = str(r[0])
        time_str = f"{ts[8:10]}:{ts[10:12]}" if len(ts) >= 12 else ts
        rows.append({
            "time": time_str,
            "open": float(r[1]) if r[1] else 0,
            "close": float(r[2]) if r[2] else 0,
            "high": float(r[3]) if r[3] else 0,
            "low": float(r[4]) if r[4] else 0,
            "volume": int(float(r[5]) / 100) if r[5] else 0,
        })
    return rows


def get_minute_mootdx(code: str) -> List[dict]:
    """mootdx 1 分钟 K（bars frequency=8，带 datetime）。"""
    client = tdx_client()
    if client is None:
        return []
    try:
        df = client.bars(symbol=code, frequency=8, offset=240)
    except Exception as e:
        print(f"[WARN] mootdx 分钟K失败: {e}")
        return []
    if df is None or df.empty:
        return []
    rows = []
    for _, row in df.iterrows():
        if "datetime" in df.columns:
            dt = pd.to_datetime(row["datetime"])
            time_str = dt.strftime("%H:%M")
        else:
            continue
        price = float(row.get("close", row.get("price", 0)) or 0)
        vol = row.get("volume", row.get("vol", 0))
        if price > 0:
            rows.append({
                "time": time_str,
                "price": price,
                "volume": int(vol or 0),
            })
    return rows


def get_minute_data(code: str) -> Tuple[List[dict], str]:
    """
    分钟量价数据（供 tail_pick 形态识别）。
    本地：mootdx → 腾讯；CI/GitHub：腾讯。
    注意：不包含东财资金流（无 price 字段）。
    """
    code = str(code).strip().split(".")[0]
    if not IS_CI:
        tdx = get_minute_mootdx(code)
        if tdx:
            return tdx, "mootdx"
    tx = get_minute_tencent(code)
    if tx:
        return tx, "tencent"
    return [], "none"


# ═══════════════════════════════════════════════════════════════════════
# 东财资金流（可选，与分钟 K 分离）
# ═══════════════════════════════════════════════════════════════════════

def _secid(code: str) -> str:
    return f"1.{code}" if code.startswith("6") else f"0.{code}"


def get_fund_flow_minute(code: str, retries: int = 2) -> List[dict]:
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params_str = f"secid={_secid(code)}&klt=1&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(f"{url}?{params_str}")
            req.add_header("User-Agent", UA)
            req.add_header("Referer", "https://quote.eastmoney.com/")
            resp = urllib.request.urlopen(req, timeout=10)
            d = json.loads(resp.read().decode())
            klines = d.get("data", {}).get("klines") or []
            rows = []
            for line in klines:
                parts = line.split(",")
                if len(parts) >= 6:
                    rows.append({
                        "time": parts[0],
                        "main_net": float(parts[1]),
                        "small_net": float(parts[2]),
                        "mid_net": float(parts[3]),
                        "large_net": float(parts[4]),
                        "super_net": float(parts[5]),
                    })
            return rows
        except Exception as e:
            if attempt < retries:
                time.sleep(1.5 + random.uniform(0.5, 1.5))
            else:
                print(f"[INFO] 东财资金流不可用: {type(e).__name__}")
    return []


def get_fund_flow_120d(code: str, retries: int = 2) -> List[dict]:
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params_str = f"secid={_secid(code)}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57&lmt=120&klt=101"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(f"{url}?{params_str}")
            req.add_header("User-Agent", UA)
            req.add_header("Referer", "https://quote.eastmoney.com/")
            resp = urllib.request.urlopen(req, timeout=10)
            d = json.loads(resp.read().decode())
            klines = d.get("data", {}).get("klines") or []
            return [
                {"date": p[0], "main_net": float(p[1])}
                for line in klines if len((p := line.split(","))) >= 2
            ]
        except Exception:
            if attempt < retries:
                time.sleep(1.5)
    return []


def get_historical_baseline(code: str) -> Tuple[List[dict], str]:
    em = get_fund_flow_120d(code)
    if em:
        return em, "eastmoney"
    daily = get_daily(code, 120)
    if daily:
        return daily, "tushare" if _tushare_api() else "mootdx"
    return [], "none"


def resolve_stock_input(text: str) -> Optional[str]:
    """解析用户输入：6 位代码或 Tushare 名称模糊匹配。"""
    text = str(text).strip()
    if not text:
        return None
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) == 6:
        return digits
    pro = _tushare_api()
    if pro is None:
        return None
    try:
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name")
        if df is None or df.empty:
            return None
        hit = df[df["name"].str.contains(text, na=False)]
        if len(hit) == 1:
            return str(hit.iloc[0]["symbol"])
        if len(hit) > 1:
            return str(hit.iloc[0]["symbol"])
    except Exception:
        pass
    return None


def query(code: str, data_type: str = "quote") -> Any:
    if data_type == "quote":
        return get_quote(code)
    if data_type == "fund_flow":
        return get_fund_flow_minute(code)
    if data_type == "fund_flow_120d":
        d, _ = get_historical_baseline(code)
        return d
    if data_type == "all":
        q = get_quote(code)
        minute, msrc = get_minute_data(code)
        hist, hsrc = get_historical_baseline(code)
        return {
            "quote": q, "minute": minute, "minute_source": msrc,
            "historical": hist, "historical_source": hsrc,
            "ci": IS_CI, "tushare": bool(TUSHARE_TOKEN),
        }
    raise ValueError(f"Unknown data_type: {data_type}")


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    q = get_quote(code)
    d, src = get_minute_data(code)
    daily = get_daily(code, 30)
    print(f"=== {q.get('name', code)}({code}) ===")
    print(f"  CI={IS_CI}  Tushare={'yes' if TUSHARE_TOKEN else 'no'}  source={q.get('_source')}")
    print(f"  现价: {q.get('price')}  涨幅: {q.get('change_pct')}%")
    print(f"  分钟: {len(d)} 条 ({src})  日K: {len(daily)} 条")
