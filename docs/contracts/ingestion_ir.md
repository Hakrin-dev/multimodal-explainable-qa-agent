# 契约四：摄入中间表示（Ingestion IR）v0.2 ✅ FROZEN（2026-09-26 · B 主责，A 代拟，B 评审细化）

> 权威实现：`backend/app/ingestion/ir.py`（dataclass 即 Schema）
> 消费方：RAG 检索（chunk）、Trace/前端溯源（page/breadcrumb）、公式计算引擎（FormulaIR）

## 1. 定位

摄入流水线各阶段间的**唯一交接物**。解析器（PyMuPDF/MinerU/PaddleOCR）只负责产出
`DocIR`，后续切片、索引、公式登记、质量报告全部基于 IR，互不感知解析细节。

```
raw file ──► 质量评估(#9) ──► 复杂度评分(#8) ──► 解析路由 ──► DocIR.blocks
                                                                │
                              FormulaIR ◄── 公式登记(#6, W2) ◄───┤
                                                                ▼
                          kb_chunk(pgvector+元数据) ◄── 层级切片(ChunkIR)
```

## 2. 数据结构（与 ir.py 严格同步）

### BlockIR（原子版面块）

```json
{
  "id": "employee_handbook-b31",
  "page": 2,
  "type": "heading|paragraph|table|formula|figure",
  "text": "年假",
  "level": 2,
  "bbox": [56.7, 402.1, 120.3, 418.9],
  "font_size": 12.0,
  "meta": {}
}
```

字段约束：

- `page` 统一为 1-based；
- `type="heading"` 时，`level=0`  表示文档标题，`level=1~4` 表示章节层级；
- 非 heading 块的 `level=0` 只是默认值，消费方应忽略；
- PyMuPDF 正常解析出的 `bbox` 必须为 `[x0,y0,x1,y1]`，单位为 PDF point；
- 对于暂时无法提供版面坐标的解析器，`bbox` 允许为空数组，但不得伪造坐标。

### ChunkIR（检索单元）

```json
{
  "id": "employee_handbook-c13",
  "doc_id": "employee_handbook",
  "block_ids": ["...-b30", "...-b31", "...-b32"],
  "breadcrumb": ["Chinook 唱片员工手册", "假期制度", "年假"],
  "page_start": 2,
  "page_end": 2,
  "text": "年假\n入职满一年的员工每年享有 10 天年假；…"
}
```

约束：**不跨 H1 切片**；目标 256~512 字；H2 起新 chunk；块内硬换行拼接（见
`chunker._assemble_text`）。

`page_start` 和 `page_end` 均为 1-based。IR 与 PostgreSQL 中的 `breadcrumb`
保持 `list[str]` / `TEXT[]`；输出 Citation 时才序列化为
`"文档 > H1 > H2"` 形式的字符串。

### FormulaIR（公式资产，中级 #6 —— W2 实现登记，Schema 先冻结）

```json
{
  "id": "employee_handbook-f1",
  "doc_id": "employee_handbook",
  "page": 1,
  "breadcrumb": ["Chinook 唱片员工手册", "薪酬与提成", "销售提成计算办法"],
  "name": "销售提成",
  "latex": "0.03 * S + 0.02 * max(S - 100000, 0)",
  "params": {
    "S": {
      "desc": "当月个人销售额",
      "unit": "元",
      "source": "db|doc|user"
    }
  }
}
```

`params.source` 是 #6a 参数三通道的绑定依据（W4 公式引擎消费）。

### DocIR（文档级容器）

```json
{
  "doc_id": "employee_handbook",
  "name": "Chinook 唱片员工手册",
  "source_path": "data/docs_raw/employee_handbook.pdf",
  "pages": 2,
  "parser": "pymupdf|mineru|paddleocr",
  "blocks": [BlockIR],
  "chunks": [ChunkIR],
  "formulas": [FormulaIR],
  "meta": {
    "body_font_size": 10.5,
    "n_blocks": 39,
    "quality": {
      "orientation": 0,
      "skew": 0.4,
      "clarity": 0.9,
      "has_text_layer": true,
      "traditional_chars": false,
      "score": 0.92
    },
    "complexity": 2
  }
}
```

`content_hash` 不属于 `DocIR.meta`。它由 `DocIR.content_hash()` 根据 blocks
计算，并独立写入 `kb_doc.content_hash`，用于判断是否需要重新摄入。

## 3. 存储映射

| IR | 落库 |
|---|---|
| DocIR | `kb_doc`（doc_id 主键，meta JSONB，content_hash 独立列用于判重） |
| ChunkIR | `kb_chunk`（breadcrumb text[]，embedding vector(dim)） |
| FormulaIR | W2 建 `kb_formula`  表（Schema 已冻结，直接照抄 FormulaIR 字段） |

**维度陷阱**：`kb_chunk.embedding` 的 vector 维度绑定首次摄入的模型
（当前 bge-small-zh=512；换 BGE-M3=1024 必须 drop + `--force` 重摄入——store 已内置
显式检测与报错指引）。

## 4. 已实现 vs B 接手

| 能力 | 状态 | 位置 |
|---|---|---|
| PyMuPDF 直抽 + 字号统计标题识别（#7 简化版地基） | ✅ v0 | `pdf_ingest.py` |
| 层级感知切片 + 面包屑 + 硬换行拼接 | ✅ v0 | `chunker.py` |
| 文本层检测（#9 第一个检测器） | ✅ | `pdf_ingest.has_text_layer` |
| 质量评估其余检测器（方向/倾斜/清晰度/繁简） | ⬜ W2 | 建议加 `quality.py` |
| 复杂度评分 1-5 + 解析路由（#8 轻量） | ✅ 规则评分/路由选择；MinerU 执行待接入 | `complexity.py`；`rag.pipeline.ingest_document` |
| MinerU / PaddleOCR 接入（扫描件路径） | ⬜ W2 | `parsers/mineru.py` 等 |
| 目录信号融合 + LLM 层级判定（#7 完整版） | ⬜ 决赛 | `pdf_ingest` 扩展 |
| 公式登记（#6） | ⬜ W2 | LLM 从 blocks 抽 LaTeX→FormulaIR |
| 坏文档生成器（3 份，W2 演示用） | ⬜ W2 | `scripts/gen_bad_docs.py` |

## 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| 0.1 | 2026-09-22 | A 代拟初稿（数据结构已随 RAG 最小闭环落地验证） |
| **0.2** | **2026-09-26** | **冻结**：B 评审细化（字段约束/序列化边界/治理规则）合入；实现 `ingestion/ir.py` 与文档一致（标题识别 4/4 文档 100% 命中） | **✅ FROZEN（A/B 签字）** |
| 0.2 | 2026-09-22 | B（sxy）完成实现对照评审；明确页码、level、bbox、breadcrumb 与 content_hash 语义；评审通过并冻结 |

## W2 B 增量：复杂度评分与解析路由（rules-v1）

本增量完成 #8 轻量评分、路由选择和原生 PDF 摄入接入，不代表摄入 v1 整体完成。
不修改 BlockIR/ChunkIR、质量字段、Citation 或 `content_hash()` 语义。

- 基础分 1；任一页无非空文本 +3；检测到表格 +1；页数 >20 +1；
  图片占文档总页面面积 >30% +1；最终截断到 1–5。
- 对混合 PDF 逐页检查，无文本页含空白页，保守要求 OCR/人工复核，避免原生直抽漏页。
  这不改变原有 `has_text_layer()` 的“任一页有文本”语义。
- 表格采用 PyMuPDF 默认有线表格检测；不保证识别无框表格。
  图片面积采用页面可见区域内图片矩形并集，重叠不重复计数；按全部页总面积加权。
- 1–2 级选择 `pymupdf`；3 级选择 `mineru`；4–5 级选择 `mineru` 并告警
  `postprocessing_required`。`DocIR.parser` 仍表示实际执行的解析器。
- MinerU 尚未实现：摄入入口抛出 `ParserUnavailableError`（携带 assessment），
  在模型加载和数据库访问前停止，不伪造解析成功，不删除已有文档或索引。
  损坏、加密文件明确报错；本模块不负责质量修复。

已有 `meta.complexity` 保存整数等级。新增可选 `meta.complexity_details`：

```json
{
  "version": "rules-v1",
  "base_score": 1,
  "contributions": {
    "textless_page": 0,
    "table": 0,
    "over_20_pages": 0,
    "image_over_30_percent": 0
  },
  "textless_pages": [],
  "table_pages": [],
  "image_ratio": 0.0,
  "selected_parser": "pymupdf",
  "warnings": []
}
```

页列表为 1-based。评估信息通过现有 `kb_doc.meta` JSONB 落库，无 DDL 迁移。
内容 hash 相同时仍刷新这两个评估字段，保留其它 metadata、chunk ID 和 embedding；
不需要 `--force`，不改变以 blocks 为依据的 hash。写入时再次检查 hash。

独立复现（不需要 GPU/OCR）：

```bash
cd backend
.venv/bin/python -m pytest tests/test_complexity.py -q
```

测试在临时目录生成正常、表格、长文档、扫描、混合、旋转、重叠图片、空白、加密、损坏
PDF，并验证现有四份知识库 PDF。真实 PG 测试使用唯一测试文档 ID，结束后仅清理该 ID。
质量评估检测器、三类业务坏文档生成/修复、MinerU/PaddleOCR 适配仍待后续交付。

增量登记（2026-09-23，B 实现并完成回归验证）：新增可选 `meta.complexity_details`，
实现已有 `meta.complexity`；所有冻结字段与语义保持不变。
