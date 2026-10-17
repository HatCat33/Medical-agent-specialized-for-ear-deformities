"""Minimal deterministic safety gate for the first validation release."""

from medical_agent.modules.safety.schemas import SafetyDecision

URGENT_TERMS = ("大量出血", "呼吸困难", "意识不清", "高热不退", "伤口裂开")
DIAGNOSIS_TERMS = ("帮我诊断", "给我确诊", "判断是什么病", "判断畸形类型")
PRESCRIPTION_TERMS = ("给我开药", "开处方", "用药剂量", "怎么配药")


def evaluate_user_input(content: str) -> SafetyDecision:
    compact = "".join(content.split())
    if any(term in compact for term in URGENT_TERMS):
        return SafetyDecision(
            action="urgent_redirect",
            code="urgent_symptom",
            response=(
                "你描述的情况可能需要尽快由医疗专业人员现场评估。"
                "请立即联系当地急救服务或尽快前往医院，不要仅依赖在线回复。"
            ),
        )
    if any(term in compact for term in DIAGNOSIS_TERMS + PRESCRIPTION_TERMS):
        return SafetyDecision(
            action="refuse",
            code="medical_boundary",
            response=(
                "我可以提供耳畸形相关健康科普信息，但不能进行诊断、确定畸形类型、"
                "判断是否需要手术或提供处方。具体情况请由医生结合面诊和检查评估。"
            ),
        )
    return SafetyDecision(action="allow", code="allowed")

