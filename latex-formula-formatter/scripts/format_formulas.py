#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""latex-formula-formatter CLI.

Detect LaTeX formulas inside a paper and rewrite them in a professionally
typeset form, keeping the original file extension.

Usage:
    python format_formulas.py PAPER.pdf
    python format_formulas.py PAPER.docx -o out/PAPER.docx
    python format_formulas.py PAPER.md --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import Dict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SUPPORTED = {".pdf": "pdf", ".docx": "docx", ".md": "markdown", ".markdown": "markdown",
             ".tex": "tex", ".latex": "tex", ".txt": "text", ".text": "text"}


def default_output(src: str) -> str:
    root, ext = os.path.splitext(src)
    return root + "-formatted" + ext


def run(src: str, dst: str, dpi: int = 0, fontsize: float = 0.0) -> Dict:
    ext = os.path.splitext(src)[1].lower()
    kind = SUPPORTED.get(ext)
    if not kind:
        raise SystemExit("unsupported input type: %s (supported: %s)"
                         % (ext, ", ".join(sorted(SUPPORTED))))

    if kind == "pdf":
        import pdf_rebuild
        stats = pdf_rebuild.process_pdf(src, dst, dpi=dpi or 400)
    elif kind == "docx":
        import docx_omml
        stats = docx_omml.process_docx(src, dst)
    elif kind == "markdown":
        import text_formats
        stats = text_formats.process_markdown(src, dst, dpi=dpi or 200,
                                              fontsize=fontsize or 12.5)
    elif kind == "tex":
        import text_formats
        stats = text_formats.process_tex(src, dst)
    else:
        import text_formats
        stats = text_formats.process_text(src, dst)

    stats = dict(stats)
    stats["kind"] = kind
    stats["input"] = os.path.abspath(src)
    stats["output"] = os.path.abspath(dst)
    stats["size_in"] = os.path.getsize(src)
    stats["size_out"] = os.path.getsize(dst) if os.path.exists(dst) else 0
    return stats


def _human(stats: Dict) -> str:
    ok = stats.get("display", 0) + stats.get("inline", 0)
    lines = [
        "输入 : %s" % stats["input"],
        "输出 : %s" % stats["output"],
        "格式 : %s" % stats["kind"],
        "公式 : 共 %d 处（块级 %d / 行内 %d）"
        % (ok, stats.get("display", 0), stats.get("inline", 0)),
    ]
    if stats.get("clipped"):
        lines.append("       其中 %d 处直接抽取原排版区域（保真）" % stats["clipped"])
    if stats.get("pre_rendered"):
        lines.append("       已是排版态、无需转换的区域 %d 处" % stats["pre_rendered"])
    if stats.get("kind") == "pdf" and not stats.get("replaced"):
        lines.append("       该 PDF 中没有 LaTeX 源码公式，输出为原件副本（版式不变）")
    if stats.get("merged_blocks"):
        lines.append("       合并跨段落公式块 %d 处" % stats["merged_blocks"])
    if stats.get("bare_blocks"):
        lines.append("       识别无定界符的裸 LaTeX 公式块 %d 处" % stats["bare_blocks"])
    if stats.get("skipped_code"):
        lines.append("       跳过代码块中的公式 %d 处" % stats["skipped_code"])
    if "omml_available" in stats:
        lines.append("原生公式引擎 : %s" % ("可用" if stats["omml_available"] else "不可用"))
    if stats.get("packages_added"):
        lines.append("补齐宏包 : %s" % ", ".join(stats["packages_added"]))
    if stats.get("failed"):
        lines.append("失败 : %d 处（已保留原文）" % stats["failed"])
        for item in stats.get("failures", [])[:5]:
            lines.append("       - %s" % item[:90])
    for warn in stats.get("warnings", [])[:3]:
        lines.append("提示 : %s" % str(warn)[:160])
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="format_formulas",
        description="将论文中的 LaTeX 公式转换为专业排版格式（保持原文件后缀）")
    ap.add_argument("input", help="论文文件：pdf / docx / md / tex / txt")
    ap.add_argument("-o", "--output", default=None, help="输出路径（默认 <name>-formatted.<ext>）")
    ap.add_argument("--dpi", type=int, default=0, help="位图渲染精度（pdf 默认 400，md 默认 200）")
    ap.add_argument("--fontsize", type=float, default=0.0, help="公式字号（md 默认 12.5）")
    ap.add_argument("--json", action="store_true", help="输出机器可读的 JSON 摘要")
    args = ap.parse_args(argv)

    src = args.input
    if not os.path.exists(src):
        print("找不到文件：%s" % src, file=sys.stderr)
        return 2

    dst = args.output or default_output(src)
    out_dir = os.path.dirname(os.path.abspath(dst))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    try:
        stats = run(src, dst, dpi=args.dpi, fontsize=args.fontsize)
    except SystemExit:
        raise
    except Exception as exc:
        print("处理失败：%s" % exc, file=sys.stderr)
        traceback.print_exc()
        return 1

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    else:
        print(_human(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
