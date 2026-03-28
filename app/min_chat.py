from __future__ import annotations

import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import Settings, get_settings


def ensure_llm_configured(settings: Settings) -> None:
    # 强制要求来自 .env（经 dotenv 注入到环境变量）的完整配置，避免使用代码默认值。
    required_env_keys = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL_ID")
    missing_keys = [key for key in required_env_keys if not os.getenv(key, "").strip()]
    if missing_keys:
        missing_text = ", ".join(missing_keys)
        raise ValueError(f"LLM 配置缺失: {missing_text}，请在 .env 中补齐。")

    if not settings.llm_configured:
        raise ValueError(
            "LLM 配置不完整，请在 .env 中设置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_ID。"
        )


def create_model(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model_id,
        temperature=0.2,
        timeout=settings.request_timeout_seconds,
        max_retries=1,
    )


def run_min_chat() -> None:
    settings = get_settings()
    ensure_llm_configured(settings)
    model = create_model(settings)

    print(f"最小对话程序已启动，当前模型: {settings.llm_model_id}")
    print("输入内容并回车开始对话，输入 /exit 退出。")

    messages = [SystemMessage(content="你是一个简洁且有帮助的助手。")]
    while True:
        user_text = input("你: ").strip()
        if not user_text:
            continue

        if user_text.lower() in {"/exit", "exit", "quit"}:
            print("对话结束。")
            return

        messages.append(HumanMessage(content=user_text))
        response = model.invoke(messages)
        assistant_text = str(response.content).strip()
        print(f"助手: {assistant_text}")


if __name__ == "__main__":
    run_min_chat()
