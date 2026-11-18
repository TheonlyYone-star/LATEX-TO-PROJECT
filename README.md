# LATEX-TO-PROJECT

把论文里的 LaTeX 公式源码，转成各文件格式下**真正排版好的原生公式**，输出保持原文件后缀。

## 这是什么

一个 WorkBuddy / Claude Skill：`latex-formula-formatter`。

上传论文 → 自动识别 `$...$`、`$$...$$`、`\[...\]`、`\(...\)`、`\begin{equation}` 等公式源码 → 转成该格式最专业的原生形态 → 输出同名同后缀的文件。

| 输入 | 输出形态 |
| --- | --- |
| `.docx` | **Word 原生公式（OMML）**，双击可继续编辑 |
| `.pdf` | 原位替换：遮盖原文 LaTeX 后叠加高精度渲染公式，**完整保留原版式与分页** |
| `.md` | base64 内嵌公式图，单文件自包含，任意 Markdown 阅读器可见 |
| `.tex` | 规范成 `equation` / `equation*` 环境，自动补齐 `amsmath,amssymb` |
| `.txt` | Unicode 数学符号（`∫₀^∞ e^(-x²) dx`、`x ∈ R^d`） |

## 能力要点

- **Word 原生公式**：LaTeX → MathML → OMML（MSO `MML2OMML.XSL`），插入 `m:oMath` / `m:oMathPara`，保留上下文字符格式；块级公式居中并自动编号；跨段落的 `$$` 自动合并。
- **裸 LaTeX 也能救**：md→docx 的简易转换器常把定界符剥掉、一条公式拆成一行一段。脚本能识别并拼回完整公式，连 `\Phi(t) =` 这种独立成段的前缀也会折进去。
- **重音与间距正确**：`\dot{x}` / `\ddot{x}` / `\vec{v}` 走 `m:acc` 重音而非"上下限"（否则点会飘在字母上方）；`\quad` / `\qquad` 间距不会丢。
- **矩阵括号会拉伸**：自动把 `[ ... ]` 重包成 `m:d` 定界符对象，不再是小号方括号。
- **多行环境排得对**：`align` 按 `=` 对齐、`cases` 带自适应大括号、`pmatrix/bmatrix` 带拉伸定界符；多行公式按行列网格拼版，效果接近 LaTeX 原生。
- **中英混排**：`\text{其中 } \sigma>0` 自动走 CJK 混排渲染。
- **永不空白**：渲染降级链 结构化布局 → mathtext → 净化 mathtext → Unicode 文本图，单条失败只保留原文并计入报告，不中断整体流程。

## 安装

把 `latex-formula-formatter/` 整个目录放到 skill 目录下：

- 用户级（推荐）：`~/.workbuddy/skills/latex-formula-formatter/`
- 项目级：`<项目>/.workbuddy/skills/latex-formula-formatter/`

或直接解压 `latex-formula-formatter.zip` 到上述位置。

### 依赖

```bash
pip install python-docx lxml pymupdf matplotlib reportlab latex2mathml
```

DOCX 原生公式需要 `MML2OMML.XSL`（随 Microsoft Office 安装，脚本自动在
`C:\Program Files\Microsoft Office\root\Office*\` 下查找，也可用环境变量 `MML2OMML_XSL` 指定）。
找不到时脚本只输出副本并报告 `omml_available: false`。

## 用法

在对话里直接说：

> 把这篇论文里的 LaTeX 公式转成正常公式

或显式调用 `/latex-formula-formatter`。

命令行（不经过对话）：

```bash
python latex-formula-formatter/scripts/format_formulas.py paper.docx
# -> paper-formatted.docx

python latex-formula-formatter/scripts/format_formulas.py paper.md -o out/paper.md --json
```

| 参数 | 说明 |
| --- | --- |
| `-o/--output` | 输出路径，默认 `<name>-formatted.<原后缀>` |
| `--dpi` | 位图精度，PDF 默认 400，MD 默认 200 |
| `--fontsize` | 公式字号，MD 默认 12.5 |
| `--json` | 输出机器可读摘要 |

摘要字段：`display` `inline` `failed` `failures` `replaced` `pre_rendered` `merged_blocks` `bare_blocks` `omml_available`。

## 已知边界

- **PDF 无法重排**：原文的 LaTeX 源码比排版后的公式宽，原位替换后该行会留下空白间距。已是排版态的 PDF（LaTeX/Word 正常导出）没有字面 LaTeX，输出即原件副本。
- **行内降级文本不可逆**：若源 docx 由 md→docx 简易转换器生成，行内公式在生成时就被压成普通文本（`n = μ/a^3`、`d_low`），docx 中已无可还原的源码。此时只能还原块级公式，行内建议从 `.md` 源文重建。
- **mathtext 不支持的环境**（位图路径）：`\begin{...}` 由结构化布局消化；`\lVert` 等已被别名映射；超复杂宏会落到 Unicode 兜底。

## 结构

```
latex-formula-formatter/
├── SKILL.md                    # skill 定义与工作流
├── references/
│   ├── detection-rules.md      # 识别规则、裸 LaTeX 判定、摘要字段
│   └── formats.md              # 各格式的转换细节与限制
└── scripts/
    ├── format_formulas.py      # CLI 入口
    ├── latex_core.py           # 识别 / 清洗 / 校验 / Unicode 转换
    ├── docx_omml.py            # LaTeX -> OMML、段落重写、MathML 层修正
    ├── pdf_rebuild.py          # PDF 原位替换
    ├── mathrender.py           # 位图渲染（结构化布局 + CJK 混排）
    └── text_formats.py         # md / tex / txt
```
