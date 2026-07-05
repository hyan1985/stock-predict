# 尾盘选股工具包 (Tail-Pick)

基于尾盘 30 分钟分时形态 + 三步筛选，评估 A 股次日走势倾向。

## 快速开始

```bash
pip install -r requirements.txt
export TUSHARE_TOKEN=你的token   # 可选但推荐

# 命令行分析
python tail_pick.py 600519 000858

# 网页（本地）
streamlit run app.py
```

## 数据层

| 数据 | 本地 | GitHub / Streamlit |
|------|------|-------------------|
| 实时行情 | 腾讯 + Tushare 补充 | 腾讯 + Tushare |
| 分钟 K | mootdx → 腾讯 | 腾讯（无 stk_mins 权限） |
| 日 K | Tushare → mootdx | Tushare |

环境变量：`TUSHARE_TOKEN`；CI 中自动设 `CI=true` 跳过 mootdx。

## 部署 Streamlit（推荐）

1. 推送本仓库到 GitHub
2. 打开 [share.streamlit.io](https://share.streamlit.io)，连接仓库
3. Main file: `app.py`
4. Secrets 添加：

```toml
TUSHARE_TOKEN = "你的token"
```

## GitHub Actions

`.github/workflows/tail-pick.yml` 每天 14:35（北京时间）自动分析，或在 Actions 页手动触发。

仓库 Settings → Secrets → `TUSHARE_TOKEN` 必填（日 K）；分钟线走腾讯 HTTP，无需分钟权限。

## 文件说明

| 文件 | 用途 |
|------|------|
| `app.py` | Streamlit 网页 |
| `tail_pick.py` | 分析引擎 |
| `astock_data.py` | 数据层（Tushare + 腾讯 + mootdx） |
| `tail_pick_backtest.py` | 历史回测 |

## 免责声明

本工具仅提供规则化分析，不构成投资建议。股市有风险，投资需谨慎。
