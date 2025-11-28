#导入 dotenv 库的 load_dotenv 函数，用于加载环境变量文件（.env）中的配置
import dotenv
from langchain_openai import ChatOpenAI
import os
dotenv.load_dotenv() #加载当前目录下的 .env 文件
api_key = os.getenv("OPENAI_API_KEY")
base_url = os.getenv("OPENAI_BASE_URL")
print(api_key)
print(base_url)

#创建一个 ChatOpenAI 对象，使用 OpenAI API 进行对话
llm = ChatOpenAI(model="qwen-plus", temperature=0, api_key=api_key, base_url=base_url)

# 直接提供问题，并调用llm
response = llm.invoke("什么是大模型？")

print(response)