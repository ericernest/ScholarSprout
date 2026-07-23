# 在项目根目录创建

import .requests, json, sys

BASE = "http://127.0.0.1:8000/paper_reading"
HEADERS = {"Content-Type": "application/json"}
SESSION = "test_verify_001"

def api(action, **kwargs):
    """发送请求并美化打印结果"""
    body = {"action": action, "session_id": SESSION, **kwargs}
    print(f"\n{'='*60}")
    print(f"📤 {action}: {json.dumps({k:v for k,v in body.items() if k != 'session_id'}, ensure_ascii=False)}")
    try:
        r = requests.post(BASE, json=body, headers=HEADERS, timeout=120)
        data = r.json()
        # 提取关键信息
        status = data.get("content", data).get("status", "?")
        print(f"📥 status={status}")
        if status == "ok":
            inner = data.get("content", data).get("data", {})
            # 打印摘要
            for key in ["count", "paper_id", "title", "agent_response", "message"]:
                if key in inner:
                    val = str(inner[key])[:200]
                    print(f"   {key}: {val}")
            if "papers" in inner:
                for i, p in enumerate(inner["papers"][:3]):
                    print(f"   论文{i+1}: {p.get('title','?')[:80]} ({p.get('year','?')})")
        else:
            print(f"   错误: {data.get('content', data).get('message', data)}")
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        print(f"   请确认 gateway 已启动: python -m cli.main gateway --host 127.0.0.1 --port 8000")

# ── 第一步: 搜索论文 ──
r1 = requests.post(BASE, json={
    "action": "search_paper",
    "search_query": "attention is all you need",
    "search_source": "arxiv",
    "search_max_results": 3,
    "session_id": SESSION,
}, headers=HEADERS, timeout=60)
d1 = r1.json()
paper = d1["content"]["data"]["papers"][0]
PAPER_ID = paper["paper_id"]
print(f"✅ 搜索成功: {paper['title'][:60]} (id={PAPER_ID})")

# ── 第二步: 上传并解析 PDF ──
arxiv_id = paper["source_id"]  # "arxiv:1706.03762"
pdf_url = f"https://arxiv.org/pdf/{arxiv_id.replace('arxiv:', '')}.pdf"
r2 = requests.post(BASE, json={
    "action": "upload_paper",
    "pdf_url": pdf_url,
    "session_id": SESSION,
}, headers=HEADERS, timeout=120)
d2 = r2.json()
print(f"✅ PDF 解析完成: {d2['content']['data']['title'][:60]}, {d2['content']['data']['sections_count']} 个章节")

# ── 第三步: 开始阅读（调用 Agent） ──
r3 = requests.post(BASE, json={
    "action": "start_reading",
    "paper_id": PAPER_ID,
    "content": "请分析这篇论文的摘要，用中文告诉我它的核心贡献和方法要点",
    "session_id": SESSION + "_read",
}, headers=HEADERS, timeout=180)
d3 = r3.json()
agent_text = d3["content"]["data"].get("agent_response", "无回复")
calls = d3["content"]["data"].get("model_calls", "?")
dur = d3["content"]["data"].get("duration_ms", "?")
print(f"✅ Agent 回复 ({calls} 次调用, {dur}ms):")
print(f"{agent_text[:500]}")

# ── 第四步: 获取进度 ──
r4 = requests.post(BASE, json={
    "action": "get_progress",
    "session_id": SESSION + "_read",
}, headers=HEADERS, timeout=30)
d4 = r4.json()
print(f"✅ 阅读进度: {d4['content']['data'].get('formatted', d4['content']['data'])}")

print(f"\n{'='*60}")
print("🎉 全流程验证通过！")

