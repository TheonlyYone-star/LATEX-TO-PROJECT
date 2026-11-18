# -*- coding: utf-8 -*-
"""Render LaTeX to high-resolution transparent PNG with a professional math look.

Used for Markdown output, PDF literal-LaTeX, and as the DOCX fallback when the
MathML->OMML transform is unavailable.

Rendering strategy (best -> fallback):
1. matplotlib mathtext with the STIX font set (no system LaTeX required).
2. Sanitised mathtext (unsupported macros neutralised).
3. Column/row layout for multi-line environments (align / cases / matrices).
4. Mixed text+math rendering when the formula contains CJK text.
5. Pure-Unicode text fallback so an image is always produced.
"""
from __future__ import annotations

import base64
import io
import os
import re
from typing import List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["mathtext.fontset"] = "stix"
matplotlib.rcParams["font.family"] = "sans-serif"
matplotlib.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "SimSun", "Noto Sans CJK SC", "DejaVu Sans",
]

from matplotlib.figure import Figure  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from latex_core import clean_latex, split_env, split_rows, to_table, latex_to_unicode  # noqa: E402

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")

_DROP_CMDS = [
    r"\\displaystyle", r"\\textstyle", r"\\scriptstyle", r"\\scriptscriptstyle",
    r"\\limits", r"\\nolimits", r"\\nonumber", r"\\notag", r"\\hfill",
    r"\\mathstrut", r"\\phantom\{[^{}]*\}", r"\\hphantom\{[^{}]*\}",
    r"\\vphantom\{[^{}]*\}", r"\\label\{[^{}]*\}", r"\\tag\{[^{}]*\}",
    r"\\hspace\*?\{[^{}]*\}", r"\\vspace\*?\{[^{}]*\}", r"\\tag\*?\{[^{}]*\}",
    r"\\bigl?", r"\\bigr?", r"\\Bigl?", r"\\Bigr?", r"\\biggl?", r"\\biggr?",
    r"\\bigm", r"\\Bigm", r"\\allowbreak", r"\\relax", r"\\strut",
]


class RenderError(Exception):
    pass


# --------------------------------------------------------------- primitives

def _font_path(serif: bool = True) -> str:
    base = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    name = "DejaVuSerif.ttf" if serif else "DejaVuSans.ttf"
    path = os.path.join(base, name)
    if os.path.exists(path):
        return path
    for cand in ("DejaVuSans.ttf", "DejaVuSerif.ttf"):
        p = os.path.join(base, cand)
        if os.path.exists(p):
            return p
    raise RenderError("no bundled font found for delimiters")


def _trim(im: Image.Image) -> Image.Image:
    im = im.convert("RGBA")
    bbox = im.getchannel("A").getbbox()
    return im.crop(bbox) if bbox else im


def _err(exc: Exception) -> str:
    lines = [l.strip() for l in str(exc).splitlines() if l.strip()]
    return (lines[-1] if lines else type(exc).__name__)[:110]


def _fig_to_img(text: str, dpi: int, fontsize: float,
                family: Optional[str] = None) -> Image.Image:
    fig = Figure()
    fig.patch.set_alpha(0)
    kwargs = {"fontsize": fontsize, "color": "black"}
    if family:
        kwargs["family"] = family
    fig.text(0, 0, text, **kwargs)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                pad_inches=0.02, transparent=True)
    buf.seek(0)
    try:
        im = Image.open(buf)
        im.load()
    finally:
        pass
    return _trim(im)


def _scaled_glyph(ch: str, height: int, width: Optional[int] = None,
                  stroke_ratio: float = 1.0) -> Image.Image:
    """Draw a delimiter glyph stretched to `height` (used for big brackets/braces)."""
    font = ImageFont.truetype(_font_path(True), max(24, height // 2))
    probe = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    d = ImageDraw.Draw(probe)
    box = d.textbbox((0, 0), ch, font=font)
    gw, gh = max(1, box[2] - box[0]), max(1, box[3] - box[1])
    canvas = Image.new("RGBA", (gw + 8, gh + 8), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).text((4 - box[0], 4 - box[1]), ch, font=font, fill=(0, 0, 0, 255))
    canvas = _trim(canvas)
    new_h = max(1, int(height))
    new_w = max(1, int(width)) if width else max(2, int(canvas.width * new_h / max(1, canvas.height)))
    return canvas.resize((new_w, new_h), Image.LANCZOS)


# ----------------------------------------------------------------- sanitising

_DELIM_MAP = {
    # mathtext spellings / aliases
    r"\lVert": r"\Vert", r"\rVert": r"\Vert", r"\lvert": r"\vert",
    r"\rvert": r"\vert", r"\middle": "",
    r"\le": r"\leq", r"\ge": r"\geq", r"\ne": r"\neq",
    r"\leqslant": r"\leq", r"\geqslant": r"\geq",
    r"\dfrac": r"\frac", r"\tfrac": r"\frac", r"\cfrac": r"\frac",
    r"\bm": r"\boldsymbol", r"\gets": r"\leftarrow",
    r"\arccot": r"\mathrm{arccot}", r"\sgn": r"\mathrm{sgn}",
    r"\varnothing": r"\emptyset", r"\lnot": r"\neg",
    r"\land": r"\wedge", r"\lor": r"\vee",
}


def _sanitize(latex: str) -> str:
    s = latex
    for k, v in _DELIM_MAP.items():
        s = re.sub(re.escape(k) + r"(?![A-Za-z])", lambda m, v=v: v, s)
    s = re.sub(r"\\text\s*\{([^{}]*)\}", lambda m: "\\mathrm{" + m.group(1).replace(" ", "\\ ") + "}", s)
    s = re.sub(r"\\(mbox|textrm|textnormal|operatorname\*?)\s*\{([^{}]*)\}",
               lambda m: "\\mathrm{" + m.group(2).replace(" ", "\\ ") + "}", s)
    for pat in _DROP_CMDS:
        s = re.sub(pat, "", s)
    s = s.replace("\\middle", "")
    s = re.sub(r"\\begin\{array\}\{[^{}]*\}", "", s)
    s = s.replace("\\end{array}", "")
    s = s.replace("\r", " ").replace("\n", " ")
    return s.strip()


def _math_segments(latex: str) -> str:
    """Turn `a \\text{中文} b` into `$a$ 中文 $b$` so CJK renders in a text font."""
    parts: List[Tuple[str, str]] = []
    i = 0
    pattern = re.compile(r"\\(?:text|mbox|textrm|textnormal)\s*\{")
    while i < len(latex):
        m = pattern.search(latex, i)
        if not m:
            parts.append(("math", latex[i:]))
            break
        parts.append(("math", latex[i:m.start()]))
        depth, j = 0, m.end() - 1
        while j < len(latex):
            if latex[j] == "{":
                depth += 1
            elif latex[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        parts.append(("text", latex[m.end():j]))
        i = j + 1

    out: List[str] = []
    for kind, seg in parts:
        if kind == "text":
            out.append(seg.replace("\\_", "_").replace("\\%", "%").replace("\\&", "&"))
        else:
            seg = _sanitize(seg).strip()
            if seg:
                out.append("$" + seg + "$")
    return " ".join(x for x in out if x)


# ------------------------------------------------------------------- layout

def _render_row_cells(rows: Sequence[Sequence[str]], dpi: int, fontsize: float,
                      align: Sequence[str]) -> Image.Image:
    gap = max(3, int(fontsize * dpi / 72 * 0.28))
    imgs: List[List[Optional[Image.Image]]] = []
    for row in rows:
        line: List[Optional[Image.Image]] = []
        for cell in row:
            cell = clean_latex(cell)
            if not cell.strip():
                line.append(None)
                continue
            try:
                line.append(_fig_to_img("$" + _sanitize(cell) + "$", dpi, fontsize))
            except Exception:
                line.append(_fig_to_img("$" + latex_to_unicode(cell).replace("$", "") + "$",
                                        dpi, fontsize) if latex_to_unicode(cell) else None)
        imgs.append(line)

    ncols = max((len(r) for r in imgs), default=1)
    col_w = [0] * ncols
    for line in imgs:
        for idx, im in enumerate(line):
            if im is not None:
                col_w[idx] = max(col_w[idx], im.width)
    row_h = [max((im.height for im in line if im is not None), default=0) or 1 for line in imgs]

    total_w = sum(col_w) + gap * (ncols - 1)
    total_h = sum(row_h) + gap * (len(imgs) - 1)
    canvas = Image.new("RGBA", (max(1, total_w), max(1, total_h)), (0, 0, 0, 0))
    y = 0
    for r, line in enumerate(imgs):
        x = 0
        for c in range(ncols):
            im = line[c] if c < len(line) else None
            if im is not None:
                if align[c] == "right":
                    px = x + col_w[c] - im.width
                elif align[c] == "center":
                    px = x + (col_w[c] - im.width) // 2
                else:
                    px = x
                py = y + (row_h[r] - im.height) // 2
                canvas.alpha_composite(im, (max(0, px), max(0, py)))
            x += col_w[c] + gap
        y += row_h[r] + gap
    return _trim(canvas)


def _with_delimiters(body: Image.Image, open_ch: str, close_ch: str) -> Image.Image:
    pad = 2
    lo = _scaled_glyph(open_ch, body.height)
    ro = _scaled_glyph(close_ch, body.height)
    total_w = lo.width + body.width + ro.width + pad * 4
    canvas = Image.new("RGBA", (total_w, body.height + 4), (0, 0, 0, 0))
    canvas.alpha_composite(lo, (pad, 2))
    canvas.alpha_composite(body, (pad * 2 + lo.width, 2))
    canvas.alpha_composite(ro, (pad * 3 + lo.width + body.width, 2))
    return canvas


def _with_brace(body: Image.Image) -> Image.Image:
    brace = _scaled_glyph("{", body.height)
    pad = 3
    canvas = Image.new("RGBA", (brace.width + body.width + pad * 3, body.height + 4), (0, 0, 0, 0))
    canvas.alpha_composite(brace, (pad, 2))
    canvas.alpha_composite(body, (pad * 2 + brace.width, 2))
    return canvas


_MATRIX_DELIMS = {
    "pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "vmatrix": ("|", "|"),
    "Vmatrix": ("‖", "‖"), "matrix": ("", ""), "smallmatrix": ("", ""),
    "array": ("", ""),
}


def _hcompose(images: Sequence[Optional[Image.Image]], gap: int = 4) -> Image.Image:
    imgs = [im for im in images if im is not None]
    if not imgs:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    total_w = sum(im.width for im in imgs) + gap * (len(imgs) - 1)
    total_h = max(im.height for im in imgs)
    canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    x = 0
    for im in imgs:
        canvas.alpha_composite(im, (x, (total_h - im.height) // 2))
        x += im.width + gap
    return _trim(canvas)


_STRUCT_RE = re.compile(r"\\begin\{([A-Za-z*]+)\}")


def _render_structured(latex: str, dpi: int, fontsize: float) -> Optional[Image.Image]:
    """Render align / cases / matrix environments with real row+column layout."""
    m = _STRUCT_RE.search(latex)
    if not m:
        return None
    env = m.group(1)
    base = env.rstrip("*")
    end_re = re.compile(r"\\end\{" + re.escape(env) + r"\}")
    ends = list(end_re.finditer(latex))
    if not ends:
        return None
    end_m = ends[-1]

    prefix = latex[:m.start()].strip()
    body = latex[m.end():end_m.start()]
    suffix = latex[end_m.end():].strip()

    known = ("align", "aligned", "alignat", "gather", "gathered", "multline",
             "split", "eqnarray", "cases", "array", "matrix", "pmatrix",
             "bmatrix", "vmatrix", "Vmatrix", "smallmatrix")
    if base not in known:
        return None

    rows = split_rows(body)
    ncols = max(len(r) for r in rows)
    if len(rows) == 1 and ncols == 1 and not prefix:
        return None

    if base in ("align", "aligned", "alignat", "split", "eqnarray"):
        align = ["right"] + ["left"] * (ncols - 1)
    elif base == "cases":
        align = ["left"] * ncols
    else:
        align = ["center"] * ncols

    grid = [list(r) + [""] * (ncols - len(r)) for r in rows]

    grid_img = _render_row_cells(grid, dpi, fontsize, align)

    if base == "cases":
        grid_img = _with_brace(grid_img)
    else:
        delims = _MATRIX_DELIMS.get(base)
        if delims and delims[0]:
            grid_img = _with_delimiters(grid_img, *delims)

    parts: List[Optional[Image.Image]] = []
    if prefix:
        try:
            parts.append(_fig_to_img("$" + _sanitize(prefix) + "$", dpi, fontsize))
        except Exception:
            parts.append(None)
    parts.append(grid_img)
    if suffix:
        try:
            parts.append(_fig_to_img("$" + _sanitize(suffix) + "$", dpi, fontsize))
        except Exception:
            parts.append(None)
    return _hcompose(parts, gap=max(2, int(fontsize * dpi / 72 * 0.15)))


# ---------------------------------------------------------------- public API

def render_image(latex: str, dpi: int = 300, fontsize: float = 12.0) -> Tuple[Image.Image, List[str]]:
    """Render one formula to a PIL image. Always succeeds or raises RenderError."""
    warns: List[str] = []

    structured = None
    try:
        structured = _render_structured(latex, dpi, fontsize)
    except Exception:
        structured = None
    if structured is not None:
        return structured, warns

    body = clean_latex(latex)

    has_cjk = bool(CJK_RE.search(body))
    if has_cjk:
        try:
            return _fig_to_img(_math_segments(body), dpi, fontsize), warns
        except Exception as exc:
            warns.append("mixed CJK render failed: %s" % _err(exc))
            body_nc = CJK_RE.sub("", body)
            if not body_nc.strip():
                raise RenderError("formula is pure CJK text")
            body = body_nc

    try:
        return _fig_to_img("$" + _sanitize(body) + "$", dpi, fontsize), warns
    except Exception as exc:
        warns.append("mathtext failed: %s" % _err(exc))

    plain = latex_to_unicode(body).replace("$", "\\$")
    family = "Microsoft YaHei" if has_cjk else "DejaVu Serif"
    try:
        return _fig_to_img(plain, dpi, fontsize, family=family), warns
    except Exception as exc:
        raise RenderError("unrenderable formula: %s" % _err(exc))


def render_png(latex: str, out_path: str, dpi: int = 300,
               fontsize: float = 12.0) -> Tuple[int, int, List[str]]:
    """Render formula to a transparent PNG. Returns (width_px, height_px, warnings)."""
    img, warns = render_image(latex, dpi=dpi, fontsize=fontsize)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    return img.width, img.height, warns


def render_data_uri(latex: str, dpi: int = 220, fontsize: float = 12.0) -> Tuple[str, int, int, List[str]]:
    """Render formula and return (data_uri, width_px, height_px, warnings)."""
    img, warns = render_image(latex, dpi=dpi, fontsize=fontsize)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return uri, img.width, img.height, warns


def points_size(px: int, dpi: int) -> float:
    """Convert pixel size to typographic points at the render DPI."""
    return px * 72.0 / float(dpi)
