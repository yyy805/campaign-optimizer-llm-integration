# llm_base_call.py
from API_test import API_KEY, BASE_HOST, session, headers

def simple_chat(prompt: str, model: str = "qwen3.8-max", max_tokens: int = 512):
    """基础对话调用函数"""
    url = f"{BASE_HOST}/compatible-mode/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens
    }
    resp = session.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code == 200:
        return resp.json()["choices"][0]["message"]["content"]
    else:
        return f"调用失败：{resp.status_code} {resp.text}"

# 测试运行
if __name__ == "__main__":
    print("AI对话已就绪，输入 quit 即可结束聊天")
    while True:
        question = input("你：")
        if question == "quit":
            print("对话结束")
            break
        ans = simple_chat(question)
        print(f"AI：{ans}\n")