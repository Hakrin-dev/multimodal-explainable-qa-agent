# 契约四：摄入中间表示（Ingestion IR）v0.2 ✅ FROZEN（2026-09-26 · B 主责，A 代拟，B 评审细化）

> 权威实现：`backend/app/ingestion/ir.py`（dataclass 即 Schema）
> 消费方：RAG 检索（chunk）、Trace/前端溯源（page/breadcrumb）、公式计算引擎（FormulaIR）

## 1. 定位

摄入流水线各阶段间的**唯一交接物**。解析器（PyMuPDF/MinerU/PaddleOCR）只负责产出
`DocIR`，后续切片、索引、公式登记、质量报告全部基于 IR，互不感知解析细节。

```
raw file ──► 质量评估(#9) ──► 复杂度评分(#8) ──► 解析路由 ──► DocIR.blocks
                                                                │
                              FormulaIR ◄── 公式自动抽取与登记(#6, W5) ◄───┤
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
| FormulaIR | `kb_formula`（按 doc_id 原子替换；抽取失败或存在拒绝项时保留旧公式） |

**维度陷阱**：`kb_chunk.embedding` 的 vector 维度绑定首次摄入的模型
（当前 bge-small-zh=512；换 BGE-M3=1024 必须 drop + `--force` 重摄入——store 已内置
显式检测与报错指引）。

## 4. 已实现 vs B 接手

| 能力 | 状态 | 位置 |
|---|---|---|
| PyMuPDF 直抽 + 字号统计标题识别（#7 简化版地基） | ✅ v0 | `pdf_ingest.py` |
| 层级感知切片 + 面包屑 + 硬换行拼接 | ✅ v0 | `chunker.py` |
| 文本层检测（#9 第一个检测器） | ✅ | `pdf_ingest.has_text_layer` |
| 质量评估（方向/倾斜/清晰度/文本层/繁简） | ✅ rules-v1；扫描件视觉方向与 OCR 级倾斜待后续增强 | `quality.py`；`rag.pipeline.ingest_document` |
| 复杂度评分 1-5 + 解析路由（#8 轻量） | ✅ 规则评分、路由选择与 MinerU 执行已接入 | `complexity.py`；`rag.pipeline.ingest_document` |
| MinerU 4.x Basic 扫描件解析 | ✅ Middle JSON v2 → DocIR；PaddleOCR 兜底仍待接入 | `parsers/mineru.py`；`rag.pipeline.ingest_document` |
| 目录信号融合 + LLM 层级判定（#7 完整版） | ⬜ 决赛 | `pdf_ingest` 扩展 |
| 公式登记（#6） | ✅ W5 自动化：候选块筛选 → LLM 抽取 → FormulaEngine 校验 → 与文档/切片同事务写入；默认关闭，失败不覆盖已有公式 | `ingestion/formula_extract.py`；`core/prompts/ingestion.py`；`formula/store.py`；`rag/store.py` |
| 坏文档生成器与修复闭环（3 份） | ✅ 旋转、模糊繁体和缺失层级三类资产均已覆盖；原件与修复件隔离保存 | `scripts/gen_bad_docs.py`；`tests/test_bad_docs.py`；`tests/test_doc_api.py` |
| 正常知识库语料 | ✅ W5 扩展到 10 份 PDF、116 个 ChunkIR；新增 6 例 Top-1 严格检索 6/6 | `scripts/kb_doc_content.py`；`scripts/kb_doc_content_w5.py`；`tests/test_kb_corpus.py` |

## 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| 0.1 | 2026-09-22 | A 代拟初稿（数据结构已随 RAG 最小闭环落地验证） |
| **0.2** | **2026-09-26** | **冻结**：B 评审细化（字段约束/序列化边界/治理规则）合入；实现 `ingestion/ir.py` 与文档一致（标题识别 4/4 文档 100% 命中） | **✅ FROZEN（A/B 签字）** |
| 0.3 | 2026-10-03 | W5 实现更新：在不改变冻结 IR Schema 的前提下接入 FormulaIR 自动抽取、双通道表达式校验及 `kb_formula` 原子替换；失败和部分拒绝均不覆盖已有公式 | ✅ 已实现 |
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
  MinerU 4.x Basic 已接入：复杂度路由选择 `mineru` 后，通过独立环境中的
  `mineru-kit parse --format middle_json` 执行无状态解析。命令失败、超时、
  输出缺失或 Schema 不兼容时抛出 `MinerUError`，并在 embedding 与数据库写入前终止，
  因此不会覆盖已有索引。
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
PDF，并验证当前十份正常知识库 PDF。真实 PG 测试使用唯一测试文档 ID，结束后仅清理该 ID。
MinerU 4.x Basic 与 Standard GPU 路径均已完成适配；旋转扫描、模糊繁体和缺失层级三类坏文档均已完成修复、解析与切片闭环。
PaddleOCR CPU 兜底与 MinerU 容器内服务化仍待后续交付。

增量登记（2026-09-23，B 实现并完成回归验证）：新增可选 `meta.complexity_details`，
实现已有 `meta.complexity`；所有冻结字段与语义保持不变。


## W2 B 增量：文档质量评估（rules-v1）

本增量完成 #9 轻量质量检测，并接入统一摄入入口。检测结果写入已有
`DocIR.meta.quality` 和 `kb_doc.meta` JSONB，不修改 BlockIR、ChunkIR、
Citation、数据库表结构或 `content_hash()` 语义。

检测项：

- `orientation`：读取 PDF 页面旋转元数据，文档级字段取主方向；
  `rotated_pages` 保留所有非零旋转页，页码为 1-based。
- `skew`：根据原生文本行方向计算相对最近直角的偏差，文档级字段取最大值；
  大于 1° 时产生 `skew_detected`。
- `clarity`：以 1.5 倍灰度渲染页面，使用 Laplacian 方差衡量边缘清晰度，
  平滑归一化到 0～1；文档平均值低于 0.35 时产生 `low_clarity`。
- `has_text_layer`：保持既有语义，只要任一页存在原生文本即为 `true`；
  `textless_pages` 单独记录所有无文本页。
- `traditional_chars`：采用保守繁体专属字符集，仅作为风险提示；
  简繁共用字符（例如“案”“描”）不得进入字符表。
- `score`：综合文本页覆盖率、旋转页、倾斜度和清晰度，截断到 0～1。
  繁体文本本身不降低质量分，只产生 `traditional_text` 提示。

新增可选元数据形状：

```json
{
  "quality": {
    "version": "rules-v1",
    "orientation": 0,
    "skew": 0.0,
    "clarity": 0.9707,
    "has_text_layer": true,
    "traditional_chars": false,
    "textless_pages": [],
    "rotated_pages": [],
    "page_reports": [
      {
        "page": 1,
        "orientation": 0,
        "skew": 0.0,
        "clarity": 0.9707,
        "has_text_layer": true,
        "traditional_chars": false
      }
    ],
    "warnings": [],
    "score": 0.9912
  }
}
```

能力边界：

- 当前方向检测依赖 PDF rotation metadata，不推断缺少方向元数据的纯扫描图片。
- 当前倾斜检测依赖原生文本行方向；纯扫描件需要后续 OCR/图像级检测。
- 清晰度是用于路由和告警的启发式指标，不代表 OCR 字符准确率。
- 繁简检测是保守风险信号，不承担全文简繁转换。
- 损坏或加密 PDF 明确报错，不覆盖或删除已有索引。

内容 hash 未变化时，摄入流程仅合并刷新 `quality`、`complexity` 和
`complexity_details`，保留原有 chunk ID 与 embedding，不重新向量化。

独立复现：

```bash
cd backend
.venv/bin/python -m pytest tests/test_quality.py -q
```

验收证据（2026-09-27）：

- 质量检测专项测试：9/9；
- 全量测试：130 passed；
- 现有四份 PDF 均无旋转、无倾斜、具备文本层且无繁体误报；
- 幂等摄入：4 docs、38 chunks、0 new chunks；
- 数据库：38/38 chunks 保留 embedding；
- RAG 评测：retrieval hit@6 = 100%；
- RAG 冒烟检索：8/8。


## W2 B 增量：三类坏文档测试资产

新增三份与正常知识库隔离的固定测试资产：

- `bad_missing_structure.pdf`：保留原生文本，但标题与正文使用同一字号，
  不产生可靠 H1～H4，用于 #7 目录结构恢复；
- `bad_rotated_scan.pdf`：图像型扫描页并带 90° PDF rotation metadata，
  用于方向修复与 OCR 路由；
- `bad_blurred_traditional.pdf`：低分辨率放大并模糊的繁体图像型扫描页，
  用于清晰度、OCR 和繁简修复。

资产存放于 `data/docs_bad/`，不会被正常的 `scripts/ingest_docs.py` 自动摄入。
三类缺陷资产均已完成生成与检测。其中旋转扫描件已完成 MinerU 解析、入库和检索闭环；
模糊繁体扫描件的正文恢复、目录结构缺失文档的层级恢复仍待增强。

验收结果（2026-09-27）：

- 坏文档专项测试：5/5；
- 相关测试：49 passed；
- 全量测试：135 passed；
- 旋转扫描和模糊繁体扫描均为复杂度 5，选择 `mineru`；
- 目录结构缺失文档为复杂度 1，选择 `pymupdf`，但不产生可靠章节层级；
- 模糊繁体样本在 OCR 前不伪造繁体识别结果。

## W2 B 增量：MinerU 4.x 扫描件解析

本增量完成复杂 PDF/扫描件从路由选择到检索入库的实际执行闭环。

- MinerU 使用独立 Python 3.12 环境，当前验证版本为 `4.0.7`；
- 默认使用 Basic tier、ONNX small backend 和 CPU 表格模型；
- 后端通过 `MINERU_BIN` 调用无状态 `mineru-kit parse`，不污染主项目虚拟环境；
- 解析产物固定使用 `docvortex.middle` v2 Middle JSON；
- MinerU 的 `page_idx` 从 0-based 转换为 IR 的 1-based；
- 归一化 bbox 根据 `width_pt`、`height_pt` 转换为 PDF point；
- 旋转页面通过 PyMuPDF `derotation_matrix` 转回冻结的未旋转坐标语义；
- 页眉、页脚不进入 BlockIR 和检索文本；
- `title`、`text`、`table`、`formula`、`figure` 分别映射到冻结的 BlockIR 类型；
- MinerU 版本、tier、parse mode 和 Schema 信息保存在 `DocIR.meta.mineru`；
- 内容不变时保留原 chunk 和 embedding，不重复向量化；
- 命令失败或超时时，在模型加载与数据库写入前停止，保留已有索引。

运行时环境变量：

```bash
export MINERU_BIN=/path/to/mqa-mineru/bin/mineru-kit
export MINERU_HOME=/path/to/mineru-cache
export MINERU_MODEL_SOURCE=modelscope
export MINERU_TABLE_DEVICE=cpu
export MINERU_TIER=basic
export MINERU_TIMEOUT_SECONDS=600
```

验收证据：

- MinerU adapter 单元测试：6 passed；
- 复杂度、坏文档与 adapter 联合测试：41 passed；
- 全量测试：141 passed，5 warnings，0 failure；
- 旋转扫描件：5 blocks、1 chunk、页码与 bbox 正确；
- 重复摄入：第二次新增 0 chunk，未重复生成 embedding；
- PostgreSQL：5 docs、39 chunks、39/39 embeddings；
- 扫描件检索：3/3 命中，全部为 Top-1；
- 原有 RAG 回归：8/8，retrieval hit@6=100%。

能力边界：

- Basic OCR 对重度模糊繁体扫描件仅恢复部分文本，仍需增强预处理或 Standard tier；
- 无视觉层级信号的原生 PDF 仍需标题层级恢复模块；
- PaddleOCR CPU 兜底尚未接入；
- 当前 MinerU 通过宿主机独立环境执行，容器内在线摄入仍需后续服务化。
