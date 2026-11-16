# 论文周报 · 城市经济 / 金融学 / AI与经济学 / 企业创新

每天抓取 arXiv 新论文；每周一汇总 arXiv + OpenAlex 期刊元数据，去重后推送一封邮件周报。
本仓库由 [Vincentqyw/cv-arxiv-daily](https://github.com/Vincentqyw/cv-arxiv-daily) fork 改造，已清除上游 CV 论文数据，只保留本人四个研究方向。

> 本文件由 `Run Arxiv Papers Daily` 工作流每日重绘为「按方向分组的论文表格」（顶部项目说明会保留）。
> 完整配置说明见 [docs/README.md](docs/README.md)。

## 四个研究方向

| 方向 | arXiv 检索词（节选） | 期刊检索式（OpenAlex） |
|---|---|---|
| 城市经济 | Urban Economics / Agglomeration Economies / Housing Market / Urban Land Use / City Size / Urban Labor Market | `urban economics` |
| 金融学 | Corporate Finance / Asset Pricing / Banking / Financial Markets / Household Finance | `corporate finance` |
| AI与经济学 | Artificial Intelligence Economics / Machine Learning Economics / Large Language Model Economics / Causal Machine Learning / Computational Economics | `machine learning economics` |
| 企业创新 | Firm Innovation / Corporate Innovation / R&D Investment / Patent / Technological Innovation / Entrepreneurship | `firm innovation` |

检索词写在 [config.yaml](config.yaml) 的 `keywords` 与 `weekly_report.journal_queries`，可自行增删。arXiv 几乎不收录中文文献，故检索词用英文短语。

## 每周推送报告

- **时间**：`.github/workflows/weekly-report.yml`，每周一 09:00 UTC（北京时间 **17:00**）。每日 08:00（北京时间）由 `Run Arxiv Papers Daily` 抓取 arXiv。
- **窗口**：上一完整周，Asia/Shanghai，`周一 00:00 ≤ 论文日期 < 下周一 00:00`（左闭右开）。
- **内容**：四个方向的新论文，按 **DOI → arXiv ID → 标题归一化** 去重，每条标注来源（arXiv / OpenAlex / Elsevier Scopus）与日期口径（arXiv 用 `updated`；期刊用 `publication_date` / `coverDate`），并附期刊名与 DOI 链接。
- **数据源**：arXiv（每日抓取）+ OpenAlex（期刊元数据，只读）；Elsevier Scopus 可选，默认关闭。
- **只取元数据**：题名、作者、期刊、DOI、日期；不取全文、不做批量收割。

### 启用邮件发送

在仓库 **Settings → Secrets and variables → Actions** 添加：

| Secret | 用途 |
|---|---|
| `MAIL_USERNAME` | 发件地址 |
| `MAIL_PASSWORD` | SMTP 授权码 / 应用密码 |
| `MAIL_TO` | 可选；覆盖 `weekly_report.to`，多个收件人用逗号分隔 |
| `OPENALEX_API_KEY` | openalex.org 免费 key |
| `ELSEVIER_API_KEY` | 可选；仅当 `config.yaml` 里 `sources.elsevier: true` 时需要 |

同时在 [config.yaml](config.yaml) 填 `weekly_report.to`，并按邮箱服务商核对 `smtp_host` / `smtp_port` / `smtp_ssl` / `starttls`（默认 QQ 邮箱 `smtp.qq.com:465` SSL）。
**未配置 `MAIL_PASSWORD` 时工作流自动降级为 dry-run**（只打印报告、不发送），因此没有 Secrets 的 fork 也不会失败。

fork 的工作流默认禁用：请在 Actions 页启用 `Run Arxiv Papers Daily`、`Run Update Paper Links Weekly`、`Weekly Paper Report`，并为前两个更新工作流授予 **Read and write permissions**。

## 目录结构

| 路径 | 作用 |
|---|---|
| `config.yaml` | 研究方向关键词、周报窗口、SMTP、数据源开关 |
| `daily_arxiv.py` | 每日抓 arXiv，生成 README / GitPage / 归档与代码链接 |
| `journal_sources.py` | 只读期刊元数据源（OpenAlex / 可选 Scopus） |
| `weekly_report.py` | 只读周报聚合器：合并、去重、渲染、发信 |
| `.github/workflows/weekly-report.yml` | 每周一推送周报 |
| `docs/cv-arxiv-daily*.json` | 各方向论文记录（工作流自动维护；初始为空） |
| `docs/README.md` | 完整配置、数据源能力对照、验收清单 |

## 本地试跑

```bash
pip install -r requirements.txt
python weekly_report.py --dry-run                  # 打印本周邮件正文
python weekly_report.py --now 2026-11-16          # 用固定「今天」测试窗口
python daily_arxiv.py                             # 手动抓一次 arXiv（会改写 README 与 JSON）
```

## 口径与局限

1. arXiv 记录按 `updated` 日期，期刊记录按 `publication_date` / `coverDate`，两者口径不同，周报中会分别标注。
2. 四个方向在 arXiv 的覆盖不均衡：金融学 / AI与经济学 命中较多，城市经济 / 企业创新 主要靠期刊源补足；宽泛检索式会带入噪声，可在 `config.yaml` 里收紧。
3. OpenAlex 自 2026-02 起需免费 API key；无 key 时该源会被跳过并在邮件中注明，不会中断周报。
4. Elsevier Scopus / ScienceDirect 需要 APIKey 与机构订阅权限，默认关闭；CNKI / 万方无公开可用 API，不纳入。
5. `weekly_report.py` 不写仓库文件、不产生 commit；只有每日与每周代码链接任务会提交生成文件。
6. 邮件凭据只从环境变量 / GitHub Secrets 读取，绝不写进配置文件或提交。
