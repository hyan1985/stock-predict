#!/usr/bin/env python
"""
app.py — Stock Predict 尾盘选股 Streamlit 网页
在线: https://stock-predict-we9pcfhnkrywlst7pziusn.streamlit.app/
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

from astock_data import IS_CI, TUSHARE_TOKEN, get_minute_data, resolve_stock_input
from tail_pick import analyze_stock, format_result

st.set_page_config(
    page_title="尾盘选股分析",
    page_icon="📈",
    layout="centered",
)

st.title("尾盘选股分析")
st.caption("基于尾盘分时形态 + 三步筛选，评估次日走势倾向（规则分析，非机器学习预测）")

with st.sidebar:
    st.markdown("### 数据环境")
    st.write(f"- CI 模式: {'是' if IS_CI else '否'}")
    st.write(f"- Tushare: {'已配置' if TUSHARE_TOKEN else '未配置（日K/名称解析受限）'}")
    st.info("14:30 后运行效果最佳。结论仅供参考，不构成投资建议。")

col1, col2 = st.columns([3, 1])
with col1:
    user_input = st.text_input(
        "股票代码或名称",
        placeholder="例如 600519 或 贵州茅台",
        label_visibility="collapsed",
    )
with col2:
    run = st.button("分析", type="primary", use_container_width=True)

if run:
    if not user_input.strip():
        st.warning("请输入股票代码或名称")
        st.stop()

    code = resolve_stock_input(user_input) or user_input.strip()
    code = "".join(c for c in code if c.isdigit())
    if len(code) != 6:
        st.error(f"无法识别股票「{user_input}」，请输入 6 位 A 股代码")
        st.stop()

    with st.spinner(f"正在分析 {code} …"):
        result = analyze_stock(code)
        _, minute_src = get_minute_data(code)

    if result.get("error"):
        st.error(result["error"])
        st.stop()

    q = result.get("quote", {})
    p = result.get("pattern", {})
    s = result.get("summary", {})
    f = result.get("filters", {})

    st.markdown(f"## {result.get('verdict', '')}")
    st.markdown(
        f"**{result['code']} {result.get('name', '')}** · "
        f"综合 **{s.get('final_score', 0)}/100** · "
        f"数据: 分钟线 `{minute_src}`"
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("现价", f"{q.get('price', 0):.2f}")
    m2.metric("涨幅", f"{q.get('change_pct', 0):+.2f}%")
    m3.metric("量比", f"{q.get('volume_ratio', 0):.2f}")
    m4.metric("换手", f"{q.get('turnover_rate', 0):.2f}%")

    st.markdown("### 分时形态")
    st.write(f"**{p.get('pattern_name', 'N/A')}** · 形态分 {p.get('score', 0)}")
    st.caption(
        f"VWAP {p.get('vwap', 0):.2f} · {p.get('tail_trend', '')} · "
        f"均线上方占比 {p.get('above_vwap_ratio', 0)*100:.0f}%"
    )
    for d in p.get("details", []):
        st.write(f"- {d}")

    with st.expander("筛选检查明细", expanded=False):
        for c in f.get("checks", []):
            icon = "✅" if c["passed"] else "❌"
            st.write(f"{icon} **{c['name']}**: {c['value']} — {c['detail']}")

    st.markdown("### 操作建议")
    st.write(result.get("recommendation", ""))
    st.caption(f"分析耗时 {s.get('elapsed_seconds', 0)}s · {result.get('timestamp', '')}")

st.markdown("---")
st.caption(
    "[GitHub](https://github.com/hyan1985/stock-predict) · "
    "Tushare + 腾讯 · 规则分析，非投资建议"
)
