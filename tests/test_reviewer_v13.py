from __future__ import annotations
import json
import pytest
from campaign_optimizer.llm.agent_workflow_v12 import load_role_configuration
from campaign_optimizer.llm.qwen_client import QwenClientError,QwenErrorCode
from campaign_optimizer.llm.three_role_runner import RoleCallAudit,RoleCallFailure
from campaign_optimizer.llm.three_role_runner_v13 import RoleCallAdapterV13
from scripts.run_reviewer_capability_smoke_v13 import run_once,safe_result

class CapturingClient:
 def __init__(self,state):self.state=state
 def chat(self,messages,*,parameters=None):
  self.state["messages"]=messages;self.state["parameters"]=parameters
  raise QwenClientError(QwenErrorCode.HTTP,status_code=400,request_id="req-safe")

def test_reviewer_request_fixes_non_thinking_and_forced_tool_choice():
 c=load_role_configuration();state={};a=RoleCallAdapterV13(c,reviewer_client_factory=lambda model:CapturingClient(state));a.set_total_limit(1);a.begin_candidate(0)
 with pytest.raises(RoleCallFailure):a.call_json(role="reviewer",payload={"safe":"payload"})
 p=state["parameters"]
 assert p["enable_thinking"] is False and p["stream"] is False and p["parallel_tool_calls"] is False
 assert p["tool_choice"]=={"type":"function","function":{"name":c.tool_name}}
 assert len(p["tools"])==1 and [m["role"] for m in state["messages"]]==["system","user"]

class OneFailureAdapter:
 def __init__(self):self.calls=0
 def begin_candidate(self,n):pass
 def set_total_limit(self,n):assert n==1
 def call_json(self,*,role,payload):
  self.calls+=1
  raise RoleCallFailure(RoleCallAudit(1,"reviewer","qwen3.7-plus","PROVIDER_ERROR","HTTP","req-400",400),repairable=False)

def test_capability_smoke_calls_at_most_once_and_reports_safe_http_metadata():
 c=load_role_configuration();a=OneFailureAdapter();out=run_once(a,c);serialized=json.dumps(out)
 assert a.calls==1 and out["status"]=="FAIL" and out["status_code"]==400 and out["request_id"]=="req-400"
 assert out["failure_category"]=="protocol" and "body" not in serialized.lower() and "api_key" not in serialized.lower() and "workspace" not in serialized.lower()

@pytest.mark.parametrize("status,category",[(400,"protocol"),(401,"provider"),(403,"provider"),(429,"provider"),(500,"provider"),(503,"provider")])
def test_safe_http_status_classes(status,category):
 exc=RoleCallFailure(RoleCallAudit(1,"reviewer","model","PROVIDER_ERROR","HTTP","req",status),repairable=False)
 out=safe_result(exc)
 assert out["status_code"]==status and out["failure_category"]==category
 assert set(out)=={"status","failure_category","safe_code","status_code","request_id","latency_ms","total_tokens"}

def test_unknown_exception_has_no_raw_exception_text():
 out=safe_result(RuntimeError("SECRET_RAW_BODY"));serialized=json.dumps(out)
 assert out["failure_category"]=="internal" and "SECRET_RAW_BODY" not in serialized and "RuntimeError" not in serialized


@pytest.mark.parametrize(("status","category"),[(400,"protocol"),(404,"protocol"),(405,"protocol"),(415,"protocol"),(422,"protocol"),(401,"provider"),(403,"provider"),(429,"provider"),(500,"provider")])
def test_pilot_and_capability_share_http_classification(status,category):
 from scripts.run_reviewer_function_pilot_v13 import classify as pilot_classify
 exc=RoleCallFailure(RoleCallAudit(1,"reviewer","model","PROVIDER_ERROR","HTTP","req",status),repairable=False)
 capability=safe_result(exc)
 pilot_category,pilot_code,pilot_repairable=pilot_classify(exc)
 assert capability["failure_category"]==pilot_category==category
 assert capability["status_code"]==status and pilot_code=="HTTP" and pilot_repairable is False

@pytest.mark.parametrize("status",[400,401,403,429,500])
def test_pilot_http_metadata_never_exposes_raw_body(status):
 from scripts.run_reviewer_function_pilot_v13 import classify as pilot_classify
 exc=RoleCallFailure(RoleCallAudit(1,"reviewer","model","PROVIDER_ERROR","AUTH" if status in {401,403} else "RATE_LIMIT" if status==429 else "HTTP","req-safe",status),repairable=False)
 result=pilot_classify(exc);serialized=json.dumps(result)
 assert result[0]==("protocol" if status==400 else "provider")
 assert "SECRET_RAW_BODY" not in serialized and "workspace" not in serialized.lower() and "api_key" not in serialized.lower()
