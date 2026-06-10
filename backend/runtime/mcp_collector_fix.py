    async def _handle_auth_start(self, frame: dict) -> None:
        rid = frame.get("request_id")
        origin = frame.get("origin_worker_id")
        server = frame.get("server")
        gid = frame.get("group_id")
        tid = frame.get("trace_id")

        async def reply(result, is_error):
            await ipc.send_msg(self._writer, ipc.protocol.envelope(
                ipc.protocol.MCP_RESULT, group_id=gid, trace_id=tid,
                request_id=rid, origin_worker_id=origin, result=result, is_error=is_error,
            ))

        prov = self._find_provider(server)
        if prov is None or not prov.url:
            await reply(f"[MCP 认证] 未找到 remote server '{server}'（仅 remote+oauth 适用）", True)
            return

        # Use per-server lock to prevent race condition in OAuth flow initiation
        # (fix for DFT-008: MCP_AUTH_START 并发锁非原子问题)
        lock = self._auth_locks.setdefault(server, asyncio.Lock())
        async with lock:
            # Double-check after acquiring lock
            if server in self._auth_inflight:
                await reply(f"[MCP 认证] '{server}' 的授权正在进行中，请使用之前返回的链接完成", True)
                return

            self._auth_inflight.add(server)
            spawned = False
            try:
                prov.set_auth(await self._build_auth_provider(prov, server))
                url_fut = self._flows.begin(server)
                t = asyncio.create_task(self._reinit_with_auth(prov, server))
                self._tasks.add(t); t.add_done_callback(self._tasks.discard)
                spawned = True   # the reinit task now owns releasing _auth_inflight
                url = await asyncio.wait_for(url_fut, timeout=60)
                await reply(f"请在浏览器打开以下链接完成 '{server}' 的授权，完成后工具会自动可用：\n{url}", False)
            except Exception as e:
                self._flows.fail(server, str(e))
                await reply(f"[MCP 认证错误] {e}", True)
            finally:
                if not spawned:
                    self._auth_inflight.discard(server)   # flow never started → release now
