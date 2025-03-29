from flask import Flask, Response, stream_with_context, request
from zhipuai import ZhipuAI
import json

app = Flask(__name__)

default_prompt = "你是票付通的数字人，名字是小飘。旨在回答并解决用户票付通相关的问题。你需要用简短的语言回答用户的问题。你必须用纯文本回复，不能使用带*的markdown格式。"

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
        return {'detail': 'Invalid key'}, 401

    # 获取请求体的JSON数据
    data = request.get_json()
    print(data)
    if not data or 'query' not in data:
        return {'detail': 'Missing query parameter'}, 400
    
    # 解析请求体中的数据
    model = data.get('model', 'glm-4')
    prompt = data.get('prompt', default_prompt)
    query = data.get('query', None)
    stream = data.get('stream', False)

    print(f'query = {query}')
    print(f'stream = {stream}')
    
    # 检查查询是否包含敏感词
    if any(banword in query for banword in BANWORDS):
        print("检测到敏感词，直接返回!")
        return "对不起，我无法回答这个问题。"
    
    # 创建消息列表和工具配置
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": query}
    ]
    tools_list = [
        {
            "type": "retrieval",
            "retrieval": {
                "knowledge_id": config['knowledge_id'],
                "prompt_template": (
                    "从你的知识库\n\"\"\"\n{{knowledge}}\n\"\"\"\n中找问题\n\"\"\"\n{{question}}\n\"\"\"\n的答案，并参考知识库进行回答，"
                                            "不要让用户知道有知识库的存在。知识库里找不到答案，就直接用自身知识回答。\n不要复述问题，直接开始回答。"
                )
            }
        }
    ]
    
    try:
        def generate():
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools_list,
                stream=True
            )
            for chunk in response:
                print(f'chunk = {chunk.choices[0].delta.content}')
                yield chunk.choices[0].delta.content

        if not stream:
            # 直接返回第一个生成的响应，而不使用stream。
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools_list,
            )
            answer = response.choices[0].message.content
            print(f'answer = {answer}')
            return answer
        else:
            # 对于流请求，返回生成器的输出。
            return Response(stream_with_context(generate()), content_type='text/event-stream')
    except Exception as e:
        return {'detail': str(e)}, 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9000)