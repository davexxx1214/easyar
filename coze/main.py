# -*- coding: utf-8 -*-
from flask import Flask, Response, stream_with_context, request
import json
import requests
import os # 保留 os 用于文件检查

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
                 raise ValueError(f"Config key 'auth_keys' must be a list")
            print("INFO: Configuration loaded successfully.") # Changed from app.logger.info

    except FileNotFoundError:
        print("ERROR: Config file 'config.json' not found.") # Changed from app.logger.error
        raise
    except json.JSONDecodeError:
        print("ERROR: Error decoding 'config.json'. Make sure it's valid JSON.") # Changed from app.logger.error
        raise
    except ValueError as ve:
        print(f"ERROR: {str(ve)}") # Changed from app.logger.error
        raise
    except Exception as e:
        print(f"ERROR: Failed to load config: {str(e)}") # Changed from app.logger.error
        raise

# 加载敏感词
def load_banwords():
    """从 banwords.txt 加载敏感词"""
    global BANWORDS
    try:
        # 检查 banwords.txt 是否存在
        if not os.path.exists('banwords.txt'):
             print("WARNING: Banwords file 'banwords.txt' not found. No banwords will be loaded.") # Changed from app.logger.warning
             BANWORDS = set()
             return

        with open('banwords.txt', 'r', encoding='utf-8') as file:
            BANWORDS = {line.strip() for line in file if line.strip()}
        print(f"INFO: Loaded {len(BANWORDS)} banwords.") # Changed from app.logger.info
    except Exception as e:
        print(f"ERROR: Failed to load banned words list: {str(e)}") # Changed from app.logger.error
        # 选择是继续运行（没有敏感词过滤）还是抛出错误停止服务
        # 这里选择继续运行，但记录错误
        BANWORDS = set() # 确保 BANWORDS 是一个集合

# 初始化数据
try:
    load_config()
    load_banwords() # Corrected: load_banwords should be inside the try block if its failure should stop the app
except Exception as e:
    # 如果初始化失败，可以选择退出程序或进行其他处理
    print(f"CRITICAL: Initialization failed due to: {e}. Application will exit.") # Changed from app.logger.critical
    # 在生产环境中可能需要更优雅的退出方式
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
    # ... (headers and payload definition remain the same) ...
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream"
    }
    payload = {
        "bot_id": bot_id,
        "user_id": "api_user", # 使用固定的 user_id
        "stream": True,
        "auto_save_history": False, # API 调用通常不需要保存历史
        "additional_messages": [
            {
                "role": "user",
                "content": query,
                "content_type": "text"
            }
        ]
    }

    try:
        # 增加超时设置 (connect_timeout, read_timeout)
        response = requests.post(COZE_API_URL, headers=headers, json=payload, stream=True, timeout=(10, 60))
        response.raise_for_status() # 检查 HTTP 错误状态码 (4xx, 5xx)

        current_event = None
        accumulated_reasoning = ""  # Store reasoning steps
        answer_started = False      # Flag to track if the main answer has started
        reasoning_yielded = False   # Flag to ensure reasoning is yielded only once

        print(f"\n--- Response stream from bot {bot_id} ---")
        # print("DEBUG: Before iterating response lines...")
        lines_iterated = 0
        for line in response.iter_lines():
            lines_iterated += 1
            if line:
                decoded_line = line.decode('utf-8')
                # print(f"DEBUG: Raw SSE line: {decoded_line}")

                if decoded_line.startswith("event:"):
                    current_event = decoded_line[len("event:"):].strip()
                elif decoded_line.startswith("data:"):
                    data_str = decoded_line[len("data:"):].strip()

                    if data_str == "[DONE]":
                        # --- Handle case where only reasoning was sent ---
                        if not answer_started and accumulated_reasoning and not reasoning_yielded:
                            print(f"<think>{accumulated_reasoning}</think>\n", end='', flush=True) # Print reasoning before exiting
                            yield f"<think>{accumulated_reasoning}</think>\n"
                            reasoning_yielded = True
                        # --- End of handling ---
                        print(f"\n--- End of stream from bot {bot_id} ---")
                        print(f"INFO: Coze stream finished for bot {bot_id}.")
                        break # 流正常结束

                    try:
                        data = json.loads(data_str)

                        if current_event == "conversation.message.delta":
                            role = data.get("role")
                            msg_type = data.get("type") # Renamed from 'type' to avoid conflict
                            content_part = data.get("content", "")
                            reasoning_content = data.get("reasoning_content") # Get reasoning content

                            # 1. Accumulate reasoning before the answer starts
                            if reasoning_content and not answer_started:
                                if accumulated_reasoning: # Add newline between steps
                                    accumulated_reasoning += "\n"
                                accumulated_reasoning += reasoning_content

                            # 2. Check if the answer is starting
                            if role == "assistant" and msg_type == "answer" and content_part:
                                # 3. If answer starts and reasoning hasn't been yielded, yield it now.
                                if not answer_started and accumulated_reasoning and not reasoning_yielded:
                                    print(f"<think>{accumulated_reasoning}</think>\n", end='', flush=True) # Print reasoning
                                    yield f"<think>{accumulated_reasoning}</think>\n"
                                    reasoning_yielded = True
                                    accumulated_reasoning = "" # Clear after yielding

                                answer_started = True # Mark that the answer has begun

                                # 4. Yield the actual answer content part
                                print(content_part, end='', flush=True) # Print answer part
                                yield content_part

                        # Handle Coze error event (unchanged)
                        elif current_event == "error":
                            error_message = data.get('error', {}).get('message', 'Unknown Coze API error')
                            error_code = data.get('error', {}).get('code', 'N/A')
                            print(f"\nERROR: Coze API Error Event received: Code={error_code}, Message={error_message}")
                            yield f"[ERROR: Coze API Error - {error_message}]"
                            break

                    except json.JSONDecodeError:
                        print(f"\nWARNING: Could not decode JSON from Coze data line: {data_str}")
                        continue
                    except Exception as e:
                         print(f"\nERROR: Error processing Coze data chunk: {e}, data: {data_str}")
                         continue
            # else:
                # print("DEBUG: Received empty line.")
        # print(f"DEBUG: After iterating response lines. Total lines iterated: {lines_iterated}")
        print() # Final newline for console clarity

    # ... (except blocks remain the same) ...
    except requests.exceptions.Timeout as e:
        print(f"ERROR: Request to Coze API timed out for bot {bot_id}: {e}")
        yield "[ERROR: Request to Coze API timed out]"
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: Coze API returned HTTP error for bot {bot_id}: {e.response.status_code} {e.response.reason}. Response: {e.response.text}")
        yield f"[ERROR: Coze API request failed - HTTP {e.response.status_code}]"
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Network error connecting to Coze API for bot {bot_id}: {e}")
        yield f"[ERROR: Failed to connect to Coze API - {e}]"
    except Exception as e:
        import traceback
        print(f"ERROR: Unexpected error during Coze stream processing for bot {bot_id}: {e}\n{traceback.format_exc()}")
        yield f"[ERROR: Internal server error during stream processing]"
# --- API 端点 ---

@app.route('/fast', methods=['POST'])
def fast_endpoint():
    """Coze 快速响应 Bot 接口 (流式)"""
    # 1. 认证
    auth_key = request.headers.get('auth-key')
    if not valid_auth_key(auth_key):
        print(f"WARNING: Invalid auth key received from {request.remote_addr}.") # Changed from app.logger.warning
        # 返回 JSON 错误和 401 状态码
        return {"error": CONFIG.get('rejection_message', "Unauthorized access")}, 401

    # 2. 获取 Query (确保是 JSON 请求)
    if not request.is_json:
        print("ERROR: Request content type is not application/json.") # Changed from app.logger.error
        return {"error": "Request must be JSON"}, 415 # Unsupported Media Type

    data = request.get_json()
    if not data or 'query' not in data or not isinstance(data['query'], str):
        print("ERROR: Missing or invalid 'query' parameter in JSON request.") # Changed from app.logger.error
        return {"error": "Missing or invalid 'query' parameter"}, 400 # Bad Request
    query = data['query']

    # 3. 敏感词检查
    if contains_banned_words(query):
        print(f"INFO: Banned word detected in query from {request.remote_addr}. Query: '{query[:50]}...'") # Changed from app.logger.info
        # 返回纯文本拒绝消息 和 200 OK (按原设计)
        # 注意：这里返回 200 可能不是最佳实践，但遵循了原始代码行为
        return Response(CONFIG.get('rejection_message', "Query contains restricted content."), mimetype='text/plain', status=200)


    # 4. 调用 Coze 并流式返回
    bot_id = CONFIG.get('fast_bot_id')
    api_key = CONFIG.get('coze_key')

    if not bot_id or not api_key:
         print("ERROR: Server configuration error: 'fast_bot_id' or 'coze_key' is missing.") # Changed from app.logger.error
         return {"error": "Server configuration error"}, 500 # Internal Server Error

    print(f"INFO: Calling fast bot ({bot_id}) for {request.remote_addr}. Query: '{query[:50]}...'") # Changed from app.logger.info
    generator = call_coze_stream(bot_id, query, api_key)
    # 使用 text/event-stream 类型返回 SSE 流
    return Response(stream_with_context(generator), content_type='text/event-stream')


@app.route('/thinking', methods=['POST'])
def thinking_endpoint():
    """Coze 深度思考 Bot 接口 (流式)"""
     # 1. 认证
    auth_key = request.headers.get('auth-key')
    if not valid_auth_key(auth_key):
        print(f"WARNING: Invalid auth key received from {request.remote_addr}.") # Changed from app.logger.warning
        return {"error": CONFIG.get('rejection_message', "Unauthorized access")}, 401

    # 2. 获取 Query
    if not request.is_json:
        print("ERROR: Request content type is not application/json.") # Changed from app.logger.error
        return {"error": "Request must be JSON"}, 415

    data = request.get_json()
    if not data or 'query' not in data or not isinstance(data['query'], str):
        print("ERROR: Missing or invalid 'query' parameter in JSON request.") # Changed from app.logger.error
        return {"error": "Missing or invalid 'query' parameter"}, 400
    query = data['query']

    # 3. 敏感词检查
    if contains_banned_words(query):
        print(f"INFO: Banned word detected in query from {request.remote_addr}. Query: '{query[:50]}...'") # Changed from app.logger.info
        return Response(CONFIG.get('rejection_message', "Query contains restricted content."), mimetype='text/plain', status=200)

    # 4. 调用 Coze 并流式返回
    bot_id = CONFIG.get('thinking_bot_id')
    api_key = CONFIG.get('coze_key')

    if not bot_id or not api_key:
        print("ERROR: Server configuration error: 'thinking_bot_id' or 'coze_key' is missing.") # Changed from app.logger.error
        return {"error": "Server configuration error"}, 500

    print(f"INFO: Calling thinking bot ({bot_id}) for {request.remote_addr}. Query: '{query[:50]}...'") # Changed from app.logger.info
    generator = call_coze_stream(bot_id, query, api_key)
    return Response(stream_with_context(generator), content_type='text/event-stream')

# --- 启动服务 ---
if __name__ == '__main__':
    # 从环境变量获取端口，默认为 9000
    port = int(os.environ.get('PORT', 9000))
    # 生产环境推荐使用 Waitress, Gunicorn 或 uWSGI
    # 例如: waitress-serve --host=0.0.0.0 --port=9000 coze.main:app
    # 这里仍然使用 Flask 自带服务器，但关闭 debug 模式
    # 添加一个启动信息
    print(f"INFO: Starting Flask server on host 0.0.0.0, port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
