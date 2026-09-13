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
def get_chat_model() -> ChatOpenAI:
    """创建并缓存连接 DeepSeek OpenAI 兼容接口的聊天客户端。

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

    # 客户端创建本身不会发送请求；temperature=0 用于提高制度问答稳定性。
    return ChatOpenAI(
        api_key=api_key,
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
        temperature=0,
    )
