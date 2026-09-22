# 契约四：摄入中间表示（Ingestion IR）v0.2（W1 末冻结 · B 主责，A 代拟初稿，B 已评审）

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
| 复杂度评分 1-5 + 解析路由（#8 轻量） | ⬜ W2 | 建议加 `complexity.py` |
| MinerU / PaddleOCR 接入（扫描件路径） | ⬜ W2 | `parsers/mineru.py` 等 |
| 目录信号融合 + LLM 层级判定（#7 完整版） | ⬜ 决赛 | `pdf_ingest` 扩展 |
| 公式登记（#6） | ⬜ W2 | LLM 从 blocks 抽 LaTeX→FormulaIR |
| 坏文档生成器（3 份，W2 演示用） | ⬜ W2 | `scripts/gen_bad_docs.py` |

## 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| 0.1 | 2026-09-22 | A 代拟初稿（数据结构已随 RAG 最小闭环落地验证）；B 评审后冻结 |
| 0.2 | 2026-09-22 | B（sxy）完成实现对照评审；明确页码、level、bbox、breadcrumb 与 content_hash 语义；评审通过并冻结 |