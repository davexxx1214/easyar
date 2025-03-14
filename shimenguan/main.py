# -*- coding: utf-8 -*-
from flask import Flask, Response, stream_with_context, request
from zhipuai import ZhipuAI
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import csv
import os

app = Flask(__name__)

# 定义全局变量来存储敏感词集合和POI映射
BANWORDS = set()
POI_MAPPING = {}
POI_LIST = []

# 加载POI数据
def load_poi_data():
    global POI_MAPPING, POI_LIST
    try:
        with open('poi.csv', 'r', encoding='utf-8') as file:
            reader = csv.reader(file)
            next(reader, None)  # 跳过标题行
            for row in reader:
                if row and len(row) > 0:
                    standard_name = row[0].strip()
                    POI_LIST.append(standard_name)
                    # 将标准名称映射到自身
                    POI_MAPPING[standard_name] = standard_name
                    # 将所有别名映射到标准名称
                    for alias in row[1:]:
                        if alias.strip():
                            POI_MAPPING[alias.strip()] = standard_name
    except Exception as e:
        app.logger.error("Failed to load POI data: %s", str(e))
        raise e

# 程序启动时加载敏感词
def load_banwords():
    global BANWORDS
    try:
        with open('banwords.txt', 'r', encoding='utf-8') as file:
            BANWORDS = {line.strip() for line in file if line.strip()}
    except Exception as e:
        app.logger.error("Failed to load banned words list: %s", str(e))
        raise e

# 初始化数据
load_banwords()
load_poi_data()

# 从config.json文件中读取配置信息
with open('config.json', 'r', encoding='utf-8') as config_file:
    config = json.load(config_file)
    
# 从配置信息中提取特定配置并赋值给变量
api_key = config['api_key']
knowledge_id = config['knowledge_id']
auth_keys = config['auth_keys']
default_prompt = config['default_prompt']
nav_prompt = config['nav_prompt']
model = config['model']  # 从配置中读取模型名称
rejection_message = config['rejection_message']  # 从配置中读取拒绝回答的消息

# 更新提示词中的POI列表
def update_prompts():
    global default_prompt, nav_prompt
    
    # 更新default_prompt中的POI列表
    poi_list_str = ", ".join([f'"{poi}"' for poi in POI_LIST])
    default_prompt = default_prompt.replace("以下是正确的完整地点列表：", f"以下是正确的完整地点列表：{poi_list_str}。")
    
    # 更新nav_prompt中的POI列表
    nav_prompt = nav_prompt.replace("这是可以作为目的地的完整地点列表。地点列表：", f"这是可以作为目的地的完整地点列表。地点列表：{poi_list_str}。")
    
    # 更新别名映射信息
    alias_mapping_str = " ".join([f"{alias}={standard}" for alias, standard in POI_MAPPING.items() if alias != standard])
    default_prompt = default_prompt.replace("重要别名对应：", f"重要别名对应：{alias_mapping_str}。")
    nav_prompt = nav_prompt.replace("重要别名对应：", f"重要别名对应：{alias_mapping_str}。")

# 更新提示词
update_prompts()

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

# 检查查询是否包含敏感词
def contains_banned_words(query):
    return any(banword in query for banword in BANWORDS)

# 获取当前时间格式化字符串
def get_formatted_time():
    tz = ZoneInfo('Asia/Shanghai')
    now = datetime.now(tz)
    return now.strftime("(现在时间是%H点%M分)")

@app.route('/bot', methods=['POST'])
def bot_endpoint():
    """
    新增 /bot 接口：
    - 验证请求头中的auth-key（同query_endpoint）
    - 接收JSON参数：query（用户的问题）和 stream（是否启用流式返回）
    - 从 config.json 中获取 app_id 配置（需在配置中添加 app_id）
    - 调用大模型接口，返回用户回答
    """
    # 验证请求头中的授权key
    auth_key = request.headers.get('auth-key')
    if not valid_auth_key(auth_key):
        return {'detail': 'Invalid key'}, 401

    # 获取请求体中的JSON数据
    data = request.get_json()
    if not data or 'query' not in data:
        return {'detail': 'Missing query parameter'}, 400

    query = data['query']
    stream = data.get('stream', False)

    formatted_time = get_formatted_time()
    if "路线" in query or "目前" in query or "现在" in query or "当前" in query or "时间" in query or "几点" in query:
        query = f"{query}{formatted_time}"
        print(query)

    # 从 config.json 中读取 app_id 配置，确保配置中包含 app_id
    app_id = config.get("app_id")
    if not app_id:
        return {'detail': 'config中缺少app_id配置'}, 500

    # 检查敏感词
    if contains_banned_words(query):
        print("检测到敏感词，直接返回!")
        return rejection_message
    # 构造调用大模型接口的请求
    url = 'https://open.bigmodel.cn/api/llm-application/open/v3/application/invoke'
    headers_bigmodel = {
        'Authorization': api_key,
        'Content-Type': 'application/json'
    }
    payload = {
        "app_id": app_id,
        "stream": stream,
        "send_log_event": False,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "value": query,
                        "type": "input"
                    }
                ]
            }
        ]
    }

    try:
        if not stream:
            r = requests.post(url, headers=headers_bigmodel, json=payload)
            if r.status_code != 200:
                return {'detail': r.text}, r.status_code
            r_json = r.json()
            # 从返回的 JSON 中提取答案，其中答案位于 choices[0].messages.content.msg 字段
            answer = r_json.get("choices", [{}])[0].get("messages", {}).get("content", {}).get("msg", "")
            return answer
        else:
            # 流式返回
            r = requests.post(url, headers=headers_bigmodel, json=payload, stream=True)
            def generate():
                for line in r.iter_lines():
                    if line:
                        try:
                            data_line = json.loads(line.decode("utf-8"))
                            chunk = data_line.get("choices", [{}])[0].get("messages", {}).get("content", {}).get("msg", "")
                            yield chunk
                        except Exception:
                            yield ""
            return Response(stream_with_context(generate()), content_type='text/event-stream')
    except Exception as e:
        return {'detail': str(e)}, 500

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
    prompt = default_prompt
    query = data.get('query', None)
    stream = data.get('stream', False)
    
    print(f'query = {query}')
    print(f'stream = {stream}')
    
    # 检查查询是否包含敏感词
    if contains_banned_words(query):
        print("检测到敏感词，直接返回!")
        return rejection_message
    
    # 获取当前时间
    formatted_time = get_formatted_time()
    prompt_with_time = f"{prompt}{formatted_time}"
    if "路线" in query or "目前" in query or "现在" in query or "当前" in query or "时间" in query or "几点" in query:
        query = f"{query}{formatted_time}"
        print(query)
    # 创建消息列表和工具配置
    messages = [
        {"role": "system", "content": prompt_with_time},
        {"role": "user", "content": query}
    ]
    tools_list = [
        {
            "type": "retrieval",
            "retrieval": {
                "knowledge_id": config['knowledge_id'],
                "prompt_template": ("请优先从景区知识库里\n\"\"\"\n{{knowledge}}\n\"\"\"\n中找问题\n\"\"\"\n{{question}}\n\"\"\"\n的答案，找到答案就参考知识库中语句回答问题，"
                                            "找不到答案就用自身知识回答。\n不要复述问题，直接开始回答。你只能回答跟景区旅游相关的问题，不要回答其他方面的问题。"
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
    
@app.route('/nav', methods=['POST'])
def query_nav_endpoint():
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
    prompt = nav_prompt
    query = data.get('query', None)
    print(f'query = {query}')
    
    # 检查查询是否包含敏感词
    if contains_banned_words(query):
        print("检测到敏感词，直接返回!")
        return '{\"NEEDNAV\":\"N\",\"POI\":\"NONE\"}'
    
    print(f'prompt = {prompt}')

    # 假设client.chat.completions.create是有效的调用代码
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": query}
            ]
        )
        # 假设response.choices[0].message.content返回有效答案
        anwser = response.choices[0].message.content
        print(anwser)
        return anwser
    except Exception as e:
        return {'detail': str(e)}, 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9000)