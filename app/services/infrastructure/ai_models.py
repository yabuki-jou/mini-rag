"""延迟创建并缓存本地 Embedding 模型和 DeepSeek 聊天客户端。"""

import logging
from functools import lru_cache

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.core.errors import AppError


logger = logging.getLogger(__name__)


@lru_cache
def get_embeddings() -> HuggingFaceEmbeddings:
    """加载并缓存生成归一化向量的本地 BGE 模型。

    Returns:
        配置为输出归一化向量的 LangChain Embedding 对象。

    Raises:
        AppError: 本地模型目录不存在。
    """
    # 配置层已经把相对目录转换为不依赖启动位置的绝对路径。
    embedding_path = settings.embedding_path

    # 在加载大模型文件前给出明确错误，避免底层库产生难懂的堆栈。
    if not embedding_path.exists():
        logger.error("embedding_model_not_found path=%s", embedding_path)
        raise AppError(
            status_code=503,
            code="EMBEDDING_MODEL_NOT_FOUND",
            message="Embedding 模型目录不存在。",
        )

    # 模型只加载一次，并统一输出适用于 COSINE 的归一化向量。
    return HuggingFaceEmbeddings(
        model_name=str(embedding_path),
        model_kwargs={"device": settings.embedding_device},
        encode_kwargs={"normalize_embeddings": True},
    )


@lru_cache
def get_chat_model(*, max_retries: int | None = None) -> ChatOpenAI:
    """创建并缓存连接 DeepSeek OpenAI 兼容接口的聊天客户端。

    Args:
        max_retries: 可选的客户端隐藏重试次数；为 ``None`` 时保留客户端默认行为。

    Returns:
        使用固定模型、接口地址和零温度配置的聊天客户端。

    Raises:
        AppError: DeepSeek API 密钥未配置或内容为空。
    """
    # 在创建客户端前检查配置，避免把缺少密钥的错误推迟到首次问答。
    if settings.deepseek_api_key is None:
        raise AppError(
            status_code=503,
            code="DEEPSEEK_NOT_CONFIGURED",
            message="DeepSeek API 密钥未配置。",
        )

    # 从 SecretStr 中提取真实值后还需排除纯空白密钥。
    api_key = settings.deepseek_api_key.get_secret_value().strip()
    if not api_key:
        raise AppError(
            status_code=503,
            code="DEEPSEEK_NOT_CONFIGURED",
            message="DeepSeek API 密钥未配置。",
        )

    # 无参入口不显式覆盖客户端既有重试默认；档案助手可按需关闭隐藏重试。
    client_options: dict[str, object] = {
        "api_key": api_key,
        "model": settings.deepseek_model,
        "base_url": settings.deepseek_base_url,
        "temperature": 0,
        "timeout": settings.deepseek_request_timeout_seconds,
    }
    if max_retries is not None:
        client_options["max_retries"] = max_retries

    # 客户端创建本身不会发送请求；temperature=0 用于提高问答稳定性。
    return ChatOpenAI(
        **client_options,
    )
