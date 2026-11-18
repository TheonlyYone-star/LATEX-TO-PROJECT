# -*- coding: utf-8 -*-
"""Convert LaTeX formulas inside a .docx into native Word equations (OMML).

Pipeline: LaTeX -> MathML (latex2mathml) -> OMML (MSO MML2OMML.XSL) -> `m:oMath`
inserted into the run stream, preserving surrounding character formatting.
"""
from __future__ import annotations

import copy
import glob
import os
import re
from typing import Dict, Iterable, List, Optional, Tuple

from lxml import etree

from docx import Document
from docx.oxml.ns import qn

from latex_core import Formula, clean_latex, find_formulas

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

_XSL_PATTERNS = [
    r"C:\Program Files\Microsoft Office\root\Office*\MML2OMML.XSL",
    r"C:\Program Files (x86)\Microsoft Office\root\Office*\MML2OMML.XSL",
    r"C:\Program Files\Microsoft Office\Office*\MML2OMML.XSL",
    r"C:\Program Files (x86)\Microsoft Office\Office*\MML2OMML.XSL",
]


def find_xsl() -> Optional[str]:
    env = os.environ.get("MML2OMML_XSL")
    if env and os.path.exists(env):
        return env
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(here, "assets", "MML2OMML.XSL")
    if os.path.exists(bundled):
        return bundled
    for pat in _XSL_PATTERNS:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


_FUNC_RE = re.compile(
    r"\\(log|ln|lg|exp|sin|cos|tan|cot|sec|csc|arcsin|arccos|arctan|"
    r"sinh|cosh|tanh|coth|limsup|liminf|lim|max|min|sup|inf|det|dim|ker|"
    r"hom|arg|deg|gcd|Pr)(?![A-Za-z])")


_ENV_ALIAS = {
    "aligned": "align", "alignedat": "align", "alignat": "align",
    "split": "align", "gathered": "gather", "multlined": "multline",
}
_ENTITY_RE = re.compile(r"&(?!#\d+;|#x[0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9]*;)")


def _normalize_envs(latex: str) -> str:
    """latex2mathml only builds a table for `align`; its `aligned` cousin leaks a
    raw `&`, which makes the produced MathML invalid XML."""
    out = latex
    for src, dst in _ENV_ALIAS.items():
        out = re.sub(r"\\begin\{" + src + r"\}", r"\\begin{" + dst + "}", out)
        out = re.sub(r"\\end\{" + src + r"\}", r"\\end{" + dst + "}", out)
    return out


MML_NS = "http://www.w3.org/1998/Math/MathML"

# single-char <mo> that must become an accent (not a limit) in OMML
_ACCENT_CHARS = set("\u02d9\u00a8\u0307\u0308\u005e\u02c6\u0302\u02dc\u0303"
                    "\u20d7\u20d6\u2192\u2190\u0304\u0305")
_SPACE_EM = "\u2003"
_THIN_SPACE = "\u2009"


def _mq(tag: str) -> str:
    return "{%s}%s" % (MML_NS, tag)


def _prepare_mathml(root) -> None:
    """Fix two MSO XSLT quirks before the MathML->OMML transform.

    1. `<mover>` without accent="true" becomes `m:limUpp` (a limit), so `\\dot{x}`
       renders as x with a floating dot above. Mark real accents explicitly.
    2. `<mspace>` is dropped entirely, losing `\\quad` / `\\qquad` spacing.
    """
    for mover in list(root.iter(_mq("mover"))):
        kids = [k for k in mover if isinstance(k.tag, str)]
        if len(kids) >= 2 and kids[1].tag == _mq("mo"):
            ch = (kids[1].text or "").strip()
            if ch in _ACCENT_CHARS:
                mover.set("accent", "true")

    for mspace in list(root.iter(_mq("mspace"))):
        parent = mspace.getparent()
        if parent is None:
            continue
        w = mspace.get("width", "") or ""
        try:
            em = float(w[:-2]) if w.endswith("em") else 0.0
        except ValueError:
            em = 0.0
        node = etree.Element(_mq("mo"))
        if em >= 0.9:
            node.text = _SPACE_EM * int(round(em))
        else:
            node.text = _THIN_SPACE * max(1, int(round(em / 0.167)))
        parent.replace(mspace, node)


def _m(tag: str) -> str:
    return "{%s}%s" % (M_NS, tag)


_DELIM_PAIRS = {"(": ")", "[": "]", "{": "}", "|": "|", "‖": "‖", "⟨": "⟩"}


def _omml_run_text(el) -> str:
    return "".join(t.text or "" for t in el.iter(_m("t")))


def _wrap_matrix_delims(root) -> None:
    """MSO's XSLT emits matrix fences as ordinary runs, so `[A]` stays text-height
    and often gets glued onto the neighbouring run. Rebuild them as `m:d` (a real
    delimiter object) so Word stretches the brackets around the matrix.
    """
    for mat in list(root.iter(_m("m"))):
        parent = mat.getparent()
        if parent is None:
            continue
        kids = [k for k in parent if isinstance(k.tag, str)]
        try:
            idx = kids.index(mat)
        except ValueError:
            continue
        if idx == 0 or idx + 1 >= len(kids):
            continue
        prev, nxt = kids[idx - 1], kids[idx + 1]
        if prev.tag != _m("r") or nxt.tag != _m("r"):
            continue

        ptext = _omml_run_text(prev)
        ntext = _omml_run_text(nxt)
        stripped = ptext.strip()
        if stripped in _DELIM_PAIRS:
            beg = stripped
            keep_prev = ""
        elif stripped and stripped[-1] in _DELIM_PAIRS:
            beg = stripped[-1]
            keep_prev = ptext[:ptext.rindex(beg)]
        else:
            continue
        end = _DELIM_PAIRS[beg]

        nstripped = ntext.strip()
        if nstripped.startswith(end):
            keep_next = ntext[ntext.index(end) + 1:]
        else:
            continue

        delim = etree.Element(_m("d"))
        pr = etree.SubElement(delim, _m("dPr"))
        bc = etree.SubElement(pr, _m("begChr"))
        bc.set(_m("val"), beg)
        ec = etree.SubElement(pr, _m("endChr"))
        ec.set(_m("val"), end)
        e = etree.SubElement(delim, _m("e"))

        parent.replace(prev, delim)
        parent.remove(mat)
        parent.remove(nxt)
        e.append(mat)

        if keep_prev.strip():
            run = etree.Element(_m("r"))
            t = etree.SubElement(run, _m("t"))
            t.text = keep_prev
            delim.addprevious(run)
        if keep_next.strip():
            run = etree.Element(_m("r"))
            t = etree.SubElement(run, _m("t"))
            t.text = keep_next
            delim.addnext(run)


def _upright_functions(latex: str) -> str:
    """Word renders bare math runs in italic; function names must stay upright."""
    return _FUNC_RE.sub(lambda m: "\\mathrm{" + m.group(1) + "}", latex)


class OmmlConverter:
    """LaTeX -> OMML element factory."""

    def __init__(self) -> None:
        self.xsl_path = find_xsl()
        self._tr = None
        self._cache: Dict[str, Optional[etree._Element]] = {}
        if self.xsl_path:
            try:
                self._tr = etree.XSLT(etree.parse(self.xsl_path))
            except Exception:
                self._tr = None

    @property
    def available(self) -> bool:
        return self._tr is not None

    def to_omml(self, latex: str) -> Optional[etree._Element]:
        if latex in self._cache:
            cached = self._cache[latex]
            return copy.deepcopy(cached) if cached is not None else None
        el = self._build(latex)
        self._cache[latex] = etree.fromstring(etree.tostring(el)) if el is not None else None
        return el

    def _build(self, latex: str) -> Optional[etree._Element]:
        if self._tr is None:
            return None
        try:
            import latex2mathml.converter as conv
        except Exception:
            return None
        try:
            prepared = _normalize_envs(_upright_functions(clean_latex(latex)))
            mathml = conv.convert(prepared)
            # last-resort guard: a stray bare `&` would make the document invalid
            mathml = _ENTITY_RE.sub("&amp;", mathml)
            root = etree.fromstring(mathml.encode("utf-8"))
            _prepare_mathml(root)
            result = self._tr(root)
            out = result.getroot()
            if out is None:
                return None
            _wrap_matrix_delims(out)
            return etree.fromstring(etree.tostring(out))
        except Exception:
            return None


# ------------------------------------------------------------------ xml utils

def _run_text(run_el) -> str:
    return "".join((t.text or "") for t in run_el.findall(qn("w:t")))


def _set_run_text(run_el, text: str) -> None:
    ts = run_el.findall(qn("w:t"))
    if not ts:
        t = etree.SubElement(run_el, qn("w:t"))
        ts = [t]
    ts[0].text = text
    if text != text.strip():
        ts[0].set(XML_SPACE, "preserve")
    for extra in ts[1:]:
        extra.text = ""


def _runs_of(p_el) -> List[Tuple[etree._Element, str]]:
    return [(r, _run_text(r)) for r in p_el.iter(qn("w:r"))]


def _drop_if_empty(run_el) -> None:
    """Remove a run that holds nothing but empty text (keeps breaks/drawings)."""
    if _run_text(run_el).strip():
        return
    for tag in ("w:br", "w:tab", "w:drawing", "w:pict", "w:object",
                "w:fldChar", "w:instrText", "w:sym", "w:noBreakHyphen"):
        if run_el.find(qn(tag)) is not None:
            return
    parent = run_el.getparent()
    if parent is not None:
        parent.remove(run_el)


def _make_omath_para(omath: etree._Element) -> etree._Element:
    para = etree.Element("{%s}oMathPara" % M_NS, nsmap={"m": M_NS})
    pr = etree.SubElement(para, "{%s}oMathParaPr" % M_NS)
    jc = etree.SubElement(pr, "{%s}jc" % M_NS)
    jc.set("{%s}val" % M_NS, "center")
    para.append(omath)
    return para


def _center_paragraph(p_el) -> None:
    ppr = p_el.find(qn("w:pPr"))
    if ppr is None:
        ppr = etree.Element(qn("w:pPr"))
        p_el.insert(0, ppr)
    jc = ppr.find(qn("w:jc"))
    if jc is None:
        jc = etree.SubElement(ppr, qn("w:jc"))
    jc.set(qn("w:val"), "center")


# ------------------------------------------------- bare (delimiter-less) latex

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
_MACRO_RE = re.compile(r"\\[A-Za-z]{2,}")
_ENV_OPEN_RE = re.compile(r"\\begin\{([A-Za-z*]+)\}")
_ENV_CLOSE_RE = re.compile(r"\\end\{([A-Za-z*]+)\}")
_CONT_END_RE = re.compile(
    r"(=|\+|-|\*|/|\\cdot|\\times|\\qquad|\\quad|,|;|\(|\[|\{|\||&|\\left\(|\\right\.)\s*$")
_CONT_START_RE = re.compile(
    r"^\s*(\\quad|\\qquad|\\text\{|\\mathrm\{|\\exp|\\log|\\ln|\\iint|\\iiint|"
    r"\\int|\\sum|\\prod|\\left|\\right|\\end\{|\\\\|\\cdot|\\times|[,.\+\-=])")


def is_bare_latex_paragraph(text: str) -> bool:
    """Detect a paragraph that holds raw LaTeX source without $ delimiters.

    Common in documents produced by naive Markdown->DOCX converters, which strip
    the delimiters and emit one paragraph per source line.
    """
    s = text.strip()
    if len(s) < 4 or "$" in s or _CJK_RE.search(s) or not _MACRO_RE.search(s):
        return False
    # a self-contained environment is already a complete formula; leave it to the
    # normal `\\begin{...}...\\end{...}` detection
    if re.fullmatch(r"\\begin\{[A-Za-z*]+\}.*\\end\{[A-Za-z*]+\}", s, re.S):
        return False
    # reject prose: any leftover english words are a strong signal
    if len(re.findall(r"[A-Za-z]{3,}", _MACRO_RE.sub("", s))) > 6:
        return False
    hits = sum([s.count("\\") >= 2, "{" in s, ("^" in s or "_" in s),
                "&" in s, s.startswith("\\begin")])
    return hits >= 2


def _open_envs(s: str) -> int:
    depth = 0
    for m in _ENV_OPEN_RE.finditer(s):
        depth += 1
    depth -= len(_ENV_CLOSE_RE.findall(s))
    return max(0, depth)


def _unbalanced(s: str) -> bool:
    return s.count("{") != s.count("}") or s.count("(") != s.count(")")


def _wrap_bare_latex_blocks(container) -> int:
    """Join runs of delimiter-less LaTeX paragraphs into one $$...$$ paragraph."""
    paras = container.findall(qn("w:p"))
    if not paras:
        return 0
    texts = ["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in paras]
    cand = [is_bare_latex_paragraph(t) for t in texts]

    # a lead-in fragment such as "\Phi(t) =" sits in its own paragraph; fold it in
    absorb = set()
    for k in range(len(texts) - 1):
        prev = texts[k].strip()
        if (prev and k + 1 < len(texts) and cand[k + 1]
                and not _CJK_RE.search(prev) and _CONT_END_RE.search(prev)):
            texts[k + 1] = prev + " " + texts[k + 1].strip()
            texts[k] = ""
            absorb.add(k)
    cand = [is_bare_latex_paragraph(t) for t in texts]

    groups = []
    i = 0
    n = len(paras)
    while i < n:
        if not cand[i]:
            i += 1
            continue
        parts = [texts[i].strip()]
        last = i
        j = i + 1
        while j < n:
            joined = " ".join(parts)
            nxt = texts[j]
            if _open_envs(joined) > 0:
                # inside a formula environment: everything up to the closer is math
                if len(parts) > 60 or _CJK_RE.search(nxt):
                    break
                if not nxt.strip():
                    j += 1
                    continue
                parts.append(nxt.strip())
                last = j
                j += 1
                continue
            if cand[j] and (_CONT_END_RE.search(joined)
                            or _CONT_START_RE.search(nxt)
                            or _unbalanced(joined)):
                parts.append(nxt.strip())
                last = j
                j += 1
                continue
            break
        groups.append((i, last, " ".join(p for p in parts if p)))
        i = last + 1

    changed = 0
    drop = set(absorb)
    for first, last, latex in reversed(groups):
        if not latex.strip():
            continue
        _set_paragraph_plain(paras[first], "$$" + latex + "$$")
        drop.update(range(first + 1, last + 1))
        changed += 1

    for k in sorted(drop, reverse=True):
        if k >= len(paras):
            continue
        parent = paras[k].getparent()
        if parent is not None:
            parent.remove(paras[k])
    return changed


# ------------------------------------------------- cross-paragraph display math

def _paragraph_texts(container) -> List[Tuple[etree._Element, str]]:
    out = []
    for p in container.findall(qn("w:p")):
        out.append((p, "".join(t.text or "" for t in p.iter(qn("w:t")))))
    return out


def _merge_cross_paragraph_display(container) -> int:
    """Join display formulas that a paragraph break split apart ($\\n ... \\n$)."""
    items = _paragraph_texts(container)
    if len(items) < 2:
        return 0
    texts = [t for _, t in items]
    joined = "\n".join(texts)
    forms = [f for f in find_formulas(joined) if f.display]
    if not forms:
        return 0

    starts: List[int] = []
    pos = 0
    for t in texts:
        starts.append(pos)
        pos += len(t) + 1

    def locate(idx: int) -> Tuple[int, int]:
        for i in range(len(starts) - 1, -1, -1):
            if idx >= starts[i]:
                return i, idx - starts[i]
        return 0, idx

    merged = 0
    for f in reversed(forms):
        pi, off = locate(f.start)
        pj, off_end = locate(f.end - 1)
        if pi == pj:
            continue
        pre = texts[pi][:off]
        post = texts[pj][off_end + 1:]
        body = joined[f.start:f.end]
        paras = [items[k][0] for k in range(pi, pj + 1)]
        _set_paragraph_plain(paras[0], pre + body + post)
        for extra in paras[1:]:
            parent = extra.getparent()
            if parent is not None:
                parent.remove(extra)
        texts[pi] = pre + body + post
        for k in range(pi + 1, pj + 1):
            texts[k] = ""
        merged += 1
    return merged


def _set_paragraph_plain(p_el, text: str) -> None:
    runs = p_el.findall(qn("w:r"))
    if not runs:
        r = etree.SubElement(p_el, qn("w:r"))
        runs = [r]
    _set_run_text(runs[0], text)
    for r in runs[1:]:
        _set_run_text(r, "")


# ------------------------------------------------------------- paragraph pass

def _apply_formula(p_el, runs: List[Tuple[etree._Element, str]],
                   f: Formula, node: etree._Element, standalone: bool) -> None:
    offsets = []
    pos = 0
    for _, t in runs:
        offsets.append((pos, pos + len(t)))
        pos += len(t)

    def locate(idx: int) -> Tuple[int, int]:
        for i, (s, e) in enumerate(offsets):
            if s <= idx < e:
                return i, idx - s
        return len(runs) - 1, len(runs[-1][1]) if runs else 0

    si, so = locate(f.start)
    ei, eo = locate(max(f.start, f.end - 1))
    s_run, s_txt = runs[si]
    e_run, e_txt = runs[ei]

    if standalone:
        for r, _ in runs:
            for br in r.findall(qn("w:br")):
                r.remove(br)
            _set_run_text(r, "")
            _drop_if_empty(r)
        _center_paragraph(p_el)
        p_el.append(node)
        return

    prefix = s_txt[:so]
    suffix = e_txt[eo + 1:]

    if si == ei:
        new_run = copy.deepcopy(s_run) if suffix else None
        _set_run_text(s_run, prefix)
        if new_run is not None:
            _set_run_text(new_run, suffix)
        s_run.addnext(node)
        if new_run is not None:
            node.addnext(new_run)
    else:
        _set_run_text(s_run, prefix)
        _set_run_text(e_run, suffix)
        for k in range(si + 1, ei):
            _set_run_text(runs[k][0], "")
        s_run.addnext(node)


def _process_paragraph(p_el, conv: OmmlConverter, stats: Dict) -> None:
    runs = _runs_of(p_el)
    if not runs:
        return
    text = "".join(t for _, t in runs)
    if not text.strip():
        return
    forms = find_formulas(text)
    if not forms:
        return

    stripped = text.strip()
    for f in reversed(forms):
        omath = conv.to_omml(f.latex)
        if omath is None:
            stats["failed"] += 1
            stats.setdefault("failures", []).append(f.latex[:120])
            continue
        standalone = f.display and stripped == text[f.start:f.end].strip()
        if standalone:
            node = _make_omath_para(omath)
            stats["display"] += 1
        else:
            node = omath
            stats["display" if f.display else "inline"] += 1
        # offsets shift after every insertion, so re-read runs each round
        runs = _runs_of(p_el)
        _apply_formula(p_el, runs, f, node, standalone)
        if standalone:
            break


# ---------------------------------------------------------------- entrypoints

def _scope_containers(doc):
    body = doc.element.body
    yield body
    for tc in body.iter(qn("w:tc")):
        yield tc
    for sec in doc.sections:
        for hf in (sec.header, sec.footer, sec.first_page_header,
                   sec.first_page_footer, sec.even_page_header, sec.even_page_footer):
            try:
                if hf.is_linked_to_previous:
                    continue
                yield hf._element
            except Exception:
                continue


def process_docx(src: str, dst: str, log=None) -> Dict:
    """Rewrite all LaTeX formulas in `src` as native Word equations, save to `dst`."""
    doc = Document(src)
    conv = OmmlConverter()
    stats: Dict = {"display": 0, "inline": 0, "failed": 0, "merged_blocks": 0,
                   "bare_blocks": 0,
                   "omml_available": conv.available, "xsl": conv.xsl_path,
                   "failures": []}

    if conv.available:
        for container in _scope_containers(doc):
            # join `$$` blocks split by paragraph breaks first, so the bare-LaTeX
            # pass never mistakes a continuation line for an undelimited formula
            try:
                stats["merged_blocks"] += _merge_cross_paragraph_display(container)
            except Exception as exc:
                stats.setdefault("warnings", []).append("merge: %s" % exc)
            try:
                stats["bare_blocks"] += _wrap_bare_latex_blocks(container)
            except Exception as exc:
                stats.setdefault("warnings", []).append("bare: %s" % exc)

        # Materialise the paragraph list first: mutating the tree while iterating
        # a lazy lxml iterator silently truncates the walk.
        paragraphs = []
        seen = set()
        for container in _scope_containers(doc):
            for p in container.iter(qn("w:p")):
                if p in seen:
                    continue
                seen.add(p)
                paragraphs.append(p)

        for p in paragraphs:
            if p.getparent() is None:  # removed by a cross-paragraph merge
                continue
            try:
                _process_paragraph(p, conv, stats)
            except Exception as exc:
                stats.setdefault("warnings", []).append("para: %s" % exc)
    else:
        stats.setdefault("warnings", []).append(
            "MML2OMML.XSL not found; native equations unavailable")

    doc.save(dst)
    return stats
