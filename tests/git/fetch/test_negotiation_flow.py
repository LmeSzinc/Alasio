"""
演示 Git Fetch 的协商 (Negotiation) 流程

演示步骤:
1. 获取远程引用列表 (Discovery)
2. 初始拉取: 下载 v0.5.1 版本 (Initial Fetch)
3. 增量拉取: 告知服务器已有 v0.5.1，请求 master (Negotiation)
"""
import trio
import os
from alasio.git.fetch.argument import Arguments
from alasio.git.fetch.transport_http import HttpTransport
from alasio.git.fetch.pkt import FetchPayload

async def show_negotiation_flow():
    # 使用 GitHub 地址，确保 v2 协议完美运行
    repo_url = "https://github.com/LmeSzinc/AzurLaneAutoScript"
    repo_path = r'c:\Users\s-desktop\docker\alasio'
    
    # 准备工作
    args = Arguments(repo_path=repo_path, repo_url=repo_url)
    transport = HttpTransport(args)
    
    print("=" * 60)
    print("🚀 阶段 1: 发现引用 (Discovery)")
    print("=" * 60)
    refs = await transport.fetch_refs()
    
    # 获取两个关键节点的 SHA1
    # v0.5.1 (旧版本)
    tag_ref = b'refs/tags/v0.5.1'
    # master (最新版本)
    master_ref = b'refs/tags/v0.5.2'
    
    v051_sha = None
    master_sha = None
    
    for sha, name in refs.items():
        if name == tag_ref:
            v051_sha = sha.decode()
        if name == master_ref:
            master_sha = sha.decode()
            
    print(f"📌 v0.5.1 SHA: {v051_sha}")
    print(f"📌 master SHA: {master_sha}")

    print("\n" + "=" * 60)
    print("🚀 阶段 2: 初始拉取 (下载 v0.5.1)")
    print("   模拟场景: 你本地还没有任何代码")
    print("=" * 60)
    
    # 构建 Payload: 只有 want，没有 have
    payload_initial = FetchPayload()
    # 类似 git fetch --depth 1 (为了演示快一点)
    payload_initial.add_line(f"want {v051_sha} {transport.capabilities.as_string()}")
    payload_initial.add_delimiter()
    payload_initial.add_done()
    
    pack_initial = os.path.join(repo_path, "v051_initial.pack")
    print("📡 正在请求 v0.5.1 的完整数据...")
    await transport.fetch_pack_v1(payload_initial, output_file=pack_initial)
    
    size_initial = os.path.getsize(pack_initial)
    print(f"✅ 初始拉取完成! 大小: {size_initial / 1024:.2f} KB")

    print("\n" + "=" * 60)
    print("🚀 阶段 3: 增量拉取 (协商 Negotiation)")
    print("   模拟场景: 你已有 v0.5.1，现在想更新到 master")
    print("=" * 60)
    
    # 构建 Payload: 包含 want (新) 和 have (旧)
    # 这就是调用流程中最重要的部分！
    payload_inc = FetchPayload()
    
    # 1. 告诉服务器我要 master
    payload_inc.add_line(f"want {master_sha} {transport.capabilities.as_string()}")
    payload_inc.add_delimiter()
    
    # 2. 核心：告诉服务器我本地已经有了 v0.5.1 的 SHA
    # 服务器会根据这个 SHA 寻找最短的差异路径
    print(f"🤝 发送协商信息: have {v051_sha}")
    payload_inc.add_have(v051_sha)
    
    # 3. 结束请求
    payload_inc.add_done()
    
    pack_inc = os.path.join(repo_path, "master_incremental.pack")
    print("📡 正在请求从 v0.5.1 到 master 的差异数据...")
    
    # 在 v2 协议下，这个 have 的处理会让返回的包极小
    await transport.fetch_pack_v2(payload_inc, output_file=pack_inc)
    
    size_inc = os.path.getsize(pack_inc)
    print(f"✅ 增量拉取完成! 大小: {size_inc / 1024:.2f} KB")
    
    print("\n" + "=" * 60)
    print("📊 结果对比")
    print("=" * 60)
    print(f"📦 完整版 (v0.5.1): {size_initial / 1024:.2f} KB")
    print(f"📦 增量版 (master - v0.5.1): {size_inc / 1024:.2f} KB")
    print(f"📉 节省流量: {(1 - size_inc/size_initial)*100:.1f}%")

if __name__ == "__main__":
    trio.run(show_negotiation_flow)
