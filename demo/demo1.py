import argparse
import ast
import json
import operator
import os
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI


def load_llm_config() -> dict[str, str]:
    load_dotenv()

    config = {
        "api_key": os.getenv("LLM_API_KEY", "").strip(),
        "base_url": os.getenv("LLM_BASE_URL", "").strip(),
        "model": os.getenv("LLM_MODEL_ID", "").strip(),
    }

    missing_keys = [key for key, value in config.items() if not value]
    if missing_keys:
        missing_names = ", ".join(missing_keys)
        raise ValueError(f"缺少模型配置: {missing_names}，请先检查 .env 文件。")

    return config


@tool
def get_current_time() -> str:
    """获取当前本地时间。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_BINARY_OPERATORS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPERATORS: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_expression(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_expression(node.body)

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)

    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _eval_expression(node.left)
        right = _eval_expression(node.right)
        return _BINARY_OPERATORS[type(node.op)](left, right)

    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        operand = _eval_expression(node.operand)
        return _UNARY_OPERATORS[type(node.op)](operand)

    raise ValueError("仅支持数字和 + - * / // % ** () 组成的表达式。")


@tool
def calculate(expression: str) -> str:
    """计算数学表达式，例如: (25 + 5) / 3。"""
    try:
        parsed = ast.parse(expression, mode="eval")
        result = _eval_expression(parsed)
    except Exception as exc:
        return f"计算失败: {exc}"

    if result.is_integer():
        return str(int(result))
    return str(result)


def build_agent() -> Any:
    config = load_llm_config()
    model = ChatOpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
        model=config["model"],
        temperature=0,
    )

    return create_agent(
        model=model,
        tools=[get_current_time, calculate],
        system_prompt=(
            "你是一个基于 LangChain 的单 Agent 对话助手。"
            "你需要优先使用中文回答。"
            "遇到时间或数学计算问题时，主动调用工具。"
            "输出保持简洁、清晰。"
        ),
    )


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        return "".join(text_parts).strip()

    return str(content)


AGENT = build_agent()
HTML_FILE = Path(__file__).with_name("index.html")


def stream_agent_reply(messages: list[dict[str, str]]):
    for chunk, metadata in AGENT.stream({"messages": messages}, stream_mode="messages"):
        if metadata.get("langgraph_node") != "model":
            continue

        text = extract_text(chunk.content)
        if text:
            yield text


def chat_once(history: list[dict[str, str]], user_input: str):
    messages = [*history, {"role": "user", "content": user_input}]
    full_reply: list[str] = []

    for text in stream_agent_reply(messages):
        full_reply.append(text)
        yield text

    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": "".join(full_reply)})


def run_cli() -> None:
    history: list[dict[str, str]] = []

    print("LangChain 单 Agent 对话 Demo")
    print("输入内容开始聊天，输入 exit / quit / q 结束。")

    while True:
        user_input = input("\n你: ").strip()
        if not user_input:
            continue

        if user_input.lower() in {"exit", "quit", "q"}:
            print("助手: 再见。")
            break

        print("助手: ", end="", flush=True)
        for text in chat_once(history, user_input):
            print(text, end="", flush=True)
        print()


class ChatHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path not in {"/", "/index.html"}:
            self.send_error(HTTPStatus.NOT_FOUND, "页面不存在")
            return

        html = HTML_FILE.read_text(encoding="utf-8")
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_error(HTTPStatus.NOT_FOUND, "接口不存在")
            return

        try:
            payload = self._read_json_body()
            user_input = str(payload.get("message", "")).strip()
            history = payload.get("history", [])
            sanitized_history = self._sanitize_history(history)

            if not user_input:
                self._send_text_error(HTTPStatus.BAD_REQUEST, "message 不能为空")
                return
        except ValueError as exc:
            self._send_text_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        try:
            for text in chat_once(sanitized_history, user_input):
                self._write_chunk(text)
            self._finish_chunked_response()
        except Exception as exc:
            self._write_chunk(f"\n[错误] {exc}")
            self._finish_chunked_response()

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            raise ValueError("请求体不能为空")

        raw_body = self.rfile.read(content_length)
        try:
            data = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("请求体不是合法 JSON") from exc

        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")

        return data

    def _sanitize_history(self, history: Any) -> list[dict[str, str]]:
        if not isinstance(history, list):
            raise ValueError("history 必须是数组")

        sanitized_history: list[dict[str, str]] = []
        for item in history:
            if not isinstance(item, dict):
                raise ValueError("history 中的每一项都必须是对象")

            role = str(item.get("role", "")).strip()
            content = str(item.get("content", "")).strip()
            if role not in {"user", "assistant"} or not content:
                continue

            sanitized_history.append({"role": role, "content": content})

        return sanitized_history

    def _send_text_error(self, status: HTTPStatus, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_chunk(self, text: str) -> None:
        data = text.encode("utf-8")
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data + b"\r\n")
        self.wfile.flush()

    def _finish_chunked_response(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def log_message(self, format: str, *args: Any) -> None:
        return


def run_web(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), ChatHandler)
    print(f"Web 对话页面已启动: http://{host}:{port}")
    print("按 Ctrl+C 停止服务。")
    server.serve_forever()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LangChain 单 Agent 对话 Demo")
    parser.add_argument("--web", action="store_true", help="启动 Web 对话页面")
    parser.add_argument("--host", default="127.0.0.1", help="Web 服务监听地址")
    parser.add_argument("--port", type=int, default=8008, help="Web 服务端口")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.web:
        run_web(args.host, args.port)
        return

    run_cli()


if __name__ == "__main__":
    main()
