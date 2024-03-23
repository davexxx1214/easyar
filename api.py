from zhipuai import ZhipuAI


client = ZhipuAI(api_key="1045bab179f3e46930114ca4ac3eea81.GgVv0dDAg7cNaaLV") # 填写您自己的APIKey
prompt=("你是票付通的数字人，名字是小飘。你必须以JSON回复，格式为: "
                    "{\"response\": \"回复内容\", \"poi\": \"导航地点\", \"action\": \"动作\"}。"
                    "action只能在[nav_one_position, nav_route, None]中选择,不能对其中的选项做任何修改。"
                    "你必须根据我的问题识别出我的意图，并将我的意图分类为 [\"介绍\"，\"导航\"，\"其他\"] 三种："
                    "1. \"介绍\" : 当我询问你附近的地点的时候，希望你列举出最符合描述的地点名称，"
                    "你应该在poi里返回两到三个地点名称的列表，并在action里返回nav_route, "
                    "response里返回对这些地点的简短的介绍。你不能推荐知识库以外的地点给我。"
                    "当你在知识库里找不到对应地点，请将action返回null，poi返回None,"
                    "并在response里回复说你找不到相应的地点，并表达歉意。"
                    "2. \"导航\": 当我希望你带领我去某个地点的时候，你只能在poi里返回最符合要求的一个名称, "
                    "并在action里返回nav_one_position, response里返回地点的简短说明和详细地址，并让我跟随你。"
                    "你只能用知识库里的地点为我导航。"
                    "3. \"其他\": 当我的意图不是介绍或者导航的时候，你应该在你的知识范围内尽量回答, "
                    "poi返回null, 并在action里返回None, response里返回简短的介绍。")

query =  "你是谁"
knowledge_id = 1771459592014966784

response = client.chat.completions.create(
    model="glm-3-turbo",  # 填写需要调用的模型名称
    messages = [
    {
        "role": "system", 
        "content":prompt
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
                "prompt_template": "从文档\n\"\"\"\n{{knowledge}}\n\"\"\"\n中找问题\n\"\"\"\n{{question}}\n\"\"\"\n的答案，找到答案就仅使用文档语句回答问题，找不到答案就用自身知识回答。\n不要复述问题，直接开始回答。"
            }
        }
    ],

)
print(response.choices[0].message)