"""Safe, value-free Reviewer semantic binding diagnostics for v13."""
from __future__ import annotations
from enum import Enum
from typing import Any,Mapping
from .agent_workflow_v5 import MAX_REVISION_ROUNDS,ReviewerPacket,WorkflowAction
from .reviewer_diagnostic_v10 import validate_reviewer_schema

class ReviewerBindingCode(str,Enum):
 CANDIDATE_ID_MISMATCH="candidate_id_mismatch"
 PACKET_DIGEST_MISMATCH="packet_digest_mismatch"
 EVIDENCE_SOURCE_OUTSIDE_ALLOWLIST="evidence_source_outside_allowlist"
 REVISION_SOURCE_OUTSIDE_ALLOWLIST="revision_source_outside_allowlist"
 REVISION_TARGET_INVALID="revision_target_invalid"
 REVISION_ACTION_SEMANTICS="revision_action_semantics"

class ReviewerBindingFailure(ValueError):
 def __init__(self,code:ReviewerBindingCode):self.code=code;super().__init__("reviewer binding rejected")

def validate_reviewer_binding_v13(value:Mapping[str,Any],*,packet:ReviewerPacket)->None:
 validate_reviewer_schema(value)
 if value["candidate_id"]!=packet.candidate_id:raise ReviewerBindingFailure(ReviewerBindingCode.CANDIDATE_ID_MISMATCH)
 if value["packet_digest"]!=packet.packet_digest:raise ReviewerBindingFailure(ReviewerBindingCode.PACKET_DIGEST_MISMATCH)
 if not set(value["evidence_source_ids"]).issubset(packet.allowed_source_ids):raise ReviewerBindingFailure(ReviewerBindingCode.EVIDENCE_SOURCE_OUTSIDE_ALLOWLIST)
 claim_ids={claim["claim_id"] for claim in packet.candidate_output["claims"]}
 for action in value["revision_actions"]:
  if action["source_id"] not in packet.allowed_source_ids:raise ReviewerBindingFailure(ReviewerBindingCode.REVISION_SOURCE_OUTSIDE_ALLOWLIST)
  if action["operation"]=="ADD_REQUIRED_LIMITATION":
   if action["target_claim_id"] is not None:raise ReviewerBindingFailure(ReviewerBindingCode.REVISION_ACTION_SEMANTICS)
  elif action["target_claim_id"] not in claim_ids:raise ReviewerBindingFailure(ReviewerBindingCode.REVISION_TARGET_INVALID)

def next_action_v13(value:Mapping[str,Any],*,packet:ReviewerPacket,revision_rounds:int,max_revision_rounds:int)->WorkflowAction:
 if not 0<=max_revision_rounds<=MAX_REVISION_ROUNDS or not 0<=revision_rounds<=max_revision_rounds:raise ValueError("revision rounds invalid")
 validate_reviewer_binding_v13(value,packet=packet)
 if value["decision"]=="PASS":return WorkflowAction.FINAL
 return WorkflowAction.REVISE if value["decision"]=="REVISE" and revision_rounds<max_revision_rounds else WorkflowAction.FALLBACK

def constrain_tool_schema_v13(schema:Mapping[str,Any],payload:Mapping[str,Any])->dict[str,Any]:
 """Narrow provider choices using only server-issued packet fields; local gate remains authoritative."""
 import copy
 result=copy.deepcopy(dict(schema));props=result["properties"]
 props["candidate_id"]={"type":"string","const":payload["candidate_id"]}
 props["packet_digest"]={"type":"string","const":payload["packet_digest"]}
 trusted=payload["trusted_context_snapshot"]
 allowed=set(trusted.get("allowed_plan_item_ids",[]))|set(trusted.get("allowed_fact_ids",[]))|set(trusted.get("allowed_rule_ids",[]))
 allowed|={x["review_item_id"] for x in trusted.get("review",{}).get("items",[]) if isinstance(x,dict) and "review_item_id" in x}
 source_enum=sorted(allowed)
 props["evidence_source_ids"]["items"]={"type":"string","enum":source_enum}
 action_props=props["revision_actions"]["items"]["properties"]
 action_props["source_id"]={"type":"string","enum":source_enum}
 claim_ids=sorted(x["claim_id"] for x in payload["candidate_output"].get("claims",[]) if isinstance(x,dict) and "claim_id" in x)
 action_props["target_claim_id"]={"type":["string","null"],"enum":[None,*claim_ids]}
 return result
