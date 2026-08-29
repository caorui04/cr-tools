"""LLM 异常定义。"""


class LLMError(Exception):
    """LLM 调用错误。

    Attributes:
        message: 人类可读错误信息
        retryable: 是否可重试
    """

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
