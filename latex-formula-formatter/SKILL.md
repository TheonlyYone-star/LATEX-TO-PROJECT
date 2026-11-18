---
name: latex-formula-formatter
description: 论文 LaTeX 公式自动识别与专业排版转换。当用户上传或指向论文/文稿文件（pdf、docx、md、tex、txt），并要求把里面的 LaTeX 公式（$...$、$$...$$、\[...\]、equation/align 环境等）转换成专业排版形式、把公式"显式化/正常显示"、美化公式、修正未渲染的公式源码时使用。输出保持原文件后缀（PDF→PDF、DOCX→原生 Word 公式、MD→渲染公式图、TEX→规范公式环境、TXT→Unicode 数学符号），可直接交付。
agent_created: true
---

# LaTeX 公式格式化

## 用途

识别论文中的 LaTeX 公式源码，并将其转换为该文件格式下**最专业的原生排版形态**，输出文件与输入保持同名同后缀，公式可读、可编辑、可直接交付。

核心能力由 `scripts/format_formulas.py` 提供，一条命令完成全流程，不要把公式处理改写成临时脚本。

## 何时使用

出现以下任一情形即使用本 skill：

- 用户上传/给出论文文件（`.pdf` `.docx` `.md` `.tex` `.txt`），要求"公式转换""公式排版""把 LaTeX 变成正常公式""公式显示不出来""美化公式"。
- 文稿里存在 `$...$`、`$$...$$`、`\(...\)`、`\[...\]`、`\begin{equation}` 等 LaTeX 源码需要变成可读公式。
- 用户要求"按原后缀输出""输出成文件"的公式处理任务。

不适用于：从零排版一篇新论文（用 docx/pptx 相关 skill）、纯图片公式 OCR。

## 工作流

### 1. 定位输入文件并确认输出路径

输入是会话中上传的附件路径，或用户明确给出的路径。默认输出到输入同目录，文件名加 `-formatted` 后缀；用户指定路径时用 `-o`。

### 2. 运行转换脚本

脚本依赖隔离环境中的 Python（见"环境准备"）。标准调用：

```bash
"<PY>" "<SKILL_DIR>/scripts/format_formulas.py" "<INPUT>" -o "<OUTPUT>"
```

加 `--json` 获取机器可读摘要（字段：`display` `inline` `failed` `failures` `replaced` `pre_rendered` 等）。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `-o/--output` | 输出路径；默认 `<name>-formatted.<原后缀>` |
| `--dpi` | 位图精度；PDF 默认 400，MD 默认 200 |
| `--fontsize` | 公式字号；MD 默认 12.5 |
| `--json` | 输出 JSON 摘要 |

### 3. 核对摘要并交付

读脚本 stdout。若 `failed > 0`，检查 `failures` 中的公式，必要时补跑或向用户说明这几处保留原文。随后用 `present_files` 打开输出文件。

**交付时必须说明的两点：**

- 本次转换了多少处公式（块级/行内），失败几处。
- 若输入是 PDF 且 `replaced == 0`：该 PDF 的公式本来就是排版好的（或已内嵌为对象），输出等同原件副本；PDF 无法重排，因此若原文是 LaTeX 源码，替换后行内会留有空白间距。

**若 `bare_blocks > 0`**：说明源文档是"裸 LaTeX"（无 `$` 定界符、按源码行拆段）。这类文档通常是 md→docx 的简易转换器生成的，块级公式能完整还原；但**行内公式在生成阶段已被降级成普通文本**（如 `n = μ/a^3`、`d_low`），docx 里已不存在可还原的 LaTeX 源码。遇到这种情况要主动告知用户，并建议从 `.md` 源文重新生成，而不是在 docx 上手工修补。

## 各格式的处理方式

| 输入 | 输出形态 | 说明 |
| --- | --- | --- |
| `.docx` | **Word 原生公式（OMML）** | 走 LaTeX→MathML→OMML（MSO `MML2OMML.XSL`），插入 `m:oMath`/`m:oMathPara`，保留上下文字符格式；独立成行的块级公式居中；跨段落的 `$$` 自动合并；**无 `$` 定界符的裸 LaTeX 段落（常见于 md→docx 生成稿）也能识别并按行拼回完整公式** |
| `.pdf` | 原位替换 | 逐字符定位字面 LaTeX，遮盖删除后叠加高精度渲染公式，**完整保留原版式与分页**；已是排版态（CMMI/STIX/Cambria Math 字体）的区域只统计不改动 |
| `.md` | 渲染公式图（base64 内嵌） | 单文件自包含，任意 Markdown 阅读器都能看到排版效果；`alt` 保留原 LaTeX，代码块与行内代码内的 `$` 不处理 |
| `.tex` | 规范公式环境 | `$$`→`equation`、`\[`→`equation*`、`\(`→`$`，并自动补齐 `amsmath,amssymb` 宏包 |
| `.txt` | Unicode 数学符号 | `∫₀^∞ e^(-x²) dx`、`x ∈ R^d` 之类纯文本形式，多行环境按行展开 |

详细的识别规则、宏观包支持范围与降级链见 `references/detection-rules.md`；各格式的排版细节与已知限制见 `references/formats.md`。

## 环境准备

脚本依赖：`python-docx lxml pymupdf matplotlib reportlab latex2mathml`。首次使用前检查，缺什么装什么（装进隔离 venv，不要污染全局）：

```bash
"<PY>" -c "import docx, lxml, fitz, matplotlib, reportlab, latex2mathml" 2>&1
"<PY>" -m pip install -q python-docx lxml pymupdf matplotlib reportlab latex2mathml
```

`<PY>` 取当前会话可用的 Python（优先 managed venv，如 `~/.workbuddy/binaries/python/envs/default/Scripts/python.exe`）。

DOCX 原生公式需要 `MML2OMML.XSL`（随 Microsoft Office 安装，脚本会自动在 `C:\Program Files\Microsoft Office\root\Office*\` 下查找，也可用环境变量 `MML2OMML_XSL` 指定）。找不到时脚本会报告 `omml_available: false`，此时应告知用户安装 Office 或改用 PDF/MD 输出。

## 失败与降级

- 单条公式渲染失败不会中断流程：该处保留原 LaTeX 文本，计入 `failed`，并在 `failures` 列出。
- 位图渲染降级链：结构化布局（align/cases/matrix）→ mathtext → 净化后的 mathtext → Unicode 文本图。**始终会产出图片**。
- 中英文混排公式（`\text{其中 }`）自动走混排渲染，中文用系统 CJK 字体。
- 遇到脚本报错先看 traceback 的最后一层：多为依赖缺失或输入文件损坏，先修复再重跑，不要绕过脚本手工改文件。

## 不要做的事

- 不要在有 `format_formulas.py` 的情况下另写一次性转换脚本。
- 不要修改用户原始文件；一律写到新路径。
- 不要为了"看起来更整齐"而重排 PDF 正文——PDF 只做原位公式替换。
- 不要在未运行脚本的情况下凭猜测汇报转换数量。
