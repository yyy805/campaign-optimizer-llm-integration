from __future__ import annotations
import json,pytest
from campaign_optimizer.llm.qwen_client import QwenClientError,QwenConfig,QwenErrorCode,TransportResponse
from campaign_optimizer.llm.qwen_function_client_v12 import MAX_FUNCTION_RESPONSE_BYTES,QwenFunctionClientV12
class Transport:
 def __init__(self,payload=None,body=None):self.payload=payload;self.body=body;self.requests=[]
 def send(self,request):self.requests.append(request);body=self.body if self.body is not None else json.dumps(self.payload).encode();return TransportResponse(200,{"x-request-id":"req"},body)
def client(payload=None,body=None):return QwenFunctionClientV12(QwenConfig("key","workspace"),transport=Transport(payload,body),clock=lambda:1.0)
def valid(content=None,calls=None,finish="tool_calls"):
 if calls is None:calls=[{"type":"function","function":{"name":"submit_reviewer_decision_v1","arguments":"{}"}}]
 return {"choices":[{"finish_reason":finish,"message":{"content":content,"tool_calls":calls}}]}
def test_valid_null_and_empty_content_shapes():
 assert client(valid(None)).chat([{"role":"system","content":"x"}],parameters={"stream":False}).content is None
 assert client(valid("")).chat([{"role":"system","content":"x"}],parameters={"stream":False}).content==""
@pytest.mark.parametrize("payload",[
 {},{"choices":[]},{"choices":[{"finish_reason":"tool_calls","message":{}},{"finish_reason":"tool_calls","message":{}}]},
 valid(calls="bad"),valid(calls=[{"type":"not_function","function":{"name":"x","arguments":"{}"}}]),
 valid(calls=[{"type":"function","function":{"name":"x"}}]),valid(finish="length")])
def test_malformed_provider_shapes_are_protocol_errors(payload):
 with pytest.raises(QwenClientError) as e:client(payload).chat([{"role":"system","content":"x"}],parameters={"stream":False})
 assert e.value.code is QwenErrorCode.INVALID_RESPONSE
@pytest.mark.parametrize("message",[{}, {"content":None}, {"content":None,"tool_calls":None}])
def test_missing_or_null_tool_calls_normalize_to_zero_model_calls(message):
 payload={"choices":[{"finish_reason":"stop","message":message}]};response=client(payload).chat([{"role":"system","content":"x"}],parameters={"stream":False});assert response.tool_calls==()
def test_http_response_byte_cap_is_protocol_error():
 with pytest.raises(QwenClientError) as e:client(body=b"x"*(MAX_FUNCTION_RESPONSE_BYTES+1)).chat([{"role":"system","content":"x"}],parameters={"stream":False})
 assert e.value.code is QwenErrorCode.INVALID_RESPONSE
