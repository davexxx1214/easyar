from flask import Flask, Response, stream_with_context, jsonify, request
from zhipuai import ZhipuAI
import json

app = Flask(__name__)

# 定义全局变量来存储敏感词集合
BANWORDS = set()

# 程序启动时加载敏感词
try:
    with open('banwords.txt', 'r', encoding='utf-8') as file:
        BANWORDS = {line.strip() for line in file if line.strip()}
except Exception as e:
    app.logger.error("Failed to load banned words list: %s", str(e))
    raise e

# 创建一个全局变量来存储配置信息
configs = None

def load_configs_from_file():
    # 全局变量声明，表明我们要修改这个全局变量
    global configs
    
    # 只在程序开始时读取一次配置文件
    with open('config.json', 'r', encoding='utf-8') as config_file:
        configs = json.load(config_file)

def get_config(config_name):
    # 检查是否存在指定的配置名
    if config_name in configs:
        # 如果存在，返回找到的配置
        return configs[config_name]
    else:
        # 如果不存在，返回错误标记，例如使用None表示找不到配置
        return None

# 在程序启动的时候，加载所有配置到内存中
load_configs_from_file()

auth_keys = configs['auth_keys']
api_key = configs['api_key']
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
    if not data or 'query' not in data:
        return jsonify({'detail': 'Missing query parameter'}), 400

    # 获取当前有效的配置
    config_name = data.get('config', 'default')
    config = get_config(config_name)
    if config is None:
        return jsonify({'detail': 'Config name not found'}), 404

    # 设置模型和查询
    model = data.get('model', config['model'])
    query = data['query']
    stream = data.get('stream', False)

    # 检查查询是否包含敏感词
    if any(banword in query for banword in BANWORDS):
        return jsonify("对不起，我无法回答这个问题。")

    # 创建消息列表和工具配置
    messages = [
        {"role": "system", "content": config['default_prompt']},
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
                yield chunk.choices[0].delta.content

        if not stream:
            # 直接返回第一个生成的响应，而不使用stream。
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools_list,
            )
            answer = response.choices[0].message.content
            return jsonify(answer)
        else:
            # 对于流请求，返回生成器的输出。
            return Response(stream_with_context(generate()), content_type='text/event-stream')
    except Exception as e:
        return jsonify({'detail': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9000)