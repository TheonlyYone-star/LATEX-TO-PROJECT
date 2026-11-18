# -*- coding: utf-8 -*-
"""LaTeX formula core utilities: detection, normalization, validation, Unicode fallback.

No third-party hard dependency except optional `latex2mathml` for validation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ---------------------------------------------------------------- environments

DISPLAY_ENVS = (
    "equation", "align", "aligned", "alignat", "gather", "gathered",
    "multline", "split", "eqnarray", "displaymath", "cases", "array",
    "matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix", "smallmatrix",
    "subequations",
)
_ENV_ALT = "|".join(DISPLAY_ENVS)

# Priority order matters: bracket / paren / double-dollar / environment / single-dollar.
_FORMULA_RE = re.compile(
    r"(?P<bracket>\\\[(?P<b1>.*?)\\\])"
    r"|(?P<paren>\\\((?P<p1>.*?)\\\))"
    r"|(?P<d2>\$\$(?P<d2b>.*?)\$\$)"
    r"|(?P<env>\\begin\{(?P<envname>" + _ENV_ALT + r")(?P<star>\*?)\}"
    r"(?P<envbody>.*?)\\end\{(?P=envname)(?P=star)\})"
    r"|(?<![\\$])\$(?!\$)(?P<d1>(?:\\.|[^$\\\n]){1,600}?)(?<!\\)\$(?!\$)",
    re.S,
)

_FENCE_RE = re.compile(r"(```.*?```|~~~.*?~~~)", re.S)
_INLINE_CODE_RE = re.compile(r"(`[^`\n]*`)")


@dataclass
class Formula:
    """A detected formula inside a plain-text buffer."""

    start: int
    end: int
    latex: str
    display: bool
    kind: str  # bracket | paren | dollar2 | env | dollar1

    @property
    def raw(self) -> str:
        return self.latex


# ------------------------------------------------------------------ detection

def _looks_like_math(s: str) -> bool:
    """Heuristic guard for single-dollar matches (avoids "$5 and $10")."""
    if not s or not s.strip():
        return False
    if re.search(r"[\\^_{}]", s):
        return True
    if re.search(r"[=<>≤≥±×÷∑∏∫√≠≈≡∞∂∇∈∉⊂⊃∪∩]", s):
        return True
    if re.search(r"[A-Za-z0-9)\]]\s*[+\-*/·]\s*[A-Za-z0-9(\[]", s):
        return True
    if re.fullmatch(r"\s*[A-Za-z]\s*", s):  # lone variable, e.g. $x$
        return True
    return False


def mask_code(text: str, style: str = "markdown") -> Tuple[str, List[str]]:
    """Blank out code spans so they are never treated as formulas.

    Returns (masked_text, placeholders). Masking preserves length and offsets.
    """
    if style != "markdown":
        return text, []

    holders: List[str] = []

    def _sub(m: re.Match) -> str:
        holders.append(m.group(0))
        return "\x00" * len(m.group(0))

    masked = _FENCE_RE.sub(_sub, text)
    masked = _INLINE_CODE_RE.sub(_sub, masked)
    return masked, holders


def unmask_code(text: str, placeholders: List[str]) -> str:
    for h in placeholders:
        text = text.replace("\x00" * len(h), h, 1)
    return text


def find_formulas(text: str) -> List[Formula]:
    """Return non-overlapping formulas in document order."""
    found: List[Formula] = []
    for m in _FORMULA_RE.finditer(text):
        if m.group("bracket") is not None:
            latex, display, kind = m.group("b1"), True, "bracket"
        elif m.group("paren") is not None:
            latex, display, kind = m.group("p1"), False, "paren"
        elif m.group("d2") is not None:
            latex, display, kind = m.group("d2b"), True, "dollar2"
        elif m.group("env") is not None:
            latex, display, kind = m.group("env"), True, "env"
        else:
            latex, display, kind = m.group("d1"), False, "dollar1"
            if not _looks_like_math(latex):
                continue

        if latex is None or not latex.strip():
            continue
        f = Formula(m.start(), m.end(), latex.strip(), display, kind)
        if found and f.start < found[-1].end:
            continue
        found.append(f)
    return found


# ---------------------------------------------------------------- normalization

_LABEL_RE = re.compile(r"\\(label|tag|nonumber|notag|hfill|displaystyle|textstyle|scriptstyle)\b\s*(\{[^{}]*\})?")
_WS_RE = re.compile(r"[ \t]{2,}")


def clean_latex(latex: str, keep_layout: bool = True) -> str:
    """Strip numbering/labels and collapse whitespace for rendering engines."""
    s = _LABEL_RE.sub("", latex)
    s = s.replace("\u00a0", " ")
    # row spacing such as \\[6pt] is layout noise for the converters we use
    s = re.sub(r"\\\\\s*\[\s*[\d.]+\s*(pt|em|ex|mm|cm|in)\s*\]", r"\\\\", s)
    if keep_layout:
        s = re.sub(r"[ \t]+", " ", s)
        s = re.sub(r"\n\s*\n+", r"\n", s)
        s = re.sub(r"\n", " ", s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


def split_env(latex: str) -> Tuple[Optional[str], str]:
    """If latex is `\\begin{env} body \\end{env}`, return (env, body)."""
    m = re.match(r"\s*\\begin\{([A-Za-z*]+)\}(.*)\\end\{\1\}\s*$", latex, re.S)
    if m:
        return m.group(1), m.group(2)
    return None, latex


def split_rows(body: str) -> List[List[str]]:
    """Split a multi-row body on `\\\\` then on `&`, respecting brace nesting."""
    rows: List[List[str]] = [[]]
    buf: List[str] = []
    depth = 0
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c == "\\" and i + 1 < n:
            nxt = body[i + 1]
            if nxt == "\\" and depth == 0:
                rows[-1].append("".join(buf))
                buf = []
                rows.append([])
                i += 2
                continue
            if nxt == "&" and depth == 0:
                rows[-1].append("".join(buf))
                buf = []
                i += 2
                continue
            buf.append(c)
            buf.append(nxt)
            i += 2
            continue
        if c == "&" and depth == 0:
            rows[-1].append("".join(buf))
            buf = []
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        buf.append(c)
        i += 1
    rows[-1].append("".join(buf))
    rows = [[c.strip() for c in r] for r in rows if any(c.strip() for c in r)]
    return rows or [[""]]


def to_table(latex: str) -> Tuple[str, List[List[str]]]:
    """Return (kind, cells) where kind is 'single' or 'table'.

    Multi-row / multi-column math (align, cases, matrix...) becomes a cell grid
    so that renderers can lay it out as a real equation array.
    """
    env, body = split_env(latex)
    if env and env.rstrip("*") in ("align", "aligned", "alignat", "gather", "gathered",
                                    "multline", "split", "eqnarray", "cases", "array",
                                    "matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix",
                                    "smallmatrix"):
        rows = split_rows(body)
        if len(rows) > 1 or any(len(r) > 1 for r in rows):
            return env.rstrip("*"), rows
        if rows:
            return "single", [[rows[0][0]]]
    return "single", [[clean_latex(latex)]]


# ------------------------------------------------------------------ validation

def _balanced(s: str) -> bool:
    depth = 0
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def validate_latex(latex: str) -> Tuple[bool, str]:
    """Best-effort validation. Multi-row bodies are validated row by row."""
    s = latex.strip()
    if not s:
        return False, "empty"
    if not _balanced(s):
        return False, "unbalanced braces"
    try:
        import latex2mathml.converter as conv
    except Exception:  # pragma: no cover
        return True, ""

    env, body = split_env(s)
    if env:
        for row in split_rows(body):
            for cell in row:
                if not cell.strip():
                    continue
                if not _balanced(cell):
                    return False, "unbalanced braces in row"
                try:
                    conv.convert(cell)
                except Exception as exc:
                    return False, str(exc).splitlines()[0][:120]
        return True, ""

    try:
        conv.convert(s)
        return True, ""
    except Exception as exc:
        return False, str(exc).splitlines()[0][:120]


# --------------------------------------------------------- unicode conversion

_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ",
    "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}

_SYMS = {
    "times": "×", "div": "÷", "pm": "±", "mp": "∓", "cdot": "·", "ast": "∗",
    "star": "⋆", "circ": "∘", "bullet": "•", "oplus": "⊕", "otimes": "⊗",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "simeq": "≃", "cong": "≅",
    "propto": "∝", "ll": "≪", "gg": "≫", "lll": "⋘", "ggg": "⋙",
    "infty": "∞", "partial": "∂", "nabla": "∇", "forall": "∀", "exists": "∃",
    "nexists": "∄", "emptyset": "∅", "varnothing": "∅", "neg": "¬", "lnot": "¬",
    "land": "∧", "wedge": "∧", "lor": "∨", "vee": "∨",
    "int": "∫", "iint": "∬", "iiint": "∭", "oint": "∮",
    "sum": "∑", "prod": "∏", "coprod": "∐",
    "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "supset": "⊃",
    "subseteq": "⊆", "supseteq": "⊇", "cup": "∪", "cap": "∩", "setminus": "∖",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒",
    "Leftarrow": "⇐", "leftrightarrow": "↔", "Leftrightarrow": "⇔",
    "mapsto": "↦", "uparrow": "↑", "downarrow": "↓", "longrightarrow": "⟶",
    "Longrightarrow": "⟹", "iff": "⇔", "implies": "⇒",
    "ldots": "…", "dots": "…", "cdots": "⋯", "vdots": "⋮", "ddots": "⋱",
    "angle": "∠", "perp": "⊥", "parallel": "∥", "mid": "|", "vert": "|",
    "Vert": "‖", "lvert": "|", "rvert": "|", "lVert": "‖", "rVert": "‖",
    "langle": "⟨", "rangle": "⟩", "lceil": "⌈", "rceil": "⌉",
    "lfloor": "⌊", "rfloor": "⌋",
    "hbar": "ℏ", "ell": "ℓ", "Re": "ℜ", "Im": "ℑ", "aleph": "ℵ",
    "prime": "′", "degree": "°", "square": "□", "triangle": "△",
    "quad": " ", "qquad": "  ", ",": " ", ";": " ", ":": " ", "!": "",
    "%": "%", "&": "&", "#": "#", "_": "_", "{": "{", "}": "}",
    "left": "", "right": "", "big": "", "Big": "", "bigg": "", "Bigg": "",
    "limits": "", "nolimits": "", "displaystyle": "", "textstyle": "",
    "\\": " ",
}

_SUP = dict(zip("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ"))
_SUB = dict(zip("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎"))
_SUB.update({"a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ",
             "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ",
             "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ"})


def _arg(s: str, i: int) -> Tuple[str, int]:
    """Read one macro argument starting at index i."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return "", i
    if s[i] == "{":
        depth = 0
        j = i
        while j < len(s):
            if s[j] == "\\":
                j += 2
                continue
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], len(s)
    m = re.match(r"\\[A-Za-z]+", s[i:])
    if m:
        return m.group(0), i + m.end()
    return s[i], i + 1


def _script(txt: str, table: dict, marker: str) -> str:
    if txt and all(ch in table for ch in txt):
        return "".join(table[ch] for ch in txt)
    if len(txt) == 1:
        return marker + txt
    return f"{marker}({txt})"


def latex_to_unicode(latex: str) -> str:
    """Convert LaTeX to readable Unicode math text (used for .txt output)."""
    s = clean_latex(latex)

    m = re.search(r"\\begin\{([A-Za-z*]+)\}", s)
    if m:
        end_re = re.compile(r"\\end\{" + re.escape(m.group(1)) + r"\}")
        ends = list(end_re.finditer(s))
        if ends:
            prefix = s[:m.start()].strip()
            body = s[m.end():ends[-1].start()]
            suffix = s[ends[-1].end():].strip()
            rows = split_rows(body)
            body_txt = " ; ".join(
                " ".join(latex_to_unicode(c) for c in row if c.strip()).strip()
                for row in rows if any(c.strip() for c in row))
            joined = " ".join(x for x in (latex_to_unicode(prefix) if prefix else "",
                                          body_txt,
                                          latex_to_unicode(suffix) if suffix else "") if x)
            return re.sub(r"\s{2,}", " ", joined).strip()

    out: List[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+)\*?", s[i:])
            if m:
                name = m.group(1)
                i += m.end()
                if name in ("frac", "dfrac", "tfrac"):
                    a, i = _arg(s, i)
                    b, i = _arg(s, i)
                    ua, ub = latex_to_unicode(a), latex_to_unicode(b)
                    if len(ub) <= 2:
                        out.append(f"{ua}/{ub}")
                    elif len(ua) <= 2:
                        out.append(f"{ua}/({ub})")
                    else:
                        out.append(f"({ua})/({ub})")
                elif name == "sqrt":
                    a, i = _arg(s, i)
                    out.append("√(" + latex_to_unicode(a) + ")")
                elif name in ("text", "mathrm", "mathbf", "mathit", "mathsf",
                              "mathtt", "mathcal", "mathbb", "mathfrak",
                              "operatorname", "boldsymbol", "mbox", "displaystyle"):
                    a, i = _arg(s, i)
                    out.append(latex_to_unicode(a) if name not in ("text", "mbox", "operatorname")
                               else a.replace("\\", ""))
                elif name in ("hat", "bar", "vec", "tilde", "dot", "ddot", "overline", "underline"):
                    a, i = _arg(s, i)
                    out.append(latex_to_unicode(a))
                elif name in _GREEK:
                    out.append(_GREEK[name])
                elif name in _SYMS:
                    out.append(_SYMS[name])
                else:
                    out.append(name)
                continue
            if i + 1 < n and s[i + 1] == "\\":
                out.append(" ; ")
                i += 2
                continue
            if i + 1 < n:
                out.append(_SYMS.get(s[i + 1], s[i + 1]))
                i += 2
                continue
            i += 1
            continue
        if c == "^":
            a, i = _arg(s, i + 1)
            out.append(_script(latex_to_unicode(a), _SUP, "^"))
            continue
        if c == "_":
            a, i = _arg(s, i + 1)
            out.append(_script(latex_to_unicode(a), _SUB, "_"))
            continue
        if c == "{":
            a, i = _arg(s, i)
            out.append(latex_to_unicode(a))
            continue
        if c == "}":
            i += 1
            continue
        if c == "&":
            out.append(" ")
            i += 1
            continue
        out.append(c)
        i += 1
    res = re.sub(r"\s{2,}", " ", "".join(out)).strip()
    return res
