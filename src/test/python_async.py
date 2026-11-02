import asyncio
import time

async def task(name, seconds):
    print(f"{name} 开始")
    await asyncio.sleep(seconds)   # 挂起，让出控制权
    print(f"{name} 结束")
    return name

async def main():
    start = time.time()
    # 同时启动三个协程
    await asyncio.gather(
        task("A", 2),
        task("B", 1),
        task("C", 3),
    )
    print(f"总耗时: {time.time() - start:.1f}s")

asyncio.run(main())