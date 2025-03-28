# -*- coding: utf-8 -*-
from flask import Flask, Response, stream_with_context, request
import json
import requests
import os 
import traceback
import sys 
import logging

# 设置 werkzeug 日志级别，减少请求日志
logging.getLogger('werkzeug').setLevel(logging.WARNING)

app = Flask(__name__)

# 定义全局变量
BANWORDS = set()
CONFIG = {}
COZE_API_URL = "https://api.coze.cn/v3/chat"

# 加载配置
def load_config():
    """从 config.json 加载配置"""
    global CONFIG
    try:
        with open('config.json', 'r', encoding='utf-8') as config_file:
            CONFIG = json.load(config_file)
            # 检查必要的配置是否存在
            required_keys = ['coze_key', 'auth_keys', 'fast_bot_id', 'thinking_bot_id', 'rejection_message']
            for key in required_keys:
                if key not in CONFIG:
                    raise ValueError(f"Config file 'config.json' is missing required key: {key}")
            if not isinstance(CONFIG.get('auth_keys'), list):
                 raise ValueError("Config key 'auth_keys' must be a list")
            print("INFO: Configuration loaded successfully.", file=sys.stderr)

    except FileNotFoundError:
        print("ERROR: Config file 'config.json' not found.", file=sys.stderr)
        raise
    except json.JSONDecodeError:
        print("ERROR: Error decoding 'config.json'. Make sure it's valid JSON.", file=sys.stderr)
        raise
    except ValueError as ve:
        print(f"ERROR: {str(ve)}", file=sys.stderr)
        raise
    except Exception as e:
        print(f"ERROR: Failed to load config: {str(e)}", file=sys.stderr)
        raise

# 加载敏感词
def load_banwords():
    """从 banwords.txt 加载敏感词"""
    global BANWORDS
    try:
        # 检查 banwords.txt 是否存在
        if not os.path.exists('banwords.txt'):
             print("WARNING: Banwords file 'banwords.txt' not found. No banwords will be loaded.", file=sys.stderr)
             BANWORDS = set()
             return

        with open('banwords.txt', 'r', encoding='utf-8') as file:
            BANWORDS = {line.strip() for line in file if line.strip()}
        print(f"INFO: Loaded {len(BANWORDS)} banwords.", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: Failed to load banned words list: {str(e)}", file=sys.stderr)
        # 选择是继续运行（没有敏感词过滤）而不是抛出错误停止服务
        BANWORDS = set() # 确保 BANWORDS 是一个集合

# 初始化数据
try:
    load_config()
    load_banwords()
except Exception as e:
    # 如果初始化失败，退出程序
    print(f"CRITICAL: Initialization failed due to: {e}. Application will exit.", file=sys.stderr)
    exit(1) # 关键配置或文件加载失败，直接退出


# --- 辅助函数 ---

def valid_auth_key(auth_key):
    """验证请求头中的 auth-key"""
    if not auth_key or not auth_key.startswith('Bearer '):
        return False
    key = auth_key.split(' ')[1]
    # 确保 CONFIG['auth_keys'] 存在且是列表
    return key in CONFIG.get('auth_keys', [])

def contains_banned_words(query):
    """检查查询是否包含敏感词"""
    if not isinstance(query, str): # 确保 query 是字符串
        return False
    return any(banword in query for banword in BANWORDS)

def call_coze_stream(bot_id: str, query: str, api_key: str):
    """调用 Coze API 并返回流式响应"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream"
    }
    payload = {
        "bot_id": bot_id,
        "user_id": "api_user",
        "stream": True,
        "auto_save_history": False,
        "additional_messages": [
            {
                "role": "user",
                "content": query,
                "content_type": "text"
            }
        ]
    }

    try:
        response = requests.post(COZE_API_URL, headers=headers, json=payload, stream=True, timeout=(10, 60))
        response.raise_for_status()

        current_event = None
        answer_started = False
        reasoning_started = False
        reasoning_ended = False

        print(f"\n--- Response stream from bot {bot_id} ---", file=sys.stderr)
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')

                if decoded_line.startswith("event:"):
                    current_event = decoded_line[len("event:"):].strip()
                elif decoded_line.startswith("data:"):
                    data_str = decoded_line[len("data:"):].strip()

                    if data_str == "[DONE]":
                        if reasoning_started and not reasoning_ended:
                            print("</think>", file=sys.stderr)
                            yield "</think>"
                            reasoning_ended = True
                        print(f"\n--- End of stream from bot {bot_id} ---", file=sys.stderr)
                        print(f"INFO: Coze stream finished for bot {bot_id}.", file=sys.stderr)
                        break

                    try:
                        data = json.loads(data_str)

                        if current_event == "conversation.message.delta":
                            role = data.get("role")
                            msg_type = data.get("type")
                            content_part = data.get("content", "")
                            reasoning_content = data.get("reasoning_content")

                            if reasoning_content:
                                if not reasoning_started:
                                    print("<think>", file=sys.stderr)
                                    yield "<think>"
                                    reasoning_started = True
                                
                                print(f"{reasoning_content}", file=sys.stderr)
                                yield reasoning_content

                            if role == "assistant" and msg_type == "answer" and content_part:
                                if reasoning_started and not reasoning_ended:
                                    print("</think>", file=sys.stderr)
                                    yield "</think>"
                                    reasoning_ended = True
                                
                                if not answer_started:
                                    answer_started = True
                                print(content_part, file=sys.stderr)
                                yield content_part

                        elif current_event == "error":
                            error_message = data.get('error', {}).get('message', 'Unknown Coze API error')
                            error_code = data.get('error', {}).get('code', 'N/A')
                            print(f"\nERROR: Coze API Error Event received: Code={error_code}, Message={error_message}", file=sys.stderr)
                            yield f"[ERROR: Coze API Error - {error_message}]"
                            break

                    except json.JSONDecodeError:
                        print(f"\nWARNING: Could not decode JSON from Coze data line: {data_str}", file=sys.stderr)
                        continue
                    except Exception as e:
                         print(f"\nERROR: Error processing Coze data chunk: {e}, data: {data_str}", file=sys.stderr)
                         continue
        print("", file=sys.stderr)

    except requests.exceptions.Timeout as e:
        print(f"ERROR: Request to Coze API timed out for bot {bot_id}: {e}", file=sys.stderr)
        yield "[ERROR: Request to Coze API timed out]"
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: Coze API returned HTTP error for bot {bot_id}: {e.response.status_code} {e.response.reason}. Response: {e.response.text}", file=sys.stderr)
        yield f"[ERROR: Coze API request failed - HTTP {e.response.status_code}]"
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Network error connecting to Coze API for bot {bot_id}: {e}", file=sys.stderr)
        yield f"[ERROR: Failed to connect to Coze API - {e}]"
    except Exception as e:
        print(f"ERROR: Unexpected error during Coze stream processing for bot {bot_id}: {e}\n{traceback.format_exc()}", file=sys.stderr)
        yield f"[ERROR: Internal server error during stream processing]"


@app.route('/fast', methods=['POST'])
def fast_endpoint():
    """Coze 快速响应 Bot 接口 (流式)"""
    # 1. 认证
    auth_key = request.headers.get('auth-key')
    if not valid_auth_key(auth_key):
        print(f"WARNING: Invalid auth key received from {request.remote_addr}.", file=sys.stderr)
        # 返回 JSON 错误和 401 状态码
        return {"error": CONFIG.get('rejection_message', "Unauthorized access")}, 401

    # 2. 获取 Query (确保是 JSON 请求)
    if not request.is_json:
        print("ERROR: Request content type is not application/json.", file=sys.stderr)
        return {"error": "Request must be JSON"}, 415 # Unsupported Media Type

    data = request.get_json()
    if not data or 'query' not in data or not isinstance(data['query'], str):
        print("ERROR: Missing or invalid 'query' parameter in JSON request.", file=sys.stderr)
        return {"error": "Missing or invalid 'query' parameter"}, 400 # Bad Request
    query = data['query']

    # 3. 敏感词检查
    if contains_banned_words(query):
        print(f"INFO: Banned word detected in query from {request.remote_addr}. Query: '{query[:50]}...'", file=sys.stderr)
        # 返回纯文本拒绝消息 和 200 OK (按原设计)
        return Response(CONFIG.get('rejection_message', "Query contains restricted content."), mimetype='text/plain', status=200)

    # 4. 调用 Coze 并流式返回
    bot_id = CONFIG.get('fast_bot_id')
    api_key = CONFIG.get('coze_key')

    if not bot_id or not api_key:
         print("ERROR: Server configuration error: 'fast_bot_id' or 'coze_key' is missing.", file=sys.stderr)
         return {"error": "Server configuration error"}, 500 # Internal Server Error

    print(f"INFO: Calling fast bot ({bot_id}) for {request.remote_addr}. Query: '{query[:50]}...'", file=sys.stderr)
    generator = call_coze_stream(bot_id, query, api_key)
    headers = {
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'  # 禁用Nginx缓冲
    }
    # 使用 text/event-stream 类型返回 SSE 流
    return Response(stream_with_context(generator), 
                   content_type='text/event-stream',
                   headers=headers)

@app.route('/thinking', methods=['POST'])
def thinking_endpoint():
    """Coze 深度思考 Bot 接口 (流式)"""
     # 1. 认证
    auth_key = request.headers.get('auth-key')
    if not valid_auth_key(auth_key):
        print(f"WARNING: Invalid auth key received from {request.remote_addr}.", file=sys.stderr)
        return {"error": CONFIG.get('rejection_message', "Unauthorized access")}, 401

    # 2. 获取 Query
    if not request.is_json:
        print("ERROR: Request content type is not application/json.", file=sys.stderr)
        return {"error": "Request must be JSON"}, 415

    data = request.get_json()
    if not data or 'query' not in data or not isinstance(data['query'], str):
        print("ERROR: Missing or invalid 'query' parameter in JSON request.", file=sys.stderr)
        return {"error": "Missing or invalid 'query' parameter"}, 400
    query = data['query']

    # 3. 敏感词检查
    if contains_banned_words(query):
        print(f"INFO: Banned word detected in query from {request.remote_addr}. Query: '{query[:50]}...'", file=sys.stderr)
        return Response(CONFIG.get('rejection_message', "Query contains restricted content."), mimetype='text/plain', status=200)

    # 4. 调用 Coze 并流式返回
    bot_id = CONFIG.get('thinking_bot_id')
    api_key = CONFIG.get('coze_key')

    if not bot_id or not api_key:
        print("ERROR: Server configuration error: 'thinking_bot_id' or 'coze_key' is missing.", file=sys.stderr)
        return {"error": "Server configuration error"}, 500

    print(f"INFO: Calling thinking bot ({bot_id}) for {request.remote_addr}. Query: '{query[:50]}...'", file=sys.stderr)
    generator = call_coze_stream(bot_id, query, api_key)
    headers = {
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'  # 禁用Nginx缓冲
    }
    return Response(stream_with_context(generator), 
                   content_type='text/event-stream',
                   headers=headers)

# --- 启动服务 ---
if __name__ == '__main__':
    # 从环境变量获取端口，默认为 9000
    port = int(os.environ.get('PORT', 9000))
    # 生产环境推荐使用 Waitress, Gunicorn 或 uWSGI
    # 例如: waitress-serve --host=0.0.0.0 --port=9000 coze.main:app
    # 这里仍然使用 Flask 自带服务器，但关闭 debug 模式
    # 添加一个启动信息
    print(f"INFO: Starting Flask server on host 0.0.0.0, port {port}...", file=sys.stderr)
    app.run(host='0.0.0.0', port=port, debug=False)