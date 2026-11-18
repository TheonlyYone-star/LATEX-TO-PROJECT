# 各格式的转换细节与限制

## DOCX —— 原生 Word 公式（OMML）

链路：`LaTeX → latex2mathml → MathML → MML2OMML.XSL → m:oMath`

- **插入位置**：按字符偏移把公式定界符替换为 `m:oMath`，直接作为 `w:p` 的子元素插在前后 run 之间，因此周围文字的粗体/字号/颜色全部保留。
- **块级公式**：当公式独占整段（去掉首尾空白后段落文本恰为公式本身）时，包一层 `m:oMathPara`（居中）并把段落对齐设为居中；原段落里的换行符 `w:br` 会一并清除，避免公式前出现空行。
- **跨段落**：Word 里粘进来的 `$$\n...\n$$` 常被拆成多个段落。`_merge_cross_paragraph_display()` 会先按容器合并这类块（把后续段落内容并入首段并删除空段），再进入公式替换。
- **裸 LaTeX（无 `$` 定界符）**：md→docx 的简易转换器会把行间公式的定界符剥掉，并把一条公式拆成"一行一段"。`_wrap_bare_latex_blocks()` 用 `is_bare_latex_paragraph()` 判定（无 CJK、含 `\macro`、数学信号 ≥2、不像散文），再按"环境未闭合 / 结尾是运算符 / 下段以 `\quad \exp \iint \end{` 等开头 / 括号未配对"续接相邻段，拼成一个 `$$...$$` 段落。`\Phi(t) =` 这类独立成段的前缀也会被折进后续公式块。
- **MathML 层修正**（`_prepare_mathml` / `_wrap_matrix_delims`）：
  - `<mover>` 缺 `accent="true"` 会被 MSO 当成"上下限"，导致 `\dot{x}` 的点儿飘在字母上方远处 → 给重音字符补 `accent="true"`，得到 `m:acc`；`\bar` 保持 `m:bar`。
  - `<mspace>` 会被整段丢弃，`\quad`/`\qquad` 的间距全没了 → 换成 em space / thin space 的 `<mo>`。
  - MSO 不生成 `m:d`，矩阵括号只是普通 run（字号高度、不拉伸）→ 把 `run([) + m:m + run(])` 重包成 `m:d` 并设置 `begChr`/`endChr`，Word 才会自动拉长括号。
  - `aligned/gathered/split` 会让 latex2mathml 吐出裸 `&`，产出的 XML 非法 → 归一成 `align/gather`（`_normalize_envs`），并对残留裸 `&` 做实体转义兜底。
- **遍历范围**：正文、表格单元格、页眉页脚（`w:p` 去重遍历），先物化段落列表再修改——lxml 的惰性迭代器在树被修改时会静默截断。
- **不可用时**：找不到 `MML2OMML.XSL` 时 `omml_available=false`，脚本不改动公式只保存副本，必须提示用户安装 Office。

**已知边界**：若源文档由 md→docx 简易转换器生成，行内公式在生成时就被降级成普通文本（`n = μ/a^3`、`P_c`、`d_low`），docx 中已无 LaTeX 源码可还原。此时只能还原块级公式；行内公式建议从 `.md` 源文重建文档。

已验证效果：行内公式与正文同行、块级公式居中、`cases` 带大括号两列对齐、`pmatrix` 矩阵、表格单元格内公式均正常；`\log` 等函数名为直立体。

## PDF —— 原位替换，保留版式

PDF 里的公式通常已经是排版态，重建文本会毁掉版式，因此只做最小干预：

1. `rawdict` 逐字符提取（每个字符带 bbox），把整页文本块按阅读顺序拼成一条流，从而支持**跨行、跨文本块**的 `$$`。
2. 命中字面 LaTeX 后，按"行"分组算出多块矩形，`add_redact_annot` 精准删掉这些字符（不碰图片/矢量）。
3. `apply_redactions` 后把 400 DPI 渲染的透明 PNG 叠回去：单行公式按基线对齐（`baseline + 0.22em` 作为底边），跨行公式按被删区域的包围盒居中缩放。

**限制（必须在交付时说明）**

- PDF 无法重排：原来的 LaTeX 源码文本比排版后的公式宽，替换后该行会留下空白间距。
- 已经是排版态（LaTeX/Word 正常导出）的 PDF 没有字面 LaTeX，`replaced == 0`，输出为原件副本 + 统计信息。
- 跨行公式会被压到原文本块的高度内，可能比正文略小。

## MD —— 渲染公式图，单文件自包含

- 每处公式渲染为 PNG（默认 200 DPI、12.5pt），以 `data:image/png;base64` 内嵌，不产生额外附件目录。
- 尺寸按 `px * 96 / dpi` 换算成 CSS 像素，与 16px 正文视觉大小一致。
- 块级公式独占一行时包 `<p align="center">`；行内保持行内。`alt` 属性保留原始 LaTeX，便于回溯。
- 代码块/行内代码中的 `$` 不处理（先掩码再还原）。

## TEX —— 规范成出版级环境

- `$$ ... $$` → `\begin{equation} ... \end{equation}`（带编号）
- `\[ ... \]` → `\begin{equation*} ... \end{equation*}`（不编号）
- `\( ... \)` → `$ ... $`
- 若存在 `\begin{document}` 而缺少宏包，自动在 `\documentclass` 之后补 `\usepackage{amsmath,amssymb}`。
- 已有的 `align/gather/...` 环境原样保留并计数。

## TXT —— Unicode 数学符号

- `\frac{a}{b}` → `a/b`；`\sqrt{x}` → `√(x)`；`^{2}` → `²`；`_{i}` → `ᵢ`（无法映射的用 `^(...)`/`_(...)`）。
- `\alpha`→`α`、`\sum`→`∑`、`\int`→`∫`、`\le`→`≤`、`\infty`→`∞` 等常用宏全覆盖。
- 多行环境（`cases/align/matrix`）按行展开，行间用 `;` 分隔，并保留 `f(x)=` 这类前缀。
- 这是唯一"不能真正排版"的格式，属于纯文本可读性兜底。
