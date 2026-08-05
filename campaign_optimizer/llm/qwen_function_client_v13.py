"""v13 Function Calling transport; parser/gates are inherited unchanged."""
from .qwen_function_client_v12 import (
    ALLOWED_FINISH_REASONS,
    MAX_FUNCTION_RESPONSE_BYTES,
    MAX_TOOL_ARGUMENT_BYTES,
    FunctionResponseV12,
    QwenFunctionClientV12,
    ToolCallV12,
)

ToolCallV13 = ToolCallV12
FunctionResponseV13 = FunctionResponseV12

class QwenFunctionClientV13(QwenFunctionClientV12):
    """Version marker for the non-thinking Reviewer protocol."""
