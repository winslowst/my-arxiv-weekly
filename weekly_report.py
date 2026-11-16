#!/usr/bin/env python3
"""Weekly paper digest for my-arxiv-weekly.

Read-only aggregator: reads docs/cv-arxiv-daily.json (arXiv papers), queries
journal metadata sources for the previous complete week, merges/deduplicates
and sends ONE email. It never writes repository files and never creates a
commit, so it does not conflict with the two existing update workflows.

Only the Python standard library plus PyYAML (already in requirements.txt)
and the local journal_sources module are used.

    python weekly_report.py                 # dry-run: print the email body
    python weekly_report.py --send          # really send (needs Secrets)
    python weekly_report.py --now 2026-11-09 --arxiv_json test.json
"""

import argparse
import datetime as dt
import html
import json
import logging
import os
import re
import smtplib
from collections import defaultdict


def load_dotenv(path=None):
    """Load KEY=VALUE from a local .env into os.environ without overwriting.

    Resolution order: explicit path, current directory, script directory. The
    .env file is gitignored so API keys never enter the repository.
    """
    candidates = []
    if path:
        candidates.append(path)
    candidates.append(os.path.join(os.getcwd(), ".env"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    for candidate in candidates:
        if not os.path.exists(candidate):
            continue
        with open(candidate, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        return

from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

import yaml

from journal_sources import (
    SourceUnavailable,
    fetch_elsevier,
    fetch_openalex,
    make_record,
)

# China has no DST; a fixed +08:00 avoids zoneinfo/tzdata issues on Windows.
TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")

log = logging.getLogger("weekly_report")

ROW_RE = re.compile(
    r"\|\*\*(\d{4}-\d{2}-\d{2})\*\*\|\*\*(.*?)\*\*\|(.*?)\|"
    r"\[([^\]]+)\]\(([^)]+)\)\|(.*?)\|\s*$", re.S)
CODE_URL_RE = re.compile(r"https?://[^)\s]+")
SOURCE_LABELS = {
    "arxiv": "arXiv",
    "openalex": "OpenAlex",
    "elsevier": "Elsevier Scopus",
}


def load_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def week_window(now=None, days=7):
    """Previous complete week: [Monday-7d, Monday) in UTC+8, left-closed."""
    now = now or dt.datetime.now(TZ)
    local_date = now.astimezone(TZ).date()
    end = local_date - dt.timedelta(days=local_date.weekday())
    start = end - dt.timedelta(days=int(days))
    return start, end


def parse_row(row):
    """Parse one markdown row stored in docs/cv-arxiv-daily.json."""
    match = ROW_RE.match(row or "")
    if not match:
        return None
    date_str, title, authors, arxiv_id, paper_url, code_cell = match.groups()
    code_url = None
    if code_cell and code_cell.strip() != "null":
        code_match = CODE_URL_RE.search(code_cell)
        code_url = code_match.group(0).rstrip(")") if code_match else None
    return {
        "date": date_str,
        "title": title.strip(),
        "authors": authors.strip(),
        "arxiv_id": re.sub(r"v\d+$", "", arxiv_id.strip()),
        "paper_url": paper_url.strip(),
        "code_url": code_url,
    }


def collect_arxiv(path, whitelist, start, end, max_per_topic):
    """Read-only: pick whitelisted topics whose updated date is in the window."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        log.warning("arXiv json unavailable: %s", type(exc).__name__)
        return [], "arXiv JSON 读取失败（%s）" % type(exc).__name__

    records, counts = [], defaultdict(int)
    for topic, papers in (data or {}).items():
        if topic not in whitelist:
            continue
        for _, row in (papers or {}).items():
            parsed = parse_row(row)
            if not parsed:
                continue
            if not (start.isoformat() <= parsed["date"] < end.isoformat()):
                continue
            if counts[topic] >= max_per_topic:
                break
            counts[topic] += 1
            records.append(make_record(
                "arxiv", topic, parsed["title"], parsed["authors"],
                parsed["date"], "updated", parsed["paper_url"],
                arxiv_id=parsed["arxiv_id"], code_url=parsed["code_url"]))
    return records, None


def collect_journals(wr, whitelist, start, end):
    """One source failing never aborts the report.

    OpenAlex `search` is a fuzzy full-text search; a title-level gate keeps
    only records whose title actually mentions the direction (configurable in
    `weekly_report.journal_title_gate`). arXiv records are never gated.
    """
    records, skipped = [], set()
    sources = wr.get("sources") or {}
    queries = wr.get("journal_queries") or {}
    gates = wr.get("journal_title_gate") or {}
    timeout = wr.get("request_timeout", 20)
    max_results = wr.get("max_results_per_source", 30)
    elsevier_cfg = wr.get("elsevier") or {}
    gated_out = 0

    def title_ok(record, topic):
        gate = gates.get(topic) or []
        if not gate:
            return True
        title = (record.get("title") or "").lower()
        return any(keyword in title for keyword in gate)

    for source, label in (("openalex", "OpenAlex"), ("elsevier", "Elsevier Scopus")):
        if not sources.get(source):
            continue
        try:
            for topic, query in queries.items():
                if topic not in whitelist:
                    continue
                if source == "openalex":
                    found = fetch_openalex(
                        query, start, end, topic,
                        api_key=os.environ.get("OPENALEX_API_KEY") or None,
                        max_results=max_results, timeout=timeout)
                else:
                    found = fetch_elsevier(
                        query, start, end, topic,
                        api_key=os.environ.get("ELSEVIER_API_KEY") or None,
                        max_results=elsevier_cfg.get("max_results", max_results),
                        timeout=timeout, view=elsevier_cfg.get("view", "BASIC"))
                for record in found:
                    if title_ok(record, topic):
                        records.append(record)
                    else:
                        gated_out += 1
        except SourceUnavailable as exc:
            skipped.add("%s：%s" % (label, exc))
            log.warning("source skipped: %s (%s)", label, exc)
    if gated_out:
        skipped.add("标题相关性闸门剔除 %d 篇（OpenAlex 模糊检索噪声）" % gated_out)
    return records, sorted(skipped)


def merge_dedup(records, max_per_topic):
    """DOI -> arXiv id -> normalized title; group by topic, newest first."""
    unique = {}
    for record in sorted(records, key=lambda r: r["date"], reverse=True):
        unique.setdefault(record["dedup_key"], record)
    groups = defaultdict(list)
    for record in unique.values():
        groups[record["topic"]].append(record)
    for topic in groups:
        groups[topic].sort(key=lambda r: r["date"], reverse=True)
        groups[topic] = groups[topic][:max_per_topic]
    return groups


def render(groups, order, start, end, now, wr, skipped, sources_enabled,
           arxiv_note=None):
    all_records = [r for topic in order for r in groups.get(topic, [])]
    total = len(all_records)
    with_code = sum(1 for r in all_records if r.get("code_url"))
    by_source = defaultdict(int)
    for record in all_records:
        by_source[record["source"]] += 1

    iso_year, iso_week, _ = start.isocalendar()
    week_tag = "%s-W%02d" % (iso_year, iso_week)
    subject = str(wr.get("subject_template", "论文周报 {week}｜{start} ~ {end}")).format(
        week=week_tag, start=start.isoformat(), end=end.isoformat())
    generated = now.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
    source_names = [SOURCE_LABELS.get(s, s) for s in sources_enabled]

    lines = [
        subject,
        "",
        "生成时间：%s（UTC+8）" % generated,
        "汇总范围：%s 00:00 ≤ 论文日期 < %s 00:00（Asia/Shanghai，%s 天，左闭右开）"
        % (start.isoformat(), end.isoformat(), wr.get("window_days", 7)),
        "数据源：%s" % "、".join(source_names),
    ]
    if skipped:
        lines.append("本期数据说明：" + "；".join(skipped))
    if arxiv_note:
        lines.append("提示：" + arxiv_note)
    lines += [
        "",
        "本期共 %d 篇" % total,
        "按方向：" + (" / ".join("%s %d" % (t, len(groups.get(t, []))) for t in order)
                     or "无"),
        "按来源：" + (" / ".join("%s %d" % (SOURCE_LABELS.get(k, k), v)
                              for k, v in sorted(by_source.items())) or "无"),
        "含代码链接：%d 篇（%s）" % (
            with_code, ("%.1f%%" % (100.0 * with_code / total)) if total else "—"),
        "",
    ]

    if total == 0:
        lines += ["本周无新增论文。", ""]
    for topic in order:
        items = groups.get(topic, [])
        if not items:
            continue
        lines += ["─" * 34, "【%s】（%d 篇）" % (topic, len(items))]
        for idx, record in enumerate(items, 1):
            lines.append(
                " %d. [%s] %s（%s）%s"
                % (idx, SOURCE_LABELS.get(record["source"], record["source"]),
                   record["date"], record["date_basis"], record["title"]))
            detail = ["   作者：%s" % record["authors"]]
            if record.get("journal"):
                detail.append("期刊：%s" % record["journal"])
            if record.get("doi"):
                detail.append("DOI：%s" % record["doi"])
            elif record.get("arxiv_id"):
                detail.append("arXiv：%s" % record["arxiv_id"])
            detail.append("链接：%s" % (record["url"] or "—"))
            if record.get("code_url"):
                detail.append("代码：%s" % record["code_url"])
            lines.append("｜".join(detail))
        lines.append("")

    lines += [
        "─" * 34,
        "仓库：%s" % wr.get("repo_readme_url", ""),
        "口径说明：arXiv 记录按 updated 日期，期刊记录按 publication_date / coverDate 筛选；"
        "按 DOI → arXiv ID → 标题归一化去重。",
    ]
    body_text = "\n".join(lines)

    if str(wr.get("format", "text")).lower() == "html":
        esc = html.escape
        parts = [
            "<html><body style=\"font-family:-apple-system,Segoe UI,Arial,sans-serif;"
            "font-size:14px;line-height:1.7;color:#222\">",
            "<h2>%s</h2>" % esc(subject),
        ]
        for line in lines[1:]:
            if not line.strip():
                parts.append("<p>&nbsp;</p>")
            elif line.startswith("【") or line.startswith("─"):
                parts.append("<h3>%s</h3>" % esc(line))
            elif line.startswith(" ") or line.startswith("   "):
                parts.append("<div style=\"padding-left:12px\">%s</div>" % esc(line))
            else:
                parts.append("<div>%s</div>" % esc(line))
        parts.append("</body></html>")
        return subject, "\n".join(parts), "html"
    return subject, body_text, "plain"


def render_obsidian(groups, order, start, end, now, wr, skipped, sources_enabled,
                    arxiv_note=None):
    """Render an Obsidian-ready Markdown note with YAML frontmatter.

    Only metadata is written (title / authors / journal / DOI / date). The
    frontmatter marks the note as ``metadata_only`` so it can never be mistaken
    for a full-text close-reading note in the vault.
    """
    all_records = [r for topic in order for r in groups.get(topic, [])]
    total = len(all_records)
    with_code = sum(1 for r in all_records if r.get("code_url"))
    by_source = defaultdict(int)
    for record in all_records:
        by_source[record["source"]] += 1

    iso_year, iso_week, _ = start.isocalendar()
    week_tag = "%d-W%02d" % (iso_year, iso_week)
    source_names = [SOURCE_LABELS.get(s, s) for s in sources_enabled]

    def y(value):
        return str(value).replace('"', "'").replace("\n", " ")

    lines = ["---", 'title: "论文周报 %s"' % y(week_tag),
             'aliases: ["%s 周报"]' % y(week_tag),
             "tags:", "  - paper-digest", "  - weekly-digest",
             'created: "%s"' % now.astimezone(TZ).strftime("%Y-%m-%d"),
             'period_start: "%s"' % start.isoformat(),
             'period_end: "%s"' % end.isoformat(),
             'source: "%s"' % y(" + ".join(source_names)),
             "total: %d" % total,
             "counts:"]
    for topic in order:
        lines.append('  "%s": %d' % (y(topic), len(groups.get(topic, []))))
    lines += ["evidence_status: metadata_only", "---", "",
              "# 论文周报 %s（%s ~ %s）" % (week_tag, start, end), "",
              "> 本笔记由 weekly_report.py 自动生成，只含元数据（题名 / 作者 / 期刊 / DOI / 日期）。",
              "> `evidence_status: metadata_only`：未逐字核对全文，不可当作精读笔记引用。", "",
              "## 本期速览", "",
              "- 汇总范围：%s 00:00 ≤ 论文日期 < %s 00:00（Asia/Shanghai，%d 天，左闭右开）。"
              % (start, end, wr.get("window_days", 7)),
              "- 本期共 **%d 篇**；按方向：%s。"
              % (total, " / ".join("%s %d" % (t, len(groups.get(t, []))) for t in order) or "无"),
              "- 按来源：%s；含代码链接 %d 篇。"
              % (" / ".join("%s %d" % (SOURCE_LABELS.get(k, k), v)
                            for k, v in sorted(by_source.items())) or "无", with_code)]
    if skipped:
        lines.append("- 数据说明：%s。" % "；".join(skipped))
    if arxiv_note:
        lines.append("- 提示：%s。" % arxiv_note)
    lines.append("")

    if total == 0:
        lines += ["本周无新增论文。", ""]
    for topic in order:
        items = groups.get(topic, [])
        if not items:
            continue
        lines += ["## %s（%d 篇）" % (topic, len(items)), ""]
        for idx, record in enumerate(items, 1):
            lines.append(
                "%d. **%s**（%s，%s）"
                % (idx, record["title"], record["date"], record["date_basis"]))
            detail = ["作者：%s" % record["authors"]]
            if record.get("journal"):
                detail.append("期刊：%s" % record["journal"])
            if record.get("doi"):
                detail.append("DOI：[%s](https://doi.org/%s)" % (record["doi"], record["doi"]))
            elif record.get("arxiv_id"):
                detail.append("arXiv：%s" % record["arxiv_id"])
            detail.append("链接：%s" % (record["url"] or "—"))
            if record.get("code_url"):
                detail.append("代码：%s" % record["code_url"])
            lines.append("   - " + "｜".join(detail))
        lines.append("")

    lines += ["## 口径与局限", "",
              "- arXiv 记录按 `updated` 日期筛选；期刊记录按 `publication_date` / `coverDate` 筛选。",
              "- 按 DOI → arXiv ID → 标题归一化去重。",
              "- 只抓取元数据，不取全文；无摘要条目只做标题级描述。",
              "- 数据源失败会跳过并在「数据说明」中标注，不会中断周报。", ""]
    return "\n".join(lines) + "\n"


def append_obsidian_index(index_path, week_tag, start, end, note_name, total):
    """Append one row to the weekly-digest index note, creating it if needed."""
    row = "| %s | %s ~ %s | %d | [[%s]] |" % (week_tag, start, end, total,
                                             note_name[:-3] if note_name.endswith(".md") else note_name)
    header = ["# 周报索引", "",
              "> 由周报脚本维护；每条指向一篇 `metadata_only` 的周报笔记。", "",
              "| 周次 | 窗口 | 收录 | 笔记 |", "|---|---|---|---|"]
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as handle:
            content = handle.read()
        if row in content:
            return index_path
        lines = content.rstrip().splitlines()
        insert_at = len(lines)
        for i, line in enumerate(lines):
            if line.startswith("| 周次 |"):
                insert_at = i + 2
                break
        lines.insert(insert_at, row)
        new_content = "\n".join(lines) + "\n"
    else:
        new_content = "\n".join(header + [row]) + "\n"
    folder = os.path.dirname(index_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as handle:
        handle.write(new_content)
    return index_path


def send_mail(wr, subject, body, subtype):
    """Send via SMTP. Credentials come only from environment variables."""
    user = os.environ.get(wr.get("username_from_env", "MAIL_USERNAME"), "")
    password = os.environ.get(wr.get("password_from_env", "MAIL_PASSWORD"), "")
    to_secret = os.environ.get(wr.get("to_from_env", "MAIL_TO"), "")
    to_list = ([addr.strip() for addr in re.split(r"[,;]", to_secret) if addr.strip()]
               if to_secret else list(wr.get("to") or []))
    cc_list = list(wr.get("cc") or [])
    recipients = to_list + cc_list
    if not recipients:
        raise RuntimeError("没有收件人：请在 config.yaml 的 weekly_report.to 填写，或设置 MAIL_TO secret")
    if not user or not password:
        raise RuntimeError("缺少 MAIL_USERNAME / MAIL_PASSWORD（请在 Actions Secrets 中设置）")

    message = MIMEText(body, subtype, "utf-8")
    message["Subject"] = Header(subject, "utf-8").encode()
    message["From"] = formataddr((str(Header(wr.get("from_name", "paper-tracker"), "utf-8")), user))
    message["To"] = ", ".join(to_list)
    if cc_list:
        message["Cc"] = ", ".join(cc_list)
    message["Date"] = formatdate(localtime=True)

    host = wr["smtp_host"]
    port = int(wr["smtp_port"])
    if wr.get("smtp_ssl"):
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.ehlo()
        if wr.get("starttls"):
            server.starttls()
            server.ehlo()
    with server:
        server.login(user, password)
        server.sendmail(user, recipients, message.as_string())
    log.info("email sent to %d recipient(s) via %s:%s", len(recipients), host, port)


def main():
    parser = argparse.ArgumentParser(description=" weekly paper digest")
    parser.add_argument("--config_path", default="config.yaml")
    parser.add_argument("--arxiv_json", default=None,
                         help="override the arXiv json path (for testing)")
    parser.add_argument("--now", default=None,
                        help="ISO date used as 'today' (for testing the window)")
    parser.add_argument("--dry-run", action="store_true",
                         help="print the email body, do not send (default)")
    parser.add_argument("--send", action="store_true",
                         help="really send the email (needs Secrets)")
    parser.add_argument("--obsidian", action="store_true",
                         help="write an Obsidian markdown note instead of sending email")
    parser.add_argument("--vault-dir", default=None,
                         help="vault directory for --obsidian (e.g. .../城市经济学/周报)")
    parser.add_argument("--index", default=None,
                         help="index note path for --obsidian (default: <vault-dir>/周报索引.md)")
    args = parser.parse_args()
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    if args.dry_run and args.send:
        parser.error("--dry-run 与 --send 不能同时使用")
    if args.obsidian and not args.vault_dir:
        parser.error("--obsidian 需要 --vault-dir 指定 vault 目录")

    config = load_config(args.config_path)
    wr = config.get("weekly_report") or {}
    if not wr.get("enabled", True):
        log.info("weekly report disabled in config")
        return 0

    now = (dt.datetime.fromisoformat(args.now).replace(tzinfo=TZ)
           if args.now else dt.datetime.now(TZ))
    start, end = week_window(now, wr.get("window_days", 7))
    max_per = int(wr.get("max_papers_per_keyword", 20))

    if wr.get("topic_whitelist_from_keywords", True):
        order = list((config.get("keywords") or {}).keys())
    else:
        order = list((wr.get("journal_queries") or {}).keys())
    whitelist = set(order)
    sources = wr.get("sources") or {}
    enabled = [s for s in ("arxiv", "openalex", "elsevier") if sources.get(s)]

    records, notes = [], []
    if sources.get("arxiv"):
        found, note = collect_arxiv(
            args.arxiv_json or wr.get("arxiv_json_path", "./docs/cv-arxiv-daily.json"),
            whitelist, start, end, max_per)
        records.extend(found)
        if note:
            notes.append(note)
    found, skipped = collect_journals(wr, whitelist, start, end)
    records.extend(found)
    groups = merge_dedup(records, max_per)

    total = sum(len(v) for v in groups.values())
    if total == 0 and not wr.get("send_when_empty", True):
        log.info("empty week and send_when_empty is false, nothing to do")
        return 0

    subject, body, subtype = render(
        groups, order, start, end, now, wr,
        sorted(set(skipped) | set(notes)), enabled)

    if args.obsidian:
        iso_year, iso_week, _ = start.isocalendar()
        week_tag = "%d-W%02d" % (iso_year, iso_week)
        note_name = "论文周报 %s.md" % week_tag
        note_path = os.path.join(args.vault_dir, note_name)
        index_path = args.index or os.path.join(args.vault_dir, "周报索引.md")
        os.makedirs(args.vault_dir, exist_ok=True)
        md = render_obsidian(groups, order, start, end, now, wr,
                             sorted(set(skipped) | set(notes)), enabled)
        with open(note_path, "w", encoding="utf-8") as handle:
            handle.write(md)
        append_obsidian_index(index_path, week_tag, start, end, note_name, total)
        log.info("Obsidian note written: %s (%d paper(s))", note_path, total)
        print("Obsidian 笔记：%s（%d 篇）" % (note_path, total))
        print("索引：%s" % index_path)
        return 0

    if args.send:
        send_mail(wr, subject, body, subtype)
    else:
        print(body)
        log.info("dry-run: %d paper(s), %d source(s) skipped", total, len(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
