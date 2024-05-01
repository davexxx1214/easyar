# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify
from zhipuai import ZhipuAI
import json
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)

default_prompt = "你的名字叫小关，你是中国云南大理石门关景区的智能客服，旨在回答并解决人们关于石门关景区相关的问题。你需要用简短的语言回答用户的问题。你必须用纯文本回复，不能使用带*的markdown格式。"

# 定义全局变量来存储敏感词集合
BANWORDS = set()

# 程序启动时加载敏感词
try:
    with open('banwords.txt', 'r', encoding='utf-8') as file:
        BANWORDS = {line.strip() for line in file if line.strip()}
except Exception as e:
    app.logger.error("Failed to load banned words list: %s", str(e))
    raise e

# 从config.json文件中读取配置信息
with open('config.json', 'r') as config_file:
    config = json.load(config_file)
    
# 从配置信息中提取特定配置并赋值给变量
api_key = config['api_key']
knowledge_id = config['knowledge_id']
auth_keys = config['auth_keys']

# 使用配置信息初始化ZhipuAI的客户端
client = ZhipuAI(api_key=api_key)

def valid_auth_key(auth_key):
    """验证授权key"""
    if not auth_key.startswith('Bearer '):
        return False
    
    key = auth_key.split(' ')[1]
    # 假设auth_keys是一个有效的授权keys列表
    if key in auth_keys:
        return True
    else:
        return False

@app.route('/', methods=['POST'])
def query_endpoint():
    # 获取请求头中的授权key
    auth_key = request.headers.get('auth-key')

    if not valid_auth_key(auth_key):
        return jsonify({'detail': 'Invalid key'}), 401

    # 获取请求体的JSON数据
    data = request.get_json()
    print(data)
    if not data or 'query' not in data:
        return jsonify({'detail': 'Missing query parameter'}), 400
    
    # 解析请求体中的数据
    model = data.get('model', 'glm-4')
    prompt = default_prompt
    query = data.get('query', None)
    stream = data.get('stream', False)
    
    # 检查查询是否包含敏感词
    if any(banword in query for banword in BANWORDS):
        print("检测到敏感词，直接返回!")
        return jsonify("对不起，这个问题我无法回答。如果您有任何其他关于石门关景区的问题或者需要帮助，请告诉我。")
    
    # 设置时区为UTC+8
    tz = ZoneInfo('Asia/Shanghai')

    # 获取当前UTC+8时区的时间
    now = datetime.now(tz)
    formatted_time = now.strftime("(现在时间是%H点%M分)")
    prompt_with_time = f"{prompt}{formatted_time}"
    if "路线" in query or "目前" in query or "现在" in query or "当前" in query or "时间" in query or "几点" in query:
        query = f"{query}{formatted_time}"
        print(query)
    
    # 假设client.chat.completions.create是有效的调用代码
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt_with_time},
                {"role": "user", "content": query}
            ],
            tools=[
                {
                    "type": "retrieval",
                    "retrieval": {
                        "knowledge_id": knowledge_id,
                        "prompt_template": "请优先从景区知识库里\n\"\"\"\n{{knowledge}}\n\"\"\"\n中找问题\n\"\"\"\n{{question}}\n\"\"\"\n的答案，找到答案就参考知识库中语句回答问题，"
                                            "找不到答案就用自身知识回答。\n不要复述问题，直接开始回答。你只能回答跟景区旅游相关的问题，不要回答其他方面的问题。"
                    }
                }
            ],
            stream=stream,
        )
        # 假设response.choices[0].message.content返回有效答案
        anwser = response.choices[0].message.content
        print(anwser)
        return jsonify(anwser)
    except Exception as e:
        return jsonify({'detail': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9000)