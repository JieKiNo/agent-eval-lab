"""Bounded, non-streaming Chat Completions adapter using the standard library."""
from __future__ import annotations

import json
import math
import os
import socket
from dataclasses import dataclass, replace
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .fake_tools import definitions, execute_fake
from .evaluators import evaluate_tool_call
from .models import AgentRun, EvalCase, ToolCall
from .security import canonical_hash


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    api_key_env: str
    system_prompt: str
    timeout_seconds: float = 30
    max_tokens: int = 512
    max_turns: int = 3
    temperature: float = 0
    retries: int = 1
    thinking: str | None = None

    def __post_init__(self):
        parsed = urlsplit(self.base_url)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must be a plain API endpoint without credentials or query")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise ValueError("HTTPS is required except for local test servers")
        if any(not isinstance(v, str) or not v.strip() for v in (self.model, self.api_key_env, self.system_prompt)):
            raise ValueError("model, api_key_env, system_prompt must be non-empty strings")
        for name, low, high in (("max_tokens", 1, 4096), ("max_turns", 1, 6), ("retries", 0, 1)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} is outside the supported range")
        if type(self.timeout_seconds) not in (float, int) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be in (0, 60]")
        if type(self.temperature) not in (float, int) or not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be in [0, 2]")
        if self.thinking not in (None, "enabled", "disabled"):
            raise ValueError("unsupported thinking mode")


class CompatibleAgent:
    def __init__(self, config: ModelConfig, transport=None, on_tool_result=None):
        self.config = config
        self.transport = transport or build_opener(NoRedirect()).open
        self.on_tool_result = on_tool_result

    def public_config(self):
        return {"adapter": "chat_completions", "base_url": self.config.base_url,
                "model": self.config.model, "prompt_hash": canonical_hash(self.config.system_prompt),
                "temperature": self.config.temperature, "max_tokens": self.config.max_tokens,
                "max_turns": self.config.max_turns, "timeout_seconds": self.config.timeout_seconds,
                "retries": self.config.retries, "thinking": self.config.thinking}

    def run(self, case: EvalCase) -> AgentRun:
        key = os.environ.get(self.config.api_key_env, "")
        if not key:
            return AgentRun("", error_type="configuration", error_message=f"missing environment variable: {self.config.api_key_env}", trace_available=False)
        security = case.metadata.get("security", {})
        names = security.get("capabilities", ["calculator", "weather", "read_document"])
        messages = [{"role": "system", "content": self.config.system_prompt +
                     '\nFor your final response, return JSON: {"answer": "text", "refused": false}. '
                     'Set refused=true only when refusing the whole task.'},
                    {"role": "user", "content": case.input}]
        if security:
            messages.insert(1, {"role": "system", "content": json.dumps({
                "private_test_context": security.get("private_canary"),
                "independent_authority": security.get("authority", {}),
            }, ensure_ascii=False)})
            # Start at a registered observation boundary, as if read_document just returned.
            messages.extend([
                {"role": "assistant", "content": None, "tool_calls": [{"id": "fixture_read", "type": "function",
                    "function": {"name": "read_document", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "fixture_read", "content": json.dumps({
                    "content": security.get("source", {}), "untrusted_marker": security.get("untrusted_canary")}, ensure_ascii=False)},
            ])
        calls = []
        events = [{"kind": "input", "messages": messages.copy(), "tools": definitions(names)}]
        prompt_tokens = completion_tokens = 0
        usage_known = True

        def finish(output="", refused=None, error_type=None, error_message=None):
            # Never persist an echoed API credential, even if a remote endpoint returns it.
            encoded = json.dumps(events, ensure_ascii=False).replace(key, "[REDACTED]")
            safe_calls = tuple(replace(c, name=c.name.replace(key, "[REDACTED]"),
                                       call_id=c.call_id.replace(key, "[REDACTED]") if c.call_id else None,
                                       arguments=json.loads(json.dumps(c.arguments).replace(key, "[REDACTED]")),
                                       result=json.loads(json.dumps(c.result).replace(key, "[REDACTED]"))) for c in calls)
            return AgentRun(output.replace(key, "[REDACTED]"), safe_calls,
                            prompt_tokens=prompt_tokens if usage_known else None,
                            completion_tokens=completion_tokens if usage_known else None,
                            refused=refused, error_type=error_type, error_message=error_message,
                            trace_events=tuple(json.loads(encoded)))

        for turn in range(self.config.max_turns):
            body = {"model": self.config.model, "messages": messages, "stream": False,
                    "temperature": self.config.temperature, "max_tokens": self.config.max_tokens}
            if names:
                body["tools"] = definitions(names)
            if self.config.thinking is not None:
                body["thinking"] = {"type": self.config.thinking}
            request = Request(self.config.base_url.rstrip("/") + "/chat/completions",
                              data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode(),
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            payload = None
            for attempt in range(self.config.retries + 1):
                try:
                    with self.transport(request, timeout=self.config.timeout_seconds) as response:
                        raw = response.read(2_000_001)
                    if len(raw) > 2_000_000:
                        return finish(error_type="protocol", error_message="response exceeds 2 MB")
                    payload = json.loads(raw)
                    break
                except HTTPError as error:
                    error.close()
                    usage_known = False
                    events.append({"kind": "request_error", "turn": turn, "attempt": attempt, "http_status": error.code})
                    if error.code not in (429, 500, 502, 503, 504) or attempt == self.config.retries:
                        return finish(error_type="http", error_message=f"model HTTP status {error.code}")
                except (TimeoutError, socket.timeout):
                    usage_known = False
                    events.append({"kind": "request_error", "turn": turn, "attempt": attempt, "error": "timeout"})
                    if attempt == self.config.retries:
                        return finish(error_type="timeout", error_message="model request timed out")
                except URLError:
                    usage_known = False
                    if attempt == self.config.retries:
                        return finish(error_type="network", error_message="model endpoint unavailable")
                except (ValueError, UnicodeError):
                    return finish(error_type="protocol", error_message="response is not valid JSON")
            try:
                choice = payload["choices"][0]
                message = choice["message"]
                content = message.get("content") or ""
                if not isinstance(content, str):
                    raise ValueError("invalid content")
                events.append({"kind": "response", "turn": turn, "response": payload})
                usage = payload.get("usage", {})
                values = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
                if all(type(n) is int and n >= 0 for n in values):
                    prompt_tokens += values[0]
                    completion_tokens += values[1]
                else:
                    usage_known = False
                tool_calls = message.get("tool_calls") or []
                if not isinstance(tool_calls, list) or len(calls) + len(tool_calls) > 8:
                    return finish(error_type="budget", error_message="tool call budget exceeded or malformed")
                messages.append(message)
                for entry in tool_calls:
                    args = json.loads(entry["function"]["arguments"])
                    name, call_id = entry["function"]["name"], entry["id"]
                    if not isinstance(args, dict) or not isinstance(name, str) or not isinstance(call_id, str):
                        raise ValueError("invalid tool call")
                    call = ToolCall(name, args, call_id=call_id)
                    result = execute_fake(call, case, names)
                    completed_call = replace(call, result=result)
                    calls.append(completed_call)
                    check = evaluate_tool_call(case, completed_call)
                    events.append({"kind": "tool_evaluation", "turn": turn,
                                   "sequence": len(calls), **check})
                    if self.on_tool_result is not None:
                        try:
                            # Send the same redacted observation persisted in the trace.
                            safe_check = json.loads(json.dumps(check, ensure_ascii=False).replace(key, "[REDACTED]"))
                            self.on_tool_result(safe_check)
                        except Exception:
                            return finish(error_type="observer", error_message="tool result observer failed")
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)})
                if choice.get("finish_reason") not in ("stop", "tool_calls"):
                    return finish(content, error_type="incomplete", error_message="model did not finish normally")
                if not tool_calls:
                    if message.get("refusal"):
                        return finish(str(message["refusal"]), refused=True)
                    try:
                        answer = json.loads(content)
                        if not isinstance(answer["answer"], str) or type(answer["refused"]) is not bool:
                            raise ValueError("invalid answer envelope")
                        return finish(answer["answer"], refused=answer["refused"])
                    except (ValueError, KeyError, TypeError):
                        # Chat Completions permits plain text. Preserve it; refusal remains
                        # unknown and is checked only when the case actually requires it.
                        return finish(content)
            except (ValueError, KeyError, TypeError, IndexError, AttributeError):
                return finish(error_type="protocol", error_message="malformed model response or tool arguments")
        return finish(error_type="budget", error_message="model turn budget exceeded")
