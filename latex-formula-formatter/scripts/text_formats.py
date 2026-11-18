# -*- coding: utf-8 -*-
"""Text-based formats: Markdown, LaTeX source, plain text.

Each format gets its most professional *native* representation:
  .md  -> rendered formula images (inline base64, single self-contained file)
  .tex -> publication-grade equation environments + guaranteed amsmath preamble
  .txt -> readable Unicode math
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple

import mathrender
from latex_core import (find_formulas, latex_to_unicode, mask_code, unmask_code,
                        clean_latex, split_env)

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        data = fh.read()
    if data.startswith("\ufeff"):
        data = data[1:]
    return data.replace("\r\n", "\n")


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _attr(s: str) -> str:
    return (s.replace("&", "&amp;").replace('"', "&quot;")
             .replace("<", "&lt;").replace(">", "&gt;").replace("\n", " "))


# ----------------------------------------------------------------- markdown

def process_markdown(src: str, dst: str, dpi: int = 200,
                     fontsize: float = 12.5) -> Dict:
    text = _read(src)
    masked, holders = mask_code(text, "markdown")
    forms = find_formulas(masked)

    stats: Dict = {"display": 0, "inline": 0, "failed": 0, "failures": [],
                   "skipped_code": len(holders)}
    out = text
    for f in reversed(forms):
        line_start = out.rfind("\n", 0, f.start) + 1
        line_end = out.find("\n", f.end)
        if line_end == -1:
            line_end = len(out)
        alone = not out[line_start:f.start].strip() and not out[f.end:line_end].strip()

        try:
            uri, w, h, warns = mathrender.render_data_uri(f.latex, dpi=dpi, fontsize=fontsize)
            cw = max(1, int(round(w * 96.0 / dpi)))
            ch = max(1, int(round(h * 96.0 / dpi)))
            img = ('<img src="%s" width="%d" height="%d" alt="%s" />'
                   % (uri, cw, ch, _attr(clean_latex(f.latex))))
            if f.display:
                replacement = ('<p align="center">%s</p>' % img) if alone else img
            else:
                replacement = img
            stats["display" if f.display else "inline"] += 1
        except Exception as exc:
            stats["failed"] += 1
            stats["failures"].append(f.latex[:100])
            replacement = out[f.start:f.end]
        out = out[:f.start] + replacement + out[f.end:]

    _write(dst, out)
    return stats


# ---------------------------------------------------------------------- tex

_KNOWN_ENVS = ("equation", "align", "gather", "multline", "eqnarray")


def process_tex(src: str, dst: str) -> Dict:
    text = _read(src)
    stats: Dict = {"display": 0, "inline": 0, "environments": 0, "packages_added": []}
    out = text

    def _env_block(m: re.Match) -> str:
        stats["display"] += 1
        return "\\begin{equation}\n" + m.group(1).strip() + "\n\\end{equation}"

    out = re.sub(r"(?<!\\)\$\$(.+?)(?<!\\)\$\$", _env_block, out, flags=re.S)

    def _bracket_block(m: re.Match) -> str:
        stats["display"] += 1
        return "\\begin{equation*}\n" + m.group(1).strip() + "\n\\end{equation*}"

    out = re.sub(r"\\\[(.+?)\\\]", _bracket_block, out, flags=re.S)

    def _inline(m: re.Match) -> str:
        stats["inline"] += 1
        return "$" + m.group(1).strip() + "$"

    out = re.sub(r"\\\((.+?)\\\)", _inline, out, flags=re.S)

    for env in _KNOWN_ENVS:
        stats["environments"] += len(re.findall(r"\\begin\{" + env + r"\*?\}", out))

    added: List[str] = []
    if "\\begin{document}" in out:
        needed = []
        for pkg in ("amsmath", "amssymb"):
            if not re.search(r"\\usepackage(\[[^\]]*\])?\{" + pkg + r"\}", out):
                needed.append(pkg)
        if needed:
            line = "\\usepackage{" + ",".join(needed) + "}\n"
            m = re.search(r"^\s*\\documentclass(\[[^\]]*\])?\{[^}]*\}[^\n]*\n", out, re.M)
            if m:
                out = out[:m.end()] + line + out[m.end():]
                added.extend(needed)
            else:
                out = line + out
                added.extend(needed)
        if "\\begin{document}" in out and "\\usepackage{amsmath" not in out \
                and "amsmath" not in ",".join(added):
            pass

    stats["packages_added"] = added
    _write(dst, out)
    return stats


# --------------------------------------------------------------------- text

def process_text(src: str, dst: str) -> Dict:
    text = _read(src)
    forms = find_formulas(text)
    stats: Dict = {"display": 0, "inline": 0, "failed": 0, "failures": []}
    out = text

    for f in reversed(forms):
        env, body = split_env(f.latex)
        if env and env.rstrip("*") in ("align", "aligned", "gather", "split",
                                       "eqnarray", "cases", "matrix", "pmatrix",
                                       "bmatrix"):
            from latex_core import split_rows
            rows = split_rows(body)
            converted = "\n".join(
                "  " + "   ".join(latex_to_unicode(c) for c in row if c.strip())
                for row in rows)
        else:
            converted = latex_to_unicode(f.latex)

        if f.display:
            before = out[:f.start]
            after = out[f.end:]
            lead = "" if (not before.strip() or before.endswith("\n")) else "\n\n"
            trail = "" if after.startswith("\n") else "\n\n"
            replacement = lead + converted + trail
            stats["display"] += 1
        else:
            replacement = converted
            stats["inline"] += 1
        out = out[:f.start] + replacement + out[f.end:]

    _write(dst, out)
    return stats
