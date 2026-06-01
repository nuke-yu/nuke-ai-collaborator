
import asyncio
import time
import statistics

async def loop_lag_monitor(duration_sec, interval=0.01):
    lags = []
    start_time = time.perf_counter()
    
    while time.perf_counter() - start_time < duration_sec:
        t0 = time.perf_counter()
        await asyncio.sleep(interval)
        t1 = time.perf_counter()
        lag = (t1 - t0) - interval
        lags.append(max(0, lag * 1000))
        
    return lags

def cpu_bound_work():
    s = 0
    for i in range(500000):
        s += i
    return s

async def worker_task(duration_sec):
    start_time = time.perf_counter()
    while time.perf_counter() - start_time < duration_sec:
        await asyncio.sleep(0.05)
        cpu_bound_work()

async def main():
    print('--- Event Loop Lag Benchmark (K=4) ---')
    duration = 3.0
    monitor = asyncio.create_task(loop_lag_monitor(duration))
    bot_tasks = [asyncio.create_task(worker_task(duration)) for _ in range(4)]
    
    await asyncio.gather(*bot_tasks)
    lags = await monitor
    
    avg = statistics.mean(lags)
    p99 = statistics.quantiles(lags, n=100)[98]
    max_lat = max(lags)
    
    print(f'Average loop lag: {avg:.2f} ms')
    print(f'P99 loop lag:     {p99:.2f} ms')
    print(f'Max loop lag:     {max_lat:.2f} ms')

if __name__ == '__main__':
    asyncio.run(main())
