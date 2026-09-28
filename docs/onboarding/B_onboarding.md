# B 角色上手文档（RAG / 文档智能）

> 写给你的人：A（2026-09-22）。你休假/上手期间我把你 W1 会阻塞团队的活干完了，
> 这份文档 = 你的代码导览 + 工作交接 + W2 任务书。**先通读一遍再动代码**，遇到与
> 文档不符的地方以代码为准并回来改文档。
>
> 阅读时间约 25 分钟，跟做约 40 分钟。

---

## 0. 你负责什么（PLAN §12）

| 模块 | 说明 | 周期 |
|---|---|---|
| **摄入流水线** | 质量评估 #9（完整）· 复杂度评分 #8（轻量）· 解析路由 · MinerU/PaddleOCR 接入 · 层级切片 | W2 主体 |
| **RAG 引擎** | 混合检索（已完成 v0）· rerank 精排 · 引用溯源 · 忠实度自检 | W2-W3 |
| **公式计算 #6** | 最小闭环（1-2 个公式演示）| W4 |
| **本地模型部署** | BGE-M3 + bge-reranker-v2-m3（GPU）· MinerU（GPU）| W3 前验证 |

**最重要的一句话**：你的所有产出最终都汇成两个东西——`kb_chunk` 表里的检索单元，
和 `docs/contracts/ingestion_ir.md` 里的中间表示。前端、编排、评测都只认这两个接口。

---

## 1. 十分钟跑起来（环境已在仓库里备好）

```bash
# 1) 启动全栈（PG + 后端 + 前端占位 + 数据导入 + 冒烟，幂等可重跑）
./deploy/quick_start.sh

# 2) 验证 RAG 端点（mock LLM 也能出答案结构）
curl -X POST http://localhost:8000/api/rag \
  -H 'Content-Type: application/json' \
  -d '{"question": "年假有几天？"}' | python3 -m json.tool | head -30

# 3) 跑 RAG 冒烟（真实检索 + mock 生成，不需要任何 API key）
cd backend && .venv/bin/python scripts/smoke_rag.py

# 4) 跑 RAG 评测
cd backend && .venv/bin/python eval/run_rag.py
```

宿主机开发环境（一次性）：

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU 版！见 §6.3
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q        # 测试应全绿（RAG 集成项会 SKIP，见下一行）

# 下载本地嵌入模型（不入 git；**2026-09-26 更新：A 已下载就位，可跳过此步**）
# 若需重建：HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 .venv/bin/python -c \
#   "from huggingface_hub import snapshot_download; import os; \
#    print(snapshot_download('BAAI/bge-small-zh-v1.5', local_dir=os.path.abspath('../models/bge-small-zh-v1.5')))"
# 注：hf-mirror 需禁用 Xet（HF_HUB_DISABLE_XET=1），否则 CDN 401
.venv/bin/python -m pytest tests/ -q        # 现在应 63 项全过（含 RAG 集成）
.venv/bin/python scripts/ingest_docs.py     # 知识库已由 A 摄入（4 文档 38 chunks），幂等可重跑
```

---

## 2. 代码地图（只有与你相关的部分）

```
backend/app/
├── rag/                      ← 你的主战场（已完成 v0）
│   ├── embedding.py          #   Embedding 抽象：local / siliconflow / mock 三 provider
│   ├── store.py              #   kb_doc / kb_chunk 表 + 向量读写（pgvector）
│   ├── retriever.py          #   混合检索：向量 + jieba-BM25 → RRF 融合；ChunkHit.citation()
│   └── pipeline.py           #   RAGPipeline.run() + ingest_document()；对接 Trace
├── ingestion/                ← 你的主战场（已完成 v0）
│   ├── ir.py                 #   ★ 契约：BlockIR / ChunkIR / FormulaIR / DocIR
│   ├── pdf_ingest.py         #   PyMuPDF 解析 + 字号统计标题识别（#7 地基）
│   └── chunker.py            #   层级感知切片 + 面包屑 + 硬换行拼接
├── core/
│   ├── llm.py                #   LLM 入口（A 写好，你只管调 get_llm_service().chat()）
│   ├── prompts/rag.py        #   ★ 你的 Prompt 静态层草案（rag.generate）
│   ├── tracing.py            #   Trace 收集器（rag_search 节点已对接）
│   └── config.py             #   所有配置项（EMBEDDING_* 是你的）
└── scripts/
    ├── kb_doc_content.py     #   ★ 3 份知识库文档的内容源（结构化 Python）
    ├── gen_kb_docs.py        #   内容 → PDF（reportlab + 文泉驿字体）
    ├── ingest_docs.py        #   data/docs_raw/*.pdf → PG（幂等）
    └── smoke_rag.py          #   检索召回检查 + 管道冒烟

backend/eval/
    ├── cases/rag_single_doc.jsonl   # 6 个 RAG 用例（§5.2）
    └── run_rag.py                   # 双指标 runner

docs/contracts/
    ├── ingestion_ir.md       # ★ 你的契约（待你评审冻结）
    └── trace.md              #   citations[] 字段已按你的实现定稿
```

★ = 你接手后要改/维护的核心文件。

---

## 3. 我替你完成了什么（每一件的设计意图）

### 3.1 知识库文档 v0（3 份）——`scripts/kb_doc_content.py`

三份文档是**结构化 Python 数据**（不是手写 PDF），`gen_kb_docs.py` 渲染成 PDF：

| 文档 | 内容设计意图 |
|---|---|
| 员工手册 | 数字/条款型事实 + **提成公式**（#6 的素材）+ 两级目录（#7 正样本）|
| 2025 销售总结 | 每位销售的方法论段落，**人名与 employee 表对齐**（W4 跨源多跳："查冠军+总结方法论"就靠它）|
| 客服 SOP | 条款型事实 + 禁语清单（评测用）|

**你要知道的**：改内容改 `kb_doc_content.py` 再跑 `gen_kb_docs.py`，不要直接改 PDF。
`tests/test_ingestion.py` 用这个模块当标题识别的 ground truth——你加文档时测试自动覆盖。

### 3.2 摄入流水线 v0

```
PDF ──PyMuPDF──► blocks（含字号/bbox/页码）
      ──字号统计──► 标题识别：body 中位数 ×1.30→H1，×1.10→H2，最大字号→level 0 文档标题
      ──chunker──► 面包屑切片：不跨 H1；H2 起新 chunk；256~512 字；硬换行智能拼接
      ──embedding──► kb_chunk(pgvector, 512d) + 元数据(breadcrumb/page/text)
```

关键设计（W2 扩展时别破坏）：
- **标题识别阈值是相对值**（字号中位数倍数），不是绝对值——不同 PDF 基础字号不同
- **硬换行拼接**（`_assemble_text`）：PDF 排版会把 "5 天" 拆成 "5\n天"，句内换行直接连接、句号/标题后换行——这是检索质量的地基，别退回简单 join
- **content_hash 幂等摄入**：重复摄入自动跳过（§11.3-C 增量缓存），`--force` 强制

### 3.3 混合检索 v0（已达标）

- 向量路：pgvector 存 512 维（bge-small-zh-v1.5，CPU），启动时载入内存算余弦
- 稀疏路：jieba 分词 + 手写 BM25（`retriever.py::_BM25Index`，k1=1.5 b=0.75）
- 融合：RRF（k=60），候选各取 top-20
- **当前指标：检索 recall@6 = 8/8（6 个评测问题 + 2 个冒烟问题，事实全部命中 top-6，且 8 个里 8 个 rank#1）**

### 3.4 RAG 管道与 Trace 对接

`RAGPipeline.run()` 产出与 `NL2SQLPipeline.run()` **同构**的结果对象（question/answer/
citations/status/trace/latency_ms）——W2 编排内核把两者当可互换工具调度，这是刻意的。
`rag_search` 是 `tool_call` 节点，`detail.citations[]` 字段已在 trace 契约定稿。

### 3.5 Embedding 抽象层

```bash
# .env 切换 provider，零代码改动：
EMBEDDING_PROVIDER=local           # 当前：bge-small-zh-v1.5 @ models/（CPU，够 W1-W2）
EMBEDDING_PROVIDER=siliconflow     # 备胎：BAAI/bge-m3 API（需 SILICONFLOW_API_KEY）
EMBEDDING_PROVIDER=mock            # 测试：确定性 hash 向量（64 维）
```

**换 BGE-M3 本地部署时**：下载模型到 `models/bge-m3`，改 `EMBEDDING_MODEL_PATH`，
然后 **必须** drop 表重摄入（维度 512→1024，store 会报错并给出指引——这是刻意的防呆）。

---

## 4. 命令速查

```bash
# 文档：改内容 → 重新生成 → 重摄入（三连，幂等）
cd backend
.venv/bin/python scripts/gen_kb_docs.py
.venv/bin/python scripts/ingest_docs.py            # 内容没变会自动跳过
.venv/bin/python scripts/ingest_docs.py --force    # 强制重摄入

# 检索质量回归（不需要 LLM key）
.venv/bin/python scripts/smoke_rag.py

# 评测
.venv/bin/python eval/run_rag.py                   # 6 用例，双指标
.venv/bin/python eval/run_nl2sql.py                # A 的 30 用例（顺手可以跑）

# 查库
docker exec mqa-db psql -U chinook -d chinook -c \
  "SELECT doc_id, name, pages FROM kb_doc;"
docker exec mqa-db psql -U chinook -d chinook -c \
  "SELECT chunk_id, breadcrumb, left(text,40) FROM kb_chunk LIMIT 5;"

# 测试
.venv/bin/python -m pytest tests/test_rag.py tests/test_ingestion.py -q
```

---

## 5. 你的 W2 任务清单（按优先级排，含验收标准）

### P0-A：摄入流水线 v1（PLAN W2：质量评估 + 复杂度评分 + MinerU 接入）

1. **质量评估 #9 检测器**（新建 `ingestion/quality.py`）
   - 已有：`has_text_layer()`（扫描件判定）
   - 待做：页面方向/倾斜（PyMuPDF 的 `/Rotate` 属性 + 文本 bbox 斜率估计）、
     清晰度（渲染缩略图的拉普拉斯方差，cv2 可选）、繁简检测（opencc 判繁体比例）
   - 产出写进 `DocIR.meta.quality`，**字段结构已冻结**（见契约 §2）
   - 验收：对 3 份正常文档报告正常分；对 W2 造的坏文档能报出对应缺陷
2. **复杂度评分 #8 轻量**（新建 `ingestion/complexity.py`）
   - 可解释规则加权：无文本层+3 / 有表格+1 / 页数>20+1 / 图片占比>30%+1 → 1-5 级
   - 路由：1-2 级→pymupdf 直抽；3 级→MinerU；4-5 级→MinerU+后处理告警
   - 验收：`meta.complexity` 落库；路由函数有单测
3. **MinerU 接入**（新建 `ingestion/parsers/mineru.py`）
   - 本地 GPU pipeline（D5 决议主路径）；解析结果转成 BlockIR（保持 bbox/page 语义）
   - SiliconFlow API 备胎：`.env` 配置化切换（参考 `rag/embedding.py` 的写法）
   - 验收：1 份扫描件 PDF 能走通 MinerU → IR → 检索
4. **坏文档生成器**（`scripts/gen_bad_docs.py`）
   - 3 份：目录层级丢失（把 H2 字号改成正文）/ 页面颠倒扫描（渲染成图+rotate 180°+噪声）/ 模糊+繁体混排（opencc 转繁体 + 高压缩）
   - 验收：质量检测器对三份各有告警；`tests/` 有一个"坏文档→修复→检索仍可用"的集成测试

### P0-B：RAG 引擎补完（PLAN W2-W3：rerank + 引用溯源 + 忠实度）

1. **rerank 精排**（`rag/reranker.py`）：bge-reranker-v2-m3 本地 GPU（W3），
   SiliconFlow API 备胎。挂进 `HybridRetriever.search()`：RRF top-20 → rerank → top-6
2. **忠实度自检**（`rag/pipeline.py` ③ 处留了位）：LLM 逐句校验答案是否有引用支撑，
   不支撑则收敛重写（≤2 轮）。purpose 标 `rag.factcheck`（记账可查成本）
3. **查询改写**（多轮融合 + HyDE 可选）：W2 编排内核给你传 `history`，
   你负责把"他呢？"补全成完整问题（参考 A 的 `nl2sql/rewriter.py` 结构）
4. **知识库扩到 10 份**（`kb_doc_content.py`）：按 PLAN §6.1 清单补产品线运营说明、
   供应商合作协议（含表格）、财务制度（多公式，#6 压测用）——记得让 C 评审评测用例

### P1：GPU 部署验证（W3 前必须完成，阻塞项 #7）

```bash
# RTX 5060 只有 8GB —— BGE-M3(fp16 ≈2.4GB) + reranker(≈2.2GB) 同驻可能吃紧
# 验证脚本建议放 scripts/verify_gpu.py，量三件事：
#   1) 两模型同驻显存峰值；2) embedding 吞吐（chunks/s）；3) rerank 延迟 P50/P95
# 不够的降级路径（D9-B 已配置化）：EMBEDDING_PROVIDER=siliconflow，当天可切
```

### 交付节奏（对齐 PLAN §13）

- **周五（W1 收尾）**：评审并冻结 `ingestion_ir.md`（你的名字该出现在变更记录里）
- **W2 周五**：摄入 v1 完成 + 知识库 10 份 + 坏文档演示可跑；L1 回归全绿
- **W3**：rerank + BGE-M3 上 GPU + 混合检索调优（用 `run_rag.py` 的 retrieval_hit 指标驱动）

---

## 6. 踩坑记录（我都替你踩过了）

### 6.1 数据事实
- 本版 Chinook **发票日期 2021-2025**（不是网上教程的 2013）
- **71 位艺术家没有专辑**——问"每位艺术家的专辑数"有 JOIN/LEFT JOIN 歧义，
  评测用例措辞必须写"专辑最多的前 N 位"
- PDF 硬换行会把 "5 天" 拆成两行——所有文本匹配必须**空白不敏感**（`_norm()` 模式已沉淀在 smoke/runner 里）

### 6.2 网络与下载
- Docker Hub / huggingface.co 直连不通；用 **hf-mirror.com**（下载模型设
  `HF_ENDPOINT=https://hf-mirror.com`）和 **docker.m.daocloud.io**（已在 .env 配好）
- **torch 必须装 CPU 版**：`pip install torch --index-url https://download.pytorch.org/whl/cpu`。
  默认 pypi 会拉 ~2.5GB CUDA 轮子然后超时（我在这里卡了 15 分钟）
- pypi / npmjs / ghproxy 可直连；codeload.github.com 可直连

### 6.3 容器
- backend 容器以宿主 UID 运行（compose `user:`），挂载目录不会出现 root 属主文件
- 容器内代码在 `/app`，data 和 models 挂载在 `/app/data`、`/app/models`——
  资源路径解析用 `config.resolve_repo_path()`（双探测），别自己拼
- nginx 类容器 entrypoint 的 apk 探测在受限网络会挂起（frontend 的 compose 已绕过，
  以后起其它镜像容器遇到"卡住不出日志"先怀疑这个）

### 6.4 维度陷阱（重要）
`kb_chunk.embedding` 的维度绑定**首次建表时的模型**。换模型 = drop 表 + `--force` 重摄入，
store 会显式报错给出指引。**不要**试图往 512 维列里塞 1024 维向量然后看报错猜原因。

### 6.5 中文字体
PDF 生成用系统文泉驿（`/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`）。
**gen_kb_docs.py 只在宿主机跑**（容器里没这字体）；PDF 已入 git，容器只负责摄入。

---

## 7. 协作接口（你需要知道别人依赖你什么）

| 依赖方 | 依赖你的东西 | 形态 |
|---|---|---|
| A（编排内核 W2） | `rag_search` 工具签名 | `RAGPipeline.run(question) -> RAGResult`，已同构 NL2SQL，**别改字段名** |
| A（公式引擎 W4） | FormulaIR 登记 | 契约已冻结 schema，W2 你实现登记时照抄 |
| C（前端 W2） | citations[] 渲染 PDF 定位 | `{doc, doc_id, page, breadcrumb, snippet, score}` 已定稿，改字段先找 C |
| C（评测 W3） | RAG 用例 + retrieval_hit 指标 | 用例格式见 `eval_case_format.md`，加用例过 `preflight` |
| 全员 | 摄入成本/耗时数据 | `DocIR.meta` 里加（别新建平行结构） |

**反过来你依赖别人的**：
- LLM 一律走 `get_llm_service().chat(messages, purpose="rag.xxx")`（缓存+记账自动生效，
  **purpose 命名规范：rag.ingest / rag.generate / rag.factcheck / rag.rewrite**）
- PG 连接一律走 `app.db.session.get_conn()`（只读默认开）
- Trace 节点用 `TraceCollector.span()`，别自己拼 dict

---

## 8. W3/W4 展望（现在别做，但别堵死）

- **W3 混合检索规模化**：chunks 上千后，内存 BM25 → PG 全文/外部索引，向量余弦 →
  pgvector HNSW 索引。`HybridRetriever.search()` 接口不变，只换内部实现
- **W3 术语向量化**（A 的 #1/#2）：会复用你的 EmbeddingService 给 `biz_term` 算向量——
  所以 **EmbeddingService 保持线程安全**（现在有锁，别删）
- **W4 公式引擎**（A 的 #6 消费端）：从你的 `kb_formula` 表读 FormulaIR，
  LaTeX→SymPy 由 A 做，你只保证登记质量（参数 desc/source 标注准确）
- **决赛 #7 完整版**：字号统计识别升级为信号融合（字号+编号模式+目录页交叉验证+LLM 判定），
  现在 `pdf_ingest.py` 的函数边界就是为这个留的

---

## 9. 第一天建议（照做即可）

1. [ ] 跑通 §1 的四条命令
2. [ ] 读 `ingestion/ir.py` + `docs/contracts/ingestion_ir.md`（15 分钟，这是你的宪法）
3. [ ] 读 `rag/retriever.py`（40 行 BM25 + RRF，理解融合逻辑）
4. [ ] 给 `kb_doc_content.py` 加一份新文档（比如"产品线运营说明"），跑三连命令，
     看 `eval/run_rag.py` 指标变化——这一趟走完你就掌握全链路了
5. [ ] 在 `ingestion_ir.md` 变更记录里签上你的名字（评审通过 = 冻结）

有疑问直接在群里 @A，或者看 `docs/weekly/W1_progress.md` 里的阻塞项清单。
