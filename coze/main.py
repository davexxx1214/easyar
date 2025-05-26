# -*- coding: utf-8 -*-
from flask import Flask, Response, stream_with_context, request
import json
import requests
import os 
import traceback
import sys 
import logging
import time

# 从 cozepy 导入必要的类
from cozepy import (
    COZE_CN_BASE_URL,
    Coze,
    JWTAuth,
    JWTOAuthApp,
    Message,
    ChatEventType,
    MessageRole,
    MessageType
)

# 设置 werkzeug 日志级别，减少请求日志
logging.getLogger('werkzeug').setLevel(logging.WARNING)

app = Flask(__name__)

# 定义全局变量
BANWORDS = set()
CONFIG = {}
COZE_API_URL = "https://api.coze.cn/v3/chat" # 将不再被 fast_endpoint 直接使用
# 全局 Coze 客户端实例
coze_client = None

# 加载配置
def load_config():
    """从 config.json 加载配置并初始化 Coze 客户端"""
    global CONFIG, coze_client
    try:
        with open('config.json', 'r', encoding='utf-8') as config_file:
            CONFIG = json.load(config_file)
            # 检查必要的配置是否存在
            required_keys = ['auth_keys', 'fast_bot_id', 'nav_bot_id', 'rejection_message'] # 添加nav_bot_id到必须项
            for key in required_keys:
                if key not in CONFIG:
                    raise ValueError(f"Config file 'config.json' is missing required key: {key}")
            if not isinstance(CONFIG.get('auth_keys'), list):
                 raise ValueError("Config key 'auth_keys' must be a list")

            # JWT Auth 所需的新配置项
            jwt_required_keys = ['private_key_file_path', 'public_key_id', 'client_id']
            for key in jwt_required_keys:
                if key not in CONFIG:
                    raise ValueError(f"Config file 'config.json' is missing JWT Auth required key: {key}")
            
            # 确定 Coze API Base URL (config -> env -> default)
            coze_api_base_url = CONFIG.get('coze_api_base')
            if not coze_api_base_url:
                coze_api_base_url = os.getenv("COZE_API_BASE", COZE_CN_BASE_URL)
            CONFIG['coze_api_base_for_sdk'] = coze_api_base_url # 存储供 SDK 使用

            print("INFO: Configuration loaded successfully.", file=sys.stderr)

            # 初始化 Coze Client
            private_key_path = CONFIG['private_key_file_path']
            try:
                with open(private_key_path, "r") as f:
                    jwt_oauth_private_key = f.read()
            except FileNotFoundError:
                print(f"ERROR: Private key file '{private_key_path}' not found.", file=sys.stderr)
                raise
            except Exception as e:
                print(f"ERROR: Error reading private key file '{private_key_path}': {e}", file=sys.stderr)
                raise

            jwt_oauth_app = JWTOAuthApp(
                client_id=CONFIG['client_id'],
                private_key=jwt_oauth_private_key,
                public_key_id=CONFIG['public_key_id'],
                base_url=CONFIG['coze_api_base_for_sdk'],
            )
            coze_client = Coze(auth=JWTAuth(oauth_app=jwt_oauth_app), base_url=CONFIG['coze_api_base_for_sdk'])
            print("INFO: Coze client initialized successfully.", file=sys.stderr)

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


def valid_auth_key(auth_key):
    """验证请求头中的 auth-key (旧版认证，fast_endpoint 将不再使用)"""
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

# 新的 SDK 流处理器
def sdk_stream_processor(sdk_stream, bot_id: str):
    """处理来自 Coze SDK 的流并产生内容部分。"""
    print(f"\n--- SDK Response stream from bot {bot_id} ---", file=sys.stderr)
    try:
        full_content_for_logging = [] 
        for event in sdk_stream:
            if event.event == ChatEventType.CONVERSATION_MESSAGE_DELTA:
                if hasattr(event, 'message') and \
                   event.message.role == MessageRole.ASSISTANT and \
                   event.message.type == MessageType.ANSWER and \
                   event.message.content:
                    content_part = event.message.content
                    full_content_for_logging.append(content_part)
                    yield content_part
            elif event.event == ChatEventType.ERROR:
                error_detail = event.error if hasattr(event, 'error') else None
                error_message = "Unknown SDK error"
                error_code = "N/A"
                if error_detail:
                    # 尝试获取更详细的错误信息
                    if hasattr(error_detail, 'message') and error_detail.message:
                        error_message = error_detail.message
                    elif isinstance(error_detail, str):
                        error_message = error_detail
                    else:
                        error_message = str(error_detail)

                    if hasattr(error_detail, 'code') and error_detail.code:
                        error_code = error_detail.code
                
                print(f"\nERROR: Coze SDK Error Event: Code={error_code}, Message='{error_message}'", file=sys.stderr)
                yield f"[ERROR: Coze SDK Error - {error_message}]"
                break 
        
        if full_content_for_logging:
            print(''.join(full_content_for_logging), file=sys.stderr) # 记录完整的消息
        print(f"\n--- End of SDK stream from bot {bot_id} ---", file=sys.stderr)
        print(f"INFO: Coze SDK stream finished for bot {bot_id}.", file=sys.stderr)

    except AttributeError as ae:
        print(f"ERROR: AttributeError during SDK stream processing for bot {bot_id}: {ae}\n{traceback.format_exc()}", file=sys.stderr)
        yield f"[ERROR: Internal server error - SDK attribute issue]"
    except Exception as e:
        print(f"ERROR: Unexpected error during SDK stream processing for bot {bot_id}: {e}\n{traceback.format_exc()}", file=sys.stderr)
        yield f"[ERROR: Internal server error during stream processing]"


@app.route('/', methods=['POST'])
def fast_endpoint():
    """Coze 快速响应 Bot 接口 (流式) - 使用 SDK 和 JWTAuth"""
    # 1. 认证 - 由 coze_client 通过 JWTAuth 自动处理

    # 2. 获取 Query (确保是 JSON 请求)
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

    # 4. 调用 Coze SDK 并流式返回
    global coze_client # 确保我们引用的是全局客户端
    if not coze_client:
        print("CRITICAL: coze_client is not initialized. Check server configuration and startup logs.", file=sys.stderr)
        return {"error": "Server configuration error - Coze client not ready"}, 500
        
    bot_id = CONFIG.get('fast_bot_id')
    if not bot_id:
         print("ERROR: Server configuration error: 'fast_bot_id' is missing.", file=sys.stderr)
         return {"error": "Server configuration error"}, 500 

    # 准备 Coze SDK 的消息体
    message_content_list = [{"type": "text", "text": query}]
    try:
        content_json_string = json.dumps(message_content_list)
    except Exception as e:
        print(f"ERROR: Failed to serialize query to JSON for Coze SDK: {e}", file=sys.stderr)
        return {"error": "Internal server error preparing request"}, 500

    user_message = Message(
        role=MessageRole.USER, 
        content=content_json_string,
        content_type="object_string" 
    )

    print(f"INFO: Calling fast bot ({bot_id}) via SDK for {request.remote_addr}. Query: '{query[:50]}...'", file=sys.stderr)
    
    try:
        # 注意：SDK 的 stream 方法可能不直接接受 stream=True 参数，它本身就是流式方法
        sdk_stream_iterable = coze_client.chat.stream(
            bot_id=bot_id,
            user_id="api_user", # 与旧版行为一致
            additional_messages=[user_message],
            auto_save_history=False, 
        )
    except AttributeError as ae: 
        print(f"ERROR: Coze SDK call failed (AttributeError) for bot {bot_id}: {ae}\n{traceback.format_exc()}", file=sys.stderr)
        return {"error": "Server error calling Coze service (SDK structure)"}, 500
    except requests.exceptions.Timeout as e: # requests.exceptions 需要导入
        print(f"ERROR: Request to Coze API timed out for bot {bot_id}: {e}", file=sys.stderr)
        return {"error": "Request to Coze API timed out"}, 504 
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: Coze API returned HTTP error for bot {bot_id}: {e.response.status_code} {e.response.reason}. Response: {e.response.text}", file=sys.stderr)
        return {"error": f"Coze API request failed - HTTP {e.response.status_code if e.response else 'Unknown'}"}, getattr(e.response, 'status_code', 500)
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Network error connecting to Coze API for bot {bot_id}: {e}", file=sys.stderr)
        return {"error": f"Failed to connect to Coze API - {type(e).__name__}"}, 503
    except Exception as e: 
        print(f"ERROR: Coze SDK call failed for bot {bot_id}: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return {"error": "Internal server error calling Coze service"}, 500

    processed_generator = sdk_stream_processor(sdk_stream_iterable, bot_id)
    
    headers = {
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'  
    }
    return Response(stream_with_context(processed_generator), 
                   content_type='text/event-stream', # SSE
                   headers=headers)


@app.route('/nav', methods=['POST'])
def nav_endpoint():
    """导航 Bot 接口 (非流式) - 使用 SDK 和 JWTAuth"""
    # 1. 认证 - 由 coze_client 通过 JWTAuth 自动处理

    # 2. 获取 Query (确保是 JSON 请求)
    if not request.is_json:
        print("ERROR: Request content type is not application/json.", file=sys.stderr)
        return {"error": "Request must be JSON"}, 415 

    data = request.get_json()
    if not data or 'query' not in data or not isinstance(data['query'], str):
        print("ERROR: Missing or invalid 'query' parameter in JSON request.", file=sys.stderr)
        return {"error": "Missing or invalid 'query' parameter"}, 400 
    query = data['query']

    # 3. 跳过敏感词检查 (根据要求)

    # 4. 调用 Coze SDK 并返回完整文本
    global coze_client # 确保我们引用的是全局客户端
    if not coze_client:
        print("CRITICAL: coze_client is not initialized. Check server configuration and startup logs.", file=sys.stderr)
        return {"error": "Server configuration error - Coze client not ready"}, 500
        
    bot_id = CONFIG.get('nav_bot_id')
    if not bot_id:
         print("ERROR: Server configuration error: 'nav_bot_id' is missing.", file=sys.stderr)
         return {"error": "Server configuration error"}, 500 

    # 准备 Coze SDK 的消息体
    message_content_list = [{"type": "text", "text": query}]
    try:
        content_json_string = json.dumps(message_content_list)
    except Exception as e:
        print(f"ERROR: Failed to serialize query to JSON for Coze SDK: {e}", file=sys.stderr)
        return {"error": "Internal server error preparing request"}, 500

    user_message = Message(
        role=MessageRole.USER, 
        content=content_json_string,
        content_type="object_string" 
    )

    print(f"INFO: Calling nav bot ({bot_id}) via SDK for {request.remote_addr}. Query: '{query[:50]}...'", file=sys.stderr)
    
    try:
        # 使用非流式调用
        chat_response = coze_client.chat.create(
            bot_id=bot_id,
            user_id="api_user",
            additional_messages=[user_message],
            auto_save_history=True
        )
        
        # 获取完整的响应文本
        full_content = ""
        
        # 等待对话完成
        max_wait_time = 60  # 最大等待60秒
        wait_interval = 0.5   # 每0.5秒检查一次
        waited_time = 0
        
        while (hasattr(chat_response, 'status') and 
               str(chat_response.status) == 'ChatStatus.IN_PROGRESS' and 
               waited_time < max_wait_time):
            
            time.sleep(wait_interval)
            waited_time += wait_interval
            
            # 重新获取对话状态
            try:
                chat_response = coze_client.chat.retrieve(
                    conversation_id=chat_response.conversation_id,
                    chat_id=chat_response.id
                )
            except Exception as e:
                print(f"ERROR: Failed to retrieve chat status: {e}", file=sys.stderr)
                break
        
        if waited_time >= max_wait_time:
            print(f"ERROR: Chat timed out after {max_wait_time} seconds", file=sys.stderr)
            return {"error": "Chat request timed out"}, 504
        
        if hasattr(chat_response, 'status') and str(chat_response.status) == 'ChatStatus.COMPLETED':
            # 对话已完成，获取消息
            if hasattr(chat_response, 'id') and hasattr(chat_response, 'conversation_id'):
                try:
                    # 获取对话中的消息
                    messages = coze_client.chat.messages.list(
                        conversation_id=chat_response.conversation_id,
                        chat_id=chat_response.id
                    )
                    
                    # 寻找助手的回复消息
                    for message in messages:
                        if (hasattr(message, 'role') and message.role == MessageRole.ASSISTANT and 
                            hasattr(message, 'type') and message.type == MessageType.ANSWER and
                            hasattr(message, 'content') and message.content):
                            full_content += message.content
                            
                except Exception as e:
                    print(f"ERROR: Failed to retrieve messages for nav bot {bot_id}: {e}", file=sys.stderr)
                    return {"error": "Failed to retrieve bot response"}, 500
        else:
            print(f"ERROR: Chat did not complete successfully. Final status: {chat_response.status}", file=sys.stderr)
            return {"error": "Chat did not complete successfully"}, 500
        
        if not full_content:
            print(f"WARNING: No valid content received from nav bot {bot_id}", file=sys.stderr)
            return {"error": "No content received from bot"}, 500
            
        print(f"INFO: Nav bot ({bot_id}) response: {full_content[:100]}...", file=sys.stderr)
        
        return Response(full_content, mimetype='text/plain', status=200)
        
    except AttributeError as ae: 
        print(f"ERROR: Coze SDK call failed (AttributeError) for nav bot {bot_id}: {ae}\n{traceback.format_exc()}", file=sys.stderr)
        return {"error": "Server error calling Coze service (SDK structure)"}, 500
    except requests.exceptions.Timeout as e:
        print(f"ERROR: Request to Coze API timed out for nav bot {bot_id}: {e}", file=sys.stderr)
        return {"error": "Request to Coze API timed out"}, 504 
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: Coze API returned HTTP error for nav bot {bot_id}: {e.response.status_code} {e.response.reason}. Response: {e.response.text}", file=sys.stderr)
        return {"error": f"Coze API request failed - HTTP {e.response.status_code if e.response else 'Unknown'}"}, getattr(e.response, 'status_code', 500)
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Network error connecting to Coze API for nav bot {bot_id}: {e}", file=sys.stderr)
        return {"error": f"Failed to connect to Coze API - {type(e).__name__}"}, 503
    except Exception as e: 
        print(f"ERROR: Coze SDK call failed for nav bot {bot_id}: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return {"error": "Internal server error calling Coze service"}, 500

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