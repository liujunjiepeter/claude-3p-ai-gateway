"""
Anthropic → OpenAI protocol translation proxy.
Claude Code (Anthropic API) → this proxy → One-API (OpenAI API) → DeepSeek / Xiaomi
"""
import json, os, sys, uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlparse


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests concurrently so count_tokens doesn't block messages."""
    daemon_threads = True

# Models exposed via /v1/models — set MODELS env var as JSON array
# Example: [{"id":"gpt-4o","type":"model","display_name":"GPT-4o"},...]
MODELS = json.loads(os.environ.get(
    "MODELS",
    '[{"id":"deepseek-v4-pro","type":"model","display_name":"DeepSeek V4 Pro"},'
    '{"id":"mimo-v2.5","type":"model","display_name":"MiMo V2.5"}]'
))
ONE_API = os.environ.get("ONE_API_URL", "http://localhost:3000")
PROXY_TOKEN = os.environ.get("PROXY_TOKEN", "your-one-api-token-here")


TEXT_ONLY_MODEL_HINTS = ("deepseek",)


def _supports_vision(model: str) -> bool:
    model_l = (model or "").lower()
    return not any(hint in model_l for hint in TEXT_ONLY_MODEL_HINTS)


def _content_to_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p)
    return json.dumps(content, ensure_ascii=False)


def _downgrade_tool_message(msg: dict) -> dict:
    tool_id = msg.get("tool_call_id", "") or "unknown"
    content = _content_to_text(msg.get("content", ""))
    if not content.strip():
        content = "Task completed successfully (no output)."
    return {"role": "user", "content": f"[工具结果 {tool_id}]\n{content}"}


def _downgrade_assistant_tool_calls(msg: dict, reason: str) -> dict:
    downgraded = dict(msg)
    calls = downgraded.pop("tool_calls", []) or []
    names = ", ".join(
        (tc.get("function") or {}).get("name", "") for tc in calls if isinstance(tc, dict)
    ) or "unknown"
    content = _content_to_text(downgraded.get("content", ""))
    suffix = f"[工具调用 {names} 已省略：{reason}]"
    downgraded["content"] = f"{content}\n{suffix}".strip()
    return downgraded


def _sanitize_openai_messages(messages: list) -> list:
    """Keep tool-call history valid for OpenAI-compatible APIs."""
    sanitized = []
    i = 0

    while i < len(messages):
        msg = messages[i]
        role = msg.get("role")

        if role == "tool":
            sanitized.append(_downgrade_tool_message(msg))
            i += 1
            continue

        if role == "assistant" and msg.get("tool_calls"):
            expected = [
                tc.get("id", "")
                for tc in msg.get("tool_calls", [])
                if isinstance(tc, dict) and tc.get("id")
            ]
            expected_set = set(expected)
            tool_msgs = []
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                tool_msgs.append(messages[j])
                j += 1

            found_set = {
                tm.get("tool_call_id", "")
                for tm in tool_msgs
                if isinstance(tm, dict) and tm.get("tool_call_id")
            }

            if expected_set and expected_set.issubset(found_set):
                sanitized.append(msg)
                emitted = set()
                for tm in tool_msgs:
                    tool_id = tm.get("tool_call_id", "")
                    if tool_id in expected_set and tool_id not in emitted:
                        sanitized.append(tm)
                        emitted.add(tool_id)
                    else:
                        sanitized.append(_downgrade_tool_message(tm))
            else:
                sanitized.append(_downgrade_assistant_tool_calls(msg, "历史中缺少匹配的工具结果"))
                for tm in tool_msgs:
                    sanitized.append(_downgrade_tool_message(tm))
            i = j
            continue

        sanitized.append(msg)
        i += 1

    return sanitized


# ── Anthropic → OpenAI request translation ──────────────────────────

def anth_to_openai(body: dict) -> dict:
    """Convert Anthropic Messages request to OpenAI Chat Completions."""
    model = body.get("model", "")
    supports_vision = _supports_vision(model)
    messages = body.get("messages", [])
    system = body.get("system", None)
    max_tokens = body.get("max_tokens", 4096)
    temperature = body.get("temperature", 0.7)

    oai_messages = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")

        if isinstance(content, list):
            text_parts = []
            thinking_parts = []
            image_urls = []
            tool_calls_oai = []
            tool_results = []

            for block in content:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type", "")
                if bt == "text":
                    text_parts.append(block.get("text", ""))
                elif bt == "thinking":
                    # Do not replay prior hidden reasoning into a different backend.
                    # Several OpenAI-compatible providers reject reasoning_content in
                    # input history, and it also bloats switch-time prompts.
                    continue
                elif bt == "image":
                    if not supports_vision:
                        text_parts.append("\n[系统提示：图片已由网关自动过滤，因为当前模型不支持视觉输入]")
                    else:
                        source = block.get("source", {})
                        image_urls.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{source.get('media_type', 'image/jpeg')};base64,{source.get('data', '')}"},
                        })
                elif bt == "tool_use":
                    tool_calls_oai.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    })
                elif bt == "tool_result":
                    content_val = block.get("content", "")
                    if isinstance(content_val, list):
                        parts = [c.get("text", "") for c in content_val if isinstance(c, dict) and c.get("type") == "text"]
                        content_val = "\n".join(parts) if parts else json.dumps(content_val)
                    elif not isinstance(content_val, str):
                        content_val = json.dumps(content_val)
                    if not content_val or content_val.strip() == "":
                        content_val = "Task completed successfully (no output)."
                    tool_results.append({
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": content_val,
                    })

            reasoning_text = "\n".join(thinking_parts) or None

            if role == "assistant" and tool_calls_oai:
                msg = {"role": "assistant", "content": "\n".join(text_parts) or None, "tool_calls": tool_calls_oai}
                if reasoning_text:
                    msg["reasoning_content"] = reasoning_text
                oai_messages.append(msg)
            elif tool_results:
                # Tool messages MUST come before user text (OpenAI validation)
                for tr in tool_results:
                    oai_messages.append({"role": "tool", "tool_call_id": tr["tool_call_id"], "content": tr["content"]})
                if text_parts:
                    oai_messages.append({"role": "user", "content": "\n".join(text_parts)})
            else:
                content_val = "\n".join(text_parts)
                if image_urls:
                    content_val = [*[{"type": "text", "text": t} for t in text_parts], *image_urls]
                msg = {"role": role, "content": content_val}
                if role == "assistant" and reasoning_text:
                    msg["reasoning_content"] = reasoning_text
                oai_messages.append(msg)
        else:
            oai_messages.append({"role": role, "content": content})

    if system:
        if isinstance(system, str):
            oai_messages.insert(0, {"role": "system", "content": system})
        elif isinstance(system, list):
            sys_text = "\n".join(b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text")
            if sys_text:
                oai_messages.insert(0, {"role": "system", "content": sys_text})

    sanitized = _sanitize_openai_messages(oai_messages)

    oai_body = {
        "model": model,
        "messages": sanitized,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": body.get("stream", False),
    }

    tools = body.get("tools")
    if tools:
        oai_tools = []
        for t in tools:
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {}),
                },
            })
        oai_body["tools"] = oai_tools
        tool_choice = body.get("tool_choice")
        if tool_choice:
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "any":
                oai_body["tool_choice"] = "auto"
            elif isinstance(tool_choice, dict) and tool_choice.get("type") == "tool":
                oai_body["tool_choice"] = {"type": "function", "function": {"name": tool_choice.get("name", "")}}

    return oai_body


def openai_to_anth(resp: dict, model: str) -> dict:
    """Convert OpenAI Chat Completions response to Anthropic Messages format."""
    choices = resp.get("choices") or [{}]
    choice = choices[0] if choices else {}
    oai_msg = choice.get("message", {})
    content = oai_msg.get("content", "") or ""
    reasoning = oai_msg.get("reasoning_content", "") or ""
    tool_calls = oai_msg.get("tool_calls") or []

    finish = choice.get("finish_reason", "stop")
    stop_reason = "end_turn"
    if finish == "length":
        stop_reason = "max_tokens"
    elif finish == "tool_calls" or tool_calls:
        stop_reason = "tool_use"

    blocks = []
    if reasoning:
        blocks.append({"type": "thinking", "thinking": reasoning, "signature": ""})
    if content:
        blocks.append({"type": "text", "text": content})
    for tc in tool_calls:
        fn = tc.get("function", {})
        args_str = fn.get("arguments", "{}")
        try:
            args = json.loads(args_str) if isinstance(args_str, str) else args_str
        except json.JSONDecodeError:
            args = {}
        blocks.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
            "name": fn.get("name", ""),
            "input": args,
        })
    if not blocks:
        blocks = [{"type": "text", "text": ""}]

    return {
        "id": resp.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": resp.get("usage", {}).get("prompt_tokens", 0),
            "output_tokens": resp.get("usage", {}).get("completion_tokens", 0),
        },
    }


# ── Streaming ────────────────────────────────────────────────────────

def stream_anth_to_openai(body: dict):
    """Stream Anthropic → OpenAI with SSE translation."""
    oai_body = anth_to_openai(body)
    oai_body["stream"] = True

    req = Request(
        f"{ONE_API}/v1/chat/completions",
        data=json.dumps(oai_body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {PROXY_TOKEN}"},
    )

    block_idx = 0
    phase = None
    finished = False
    pinged = False
    tool_states = {}

    try:
        with urlopen(req, timeout=120) as resp:
            for line in resp:
                line = line.decode().strip()
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    if not finished:
                        yield from _emit_stream_close(phase, block_idx)
                    break
                try:
                    chunk = json.loads(data_str)
                    choices = chunk.get("choices")
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta", {})
                    reasoning = delta.get("reasoning_content", "")
                    content = delta.get("content", "")

                    if reasoning:
                        if phase != "thinking":
                            yield _event("content_block_start", {"type": "content_block_start", "index": block_idx, "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
                            if not pinged:
                                yield f"event: ping\ndata: {json.dumps({'type': 'ping'})}\n\n"
                                pinged = True
                            phase = "thinking"
                        yield _event("content_block_delta", {"type": "content_block_delta", "index": block_idx, "delta": {"type": "thinking_delta", "thinking": reasoning}})

                    if content:
                        if phase == "thinking":
                            yield _event("content_block_stop", {"type": "content_block_stop", "index": block_idx})
                            block_idx = 1
                            phase = "text"
                            yield _event("content_block_start", {"type": "content_block_start", "index": block_idx, "content_block": {"type": "text", "text": ""}})
                        elif phase != "text" and phase != "tool_use":
                            phase = "text"
                            yield _event("content_block_start", {"type": "content_block_start", "index": block_idx, "content_block": {"type": "text", "text": ""}})
                            if not pinged:
                                yield f"event: ping\ndata: {json.dumps({'type': 'ping'})}\n\n"
                                pinged = True
                        yield _event("content_block_delta", {"type": "content_block_delta", "index": block_idx, "delta": {"type": "text_delta", "text": content}})

                    # Tool calls in delta
                    for tc in (delta.get("tool_calls") or []):
                        idx = tc.get("index", 0)
                        if idx not in tool_states:
                            if phase:
                                yield _event("content_block_stop", {"type": "content_block_stop", "index": block_idx})
                            block_idx += 1
                            phase = "tool_use"
                            tool_states[idx] = {"id": tc.get("id") or "", "name": "", "args_str": ""}
                        ts = tool_states[idx]
                        if tc.get("id"):
                            ts["id"] = tc["id"]
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            ts["name"] = fn["name"]
                            yield _event("content_block_start", {"type": "content_block_start", "index": block_idx, "content_block": {"type": "tool_use", "id": ts["id"], "name": ts["name"], "input": {}}})
                        if fn.get("arguments"):
                            ts["args_str"] += fn["arguments"]
                            yield _event("content_block_delta", {"type": "content_block_delta", "index": block_idx, "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]}})

                    finish = choice.get("finish_reason")
                    if finish:
                        yield from _emit_stream_close(phase, block_idx)
                        finished = True

                except (json.JSONDecodeError, IndexError, KeyError):
                    continue
    except (HTTPError, OSError) as e:
        err_body = e.read().decode() if hasattr(e, "read") else str(e)
        if not finished:
            yield from _emit_stream_close(phase, block_idx)
        yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': f'Upstream error: {err_body}'}})}\n\n"


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


def _emit_stream_close(phase: str, block_idx: int):
    ev = _close_current_block(phase, block_idx)
    if ev:
        yield ev
    yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 0}})}\n\n"
    yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"


def _estimate_tokens(body: dict) -> int:
    total = 0
    for msg in body.get("messages", []):
        content = msg.get("content", "")
        if isinstance(content, str):
            total += max(len(content) // 3, 1)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    total += max(len(block.get("text", "")) // 3, 1)
    system = body.get("system", "")
    if isinstance(system, str):
        total += max(len(system) // 3, 1)
    elif isinstance(system, list):
        for b in system:
            if isinstance(b, dict) and b.get("type") == "text":
                total += max(len(b.get("text", "")) // 3, 1)
    return max(total, 1)


def _close_current_block(phase: str, block_idx: int):
    if phase in ("thinking", "text", "tool_use"):
        return _event("content_block_stop", {"type": "content_block_stop", "index": block_idx})
    return ""


# ── HTTP Handler ─────────────────────────────────────────────────────

class ProxyHandler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/v1/messages":
            self._handle_messages()
        elif path == "/v1/messages/count_tokens":
            self._handle_count_tokens()
        else:
            self._proxy_pass("POST")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/v1/models":
            self._handle_models()
        else:
            self._proxy_pass("GET")

    def _handle_messages(self):
        content_len = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(content_len))
        except (json.JSONDecodeError, ValueError):
            self.send_response(400)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON body"}}).encode())
            return

        msgs = body.get("messages", [])
        last_user = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                c = m.get("content", "")
                if isinstance(c, list):
                    c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                last_user = str(c)[:200]
                break
        sys.stderr.write(f"[proxy] model={body.get('model','?')} stream={body.get('stream')} tokens={body.get('max_tokens','?')} msgs={len(msgs)} last_user={last_user}\n")

        if body.get("stream"):
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            model = body.get("model", "")
            msg_id = f"msg_{uuid.uuid4().hex[:24]}"
            input_tokens = _estimate_tokens(body)

            start = {"type": "message_start", "message": {"id": msg_id, "type": "message", "role": "assistant", "model": model, "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": input_tokens, "output_tokens": 0}}}
            self.wfile.write(f"event: message_start\ndata: {json.dumps(start)}\n\n".encode())

            for chunk_str in stream_anth_to_openai(body):
                self.wfile.write(chunk_str.encode())
                self.wfile.flush()
        else:
            oai_body = anth_to_openai(body)
            req = Request(
                f"{ONE_API}/v1/chat/completions",
                data=json.dumps(oai_body).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {PROXY_TOKEN}"},
            )
            try:
                with urlopen(req, timeout=120) as resp:
                    oai_resp = json.loads(resp.read())
                anth_resp = openai_to_anth(oai_resp, body.get("model", ""))
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(anth_resp).encode())
            except (HTTPError, json.JSONDecodeError, OSError) as e:
                err = e.read().decode() if hasattr(e, "read") else str(e)
                self.send_response(getattr(e, "code", 502))
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"type": "error", "error": {"type": "api_error", "message": err}}).encode())

    def _handle_count_tokens(self):
        content_len = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(content_len))
        except (json.JSONDecodeError, ValueError):
            self.send_response(400)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode())
            return
        resp = {"input_tokens": _estimate_tokens(body)}
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp).encode())

    def _handle_models(self):
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": MODELS}).encode())

    def _proxy_pass(self, method):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len > 0 else b""
        req = Request(
            f"{ONE_API}{self.path}",
            data=body if body else None,
            headers={"Content-Type": self.headers.get("Content-Type", "application/json"), "Authorization": f"Bearer {PROXY_TOKEN}"},
        )
        try:
            with urlopen(req, timeout=30) as resp:
                data = resp.read()
            self.send_response(resp.getcode())
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)
        except (HTTPError, OSError) as e:
            err = e.read().decode() if hasattr(e, "read") else str(e)
            self.send_response(getattr(e, "code", 502))
            self._cors()
            self.end_headers()
            self.wfile.write(err.encode())

    def log_message(self, format, *args):
        sys.stderr.write(f"[proxy] {args[0]}\n")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
    print(f"🔀 Anthropic→OpenAI proxy listening on :{port}  →  {ONE_API}")
    ThreadingHTTPServer(("0.0.0.0", port), ProxyHandler).serve_forever()
