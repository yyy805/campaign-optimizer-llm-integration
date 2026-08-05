"""Strict isolated Function Calling transport for the v12 Reviewer."""
from __future__ import annotations

import json, socket, time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .qwen_client import QwenClientError,QwenConfig,QwenErrorCode,QwenUsage,SyncTransport,TransportRequest,UrllibTransport,_header,_optional_string,_parse_usage

MAX_FUNCTION_RESPONSE_BYTES=512*1024
MAX_TOOL_ARGUMENT_BYTES=64*1024
ALLOWED_FINISH_REASONS=frozenset({"tool_calls","stop"})

@dataclass(frozen=True)
class ToolCallV12:
    name:str
    arguments:str

@dataclass(frozen=True)
class FunctionResponseV12:
    content:Any
    tool_calls:tuple[ToolCallV12,...]
    request_id:str|None
    model:str
    usage:QwenUsage
    latency_ms:float
    finish_reason:str

class QwenFunctionClientV12:
    def __init__(self,config:QwenConfig,*,transport:SyncTransport|None=None,clock=time.perf_counter):self.config=config;self._transport=transport or UrllibTransport();self._clock=clock
    def chat(self,messages:Sequence[Mapping[str,str]],*,parameters:Mapping[str,Any])->FunctionResponseV12:
        body=dict(parameters);body["model"]=self.config.model;body["messages"]=[dict(x) for x in messages]
        request=TransportRequest(self.config.endpoint,{"Authorization":f"Bearer {self.config.api_key}","Content-Type":"application/json"},json.dumps(body,ensure_ascii=False).encode(),self.config.timeout_seconds)
        started=self._clock()
        try:response=self._transport.send(request)
        except (TimeoutError,socket.timeout) as exc:raise QwenClientError(QwenErrorCode.TIMEOUT) from exc
        except OSError as exc:raise QwenClientError(QwenErrorCode.NETWORK) from exc
        latency=max(0.0,(self._clock()-started)*1000);request_id=_header(response.headers,"x-request-id") or _header(response.headers,"x-dashscope-request-id")
        if response.status_code in {401,403}:raise QwenClientError(QwenErrorCode.AUTH,status_code=response.status_code,request_id=request_id)
        if response.status_code==429:raise QwenClientError(QwenErrorCode.RATE_LIMIT,status_code=429,request_id=request_id)
        if not 200<=response.status_code<300:raise QwenClientError(QwenErrorCode.HTTP,status_code=response.status_code,request_id=request_id)
        if len(response.body)>MAX_FUNCTION_RESPONSE_BYTES:raise QwenClientError(QwenErrorCode.INVALID_RESPONSE,request_id=request_id)
        try:
            payload=json.loads(response.body.decode());choices=payload["choices"]
            if not isinstance(payload,dict) or not isinstance(choices,list) or len(choices)!=1:raise TypeError
            choice=choices[0];message=choice["message"];finish=choice["finish_reason"]
            if not isinstance(choice,dict) or not isinstance(message,dict) or finish not in ALLOWED_FINISH_REASONS:raise TypeError
            raw_calls=message.get("tool_calls")
            if raw_calls is None:raw_calls=[]
            if not isinstance(raw_calls,list):raise TypeError
        except (UnicodeDecodeError,json.JSONDecodeError,KeyError,IndexError,TypeError):raise QwenClientError(QwenErrorCode.INVALID_RESPONSE,request_id=request_id) from None
        calls=[]
        for raw in raw_calls:
            try:
                if not isinstance(raw,dict) or raw.get("type")!="function" or set(raw)<{"type","function"}:raise TypeError
                function=raw["function"]
                if not isinstance(function,dict) or set(function)<{"name","arguments"} or not isinstance(function["name"],str) or not isinstance(function["arguments"],str):raise TypeError
                if len(function["arguments"].encode())>MAX_TOOL_ARGUMENT_BYTES:raise TypeError
            except (TypeError,UnicodeEncodeError):raise QwenClientError(QwenErrorCode.INVALID_RESPONSE,request_id=request_id) from None
            calls.append(ToolCallV12(function["name"],function["arguments"]))
        return FunctionResponseV12(message.get("content"),tuple(calls),request_id or _optional_string(payload.get("id")),_optional_string(payload.get("model")) or self.config.model,_parse_usage(payload.get("usage"),request_id=request_id),latency,finish)
