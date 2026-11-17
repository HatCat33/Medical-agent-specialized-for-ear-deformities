from medical_agent.api.dependencies import owner_reference


def test_owner_reference_is_stable_and_matches_the_frontend() -> None:
    assert owner_reference("web-test-visitor") == "ab3d6c568d61eebf"
