#!/usr/bin/env python
"""
app.py — Stock Predict 尾盘选股 Streamlit 网页
在线: https://stock-predict-we9pcfhnkrywlst7pziusn.streamlit.app/
"""

from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import streamlit as st


def _inject_streamlit_secrets() -> None:
    """Streamlit Cloud Secrets → 环境变量（与 GitHub Actions Secrets 无关）。"""
    if os.getenv("TUSHARE_TOKEN"):
        return
    try:
        token = st.secrets.get("TUSHARE_TOKEN")
        if token:
            os.environ["TUSHARE_TOKEN"] = str(token).strip()
    except Exception:
        pass


_inject_streamlit_secrets()

from astock_data import (
    IS_CI,
    MAX_STOCKS_COMPARE,
    has_tushare_token,
    parse_stock_inputs,
    resolve_stock_input,
)
from tail_pick import analyze_stock

MAX_HISTORY = 20
HISTORY_CACHE_MINUTES = 30

VERDICT_SHORT = {
    "🟢 强烈推荐": "🟢 强烈推荐",
    "🟡 可以考虑": "🟡 可以考虑",
    "🟡 形态偏多": "🟡 形态偏多",
    "🔴 建议回避": "🔴 建议回避",
    "⚪ 中性偏强": "⚪ 中性偏强",
    "⚪ 暂不建议": "⚪ 暂不建议",
}


def _init_session() -> None:
    if "search_history" not in st.session_state:
        st.session_state.search_history = []
    if "active_history_id" not in st.session_state:
        st.session_state.active_history_id = None


def _history_label(mode: str, codes: list, results: Optional[List] = None) -> str:
    if mode == "single" and results and not results[0].get("error"):
        r = results[0]
        return f"{r['code']} {r.get('name', '')[:4]}"
    if results:
        names = [
            f"{r.get('code', '')}{r.get('name', '')[:2]}"
            for r in results if not r.get("error")
        ]
        if names:
            preview = " ".join(names[:3])
            if len(names) > 3:
                preview += f" 等{len(codes)}只"
            return preview
    return ", ".join(codes)


def _add_history(query: str, mode: str, codes: list, results: list) -> None:
    key = (mode, tuple(codes))
    st.session_state.search_history = [
        h for h in st.session_state.search_history
        if (h["mode"], tuple(h["codes"])) != key
    ]
    entry = {
        "id": f"{int(time.time() * 1000)}_{codes[0]}",
        "query": query.strip(),
        "mode": mode,
        "codes": codes,
        "label": _history_label(mode, codes, results),
        "time": datetime.now().strftime("%m-%d %H:%M"),
        "ts": time.time(),
        "results": results,
    }
    st.session_state.search_history.insert(0, entry)
    st.session_state.search_history = st.session_state.search_history[:MAX_HISTORY]
    st.session_state.active_history_id = entry["id"]


def _get_active_history():
    hid = st.session_state.active_history_id
    if not hid:
        return None
    for h in st.session_state.search_history:
        if h["id"] == hid:
            return h
    return None


def _history_stale(entry: dict) -> bool:
    age_min = (time.time() - entry.get("ts", 0)) / 60
    return age_min > HISTORY_CACHE_MINUTES


def _render_sidebar_history() -> None:
    st.markdown("### 搜索历史")
    history = st.session_state.search_history
    if not history:
        st.caption("暂无记录，分析后会出现在这里。")
        return

    if st.button("清空历史", use_container_width=True, key="clear_history"):
        st.session_state.search_history = []
        st.session_state.active_history_id = None
        st.rerun()

    for item in history:
        mode_tag = "单" if item["mode"] == "single" else f"{len(item['codes'])}只"
        btn_label = f"{item['time']} [{mode_tag}] {item['label']}"
        if st.button(btn_label, key=f"hist_{item['id']}", use_container_width=True):
            st.session_state.active_history_id = item["id"]
            st.session_state.single_input = item["query"] if item["mode"] == "single" else ""
            st.session_state.compare_input = item["query"] if item["mode"] == "compare" else ""
            st.session_state._single_manual = False
            st.session_state._compare_manual = False
            st.rerun()


def _render_single(result: dict) -> None:
    if result.get("error"):
        st.error(result["error"])
        return

    q = result.get("quote", {})
    p = result.get("pattern", {})
    s = result.get("summary", {})
    f = result.get("filters", {})

    st.markdown(f"## {result.get('verdict', '')}")
    st.markdown(
        f"**{result['code']} {result.get('name', '')}** · "
        f"综合 **{s.get('final_score', 0)}/100**"
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


def _results_to_dataframe(results: list) -> pd.DataFrame:
    rows = []
    for r in results:
        if r.get("error"):
            rows.append({
                "代码": r.get("code", ""),
                "名称": "—",
                "现价": None,
                "涨幅%": None,
                "量比": None,
                "换手%": None,
                "市值(亿)": None,
                "形态": r.get("error", "")[:20],
                "形态分": None,
                "综合分": None,
                "建议": "❌ 失败",
            })
            continue
        q = r.get("quote", {})
        p = r.get("pattern", {})
        s = r.get("summary", {})
        rows.append({
            "代码": r["code"],
            "名称": r.get("name", ""),
            "现价": q.get("price", 0),
            "涨幅%": q.get("change_pct", 0),
            "量比": q.get("volume_ratio", 0),
            "换手%": q.get("turnover_rate", 0),
            "市值(亿)": q.get("circ_mcap_yi", 0),
            "形态": p.get("pattern_name", ""),
            "形态分": p.get("score", 0),
            "综合分": s.get("final_score", 0),
            "建议": VERDICT_SHORT.get(r.get("verdict", ""), r.get("verdict", "")),
        })
    df = pd.DataFrame(rows)
    if "综合分" in df.columns and not df["综合分"].isna().all():
        df = df.sort_values("综合分", ascending=False, na_position="last")
    return df.reset_index(drop=True)


def _render_compare(results: list) -> None:
    ok = [r for r in results if not r.get("error")]
    buy = sum(1 for r in ok if "🟢" in r.get("verdict", ""))
    maybe = sum(1 for r in ok if "🟡" in r.get("verdict", ""))
    avoid = sum(1 for r in ok if "🔴" in r.get("verdict", ""))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("分析数量", len(results))
    c2.metric("🟢 推荐", buy)
    c3.metric("🟡 关注", maybe)
    c4.metric("🔴 回避", avoid)

    st.markdown("### 对比总览（按综合分排序）")
    st.dataframe(
        _results_to_dataframe(results),
        use_container_width=True,
        hide_index=True,
    )

    if ok:
        best = max(ok, key=lambda r: r.get("summary", {}).get("final_score", 0))
        st.success(
            f"综合最高：**{best['code']} {best.get('name', '')}** "
            f"({best.get('summary', {}).get('final_score', 0)} 分) · {best.get('verdict', '')}"
        )

    st.markdown("### 个股详情")
    for r in sorted(
        results,
        key=lambda x: x.get("summary", {}).get("final_score", 0) or -1,
        reverse=True,
    ):
        label = f"{r.get('code', '?')} {r.get('name', r.get('code', ''))} · {r.get('verdict', '❌')}"
        with st.expander(label, expanded=len(results) <= 3):
            _render_single(r)


def _analyze_batch(codes: list, progress_bar) -> list:
    results = []
    n = len(codes)
    for i, code in enumerate(codes):
        results.append(analyze_stock(code))
        progress_bar.progress((i + 1) / n, text=f"已完成 {i + 1}/{n}：{code}")
        if i < n - 1:
            time.sleep(0.6)
    return results


def _run_single_analysis(query: str, code: str) -> None:
    with st.spinner(f"正在分析 {code} …"):
        results = [analyze_stock(code)]
    _add_history(query, "single", [code], results)
    _render_single(results[0])


def _run_compare_analysis(query: str, codes: list) -> None:
    if len(codes) == 1:
        _run_single_analysis(query, codes[0])
        return
    progress = st.progress(0, text="准备分析…")
    results = _analyze_batch(codes, progress)
    progress.empty()
    _add_history(query, "compare", codes, results)
    _render_compare(results)


def _render_history_tab() -> None:
    history = st.session_state.search_history
    if not history:
        st.info("还没有搜索记录。在「单股分析」或「多股对比」中查询后会自动保存。")
        return

    if st.button("清空全部历史", key="clear_history_tab"):
        st.session_state.search_history = []
        st.session_state.active_history_id = None
        st.rerun()

    for item in history:
        stale = _history_stale(item)
        mode_name = "单股" if item["mode"] == "single" else f"对比 {len(item['codes'])} 只"
        header = f"**{item['time']}** · {mode_name} · `{item['query']}`"
        if stale:
            header += " · _缓存已过期_"

        with st.expander(header, expanded=item["id"] == st.session_state.active_history_id):
            col_a, col_b = st.columns([1, 1])
            with col_a:
                if st.button("设为当前", key=f"view_{item['id']}"):
                    st.session_state.active_history_id = item["id"]
                    st.session_state._single_manual = False
                    st.session_state._compare_manual = False
                    st.rerun()
            with col_b:
                if st.button("重新分析", key=f"refresh_{item['id']}"):
                    if item["mode"] == "single":
                        _run_single_analysis(item["query"], item["codes"][0])
                    else:
                        _run_compare_analysis(item["query"], item["codes"])
                    st.stop()

            if item["mode"] == "single":
                _render_single(item["results"][0])
            else:
                _render_compare(item["results"])


def _render_active_history_banner(entry: dict) -> None:
    if _history_stale(entry):
        st.caption(
            f"📋 历史记录 · {entry['time']} · "
            f"缓存展示（超过 {HISTORY_CACHE_MINUTES} 分钟建议重新分析）"
        )
    else:
        st.caption(f"📋 历史记录 · {entry['time']} · 侧边栏可切换其他记录")


_init_session()

st.set_page_config(
    page_title="尾盘选股分析",
    page_icon="📈",
    layout="wide",
)

st.title("尾盘选股分析")
st.caption("基于尾盘分时形态 + 三步筛选，评估次日走势倾向（规则分析，非机器学习预测）")

with st.sidebar:
    st.markdown("### 数据环境")
    st.write(f"- CI 模式: {'是' if IS_CI else '否'}")
    if has_tushare_token():
        st.write("- Tushare: ✅ 已配置")
    else:
        st.write("- Tushare: ❌ 未配置")
        st.caption(
            "请在 Streamlit Cloud → App → **Settings → Secrets** 添加 `TUSHARE_TOKEN`。"
        )
    st.divider()
    _render_sidebar_history()
    st.divider()
    st.info("14:30 后运行效果最佳。结论仅供参考，不构成投资建议。")
    st.caption(f"多股对比最多 {MAX_STOCKS_COMPARE} 只 · 历史保留 {MAX_HISTORY} 条（本次会话）")

tab_single, tab_compare, tab_history = st.tabs(["单股分析", "多股对比", "历史记录"])

active = _get_active_history()

with tab_single:
    if active and active["mode"] == "single" and not st.session_state.get("_single_manual"):
        _render_active_history_banner(active)
        _render_single(active["results"][0])

    col1, col2 = st.columns([3, 1])
    with col1:
        st.text_input(
            "股票代码或名称",
            placeholder="例如 600519 或 贵州茅台",
            key="single_input",
            label_visibility="collapsed",
        )
    with col2:
        run_single = st.button("分析", type="primary", use_container_width=True, key="run_single")

    if run_single:
        st.session_state._single_manual = True
        st.session_state.active_history_id = None
        query = st.session_state.single_input
        if not query.strip():
            st.warning("请输入股票代码或名称")
        else:
            code = resolve_stock_input(query) or query.strip()
            code = "".join(c for c in code if c.isdigit())
            if len(code) != 6:
                st.error(f"无法识别「{query}」，请输入 6 位 A 股代码")
            else:
                _run_single_analysis(query, code)

with tab_compare:
    if active and active["mode"] == "compare" and not st.session_state.get("_compare_manual"):
        _render_active_history_banner(active)
        if len(active["codes"]) == 1:
            _render_single(active["results"][0])
        else:
            _render_compare(active["results"])

    st.text_area(
        "输入多只股票（逗号、空格或换行分隔）",
        placeholder="600519, 000858, 002008\n贵州茅台",
        height=100,
        key="compare_input",
    )
    run_compare = st.button("开始对比", type="primary", key="run_compare")

    if run_compare:
        st.session_state._compare_manual = True
        st.session_state.active_history_id = None
        query = st.session_state.compare_input
        if not query.strip():
            st.warning("请输入至少一只股票")
        else:
            codes, parse_errors = parse_stock_inputs(query)
            if parse_errors:
                st.warning("以下输入无法识别，已跳过：" + "、".join(parse_errors))
            if not codes:
                st.error("没有有效的 6 位股票代码")
            else:
                _run_compare_analysis(query, codes)

with tab_history:
    _render_history_tab()

st.markdown("---")
st.caption(
    "[GitHub](https://github.com/hyan1985/stock-predict) · "
    "Tushare + 腾讯 · 规则分析，非投资建议"
)
