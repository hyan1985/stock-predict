#!/usr/bin/env python
"""
tail_pick_backtest.py — 尾盘选股策略回测引擎
验证"阳哥爱打板"尾盘30分钟策略在历史数据上的有效性

用法:
    python tail_pick_backtest.py 603986 600667 002384 600378 600584
    python tail_pick_backtest.py 603986 600667 --days 20

策略逻辑（来自 tail_pick.py）：
    1. 六种分时形态识别 → 得出 buy/neutral/avoid 信号
    2. 三步筛选法（部分可在回测中验证）
    3. 次日涨跌验证
"""

import sys
import os
import json
import time
import random
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 导入 tail_pick 的核心分析函数 ──
from tail_pick import (
    analyze_intraday_pattern,
    _normalize_minute,
    _safe_float,
)

from mootdx.quotes import Quotes

# ── 配置 ──
BARS_PAGE = 800        # 每页bar数
MAX_PAGES = 15         # 最大翻页数 (15 × 800 = 12000 bars ≈ 50个交易日)
MIN_TAIL_MINUTES = 20  # 尾盘至少需要的分钟数
INTERVAL_SEC = 1.2     # API 调用间隔

# ── 股票名称映射 ──
STOCK_NAMES = {
    "603986": "兆易创新", "600667": "太极实业", "002384": "东山精密",
    "600378": "昊华科技", "600584": "长电科技",
    "000725": "京东方A", "600707": "彩虹股份", "002837": "英维克",
    "000021": "深科技", "002158": "汉钟精机",
}


def get_tdx_client():
    """获取 mootdx 客户端单例"""
    return Quotes.factory(market="std")


def fetch_daily_kline(client, code: str, n_days: int = 60) -> pd.DataFrame:
    """获取日K线数据"""
    try:
        df = client.bars(symbol=code, category=4, offset=n_days)
        if df is None or df.empty:
            return pd.DataFrame()
        # bars() 同时返回 'vol' 和 'volume' 两列，删除 'vol' 避免列名冲突
        if "vol" in df.columns:
            df = df.drop(columns=["vol"])
        if "datetime" in df.columns:
            df["trade_date"] = pd.to_datetime(df["datetime"]).dt.normalize()
            df = df.set_index("trade_date")
        else:
            df.index = pd.to_datetime(df.index).normalize()
            df.index.name = "trade_date"
        df = df.sort_index()
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        keep_cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        return df[keep_cols].dropna(subset=["open", "close"]) if keep_cols else pd.DataFrame()
    except Exception as e:
        print(f"  [WARN] 获取 {code} 日K失败: {e}")
        return pd.DataFrame()


def fetch_minute_bars(client, code: str, start_date: str) -> pd.DataFrame:
    """
    分页拉取1分钟K线，直到覆盖 start_date。
    返回 DataFrame，索引为 datetime，列含 open/high/low/close/volume
    """
    start_ts = pd.Timestamp(start_date)
    chunks = []
    for page in range(MAX_PAGES):
        try:
            df = client.bars(
                symbol=code,
                frequency=8,  # 1分钟
                start=page * BARS_PAGE,
                offset=BARS_PAGE,
            )
        except Exception as e:
            print(f"  [WARN] bars() page {page} 失败: {e}")
            break

        if df is None or df.empty:
            break

        chunks.append(df)

        # 检查最旧一条是否已早于 start_date
        if "datetime" in df.columns:
            first_dt = pd.to_datetime(df["datetime"].iloc[0])
        else:
            first_dt = pd.to_datetime(df.index[0])
        if first_dt <= start_ts:
            break

        time.sleep(0.3)  # 页间小间隔

    if not chunks:
        return pd.DataFrame()

    combined = pd.concat(chunks, ignore_index=False)

    # 标准化列名
    if "datetime" in combined.columns:
        combined["trade_time"] = pd.to_datetime(combined["datetime"])
        combined = combined.set_index("trade_time")
    else:
        combined.index = pd.to_datetime(combined.index)
        combined.index.name = "trade_time"

    combined = combined.sort_index()

    # 标准化数值列
    for col in ("open", "high", "low", "close", "volume"):
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col], errors="coerce")

    # 裁剪到 start_date 之后
    combined = combined[combined.index >= start_ts]

    return combined


def group_minute_by_date(minute_df: pd.DataFrame) -> dict:
    """
    将分钟数据按交易日期分组。
    返回: {date_str: [{time, price, volume}, ...]}
    """
    if minute_df.empty:
        return {}

    grouped = defaultdict(list)
    for idx, row in minute_df.iterrows():
        date_str = idx.strftime("%Y-%m-%d")
        time_str = idx.strftime("%H:%M")
        price = row.get("close", row.get("price", 0))
        vol = row.get("volume", 0)
        if price and price > 0:
            grouped[date_str].append({
                "time": time_str,
                "price": float(price),
                "volume": float(vol) if vol else 0,
            })

    return dict(grouped)


def build_synthetic_quote(day_minute: list, daily_df: pd.DataFrame, date_str: str) -> dict:
    """
    用分钟数据和日K数据构建模拟的 quote dict（模拟 get_quote 返回格式）。
    """
    if not day_minute:
        return {}

    prices = [m["price"] for m in day_minute if m["price"] > 0]
    volumes = [m["volume"] for m in day_minute]
    if not prices:
        return {}

    current_price = prices[-1]
    open_price = prices[0] if prices else current_price

    # 从日K获取前一日收盘价
    prev_close = current_price
    if not daily_df.empty:
        date_ts = pd.Timestamp(date_str)
        prev_dates = daily_df.index[daily_df.index < date_ts]
        if len(prev_dates) > 0:
            prev_close = float(daily_df.loc[prev_dates[-1], "close"])

    change_pct = (current_price / prev_close - 1) * 100 if prev_close > 0 else 0

    # 量比：当日总成交量 / 5日均量
    today_vol = sum(volumes) if volumes else 0
    volume_ratio = 1.0
    if not daily_df.empty and len(daily_df) >= 6:
        date_ts = pd.Timestamp(date_str)
        # 找 date_str 在 daily_df 中的位置
        prev_mask = daily_df.index < date_ts
        if prev_mask.any():
            recent_vols = daily_df.loc[prev_mask, "volume"].tail(5)
            if len(recent_vols) >= 5 and recent_vols.mean() > 0:
                # 需要当天日K的量
                today_daily_vol = daily_df.loc[date_ts, "volume"] if date_ts in daily_df.index else today_vol
                volume_ratio = today_daily_vol / recent_vols.mean() if recent_vols.mean() > 0 else 1.0

    return {
        "price": round(current_price, 2),
        "open": round(open_price, 2),
        "last_close": round(prev_close, 2),
        "change_pct": round(change_pct, 2),
        "volume_ratio": round(volume_ratio, 2),
        "turnover_rate": 0,   # 回测无法精确获取
        "circ_mcap_yi": 0,    # 回测无法精确获取
        "name": "",
    }


def compute_next_day_return(daily_df: pd.DataFrame, date_str: str) -> dict:
    """
    计算次日收益（从 date_str 收盘到下一个交易日收盘）。
    返回: {next_date, today_close, next_open, next_close, return_close, return_open}
    """
    if daily_df.empty:
        return {}

    date_ts = pd.Timestamp(date_str)
    if date_ts not in daily_df.index:
        return {}

    today_close = float(daily_df.loc[date_ts, "close"])

    # 找下一个交易日
    future_dates = daily_df.index[daily_df.index > date_ts]
    if len(future_dates) == 0:
        return {}

    next_date = future_dates[0]
    next_open = float(daily_df.loc[next_date, "open"])
    next_close = float(daily_df.loc[next_date, "close"])

    return {
        "next_date": next_date.strftime("%Y-%m-%d"),
        "today_close": round(today_close, 2),
        "next_open": round(next_open, 2),
        "next_close": round(next_close, 2),
        "return_open": round((next_open / today_close - 1) * 100, 2),    # 次日开盘收益
        "return_close": round((next_close / today_close - 1) * 100, 2),  # 次日收盘收益
        "next_change_pct": round((next_close / next_open - 1) * 100, 2), # 次日日内涨幅
    }


def run_backtest_for_stock(client, code: str, start_date: str, end_date: str) -> list:
    """
    对单只股票在日期区间内逐日回测尾盘策略。
    返回每天的记录列表。
    """
    name = STOCK_NAMES.get(code, code)
    print(f"\n{'='*60}")
    print(f"回测 {code} {name}")
    print(f"{'='*60}")

    # 1. 获取日K线
    print(f"  [1/3] 获取日K线...")
    daily_df = fetch_daily_kline(client, code, n_days=120)
    if daily_df.empty:
        print(f"  [ERROR] 无法获取 {code} 日K线数据")
        return []
    print(f"    获取到 {len(daily_df)} 天日K数据 ({daily_df.index[0].strftime('%Y-%m-%d')} ~ {daily_df.index[-1].strftime('%Y-%m-%d')})")

    # 2. 获取1分钟K线
    print(f"  [2/3] 获取1分钟K线（分页拉取，可能需要十几秒）...")
    minute_df = fetch_minute_bars(client, code, start_date)
    if minute_df.empty:
        print(f"  [ERROR] 无法获取 {code} 分钟K线数据")
        return []
    print(f"    获取到 {len(minute_df)} 条1分钟K线")

    # 3. 按日期分组
    print(f"  [3/3] 逐日分析尾盘信号...")
    day_groups = group_minute_by_date(minute_df)

    # 筛选回测区间内的交易日
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    target_dates = sorted([
        d for d in day_groups.keys()
        if start_ts <= pd.Timestamp(d) <= end_ts
    ])

    print(f"    回测区间 {start_date} ~ {end_date}，共 {len(target_dates)} 个交易日")

    records = []
    for date_str in target_dates:
        minute_data = day_groups[date_str]
        if len(minute_data) < 60:
            continue

        # 构建模拟quote
        quote = build_synthetic_quote(minute_data, daily_df, date_str)
        if not quote:
            continue

        # 运行分时形态识别
        open_price = quote.get("open", 0)
        last_close = quote.get("last_close", 0)
        pattern = analyze_intraday_pattern(minute_data, open_price, last_close)

        # 简化筛选（仅用可回测的指标）
        change_pct = quote.get("change_pct", 0)
        vol_ratio = quote.get("volume_ratio", 0)

        step1_ok = 3.0 <= change_pct <= 5.0
        vol_ok = vol_ratio >= 1.0

        # 综合判断
        pattern_signal = pattern.get("signal", "neutral")
        pattern_score = pattern.get("score", 0)
        filter_score = (25 if step1_ok else 0) + (15 if vol_ok else 0)
        final_score = int(pattern_score * 0.6 + filter_score * 0.4)

        # 确定信号（均线上方(偏强) 已在 analyze_intraday_pattern 中归为 buy）
        if pattern_signal == "buy":
            signal = "buy"
        elif pattern_signal == "avoid":
            signal = "avoid"
        else:
            signal = "neutral"

        # 次日收益
        next_day = compute_next_day_return(daily_df, date_str)
        next_return = next_day.get("return_close", None) if next_day else None

        # 信号是否正确
        if next_return is not None:
            if signal == "buy" and next_return > 0:
                correct = True
            elif signal == "avoid" and next_return < 0:
                correct = True
            elif signal == "neutral":
                correct = None  # 中性不评判
            else:
                correct = False
        else:
            correct = None

        record = {
            "code": code,
            "name": name,
            "date": date_str,
            "price": quote["price"],
            "change_pct": change_pct,
            "vol_ratio": vol_ratio,
            "pattern_id": pattern["pattern"],
            "pattern_name": pattern["pattern_name"],
            "pattern_score": pattern_score,
            "filter_score": filter_score,
            "final_score": final_score,
            "signal": signal,
            "tail_trend": pattern.get("tail_trend", ""),
            "above_vwap": pattern.get("above_vwap_ratio", 0),
            "next_date": next_day.get("next_date", ""),
            "next_return_close": next_return,
            "next_return_open": next_day.get("return_open", None),
            "next_change_pct": next_day.get("next_change_pct", None),
            "correct": correct,
            "details": pattern.get("details", []),
        }
        records.append(record)

    return records


def print_summary(all_records: list):
    """打印回测汇总报告"""
    if not all_records:
        print("\n无回测数据")
        return

    print(f"\n{'='*80}")
    print(f"  尾盘选股策略回测报告")
    print(f"  回测时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*80}")

    # ── 按信号分组统计 ──
    buy_records = [r for r in all_records if r["signal"] == "buy"]
    avoid_records = [r for r in all_records if r["signal"] == "avoid"]
    neutral_records = [r for r in all_records if r["signal"] == "neutral"]

    print(f"\n  ── 信号分布 ──")
    print(f"  🟢 买入信号: {len(buy_records)} 次")
    print(f"  🔴 回避信号: {len(avoid_records)} 次")
    print(f"  ⚪ 中性信号: {len(neutral_records)} 次")
    print(f"  总计: {len(all_records)} 次交易机会")

    # ── 买入信号表现 ──
    if buy_records:
        buy_returns = [r["next_return_close"] for r in buy_records if r["next_return_close"] is not None]
        buy_correct = [r for r in buy_records if r["correct"] is True]
        buy_wrong = [r for r in buy_records if r["correct"] is False]

        print(f"\n  ── 🟢 买入信号次日表现 ──")
        if buy_returns:
            avg_ret = sum(buy_returns) / len(buy_returns)
            win_count = len([x for x in buy_returns if x > 0])
            win_rate = win_count / len(buy_returns) * 100 if buy_returns else 0
            print(f"  样本数: {len(buy_returns)} | 平均收益: {avg_ret:+.2f}% | 胜率: {win_rate:.1f}% ({win_count}/{len(buy_returns)})")
            print(f"  最大收益: {max(buy_returns):+.2f}% | 最小收益: {min(buy_returns):+.2f}%")
            print(f"  预测正确(买入+次日涨): {len(buy_correct)} 次")
            print(f"  预测错误(买入+次日跌): {len(buy_wrong)} 次")

            # 逐笔明细
            print(f"\n  {'日期':<12} {'名称':<8} {'今日收盘':>8} {'涨幅%':>7} {'次日收盘':>8} {'次日收益%':>9} {'命中'}")
            print(f"  {'-'*60}")
            for r in sorted(buy_records, key=lambda x: x["date"]):
                ret = r["next_return_close"]
                ret_str = f"{ret:+.2f}%" if ret is not None else "N/A"
                hit = "✅" if r["correct"] else ("❌" if r["correct"] is False else "—")
                print(f"  {r['date']:<12} {r['name']:<8} {r['price']:>8.2f} {r['change_pct']:>+6.2f}% {r.get('next_date',''):>8} {ret_str:>9} {hit}")
        else:
            print(f"  无有效次日数据")

    # ── 回避信号表现 ──
    if avoid_records:
        avoid_returns = [r["next_return_close"] for r in avoid_records if r["next_return_close"] is not None]
        avoid_correct = [r for r in avoid_records if r["correct"] is True]

        print(f"\n  ── 🔴 回避信号次日表现 ──")
        if avoid_returns:
            avg_ret = sum(avoid_returns) / len(avoid_returns)
            down_count = len([x for x in avoid_returns if x < 0])
            down_rate = down_count / len(avoid_returns) * 100 if avoid_returns else 0
            print(f"  样本数: {len(avoid_returns)} | 平均收益: {avg_ret:+.2f}% | 下跌概率: {down_rate:.1f}% ({down_count}/{len(avoid_returns)})")
            print(f"  预测正确(回避+次日跌): {len(avoid_correct)} 次")
            print(f"  最大收益: {max(avoid_returns):+.2f}% | 最小收益: {min(avoid_returns):+.2f}%")

            print(f"\n  {'日期':<12} {'名称':<8} {'今日收盘':>8} {'涨幅%':>7} {'次日收盘':>8} {'次日收益%':>9} {'命中'}")
            print(f"  {'-'*60}")
            for r in sorted(avoid_records, key=lambda x: x["date"]):
                ret = r["next_return_close"]
                ret_str = f"{ret:+.2f}%" if ret is not None else "N/A"
                hit = "✅" if r["correct"] else ("❌" if r["correct"] is False else "—")
                print(f"  {r['date']:<12} {r['name']:<8} {r['price']:>8.2f} {r['change_pct']:>+6.2f}% {r.get('next_date',''):>8} {ret_str:>9} {hit}")
        else:
            print(f"  无有效次日数据")

    # ── 综合评估 ──
    all_with_ret = [r for r in all_records if r["next_return_close"] is not None]
    overall_correct = [r for r in all_with_ret if r["correct"] is True]
    overall_wrong = [r for r in all_with_ret if r["correct"] is False]

    print(f"\n  ── 综合评估 ──")
    if overall_correct or overall_wrong:
        total = len(overall_correct) + len(overall_wrong)
        accuracy = len(overall_correct) / total * 100 if total > 0 else 0
        print(f"  有效预测(Buy/Avoid): {total} 次")
        print(f"  方向正确: {len(overall_correct)} 次 | 方向错误: {len(overall_wrong)} 次")
        print(f"  方向准确率: {accuracy:.1f}%")

    # ── 按形态统计 ──
    print(f"\n  ── 各形态表现 ──")
    pattern_groups = defaultdict(list)
    for r in all_with_ret:
        pattern_groups[r["pattern_name"]].append(r["next_return_close"])

    print(f"  {'形态':<25} {'次数':>4} {'平均收益%':>9} {'胜率%':>7}")
    print(f"  {'-'*50}")
    for pname, rets in sorted(pattern_groups.items(), key=lambda x: -len(x[1])):
        avg = sum(rets) / len(rets) if rets else 0
        win = len([x for x in rets if x > 0]) / len(rets) * 100 if rets else 0
        print(f"  {pname:<25} {len(rets):>4} {avg:>+9.2f} {win:>7.1f}")

    print(f"\n{'='*80}")


def main():
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        print("用法: python tail_pick_backtest.py <代码1> <代码2> ... [--days N]")
        print("示例: python tail_pick_backtest.py 603986 600667 002384 600378 600584")
        print("      python tail_pick_backtest.py 603986 600667 --days 30")
        return

    # 解析参数
    codes = []
    n_days = 20
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--days" and i + 1 < len(sys.argv):
            n_days = int(sys.argv[i+1])
            i += 2
        else:
            codes.append(arg.strip().replace(",", "").replace("，", ""))
            i += 1

    codes = [c for c in codes if c]
    if not codes:
        print("未提供任何股票代码")
        return

    # 计算日期区间（往前推 n_days 再加一些缓冲用于分钟数据对齐）
    # 获取当前日期
    today = datetime.now()
    # 找最近的交易日（简化：如果是周末则往前推）
    end_date = today.strftime("%Y-%m-%d")
    start_date = (today - timedelta(days=n_days + 10)).strftime("%Y-%m-%d")

    print(f"尾盘选股策略回测")
    print(f"股票: {', '.join(f'{c}({STOCK_NAMES.get(c,c)})' for c in codes)}")
    print(f"回测区间: 近{n_days}个交易日 (from {start_date})")
    print(f"注: 策略逻辑来自 tail_pick.py，此处仅回测分时形态识别部分")
    print(f"    筛选条件(换手率/流通市值)在回测中无法精确获取，仅用涨幅+量比")

    client = get_tdx_client()

    all_records = []
    for i, code in enumerate(codes):
        if i > 0:
            time.sleep(INTERVAL_SEC + random.uniform(0, 0.5))
        records = run_backtest_for_stock(client, code, start_date, end_date)
        # 只保留最近 n_days 的数据
        cutoff = (today - timedelta(days=n_days + 2)).strftime("%Y-%m-%d")
        records = [r for r in records if r["date"] >= cutoff][:n_days]
        all_records.extend(records)
        print(f"  → {len(records)} 条有效回测记录")

    # 打印汇总
    print_summary(all_records)

    # 保存JSON
    json_path = os.path.join(os.path.dirname(__file__), "tail_pick_backtest_result.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n详细JSON已保存至: {json_path}")


if __name__ == "__main__":
    main()
