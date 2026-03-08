import os
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from rag import get_rag_chain, initialize_vector_db

# 載入環境變數
load_dotenv()

app = FastAPI()

# --- CORS 設定 ---
# 這讓你的 Next.js (localhost:3000) 可以呼叫這個後端 (localhost:8000)
origins = [
    "http://localhost",
    "http://localhost:3000",
    "http://3.25.200.182",
    "https://leverag.xyz",
]

app.add_middleware(
    CORSMiddleware,
    # 改成讀取上面的清單，而不是 ["*"]
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 定義請求格式 ---
class ChatRequest(BaseModel):
    query: str
    mode: str = "hr"
    #token: str  

# --- 全域變數 ---
rag_chains = {
    "hr": None,
    "client": None
}
CLOUDFLARE_SECRET_KEY = os.getenv("CLOUDFLARE_SECRET_KEY")

@app.on_event("startup")
async def startup_event():
    """伺服器啟動時執行：預先初始化兩種模式的 RAG"""
    global rag_chains
    try:
        print("⚙️ Initializing RAG Chains...")
        
        # 初始化 HR 模式
        rag_chains["hr"] = get_rag_chain(mode='hr')
        print("✅ HR Mode: Ready")

        # 初始化 Client 模式
        rag_chains["client"] = get_rag_chain(mode='client')
        print("✅ Client Mode: Ready")
        
        if not rag_chains["hr"] or not rag_chains["client"]:
            print("⚠️ Warning: One or more chains failed to load.")
            
    except Exception as e:
        print(f"❌ RAG Initialization Failed: {e}")

async def verify_turnstile(token: str) -> bool:
    """驗證 Cloudflare Turnstile Token"""
    url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    data = {
        "secret": CLOUDFLARE_SECRET_KEY,
        "response": token
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=data)
        result = response.json()
        return result.get("success", False)

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    """聊天接口：支援模式切換與串流輸出"""
    
    # Cloudflare 驗證邏輯 (暫時註解)
    # if CLOUDFLARE_SECRET_KEY:
    #     is_human = await verify_turnstile(request.token)
    #     if not is_human:
    #         raise HTTPException(status_code=403, detail="Cloudflare verification failed.")

    # --- 3. 根據請求選擇對應的 Chain ---
    # 如果 request.mode 是 'client' 就拿 client chain，否則預設拿 hr chain
    selected_mode = request.mode if request.mode in ["client", "hr"] else "hr"
    active_chain = rag_chains.get(selected_mode)

    if not active_chain:
        # 如果該模式的 Chain 沒跑起來，嘗試用 HR 當備案，真的都沒有才報錯
        active_chain = rag_chains.get("hr")
        if not active_chain:
            raise HTTPException(status_code=503, detail="RAG system is currently unavailable.")

    # 4. 定義產生器
    async def generate_response():
        try:
            # 使用選定的 Chain 進行問答
            async for chunk in active_chain.astream({"input": request.query}):
                
                if "answer" in chunk:
                    content = chunk["answer"]
                    if content:
                        yield content 

        except Exception as e:
            print(f"Stream Error: {e}")
            yield f"Error: {str(e)}"

    print(f"📩 Query: {request.query} | 🎭 Mode: {selected_mode}")
    
    return StreamingResponse(generate_response(), media_type="text/event-stream")

@app.get("/api/")
def health_check():
    return {"status": "ok", "message": "Rudy's Backend is running!"}

# 啟動指令: uvicorn main:app --reload --port 8000