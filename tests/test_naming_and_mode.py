"""P2-compatible product names and the mock/live mode reported to the UI."""

import pytest

from p3.naming import ProductName


@pytest.mark.parametrize("Prompt,Expected", [
    ("A slim band that twists into a small open leaf at the front", "The Laurel Ring"),
    ("serpent wrapped signet", "The Serpent Signet Ring"),
    ("Ring inspired by Haaland", "The Haaland Ring"),
    ("a chunky statement band", "The Bold Ring"),
])
def test_product_names_follow_pipeline2_rules(Prompt, Expected):
    assert ProductName(Prompt) == Expected


def test_fallback_name_is_deterministic():
    assert ProductName("xyz qqq") == ProductName("xyz qqq")
    assert ProductName("xyz qqq").startswith("The ") and ProductName("xyz qqq").endswith(" Ring")


async def test_design_title_uses_product_name(H):
    Batch = await H.NewDesign("A band with a crescent moon")
    assert (await H.Design(Batch["design_id"]))["title"] == "The Luna Ring"


async def test_health_reports_mock_mode(H):
    R = (await H.Client.get("/api/health")).json()
    assert R["mode"] == "mock" and R["provider"] == "mock"
