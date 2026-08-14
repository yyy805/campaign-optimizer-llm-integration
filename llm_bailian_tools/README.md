# 阿里云通义大模型API调用基础工具
## 1、文件说明
- API_test.py：接口、密钥、请求会话统一配置文件
- llm_base_call.py：大模型对话调用封装函数，业务直接复用

## 2、使用前置准备
1. 安装依赖库，终端执行：
pip install requests
2. 两个py文件**必须放在同一个文件夹**，不可拆分。

## 3、密钥配置
打开 API_test.py，将占位文字替换为自己阿里云百炼后台的API-Key：
API_KEY = "请在此处填写你自己的阿里云API-Key"

## 4、基础调用示例
```python
from llm_base_call import simple_chat
res = simple_chat("需要提问的内容")
print(res)