"""可替换的本地文本向量化与余弦相似度实现。"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Protocol

SparseVector = dict[str, float]


class TextVectorizer(Protocol):
    def vectorize(self, texts: list[str]) -> list[SparseVector]: ...


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


def cosine_similarity(left: SparseVector, right: SparseVector) -> float:
    if len(left) > len(right):
        left, right = right, left
    return max(0.0, min(1.0, sum(value * right.get(token, 0.0) for token, value in left.items())))
