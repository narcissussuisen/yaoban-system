"""Markdown -> HTML 转换（自包含，复用 v4.0 转换器；支持标题/表格/代码块/引用/列表/分隔线）"""
from __future__ import annotations

import html
import re
from pathlib import Path


def md2html(md_text: str, title: str = "妖板系统报告") -> str:
    lines = md_text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)

    def inline(t: str) -> str:
        t = html.escape(t, quote=False)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
        return t

    def split_row(line: str) -> list[str]:
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    def is_sep(line: str) -> bool:
        s = line.strip()
        if not s.startswith("|"):
            return False
        return all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in s.strip("|").split("|"))

    while i < n:
        line = lines[i]
        if line.strip().startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(html.escape(lines[i], quote=False))
                i += 1
            i += 1
            out.append('<pre class="code"><code>' + "\n".join(buf) + "</code></pre>")
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            out.append(f'<h{len(m.group(1))}>{inline(m.group(2))}</h{len(m.group(1))}>')
            i += 1
            continue
        if re.match(r"^\s*---+\s*$", line):
            out.append("<hr>")
            i += 1
            continue
        if line.startswith(">"):
            buf = []
            while i < n and lines[i].startswith(">"):
                buf.append(inline(lines[i].lstrip(">").strip()))
                i += 1
            out.append('<blockquote>' + " ".join(buf) + "</blockquote>")
            continue
        if line.strip().startswith("|") and i + 1 < n and is_sep(lines[i + 1]):
            head = split_row(line)
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            th = "".join(f"<th>{inline(c)}</th>" for c in head)
            body = ""
            for r in rows:
                body += "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
            out.append(f'<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>')
            continue
        if re.match(r"^\s*-\s+", line):
            buf = []
            while i < n and re.match(r"^\s*-\s+", lines[i]):
                buf.append("<li>" + inline(re.sub(r"^\s*-\s+", "", lines[i])) + "</li>")
                i += 1
            out.append("<ul>" + "".join(buf) + "</ul>")
            continue
        if line.strip() == "":
            i += 1
            continue
        buf = [inline(line.strip())]
        i += 1
        while i < n and lines[i].strip() != "" and not lines[i].strip().startswith(("```", "#", ">", "|", "-")):
            buf.append(inline(lines[i].strip()))
            i += 1
        out.append("<p>" + " ".join(buf) + "</p>")

    css = """
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  max-width: 980px; margin: 0 auto; padding: 32px; line-height: 1.7;
  color: #1f2329; background: #fff; }
h1 { font-size: 26px; border-bottom: 3px solid #2f6fed; padding-bottom: 8px; }
h2 { font-size: 20px; margin-top: 30px; border-left: 5px solid #2f6fed; padding-left: 12px; }
h3 { font-size: 17px; margin-top: 22px; color: #2f6fed; }
table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 13px; }
th, td { border: 1px solid #d0d7de; padding: 7px 9px; text-align: left; vertical-align: top; }
th { background: #eef3ff; font-weight: 600; }
tbody tr:nth-child(even) { background: #f7f9fc; }
pre.code { background: #f4f5f7; border: 1px solid #e1e4e8; border-radius: 8px;
  padding: 12px 14px; overflow-x: auto; font-size: 12.5px; }
code { font-family: Consolas, monospace; background: #f0f2f5; padding: 1px 4px; border-radius: 4px; }
pre.code code { background: none; padding: 0; }
blockquote { border-left: 4px solid #f0a020; background: #fff8ec; margin: 12px 0;
  padding: 8px 14px; color: #6b4f1a; }
hr { border: none; border-top: 2px dashed #c8d2e0; margin: 22px 0; }
ul { padding-left: 22px; }
li { margin: 3px 0; }
p { margin: 8px 0; }
strong { color: #c0392b; }
"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{css}</style>
</head>
<body>
{"".join(out)}
</body>
</html>"""


def md_file2html(md_path: str | Path, html_path: str | Path, title: str | None = None) -> str:
    p = Path(md_path)
    text = p.read_text(encoding="utf-8")
    t = title or p.stem
    doc = md2html(text, t)
    Path(html_path).write_text(doc, encoding="utf-8")
    return str(html_path)
