from collections.abc import Generator
from json import dumps, loads
from typing import Any, Union
from requests import Response, post
from models.llm.errors import (
    BadRequestError,
    InsufficientAccountBalanceError,
    InternalServerError,
    InvalidAPIKeyError,
    InvalidAuthenticationError,
    RateLimitReachedError,
)
from models.llm.types import MinimaxMessage


class MinimaxChatCompletion:
    """
    Minimax Chat Completion API
    """

    def generate(
        self,
        model: str,
        api_key: str,
        group_id: str,
        endpoint_url: str,
        prompt_messages: list[MinimaxMessage],
        model_parameters: dict,
        tools: list[dict[str, Any]],
        stop: list[str] | None,
        stream: bool,
        user: str,
    ) -> Union[MinimaxMessage, Generator[MinimaxMessage, None, None]]:
        """
        generate chat completion
        """
        if not api_key or not group_id:
            raise InvalidAPIKeyError("Invalid API key or group ID")

        base_url = endpoint_url.rstrip('/')
        url = f"{base_url}/v1/text/chatcompletion?GroupId={group_id}"

        extra_kwargs = {}
        if "max_tokens" in model_parameters and isinstance(model_parameters["max_tokens"], int):
            extra_kwargs["tokens_to_generate"] = model_parameters["max_tokens"]
        if "temperature" in model_parameters and isinstance(model_parameters["temperature"], float):
            extra_kwargs["temperature"] = model_parameters["temperature"]
        if "top_p" in model_parameters and isinstance(model_parameters["top_p"], float):
            extra_kwargs["top_p"] = model_parameters["top_p"]
        prompt = "你是一个什么都懂的专家"
        role_meta = {"user_name": "我", "bot_name": "专家"}
        if len(prompt_messages) == 0:
            raise BadRequestError("At least one message is required")
        if prompt_messages[0].role == MinimaxMessage.Role.SYSTEM.value:
            if prompt_messages[0].content:
                prompt = prompt_messages[0].content
            prompt_messages = prompt_messages[1:]
        if len(prompt_messages) == 0:
            raise BadRequestError("At least one user message is required")
        messages = [message.to_completion_dict() for message in prompt_messages]
        headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
        body = {
            "model": model,
            "messages": messages,
            "prompt": prompt,
            "role_meta": role_meta,
            "stream": stream,
            **extra_kwargs,
        }
        try:
            response = post(url=url, data=dumps(body), headers=headers, stream=stream, timeout=(10, 300))
        except Exception as e:
            raise InternalServerError(e)
        if response.status_code != 200:
            raise InternalServerError(response.text)
        if stream:
            return self._handle_stream_chat_generate_response(response)
        return self._handle_chat_generate_response(response)

    def _handle_error(self, code: int, msg: str):
        if code in {1000, 1001, 1013, 1027}:
            raise InternalServerError(msg)
        elif code in {1002, 1039}:
            raise RateLimitReachedError(msg)
        elif code == 1004:
            raise InvalidAuthenticationError(msg)
        elif code == 1008:
            raise InsufficientAccountBalanceError(msg)
        elif code == 2013:
            raise BadRequestError(msg)
        else:
            raise InternalServerError(msg)

    def _handle_chat_generate_response(self, response: Response) -> MinimaxMessage:
        """
        handle chat generate response
        """
        response = response.json()
        if "base_resp" in response and response["base_resp"]["status_code"] != 0:
            code = response["base_resp"]["status_code"]
            msg = response["base_resp"]["status_msg"]
            self._handle_error(code, msg)
        message = MinimaxMessage(content=response["reply"], role=MinimaxMessage.Role.ASSISTANT.value)
        message.usage = {
            "prompt_tokens": 0,
            "completion_tokens": response["usage"]["total_tokens"],
            "total_tokens": response["usage"]["total_tokens"],
        }
        message.stop_reason = response["choices"][0]["finish_reason"]
        return message

    def _handle_stream_chat_generate_response(self, response: Response) -> Generator[MinimaxMessage, None, None]:
        """
        handle stream chat generate response
        """
        for line in response.iter_lines():
            if not line:
                continue
            line: str = line.decode("utf-8")
            if line.startswith("data: "):
                line = line[6:].strip()
            data = loads(line)
            if "base_resp" in data and data["base_resp"]["status_code"] != 0:
                code = data["base_resp"]["status_code"]
                msg = data["base_resp"]["status_msg"]
                self._handle_error(code, msg)
            if data["reply"]:
                total_tokens = data["usage"]["total_tokens"]
                message = MinimaxMessage(role=MinimaxMessage.Role.ASSISTANT.value, content="")
                message.usage = {"prompt_tokens": 0, "completion_tokens": total_tokens, "total_tokens": total_tokens}
                message.stop_reason = data["choices"][0]["finish_reason"]
                yield message
                return
            choices = data.get("choices", [])
            if len(choices) == 0:
                continue
            for choice in choices:
                message = choice["delta"]
                yield MinimaxMessage(content=message, role=MinimaxMessage.Role.ASSISTANT.value)
