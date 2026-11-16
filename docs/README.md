<p align="center">
  <h1 align="center"><br><ins>MY-ARXIV-WEEKLY</ins><br>每周追踪 城市经济 / 金融学 / AI与经济学 / 企业创新</h1>
</p>

## 说明

本仓库由 [Vincentqyw/cv-arxiv-daily](https://github.com/Vincentqyw/cv-arxiv-daily) fork 而来，已改造为本人研究方向的论文追踪器：每天抓取 arXiv 新论文，每周一汇总 arXiv + 期刊元数据并推送一封邮件周报。

## Overview

- `daily_arxiv.py`：按 `config.yaml` 的关键词抓取 arXiv 并生成 README / GitPage / 归档。
- `journal_sources.py`：只读的期刊元数据源（OpenAlex，可选 Elsevier Scopus）。
- `weekly_report.py`：只读聚合器，按周窗口合并去重并发送一封邮件。
- `config.yaml`：研究方向关键词、周报窗口、SMTP 与数据源配置。

## Usage

<details>
  <summary>Table of Contents</summary>

1. 本仓库已 fork 到 [winslowst/my-arxiv-weekly](https://github.com/winslowst/my-arxiv-weekly)。
2. 修改配置：
    - `.github/workflows/cv-arxiv-daily.yml` 与 `update_paper_links.yml` 中的 `GITHUB_USER_NAME` / `GITHUB_USER_EMAIL`（已改为 `winslowst`）。
    - `config.yaml` 中的 `user_name`、四个研究方向的 `keywords` / `journal_queries`、`weekly_report.to`。
    - 推送到远端仓库。
3. 配置 GitHub Actions：
    - Settings → Actions → Workflow permissions → `Read and write permissions` 并保存。
      ![](../assets/4-ga-2-1.png)
    - Actions 页启用工作流：`Run Arxiv Papers Daily`、`Run Update Paper Links Weekly`、`Weekly Paper Report`，并各运行一次。
      ![](../assets/4-ga-3-1.png)
      ![](../assets/4-ga-5-1.png)
      ![](../assets/4-ga-7.png)
      ![](../assets/4-ga-8.png)
      ![](../assets/4-ga-9.png)
4. GitPages（可选）：
    - Settings → Pages → Source 选 `Deploy from a branch`，Branch 选 `main` 与 `/docs`。
      ![](../assets/5-pages-1.png)
    - 打开 https://winslowst.github.io/my-arxiv-weekly
5. 调整研究方向（可选）：编辑 `config.yaml` 的 `keywords` 与 `weekly_report.journal_queries`，推送后手动重跑一次 Action。
6. 归档（可选）：`README.md` / `docs/index.md` 每个方向只保留最新 `max_papers_in_readme` 篇，其余按年份归档到 `docs/archive/<year>/<Keyword>.md`（GitHub 对超过 512 KB 的 Markdown 停止渲染）。
7. 代码链接：PapersWithCode 已停止服务，代码链接从论文摘要、[Hugging Face papers](https://huggingface.co/papers) 与 GitHub code search 查找。GitHub 搜索需要 `GITHUB_TOKEN`（由工作流提供），限 10 次/分钟，因此只在每周任务中运行；`docs/code-checks.json` 记录检查时间。

</details>

## Research directions and data sources

本仓库追踪四个方向（按本人指定）：**城市经济、金融学、AI与经济学、企业创新**。

- `config.yaml` → `keywords`：`daily_arxiv.py` 每日抓取 arXiv 使用。检索词为英文短语（arXiv 几乎不收录中文文献）；刻意不设 `categories`，因为 `daily_arxiv.py` 还会按 `primary_category` 二次过滤，对非 CS 主题过窄。
- `config.yaml` → `weekly_report.journal_queries`：每个方向一条宽泛检索式，供期刊源使用。

| Source | 覆盖 | 访问方式 | 状态 |
|---|---|---|---|
| arXiv | 预印本；金融学 / AI与经济学 覆盖较好，城市经济 / 企业创新 相对少 | 免费，现有 `arxiv` 库 | 每日抓取 |
| OpenAlex | 2.5 亿+ 作品，含期刊名 / DOI / 发表日期 | **2026-02 起需免费 API key**（旧的 `mailto` polite pool 已取消）；每日约 $1 免费额度 | 主期刊源，已启用 |
| Elsevier Scopus / ScienceDirect | 5000 家出版社、7800 万条记录 | APIKey（非商业学术用途免费，但新 key 默认无数据权限，完整访问需机构订阅），有周配额与 HTTP 429 | 可选，默认关闭 |
| CNKI / 万方 | 中文期刊 | CNKI 无公开检索 API；万方按次付费 | **不纳入**：不抓取、不绕过平台限制 |

只抓取元数据（题名 / 作者 / 期刊 / DOI / 日期），不取全文、不做批量收割。`requirements.txt` 保持 `requests / arxiv / pyyaml`，不新增依赖、数据库或服务。

周报在运行时合并各源，按 **DOI → arXiv ID → 标题归一化** 去重，并给每条标注来源与日期口径（arXiv 用 `updated`；期刊用 `publication_date` / `coverDate`）。窗口为 `Asia/Shanghai` 的上一完整周：`周一 00:00 ≤ 日期 < 下周一 00:00`，左闭右开。单个数据源失败会跳过并在邮件中注明，不会中断周报。

## 每周邮件周报

`weekly_report.py` 是**只读**聚合器：读取 `docs/cv-arxiv-daily.json` 并调用期刊 API，只发一封邮件；不写仓库文件、不创建 commit。

```bash
python weekly_report.py                              # dry-run：打印邮件正文
python weekly_report.py --send                        # 真正发送
python weekly_report.py --now 2026-11-09              # 用固定"今天"测试
python weekly_report.py --arxiv_json /path/test.json  # 用测试数据测试
```

启用发送前，在 Settings → Secrets and variables → Actions 添加以下 **Secrets**，切勿把密钥写进 `config.yaml` 或提交：

| Secret | 用途 |
|---|---|
| `MAIL_USERNAME` | 发件地址 |
| `MAIL_PASSWORD` | SMTP 授权码 / 应用密码 |
| `MAIL_TO` | 可选；覆盖 `weekly_report.to`（多个收件人用逗号分隔） |
| `OPENALEX_API_KEY` | openalex.org 的免费 key |
| `ELSEVIER_API_KEY` | 可选；仅当 `sources.elsevier: true` 时需要 |

同时填写 `weekly_report.to` / `cc`，并核对 `smtp_host` / `smtp_port` / `smtp_ssl` / `starttls`（默认 QQ 邮箱 `smtp.qq.com:465` SSL；163 为 `smtp.163.com:465` SSL；Outlook 为 `smtp.office365.com:587` STARTTLS）。

`.github/workflows/weekly-report.yml` 于每周一 09:00 UTC（北京时间 17:00）运行，比每周代码链接任务晚一小时，并共享 `update-papers` 并发组。存在 `MAIL_PASSWORD` 时发送；否则（或手动勾选 `dry_run` 时）只打印报告，因此没有配置 Secrets 的 fork 也不会失败。fork 默认禁用工作流，需在 Actions 页启用三个工作流，并为两个更新工作流授予 **Read and write permissions**。

验收清单：

1. `Run Arxiv Papers Daily` 仍成功并产出常规 commit 与生成文件。
2. `python weekly_report.py --dry-run` 打印完整报告；构造一条窗口内、一条窗口外记录验证筛选、去重与分组；四个方向之外的主题不会出现在邮件里（方向名即白名单）。
3. 无 `ELSEVIER_API_KEY` 时报告仍可用 arXiv + OpenAlex 工作，并记录被跳过的数据源；模拟超时 / 429 不会中断邮件。
4. 配置 Secrets 后手动 `--send` 恰好发出一封 UTF-8 邮件，之后按周定时发送；日志中无明文凭据。
5. `requirements.txt` 不变，周报任务不产生 commit。
