"""Creator and community achievement evaluation."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from bson import ObjectId

from .extensions import mongo

BADGE_RULES = (
    {
        "id": "first_spark",
        "name": "First Spark",
        "icon": "sparkles",
        "category": "Content Creator",
        "description": "Publish your first video.",
        "metric": "total_uploads",
        "threshold": 1,
    },
    {
        "id": "rising_creator",
        "name": "Rising Creator",
        "icon": "film",
        "category": "Content Creator",
        "description": "Publish 5 videos.",
        "metric": "total_uploads",
        "threshold": 5,
    },
    {
        "id": "master_storyteller",
        "name": "Master Storyteller",
        "icon": "film",
        "category": "Content Creator",
        "description": "Publish 25 videos.",
        "metric": "total_uploads",
        "threshold": 25,
    },
    {
        "id": "friendly_face",
        "name": "Friendly Face",
        "icon": "users",
        "category": "Community & Reach",
        "description": "Reach 10 subscribers.",
        "metric": "total_subscribers",
        "threshold": 10,
    },
    {
        "id": "community_favorite",
        "name": "Community Favorite",
        "icon": "users",
        "category": "Community & Reach",
        "description": "Reach 50 subscribers.",
        "metric": "total_subscribers",
        "threshold": 50,
    },
    {
        "id": "star_influencer",
        "name": "Star Influencer",
        "icon": "star",
        "category": "Community & Reach",
        "description": "Reach 250 subscribers.",
        "metric": "total_subscribers",
        "threshold": 250,
    },
    {
        "id": "safe_hands",
        "name": "Safe Hands",
        "icon": "shield-check",
        "category": "Safety & Trust",
        "description": "Publish 5 consecutive videos with no policy violations.",
        "metric": "clean_streak",
        "threshold": 5,
    },
    {
        "id": "guardian_certified",
        "name": "Guardian Certified",
        "icon": "certificate",
        "category": "Safety & Trust",
        "description": "Keep every published video at a 95% or higher clean score.",
        "metric": "guardian_clean_score_pct",
        "threshold": 95,
    },
    {
        "id": "century_views",
        "name": "Century Views",
        "icon": "eye",
        "category": "Engagement",
        "description": "Reach 100 total views across your published videos.",
        "metric": "total_views",
        "threshold": 100,
    },
)


def _is_clean_video(video: dict) -> bool:
    ai_result = video.get("ai_result") or {}
    policy = ai_result.get("policy") or {}
    violations = policy.get("violations_summary") or ai_result.get(
        "violations_summary", []
    )
    return policy.get("compliance_status") == "passed" and not violations


def _scan_score(video: dict) -> float | None:
    ai_result = video.get("ai_result") or {}
    policy = ai_result.get("policy") or {}
    score = ai_result.get("overall_safe_pct")
    if score is None:
        score = policy.get("overall_safe_pct")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
    ):
        return None
    return float(score)


def _creator_subscriber_count(persona_id: ObjectId) -> int:
    subscriber_ids = {
        str(persona["_id"])
        for persona in mongo.db.personas.find(
            {"subscribed_creators": {"$in": [persona_id, str(persona_id)]}},
            {"_id": 1},
        )
    }

    creator_id_values = [persona_id, str(persona_id)]
    channels = mongo.db.channels.find(
        {"created_by": {"$in": creator_id_values}},
        {"subscribers": 1},
    )
    for channel in channels:
        subscriber_ids.update(
            str(subscriber_id) for subscriber_id in (channel.get("subscribers") or [])
        )
    return len(subscriber_ids)


def _calculate_metrics(persona_id: ObjectId) -> dict:
    owner_ids = [str(persona_id), persona_id]
    videos = list(
        mongo.db.videos.find(
            {"uploaded_by": {"$in": owner_ids}, "status": "published"},
            {
                "views": 1,
                "ai_result": 1,
                "published_at": 1,
                "created_at": 1,
            },
        ).sort([("published_at", -1), ("created_at", -1)])
    )

    clean_streak = 0
    for video in videos:
        if not _is_clean_video(video):
            break
        clean_streak += 1

    qualified_scores = [
        _scan_score(video)
        for video in videos
        if _is_clean_video(video) and (_scan_score(video) or 0) >= 95
    ]
    all_content_qualified = bool(videos) and len(qualified_scores) == len(videos)
    guardian_score = (
        min(qualified_scores)
        if all_content_qualified and qualified_scores
        else None
    )

    return {
        "total_uploads": len(videos),
        "total_subscribers": _creator_subscriber_count(persona_id),
        "total_views": sum(
            int(max(0, video.get("views", 0)))
            for video in videos
            if isinstance(video.get("views", 0), (int, float))
        ),
        "clean_streak": clean_streak,
        "guardian_clean_score_pct": guardian_score,
        "guardian_qualified_uploads": len(qualified_scores),
        "published_video_count": len(videos),
    }


def evaluate_user_badges(user_id: str | ObjectId) -> dict:
    """Recalculate a persona's achievement metrics and award newly earned badges."""
    if not ObjectId.is_valid(str(user_id)):
        raise ValueError("A valid persona id is required to evaluate badges.")

    persona_id = ObjectId(str(user_id))
    persona = mongo.db.personas.find_one({"_id": persona_id}, {"badges": 1})
    if persona is None:
        raise LookupError(f"Persona {persona_id} was not found.")

    metrics = _calculate_metrics(persona_id)
    mongo.db.personas.update_one(
        {"_id": persona_id},
        {"$set": {"metrics": metrics}},
    )

    mongo.db.personas.update_one(
        {
            "_id": persona_id,
            "$or": [
                {"badges": {"$exists": False}},
                {"badges": None},
            ],
        },
        {"$set": {"badges": []}},
    )
    existing_badges = persona.get("badges") or []
    existing_ids = {
        badge.get("badge_id")
        for badge in existing_badges
        if isinstance(badge, dict)
    }
    newly_unlocked = []
    for rule in BADGE_RULES:
        metric_value = metrics.get(rule["metric"])
        if rule["id"] in existing_ids:
            continue
        if rule["id"] == "guardian_certified":
            eligible = metric_value is not None and metric_value >= rule["threshold"]
        else:
            eligible = (metric_value or 0) >= rule["threshold"]
        if not eligible:
            continue

        badge = {
            "badge_id": rule["id"],
            "name": rule["name"],
            "icon": rule["icon"],
            "unlocked_at": datetime.now(timezone.utc),
        }
        result = mongo.db.personas.update_one(
            {"_id": persona_id, "badges.badge_id": {"$ne": rule["id"]}},
            {"$push": {"badges": badge}},
        )
        if result.modified_count:
            newly_unlocked.append(badge)
            existing_ids.add(rule["id"])
            from .notifications import create_notification

            create_notification(
                str(persona_id),
                (
                    f"You earned the {rule['name']} badge! "
                    f"{rule['description']}"
                ),
                f"badge:{persona_id}:{rule['id']}",
                "badge",
                "/auth/profile#badges",
                title="Achievement unlocked!",
                icon_type=rule["icon"],
            )

    updated_persona = mongo.db.personas.find_one(
        {"_id": persona_id},
        {"badges": 1},
    ) or {}
    badges_by_id = {
        badge.get("badge_id"): badge
        for badge in (updated_persona.get("badges") or [])
        if isinstance(badge, dict)
    }
    badge_cards = []
    for rule in BADGE_RULES:
        metric_value = metrics.get(rule["metric"])
        if rule["id"] == "guardian_certified":
            current = metrics["guardian_qualified_uploads"]
            goal = max(metrics["published_video_count"], 1)
            complete = metrics["guardian_clean_score_pct"] is not None
            progress_text = (
                f"{current} / {metrics['published_video_count']} published videos at 95%+"
                if metrics["published_video_count"]
                else "Publish your first video at 95% or higher."
            )
        else:
            current = int(metric_value or 0)
            goal = rule["threshold"]
            complete = current >= goal
            progress_text = f"{min(current, goal)} / {goal}"

        badge = badges_by_id.get(rule["id"])
        unlocked_at = badge.get("unlocked_at") if badge else None
        badge_cards.append({
            **rule,
            "badge_id": rule["id"],
            "unlocked": badge is not None,
            "unlocked_at": (
                unlocked_at.isoformat()
                if isinstance(unlocked_at, datetime)
                else unlocked_at
            ),
            "current": current,
            "goal": goal,
            "progress_pct": min(100, int(current * 100 / goal)) if goal else 0,
            "progress_text": progress_text,
            "complete": complete,
        })

    return {
        "badges": badge_cards,
        "metrics": metrics,
        "newly_unlocked": [
            {
                **badge,
                "unlocked_at": badge["unlocked_at"].isoformat(),
            }
            for badge in newly_unlocked
        ],
    }
