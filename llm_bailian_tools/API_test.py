import os
import json
import requests
import sys

# ============使用者自行填写个人阿里云百炼密钥============
API_KEY = "请在此处填写你自己的阿里云API-Key"

BASE_HOST = "https://ws-xg6ep5qht856sb5v.cn-beijing.maas.aliyuncs.com"

print("[info] 当前环境代理：")
for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    print(f"  {name}={os.environ.get(name)!r}")

for name in ("ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
    print(f"  {name}={os.environ.get(name)!r}")

session = requests.Session()
session.trust_env = False

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

checks = [
    {
        "name": "api/v1 text-generation qwen3.7-max",
        "url": f"{BASE_HOST}/api/v1/services/aigc/text-generation/generation",
        "json": {
            "model": "qwen3.7-max",
            "input": {"messages": [{"role": "user", "content": "你好，测试连接"}]},
            "parameters": {"result_format": "message"},
        },
    },
    {
        "name": "compatible-mode chat qwen3.8-max",
        "url": f"{BASE_HOST}/compatible-mode/v1/chat/completions",
        "json": {
            "model": "qwen3.8-max",
            "messages": [{"role": "user", "content": "你好，测试连接"}],
            "max_tokens": 64,
        },
    },
]

for check in checks:
    print("\n=====", check["name"], "=====")
    print("url:", check["url"])
    try:
        res = session.post(check["url"], headers=headers, json=check["json"], timeout=20)
        print("status:", res.status_code)
        try:
            print(json.dumps(res.json(), ensure_ascii=False, indent=2))
        except ValueError:
            print(res.text)
    except Exception as exc:
        print("request failed:", repr(exc))
