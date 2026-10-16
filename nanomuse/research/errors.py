"""Safe user-facing diagnostics, without provider messages, paths or credentials."""

from __future__ import annotations

_MESSAGES = {
    "configuration": (
        "Configure a model in Settings, then retry live research. / "
        "请先在设置中配置模型，再重试实时研究。"
    ),
    "authentication": (
        "Model authentication failed. Check the connection in Settings. / "
        "模型认证失败，请在设置中检查连接。"
    ),
    "rate_limit": "Model rate limit reached. Wait before retrying. / 模型请求达到限额，请稍后重试。",
    "model_budget": (
        "Model call limit reached. Review the limit before retrying. / "
        "模型调用次数达到上限，请核对限额后重试。"
    ),
    "context_budget": (
        "The required context is too large. Reduce the project scope or review the sources. / "
        "必要上下文超出容量，请缩小项目范围或检查来源。"
    ),
    "evidence_validation": (
        "Evidence validation failed. Review the sources and unresolved requirements. / "
        "证据校验未通过，请检查来源和待核验条件。"
    ),
    "model_format": (
        "The model output was incomplete or invalid. Retry the run. / "
        "模型输出不完整或格式有误，请重试运行。"
    ),
}


def safe_error(exc: Exception) -> str:
    """Only fixed messages cross the persistence and UI boundary."""
    code = getattr(exc, "code", "")
    if code in _MESSAGES:
        return _MESSAGES[code]
    if code in {"unsafe_url", "invalid_source", "invalid_content", "size_limit"}:
        return (
            "The source was refused. Check its public URL and text content. / "
            "来源被拒绝，请检查公开地址和文本内容。"
        )
    if code in {"rate_limited", "network_error", "timeout"} or isinstance(exc, TimeoutError):
        return "Source access failed. Check connectivity and retry later. / 来源访问失败，请检查网络并稍后重试。"
    return (
        "Research could not continue. Check settings and sources, then retry. / "
        "研究无法继续，请检查设置和来源后重试。"
    )
