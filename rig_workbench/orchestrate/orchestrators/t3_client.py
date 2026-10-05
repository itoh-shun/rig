"""Optional MCP SDK 1.x transport, owned entirely by one loop thread and task."""
from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass
from datetime import timedelta
import json
import threading
import time
from typing import Mapping, Protocol

from .config import valid_endpoint


class T3Client(Protocol):
    def list_tools(self, *, timeout_s: float) -> Mapping[str, Mapping[str, object]]: ...
    def call_tool(self, name: str, arguments: Mapping[str, object], *, timeout_s: float) -> Mapping[str, object]: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class _Sdk:
    session: object
    transport: object
    http_client: object
    pagination: object


def _sdk_factory():
    # SDK and its HTTP dependency are imported only by the configured client factory.
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.types import PaginatedRequestParams
    import httpx
    return _Sdk(ClientSession, streamable_http_client, httpx.AsyncClient, PaginatedRequestParams)


class McpT3Client:
    """Synchronous callers submit work to a single lifetime-owning async task.

    Session/transport contexts must enter and exit in that same task: anyio's
    cancel scopes cannot safely be moved between separate submit coroutines.
    SDK shape follows python-sdk v1.28.1; external T3 schemas remain unverified.
    """
    contract_verified = False

    def __init__(self, endpoint, token, *, timeout_s=5, sdk_factory=None):
        if not valid_endpoint(endpoint) or not isinstance(token, str) or not token:
            raise RuntimeError("invalid_t3_client_configuration")
        if token in endpoint:
            raise RuntimeError("secret_in_endpoint")
        started = time.monotonic()
        if timeout_s <= 0:
            raise RuntimeError("mcp_initialize_timeout")
        self._initialize_deadline = started + timeout_s
        self._endpoint = endpoint
        self._token = token
        self._ready = concurrent.futures.Future()
        self._stopped = threading.Event()
        self._closed = False
        self._close_lock = threading.Lock()
        self._loop = None
        self._queue = None
        self._owner_task = None
        self._thread = threading.Thread(target=self._thread_main,
                                        args=(sdk_factory or _sdk_factory, timeout_s),
                                        name="rig-t3-mcp", daemon=True)
        self._thread.start()
        try:
            self._ready.result(timeout=max(0, self._initialize_deadline - time.monotonic()))
            self._check_initialize_deadline()
        except KeyboardInterrupt:
            self.close(timeout_s=max(0, self._initialize_deadline - time.monotonic()))
            raise
        except Exception as error:
            self.close(timeout_s=max(0, self._initialize_deadline - time.monotonic()))
            if str(error) == "mcp_sdk_unavailable":
                raise RuntimeError("mcp_sdk_unavailable") from None
            raise RuntimeError("mcp_initialize_failed") from None

    def _check_initialize_deadline(self):
        if self._closed or time.monotonic() >= self._initialize_deadline:
            raise TimeoutError("mcp_initialize_timeout")

    def _thread_main(self, sdk_factory, timeout_s):
        try:
            asyncio.run(self._owner(sdk_factory, timeout_s))
        except BaseException:
            if not self._ready.done():
                self._ready.set_exception(RuntimeError("mcp_session_failed"))
        finally:
            self._stopped.set()

    async def _owner(self, sdk_factory, timeout_s):
        loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        owner = asyncio.current_task()
        self._owner_task = owner
        # Publishing the loop lets close submit callbacks using the queue/task.
        self._loop = loop
        timer = self._loop.call_later(max(0, self._initialize_deadline - time.monotonic()), owner.cancel)
        # A bounded heartbeat lets thread submissions/shutdown progress even when
        # the host refuses asyncio's AF_UNIX self-pipe write. No network or provider
        # is launched by this local timer; unconfigured Native never creates it.
        # Keep it alive through asyncio.run's executor shutdown too: a factory
        # worker may finish only after the owner has already been cancelled.
        # Closing the loop discards the final scheduled tick.
        def tick():
            self._loop.call_later(0.05, tick)
        tick()
        pending = None
        try:
            self._check_initialize_deadline()
            try:
                sdk = await asyncio.to_thread(sdk_factory)
            except ImportError:
                self._ready.set_exception(RuntimeError("mcp_sdk_unavailable"))
                return
            self._check_initialize_deadline()
            async def endpoint_guard(request):
                if str(request.url) != self._endpoint:
                    request.headers.pop("Authorization", None)
                    raise RuntimeError("mcp_endpoint_changed")
            self._check_initialize_deadline()
            http_context = sdk.http_client(headers={"Authorization": "Bearer " + self._token},
                                           follow_redirects=False, timeout=timeout_s,
                                           event_hooks={"request": [endpoint_guard]})
            self._check_initialize_deadline()
            async with http_context as http:
                self._check_initialize_deadline()
                transport_context = sdk.transport(self._endpoint, http_client=http, terminate_on_close=True)
                self._check_initialize_deadline()
                async with transport_context as streams:
                    self._check_initialize_deadline()
                    session_context = sdk.session(streams[0], streams[1])
                    self._check_initialize_deadline()
                    async with session_context as session:
                        self._check_initialize_deadline()
                        await session.initialize()
                        self._check_initialize_deadline()
                        timer.cancel()
                        self._ready.set_result(True)
                        while True:
                            pending = await self._queue.get()
                            if pending is None:
                                break
                            kind, name, args, deadline, future = pending
                            if future.cancelled():
                                continue
                            remaining = deadline - time.monotonic()
                            try:
                                if remaining <= 0:
                                    raise TimeoutError()
                                operation = (self._list_tools(session, sdk, deadline) if kind == "list"
                                             else session.call_tool(name, arguments=dict(args),
                                                                    read_timeout_seconds=timedelta(seconds=remaining)))
                                response = await asyncio.wait_for(operation, remaining)
                                value = response if kind == "list" else self._decode_result(response)
                                if not future.done():
                                    future.set_result(value)
                            except Exception:
                                if not future.done():
                                    future.set_exception(RuntimeError("mcp_operation_failed"))
                            finally:
                                pending = None
        except BaseException:
            if not self._ready.done():
                self._ready.set_exception(RuntimeError("mcp_initialize_failed"))
            if pending is not None and not pending[-1].done():
                pending[-1].set_exception(RuntimeError("mcp_session_closed"))
        finally:
            timer.cancel()
            while self._queue is not None and not self._queue.empty():
                waiting = self._queue.get_nowait()
                if waiting is not None and not waiting[-1].done():
                    waiting[-1].set_exception(RuntimeError("mcp_session_closed"))

    async def _list_tools(self, session, sdk, deadline):
        tools, seen, cursor = {}, set(), None
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError()
            response = await session.list_tools(params=sdk.pagination(cursor=cursor) if cursor is not None else None)
            for tool in response.tools:
                if tool.name in tools:
                    raise ValueError("duplicate tool")
                tools[tool.name] = tool.inputSchema
            cursor = response.nextCursor
            if cursor is None:
                return tools
            if not isinstance(cursor, str) or cursor in seen:
                raise ValueError("invalid pagination cursor")
            seen.add(cursor)

    @staticmethod
    def _decode_result(response):
        if response.isError:
            raise ValueError("tool failed")
        if response.structuredContent is not None:
            value = response.structuredContent
        elif len(response.content) == 1 and getattr(response.content[0], "type", None) == "text":
            value = json.loads(response.content[0].text)
        else:
            raise ValueError("unsupported result content")
        if not isinstance(value, dict):
            raise ValueError("invalid structured result")
        return value

    def _submit(self, kind, name, arguments, timeout_s):
        if self._closed or self._stopped.is_set() or timeout_s <= 0:
            raise RuntimeError("mcp_session_unavailable")
        future = concurrent.futures.Future()
        request = (kind, name, arguments, time.monotonic() + timeout_s, future)
        self._loop.call_soon_threadsafe(self._queue.put_nowait, request)
        try:
            return future.result(timeout=timeout_s)
        except KeyboardInterrupt:
            future.cancel()
            raise
        except Exception:
            future.cancel()
            raise RuntimeError("mcp_operation_failed") from None

    def list_tools(self, *, timeout_s):
        return self._submit("list", None, {}, timeout_s)

    def call_tool(self, name, arguments, *, timeout_s):
        return self._submit("call", name, arguments, timeout_s)

    def _schedule_close(self, callback, *args):
        try:
            self._loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            # The owner can close its loop between the liveness check and submit.
            if not self._loop.is_closed():
                raise

    def close(self, *, timeout_s=2):
        deadline = time.monotonic() + max(0, timeout_s)
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            if self._loop is not None and self._loop.is_running():
                self._schedule_close(self._queue.put_nowait, None)
            self._thread.join(timeout=min(1, max(0, deadline - time.monotonic())))
            if self._thread.is_alive() and self._loop is not None:
                def cancel_owner():
                    self._owner_task.cancel()
                    # A finalizer may begin only after the first cancellation is
                    # delivered. A second bounded cancellation prevents it from
                    # consuming a fresh, unbounded close budget.
                    self._loop.call_later(0.05, self._owner_task.cancel)
                self._schedule_close(cancel_owner)
                self._thread.join(timeout=max(0, deadline - time.monotonic()))
