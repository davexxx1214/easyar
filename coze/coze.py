import requests
import json # 导入 json 模块
import os
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
# --- JWT Auth Setup ---
coze_api_base = os.getenv("COZE_API_BASE", COZE_CN_BASE_URL)
private_key_file_path = "shahu_private_key.pem" # 私钥文件路径
config_file_path = "config.json" # 配置文件路径

# Define variables outside try block with None initially
jwt_oauth_private_key = None
jwt_oauth_public_key_id = None
jwt_oauth_client_id = None

# 读取私钥
try:
    with open(private_key_file_path, "r") as f:
        jwt_oauth_private_key = f.read()
except FileNotFoundError:
    print(f"错误: 私钥文件 '{private_key_file_path}' 未找到。")
    exit(1) # 如果私钥文件不存在，则退出
except Exception as e:
    print(f"读取私钥文件时发生错误: {e}")
    exit(1)

# 读取配置文件获取 public_key_id 和 client_id
try:
    with open(config_file_path, 'r', encoding='utf-8') as f: # Explicitly set encoding to utf-8
        config = json.load(f)
        jwt_oauth_public_key_id = config.get("public_key")
        jwt_oauth_client_id = config.get("client_id") # Read client_id from config

        if not jwt_oauth_public_key_id:
            print(f"错误: 未在 '{config_file_path}' 中找到 'public_key'。")
            exit(1)
        if not jwt_oauth_client_id:
            print(f"错误: 未在 '{config_file_path}' 中找到 'client_id'。") # Updated error message
            exit(1)

except FileNotFoundError:
    print(f"错误: 配置文件 '{config_file_path}' 未找到。")
    exit(1)
except json.JSONDecodeError:
    print(f"错误: 配置文件 '{config_file_path}' 不是有效的 JSON。")
    exit(1)
except Exception as e:
    print(f"读取配置文件时发生错误: {e}")
    exit(1)

# 初始化 JWTOAuthApp
jwt_oauth_app = JWTOAuthApp(
    client_id=jwt_oauth_client_id,
    private_key=jwt_oauth_private_key,
    public_key_id=jwt_oauth_public_key_id,
    base_url=coze_api_base,
)

# 初始化 Coze 客户端 (使用 JWTAuth)
# 注意：SDK 会在需要时自动使用 JWTOAuthApp 获取或刷新 token
coze_client = Coze(auth=JWTAuth(oauth_app=jwt_oauth_app), base_url=coze_api_base)

# --- End JWT Auth Setup ---


def upload_file_to_coze(file_path: str) -> str | None:
    """
    使用 Coze SDK 上传本地文件并返回 file_id。
    """
    try:
        with open(file_path, 'rb') as f:
            upload_response = coze_client.files.upload(file=f) # SDK 处理文件上传细节

        file_id = upload_response.id
        print(f"文件 '{file_path}' 上传成功. File ID: {file_id}")
        return file_id
    except FileNotFoundError:
        print(f"错误: 文件 '{file_path}' 未找到。")
        return None
    except AttributeError:
         print(f"错误: 上传响应对象没有 'id' 属性。请检查 cozepy SDK 返回的文件对象结构。 Response: {upload_response}")
         return None
    except Exception as e: # 捕获 SDK 可能抛出的通用异常
        print(f"上传文件时发生错误: {e}")
        return None


def send_coze_message():

    # 首先，上传图片并获取 file_id
    local_image_path = "test.jpg" # 假设 test.jpg 在脚本同目录下
    file_id = upload_file_to_coze(local_image_path)

    if not file_id:
        print("由于文件上传失败，无法发送消息。")
        return None


    message_content_list = [
        {"type": "image", "file_id": file_id}, # Image part
        {"type": "text", "text": "帮我介绍一下图片的建筑或者景点"} # Text part
    ]
    
    content_json_string = json.dumps(message_content_list)

    # 直接实例化 Message 对象
    user_message = Message(
        role="user",
        content=content_json_string,    # Pass the JSON string
        content_type="object_string"  # Use 'object_string' as content_type
    )

    # 请求体数据 (使用 SDK 方法参数)
    bot_id = "7474949068725518386" # Bot ID
    user_id = "123123" # User ID

    # 使用 SDK 发送聊天请求
    try:
        stream = coze_client.chat.stream(
            bot_id=bot_id,
            user_id=user_id,
            additional_messages=[user_message], # 使用构建的 Message 对象
            auto_save_history=True, # 假设 stream 方法支持此参数
        )
        full_content = "" # 用于累积消息内容

        for event in stream:
            if event.event == ChatEventType.CONVERSATION_MESSAGE_DELTA:
                 # 检查 message 是否存在以及 role 和 type
                 if hasattr(event, 'message') and event.message.role == MessageRole.ASSISTANT and event.message.type == MessageType.ANSWER:
                     content_part = event.message.content
                     full_content += content_part
                     print(content_part, end='', flush=True)
            elif event.event == ChatEventType.CONVERSATION_MESSAGE_COMPLETED:
                 # 检查 message 是否存在以及 role 和 type
                 if hasattr(event, 'message') and event.message.role == MessageRole.ASSISTANT and event.message.type == MessageType.ANSWER:
                    # 最终消息，可以选择在这里验证或记录
                    print() # 换行
                    pass
            elif event.event == ChatEventType.CONVERSATION_CHAT_COMPLETED:
                pass
            elif event.event == ChatEventType.ERROR:
                print(f"\nError received: {event}") # 打印整个错误事件
                break # 出现错误，停止处理

        return full_content # 可以选择返回累积的完整内容

    except AttributeError as ae:
        # 捕获可能的属性错误，例如 Message.build_user_question 不存在或参数错误
        print(f"构建消息或处理事件时发生属性错误: {ae}")
        print("请检查 cozepy SDK 的 Message 类用法和事件结构。")
        return None
    except Exception as e:
        print(f"与 Coze API 交互时发生错误: {e}")

        return None


# 调用函数
if __name__ == "__main__":
    print("Bot:")
    result = send_coze_message()
