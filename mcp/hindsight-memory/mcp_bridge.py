"""Project-scoped stdio access to the existing authenticated Hindsight MCP."""
from copy import deepcopy
import asyncio
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

for stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8')

from fastmcp.server import create_proxy
from fastmcp.server.middleware import Middleware
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, ErrorData, TextContent

from api_relay import _redact

ROOT = Path(__file__).resolve().parent.parent


def _safe_error(error, token):
    if isinstance(error, MCPError):
        original = error.error.model_dump(mode='json', by_alias=True)
        cleaned = _redact(original, token)
        return error if cleaned == original else MCPError.from_error_data(ErrorData.model_validate(cleaned))
    message = str(error)
    cleaned = _redact(message, token)
    return error if cleaned == message else ToolError(cleaned)


class ProjectBankGuard(Middleware):
    def __init__(self, token=''):
        super().__init__()
        self._token = token

    async def on_list_tools(self, context, call_next):
        result = []
        try:
            tools = await call_next(context)
        except Exception as error:
            cleaned = _safe_error(error, self._token)
            if cleaned is error:
                raise
            raise cleaned from None
        for tool in tools:
            schema = deepcopy(tool.parameters)
            if 'bank_id' in schema.get('properties', {}):
                schema['properties']['bank_id'] = {
                    'type': 'string', 'minLength': 1,
                    'description': 'Explicit project bank ID, shared by the same project across machines and agents. Never use default.',
                }
                schema['required'] = list(dict.fromkeys(schema.get('required', []) + ['bank_id']))
                if tool.name == 'retain':
                    schema['properties']['operation_id'] = {
                        'type': 'string', 'format': 'uuid',
                        'description': 'Optional caller-allocated operation UUID. With a stable document_id and unchanged payload, this uses the native async REST replay contract. Keep the same UUID for one intent; never reuse it for new content.',
                    }
                tool = tool.model_copy(update={'parameters': schema})
            elif tool.name != 'list_banks':
                raise ToolError('Upstream tool has no project bank scope; review the bridge before enabling it: ' + tool.name)
            result.append(tool)
        return result

    async def on_call_tool(self, context, call_next):
        try:
            tool = await context.fastmcp_context.fastmcp.get_tool(context.message.name)
            if 'bank_id' in tool.parameters.get('properties', {}):
                bank = (context.message.arguments or {}).get('bank_id')
                if not isinstance(bank, str) or not bank.strip() or bank != bank.strip() or bank.casefold() == 'default':
                    raise ToolError('An explicit non-default project bank_id is required. Resolve the project identity before calling this tool.')
            elif tool.name != 'list_banks':
                raise ToolError('Tool has no confirmed project bank scope.')
            arguments = context.message.arguments or {}
            if tool.name == 'retain' and 'operation_id' in arguments:
                from api_relay import handle_request
                request = {
                    'action': 'retain',
                    'bank_id': arguments.get('bank_id'),
                    'operation_id': arguments['operation_id'],
                    'document_id': arguments.get('document_id'),
                    'payload': {key: value for key, value in arguments.items()
                                if key not in ('bank_id', 'operation_id', 'document_id')},
                }
                receipt = await asyncio.to_thread(handle_request, request, root=ROOT)
                if receipt['state'] == 'SUBMITTED':
                    accepted = {
                        'status': 'accepted',
                        'operation_id': receipt['operation_id'],
                        'message': 'Async operation accepted. Query this operation ID to confirm extraction completion.',
                    }
                    result = ToolResult(content=[TextContent(type='text', text=json.dumps(accepted))],
                                        structured_content=accepted)
                else:
                    result = ToolResult(content=[TextContent(type='text', text=json.dumps(receipt))],
                                        structured_content=receipt, is_error=True)
            else:
                result = await call_next(context)
        except Exception as error:
            cleaned = _safe_error(error, self._token)
            if cleaned is error:
                raise
            raise cleaned from None
        native = result.to_mcp_result()
        if not isinstance(native, CallToolResult):
            # FastMCP also returns a content list or (content, structured) tuple
            # for fresh results. Normalize using its installed SDK constructor.
            native = CallToolResult(content=result.content,
                                    structured_content=result.structured_content,
                                    is_error=result.is_error, _meta=result.meta)
        original = native.model_dump(mode='json', by_alias=True)
        cleaned = _redact(original, self._token)
        if cleaned == original:
            return result
        native = CallToolResult.model_validate(cleaned)
        # Rebuild instead of copying: FastMCP may retain a raw MCP result whose
        # wire serialization would otherwise bypass edits to visible fields.
        return ToolResult(content=native.content, structured_content=native.structured_content,
                          meta=cleaned.get('_meta'), is_error=native.is_error)


def build_proxy():
    config = {}
    for line in (ROOT / 'config' / 'mcp-client.env').read_text(encoding='utf-8-sig').splitlines():
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            config[key.strip()] = value.strip().strip('\"\'')
    url = config['HINDSIGHT_WIN_LOCAL_MCP_URL']
    parsed = urlsplit(url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username or parsed.password:
        raise ValueError('The local MCP bridge must connect to an HTTP loopback URL.')
    shared = dict(line.split('=', 1) for line in (ROOT / 'config' / 'shared.env').read_text(encoding='utf-8-sig').splitlines() if line and not line.startswith('#') and '=' in line)
    token = shared['HINDSIGHT_API_TENANT_API_KEY'].strip()
    if not token:
        raise ValueError('Hindsight service authentication is missing.')
    transport = StreamableHttpTransport(url, headers={'Authorization': 'Bearer ' + token})
    proxy = create_proxy(transport, name='hindsight', provider_error_strategy='raise')
    proxy.add_middleware(ProjectBankGuard(token=token))
    return proxy


if __name__ == '__main__':
    build_proxy().run(transport='stdio', show_banner=False)
