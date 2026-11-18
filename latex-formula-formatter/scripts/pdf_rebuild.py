# -*- coding: utf-8 -*-
"""PDF handler: replace literal LaTeX source text with typeset formulas in place.

Rebuilding a PDF from extracted text destroys the original layout, so this module
edits the original pages instead:

1. Extract every page with `rawdict` (per-character boxes) and concatenate each
   text block into one string, remembering which line/char each position came from.
2. Detect literal LaTeX ($..$, $$..$$, \\[..\\], \\(..\\), equation envs), including
   formulas that wrap across several lines.
3. Redact exactly those characters (per line rectangle) and drop a high-DPI
   rendered formula on top, baseline-aligned with the surrounding text.

Formulas that are already typeset (math fonts: CMMI / STIX / Cambria Math ...) are
already in the target form and are only counted, never touched.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from typing import Dict, List, Optional, Tuple

try:  # pymupdf >= 1.24 exposes the canonical name
    import pymupdf as fitz
except Exception:  # pragma: no cover
    import fitz  # type: ignore

import mathrender
from latex_core import find_formulas

MATH_FONT_RE = re.compile(
    r"(cmmi|cmsy|cmex|cmr\d|cmbx|cmsl|msam|msbm|eufm|eusm|rsfs|symbol|"
    r"stix|xits|mathjax|latinmodern|lmroman|lmmath|texgyre|asana|cambria\s*math|"
    r"dejavu\s*math|euclid|math)", re.I)

LATEX_HINT_RE = re.compile(r"\$|\\begin\{")
MARGIN_X = 40.0


class _Rec:
    __slots__ = ("ch", "bbox", "origin", "size", "font", "line", "blk")

    def __init__(self, ch, bbox, origin, size, font, line, blk=0):
        self.ch = ch
        self.bbox = bbox
        self.origin = origin
        self.size = size
        self.font = font
        self.line = line
        self.blk = blk


def _font_is_math(name: str) -> bool:
    return bool(MATH_FONT_RE.search(name or ""))


def _collect_block(block, blk: int = 0) -> Tuple[str, List[Optional[_Rec]]]:
    buf: List[str] = []
    recs: List[Optional[_Rec]] = []
    for li, line in enumerate(block.get("lines", [])):
        if li:
            buf.append("\n")
            recs.append(None)
        for span in line.get("spans", []):
            size = span.get("size", 10.0)
            font = span.get("font", "")
            for ch in span.get("chars", []):
                buf.append(ch.get("c", ""))
                recs.append(_Rec(ch.get("c", ""), fitz.Rect(ch["bbox"]),
                                 ch.get("origin"), size, font, li, blk))
    return "".join(buf), recs


def _collect_page(page) -> Tuple[str, List[Optional[_Rec]], int]:
    """Concatenate all text blocks of a page so display math can span blocks."""
    buf: List[str] = []
    recs: List[Optional[_Rec]] = []
    pre_rendered = 0
    for bi, block in enumerate(page.get_text("rawdict")["blocks"]):
        if block.get("type") != 0:
            continue
        text, r = _collect_block(block, bi)
        if not text.strip():
            continue
        if not LATEX_HINT_RE.search(text):
            chars = [x for x in r if x is not None and x.ch.strip()]
            if chars:
                math_n = sum(1 for x in chars if _font_is_math(x.font))
                if math_n >= max(3, len(chars) * 0.5):
                    pre_rendered += 1
        if buf:
            buf.append("\n")
            recs.append(None)
        buf.append(text)
        recs.extend(r)
    return "".join(buf), recs, pre_rendered


def _rect_of(recs: List[_Rec]) -> fitz.Rect:
    rect = fitz.Rect(recs[0].bbox)
    for r in recs[1:]:
        rect = rect | fitz.Rect(r.bbox)
    return rect


def _line_bounds(text: str, start: int, end: int) -> Tuple[int, int]:
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    if le == -1:
        le = len(text)
    return ls, le


def process_pdf(src: str, dst: str, dpi: int = 400, **_ignored) -> Dict:
    tmp = tempfile.mkdtemp(prefix="lff_")
    stats: Dict = {"display": 0, "inline": 0, "failed": 0, "failures": [],
                   "pre_rendered": 0, "pages": 0, "replaced": 0}
    counter = [0]

    def render(latex: str, fontsize: float) -> Optional[Tuple[bytes, float, float]]:
        counter[0] += 1
        path = os.path.join(tmp, "f%04d.png" % counter[0])
        try:
            w, h, _ = mathrender.render_png(latex, path, dpi=dpi, fontsize=fontsize)
        except Exception as exc:
            stats["failed"] += 1
            stats["failures"].append("%s -> %s" % (latex[:70], exc))
            return None
        with open(path, "rb") as fh:
            data = fh.read()
        return data, w * 72.0 / dpi, h * 72.0 / dpi

    try:
        doc = fitz.open(src)
        stats["pages"] = doc.page_count

        for page in doc:
            page_w = page.rect.width
            plan: List[Dict] = []
            text, recs, pre = _collect_page(page)
            stats["pre_rendered"] += pre

            for f in find_formulas(text):
                used = [r for r in recs[f.start:f.end] if r is not None]
                if not used:
                    continue
                by_key: Dict[Tuple[int, int], List[_Rec]] = {}
                for r in used:
                    by_key.setdefault((r.blk, r.line), []).append(r)
                order = sorted(by_key)
                first = by_key[order[0]]
                ls, le = _line_bounds(text, f.start, f.end)
                full_line = (not text[ls:f.start].strip()
                             and not text[f.end:le].strip())
                plan.append({
                    "latex": f.latex,
                    "display": f.display,
                    "rects": [_rect_of(by_key[k]) for k in order],
                    "baseline": (first[0].origin[1] if first[0].origin
                                 else _rect_of(first).y1),
                    "size": max(r.size for r in used) or 10.0,
                    "full_line": full_line,
                    "union": _rect_of(used),
                    "multiline": len(order) > 1,
                })

            if not plan:
                continue

            placements = []
            for item in plan:
                got = render(item["latex"], item["size"])
                if got is None:
                    continue
                data, w_pt, h_pt = got
                for r in item["rects"]:
                    try:
                        page.add_redact_annot(r + (-0.5, -1.2, 0.5, 1.2))
                    except Exception:
                        pass
                placements.append((item, data, w_pt, h_pt))
                stats["display" if item["display"] else "inline"] += 1

            if not placements:
                continue

            try:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            except TypeError:
                page.apply_redactions()
            except Exception:
                pass

            for item, data, w_pt, h_pt in placements:
                try:
                    if item["multiline"]:
                        box = item["union"]
                        scale = 1.0
                        if h_pt > box.height - 2 and h_pt:
                            scale = max(0.55, (box.height - 2) / h_pt)
                        if w_pt * scale > page_w - 2 * MARGIN_X and w_pt:
                            scale = min(scale, (page_w - 2 * MARGIN_X) / w_pt)
                        tw, th = w_pt * scale, h_pt * scale
                        x0 = (page_w - tw) / 2.0 if item["full_line"] else box.x0
                        y0 = box.y0 + (box.height - th) / 2.0
                        target = fitz.Rect(x0, y0, x0 + tw, y0 + th)
                    else:
                        bottom = item["baseline"] + item["size"] * 0.22
                        x0 = item["rects"][0].x0
                        if item["full_line"] and item["display"]:
                            x0 = max(MARGIN_X, (page_w - w_pt) / 2.0)
                        target = fitz.Rect(x0, bottom - h_pt, x0 + w_pt, bottom)
                    page.insert_image(target, stream=data, keep_proportion=True)
                    stats["replaced"] += 1
                except Exception as exc:
                    stats["failed"] += 1
                    stats["failures"].append("insert: %s" % exc)

        doc.save(dst, garbage=3, deflate=True)
        doc.close()
        return stats
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
