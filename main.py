from typing import Optional
from fastapi import FastAPI, HTTPException, Body,Header, Depends

import json
import uvicorn
from zhipuai import ZhipuAI

# 从配置文件中读取配置
with open('config.json', 'r') as config_file:
    config = json.load(config_file)

# 创建客户端实例
client = ZhipuAI(api_key=config["api_key"])
knowledge_id = config["knowledge_id"]

app = FastAPI()
auth_keys = []

# 定义全局变量来存储敏感词集合
BANWORDS = set()


with open('auth_keys.txt', 'r') as f:
    auth_keys = [line.strip() for line in f.readlines()]

def valid_auth_key(auth_key: str = Header(...)):  # Use depends to validate and count auth_key
    if not auth_key.startswith('Bearer '):
        raise HTTPException(status_code=400, detail='Invalid token schema')
    
    key = auth_key.split(' ')[1]
    if key not in auth_keys:
        raise HTTPException(status_code=401, detail='Invalid key')

    return key

@app.on_event("startup")
async def load_banwords():
    try:
        with open('banwords.txt', 'r', encoding='utf-8') as file:
            global BANWORDS
            BANWORDS = set(line.strip() for line in file.readlines())
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to load banned words list.")


# 提供的默认提示，如果没有从请求中收到 prompt
default_prompt = ("你是票付通的数字人，名字是小飘。旨在回答并解决用户票付通相关的问题。你需要用简短的语言回答用户的问题。请用纯文本回复，不要用markdown格式回复。")

@app.post("/query")
async def query_endpoint(key: str = Depends(valid_auth_key),
    model: str = Body(default="glm-4", embed=True), 
    prompt: Optional[str] = Body(default=default_prompt, embed=True), 
    query: str = Body(..., embed=True)  # '...' 意味着这是一个必填字段
):
    if any(banword in query for banword in BANWORDS):
        return "对不起，我无法回答这个问题。"

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system", 
                    "content": prompt
                },
                {
                    "role": "user", 
                    "content": query
                }
            ],
            tools=[
                {
                    "type": "retrieval",
                    "retrieval": {
                        "knowledge_id": knowledge_id,
                        "prompt_template": ("从票付通的知识库\n\"\"\"\n{{knowledge}}\n\"\"\"\n中找问题\n\"\"\"\n{{question}}\n\"\"\"\n的答案，并参考知识库进行回答，"
                                            "找不到答案就用自身知识回答。\n不要复述问题，直接开始回答。")
                    }
                }
            ],
        )
        return response.choices[0].message.content
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3000)