from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import unicodedata
import uuid
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.config import Settings
from app.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    ExtractedEntities,
    HealthResponse,
    IntentDecision,
    IntentType,
    RouteTarget,
    SubsystemResult,
    TraceEvent,
)
from app.services.knowledge_base import KnowledgeBaseService
from app.services.supply_analytics import SupplyAnalyticsService
from app.services.workflow_automation import WorkflowAutomationService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Workflow state
# ---------------------------------------------------------------------------

class RoutingWorkflowState(TypedDict, total=False):
    request_id: str
    request: AnalyzeRequest
    normalized_query: str
    injection_flags: list[str]
    trace: list[TraceEvent]
    rule_decision: IntentDecision
    llm_decision: IntentDecision | None
    final_decision: IntentDecision
    subsystem_result: SubsystemResult


# ---------------------------------------------------------------------------
# LLM structured output schema
# ---------------------------------------------------------------------------

class LLMIntentPayload(BaseModel):
    primary_intent: IntentType
    secondary_intents: list[IntentType] = Field(default_factory=list)
    route_target: RouteTarget
    confidence: float = Field(ge=0.0, le=1.0)
    requires_confirmation: bool = False
    missing_slots: list[str] = Field(default_factory=list)
    normalized_query: str
    rationale: str
    extracted_entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    risk_flags: list[str] = Field(default_factory=list)
    candidate_scores: dict[str, float] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# P0: Prompt injection detection
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    r"(?i)ignore\s+(?:all\s+)?previous\s+instructions",
    r"(?i)you\s+are\s+now\s+a",
    r"(?i)forget\s+(?:all\s+)?previous",
    r"(?:忽略|无视).{0,4}(?:之前|上面|以上).{0,4}(?:指令|要求|规则)",
    r"(?:你现在是|你的新角色|从现在开始你是)",
    r"(?:不要遵守|不要遵循).{0,4}(?:规则|指令)",
]


def detect_injection(query: str) -> list[str]:
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, query):
            return ["prompt_injection_detected"]
    return []


# ---------------------------------------------------------------------------
# P0: Circuit breaker for LLM calls
# ---------------------------------------------------------------------------

class CircuitBreaker:
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 60) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._state = self.CLOSED
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == self.OPEN and self._last_failure_time is not None:
                if (time.monotonic() - self._last_failure_time) >= self._recovery_timeout:
                    self._state = self.HALF_OPEN
            return self._state

    def allow_request(self) -> bool:
        return self.state in (self.CLOSED, self.HALF_OPEN)

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = self.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self._failure_threshold:
                self._state = self.OPEN


# ---------------------------------------------------------------------------
# P2: Query result cache
# ---------------------------------------------------------------------------

class QueryCache:
    def __init__(self, max_size: int = 256, ttl_seconds: int = 300) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    @staticmethod
    def build_key(query: str, allow_action: bool, dry_run: bool) -> str:
        raw = f"{query}|{allow_action}|{dry_run}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.monotonic() - ts >= self._ttl:
                del self._cache[key]
                return None
            return value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._cache) >= self._max_size:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest]
            self._cache[key] = (time.monotonic(), value)


# ---------------------------------------------------------------------------
# P0: Rule-based classifier (compound keywords + negation)
# ---------------------------------------------------------------------------

class RuleBasedIntentClassifier:
    def __init__(
        self,
        suppliers: list[str],
        materials: list[str],
        workflows: list[str],
        knowledge_topics: list[str],
    ) -> None:
        self._suppliers = suppliers
        self._materials = materials
        self._workflows = workflows
        self._knowledge_topics = knowledge_topics
        self._intent_keywords: dict[IntentType, dict[str, float]] = {
            IntentType.KNOWLEDGE_RAG: {
                "知识库": 0.34,
                "文档": 0.22,
                "制度": 0.2,
                "规范": 0.18,
                "sop": 0.32,
                "faq": 0.28,
                "手册": 0.2,
                "指引": 0.18,
                "说明": 0.14,
            },
            IntentType.SUPPLY_ANALYTICS: {
                "供应量": 0.36,
                "产量": 0.22,
                "交付量": 0.2,
                "达成率": 0.22,
                "趋势": 0.18,
                "波动": 0.18,
                "同比": 0.18,
                "环比": 0.18,
                "分析": 0.16,
                "报告": 0.16,
                "统计": 0.16,
            },
            IntentType.WORKFLOW_AUTOMATION: {
                "工作流": 0.34,
                "流程": 0.18,
                "自动化": 0.26,
                "触发": 0.26,
                "执行": 0.24,
                "启动": 0.24,
                "同步": 0.2,
                "审批": 0.18,
                "通知": 0.16,
                "回写": 0.16,
            },
        }
        self._metric_keywords = [
            "供应量", "计划量", "实际量", "交付量", "达成率",
            "同比", "环比", "趋势", "波动",
        ]
        self._action_verbs = [
            "执行", "触发", "启动", "运行", "发起", "创建", "同步", "通知", "回写",
        ]
        self._dangerous_verbs = ["删除", "停用", "清空", "覆盖", "回滚"]

        self._compound_keywords: dict[str, tuple[IntentType, float]] = {
            "审批流程说明": (IntentType.KNOWLEDGE_RAG, 0.30),
            "工作流说明": (IntentType.KNOWLEDGE_RAG, 0.28),
            "流程说明": (IntentType.KNOWLEDGE_RAG, 0.28),
            "制度说明": (IntentType.KNOWLEDGE_RAG, 0.26),
            "供应量分析": (IntentType.SUPPLY_ANALYTICS, 0.34),
            "供应量报告": (IntentType.SUPPLY_ANALYTICS, 0.30),
            "交付分析": (IntentType.SUPPLY_ANALYTICS, 0.28),
        }

        self._negation_patterns: list[tuple[str, IntentType, float]] = [
            (r"不要.{0,4}(?:执行|触发|启动|运行)", IntentType.WORKFLOW_AUTOMATION, -0.35),
            (r"不需要.{0,4}(?:执行|触发|启动)", IntentType.WORKFLOW_AUTOMATION, -0.30),
            (r"别.{0,2}(?:执行|触发|启动|运行)", IntentType.WORKFLOW_AUTOMATION, -0.35),
            (r"不要.{0,4}(?:分析|统计|生成报告)", IntentType.SUPPLY_ANALYTICS, -0.30),
            (r"只.{0,6}(?:查|检索|搜索|看).{0,8}(?:知识|文档|制度|sop|手册)", IntentType.KNOWLEDGE_RAG, 0.20),
        ]

    def classify(self, query: str) -> IntentDecision:
        normalized_query = normalize_query(query)
        entities = self._extract_entities(normalized_query)
        scores = {intent: 0.08 for intent in IntentType}
        scores[IntentType.CLARIFICATION] = 0.05
        evidence: dict[IntentType, list[str]] = {intent: [] for intent in IntentType}
        risk_flags: list[str] = []

        # --- compound keywords first (longest match wins) ---
        consumed_keywords: set[str] = set()
        for compound, (intent, weight) in sorted(
            self._compound_keywords.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if compound in normalized_query:
                scores[intent] += weight
                evidence[intent].append(f"命中复合关键词 {compound}")
                for any_intent, kw_weights in self._intent_keywords.items():
                    for kw in kw_weights:
                        if kw in compound:
                            consumed_keywords.add(kw)

        # --- single keywords (skip consumed) ---
        for intent, keyword_weights in self._intent_keywords.items():
            for keyword, weight in keyword_weights.items():
                if keyword in consumed_keywords:
                    continue
                if keyword in normalized_query:
                    scores[intent] += weight
                    evidence[intent].append(f"命中关键词 {keyword}")

        # --- negation / reinforcement signals ---
        for pattern, target_intent, adjustment in self._negation_patterns:
            if re.search(pattern, normalized_query):
                label = "否定抑制" if adjustment < 0 else "强化增益"
                scores[target_intent] += adjustment
                evidence[target_intent].append(f"{label}信号命中")

        # --- entity boosts ---
        if entities.suppliers or entities.materials:
            scores[IntentType.SUPPLY_ANALYTICS] += 0.18
            evidence[IntentType.SUPPLY_ANALYTICS].append("识别到供应商或物料实体")

        if entities.time_range:
            scores[IntentType.SUPPLY_ANALYTICS] += 0.12
            evidence[IntentType.SUPPLY_ANALYTICS].append(f"识别到时间范围 {entities.time_range}")

        if entities.workflow_name:
            scores[IntentType.WORKFLOW_AUTOMATION] += 0.28
            evidence[IntentType.WORKFLOW_AUTOMATION].append(
                f"识别到工作流名称 {entities.workflow_name}"
            )

        if entities.knowledge_topics:
            scores[IntentType.KNOWLEDGE_RAG] += 0.12
            evidence[IntentType.KNOWLEDGE_RAG].append("识别到知识主题标签")

        if entities.action_verb:
            scores[IntentType.WORKFLOW_AUTOMATION] += 0.16
            evidence[IntentType.WORKFLOW_AUTOMATION].append(
                f"识别到执行动词 {entities.action_verb}"
            )

        if len(normalized_query) <= 6:
            scores[IntentType.CLARIFICATION] += 0.14
            risk_flags.append("query_too_short")

        if "同时" in normalized_query or "并且" in normalized_query or "然后" in normalized_query:
            risk_flags.append("multi_stage_request")

        if any(verb in normalized_query for verb in self._dangerous_verbs):
            risk_flags.append("high_risk_action")

        primary_intent = max(scores, key=scores.get)
        primary_score = min(scores[primary_intent], 0.99)

        if primary_score < 0.42:
            primary_intent = IntentType.CLARIFICATION
            primary_score = 0.36
            evidence[IntentType.CLARIFICATION].append("缺少足够清晰的业务意图信号")

        secondary_intents = [
            intent
            for intent, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
            if intent != primary_intent and score >= max(primary_score - 0.18, 0.45)
        ][:2]

        if len(secondary_intents) >= 1:
            risk_flags.append("cross_domain_request")

        missing_slots = self._build_missing_slots(primary_intent, normalized_query, entities)
        requires_confirmation = (
            primary_intent == IntentType.WORKFLOW_AUTOMATION and entities.action_verb is not None
        )

        if missing_slots:
            risk_flags.append("missing_critical_slots")

        if primary_score < 0.58:
            risk_flags.append("rule_low_confidence")

        candidate_scores = {
            intent.value: round(min(score, 0.99), 3) for intent, score in scores.items()
        }

        route_target = RouteTarget(primary_intent.value)
        evidence_list = evidence.get(primary_intent, [])
        rationale = "；".join(evidence_list[:3]) if evidence_list else "规则匹配信号较弱，建议补充信息。"

        return IntentDecision(
            source="rules",
            primary_intent=primary_intent,
            secondary_intents=secondary_intents,
            route_target=route_target,
            confidence=round(primary_score, 3),
            requires_confirmation=requires_confirmation,
            missing_slots=missing_slots,
            normalized_query=normalized_query,
            rationale=rationale,
            extracted_entities=entities,
            risk_flags=dedupe(risk_flags),
            candidate_scores=candidate_scores,
            evidence=evidence_list,
        )

    def _extract_entities(self, normalized_query: str) -> ExtractedEntities:
        suppliers = [
            supplier for supplier in self._suppliers if supplier.lower() in normalized_query
        ]
        materials = [
            material for material in self._materials if material.lower() in normalized_query
        ]
        workflows = [
            workflow for workflow in self._workflows if workflow.lower() in normalized_query
        ]
        knowledge_topics = [
            topic for topic in self._knowledge_topics if topic.lower() in normalized_query
        ]
        metrics = [metric for metric in self._metric_keywords if metric in normalized_query]
        action_verb = next(
            (verb for verb in self._action_verbs if verb in normalized_query),
            None,
        )

        time_range = extract_time_range(normalized_query)

        return ExtractedEntities(
            suppliers=suppliers[:3],
            materials=materials[:3],
            metrics=metrics[:5],
            time_range=time_range,
            workflow_name=workflows[0] if workflows else None,
            knowledge_topics=knowledge_topics[:4],
            action_verb=action_verb,
        )

    @staticmethod
    def _build_missing_slots(
        primary_intent: IntentType,
        normalized_query: str,
        entities: ExtractedEntities,
    ) -> list[str]:
        missing_slots: list[str] = []

        if primary_intent == IntentType.WORKFLOW_AUTOMATION and not entities.workflow_name:
            missing_slots.append("workflow_name")

        if primary_intent == IntentType.SUPPLY_ANALYTICS and (
            ("分析" in normalized_query or "报告" in normalized_query) and not entities.time_range
        ):
            missing_slots.append("time_range")

        if primary_intent == IntentType.KNOWLEDGE_RAG and (
            "知识库" in normalized_query and not entities.knowledge_topics
        ):
            missing_slots.append("knowledge_topic")

        return missing_slots


# ---------------------------------------------------------------------------
# LLM classifier (with circuit breaker + thread safety + few-shot)
# ---------------------------------------------------------------------------

class LLMIntentClassifier:
    def __init__(
        self,
        settings: Settings,
        suppliers: list[str],
        materials: list[str],
        workflows: list[str],
        knowledge_topics: list[str],
    ) -> None:
        self._settings = settings
        self._suppliers = suppliers
        self._materials = materials
        self._workflows = workflows
        self._knowledge_topics = knowledge_topics
        self._last_error: str | None = None
        self._lock = threading.Lock()
        self._model: ChatOpenAI | None = None
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=settings.circuit_breaker_failure_threshold,
            recovery_timeout=settings.circuit_breaker_recovery_timeout,
        )

        if settings.enable_llm_classifier and settings.llm_configured:
            self._model = ChatOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                model=settings.llm_model_id,
                temperature=0,
                timeout=settings.request_timeout_seconds,
                max_retries=1,
            )

    @property
    def status(self) -> str:
        if not self._settings.enable_llm_classifier:
            return "disabled"
        if not self._settings.llm_configured:
            return "not_configured"
        if self._circuit_breaker.state == CircuitBreaker.OPEN:
            return "circuit_open"
        if self._last_error:
            return "degraded"
        return "ready"

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def classify(self, query: str, normalized_query: str) -> IntentDecision:
        if self._model is None:
            raise RuntimeError("LLM 分类器未启用。")

        if not self._circuit_breaker.allow_request():
            raise RuntimeError("LLM 熔断器已打开，暂时跳过 LLM 调用。")

        structured_model = self._model.with_structured_output(LLMIntentPayload)
        prompt = self._build_prompt(query=query, normalized_query=normalized_query)
        payload = structured_model.invoke(prompt)

        if not isinstance(payload, LLMIntentPayload):
            self._circuit_breaker.record_failure()
            raise RuntimeError("LLM 未返回合法的结构化分类结果。")

        self._circuit_breaker.record_success()
        with self._lock:
            self._last_error = None
        return IntentDecision(
            source="llm",
            primary_intent=payload.primary_intent,
            secondary_intents=payload.secondary_intents,
            route_target=payload.route_target,
            confidence=payload.confidence,
            requires_confirmation=payload.requires_confirmation,
            missing_slots=payload.missing_slots,
            normalized_query=payload.normalized_query or normalized_query,
            rationale=payload.rationale,
            extracted_entities=payload.extracted_entities,
            risk_flags=payload.risk_flags,
            candidate_scores=payload.candidate_scores,
            evidence=payload.evidence,
        )

    def record_error(self, error_message: str) -> None:
        self._circuit_breaker.record_failure()
        with self._lock:
            self._last_error = error_message

    def _build_prompt(self, query: str, normalized_query: str) -> list[Any]:
        system_prompt = (
            "你是企业 Agent 系统的意图识别中枢，需要把用户请求路由到最合适的子系统。"
            "只允许返回结构化结果，不要输出多余文本。\n"
            "意图定义如下：\n"
            "1. knowledge_rag: 需要检索企业私域知识库、制度、SOP、FAQ、合同、流程说明。\n"
            "2. supply_analytics: 需要查询供应量、产量、交付量、达成率、趋势、异常分析或生成分析报告。\n"
            "3. workflow_automation: 需要触发、执行、发起、同步、通知或自动化运行企业工作流。\n"
            "4. clarification: 请求含糊、跨域冲突严重、或缺少关键槽位导致无法稳定路由。\n"
            "决策要求：\n"
            "- 尽量输出 primary_intent，同时可给出 secondary_intents。\n"
            "- 如果请求会产生动作执行风险，需要 requires_confirmation=true。\n"
            "- 对于工作流请求，若看不出具体流程名称，应补充 missing_slots=['workflow_name']。\n"
            "- 对于分析报告类请求，若缺少时间范围，可补充 missing_slots=['time_range']。\n"
            "- 如果请求更像查询文档而不是数据分析，不要误判为 workflow_automation。\n"
            "- 风险标记只保留短标签，例如 high_risk_action、cross_domain_request、missing_critical_slots。\n"
        )

        catalog_hint = (
            f"已知供应商: {', '.join(self._suppliers[:8])}\n"
            f"已知物料: {', '.join(self._materials[:8])}\n"
            f"已知工作流: {', '.join(self._workflows[:8])}\n"
            f"已知知识主题: {', '.join(self._knowledge_topics[:12])}\n"
        )

        few_shot_hint = (
            "参考样例（仅供对齐输出格式）：\n"
            "- '检索供应异常升级SOP' → knowledge_rag, confidence≈0.88\n"
            "- '分析华东智造近三个月动力电池模组供应量波动' → supply_analytics, confidence≈0.90\n"
            "- '触发供应异常预警流程并通知采购负责人' → workflow_automation, confidence≈0.85, requires_confirmation=true\n"
            "- '帮我处理一下' → clarification, confidence≈0.40\n"
            "- '从知识库查补货审批流程说明' → knowledge_rag（查文档说明而非执行流程）\n"
        )

        user_prompt = (
            f"原始查询: {query}\n"
            f"规范化查询: {normalized_query}\n"
            f"{catalog_hint}"
            f"{few_shot_hint}"
            "请完成生产环境可用的意图识别与路由判断。"
        )

        return [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]


# ---------------------------------------------------------------------------
# Fusion (with LLM-unavailable safety compensation)
# ---------------------------------------------------------------------------

class DecisionFusionStrategy:
    def fuse(
        self,
        rule_decision: IntentDecision,
        llm_decision: IntentDecision | None,
    ) -> IntentDecision:
        if llm_decision is None:
            degraded_confidence = round(min(rule_decision.confidence * 0.85, 0.92), 3)
            degraded_flags = dedupe([*rule_decision.risk_flags, "llm_unavailable"])
            return rule_decision.model_copy(
                update={
                    "source": "fusion",
                    "confidence": degraded_confidence,
                    "risk_flags": degraded_flags,
                    "rationale": f"{rule_decision.rationale}；LLM 不可用，置信度已降级，已切换到规则兜底。",
                }
            )

        if rule_decision.primary_intent == llm_decision.primary_intent:
            chosen = llm_decision
            confidence = min(
                0.99,
                rule_decision.confidence * 0.42 + llm_decision.confidence * 0.58 + 0.06,
            )
            rationale = (
                f"规则与 LLM 均判定为 {llm_decision.primary_intent.value}，"
                f"融合后优先沿用 LLM 判定。"
            )
        else:
            score_gap = llm_decision.confidence - rule_decision.confidence
            if score_gap >= 0.18:
                chosen = llm_decision
                confidence = llm_decision.confidence * 0.92
                rationale = (
                    f"规则与 LLM 存在分歧，但 LLM 置信度更高，优先采用 {llm_decision.primary_intent.value}。"
                )
            elif score_gap <= -0.18:
                chosen = rule_decision
                confidence = rule_decision.confidence * 0.92
                rationale = (
                    f"规则与 LLM 存在分歧，但规则判定更稳定，优先采用 {rule_decision.primary_intent.value}。"
                )
            else:
                return IntentDecision(
                    source="fusion",
                    primary_intent=IntentType.CLARIFICATION,
                    secondary_intents=dedupe_intents(
                        [rule_decision.primary_intent, llm_decision.primary_intent]
                    ),
                    route_target=RouteTarget.CLARIFICATION,
                    confidence=0.46,
                    requires_confirmation=True,
                    missing_slots=dedupe(
                        [*rule_decision.missing_slots, *llm_decision.missing_slots]
                    ),
                    normalized_query=rule_decision.normalized_query,
                    rationale="规则与 LLM 对主意图分歧较大，为避免误路由，转入澄清分支。",
                    extracted_entities=merge_entities(
                        rule_decision.extracted_entities, llm_decision.extracted_entities
                    ),
                    risk_flags=dedupe(
                        [*rule_decision.risk_flags, *llm_decision.risk_flags, "classifier_disagreement"]
                    ),
                    candidate_scores=merge_candidate_scores(
                        rule_decision.candidate_scores, llm_decision.candidate_scores
                    ),
                    evidence=dedupe([*rule_decision.evidence, *llm_decision.evidence]),
                )

        merged_entities = merge_entities(
            rule_decision.extracted_entities, llm_decision.extracted_entities
        )
        merged_secondary = dedupe_intents(
            [*rule_decision.secondary_intents, *llm_decision.secondary_intents]
        )
        merged_risks = dedupe(
            [*rule_decision.risk_flags, *llm_decision.risk_flags]
            + (["classifier_disagreement"] if rule_decision.primary_intent != llm_decision.primary_intent else [])
        )
        merged_missing = dedupe(
            [*rule_decision.missing_slots, *llm_decision.missing_slots]
        )

        return IntentDecision(
            source="fusion",
            primary_intent=chosen.primary_intent,
            secondary_intents=[intent for intent in merged_secondary if intent != chosen.primary_intent],
            route_target=chosen.route_target,
            confidence=round(min(confidence, 0.99), 3),
            requires_confirmation=chosen.requires_confirmation or bool(merged_missing),
            missing_slots=merged_missing,
            normalized_query=chosen.normalized_query,
            rationale=rationale,
            extracted_entities=merged_entities,
            risk_flags=merged_risks,
            candidate_scores=merge_candidate_scores(
                rule_decision.candidate_scores, llm_decision.candidate_scores
            ),
            evidence=dedupe([*rule_decision.evidence, *llm_decision.evidence]),
        )


# ---------------------------------------------------------------------------
# Routing engine (with cache, injection gate, short-circuit)
# ---------------------------------------------------------------------------

class IntentRoutingEngine:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._knowledge_base_service = KnowledgeBaseService(settings.data_dir)
        self._supply_analytics_service = SupplyAnalyticsService(settings.data_dir)
        self._workflow_automation_service = WorkflowAutomationService(settings.data_dir)

        self._rule_classifier = RuleBasedIntentClassifier(
            suppliers=self._supply_analytics_service.get_suppliers(),
            materials=self._supply_analytics_service.get_materials(),
            workflows=self._workflow_automation_service.get_workflow_names(),
            knowledge_topics=self._knowledge_base_service.get_topics(),
        )
        self._llm_classifier = LLMIntentClassifier(
            settings=settings,
            suppliers=self._supply_analytics_service.get_suppliers(),
            materials=self._supply_analytics_service.get_materials(),
            workflows=self._workflow_automation_service.get_workflow_names(),
            knowledge_topics=self._knowledge_base_service.get_topics(),
        )
        self._fusion_strategy = DecisionFusionStrategy()
        self._cache = QueryCache(
            max_size=settings.cache_max_size,
            ttl_seconds=settings.cache_ttl_seconds,
        )
        self._graph = self._build_graph()

    def build_health(self) -> HealthResponse:
        return HealthResponse(
            status="ok",
            app_name=self._settings.app_name,
            version=self._settings.app_version,
            llm_configured=self._settings.llm_configured,
            llm_enabled=self._settings.enable_llm_classifier,
            llm_status=self._llm_classifier.status,
        )

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        cache_key = QueryCache.build_key(
            request.query, request.allow_action_execution, request.dry_run
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("缓存命中: %s", cache_key)
            return cached

        initial_state: RoutingWorkflowState = {
            "request_id": f"req-{uuid.uuid4().hex[:10]}",
            "request": request,
            "trace": [],
        }
        final_state = self._graph.invoke(initial_state)

        response = AnalyzeResponse(
            request_id=final_state["request_id"],
            query=request.query,
            classification=final_state["final_decision"],
            rule_decision=final_state["rule_decision"],
            llm_decision=final_state.get("llm_decision"),
            subsystem_result=final_state["subsystem_result"],
            trace=final_state.get("trace", []),
        )

        self._cache.put(cache_key, response)
        return response

    # --- graph construction ---

    def _build_graph(self):
        graph = StateGraph(RoutingWorkflowState)
        graph.add_node("prepare", self._prepare)
        graph.add_node("rules", self._run_rules)
        graph.add_node("llm", self._run_llm)
        graph.add_node("fusion", self._run_fusion)
        graph.add_node("knowledge_rag", self._execute_knowledge_rag)
        graph.add_node("supply_analytics", self._execute_supply_analytics)
        graph.add_node("workflow_automation", self._execute_workflow_automation)
        graph.add_node("clarification", self._execute_clarification)

        graph.add_edge(START, "prepare")
        graph.add_edge("prepare", "rules")
        graph.add_edge("rules", "llm")
        graph.add_edge("llm", "fusion")
        graph.add_conditional_edges(
            "fusion",
            self._select_route,
            {
                RouteTarget.KNOWLEDGE_RAG.value: "knowledge_rag",
                RouteTarget.SUPPLY_ANALYTICS.value: "supply_analytics",
                RouteTarget.WORKFLOW_AUTOMATION.value: "workflow_automation",
                RouteTarget.CLARIFICATION.value: "clarification",
            },
        )
        graph.add_edge("knowledge_rag", END)
        graph.add_edge("supply_analytics", END)
        graph.add_edge("workflow_automation", END)
        graph.add_edge("clarification", END)
        return graph.compile()

    # --- node implementations ---

    def _prepare(self, state: RoutingWorkflowState) -> RoutingWorkflowState:
        raw_query = state["request"].query
        normalized_query = normalize_query(raw_query)
        injection_flags = detect_injection(raw_query)
        payload: dict[str, Any] = {"normalized_query": normalized_query}
        message = "已完成查询规范化与预处理。"
        if injection_flags:
            payload["injection_flags"] = injection_flags
            message += " 检测到疑似注入风险，LLM 分类将被禁用。"
        return {
            "normalized_query": normalized_query,
            "injection_flags": injection_flags,
            "trace": append_trace(state, "prepare", message, payload),
        }

    def _run_rules(self, state: RoutingWorkflowState) -> RoutingWorkflowState:
        rule_decision = self._rule_classifier.classify(state["request"].query)
        return {
            "rule_decision": rule_decision,
            "trace": append_trace(
                state,
                "rules",
                "规则分类器已完成首轮意图判断。",
                {
                    "primary_intent": rule_decision.primary_intent.value,
                    "confidence": rule_decision.confidence,
                    "missing_slots": rule_decision.missing_slots,
                    "risk_flags": rule_decision.risk_flags,
                },
            ),
        }

    def _run_llm(self, state: RoutingWorkflowState) -> RoutingWorkflowState:
        if self._llm_classifier.status in {"disabled", "not_configured", "circuit_open"}:
            return {
                "llm_decision": None,
                "trace": append_trace(
                    state,
                    "llm",
                    f"LLM 分类器不可用（{self._llm_classifier.status}），沿用规则兜底。",
                    {"llm_status": self._llm_classifier.status},
                ),
            }

        if state.get("injection_flags"):
            return {
                "llm_decision": None,
                "trace": append_trace(
                    state,
                    "llm",
                    "检测到注入风险，已跳过 LLM 分类。",
                    {"llm_status": "skipped_injection", "injection_flags": state["injection_flags"]},
                ),
            }

        rule_decision = state.get("rule_decision")
        if rule_decision and rule_decision.confidence >= self._settings.rule_high_confidence_threshold:
            return {
                "llm_decision": None,
                "trace": append_trace(
                    state,
                    "llm",
                    f"规则置信度 {rule_decision.confidence} 已超过阈值，短路跳过 LLM。",
                    {"llm_status": "short_circuited", "rule_confidence": rule_decision.confidence},
                ),
            }

        try:
            llm_decision = self._llm_classifier.classify(
                query=state["request"].query,
                normalized_query=state["normalized_query"],
            )
            return {
                "llm_decision": llm_decision,
                "trace": append_trace(
                    state,
                    "llm",
                    "LLM 结构化分类已完成。",
                    {
                        "primary_intent": llm_decision.primary_intent.value,
                        "confidence": llm_decision.confidence,
                        "llm_status": self._llm_classifier.status,
                    },
                ),
            }
        except Exception as exc:
            self._llm_classifier.record_error(str(exc))
            return {
                "llm_decision": None,
                "trace": append_trace(
                    state,
                    "llm",
                    "LLM 分类失败，已自动切换到规则兜底。",
                    {"llm_status": self._llm_classifier.status, "error": str(exc)},
                ),
            }

    def _run_fusion(self, state: RoutingWorkflowState) -> RoutingWorkflowState:
        final_decision = self._fusion_strategy.fuse(
            state["rule_decision"],
            state.get("llm_decision"),
        )

        if (
            final_decision.primary_intent == IntentType.WORKFLOW_AUTOMATION
            and not state["request"].allow_action_execution
        ):
            final_decision = final_decision.model_copy(
                update={
                    "requires_confirmation": True,
                    "risk_flags": dedupe(
                        [*final_decision.risk_flags, "execution_guardrail_enabled"]
                    ),
                }
            )

        injection_flags = state.get("injection_flags", [])
        if injection_flags:
            final_decision = final_decision.model_copy(
                update={
                    "risk_flags": dedupe([*final_decision.risk_flags, *injection_flags]),
                }
            )

        return {
            "final_decision": final_decision,
            "trace": append_trace(
                state,
                "fusion",
                "已完成规则与 LLM 判定融合，并生成最终路由。",
                {
                    "primary_intent": final_decision.primary_intent.value,
                    "route_target": final_decision.route_target.value,
                    "confidence": final_decision.confidence,
                },
            ),
        }

    def _execute_knowledge_rag(self, state: RoutingWorkflowState) -> RoutingWorkflowState:
        result = self._knowledge_base_service.run(
            query=state["request"].query,
            entities=state["final_decision"].extracted_entities,
        )
        return {
            "subsystem_result": result,
            "trace": append_trace(
                state,
                "knowledge_rag",
                "已路由到企业知识库检索子系统。",
                {"status": result.status},
            ),
        }

    def _execute_supply_analytics(self, state: RoutingWorkflowState) -> RoutingWorkflowState:
        result = self._supply_analytics_service.run(
            query=state["request"].query,
            entities=state["final_decision"].extracted_entities,
        )
        return {
            "subsystem_result": result,
            "trace": append_trace(
                state,
                "supply_analytics",
                "已路由到供应量数据分析子系统。",
                {"status": result.status},
            ),
        }

    def _execute_workflow_automation(
        self,
        state: RoutingWorkflowState,
    ) -> RoutingWorkflowState:
        result = self._workflow_automation_service.run(
            query=state["request"].query,
            entities=state["final_decision"].extracted_entities,
            allow_action_execution=state["request"].allow_action_execution,
            dry_run=state["request"].dry_run,
        )
        return {
            "subsystem_result": result,
            "trace": append_trace(
                state,
                "workflow_automation",
                "已路由到供应量工作流自动化子系统。",
                {
                    "status": result.status,
                    "execution_mode": result.data.get("execution_mode", "unknown"),
                },
            ),
        }

    def _execute_clarification(self, state: RoutingWorkflowState) -> RoutingWorkflowState:
        decision = state["final_decision"]
        result = SubsystemResult(
            target=RouteTarget.CLARIFICATION,
            status="clarification_required",
            title="需要补充信息后再路由",
            summary=(
                "当前请求存在意图冲突或关键槽位缺失，为避免误触发系统，"
                "建议先明确目标子系统、时间范围或工作流名称。"
            ),
            data={
                "missing_slots": decision.missing_slots,
                "risk_flags": decision.risk_flags,
                "secondary_intents": [intent.value for intent in decision.secondary_intents],
            },
            suggestions=[
                "如果你要查企业文档，请补充知识主题或制度名称",
                "如果你要做供应量分析，请补充时间范围、供应商或物料",
                "如果你要执行流程，请补充具体工作流名称并确认是否允许执行",
            ],
        )
        return {
            "subsystem_result": result,
            "trace": append_trace(
                state,
                "clarification",
                "已进入澄清分支，等待用户补充信息。",
                {"status": result.status},
            ),
        }

    @staticmethod
    def _select_route(state: RoutingWorkflowState) -> str:
        return state["final_decision"].route_target.value


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def normalize_query(query: str) -> str:
    normalized = unicodedata.normalize("NFKC", query)
    normalized = normalized.strip().replace("\n", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.lower()


def extract_time_range(query: str) -> str | None:
    patterns = [
        r"(\d{1,2}月到\d{1,2}月)",
        r"(近7天|最近7天|近一周|最近一周)",
        r"(近30天|最近30天|近一个月|最近一个月)",
        r"(近三个月|最近三个月|近3个月|最近3个月)",
        r"(近半年|最近半年|近六个月|最近六个月)",
        r"(近一年|最近一年|近12个月)",
        r"(本月|上月|本季度|上季度|本年度|上年度)",
        r"(去年[QqＱ][1-4]|今年[QqＱ][1-4])",
        r"(20\d{2}年\d{1,2}月)",
        r"(20\d{2}-\d{1,2})",
        r"(20\d{2}年全年|20\d{2}年)",
    ]

    for pattern in patterns:
        match = re.search(pattern, query)
        if match:
            return match.group(1)

    return None


def merge_entities(left: ExtractedEntities, right: ExtractedEntities) -> ExtractedEntities:
    return ExtractedEntities(
        suppliers=dedupe([*left.suppliers, *right.suppliers]),
        materials=dedupe([*left.materials, *right.materials]),
        metrics=dedupe([*left.metrics, *right.metrics]),
        time_range=left.time_range or right.time_range,
        workflow_name=left.workflow_name or right.workflow_name,
        knowledge_topics=dedupe([*left.knowledge_topics, *right.knowledge_topics]),
        action_verb=left.action_verb or right.action_verb,
    )


def merge_candidate_scores(
    left: dict[str, float],
    right: dict[str, float],
) -> dict[str, float]:
    merged: dict[str, float] = {}
    for intent in IntentType:
        left_score = left.get(intent.value, 0.0)
        right_score = right.get(intent.value, 0.0)
        merged[intent.value] = round(max(left_score, right_score), 3)
    return merged


def append_trace(
    state: RoutingWorkflowState,
    stage: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> list[TraceEvent]:
    trace = list(state.get("trace", []))
    trace.append(TraceEvent(stage=stage, message=message, payload=payload or {}))
    return trace


def dedupe(items: list[str]) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        results.append(item)
    return results


def dedupe_intents(items: list[IntentType]) -> list[IntentType]:
    results: list[IntentType] = []
    seen: set[IntentType] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        results.append(item)
    return results
