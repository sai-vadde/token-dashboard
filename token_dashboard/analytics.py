"""Provider-aware usage, cost, cache, and subscription summaries."""
from __future__ import annotations

from .db import model_breakdown, overview_totals, source_summary
from .pricing import codex_credits_for, financial_summary
from .providers import enabled_sources, platform_catalog, platforms_configured


def _sum_credits(rows: list, pricing: dict) -> dict:
    known = 0.0
    priced = 0
    total = 0
    unknown = []
    for row in rows:
        calls = int(row.get("turns", 0) or 0)
        total += calls
        value = codex_credits_for(row.get("model", ""), row, pricing)
        if value is None:
            if row.get("model") not in unknown:
                unknown.append(row.get("model") or "unknown")
        else:
            known += value
            priced += calls
    return {
        "estimated_credits": round(known, 4) if priced else (0.0 if total == 0 else None),
        "credit_coverage": 1.0 if total == 0 else round(priced / total, 4),
        "credit_unknown_models": unknown,
    }


def _subscription(platform: dict, pricing: dict) -> dict:
    plans = pricing.get("provider_plans", {}).get(platform["source"], {})
    selected = plans.get(platform.get("plan"), {})
    return {
        "plan_options": plans,
        "plan_label": selected.get("label", platform.get("plan", "api")),
        "subscription_usd": selected.get("monthly"),
    }


def platform_analytics(db_path, pricing: dict, since=None, until=None) -> dict:
    enabled = enabled_sources(db_path)
    stats_by_source = {
        row["source"]: row for row in source_summary(db_path, since, until)
    }
    platforms = []
    for platform in platform_catalog(db_path):
        source = platform["source"]
        models = model_breakdown(db_path, since, until, source=source) if platform["status"] == "available" else []
        stats = stats_by_source.get(source, {})
        item = {**platform, **stats, **_subscription(platform, pricing)}
        item["financial"] = financial_summary(models, pricing)
        item["cache"] = {
            "read_tokens": int(stats.get("cache_read_tokens", 0) or 0),
            "read_events": int(stats.get("cache_read_events", 0) or 0),
            "create_tokens": (
                int(stats.get("cache_create_5m_tokens", 0) or 0)
                + int(stats.get("cache_create_1h_tokens", 0) or 0)
                if platform["capabilities"]["cache_create"] == "reported" else None
            ),
            "create_events": (
                int(stats.get("cache_create_events", 0) or 0)
                if platform["capabilities"]["cache_create"] == "reported" else None
            ),
            "create_telemetry": platform["capabilities"]["cache_create"],
            "savings_usd": item["financial"]["cache_savings_usd"],
            "savings_partial": item["financial"]["cache_savings_partial"],
        }
        if source == "codex":
            item["credits"] = _sum_credits(models, pricing)
        platforms.append(item)

    all_models = model_breakdown(db_path, since, until, source=enabled)
    totals = overview_totals(db_path, since, until, source=enabled)
    all_financial = financial_summary(all_models, pricing)
    enabled_items = [p for p in platforms if p["enabled"] and p["status"] == "available"]
    subscriptions = [p["subscription_usd"] for p in enabled_items if p.get("subscription_usd")]
    any_unreported_create = any(
        p["capabilities"]["cache_create"] != "reported" for p in enabled_items
    )
    return {
        "configured": platforms_configured(db_path),
        "enabled_sources": enabled,
        "platforms": platforms,
        "all": {
            **totals,
            "financial": all_financial,
            "monthly_subscriptions_usd": round(sum(subscriptions), 2),
            "cache": {
                "read_tokens": int(totals.get("cache_read_tokens", 0) or 0),
                "explicit_create_tokens": int(totals.get("cache_create_5m_tokens", 0) or 0)
                    + int(totals.get("cache_create_1h_tokens", 0) or 0),
                "create_telemetry_complete": not any_unreported_create,
                "savings_usd": all_financial["cache_savings_usd"],
                "savings_partial": all_financial["cache_savings_partial"],
            },
        },
        "pricing_provenance": pricing.get("provenance", {}),
    }
