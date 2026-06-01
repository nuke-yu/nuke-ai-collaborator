import asyncio
import time
import sys
import statistics
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runtime import ipc

async def run_worker(addr, num_messages):
    reader, writer = await ipc.connect(addr)
    # Send HELLO
    await ipc.send_msg(writer, {"type": ipc.protocol.HELLO, "worker_id": "bench_worker"})
    
    # Wait for ping, send pong
    try:
        for _ in range(num_messages):
            msg = await ipc.recv_msg(reader)
            if msg.get("type") == "ping":
                await ipc.send_msg(writer, {"type": "pong", "ts": msg["ts"]})
    finally:
        writer.close()

async def run_supervisor(addr, num_messages):
    latencies = []
    
    async def on_conn(reader, writer):
        hello = await ipc.recv_msg(reader)
        # Warmup
        for _ in range(10):
            await ipc.send_msg(writer, {"type": "ping", "ts": time.perf_counter()})
            await ipc.recv_msg(reader)
            
        # Benchmark
        for _ in range(num_messages):
            start = time.perf_counter()
            await ipc.send_msg(writer, {"type": "ping", "ts": start})
            pong = await ipc.recv_msg(reader)
            end = time.perf_counter()
            latencies.append((end - start) * 1000) # ms
            
        writer.close()

    server = await ipc.serve(addr, on_conn)
    return server, latencies

async def main():
    if sys.platform == "win32":
        addr = r"\\.\pipe\nuke_bench_ipc"
    else:
        addr = "/tmp/nuke_bench_ipc.sock"
        
    num_messages = 1000
    
    server, latencies_list = await run_supervisor(addr, num_messages)
    
    worker_task = asyncio.create_task(run_worker(addr, num_messages))
    await worker_task
    
    server.close()
    await server.wait_closed()
    
    avg = statistics.mean(latencies_list)
    p99 = statistics.quantiles(latencies_list, n=100)[98]
    max_lat = max(latencies_list)
    
    print(f"--- IPC Latency Benchmark ({num_messages} messages) ---")
    print(f"Average: {avg:.3f} ms")
    print(f"P99:     {p99:.3f} ms")
    print(f"Max:     {max_lat:.3f} ms")
    print("---------------------------------------------")

if __name__ == "__main__":
    asyncio.run(main())
