# 公式识别与渲染规则

## 1. 识别哪些写法

`scripts/latex_core.py:find_formulas` 按以下优先级扫描文本（先匹配到的先占位，重叠的丢弃）：

| 类型 | 正则形态 | 判定 | 典型来源 |
| --- | --- | --- | --- |
| `bracket` | `\[ ... \]` | 块级 | TeX 导出、Pandoc |
| `paren` | `\( ... \)` | 行内 | TeX 导出 |
| `dollar2` | `$$ ... $$` | 块级，**可跨行/跨文本块** | Markdown、AI 生成稿、Word 里粘的源码 |
| `env` | `\begin{equation|align|gather|multline|eqnarray|cases|matrix...} ... \end{同名}` | 块级 | 论文源码 |
| `dollar1` | `$ ... $`（单行内） | 行内 | Markdown、笔记 |

单 `$` 匹配额外过 `_looks_like_math()` 闸门，避免把 `$5 与 $10` 这类价格当公式：正文里必须出现 `\ ^ _ { }`、关系/运算符、或为单个变量才认定。

## 2. 明确跳过的内容

- Markdown 代码块（``` ``` 与 ~~~ ~~~）和行内代码 `` `...` `` 内的 `$`——先做等长掩码再扫描，输出时还原。
- 转义的 `\$`（不视为定界符）。
- 已含 `$` 定界符的段落不会被当成"裸 LaTeX"重复包装；自成一体的 `\begin{env}...\end{env}` 段落走 env 分支。
- PDF 中判定为"已排版公式"的区域：整行中 ≥50% 字符来自数学字体且该行不含 `$`/`\begin{`。

## 2.1 裸 LaTeX 段落（DOCX 专用）

`is_bare_latex_paragraph()` 用于识别被 md→docx 转换器剥掉定界符的公式段落，全部条件同时满足才判定：

1. 长度 ≥4，且不含 `$`；
2. 不含 CJK 字符；
3. 含至少一个 `\macro`（`\[A-Za-z]{2,}`）；
4. 不是完整的 `\begin{env}...\end{env}`（那种直接交给 env 分支）；
5. 剔除宏后剩余英文单词 ≤6（超过即为散文）；
6. 数学信号 ≥2：`\` 出现 ≥2 次 / 含 `{` / 含 `^` 或 `_` / 含 `&` / 以 `\begin` 开头。

相邻段落按下列任一条件续接成一条公式：环境未闭合、当前片段以运算符或 `(` `[` `{` `,` 结尾、下一片段以 `\quad \qquad \text{ \exp \log \iint \int \sum \left \right \end{ \\ , . =` 开头、当前片段括号未配对。

## 3. 数学字体识别（仅 PDF）

`MATH_FONT_RE` 命中即视为数学字体：`CMMI CMSY CMEX CMR* CMBX CMSL MSAM MSBM EUFM EUSM RSFS SYMBOL STIX XITS MathJax LatinModern LMRoman LMMath TeXGyre Asana CambriaMath DejaVuMath Euclid` 以及含 `math` 的字体名。

PDF 走的是**原位替换**：只处理字面 LaTeX 文本，已排版区域仅计数（`pre_rendered`）。

## 4. 清洗与函数名

- `clean_latex()` 去掉 `\label \tag \nonumber \notag \hfill \displaystyle` 等只影响编号/样式的宏，折叠多余空白。
- DOCX 路径额外把 `\log \ln \exp \sin \cos \tan \lim \max \min \sup \inf \det \dim \gcd \arg ...` 改写为 `\mathrm{...}`，保证 Word 里函数名是直立体（否则 Word 默认渲染为斜体）。
- 多行环境用 `split_env()` + `split_rows()` 拆成行/列网格：`\\` 断行、`&` 断列，且只在花括号深度 0 的位置断。

## 5. 位图渲染降级链（`scripts/mathrender.py`）

1. **结构化布局**：`align/aligned/alignat/split/eqnarray` → 右侧列对 `=` 对齐；`cases` → 左对齐 + 自适应大括号；`pmatrix/bmatrix/vmatrix/Vmatrix` → 网格 + 拉伸定界符。每格单独渲染后拼版，效果接近 LaTeX 原生。
2. **mathtext**：`matplotlib` 的 STIX 字体集，无需系统安装 LaTeX。渲染前经 `_sanitize()` 做宏别名映射（`\le→\leq`、`\dfrac→\frac`、`\lVert→\Vert`、`\bm→\boldsymbol` 等）并剔除 mathtext 不支持的宏。
3. **中文混排**：公式含 CJK 时，把 `\text{...}` 段拆出为正文文本、其余段包成 `$...$`，交给 matplotlib 混排渲染（CJK 用 Microsoft YaHei / SimHei / Noto CJK）。
4. **Unicode 文本图**：以上都失败时用 `latex_to_unicode()` 出图，保证一定有产物。

已知 mathtext 不支持：`\begin{...}` 环境（由第 1 步消化）、`\lVert/\rVert/\lvert/\rvert`（已映射）、`\displaystyle`（已剔除）、CJK 字形（由第 3 步消化）。

## 6. 摘要字段含义

| 字段 | 含义 |
| --- | --- |
| `display` / `inline` | 识别到的块级 / 行内公式数量 |
| `failed` / `failures` | 未能转换的数量与原文片段（该处保留原 LaTeX） |
| `replaced` | PDF 中实际完成原位替换的数量 |
| `pre_rendered` | PDF 中判定为"已是排版态"的文本块数量 |
| `merged_blocks` | DOCX 中跨段落合并的 `$$` 块数量 |
| `bare_blocks` | DOCX 中识别并拼回的"裸 LaTeX"公式块数量 |
| `skipped_code` | MD 中因处于代码块而跳过的数量 |
| `omml_available` / `xsl` | DOCX 原生公式引擎是否可用及其 XSL 路径 |
| `packages_added` | TEX 中自动补齐的宏包 |
