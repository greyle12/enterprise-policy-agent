# Phase 22：Document Loader 抽象

Phase 22 在既有制度 RAG 之前增加统一的文档加载边界。Phase 23/24 注册 PDF 原生文本与 DOCX
段落/表格 Loader，Phase 25 在这两个 Loader 内加入显式 OCR Provider fallback 和质量门禁。

## 1. 它解决什么问题

原来的 `policy_parser.py` 同时负责读取 UTF-8 Markdown、检查扩展名、拆分 YAML front
matter 和校验制度元数据。如果直接在里面继续添加 PDF、DOCX 和 OCR 分支，这个模块会同时
承担二进制解析、格式路由、OCR 决策和业务 Schema 校验，测试与错误处理都会逐渐耦合。

新的职责边界是：

```text
Source file
→ DocumentLoaderRegistry
→ format-specific DocumentLoader
→ LoadedDocument (normalized text)
→ Policy Parser (front matter + metadata)
→ Policy Chunker
→ Embedding / Retrieval
```

Document Loader 只解决“文件如何变成文本”。它不解释制度编号、权限、版本、章节和条款，也
不执行 Embedding 或检索。

## 2. 输入、输出与位置

| 组件 | 输入 | 输出 | 所在位置 |
|---|---|---|---|
| `DocumentLoaderRegistry` | 文件路径 | 与扩展名匹配的 Loader | RAG ingestion 最前端 |
| `MarkdownDocumentLoader` | `.md` 路径 | UTF-8 规范化文本 | 格式提取层 |
| `LoadedDocument` | Loader 的提取结果 | 路径、文本、media type、loader name | Loader 与 Parser 的稳定契约 |
| `Policy Parser` | `LoadedDocument.text` | `PolicyDocument` | 业务结构解析层 |
| `Policy Chunker` | `PolicyDocument` | `PolicyChunk[]` | 索引前处理层 |

`LoadedDocument.source_path` 必须与请求路径一致，避免 Loader 意外把另一份文件的内容绑定到
错误引用。空文本也会在加载边界立即失败，不允许进入元数据解析和索引。

## 3. 架构设计

### 3.1 Loader Protocol

`DocumentLoader` 是结构化协议。一个实现需要声明：

- 稳定的 `name`；
- 输出 `media_type`；
- 拥有的文件扩展名；
- `load(path) -> LoadedDocument`。

协议使用依赖倒置：Parser 依赖最小契约，不直接依赖 PyMuPDF、python-docx 或 OCR SDK。
Phase 23/24 已通过新增 `PDFDocumentLoader`、`DOCXDocumentLoader` 验证无需复制 Parser、
Chunker、Retriever 或安全代码。

### 3.2 不可变 Registry

`DocumentLoaderRegistry` 在构造时建立扩展名到 Loader 的只读映射，并拒绝两个 Loader 同时
声明同一扩展名。这样不会出现 `.pdf` 到底由原生解析还是 OCR 解析的隐式覆盖。

Registry 还提供确定性目录发现：只选择已注册的普通文件，并按文件名排序。因此文档顺序、
Chunk ID 和离线测试结果保持稳定。

### 3.3 兼容现有 API

以下入口仍然保留，已有调用不需要修改：

```python
parse_policy_file(path)
parse_policy_directory(directory)
chunk_policy_file(path)
chunk_policy_directory(directory)
PolicyRetriever.from_directory(directory, embedding_provider=provider)
```

它们默认使用已注册 Markdown、PDF、DOCX 的 Registry。测试或部署需要限制格式时，可通过
关键字参数传入自定义 Registry；原有入口保持不变。

## 4. 安全模型为什么没有变化

Loader 属于离线/管理侧 ingestion，不参与用户身份判断。它只提供候选文本，现有运行时顺序
保持为：

```text
Trusted PolicyAccessContext
→ authorized_chunk_ids
→ allowed_record_ids
→ vector similarity
→ evidence prompt-injection scan
→ Context Builder
→ LLM
```

因此本阶段不会把权限过滤移动到检索之后。未来接入 pgvector 时，允许范围必须转成 SQL
过滤条件，在相似度排序之前执行。Loader 产出的文本也不能被视为可信指令；召回后仍会经过
证据污染检测。

## 5. 为什么选择这个方案

可选方案包括：

1. 在 `parse_policy_file` 中按扩展名写 `if/elif`：代码少，但每种格式都会扩大业务 Parser，
   OCR fallback 也很难单测。
2. 直接采用框架内置 Loader：接入快，但会把框架特有 Document 类型传播到现有 Pydantic
   Schema，并增加当前阶段不需要的依赖。
3. 使用 Registry + 最小 Protocol：多一个小抽象，但保持现有 RAG 数据模型稳定，也方便用
   离线 Fixture Loader 测试整个 Parser → Chunker → Retriever 路径。

当前项目选择第三种。它满足增量开发，并只为已完成的 PDF/DOCX 能力引入 PyMuPDF 和
python-docx。

## 6. 错误边界

加载层区分：路径不存在、路径不是文件、格式未注册、解码失败、空提取和一般读取失败。
`policy_parser.py` 将这些基础错误转换成原有 `PolicyParseError`，因此 API 上层仍只需要处理统一
的制度解析错误。

当前 Markdown Loader 使用 `utf-8-sig`：普通 UTF-8 和带 BOM 的 UTF-8 都能读取，其他编码
明确失败，而不是静默产生乱码。

## 7. 测试与离线验收

单元测试覆盖：

- BOM 处理和格式无关输出；
- 扩展名大小写路由；
- 目录确定性发现；
- 未注册格式与重复扩展名拒绝；
- 空文本拒绝；
- 自定义 Fixture Loader 贯穿 Parser、Chunker 和 Retriever；
- 原有 5 份 Markdown 制度仍解析为 5 份文档、199 个 Chunk。

PowerShell 验证：

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -X utf8 -m scripts.verify_document_loader
python -X utf8 -m scripts.verify_rag_security
python -X utf8 -m scripts.verify_ci_configuration
```

专项脚本正确时应返回：

```json
{
  "phase": 22,
  "passed": true,
  "supported_extensions": [".docx", ".md", ".pdf"],
  "document_count": 5,
  "chunk_count": 199,
  "network_calls": false,
  "model_calls": false
}
```

## 8. 生产环境仍有哪些不足

- 当前只支持顶层目录，不递归扫描，也没有对象存储 URI；
- 未限制文件字节数、页数、压缩比或提取耗时；
- 没有恶意文件扫描、沙箱进程和文档级审计记录；
- PDF 已保留页码，但没有表格和复杂版面结构信息；DOCX 已保留顶层块序号，但不伪造页码；
- OCR 已保留 Provider 置信度，但没有真实扫描集标定、语言检测或人工复核队列；
- 文档元数据仍依赖 YAML front matter；非 Markdown 格式如何携带可信元数据将在对应 Phase
  显式设计。

## 9. 面试官可能追问

### 为什么 Loader 和 Parser 要分开？

Loader 处理文件格式与字节提取，Parser 处理企业制度语义和 Schema。两者变化原因不同，拆开
后新增格式不需要改制度业务规则，也能分别测试乱码、空提取和元数据错误。

### 为什么不直接让每个 Loader 返回 `PolicyDocument`？

那会让 PDF、DOCX、OCR 各自复制制度元数据校验，容易产生格式间行为差异。统一先输出文本，
再进入同一个 Policy Parser，能保持版本、权限和引用字段一致。

### Registry 如何避免扩展冲突？

构造时规范化扩展名，并拒绝重复所有权；运行时映射不可变，不允许后注册的 Loader 静默覆盖。

### PDF 没有 YAML front matter 怎么办？

Phase 23 选择同名 sidecar YAML：`policy.pdf` 对应 `policy.metadata.yaml`。它必须由受控 ingestion
流程写入；不能让 LLM 从正文猜权限字段。

DOCX 使用相同的受信任 sidecar 规则：`policy.docx` 对应 `policy.metadata.yaml`。

### 这个改动怎样保证授权过滤仍在 Hybrid Search 前？

Loader 只改变索引输入；`AccessControlledPolicyRetriever` 仍先计算授权 Chunk ID，再将
`allowed_record_ids` 同时交给 Vector 与 BM25。RRF 只能融合两路已经授权的排名。
