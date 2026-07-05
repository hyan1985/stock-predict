#!/usr/bin/env python
"""
tail_pick.py — 尾盘选股分析引擎
基于"阳哥爱打板"抖音策略：尾盘30分钟判断次日涨跌

用法:
    python tail_pick.py 600519 000858              # 分析指定股票
    python tail_pick.py --list watchlist.txt       # 从文件批量分析
    python tail_pick.py --top 50                   # 扫描涨幅榜前50只

策略要点:
    一、六种分时形态识别（形态4/5为买入信号）
    二、三步筛选法：涨幅3-5% → 三不要 → K线+分时确认
"""

import sys
import os
import json
import time
import random
from datetime import datetime

# 确保能找到 astock_data
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from astock_data import get_quote, get_minute_data, get_daily

# ── 配置 ──────────────────────────────────────────────────────────────

TAIL_START = "14:30"       # 尾盘起点
MARKET_CLOSE = "15:00"     # 收盘时间
MIN_TAIL_MINUTES = 20      # 尾盘至少需要的数据分钟数


# ═══════════════════════════════════════════════════════════════════════
# 分时形态识别（六种形态）
# ═══════════════════════════════════════════════════════════════════════

def _safe_float(v, default=0.0):
    try:
        return float(v) if v else default
    except (ValueError, TypeError):
        return default


def _normalize_minute(raw: list) -> list:
    """
    归一化分钟数据为统一格式 [{time, price, volume}]
    腾讯返回 {time, open, close, high, low, volume} → price = close
    mootdx返回 {time, price, volume} → 直接使用
    东财返回 {time, main_net, ...} → 不包含价格，丢弃
    """
    if not raw:
        return []
    first = raw[0]
    # 腾讯格式：有 close 字段
    if "close" in first:
        return [{
            "time": m.get("time", ""),
            "price": m.get("close", m.get("price", 0)),
            "volume": m.get("volume", 0),
        } for m in raw]
    # mootdx 格式：有 price 字段
    if "price" in first:
        return raw
    # 东财资金流格式：不包含价格数据，无法用于形态识别
    return []


def analyze_intraday_pattern(minute_data: list, open_price: float, last_close: float) -> dict:
    """
    识别六种尾盘分时形态，返回信号评分和描述。
    
    返回: {
        pattern: 1-6,       # 形态编号
        pattern_name: str,  # 形态名称
        signal: "buy"|"neutral"|"avoid",
        score: 0-100,       # 综合评分
        details: [...],     # 详细判断依据
        current_price: float,
        vwap: float,
        tail_trend: str,    # 尾盘走势描述
    }
    """
    if not minute_data or len(minute_data) < 60:
        return {"pattern": 0, "pattern_name": "数据不足", "signal": "neutral",
                "score": 0, "details": ["分钟数据不足60条"], "current_price": 0, "vwap": 0, "tail_trend": "N/A"}

    # 提取价格和成交量
    prices = [m.get("price", 0) for m in minute_data if m.get("price", 0) > 0]
    volumes = [m.get("volume", 0) for m in minute_data]
    times = [m.get("time", "") for m in minute_data]

    if len(prices) < 30:
        return {"pattern": 0, "pattern_name": "数据不足", "signal": "neutral",
                "score": 0, "details": ["有效价格数据不足"], "current_price": 0, "vwap": 0, "tail_trend": "N/A"}

    current_price = prices[-1]
    
    # 计算 VWAP (分时均线)
    vwap_prices = prices[:]
    vwap_vols = volumes[:len(prices)]
    total_mv = sum(p * v for p, v in zip(vwap_prices, vwap_vols) if v > 0)
    total_v = sum(v for v in vwap_vols if v > 0)
    vwap = total_mv / total_v if total_v > 0 else current_price

    # 划分时段
    morning_end = next((i for i, t in enumerate(times) if t >= "11:30"), len(times))
    afternoon_start = next((i for i, t in enumerate(times) if t >= "13:00"), morning_end)
    tail_start_idx = next((i for i, t in enumerate(times) if t >= TAIL_START), len(times) - 1)

    morning_prices = prices[:morning_end]
    afternoon_prices = prices[afternoon_start:]
    tail_prices = prices[tail_start_idx:]
    tail_volumes = volumes[tail_start_idx:] if tail_start_idx < len(volumes) else []

    if not afternoon_prices:
        afternoon_prices = prices[len(prices)//2:]

    morning_high = max(morning_prices) if morning_prices else current_price
    morning_low = min(morning_prices) if morning_prices else current_price
    afternoon_high = max(afternoon_prices) if afternoon_prices else current_price
    afternoon_low = min(afternoon_prices) if afternoon_prices else current_price
    tail_high = max(tail_prices) if tail_prices else current_price
    tail_low = min(tail_prices) if tail_prices else current_price
    tail_avg_vol = sum(tail_volumes) / len(tail_volumes) if tail_volumes else 0
    all_avg_vol = sum(volumes[len(volumes)//2:]) / max(len(volumes[len(volumes)//2:]), 1)  # 下午均量

    # 价格变化幅度
    day_range_pct = (max(prices) - min(prices)) / min(prices) * 100 if min(prices) > 0 else 0
    tail_direction = tail_prices[-1] - tail_prices[0] if len(tail_prices) >= 2 else 0
    tail_direction_pct = tail_direction / tail_prices[0] * 100 if tail_prices and tail_prices[0] > 0 else 0

    # 成交量特征
    tail_vol_ratio = tail_avg_vol / all_avg_vol if all_avg_vol > 0 else 1
    vol_trend = "放大" if tail_vol_ratio > 1.3 else ("萎缩" if tail_vol_ratio < 0.7 else "平稳")

    # 日内强弱
    above_vwap = sum(1 for p in tail_prices if p > vwap) / max(len(tail_prices), 1)
    above_open = sum(1 for p in tail_prices if p > open_price) / max(len(tail_prices), 1)

    # ── 形态判断 ──
    details = []
    pattern = 0
    pattern_name = ""
    signal = "neutral"
    score = 50

    # 形态2: 先涨后跌 — 先高于开盘且冲高，后跌破开盘价
    if morning_high > open_price * 1.01 and current_price < open_price * 0.99:
        pattern = 2
        pattern_name = "先涨后跌(拉高出货)"
        signal = "avoid"
        score = 10
        details = [
            f"早盘冲高至{morning_high:.2f}(+{(morning_high/open_price-1)*100:.1f}%)",
            f"当前价{current_price:.2f}已跌破开盘价{open_price:.2f}",
            "典型的拉高诱多出货形态，主力在跑路"
        ]

    # 形态6: 尾盘最后几分钟急拉 — 尾盘急拉远离均线
    elif (len(tail_prices) >= 3 and tail_prices[-1] > vwap * 1.02 
          and tail_direction_pct > 1.0 and tail_vol_ratio > 1.5):
        pattern = 6
        pattern_name = "尾盘急拉(诱多出货)"
        signal = "avoid"
        score = 15
        details = [
            f"尾盘急拉{tail_direction_pct:.2f}%，远离均线{(current_price/vwap-1)*100:.1f}%",
            f"尾盘成交{vol_trend}({tail_vol_ratio:.1f}x)",
            "拉高诱多！一追就成接盘侠，务必管住手"
        ]

    # 形态3: 先跌后反弹无力 — 全天在均线下方
    elif above_vwap < 0.3 and current_price < open_price * 0.98:
        pattern = 3
        pattern_name = "全天受压(反弹无力)"
        signal = "avoid"
        score = 20
        details = [
            f"尾盘仅{above_vwap*100:.0f}%时间在均线上方",
            f"当前价低于开盘价{(1-current_price/open_price)*100:.1f}%",
            "抛压太重，主力没心思做多，次日多半低开"
        ]

    # 形态1: 横盘不动
    elif day_range_pct < 2.0 and abs(current_price - open_price) / open_price < 0.01:
        pattern = 1
        pattern_name = "横盘不动(洗盘吸筹)"
        signal = "neutral"
        score = 35
        details = [
            f"全天振幅仅{day_range_pct:.1f}%",
            f"量比{vol_trend}，主力还在洗盘",
            "没耐心的跳过，等方向明确了再说"
        ]

    # 形态4: 冲高回落不破均线 — 回踩不破VWAP
    elif (afternoon_high > vwap * 1.01 and current_price >= vwap * 0.995 
          and above_vwap >= 0.5 and tail_direction_pct > -0.3):
        pattern = 4
        pattern_name = "冲高回踩(承接有力)"
        signal = "buy"
        score = 70
        details = [
            f"冲高后回踩不破分时均线({vwap:.2f})",
            f"尾盘{above_vwap*100:.0f}%时间在均线上方",
            "下方资金承接很强，洗盘结束，次日大概率还能冲高"
        ]

    # 形态5: 小阳缓升+温和放量
    elif (0 < (current_price / open_price - 1) < 0.03 
          and above_vwap >= 0.7 and tail_vol_ratio >= 1.0 
          and tail_vol_ratio <= 2.0 and tail_direction_pct >= -0.1):
        pattern = 5
        pattern_name = "小阳缓升(主力吃货)"
        signal = "buy"
        score = 80
        details = [
            f"涨幅{((current_price/open_price)-1)*100:.1f}%，控制在3%以内",
            f"全天在均线上方稳稳运行",
            f"成交量{vol_trend}({tail_vol_ratio:.1f}x)，主力偷偷进货",
            "次日可能直接拉涨停！尾盘轻仓埋伏"
        ]

    # 形态5 变体: 涨幅略大但仍在均线上方 + 尾盘创当日新高
    elif (0.02 < (current_price / open_price - 1) < 0.06 
          and above_vwap >= 0.6 and tail_vol_ratio >= 0.9
          and abs(tail_prices[-1] - tail_high) / tail_high < 0.003):
        pattern = 5
        pattern_name = "强势缓升(尾盘新高)"
        signal = "buy"
        score = 75
        details = [
            f"涨幅{((current_price/open_price)-1)*100:.1f}%",
            f"尾盘逼近全天高点{tail_high:.2f}",
            f"成交量{vol_trend}，控盘很稳",
            "可以考虑尾盘轻仓埋伏"
        ]

    # 形态4 变体: 回踩均线后企稳
    elif (above_vwap >= 0.4 and current_price > open_price * 0.995
          and tail_prices[-1] >= vwap * 0.998 and tail_direction_pct > -0.2):
        pattern = 4
        pattern_name = "均线上方震荡(偏强)"
        signal = "buy"
        score = 65
        details = [
            f"价格在均线上方震荡，回踩不破",
            f"尾盘走势平稳，资金有承接",
            "可以小仓位更一把，次日大概率能赚短线"
        ]

    # 默认: 中性偏弱
    else:
        if above_vwap >= 0.5:
            pattern = 0
            pattern_name = "均线上方(偏强)"
            signal = "buy"
            score = 65
            details = [
                f"尾盘{above_vwap*100:.0f}%时间在均线上方",
                "资金在均线附近承接有力，尾盘站稳",
                "次日大概率继续走强，可小仓位尾盘埋伏"
            ]
        else:
            pattern = 0
            pattern_name = "均线下方(中性偏弱)"
            signal = "neutral"
            score = 40
            details = [f"尾盘{(1-above_vwap)*100:.0f}%时间在均线下方，等待企稳信号"]

    return {
        "pattern": pattern,
        "pattern_name": pattern_name,
        "signal": signal,
        "score": score,
        "details": details,
        "current_price": round(current_price, 2),
        "vwap": round(vwap, 2),
        "tail_trend": f"尾盘{tail_direction_pct:+.2f}%, 量{vol_trend}",
        "day_range_pct": round(day_range_pct, 2),
        "tail_vol_ratio": round(tail_vol_ratio, 2),
        "above_vwap_ratio": round(above_vwap, 2),
    }


# ═══════════════════════════════════════════════════════════════════════
# 三步筛选法
# ═══════════════════════════════════════════════════════════════════════

def check_filters(quote: dict, daily_data: list, minute_data: list) -> dict:
    """
    执行三步筛选法。
    返回: {passed: bool, score: 0-100, checks: [{name, passed, value, threshold, detail}], ...}
    """
    checks = []
    filter_score = 0
    
    # ── 第一步：涨幅 3%-5% ──
    change_pct = quote.get("change_pct", 0)
    step1_ok = 3.0 <= change_pct <= 5.0
    checks.append({
        "step": 1, "name": "涨幅3%-5%",
        "passed": step1_ok,
        "value": f"{change_pct:.2f}%",
        "threshold": "3.0% ~ 5.0%",
        "detail": "✅ 强势但不极端" if step1_ok else f"{'偏低' if change_pct < 3 else '偏高'}"
    })
    if step1_ok:
        filter_score += 25

    # ── 第二步：三不要 ──
    # 量比 ≥ 1
    vol_ratio = quote.get("volume_ratio", 0)
    vol_ok = vol_ratio >= 1.0
    checks.append({
        "step": 2, "name": "量比≥1",
        "passed": vol_ok,
        "value": f"{vol_ratio:.2f}",
        "threshold": "≥ 1.0",
        "detail": "✅ 有量有行情" if vol_ok else "量能不足，没行情"
    })
    if vol_ok:
        filter_score += 15

    # 换手率 5%-10%
    turnover = quote.get("turnover_rate", 0)
    turnover_ok = 5.0 <= turnover <= 10.0
    checks.append({
        "step": 2, "name": "换手率5%-10%",
        "passed": turnover_ok,
        "value": f"{turnover:.2f}%",
        "threshold": "5.0% ~ 10.0%",
        "detail": "✅ 活跃适中" if turnover_ok else (f"{'太低，没活性' if turnover < 5 else '太高，有出货嫌疑'}")
    })
    if turnover_ok:
        filter_score += 15

    # 流通市值 50亿-200亿
    circ_mcap = quote.get("circ_mcap_yi", 0)
    mcap_ok = 50 <= circ_mcap <= 200
    checks.append({
        "step": 2, "name": "流通市值50-200亿",
        "passed": mcap_ok,
        "value": f"{circ_mcap:.0f}亿",
        "threshold": "50亿 ~ 200亿",
        "detail": "✅ 盘子适中" if mcap_ok else (f"{'太小，易操纵' if circ_mcap < 50 else '太大，拉不动'}")
    })
    if mcap_ok:
        filter_score += 15

    # ── 第三步：日K线 + 分时确认 ──
    k_checks = _check_kline(daily_data, quote)
    checks.extend(k_checks)
    for c in k_checks:
        if c["passed"]:
            filter_score += 10

    # 尾盘创当日新高
    if minute_data:
        prices = [m.get("price", 0) for m in minute_data if m.get("price", 0) > 0]
        times = [m.get("time", "") for m in minute_data]
        tail_start_idx = next((i for i, t in enumerate(times) if t >= TAIL_START), len(prices) - 1)
        tail_prices = prices[tail_start_idx:]
        
        if tail_prices and prices:
            day_high = max(prices)
            tail_high = max(tail_prices)
            new_high = tail_high >= day_high * 0.998  # 逼近日内新高
            checks.append({
                "step": 3, "name": "尾盘创当日新高",
                "passed": new_high,
                "value": f"尾盘高{tail_high:.2f} / 全天高{day_high:.2f}",
                "threshold": "尾盘高点 ≈ 全天高点",
                "detail": "✅ 强势突破信号！" if new_high else "尾盘未能突破前高"
            })
            if new_high:
                filter_score += 10
        else:
            checks.append({
                "step": 3, "name": "尾盘创当日新高",
                "passed": False, "value": "N/A", "threshold": "尾盘高点 ≈ 全天高点",
                "detail": "分钟数据不足，无法判断"
            })
    else:
        checks.append({
            "step": 3, "name": "尾盘创当日新高",
            "passed": False, "value": "N/A", "threshold": "尾盘高点 ≈ 全天高点",
            "detail": "无分时数据"
        })

    # 综合判断
    total_checks = len(checks)
    passed_checks = sum(1 for c in checks if c["passed"])
    
    # 核心必要条件
    core_conditions = [step1_ok, vol_ok, turnover_ok, mcap_ok]
    all_core_passed = all(core_conditions)
    
    passed = filter_score >= 50 and passed_checks >= total_checks * 0.5

    return {
        "passed": passed,
        "score": min(filter_score, 100),
        "passed_checks": passed_checks,
        "total_checks": total_checks,
        "checks": checks,
    }


def _check_kline(daily_data: list, quote: dict) -> list:
    """检查K线条件：成交量温和放大 / 5日线金叉 / 30日线向上"""
    checks = []
    if not daily_data or len(daily_data) < 31:
        checks.append({"step": 3, "name": "K线分析", "passed": False,
                       "value": f"{len(daily_data)}天数据", "threshold": "≥31天",
                       "detail": "日K数据不足，无法分析"})
        return checks

    closes = [d.get("close", 0) for d in daily_data if d.get("close", 0) > 0]
    volumes = [d.get("volume", 0) for d in daily_data if d.get("volume", 0) > 0]
    
    if len(closes) < 31:
        checks.append({"step": 3, "name": "K线分析", "passed": False,
                       "value": f"{len(closes)}天有效数据", "threshold": "≥31天",
                       "detail": "日K数据不足"})
        return checks

    # 成交量温和放大：今日量 > 5日均量
    today_vol = volumes[-1] if volumes else 0
    avg_vol_5 = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else (sum(volumes[:-1]) / max(len(volumes)-1, 1))
    vol_growing = today_vol > avg_vol_5 * 1.05
    vol_exploding = today_vol > avg_vol_5 * 2.5
    checks.append({
        "step": 3, "name": "成交量温和放大",
        "passed": vol_growing and not vol_exploding,
        "value": f"今日{today_vol} / 5日均{avg_vol_5:.0f}({today_vol/avg_vol_5*100:.0f}%)",
        "threshold": ">105% 且 <250%",
        "detail": "✅ 温和放量" if (vol_growing and not vol_exploding) else (
            "⚠️ 异常爆量，警惕" if vol_exploding else "缩量，不够活跃"
        )
    })

    # 5日线：计算 5MA 和 10MA
    ma5 = sum(closes[-6:-1]) / 5 if len(closes) >= 6 else 0
    ma10 = sum(closes[-11:-1]) / 10 if len(closes) >= 11 else 0
    ma30 = sum(closes[-31:-1]) / 30 if len(closes) >= 31 else 0
    current = quote.get("price", closes[-1])

    # 5日线金叉（5MA > 10MA）
    golden_cross = ma5 > ma10
    checks.append({
        "step": 3, "name": "5日线金叉",
        "passed": golden_cross,
        "value": f"MA5={ma5:.2f} / MA10={ma10:.2f}",
        "threshold": "MA5 > MA10",
        "detail": "✅ 金叉，短期趋势向上" if golden_cross else "MA5在MA10下方，短期趋势偏弱"
    })

    # 30日线趋势向上：MA30 近5日斜率 > 0 或 MA30 今日 > 5日前
    if len(closes) >= 36:
        ma30_5d_ago = sum(closes[-36:-6]) / 30 if len(closes) >= 36 else ma30
        trend_up = ma30 > ma30_5d_ago * 0.995
    else:
        trend_up = current > ma30
    
    checks.append({
        "step": 3, "name": "30日线趋势向上",
        "passed": trend_up,
        "value": f"MA30={ma30:.2f} / 现价={current:.2f}",
        "threshold": "MA30 走平或向上",
        "detail": "✅ 中期趋势向上，主力在悄悄建仓" if trend_up else "中期趋势向下，不够安全"
    })

    return checks


# ═══════════════════════════════════════════════════════════════════════
# 单股综合分析
# ═══════════════════════════════════════════════════════════════════════

def analyze_stock(code: str, retries: int = 1) -> dict:
    """
    对单只股票执行完整的尾盘选股分析。
    返回结构化结果 dict。
    """
    code = str(code).strip()
    start_time = time.time()

    result = {
        "code": code,
        "name": code,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "is_trading_time": True,
        "error": None,
        "quote": {},
        "filters": {},
        "pattern": {},
        "verdict": "",
        "recommendation": "",
        "summary": {},
    }

    # 1. 获取行情
    for attempt in range(retries + 1):
        try:
            quote = get_quote(code)
            if quote.get("price", 0) > 0:
                break
        except Exception as e:
            if attempt == retries:
                result["error"] = f"行情获取失败: {e}"
                return result
            time.sleep(1)

    result["quote"] = quote
    result["name"] = quote.get("name", code)
    
    if quote.get("price", 0) <= 0:
        result["error"] = f"无法获取 {code} 的实时行情（可能停牌或代码错误）"
        result["verdict"] = "❌ 数据异常"
        return result

    # 2. 分钟 K（mootdx 本地 / 腾讯 CI 兜底）
    minute_data = []
    try:
        raw_minute, _ = get_minute_data(code)
        minute_data = _normalize_minute(raw_minute)
    except Exception:
        pass

    # 3. 日 K（Tushare → mootdx）
    daily_data = []
    try:
        daily_data = get_daily(code, 60)
    except Exception:
        pass

    # 4. 执行筛选
    filter_result = check_filters(quote, daily_data, minute_data)
    result["filters"] = filter_result

    # 5. 分时形态识别
    open_price = quote.get("open", quote.get("last_close", 0))
    last_close = quote.get("last_close", 0)
    pattern_result = analyze_intraday_pattern(minute_data, open_price, last_close)
    result["pattern"] = pattern_result

    # 6. 综合判断
    pattern_signal = pattern_result.get("signal", "neutral")
    pattern_score = pattern_result.get("score", 0)
    filter_passed = filter_result.get("passed", False)
    filter_score = filter_result.get("score", 0)

    # 最终评分：形态权重60%，筛选权重40%
    final_score = int(pattern_score * 0.6 + filter_score * 0.4)

    if pattern_signal == "buy" and filter_passed:
        verdict = "🟢 强烈推荐"
        recommendation = "尾盘轻仓买入，次日冲高卖出。止损设在今日低点下方2%。"
    elif pattern_signal == "buy" and final_score >= 50:
        verdict = "🟡 可以考虑"
        recommendation = "形态不错但筛选条件未全过，小仓位试探，严格止损。"
    elif pattern_signal == "buy":
        verdict = "🟡 形态偏多"
        recommendation = "分时形态偏强，但硬性条件不满足，观望为主。"
    elif pattern_signal == "avoid":
        verdict = "🔴 建议回避"
        recommendation = "形态走坏，有出货嫌疑，千万别碰！"
    elif final_score >= 50:
        verdict = "⚪ 中性偏强"
        recommendation = "可继续观察，等待更明确的买入信号。"
    else:
        verdict = "⚪ 暂不建议"
        recommendation = "条件不满足，空仓等待更好机会。"

    result["verdict"] = verdict
    result["recommendation"] = recommendation
    result["summary"] = {
        "final_score": final_score,
        "pattern_score": pattern_score,
        "filter_score": filter_score,
        "elapsed_seconds": round(time.time() - start_time, 2),
    }

    return result


# ═══════════════════════════════════════════════════════════════════════
# 批量分析
# ═══════════════════════════════════════════════════════════════════════

def batch_analyze(codes: list, delay: float = 1.5) -> list:
    """
    批量分析股票。
    codes: 股票代码列表
    delay: 每只股票之间的间隔（秒），防封IP
    """
    results = []
    n = len(codes)
    for i, code in enumerate(codes):
        print(f"\r[{i+1}/{n}] 分析 {code}...", end="", flush=True)
        r = analyze_stock(str(code).strip())
        results.append(r)
        if i < n - 1:
            time.sleep(delay + random.uniform(0, 0.5))
    print()  # newline
    return results


# ═══════════════════════════════════════════════════════════════════════
# 输出格式化
# ═══════════════════════════════════════════════════════════════════════

def format_result(r: dict, verbose: bool = True) -> str:
    """格式化单只股票的分析结果为可读文本"""
    if r.get("error"):
        return f"【{r['code']}】❌ {r['error']}"

    q = r.get("quote", {})
    f = r.get("filters", {})
    p = r.get("pattern", {})
    s = r.get("summary", {})

    name = r.get("name", r["code"])
    code = r["code"]
    price = q.get("price", 0)
    change_pct = q.get("change_pct", 0)
    
    lines = []
    lines.append(f"{'─'*60}")
    lines.append(f"【{code} {name}】 {r.get('verdict', '')} (综合评分: {s.get('final_score', 0)}/100)")
    lines.append(f"  现价: {price:.2f}  |  涨幅: {change_pct:+.2f}%  |  量比: {q.get('volume_ratio', 0):.2f}")
    lines.append(f"  换手: {q.get('turnover_rate', 0):.2f}%  |  流通市值: {q.get('circ_mcap_yi', 0):.0f}亿")
    
    # 分时形态
    if p:
        lines.append(f"  ── 分时形态 ──")
        lines.append(f"  形态: {p.get('pattern_name', 'N/A')}")
        lines.append(f"  均线(VWAP): {p.get('vwap', 0):.2f}  |  尾盘走势: {p.get('tail_trend', 'N/A')}")
        for d in p.get("details", [])[:3]:
            lines.append(f"    → {d}")

    # 筛选详情
    if verbose and f:
        lines.append(f"  ── 筛选检查 ({f.get('passed_checks', 0)}/{f.get('total_checks', 0)} 通过) ──")
        for c in f.get("checks", []):
            icon = "✅" if c["passed"] else "❌"
            lines.append(f"  {icon} {c['name']}: {c['value']} ({c['threshold']}) — {c['detail']}")
    
    # 建议
    lines.append(f"  ── 操作建议 ──")
    lines.append(f"  {r.get('recommendation', 'N/A')}")
    lines.append(f"  (分析耗时: {s.get('elapsed_seconds', 0)}s)")
    
    return "\n".join(lines)


def format_batch_summary(results: list) -> str:
    """批量分析汇总表"""
    lines = []
    lines.append(f"\n{'='*80}")
    lines.append(f"  尾盘选股批量分析结果 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"{'='*80}")
    
    # 按评分排序
    sorted_results = sorted(results, key=lambda r: r.get("summary", {}).get("final_score", 0), reverse=True)
    
    # 汇总表头
    lines.append(f"{'代码':<10}{'名称':<8}{'现价':>7}{'涨幅':>7}{'量比':>6}{'换手':>6}{'市值':>6}{'形态评分':>8}{'综合':>5}{'建议'}")
    lines.append(f"{'-'*80}")
    
    for r in sorted_results:
        if r.get("error"):
            lines.append(f"{r['code']:<10}{'❌ ' + r['error'][:30]}")
            continue
        
        q = r.get("quote", {})
        p = r.get("pattern", {})
        s = r.get("summary", {})
        verdict = r.get("verdict", "")
        verdict_short = {"🟢 强烈推荐": "🟢买", "🟡 可以考虑": "🟡看", "🟡 形态偏多": "🟡偏多",
                         "🔴 建议回避": "🔴避", "⚪ 中性偏强": "⚪等", "⚪ 暂不建议": "⚪等"}.get(verdict, "?")
        
        name = r.get("name", "")[:4]
        lines.append(
            f"{r['code']:<10}{name:<8}{q.get('price',0):>7.2f}{q.get('change_pct',0):>+6.2f}%"
            f"{q.get('volume_ratio',0):>6.2f}{q.get('turnover_rate',0):>5.1f}%"
            f"{q.get('circ_mcap_yi',0):>6.0f}{p.get('score',0):>8}{s.get('final_score',0):>5}  {verdict_short}"
        )
    
    lines.append(f"{'─'*80}")
    
    # 统计
    buy_count = sum(1 for r in results if "🟢" in r.get("verdict", ""))
    maybe_count = sum(1 for r in results if "🟡" in r.get("verdict", ""))
    avoid_count = sum(1 for r in results if "🔴" in r.get("verdict", ""))
    neutral_count = len(results) - buy_count - maybe_count - avoid_count
    
    lines.append(f"  总计 {len(results)} 只 | 🟢推荐 {buy_count} | 🟡关注 {maybe_count} | 🔴回避 {avoid_count} | ⚪观望 {neutral_count}")
    lines.append(f"  若市场情绪不对，空仓才是交易的王道。")
    
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# 命令行入口
# ═══════════════════════════════════════════════════════════════════════

def main():
    # 修复 Windows 控制台编码
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        print("用法: python tail_pick.py <代码1> [代码2] ...  |  --list <文件>")
        print("示例: python tail_pick.py 600519 000858 002008")
        print("      python tail_pick.py --list watchlist.txt")
        return

    codes = []
    if sys.argv[1] == "--list":
        if len(sys.argv) < 3:
            print("请提供股票列表文件路径")
            return
        with open(sys.argv[2], "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    codes.append(line.split()[0] if line.split() else line)
    else:
        codes = [c.strip().replace(",", "").replace("，", "") for c in sys.argv[1:]]
        codes = [c for c in codes if c]

    if not codes:
        print("未提供任何股票代码")
        return

    print(f"\n开始分析 {len(codes)} 只股票的尾盘信号...\n")
    results = batch_analyze(codes)

    # 每只股票详细输出
    for r in results:
        print(format_result(r, verbose=True))

    # 汇总表
    print(format_batch_summary(results))
    
    # 同时输出 JSON 便于程序处理
    json_path = os.path.join(os.path.dirname(__file__), "tail_pick_result.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n详细JSON已保存至: {json_path}")


if __name__ == "__main__":
    main()
