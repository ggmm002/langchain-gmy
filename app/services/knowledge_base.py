from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.models import ExtractedEntities, RouteTarget, SubsystemResult


class KnowledgeBaseService:
    def __init__(self, data_dir: Path) -> None:
        self._documents = self._load_documents(data_dir / "knowledge_base.json")

    @staticmethod
    def _load_documents(path: Path) -> list[dict[str, Any]]:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            raise ValueError("knowledge_base.json 必须是数组。")

        return data

    def get_topics(self) -> list[str]:
        topics: set[str] = set()
        for document in self._documents:
            for tag in document.get("tags", []):
                if tag:
                    topics.add(str(tag))

        return sorted(topics)

    def search(
        self,
        query: str,
        entities: ExtractedEntities,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        query_terms = self._expand_terms(query, entities.knowledge_topics)
        scored_documents: list[tuple[float, dict[str, Any]]] = []

        for document in self._documents:
            title = str(document.get("title", ""))
            content = str(document.get("content", ""))
            tags = [str(tag) for tag in document.get("tags", [])]
            haystack = f"{title} {' '.join(tags)} {content}".lower()
            score = 0.0

            for term in query_terms:
                lowered_term = term.lower()
                if lowered_term in title.lower():
                    score += 0.36
                if lowered_term in " ".join(tags).lower():
                    score += 0.3
                if lowered_term in haystack:
                    score += min(0.22, haystack.count(lowered_term) * 0.08)

            if score <= 0:
                continue

            scored_documents.append((score, document))

        scored_documents.sort(key=lambda item: item[0], reverse=True)
        results: list[dict[str, Any]] = []

        for score, document in scored_documents[:top_k]:
            snippet = self._build_snippet(query_terms, str(document.get("content", "")))
            results.append(
                {
                    "id": document.get("id"),
                    "title": document.get("title"),
                    "score": round(min(score, 0.99), 3),
                    "tags": document.get("tags", []),
                    "snippet": snippet,
                }
            )

        return results

    def run(self, query: str, entities: ExtractedEntities) -> SubsystemResult:
        matches = self.search(query, entities)

        if not matches:
            return SubsystemResult(
                target=RouteTarget.KNOWLEDGE_RAG,
                status="no_match",
                title="未命中企业知识库文档",
                summary="当前没有找到直接匹配的知识库内容，建议补充业务主题、文档名称或流程节点。",
                data={"matches": []},
                suggestions=[
                    "补充更具体的文档关键词，例如 SOP 名称、制度标题、表单名称",
                    "如果你其实想看数据趋势，请改问供应量分析",
                    "如果你想直接执行流程，请明确需要触发的工作流",
                ],
            )

        top_titles = "、".join(match["title"] for match in matches[:2])
        topics = entities.knowledge_topics or matches[0].get("tags", [])
        summary = (
            f"已命中 {len(matches)} 篇企业文档，优先推荐查看 {top_titles}。"
            f"本次识别主题为：{'、'.join(topics[:3]) if topics else '通用知识检索'}。"
        )

        return SubsystemResult(
            target=RouteTarget.KNOWLEDGE_RAG,
            status="success",
            title="企业私域知识库检索结果",
            summary=summary,
            data={
                "query": query,
                "matched_topics": topics,
                "matches": matches,
            },
            suggestions=[
                "如需进一步总结，可在下一轮追加“请整理成操作步骤”",
                "如需关联供应量数据，请补充时间范围和供应商",
            ],
        )

    @staticmethod
    def _expand_terms(query: str, extra_terms: list[str]) -> list[str]:
        tokens = re.findall(r"[A-Za-z0-9_-]+|[\u4e00-\u9fff]{2,}", query)
        terms = {token.strip() for token in tokens if token.strip()}
        terms.update(term.strip() for term in extra_terms if term.strip())
        return sorted(terms, key=len, reverse=True)

    @staticmethod
    def _build_snippet(terms: list[str], content: str) -> str:
        if not content:
            return ""

        best_term = next((term for term in terms if term and term in content), None)
        if not best_term:
            return content[:120]

        index = content.find(best_term)
        start = max(0, index - 28)
        end = min(len(content), index + max(80, len(best_term) + 40))
        snippet = content[start:end]
        if start > 0:
            snippet = f"...{snippet}"
        if end < len(content):
            snippet = f"{snippet}..."
        return snippet
