# def gen():
#     print("第一步")
#     yield 1
#     print("第二步")
#     yield 2
#     print("第三步")
#
# g = gen()          # 注意：这里一句都没打印！只是创建了生成器
# print(next(g))     # 第一步 → 1
# print(next(g))     # 第二步 → 2
# print(next(g))     # 第三步 → 报错 StopIteration

def echo():
    while True:
        received = yield        # yield 交出去 None，同时等外面 send 值进来
        print("收到:", received)

e = echo()
next(e)              # 先启动，跑到 yield 停住
e.send("hello")
e.send("world")


def inner():
    yield 1
    yield 2

def outer():
    yield from inner()   # 把 inner 的值一个个转发出去
    yield 3

for x in outer():
    print(x)