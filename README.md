# Stock Predict · 尾盘选股分析

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://stock-predict-we9pcfhnkrywlst7pziusn.streamlit.app/)

A 股尾盘选股工具：基于**分时形态识别 + 三步筛选**，对单只股票给出次日走势倾向与操作建议。

**在线体验 → [stock-predict.streamlit.app](https://stock-predict-we9pcfhnkrywlst7pziusn.streamlit.app/)**

> 规则化分析，非机器学习预测。14:30 后运行效果最佳。结论仅供参考，不构成投资建议。

---

## 功能

- 输入 **6 位代码**或**股票名称**，一键分析
- 六种尾盘分时形态 + 涨幅/量比/换手/市值/K 线筛选
- 综合评分与 buy / neutral / avoid 建议
- 命令行批量分析、GitHub Actions 定时任务、历史回测

## 在线使用

打开 **[在线分析页面](https://stock-predict-we9pcfhnkrywlst7pziusn.streamlit.app/)**，输入代码或名称（如 `600519`、`贵州茅台`），点击「分析」。

## 本地运行

```bash
git clone https://github.com/hyan1985/stock-predict.git
cd stock-predict
pip install -r requirements.txt
export TUSHARE_TOKEN=你的token   # 推荐：日 K、名称解析

# 命令行
python tail_pick.py 600519 000858

# 本地网页
streamlit run app.py
```

## 数据层

| 数据 | 本地 | Streamlit / GitHub Actions |
|------|------|---------------------------|
| 实时行情 | 腾讯 + Tushare | 腾讯 + Tushare |
| 分钟 K | mootdx → 腾讯 | 腾讯 |
| 日 K | Tushare → mootdx | Tushare |

环境变量：`TUSHARE_TOKEN`（[Tushare Pro](https://tushare.pro)）。CI 环境设 `CI=true` 时跳过 mootdx。

## 部署说明

### Streamlit Cloud（已部署）

- 仓库：[hyan1985/stock-predict](https://github.com/hyan1985/stock-predict)
- 入口文件：`app.py`
- Secrets：`TUSHARE_TOKEN`

### GitHub Actions

`.github/workflows/tail-pick.yml` 工作日 **14:35（北京时间）** 自动分析，或在 Actions 页手动触发。

仓库 **Settings → Secrets → Actions** 中配置 `TUSHARE_TOKEN`。

## 项目结构

| 文件 | 说明 |
|------|------|
| `app.py` | Streamlit 在线分析页 |
| `tail_pick.py` | 分析引擎（形态 + 筛选 + 综合评分） |
| `astock_data.py` | 数据层（Tushare / 腾讯 / mootdx） |
| `tail_pick_backtest.py` | 历史回测 |

## 策略概要

- **形态**：横盘、先涨后跌、全天受压、冲高回踩、小阳缓升、尾盘急拉等
- **筛选**：涨幅 3%–5%、量比 ≥ 1、换手 5%–10%、流通市值 50–200 亿、K 线确认
- **评分**：形态 60% + 筛选 40%

## 免责声明

本工具仅提供数据与规则化分析，**不构成任何投资建议**。股市有风险，投资需谨慎。
