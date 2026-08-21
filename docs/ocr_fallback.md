# Advanced RAG Phase 25：OCR fallback

Phase 25 消费 PDF/DOCX Loader 已有的 `OCRRequiredError`，增加显式 OCR Provider、质量门禁、
本机 Tesseract 适配器以及 OCR 来源追踪。本阶段不建立第二套 Parser、Chunker 或 Retriever。

## 1. 它解决什么问题

扫描 PDF 没有可搜索文本层，图片型 DOCX 也可能只有制度截图。直接索引会静默漏文档；无条件 OCR
又可能把低置信度乱码变成正式制度证据。

```text
Native extraction
→ text sufficient? → existing Parser
→ text insufficient
→ configured OCR Provider?
   → no: OCRRequiredError
   → yes: raster/image → OCR → quality gate
      → pass: existing Parser
      → fail: OCRQualityError, never indexed
```

## 2. 新增模块的输入和输出

### OCRImage

输入单元包含图片字节、图片 media type、原始文件路径、容器格式，以及来源单元：

- PDF 使用 `unit_kind=page` 和真实页码；
- DOCX 使用 `unit_kind=block` 和顶层正文块序号。

### OCRProvider

最小接口为：

```python
recognize(image: OCRImage) -> OCRResult
```

Provider 不解析 PolicyMetadata，也不参与权限判断。

### OCRResult

输出包含：

- `text`：识别文本；
- `confidence`：0 到 1；
- `engine`：稳定引擎名；
- `language`：识别语言配置。

### OCRQualityGate

当前使用两个硬门槛：

```text
non-whitespace characters >= 20
confidence >= 0.80
```

任一失败都抛出 `OCRQualityError`，不进入 Policy Parser、Embedding 或索引。

## 3. PDF fallback

`PDFDocumentLoader` 逐页检查原生文本。低于阈值时：

1. 未配置 Provider：抛出 `OCRRequiredError`；
2. 已配置 Provider：使用 PyMuPDF `Page.get_pixmap(dpi=200, alpha=False)` 渲染 PNG；
3. Provider 识别并通过质量门禁；
4. OCR 文本继续使用已有章/条 Markdown 规范化；
5. 行页码保持为原始 PDF 页码。

默认最多 OCR 20 页，DPI 只允许 72–600，防止意外高分辨率渲染和无界 OCR 工作量。

## 4. DOCX fallback

`DOCXDocumentLoader` 只对“包含内嵌图片且没有原生段落文字”的顶层 Paragraph 执行 OCR，避免
对带说明文字的普通图片无条件调用 OCR。识别结果映射回该 Paragraph 的 block number。

默认最多处理 50 张图片。表格和原生段落仍走 Phase 24 路径，不改变行为。

python-docx 的公开 API 能确认 inline shape 是位于文本 run 中的图片对象，但当前版本没有完整的
公开图片读取 API；实现通过 OOXML relationship 读取图片 part。该部分被限制在 Loader 内，未来
python-docx API 变化时不会影响 Policy Schema。

## 5. Tesseract 适配器

`TesseractOCRProvider` 是本机同步 Provider，使用 pytesseract 的 `image_to_data()`，读取单词、行号
和 confidence，再确定性重建行文本。它具备：

- OCR 调用超时；
- 最大图片字节数；
- 最大解码像素数；
- 可配置 Tesseract 命令路径；
- 默认 `chi_sim+eng`；
- 可配置 Page Segmentation Mode。

安装 Python 适配器：

```powershell
python -m pip install -e ".[dev,ocr]"
```

这不会安装 Tesseract 可执行文件。Windows 仍需单独安装 Tesseract、简体中文 traineddata，并确保
命令在 PATH 中或显式传入 `command`。

默认 Registry 不配置 Provider，因此安装 Tesseract 不会让所有上传自动执行 OCR。管理侧
Indexing Pipeline 必须显式构造 Loader，例如：

```python
provider = TesseractOCRProvider(
    language="chi_sim+eng",
    command=r"C:\Program Files\Tesseract-OCR\tesseract.exe",
)
loader = PDFDocumentLoader(ocr_provider=provider)
```

## 6. OCR provenance

通过质量门禁的内容保留：

```text
source_ocr_engine
source_ocr_unit_kind
source_ocr_unit_numbers
source_ocr_confidence_min
```

这些字段贯穿 `LoadedDocument → PolicyDocument → PolicyChunk → Retriever metadata → Context →
PolicyCitation → Research API`。回答客户端可以标注“此证据来自 OCR”，审计系统也能定位原页/块。

## 7. 安全模型

OCR 结果是不可信内容，不是系统指令，也不是授权元数据。权限仍来自受控 sidecar：

```text
Trusted sidecar metadata
→ authorized_chunk_ids
→ allowed_record_ids
→ vector similarity
→ OCR/native evidence Prompt Guard
→ Context Builder
```

专项验证同时断言：无权限内容在评分前排除，包含提示注入的 OCR Chunk 在进入 LLM 前隔离。

## 8. 为什么选择 Provider + Tesseract

### 当前选择

Provider Protocol 把 Loader 与具体 OCR 引擎解耦；离线测试不需要系统二进制，生产可以选本机
Tesseract。Tesseract 支持中文语言包、完全离线，适合制度数据不能发往公网的场景。

### 替代方案

- PaddleOCR：中文和版面能力更强，但模型、推理依赖和资源占用更大；
- RapidOCR：部署相对轻，但仍需要模型与实际扫描集评测；
- 云 OCR：表格/票据能力强，但涉及数据出境、密钥、成本与供应商合规；
- PyMuPDF 内置 OCR：接口简单，但仍依赖本机 Tesseract，且更难替换 Provider/测试质量策略。

当前阶段优先建立正确的接口、安全顺序和质量边界；引擎准确率必须由真实数据评测决定。

## 9. 测试和验证

CI 使用固定离线 OCR Provider，动态生成空文本 PDF 和图片型 DOCX，不调用网络、模型或系统
Tesseract，覆盖：

- PDF 页渲染与 OCR；
- DOCX 图片提取与 block mapping；
- Parser/Chunker 复用；
- Chunk/Citation OCR provenance；
- 未配置 Provider 的显式 handoff；
- 低置信度和文本过短拒绝；
- authorization-before-similarity；
- OCR 污染证据隔离；
- CI 契约。

PowerShell：

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -X utf8 -m scripts.verify_ocr_fallback
python -X utf8 -m scripts.verify_docx_document_parsing
python -X utf8 -m scripts.verify_pdf_document_parsing
python -X utf8 -m scripts.verify_rag_security
python -X utf8 -m scripts.verify_ci_configuration
```

专项报告应显示 `phase=25`、`passed=true`、PDF/DOCX 各 1 个 OCR 单元、最低置信度 0.80，并明确
`external_ocr_processes=0`、`network_calls=false`、`model_calls=false`。

## 10. 生产环境不足

- 当前 OCR 在同步 Loader 内执行，尚无异步任务队列和状态机；
- 没有真实中文企业制度扫描集上的字符错误率、Recall@K 和回答评测；
- 置信度 0.80 是工程默认值，尚未按真实风险标定；
- 没有自动 deskew、去噪、方向检测和版面/表格恢复；
- DOCX 图片关系读取使用受限的 OOXML 内部接口；
- 没有人工复核队列、二次 OCR 或低质量隔离存储；
- OCR 进程尚未放进独立沙箱或资源配额容器；
- 未绑定文档哈希、OCR 引擎版本、语言包版本和审计事件。

## 11. 面试官可能追问

### 为什么 OCR 结果不能直接索引？

OCR 错字会改变金额、日期、否定词和权限描述。质量门禁至少能阻止明显低质量结果；高风险制度还
需要人工复核和真实集标定。

### 为什么置信度不能代表绝对正确？

它是引擎内部估计，不同字体、语言、版面和引擎之间不可直接等价。必须结合字符错误率、检索指标
和业务风险选择阈值。

### 为什么默认不启用 OCR？

OCR 是高 CPU、外部二进制和语言包相关能力。隐式启用会造成不可预测延迟、部署失败与文本污染，
因此由管理侧索引流程显式配置。

### OCR 后怎样保证权限安全？

OCR 只产生正文；部门、角色、密级和生效状态仍来自受控 sidecar。检索时仍先过滤授权 ID，再执行
相似度计算，OCR 内容召回后还要经过 Prompt Guard。
