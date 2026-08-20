# Advanced RAG Phase 23：PDF 文档解析

Phase 23 在 Phase 22 的 `DocumentLoader` Protocol 上增加 `PDFDocumentLoader`。本阶段只读取
PDF 原生文本层，不执行 OCR；没有可用文本层的扫描件会返回稳定的 `OCRRequiredError`，由
Phase 25 处理。

## 1. 本阶段解决什么问题

企业制度经常以 PDF 发布。把 PDF 当作普通 UTF-8 文本读取会失败，而直接把 PDF 专用逻辑写进
Policy Parser 又会破坏 Phase 22 建立的职责边界。

当前数据流是：

```text
policy.pdf + policy.metadata.yaml
→ PDFDocumentLoader
→ LoadedDocument(text + page mapping + trusted metadata)
→ Policy Parser
→ existing Policy Chunker
→ Embedding / Retrieval
→ Context + page citation
```

## 2. 输入与输出

### 输入

PDF 必须有同目录、同主文件名的可信 sidecar：

```text
travel_policy.pdf
travel_policy.metadata.yaml
```

Sidecar 使用现有 `PolicyMetadata` 字段，例如：

```yaml
document_id: TRAVEL_POLICY_PDF_001
document_type: policy
title: 差旅报销管理制度
version: "2.0"
status: effective
issuing_department: 财务部
effective_date: 2026-01-01
allowed_departments:
  - ALL
allowed_roles:
  - EMPLOYEE
security_level: internal
region: 中国大陆
```

权限、版本、状态和发布部门必须来自受控 ingestion sidecar，不能从 PDF 正文或 LLM 猜测。

### 输出

`PDFDocumentLoader` 返回：

- `text`：规范化文本；
- `media_type=application/pdf`；
- `loader_name=pymupdf`；
- `metadata_text` 和 `metadata_source_path`；
- `page_count`；
- 与文本每一行对齐的 `line_page_numbers`。

页码继续传递到 `PolicyDocument`、`PolicyChunk`、`PolicyCitation` 和研究 API。

## 3. 文本提取和结构规范化

实现使用 PyMuPDF：

```python
page.get_text("text", sort=True)
```

`sort=True` 尝试按页面坐标从左上到右下重排文本，比完全依赖 PDF 内部对象顺序更稳定。但它
不是通用版面理解模型，复杂双栏、跨页表格、浮动文本框仍可能需要更强的 layout parser。

为了复用现有制度 Chunker，Loader 对常见纯文本标题做确定性转换：

```text
第一章 总则        → ## 第一章 总则
第一条 适用范围    → ### 第一条 适用范围
```

如果“第某条”后面是明显的完整句子，则将条款编号和正文拆成两行，避免把整句正文误当作条款
标题。该规则是可测试的启发式，不宣称能处理所有 PDF 排版。

## 4. Sidecar 为什么是安全设计

PDF 标准 metadata 通常只有标题、作者、主题等字段，不能可靠表达本项目的部门、角色、密级、
生效日期和区域授权。更不能让 LLM 从正文自动生成这些字段，因为错误密级会直接扩大检索范围。

Sidecar 的使用前提是：它与 PDF 一起由受控上传或索引流程写入，普通聊天用户不能修改。未来
Phase 30 的 Document Indexing Pipeline 需要进一步增加上传者身份、内容哈希、审批状态和审计
记录。

## 5. OCR 边界

本阶段通过非空白字符阈值判断原生文本是否足够：

```text
native text < minimum_text_characters
→ OCRRequiredError
```

当前默认阈值为 20，可在构造 `PDFDocumentLoader` 时调整。该判断只负责把扫描件交给后续 OCR，
不会自动渲染页面或调用 OCR Provider，因此：

- 不产生隐藏网络请求；
- 不增加本阶段不可控的模型依赖；
- 不把 OCR 低置信度文字直接写入正式索引；
- Phase 25 可以独立测试 OCR 成功、失败和人工复核边界。

## 6. 错误类型

| 错误 | 含义 |
|---|---|
| `DocumentMetadataError` | sidecar 缺失、为空或不是 UTF-8 |
| `EncryptedDocumentError` | PDF 需要密码 |
| `InvalidDocumentError` | PDF 损坏、无页面或提取接口异常 |
| `OCRRequiredError` | 原生文本不足，需要 Phase 25 OCR |
| `DocumentDependencyError` | 未安装 PyMuPDF |

`parse_policy_file` 仍把 Loader 错误封装为统一的 `PolicyParseError`，同时保留异常 cause，应用层
无需为每种格式重写错误处理。

## 7. 页码引用

原来的 Markdown 制度保留行号引用，PDF 额外保留：

```text
source_page_start
source_page_end
```

Context 只在 PDF Chunk 有页码时写入这两个字段，因此原有 Markdown Prompt 和黄金评测输出保持
兼容。Research API 也返回页码，客户端可以展示“制度第 4 页”。

## 8. 安全顺序保持不变

PDF 只改变 ingestion 输入，不改变检索安全边界：

```text
Trusted metadata
→ PolicyChunk
→ authorized_chunk_ids
→ allowed_record_ids SQL/index scope
→ vector similarity
→ evidence prompt-injection scan
→ Context Builder
```

未授权 PDF Chunk 仍然在向量评分前排除。PDF 正文即使包含恶意指令，召回后仍会在进入 Prompt
前被证据污染检测隔离。

## 9. 测试和验证

专项测试动态生成真实中文 PDF，不依赖网络或仓库中的二进制测试文件，覆盖：

- 两页原生中文文本提取；
- 章/条标题规范化；
- sidecar 元数据；
- Parser → Chunker → Retriever；
- Chunk 和 Citation 页码；
- 扫描/空文本转交 OCR；
- 缺少 sidecar；
- 损坏 PDF；
- 密码保护 PDF；
- CI 质量门禁。

PowerShell：

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -X utf8 -m scripts.verify_pdf_document_parsing
python -X utf8 -m scripts.verify_document_loader
python -X utf8 -m scripts.verify_rag_security
python -X utf8 -m scripts.verify_ci_configuration
```

专项脚本正确时应显示：

```json
{
  "phase": 23,
  "passed": true,
  "parser": "pymupdf-native-text",
  "document_count": 1,
  "page_count": 2,
  "chunk_count": 2,
  "ocr_executed": false,
  "network_calls": false,
  "model_calls": false
}
```

## 10. 替代方案与取舍

### pypdf

依赖较轻，适合基本 PDF 操作；复杂读取顺序和版面信息需要更多自定义处理。

### pdfplumber

对字符、坐标和表格分析更友好，但当前阶段只需要稳定的原生文本和页级 provenance。

### Unstructured / Docling

能提供更丰富的版面与文档元素，但依赖和运行成本更高，也可能引入另一套 Document 模型。本项目
当前优先保持现有 Policy Parser/Chunker 数据模型稳定。

### 当前选择

PyMuPDF 提供简单的页级文本 API、稳定页码和离线执行能力，适合当前个人作品集规模。复杂 layout
可以在取得真实错误样本和评测数据后再引入，不先为了简历堆框架。

## 11. 生产环境不足

- 没有文件大小、页数、解析时间和内存预算；
- 没有 PDF 恶意内容扫描或隔离进程；
- 不处理数字签名、附件、批注和表单字段；
- 双栏和复杂阅读顺序可能错误；
- 表格没有转成结构化行列；
- 页眉页脚没有自动去重；
- Sidecar 尚未接上传审批、内容哈希绑定和审计；
- OCR 尚未实现；
- 尚无真实企业 PDF 检索评测集。

## 12. 面试官可能追问

### 为什么不能从 PDF 正文自动推断权限？

权限元数据是访问控制输入，不是普通内容。错误推断可能让敏感 Chunk 进入候选范围，因此必须来自
可信上传流程或受控 sidecar。

### 为什么扫描件不直接返回空文本？

空文本继续进入 Parser 会产生误导性的“文档格式错误”，也可能让索引静默漏文档。
`OCRRequiredError` 明确表达下一步动作，并便于指标和人工复核。

### `sort=True` 能完全解决阅读顺序吗？

不能。它是坐标排序，不是语义版面模型。复杂多栏、表格和浮动元素仍需 layout-aware parser，并应
通过真实评测集决定是否引入。

### 页码为什么从 Loader 一直传到 Citation？

如果只保留最终文本，检索结果无法回到原始 PDF。页码 provenance 能支持用户核验、审计和后续
高亮定位。

