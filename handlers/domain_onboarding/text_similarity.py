"""可替换的本地文本向量化与余弦相似度实现。"""

from __future__ import annotations

import math
import re
from collections import Counter, OrderedDict
from threading import Lock
from typing import Any, Protocol

SparseVector = dict[str, float]


class TextVectorizer(Protocol):
    def vectorize(self, texts: list[str]) -> list[SparseVector]: ...


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbeddingProvider:
    """Adapter for OpenAI-compatible clients with an explicit embedding model."""

    def __init__(self, model: object, embedding_model: str) -> None:
        if not embedding_model.strip():
            raise ValueError("embedding_model must not be empty")
        self.model = model
        self.embedding_model = embedding_model.strip()

    def embed(self, texts: list[str]) -> list[list[float]]:
        method = getattr(self.model, "embed", None)
        if not callable(method):
            raise TypeError("configured model does not support embeddings")
        return method(texts, model=self.embedding_model)


class FastEmbedProvider:
    """Local ONNX embedding provider backed by the optional FastEmbed package."""

    def __init__(
        self,
        model_name: str,
        *,
        cache_dir: str | None = None,
        encoder: Any | None = None,
    ) -> None:
        if not model_name.strip():
            raise ValueError("model_name must not be empty")
        self.model_name = model_name.strip()
        if encoder is not None:
            self.encoder = encoder
            return
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "local embeddings require the optional dependency: "
                "pip install 'NoviceSynapse[embeddings]'"
            ) from exc
        kwargs = {"model_name": self.model_name}
        if cache_dir:
            kwargs["cache_dir"] = cache_dir
        self.encoder = TextEmbedding(**kwargs)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            vector.tolist() if hasattr(vector, "tolist") else list(vector)
            for vector in self.encoder.embed(texts)
        ]


class MultilingualEvidenceTextVectorizer:
    """Adds a small, deterministic terminology bridge before lexical fallback."""

    name = "multilingual_tfidf"
    _TERMS = {
        "检索": "retrieval",
        "增强": "augmented augmentation",
        "生成": "generation generate",
        "证据": "evidence",
        "事实": "factual factuality",
        "幻觉": "hallucination",
        "图神经网络": "graph neural network",
        "扩散模型": "diffusion model",
        "多模态": "multimodal",
        "智能体": "agent",
        "辩论": "debate",
        "方法": "method",
        "模型": "model",
        "评测": "evaluation benchmark",
        "忠实度": "faithfulness faithful",
        "相关性": "relevance relevant",
        "上下文": "context",
        "检索质量": "retrieval quality",
        "证据质量": "evidence quality",
        "向量数据库": "vector database",
        "向量检索": "vector retrieval dense retrieval",
        "混合检索": "hybrid retrieval",
        "重排序": "reranking reranker",
        "长文档": "long document",
        "架构": "architecture",
        "框架": "framework",
        "准确": "accuracy accurate",
        "可靠": "reliable reliability",
        "局限": "limitation",
        "基准": "benchmark",
        "训练": "training",
        "推理": "reasoning inference",
        "知识过时": "outdated knowledge knowledge update",
        "外部知识": "external knowledge non-parametric memory",
        "问答": "question answering qa",
        "模块化": "modular modularity",
        "组件": "component",
        "古文": "classical chinese",
        "分词": "word segmentation tokenization",
        "推荐": "recommendation",
        "噪声": "noise noisy",
        "领域适配": "domain adaptation domain-specific",
        "多义词": "polysemy ambiguous words",
    }

    def __init__(self, base: TextVectorizer | None = None) -> None:
        self.base = base or TfidfTextVectorizer()

    def vectorize(self, texts: list[str]) -> list[SparseVector]:
        return self.base.vectorize([self.expand(text) for text in texts])

    @classmethod
    def expand(cls, text: str) -> str:
        bridged = [english for chinese, english in cls._TERMS.items() if chinese in text]
        return " ".join([text, *bridged])

    @classmethod
    def has_bridge_terms(cls, text: str) -> bool:
        return any(term in text for term in cls._TERMS)


def tokenize(text: str) -> list[str]:
    normalized = text.lower()
    tokens = re.findall(r"[a-z0-9]+", normalized)
    for sequence in re.findall(r"[\u4e00-\u9fff]+", normalized):
        tokens.append(sequence)
        if len(sequence) > 1:
            tokens.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


class TfidfTextVectorizer:
    """无外部模型依赖的确定性 TF-IDF 向量器。"""

    name = "tfidf"

    def vectorize(self, texts: list[str]) -> list[SparseVector]:
        tokenized = [tokenize(text) for text in texts]
        document_count = len(tokenized)
        frequencies = Counter(token for tokens in tokenized for token in set(tokens))
        vectors: list[SparseVector] = []
        for tokens in tokenized:
            counts = Counter(tokens)
            vector = {
                token: (1.0 + math.log(count))
                * (math.log((1.0 + document_count) / (1.0 + frequencies[token])) + 1.0)
                for token, count in counts.items()
            }
            norm = math.sqrt(sum(value * value for value in vector.values()))
            vectors.append(
                {token: value / norm for token, value in vector.items()} if norm else {}
            )
        return vectors


class CachedEmbeddingTextVectorizer:
    """对任意 embedding provider 提供批处理和有界进程内缓存。"""

    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        batch_size: int = 32,
        cache_max_entries: int = 2048,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if cache_max_entries < 0:
            raise ValueError("cache_max_entries must not be negative")
        self.provider = provider
        self.batch_size = batch_size
        self.cache_max_entries = cache_max_entries
        self._cache: OrderedDict[str, SparseVector] = OrderedDict()
        self._lock = Lock()

    @property
    def name(self) -> str:
        model_name = getattr(self.provider, "embedding_model", None) or getattr(
            self.provider, "model_name", None
        )
        return f"embedding:{model_name}" if model_name else "embedding"

    def vectorize(self, texts: list[str]) -> list[SparseVector]:
        vectors: list[SparseVector | None] = [None] * len(texts)
        missing: dict[str, list[int]] = {}
        with self._lock:
            for index, text in enumerate(texts):
                cached = self._cache.get(text)
                if cached is None:
                    missing.setdefault(text, []).append(index)
                    continue
                self._cache.move_to_end(text)
                vectors[index] = dict(cached)

        unique_texts = list(missing)
        for offset in range(0, len(unique_texts), self.batch_size):
            batch = unique_texts[offset : offset + self.batch_size]
            dense_vectors = self.provider.embed(batch)
            if len(dense_vectors) != len(batch):
                raise ValueError("embedding provider returned an unexpected vector count")
            for text, dense in zip(batch, dense_vectors, strict=True):
                sparse = self._to_sparse(dense)
                for index in missing[text]:
                    vectors[index] = dict(sparse)
                self._store(text, sparse)

        if any(vector is None for vector in vectors):
            raise ValueError("embedding vectorization produced an incomplete result")
        return [vector for vector in vectors if vector is not None]

    @staticmethod
    def _to_sparse(vector: list[float]) -> SparseVector:
        if not vector:
            raise ValueError("embedding provider returned an empty vector")
        values = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("embedding provider returned a non-finite value")
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            raise ValueError("embedding provider returned a zero vector")
        return {
            str(index): value / norm
            for index, value in enumerate(values)
            if value != 0.0
        }

    def _store(self, text: str, vector: SparseVector) -> None:
        if self.cache_max_entries <= 0:
            return
        with self._lock:
            self._cache[text] = dict(vector)
            self._cache.move_to_end(text)
            while len(self._cache) > self.cache_max_entries:
                self._cache.popitem(last=False)


def cosine_similarity(left: SparseVector, right: SparseVector) -> float:
    if len(left) > len(right):
        left, right = right, left
    return max(0.0, min(1.0, sum(value * right.get(token, 0.0) for token, value in left.items())))
