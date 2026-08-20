# Advanced RAG Phase 24：DOCX 文档解析

Phase 24 在现有 `DocumentLoaderRegistry` 上注册 `DOCXDocumentLoader`。它读取 Word 正文中的
段落、标题样式和表格，再交给同一个 Policy Parser 与 Policy Chunker；Phase 25 可显式注入 OCR
Provider 识别无原生文字的图片段落。

## 1. 本阶段解决什么问题

`.docx` 是 Office Open XML 压缩包，不能当作 UTF-8 文本读取。与此同时，Word 文档中的段落和
表格可以交错出现，只分别读取 `document.paragraphs` 和 `document.tables` 会丢失二者的原始顺序。

当前数据流是：

```text
policy.docx + policy.metadata.yaml
→ DOCXDocumentLoader
→ LoadedDocument(text + block mapping + trusted metadata)
→ existing Policy Parser
→ existing Policy Chunker
→ Embedding / Retrieval
→ Context + block citation
```

## 2. 输入、输出和 Pipeline 位置

### 输入

DOCX 必须有同目录、同主文件名的可信 sidecar：

```text
travel_policy.docx
travel_policy.metadata.yaml
```

Sidecar 沿用 `PolicyMetadata`，保存制度编号、版本、状态、部门、角色、密级、区域和生效日期。
这些授权输入不能从正文、Word core properties 或 LLM 推断。

### 输出

`DOCXDocumentLoader` 返回：

- 规范化的 Markdown 文本；
- DOCX media type 和 `loader_name=python-docx`；
- 受信任的 sidecar 内容与路径；
- `block_count`；
- 与输出每一行对齐的 `line_block_numbers`。

它位于 ingestion 最前端，只负责“DOCX 如何变成可追溯文本”，不负责权限决策、Embedding 或回答。

## 3. 段落、标题和表格

实现使用 python-docx 1.2 的 `Document.iter_inner_content()`，按正文顺序产生顶层 `Paragraph`
或 `Table`。这样可以保留“段落 → 表格 → 下一段”的真实顺序。

标题有两层确定性规则：

1. 优先识别制度文本自身的“第某章/第某条”；
2. 其余内容按 Word `Title`、`Heading 1`、`Heading 2` 等样式转换为 Markdown 标题。

常见转换为：

```text
Title       → # 标题
Heading 1   → ## 章节
Heading 2   → ### 条款
```

表格按行转换为 Markdown table，单元格内换行转换为 `<br>`，竖线被转义。表格文本继续位于原条款
中，因此现有 Chunker、Embedding 和 Prompt Injection Guard 都能看到它。

## 4. 为什么使用块序号，不使用页码

DOCX 保存内容与排版指令，但最终页数会随 Word/LibreOffice 版本、字体、纸张和打印机设置变化。
python-docx 提供段落和表格对象，不提供稳定的渲染页码。因此本项目不伪造“第几页”，而保存：

```text
source_block_start
source_block_end
```

一个顶层段落或表格算一个 block。表格生成的多行文本都映射到同一个块序号。这个范围从
`LoadedDocument` 传到 `PolicyDocument`、`PolicyChunk`、Retriever metadata、`PolicyCitation`
和 Research API，足以支持审计与后续 Word 高亮定位。

替代方案是在服务器安装 LibreOffice/Word 渲染为 PDF 后再取页码，但会增加系统依赖、字体差异、
执行沙箱和性能成本，不适合本阶段。

## 5. 安全模型保持不变

DOCX 的表格、标题和正文都是不可信证据；sidecar 才是受控授权元数据。运行时仍然执行：

```text
Trusted identity
→ authorized_chunk_ids
→ allowed_record_ids
→ vector similarity
→ evidence prompt-injection scan
→ Context Builder
```

因此无权限 DOCX Chunk 在向量相似度之前被排除；召回内容中的恶意指令仍会在进入 LLM 前被隔离。

## 6. OCR 边界与错误类型

空正文或只有图片、原生字符少于阈值的 DOCX 在未配置 Provider 时抛出 `OCRRequiredError`。
Phase 25 可提取图片段落执行 OCR，并在 Parser/索引前拒绝低置信度文字。默认阈值为 20 个非空白字符。

| 错误 | 含义 |
|---|---|
| `DocumentMetadataError` | sidecar 缺失、为空或不是 UTF-8 |
| `InvalidDocumentError` | DOCX/OOXML 包损坏或提取失败 |
| `OCRRequiredError` | 原生文本不足且没有可用 Provider，或超出 OCR 图片上限 |
| `DocumentDependencyError` | 未安装 python-docx |

## 7. 为什么选择 python-docx

### 当前方案

python-docx 对 `.docx` 的段落、样式、表格和 OOXML 包提供成熟的纯 Python API，依赖范围可控，
并能通过 `iter_inner_content()` 保持顶层内容顺序。它与现有最小 Loader Protocol 对接，不会把
第三方 Document 类型传播到业务 Schema。

### 替代方案

- `mammoth`：适合 DOCX 到 HTML 的语义转换，但 HTML 不是当前 Parser 的稳定边界；
- `docx2txt`：简单提取方便，但结构、表格顺序和来源定位较弱；
- Unstructured/Docling：版面能力更强，但依赖和模型更重，会引入第二套文档对象；
- LibreOffice 转 PDF：能获得渲染页码，但需要外部进程、字体和沙箱治理。

## 8. 测试和验证

专项测试在临时目录动态生成真实 DOCX 和 sidecar，不提交二进制 fixture，不调用网络或模型，覆盖：

- 段落和表格的交错顺序；
- Title/Heading 样式；
- Markdown 表格序列化；
- Parser、Chunker、Context 和 Retriever 复用；
- Chunk/Citation 块范围；
- 受信任 sidecar；
- 空/图片型文档转交 OCR；
- 损坏 DOCX；
- CI 质量门禁。

PowerShell：

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -X utf8 -m scripts.verify_docx_document_parsing
python -X utf8 -m scripts.verify_pdf_document_parsing
python -X utf8 -m scripts.verify_document_loader
python -X utf8 -m scripts.verify_rag_security
python -X utf8 -m scripts.verify_ci_configuration
```

专项脚本正确时应显示 `phase: 24`、`passed: true`、1 份文档、7 个来源块、2 个 Chunk，并明确
`ocr_executed/network_calls/model_calls` 都为 `false`。

## 9. 生产环境不足

- 不读取页眉、页脚、批注、修订记录、文本框和脚注；
- 嵌套表格只通过单元格文本扁平化，合并单元格可能重复；
- 不解析图表、SmartArt、公式和嵌入对象；
- 不限制文件大小、解压大小、block 数、表格尺寸、解析时间和内存；
- 未在隔离进程进行恶意 OOXML/ZIP bomb 扫描；
- 样式映射是确定性启发式，不是通用 Word 语义模型；
- Sidecar 尚未和文档哈希、上传审批与审计记录绑定；
- OCR 异步任务、人工复核、索引状态机和失败重试队列尚未实现。

## 10. 面试官可能追问

### 为什么不把 DOCX 解析直接写进 Policy Parser？

DOCX 字节提取与制度元数据校验是两种变化原因。Loader 处理文件格式，Parser 继续统一校验制度
Schema，避免 Markdown/PDF/DOCX 三套业务规则漂移。

### 为什么表格要留在条款文本里？

制度的金额、材料、角色和时限经常在表格中。丢弃表格会直接降低召回与回答完整性；确定性
Markdown 序列化能被现有 Embedding、关键词检索和安全扫描共同使用。

### 块序号对用户有什么价值？

它是与 OOXML 正文顺序绑定的稳定定位，不受字体和分页变化影响。生产系统可用 block 范围打开
原文、高亮对应段落或关联审计记录。

### 如何保证 DOCX 没破坏 authorization-before-similarity？

Loader 只改变索引输入，检索仍先用 sidecar 生成的权限字段计算授权 Chunk ID，再把允许 ID 交给
向量索引评分。专项验证同时断言授权范围与检索结果。
