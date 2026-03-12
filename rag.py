import os
import hashlib
import glob as glob_mod
import shutil
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
HASH_FILE = os.path.join(DB_PATH, ".data_hash")


def _compute_data_hash():
    """計算 data/ 資料夾內所有 .md 檔案的內容 hash，用來偵測資料是否有變動"""
    h = hashlib.md5()
    md_files = sorted(glob_mod.glob(os.path.join(DATA_PATH, "**/*.md"), recursive=True))
    for fpath in md_files:
        with open(fpath, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def _data_has_changed():
    """比對目前 data hash 與上次建 DB 時的 hash，不同就代表資料有更新"""
    if not os.path.exists(HASH_FILE):
        return True
    with open(HASH_FILE, "r") as f:
        old_hash = f.read().strip()
    return _compute_data_hash() != old_hash


def _save_data_hash():
    """儲存目前的 data hash"""
    os.makedirs(DB_PATH, exist_ok=True)
    with open(HASH_FILE, "w") as f:
        f.write(_compute_data_hash())

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
    _save_data_hash()
    print(f"✅ [{LLM_PROVIDER}] vector DB built with {len(all_splits)} chunks at {DB_PATH}")
    return vectorstore

def get_rag_chain(mode='hr'):
    """建立 RAG 問答鏈"""
    
    # 檢查是否需要重建向量資料庫（不存在 or 資料有更新）
    need_rebuild = not os.path.exists(DB_PATH) or _data_has_changed()

    if need_rebuild:
        if os.path.exists(DB_PATH):
            shutil.rmtree(DB_PATH)
            print(f"🔄 資料有變動，刪除舊 DB: {DB_PATH}")
        else:
            print(f"找不到 {LLM_PROVIDER} 的現有資料庫，嘗試初始化...")
        vectorstore = initialize_vector_db()
        if not vectorstore:
            return None
    else:
        # 資料沒變，讀取現有資料庫
        vectorstore = Chroma(
            persist_directory=DB_PATH,
            embedding_function=get_embeddings()
        )
        print(f"✅ 資料未變動，使用現有 DB: {DB_PATH}")

    retriever = vectorstore.as_retriever(
        search_type="similarity_score_threshold",
        search_kwargs={"score_threshold": 0.5, "k": 3})

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