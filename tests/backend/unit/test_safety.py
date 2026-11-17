from medical_agent.modules.safety.service import evaluate_user_input


def test_allows_general_education_question() -> None:
    decision = evaluate_user_input("小耳畸形通常有哪些科普注意事项？")
    assert decision.action == "allow"


def test_refuses_explicit_diagnosis_request() -> None:
    decision = evaluate_user_input("请根据描述帮我诊断是什么病")
    assert decision.action == "refuse"
    assert decision.code == "medical_boundary"


def test_prioritizes_urgent_redirect() -> None:
    decision = evaluate_user_input("伤口正在大量出血，应该怎么办？")
    assert decision.action == "urgent_redirect"
    assert decision.code == "urgent_symptom"
