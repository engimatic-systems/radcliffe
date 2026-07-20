from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from radcliffe_control.resume_cost import ModelPricingRegistry, PricingDocument
from radcliffe_control.settings import Settings


def registry(*models: dict[str, object]) -> ModelPricingRegistry:
    return ModelPricingRegistry(
        PricingDocument.model_validate(
            {
                "schema": "radcliffe-model-pricing-v1",
                "updated_at": "2026-05-27",
                "source_url": "https://example.invalid/official-pricing",
                "models": list(models),
            }
        )
    )


def cloud_model(
    *,
    model_id: str = "cloud-model",
    effective: str = "2026-01-01",
    expires: str | None = None,
    tiers: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "provider": "anthropic",
        "model_id": model_id,
        "currency": "USD",
        "pricing_effective_date": effective,
        "pricing_expires_on": expires,
        "local": False,
        "cold_input_class": "cache_write_5m",
        "tiers": tiers
        or [
            {
                "up_to_input_tokens": 200_000,
                "uncached_input_per_million": 1,
                "cache_write_5m_per_million": 1.25,
                "cache_read_per_million": 0.1,
                "output_per_million": 5,
                "promotional_expires_on": None,
            }
        ],
    }


def estimate(
    pricing: ModelPricingRegistry,
    model_id: str = "cloud-model",
    inputs: int = 100_000,
    outputs: int = 512,
    *,
    exact: bool = True,
    overflow: bool = False,
    cache: str = "cold",
    as_of: date = date(2026, 7, 19),
):
    return pricing.estimate(
        model_id=model_id,
        projected_input_tokens=inputs,
        representative_output_tokens=outputs,
        exact=exact,
        overflow=overflow,
        cache_assumption=cache,  # type: ignore[arg-type]
        as_of=as_of,
    )


def test_checked_in_registry_matches_pinned_haiku_and_local_contracts() -> None:
    pricing = ModelPricingRegistry.load(Settings().model_pricing_path)

    haiku = estimate(pricing, "claude-haiku-4-5-20251001")
    local = estimate(pricing, "qwen3:8b")

    assert haiku.provider == "anthropic"
    assert haiku.input_rate_per_million == 1.25
    assert haiku.output_rate_per_million == 5
    assert haiku.pricing_effective_date == date(2026, 5, 27)
    assert local.available
    assert local.no_api_charge
    assert local.minimum_usd == local.maximum_usd == 0


def test_cold_and_warm_ranges_use_input_class_and_representative_output() -> None:
    pricing = registry(cloud_model())

    cold = estimate(pricing)
    warm = estimate(pricing, cache="warm")
    empty = estimate(pricing, inputs=0)

    assert Decimal(str(cold.minimum_usd)) == Decimal("0.125")
    assert Decimal(str(cold.maximum_usd)) == Decimal("0.12756")
    assert cold.input_rate_per_million == 1.25
    assert Decimal(str(warm.minimum_usd)) == Decimal("0.01")
    assert warm.input_rate_per_million == 0.1
    assert empty.minimum_usd == 0
    assert Decimal(str(empty.maximum_usd)) == Decimal("0.00256")


def test_inexact_overflow_unknown_and_model_switches_are_honest() -> None:
    local = {
        "provider": "ollama",
        "model_id": "local-model",
        "currency": "USD",
        "pricing_effective_date": "2026-01-01",
        "pricing_expires_on": None,
        "local": True,
        "cold_input_class": "uncached_input",
        "tiers": [],
    }
    pricing = registry(cloud_model(), local)

    inexact = estimate(pricing, exact=False)
    overflow = estimate(pricing, overflow=True)
    unknown = estimate(pricing, "future-model")
    local_estimate = estimate(pricing, "local-model")

    assert inexact.token_basis == "estimated_projected_payload"
    assert inexact.available
    assert not overflow.available and overflow.unavailable_reason == "context_overflow"
    assert overflow.minimum_usd is None
    assert not unknown.available and unknown.unavailable_reason == "unknown_model"
    assert local_estimate.no_api_charge and local_estimate.maximum_usd == 0


def test_tier_boundaries_and_missing_long_context_tier_are_explicit() -> None:
    bounded = {
        "up_to_input_tokens": 200_000,
        "uncached_input_per_million": 1,
        "cache_write_5m_per_million": 1.25,
        "cache_read_per_million": 0.1,
        "output_per_million": 5,
        "promotional_expires_on": None,
    }
    premium = {
        "up_to_input_tokens": None,
        "uncached_input_per_million": 2,
        "cache_write_5m_per_million": 2.5,
        "cache_read_per_million": 0.2,
        "output_per_million": 7.5,
        "promotional_expires_on": None,
    }
    tiered = registry(cloud_model(tiers=[bounded, premium]))
    limited = registry(cloud_model(tiers=[bounded]))

    assert estimate(tiered, inputs=200_000).input_rate_per_million == 1.25
    assert estimate(tiered, inputs=200_001).input_rate_per_million == 2.5
    unavailable = estimate(limited, inputs=200_001)
    assert not unavailable.available
    assert unavailable.unavailable_reason == "pricing_tier_unavailable"


def test_effective_expired_and_promotional_dates_fail_closed() -> None:
    future = registry(cloud_model(effective="2026-08-01"))
    expired = registry(cloud_model(expires="2026-07-01"))
    promotional = cloud_model()
    promotional["tiers"] = [
        {
            **promotional["tiers"][0],  # type: ignore[index]
            "promotional_expires_on": "2026-07-01",
        }
    ]

    assert estimate(future).unavailable_reason == "pricing_not_yet_effective"
    assert estimate(expired).unavailable_reason == "pricing_expired"
    assert estimate(registry(promotional)).unavailable_reason == "promotion_expired"


def test_registry_rejects_duplicate_models_and_invalid_tier_order() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        registry(cloud_model(), cloud_model())
    reversed_tiers = cloud_model(
        tiers=[
            {**cloud_model()["tiers"][0], "up_to_input_tokens": 200_000},  # type: ignore[index]
            {**cloud_model()["tiers"][0], "up_to_input_tokens": 100_000},  # type: ignore[index]
        ]
    )
    with pytest.raises(ValidationError, match="ascending"):
        registry(reversed_tiers)
