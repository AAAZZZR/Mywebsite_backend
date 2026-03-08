import os
from dotenv import load_dotenv
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter
# OpenAI 相關
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
# Google Gemini 相關
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
# 向量資料庫與 Chain
from langchain_chroma import Chroma
from langchain_classic.chains import create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate
from prompt import HR_SYSTEM_PROMPT,HR_EXAMPLE,CLIENT_SYSTEM_PROMPT,CLIENT_EXAMPLE

# 載入 .env
load_dotenv()

LLM_PROVIDER = "gemini" 

DATA_PATH = "./data"
# 自動根據模型區分資料庫路徑，避免向量維度衝突
DB_PATH = f"./vector_db_{LLM_PROVIDER}"

def get_embeddings():
    """根據設定回傳對應的 Embedding 模型"""
    if LLM_PROVIDER == "gemini":
        # 使用 Google 的 Embedding 模型
        return GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")
    else:
        # 使用 OpenAI 的 Embedding 模型
        return OpenAIEmbeddings()

# rag.py

def get_llm():
    """根據設定回傳對應的 LLM 模型"""
    if LLM_PROVIDER == "gemini":
        return ChatGoogleGenerativeAI(
            model="gemini-2.5-flash", 
            temperature=0.3,
            convert_system_message_to_human=True,
            streaming=True  # <--- 加入這一行，明確告訴模型我們要串流
        )
    else:
        # OpenAI 也一樣建議加上
        return ChatOpenAI(
            model="gpt-4o-mini", 
            temperature=0.7, 
            streaming=True
        )

def initialize_vector_db():
    """初始化向量資料庫：讀取資料 -> 切分 -> 存入 ChromaDB"""
    if not os.path.exists(DATA_PATH):
        os.makedirs(DATA_PATH)
        print(f"⚠️ 警告: {DATA_PATH} 資料夾不存在，已自動建立。請放入 .txt 檔案。")
        return None

   
    loader = DirectoryLoader(DATA_PATH, glob="**/*.md", loader_cls=TextLoader,loader_kwargs={'encoding': 'utf-8'})
    docs = loader.load()
    
    if not docs:
        print("Warn No doc found")
        return None

    headers_to_split_on = [
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
    ]

    # 2. 切分文件
    md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
    

    all_splits = []
    
    for doc in docs:
        # md_splitter 會把標題資訊放入 metadata
        splits = md_splitter.split_text(doc.page_content)
        
        all_splits.extend(splits)
    
    vectorstore = Chroma.from_documents(
        documents=all_splits, 
        embedding=get_embeddings(), 
        persist_directory=DB_PATH
    )
    print(f"✅ [{LLM_PROVIDER} is established path:{DB_PATH}")
    return vectorstore

def get_rag_chain(mode='hr'):
    """建立 RAG 問答鏈"""
    
    # 檢查是否已經有對應模型的向量資料庫
    if not os.path.exists(DB_PATH):
        print(f"找不到 {LLM_PROVIDER} 的現有資料庫，嘗試初始化...")
        vectorstore = initialize_vector_db()
        if not vectorstore:
            return None
    else:
        # 讀取現有資料庫 (必須用同樣的 embedding model 讀取)
        vectorstore = Chroma(
            persist_directory=DB_PATH, 
            embedding_function=get_embeddings()
        )

    retriever = vectorstore.as_retriever(
        search_type="similarity_score_threshold",
        search_kwargs={"score_threshold": 0.6, "k": 3})

    # 取得 LLM (根據全域設定)
    llm = get_llm()

    if mode == "client":
        # 使用接案模式設定
        sys_prompt = CLIENT_SYSTEM_PROMPT
        example_human = CLIENT_EXAMPLE["human"]
        example_assistant = CLIENT_EXAMPLE["assistant"]
        print(" Mode: Client (Business Representative)")
    else:
        # 預設使用 HR 模式設定
        sys_prompt = HR_SYSTEM_PROMPT
        example_human = HR_EXAMPLE["human"]
        example_assistant = HR_EXAMPLE["assistant"]
        print("Mode: HR (Interview Assistant)")
    
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", sys_prompt),
            
            ("human", example_human),
            ("assistant", example_assistant),
           
            ("human", "{input}"),
        ]
    )

    question_answer_chain = create_stuff_documents_chain(llm, prompt)
    rag_chain = create_retrieval_chain(retriever, question_answer_chain)

    return rag_chain