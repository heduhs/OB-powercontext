/* 智研 ZhiYan frontend logic.
 * Two modes:
 *  - live: a local FastAPI backend is reachable -> real seekdb + MiniMax + PowerContext
 *  - demo: bundled sample corpus + client-side retrieval + localStorage memory
 * The published Qoder Site runs in demo mode (clearly labeled).
 */

// ---------- bundled sample corpus (demo mode) ----------
const CORPUS = [
  {id:"doc-01",type:"paper",title:"Efficient Memory Management for LLM Serving with PagedAttention",section:"摘要",tags:["LLM","推理优化","显存管理"],text:"PagedAttention 借鉴操作系统分页思想，将 KV Cache 切分为固定大小的块并按需分配，显著减少显存碎片。vLLM 在此基础上实现接近零浪费的 KV Cache 管理，相比 HuggingFace Transformers 吞吐提升 2-4 倍，相比 TGI 提升 2.2 倍。"},
  {id:"doc-02",type:"paper",title:"Retrieval-Augmented Generation for Knowledge-Intensive NLP",section:"方法",tags:["RAG","检索增强","向量检索"],text:"RAG 将参数化记忆（预训练 seq2seq 模型）与非参数化记忆（可检索的稠密向量索引）结合。检索器用 DPR 将问题与文档编码为稠密向量，按最大内积搜索取 Top-K 文档，生成器以这些文档为条件生成答案，从而在知识密集型任务上减少幻觉。"},
  {id:"doc-03",type:"paper",title:"Efficient and Robust Approximate Nearest Neighbor Search Using HNSW",section:"核心思想",tags:["ANN","HNSW","向量索引"],text:"HNSW 是基于多层可导航小世界图的近似最近邻算法。上层稀疏用于长距离快速逼近，底层稠密用于精确检索。构建时每个新节点从最高层贪心下降插入，复杂度 O(log N)。在召回率与延迟的权衡上优于多数基于树和量化的方法。"},
  {id:"doc-04",type:"note",title:"数据库课程笔记",section:"第5讲 索引",tags:["数据库","索引","B+树"],text:"B+树索引：所有数据存储在叶子节点，叶子节点通过指针相连形成有序链表，利于范围查询。与B树相比，B+树非叶节点只存键不存数据，单个节点能容纳更多键，树更矮，磁盘IO更少。适合 OLTP 的等值与范围查询。"},
  {id:"doc-05",type:"note",title:"数据库课程笔记",section:"第7讲 事务与ACID",tags:["数据库","事务","ACID","MVCC"],text:"事务ACID：原子性、一致性、隔离性、持久性。隔离级别从低到高：读未提交、读已提交、可重复读、串行化，分别可能出现脏读、不可重复读、幻读。MVCC 通过保存数据的多版本（undo log + read view）实现读写不阻塞，是可重复读隔离的基础。"},
  {id:"doc-06",type:"note",title:"数据库课程笔记",section:"第9讲 分布式一致性",tags:["分布式","Paxos","Raft","一致性"],text:"Paxos 与 Raft 都是解决分布式共识的算法。Raft 将共识分解为领导选举、日志复制、安全性三个子问题，比 Paxos 更易理解。多数派(quorum)写入保证一致性。OceanBase 使用 Paxos 保证多副本强一致。"},
  {id:"doc-07",type:"note",title:"机器学习课程笔记",section:"第3讲 梯度下降",tags:["机器学习","优化","梯度下降"],text:"梯度下降沿损失函数负梯度方向更新参数。批量梯度下降用全量数据，稳定但慢；随机梯度下降每次一个样本，快但震荡；小批量折中。学习率过大发散、过小收敛慢，常用学习率衰减或 Adam 自适应优化器。"},
  {id:"doc-08",type:"note",title:"机器学习课程笔记",section:"第6讲 过拟合与正则化",tags:["机器学习","过拟合","正则化"],text:"过拟合指模型在训练集表现好但泛化差，表现为高方差。缓解手段：增加数据、早停、Dropout、L1/L2正则化、降低模型复杂度、交叉验证。L1正则产生稀疏解可做特征选择，L2正则使权重更平滑。欠拟合则相反，是高偏差问题。"},
  {id:"doc-09",type:"lab",title:"实验记录：向量检索性能对比",section:"实验2 HNSW vs IVF",tags:["实验","向量检索","HNSW","IVF"],text:"在 100万条 768维向量上对比 HNSW 与 IVF-PQ：HNSW 召回率95%时QPS约3000，内存占用高；IVF-PQ 召回率92%时QPS约5000，内存占用低但召回略差。结论：内存充足且追求召回选HNSW，海量数据且容忍精度损失选IVF-PQ。"},
  {id:"doc-10",type:"lab",title:"实验记录：RAG 问答系统搭建",section:"实验3 切分策略",tags:["实验","RAG","文档切分"],text:"文档切分策略影响RAG效果：固定长度切分简单但可能割裂语义；按语义/标题切分保留完整性。实验中按段落切分+重叠窗口(overlap 50 tokens)效果最好，Top-K=5时答案准确率最高。切分过大引入噪声，过小丢失上下文。"},
  {id:"doc-11",type:"paper",title:"Attention Is All You Need",section:"自注意力机制",tags:["Transformer","注意力机制","深度学习"],text:"Transformer 抛弃循环和卷积，完全基于注意力机制。缩放点积注意力 Attention(Q,K,V)=softmax(QK^T/√d)V，除以√d防止点积过大导致softmax梯度消失。多头注意力让模型在不同子空间关注不同位置的信息。位置编码用正弦函数注入序列顺序信息。"},
  {id:"doc-12",type:"note",title:"数据库课程笔记",section:"第11讲 向量数据库",tags:["数据库","向量数据库","混合检索"],text:"向量数据库支持高维向量的存储与相似性检索，常见距离：欧氏(L2)、内积、余弦。混合检索结合向量语义检索与全文关键词检索，兼顾语义相关与精确匹配。OceanBase seekdb 在单一引擎内统一向量、全文、结构化数据，支持混合检索，适合AI Agent存储。"},
];

const SUGGESTIONS = ["向量数据库如何做混合检索","事务隔离级别与MVCC","HNSW 算法原理","RAG 如何减少幻觉","过拟合怎么缓解","Paxos 和 Raft 的区别"];

// ---------- state ----------
let LIVE = false;
let API = "";

// ---------- helpers ----------
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
function esc(str){ return String(str).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function typeLabel(t){ return {paper:"文献",note:"笔记",lab:"实验"}[t] || t; }

async function tryHealth(base){
  try {
    const r = await fetch(base + "/api/health", {method:"GET"});
    if (!r.ok) return false;
    const d = await r.json();
    return d && d.status === "ok";
  } catch(e){ return false; }
}

async function detectMode(){
  // same-origin first (served by the local backend), then common local port
  if (await tryHealth("")) { LIVE = true; API = ""; return; }
  if (await tryHealth("http://127.0.0.1:8010")) { LIVE = true; API = "http://127.0.0.1:8010"; return; }
  LIVE = false; API = "";
}

function setModeBadge(){
  const b = $("#mode-badge");
  if (LIVE){ b.textContent = "实时模式 · seekdb+MiniMax"; b.className = "badge badge-live"; $("#demo-note").innerHTML = "已连接本地后端，<b>实时模式</b>：检索由 seekdb 混合检索提供，问答由 MiniMax-M1 基于资料生成，薄弱点与复习进度由 PowerContext 持久化。"; }
  else { b.textContent = "演示模式 · 示例数据"; b.className = "badge badge-demo"; }
}

// ---------- demo-mode client-side retrieval ----------
function demoScore(query, doc){
  const hay = (doc.title + " " + doc.section + " " + doc.text + " " + doc.tags.join(" ")).toLowerCase();
  const q = query.toLowerCase();
  let score = 0;
  // whole-query substring
  if (hay.includes(q)) score += 3;
  // token / bigram overlap
  const tokens = q.split(/[\s,，。、？?]+/).filter(Boolean);
  for (const t of tokens){
    if (t.length >= 2 && hay.includes(t)) score += 2;
  }
  // character bigrams for Chinese
  const clean = q.replace(/\s+/g,"");
  for (let i=0;i<clean.length-1;i++){
    const bg = clean.slice(i,i+2);
    if (bg.length===2 && hay.includes(bg)) score += 0.6;
  }
  // tag bonus
  for (const tag of doc.tags){ if (q.includes(tag.toLowerCase())) score += 1.5; }
  return score;
}
function demoSearch(query, type, topK){
  let docs = CORPUS.filter(d => !type || d.type === type);
  const scored = docs.map(d => ({d, s: demoScore(query, d)})).filter(x => x.s > 0);
  scored.sort((a,b) => b.s - a.s);
  const max = Math.max(1, scored.length ? scored[0].s : 1);
  return scored.slice(0, topK).map(x => ({
    title:x.d.title, section:x.d.section, type:x.d.type, tags:x.d.tags,
    text:x.d.text, score: Math.round((0.5 + 0.5*(x.s/max))*1000)/1000
  }));
}

// ---------- demo-mode memory (localStorage, SM-2 lite) ----------
const LS_KEY = "zhiyan_weak_points_v1";
function lsLoad(){ try { return JSON.parse(localStorage.getItem(LS_KEY)) || []; } catch(e){ return []; } }
function lsSave(arr){ localStorage.setItem(LS_KEY, JSON.stringify(arr)); }
function slug(s){ let h=0; const n=s.trim().toLowerCase(); for(let i=0;i<n.length;i++){h=(h*31+n.charCodeAt(i))|0;} return "w"+(h>>>0).toString(16); }
function todayStr(){ return new Date().toISOString().slice(0,10); }
function addDays(ds, n){ const d=new Date(ds+"T00:00:00"); d.setDate(d.getDate()+n); return d.toISOString().slice(0,10); }

function demoAddWeak(point, source){
  const arr = lsLoad();
  if (arr.some(w=>w.id===slug(point))) return;
  arr.push({id:slug(point),point,source:source||"",version:1,review_count:0,interval_days:0,ease:2.5,next_review:todayStr(),created_at:todayStr()});
  lsSave(arr);
}
function demoListWeak(){ return lsLoad().sort((a,b)=> (a.next_review<b.next_review?-1:1)); }
function demoReview(point, quality){
  const arr = lsLoad(); const id = slug(point);
  const w = arr.find(x=>x.id===id); if(!w) return null;
  quality = Math.max(0, Math.min(5, quality));
  let rc = w.review_count, ease = w.ease, iv = w.interval_days;
  if (quality >= 3){
    rc += 1;
    iv = rc===1?1:(rc===2?6:Math.max(1, Math.round(iv*ease)));
    ease = Math.max(1.3, ease + (0.1 - (5-quality)*(0.08+(5-quality)*0.02)));
  } else { rc = 0; iv = 1; ease = Math.max(1.3, ease-0.2); }
  w.review_count=rc; w.ease=Math.round(ease*1000)/1000; w.interval_days=iv;
  w.next_review=addDays(todayStr(), iv); w.last_quality=quality; w.version=(w.version||0)+1;
  lsSave(arr); return w;
}
function demoPlan(){ const t=todayStr(); return lsLoad().filter(w=>w.next_review<=t).sort((a,b)=>a.next_review<b.next_review?-1:1); }

// ---------- API wrappers (mode-aware) ----------
async function apiSearch(query, type, topK){
  if (LIVE){
    const r = await fetch(API+"/api/search",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query,type:type||null,top_k:topK})});
    const d = await r.json(); return d.results || [];
  }
  return demoSearch(query, type, topK);
}
async function apiAsk(question){
  if (LIVE){
    const r = await fetch(API+"/api/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question,top_k:5})});
    return await r.json();
  }
  // demo: assemble a grounded-style answer from top chunks, clearly labeled
  const hits = demoSearch(question, "", 3);
  if (!hits.length) return {answer:"（演示模式）没有匹配到示例资料，请换个关键词，或在实时模式下由 MiniMax 作答。", citations:[], sources:[]};
  const body = hits.map((h,i)=>`[${i+1}] ${h.text}`).join("\n\n");
  return {answer:`（演示模式·示例拼接，实时模式由 MiniMax-M1 生成）\n\n根据检索到的资料：\n\n${body}`, citations:hits.map((h,i)=>({index:i+1,title:h.title,section:h.section})), sources:hits};
}
async function apiAddWeak(point, source){
  if (LIVE){ await fetch(API+"/api/weak-points",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({point,source})}); return; }
  demoAddWeak(point, source);
}
async function apiListWeak(){
  if (LIVE){ const r=await fetch(API+"/api/weak-points"); const d=await r.json(); return d.weak_points||[]; }
  return demoListWeak();
}
async function apiReview(point, quality){
  if (LIVE){ const r=await fetch(API+"/api/review",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({point,quality})}); const d=await r.json(); return d.updated; }
  return demoReview(point, quality);
}
async function apiPlan(){
  if (LIVE){ const r=await fetch(API+"/api/review/plan"); const d=await r.json(); return d.due||[]; }
  return demoPlan();
}

// ---------- rendering ----------
function renderResults(hits){
  const box = $("#results");
  if (!hits.length){ box.innerHTML = '<div class="empty">没有检索到相关资料，换个关键词试试。</div>'; return; }
  box.innerHTML = hits.map(h => `
    <div class="result">
      <div class="result-top">
        <span class="tag type-${h.type}">${typeLabel(h.type)}</span>
        <h4>${esc(h.title)}</h4>
        <span class="sec">· ${esc(h.section)}</span>
        <span class="score">相关度 ${h.score}</span>
      </div>
      <p>${esc(h.text)}</p>
      <div class="chips" style="margin-top:8px">${(h.tags||[]).map(t=>`<span class="chip" style="cursor:default">#${esc(t)}</span>`).join("")}</div>
    </div>`).join("");
}

function renderAnswer(res){
  const box = $("#answer");
  const cites = (res.citations||[]).map(c=>`<span class="cite">[${c.index}] ${esc(c.title)} · ${esc(c.section||"")}</span>`).join("");
  box.innerHTML = `<div class="a-text">${esc(res.answer||"")}</div>${cites?`<div class="cites">${cites}</div>`:""}`;
}

function renderWeak(list){
  const box = $("#weak-list");
  if (!list.length){ box.innerHTML = '<div class="empty">还没有薄弱知识点。检索或问答后，把记不住的点标记下来。</div>'; return; }
  const t = todayStr();
  box.innerHTML = list.map(w => `
    <div class="weak">
      <div class="wp-main">
        <div class="wp-point">${esc(w.point)}</div>
        <div class="wp-meta">${w.source?esc(w.source)+" · ":""}复习 ${w.review_count||0} 次 · 下次 ${esc(w.next_review||t)} ${ (w.next_review||t)<=t ? '<span class="pill due">待复习</span>':'' }</div>
      </div>
      <div class="rate" data-point="${esc(w.point)}">
        <button data-q="1" title="完全忘了">1</button>
        <button data-q="3" title="有点印象">3</button>
        <button data-q="5" title="记得很牢">5</button>
      </div>
    </div>`).join("");
  $$("#weak-list .rate button").forEach(b => b.onclick = async () => {
    const point = b.parentElement.getAttribute("data-point");
    await apiReview(point, parseInt(b.getAttribute("data-q"),10));
    renderWeak(await apiListWeak()); renderPlan(await apiPlan());
  });
}

function renderPlan(list){
  const box = $("#plan-list");
  if (!list.length){ box.innerHTML = '<div class="empty">今天没有到期的复习项，去「薄弱知识点」标记一些吧。</div>'; return; }
  box.innerHTML = list.map(w => `
    <div class="weak">
      <div class="wp-main">
        <div class="wp-point">${esc(w.point)}</div>
        <div class="wp-meta">${w.source?esc(w.source)+" · ":""}已复习 ${w.review_count||0} 次 · 计划 ${esc(w.next_review)}</div>
      </div>
      <span class="pill due">今日复习</span>
    </div>`).join("");
}

// ---------- events ----------
function bindTabs(){
  $$(".tab").forEach(tab => tab.onclick = () => {
    $$(".tab").forEach(t=>t.classList.remove("active"));
    $$(".panel").forEach(p=>p.classList.remove("active"));
    tab.classList.add("active");
    $("#panel-"+tab.getAttribute("data-tab")).classList.add("active");
  });
}

async function doSearch(){
  const q = $("#q").value.trim(); if(!q) return;
  $("#results").innerHTML = '<div class="empty">检索中…</div>';
  renderResults(await apiSearch(q, $("#q-type").value, 5));
}
async function doAsk(){
  const q = $("#ask-q").value.trim(); if(!q) return;
  $("#answer").innerHTML = '<div class="a-text">思考中…（实时模式调用 MiniMax-M1，约需数秒）</div>';
  renderAnswer(await apiAsk(q));
}
async function doAddWeak(){
  const p = $("#wp-point").value.trim(); if(!p) return;
  await apiAddWeak(p, $("#wp-source").value.trim());
  $("#wp-point").value=""; $("#wp-source").value="";
  renderWeak(await apiListWeak()); renderPlan(await apiPlan());
}

// ---------- init ----------
(async function init(){
  $("#repo-link").href = "https://github.com/heduhs/powercontext-2026/tree/2026-competition-ecosystem/qoder/zhiyan";
  bindTabs();
  $("#q-suggest").innerHTML = SUGGESTIONS.map(s=>`<span class="chip">${esc(s)}</span>`).join("");
  $$("#q-suggest .chip").forEach(c => c.onclick = () => { $("#q").value = c.textContent; doSearch(); });
  $("#q-btn").onclick = doSearch;
  $("#q").addEventListener("keydown", e => { if(e.key==="Enter") doSearch(); });
  $("#ask-btn").onclick = doAsk;
  $("#ask-q").addEventListener("keydown", e => { if(e.key==="Enter") doAsk(); });
  $("#wp-btn").onclick = doAddWeak;
  $("#wp-point").addEventListener("keydown", e => { if(e.key==="Enter") doAddWeak(); });

  await detectMode();
  setModeBadge();
  $("#stat-corpus").textContent = LIVE ? "—" : CORPUS.length;

  // initial content
  renderResults(await apiSearch("向量数据库如何做混合检索", "", 3));
  renderWeak(await apiListWeak());
  renderPlan(await apiPlan());
})();
