项目结构
                                                                                
  rag_tool/                                                 
  ├── __init__.py              # 包入口                                         
  ├── config.py                # 配置管理                   
  ├── config.yaml              # 配置文件
  ├── pyproject.toml           # 依赖管理                                       
  ├── main.py                  # 示例入口
  │                                                                             
  ├── core/                    # 核心模块                   
  │   ├── embedder.py          # BGE-m3-zh 嵌入
  │   ├── reranker.py          # BGE-reranker                                   
  │   └── chunker.py           # 中文文本分块
  │                                                                             
  ├── retrieval/               # 检索模块                   
  │   ├── vector_store.py      # Qdrant HNSW 检索                               
  │   ├── bm25_store.py        # BM25 关键词检索
  │   └── fusion.py            # RRF 融合                                       
  │                                                         
  ├── pipeline/                # 处理管道
  │   ├── ingest.py            # 数据摄入                                       
  │   └── query_enhancer.py    # Multi-Query 查询增强
  │                                                                             
  └── langgraph_integration/  # LangGraph 集成              
      ├── nodes.py             # 节点封装
      └── wrapper.py           # 封装器                                         
   
  ---                                                                           
  安装依赖                                                  

  cd /Users/lsf/Desktop/RAG
  pip install -e rag_tool                                                       
   
  前置服务: Qdrant 需要运行在 localhost:6333                                    
                                                            
  docker run -d -p 6333:6333 -p 6334:6334 qdrant/qdrant                         
                                                            
  ---
  接口文档
                                                                                
  1. 快速初始化
                                                                                
  from rag_tool import RAGLangGraphWrapper, RAGConfig       

  # 方式1: 使用配置文件
  wrapper = RAGLangGraphWrapper(config_path="rag_tool/config.yaml")
                                                                                
  # 方式2: 手动配置
  config = RAGConfig()                                                          
  config.llm.api_key = "your-minimax-api-key"               
  wrapper = RAGLangGraphWrapper(config=config)
                                                                                
  2. 数据摄入
                                                                                
  # 初始化向量数据库                                        
  wrapper.initialize(recreate_collection=False)

  # 摄入文档
  wrapper.ingest(
      doc_id="novel_001",                                                       
      text="《红楼梦》是中国古代章回体长篇小说...",
      metadata={"source": "文学", "title": "红楼梦"}                            
  )                                                                             
   
  3. 检索                                                                       
                                                            
  # 检索相关文本
  results = wrapper.retrieve("红楼梦的作者是谁？")
                                                                                
  # results 是 RetrievedChunk 列表，每个元素包含:
  # - content: str       # 文本内容                                             
  # - metadata: dict     # 元数据                           
  # - score: float       # 相关性分数
                                                                                
  4. LangGraph Agent 集成
                                                                                
  from rag_tool import RAGLangGraphWrapper                  
  from langgraph.graph import StateGraph, END
  from langchain_core.messages import HumanMessage                              
   
  wrapper = RAGLangGraphWrapper(config_path="rag_tool/config.yaml")             
  wrapper.initialize()                                      

  # 获取 RAG 节点
  rag_node = wrapper.get_langgraph_node()
                                                                                
  # 构建 Agent
  graph = StateGraph(dict)                                                      
  graph.add_node("rag_retrieve", rag_node)                  
  graph.add_node("llm", your_llm_node)  # 你的 LLM 节点
  graph.set_entry_point("rag_retrieve")                                         
  graph.add_edge("rag_retrieve", "llm")
  graph.add_edge("llm", END)                                                    
                                                            
  app = graph.compile()

  # 执行                                                                        
  result = app.invoke({
      "messages": [HumanMessage(content="用户问题...")]                         
  })                                                        

  5. 核心类说明

  ┌────────────────────┬────────────────────────────────┬───────────────────┐   
  │         类         │              文件              │       功能        │
  ├────────────────────┼────────────────────────────────┼───────────────────┤   
  │ RAGLangGraphWrappe │ langgraph_integration/wrapper. │ 主封装器，简洁接  │
  │ r                  │ py                             │ 口                │
  ├────────────────────┼────────────────────────────────┼───────────────────┤   
  │ RAGPipeline        │ langgraph_integration/wrapper. │ RAG 检索管道      │
  │                    │ py                             │                   │   
  ├────────────────────┼────────────────────────────────┼───────────────────┤
  │ BGEEmbedder        │ core/embedder.py               │ BGE-m3-zh 向量化  │
  ├────────────────────┼────────────────────────────────┼───────────────────┤   
  │ BGEReranker        │ core/reranker.py               │ 重排序            │
  ├────────────────────┼────────────────────────────────┼───────────────────┤   
  │ ChineseTextChunker │ core/chunker.py                │ 中文分块          │
  │                    │                                │ (200-500字)       │   
  ├────────────────────┼────────────────────────────────┼───────────────────┤
  │ QdrantVectorStore  │ retrieval/vector_store.py      │ HNSW 向量检索     │   
  ├────────────────────┼────────────────────────────────┼───────────────────┤
  │ BM25Store          │ retrieval/bm25_store.py        │ BM25 关键词检索   │
  ├────────────────────┼────────────────────────────────┼───────────────────┤   
  │ RRFFusion          │ retrieval/fusion.py            │ RRF 多路融合      │
  └────────────────────┴────────────────────────────────┴───────────────────┘   
                                                            
  6. 配置项 (config.yaml)

  embedder:
    model_name: "BAAI/bge-m3-zh"                                                
    dimension: 1024
    batch_size: 32                                                              
    device: "cpu"          # 或 "cuda"                      
                                                                                
  reranker:
    model_name: "BAAI/bge-reranker"                                             
    device: "cpu"                                           

  qdrant:
    host: "localhost"
    port: 6333
    collection: "rag_docs"

  retrieval:
    sub_query_count: 3      # Multi-Query 生成子查询数量
    vector_top_k: 20        # 向量检索返回数                                    
    bm25_top_k: 20          # BM25 返回数
    final_top_k: 5          # 最终返回数                                        
    use_multi_query: true   # 是否启用                      
                                                                                
  chunk:                                                    
    min_chars: 200
    max_chars: 500
    overlap_chars: 50                                                           
   
  llm:                                                                          
    api_key: ""            # MiniMax API Key                
    base_url: "https://api.minimaxi.com/anthropic"
    model: "MiniMax-M2.7"
                                                                                
  ---
  注意事项                                                                      
                                                            
  1. 首次使用: 首次初始化会下载 BGE-m3 模型（约 1GB），请确保网络畅通
  2. Qdrant: 必须先启动 Qdrant 服务                                             
  3. Multi-Query: 如不使用 LLM，可设置 use_multi_query: false 禁用              
  4. API Key: MiniMax API Key 可通过环境变量或配置文件设置