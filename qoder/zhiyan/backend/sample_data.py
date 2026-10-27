"""Bundled sample corpus: literature summaries + course/lab notes.

Used to initialize seekdb so the project is runnable out-of-the-box and the
retrieval/memory features can be demonstrated without external data. In the
published Qoder Site the same corpus powers the clearly-labeled demo mode.
"""

from __future__ import annotations

# type: paper (文献) | note (笔记) | lab (实验记录)
SAMPLE_CORPUS: list[dict] = [
    {
        "id": "doc-01",
        "type": "paper",
        "title": "Efficient Memory Management for LLM Serving with PagedAttention",
        "section": "摘要",
        "tags": ["LLM", "推理优化", "显存管理"],
        "text": "PagedAttention 借鉴操作系统分页思想，将 KV Cache 切分为固定大小的块并按需分配，"
                "显著减少显存碎片。vLLM 在此基础上实现接近零浪费的 KV Cache 管理，"
                "相比 HuggingFace Transformers 吞吐提升 2-4 倍，相比 TGI 提升 2.2 倍。",
    },
    {
        "id": "doc-02",
        "type": "paper",
        "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP",
        "section": "方法",
        "tags": ["RAG", "检索增强", "向量检索"],
        "text": "RAG 将参数化记忆（预训练 seq2seq 模型）与非参数化记忆（可检索的稠密向量索引）结合。"
                "检索器用 DPR 将问题与文档编码为稠密向量，按最大内积搜索取 Top-K 文档，"
                "生成器以这些文档为条件生成答案，从而在知识密集型任务上减少幻觉。",
    },
    {
        "id": "doc-03",
        "type": "paper",
        "title": "Efficient and Robust Approximate Nearest Neighbor Search Using HNSW",
        "section": "核心思想",
        "tags": ["ANN", "HNSW", "向量索引"],
        "text": "HNSW 是基于多层可导航小世界图的近似最近邻算法。上层稀疏用于长距离快速逼近，"
                "底层稠密用于精确检索。构建时每个新节点从最高层贪心下降插入，复杂度 O(log N)。"
                "在召回率与延迟的权衡上优于多数基于树和量化的方法。",
    },
    {
        "id": "doc-04",
        "type": "note",
        "title": "数据库课程笔记",
        "section": "第5讲 索引",
        "tags": ["数据库", "索引", "B+树"],
        "text": "B+树索引：所有数据存储在叶子节点，叶子节点通过指针相连形成有序链表，利于范围查询。"
                "与B树相比，B+树非叶节点只存键不存数据，单个节点能容纳更多键，树更矮，磁盘IO更少。"
                "适合 OLTP 的等值与范围查询。",
    },
    {
        "id": "doc-05",
        "type": "note",
        "title": "数据库课程笔记",
        "section": "第7讲 事务与ACID",
        "tags": ["数据库", "事务", "ACID", "MVCC"],
        "text": "事务ACID：原子性(Atomicity)、一致性(Consistency)、隔离性(Isolation)、持久性(Durability)。"
                "隔离级别从低到高：读未提交、读已提交、可重复读、串行化，分别可能出现脏读、不可重复读、幻读。"
                "MVCC 通过保存数据的多版本（undo log + read view）实现读写不阻塞，是可重复读隔离的基础。",
    },
    {
        "id": "doc-06",
        "type": "note",
        "title": "数据库课程笔记",
        "section": "第9讲 分布式一致性",
        "tags": ["分布式", "Paxos", "Raft", "一致性"],
        "text": "Paxos 与 Raft 都是解决分布式共识的算法。Raft 将共识分解为领导选举、日志复制、安全性三个子问题，"
                "比 Paxos 更易理解。多数派(quorum)写入保证一致性：N 个副本中写入超过半数即提交。"
                "OceanBase 使用 Paxos 保证多副本强一致。",
    },
    {
        "id": "doc-07",
        "type": "note",
        "title": "机器学习课程笔记",
        "section": "第3讲 梯度下降",
        "tags": ["机器学习", "优化", "梯度下降"],
        "text": "梯度下降沿损失函数负梯度方向更新参数。批量梯度下降(BGD)用全量数据，稳定但慢；"
                "随机梯度下降(SGD)每次一个样本，快但震荡；小批量(mini-batch)折中。"
                "学习率过大发散、过小收敛慢，常用学习率衰减或 Adam 自适应优化器。",
    },
    {
        "id": "doc-08",
        "type": "note",
        "title": "机器学习课程笔记",
        "section": "第6讲 过拟合与正则化",
        "tags": ["机器学习", "过拟合", "正则化"],
        "text": "过拟合指模型在训练集表现好但泛化差，表现为高方差。缓解手段：增加数据、早停、"
                "Dropout、L1/L2正则化、降低模型复杂度、交叉验证。L1正则产生稀疏解可做特征选择，"
                "L2正则使权重更平滑。欠拟合则相反，是高偏差问题。",
    },
    {
        "id": "doc-09",
        "type": "lab",
        "title": "实验记录：向量检索性能对比",
        "section": "实验2 HNSW vs IVF",
        "tags": ["实验", "向量检索", "HNSW", "IVF"],
        "text": "在 100万条 768维向量上对比 HNSW 与 IVF-PQ：HNSW 召回率95%时QPS约3000，"
                "内存占用高；IVF-PQ 召回率92%时QPS约5000，内存占用低但召回略差。"
                "结论：内存充足且追求召回选HNSW，海量数据且容忍精度损失选IVF-PQ。",
    },
    {
        "id": "doc-10",
        "type": "lab",
        "title": "实验记录：RAG 问答系统搭建",
        "section": "实验3 切分策略",
        "tags": ["实验", "RAG", "文档切分"],
        "text": "文档切分策略影响RAG效果：固定长度切分简单但可能割裂语义；按语义/标题切分保留完整性。"
                "实验中按段落切分+重叠窗口(overlap 50 tokens)效果最好，Top-K=5时答案准确率最高。"
                "切分过大引入噪声，过小丢失上下文。",
    },
    {
        "id": "doc-11",
        "type": "paper",
        "title": "Attention Is All You Need",
        "section": "自注意力机制",
        "tags": ["Transformer", "注意力机制", "深度学习"],
        "text": "Transformer 抛弃循环和卷积，完全基于注意力机制。缩放点积注意力 Attention(Q,K,V)=softmax(QK^T/√d)V，"
                "除以√d防止点积过大导致softmax梯度消失。多头注意力让模型在不同子空间关注不同位置的信息。"
                "位置编码用正弦函数注入序列顺序信息。",
    },
    {
        "id": "doc-12",
        "type": "note",
        "title": "数据库课程笔记",
        "section": "第11讲 向量数据库",
        "tags": ["数据库", "向量数据库", "混合检索"],
        "text": "向量数据库支持高维向量的存储与相似性检索，常见距离：欧氏(L2)、内积、余弦。"
                "混合检索(hybrid search)结合向量语义检索与全文关键词检索，兼顾语义相关与精确匹配。"
                "OceanBase seekdb 在单一引擎内统一向量、全文、结构化数据，支持混合检索，适合AI Agent存储。",
    },
]


def all_documents() -> list[dict]:
    return list(SAMPLE_CORPUS)
