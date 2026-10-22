"""
Policy RAG Agent — ChromaDB vector store + sentence-transformers.
Retrieves relevant age-tiered safety policy clauses for multimodal observations.
"""
from __future__ import annotations

import math
import os
from threading import Lock
from typing import List, Dict, Any

from ..category_rules import AGE_GROUPS, CATEGORY_OPTIONS

EMBED_MODEL = os.getenv(
    "SENTENCE_TRANSFORMER_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)

ALLOWED_CATEGORIES = {
    age_group: {category.casefold() for category in categories}
    for age_group, categories in CATEGORY_OPTIONS.items()
}

# ── Granular age-tiered policy corpus ────────────────────────────────────────
POLICY_DOCUMENTS = [
    # COPPA / Preschool (0–4)
    {
        "id": "COPPA-001",
        "text": (
            "Platform Policy: Preschool silo (age 0–4) is whitelist-only and has "
            "zero tolerance for loud audio, combat, or conflict. Loud audio is "
            "defined as maximum 100 ms RMS above -12 dBFS."
        ),
        "silo": "preschool",
    },
    {
        "id": "COPPA-002",
        "text": (
            "Platform Policy: Preschool, Kids, and Tweens silos do not use "
            "behavioral tracking."
        ),
        "silo": "preschool",
    },
    {
        "id": "PRESCHOOL-001",
        "text": (
            "Platform Policy: Preschool content (age 0–4) categories include nursery "
            "rhymes, moral stories, cartoons, alphabet and phonics, numbers and "
            "counting, colors and shapes, animal videos, songs, basic learning, and "
            "simple stories. Apply preschool safety rules to all such content."
        ),
        "silo": "preschool",
    },
    # Kids (5–8)
    {
        "id": "KIDS-001",
        "text": (
            "Platform Policy: Kids content (age 5–8) categories include educational "
            "videos, animated stories, cartoons, science basics, mathematics, "
            "crafts and DIY, nature and animals, moral stories, guided learning, and "
            "simple experiments. Violence, weapons, profanity, and substances are "
            "not allowed."
        ),
        "silo": "kids",
    },
    {
        "id": "KIDS-002",
        "text": (
            "Platform Policy: Kids content (age 5–8) has zero tolerance for violence, "
            "weapons, profanity, and substances."
        ),
        "silo": "kids",
    },
    # Tweens (9–12)
    {
        "id": "TWEENS-001",
        "text": (
            "Platform Policy: Tweens content (age 9–12) categories include coding "
            "tutorials, technology videos, educational content, music, vlogs, "
            "gaming, sports, science experiments, reviews, and creative content. "
            "Filter profanity and horror."
        ),
        "silo": "tweens",
    },
    {
        "id": "TWEENS-002",
        "text": (
            "Platform Policy: Filter profanity and horror for the Tweens silo "
            "(age 9–12)."
        ),
        "silo": "tweens",
    },
    # Teens (13–17)
    {
        "id": "TEENS-001",
        "text": (
            "Platform Policy: Teens content (age 13–17) categories include gaming, "
            "technology, music, vlogs, reviews, commentary, sports, entertainment, "
            "educational videos, career guidance, and current-topic discussions. "
            "Gore and explicit adult material are not allowed."
        ),
        "silo": "teens",
    },
    {
        "id": "TEENS-002",
        "text": (
            "Platform Policy: Teens content (age 13–17) has behavioral tracking "
            "enabled."
        ),
        "silo": "teens",
    },
    # Unrestricted (18+)
    {
        "id": "ADULT-001",
        "text": (
            "Platform Policy: Unrestricted content (age 18+) categories include "
            "entertainment, gaming, music, vlogs, news, documentaries, tutorials, "
            "reviews, commentary, and educational content. Illegal content requires "
            "immediate removal."
        ),
        "silo": "unrestricted",
    },
    # Cross-silo universal rules
    {
        "id": "UNIVERSAL-001",
        "text": (
            "Platform Policy: Apply category restrictions for the selected age tier; "
            "object detections alone do not establish violence or weapon use."
        ),
        "silo": "all",
    },
    {
        "id": "UNIVERSAL-002",
        "text": (
            "Platform Policy: Behavioral tracking is disabled for Preschool, Kids, "
            "and Tweens, and enabled for Teens and Unrestricted."
        ),
        "silo": "all",
    },
    {
        "id": "UNIVERSAL-003",
        "text": (
            "Platform Policy: Evaluate each age tier using its configured category, "
            "visual weapon, transcript toxicity, and audio loudness checks."
        ),
        "silo": "all",
    },
]

PRESCHOOL_MAX_WINDOW_RMS_DBFS = -12.0
WEAPON_CLASSES = {"knife", "gun", "pistol", "rifle", "sword"}
AGE_GROUP_SCAN_THRESHOLDS = {
    "preschool": 99.0,
    "kids": 95.0,
    "tweens": 90.0,
    "teens": 85.0,
    "unrestricted": 80.0,
}
AGE_GROUP_POLICY_RULES = {
    "preschool": {
        "restricted_weapons": True,
        "transcript_toxicity": True,
        "max_audio_rms_dbfs": PRESCHOOL_MAX_WINDOW_RMS_DBFS,
    },
    "kids": {
        "restricted_weapons": True,
        "transcript_toxicity": True,
        "max_audio_rms_dbfs": None,
    },
    "tweens": {
        "restricted_weapons": False,
        "transcript_toxicity": True,
        "max_audio_rms_dbfs": None,
    },
    "teens": {
        "restricted_weapons": False,
        "transcript_toxicity": False,
        "max_audio_rms_dbfs": None,
    },
    "unrestricted": {
        "restricted_weapons": False,
        "transcript_toxicity": False,
        "max_audio_rms_dbfs": None,
    },
}


def _content_safety_metrics(
    visual_result: Dict[str, Any],
    audio_result: Dict[str, Any],
    transcript_result: Dict[str, Any],
    age_silo: str,
) -> Dict[str, Any]:
    visual_total = max(0, int(visual_result.get("total_frames_sampled", 0)))
    visual_flagged = min(
        visual_total,
        max(0, int(visual_result.get("unsafe_frame_count", 0))),
    )
    transcript_segments = transcript_result.get("segments", [])
    audio_applies_to_age_silo = AGE_GROUP_POLICY_RULES.get(
        age_silo, {}
    ).get("transcript_toxicity", False)
    audio_total = 0
    audio_flagged = 0
    if audio_applies_to_age_silo:
        audio_total = max(
            0,
            int(
                audio_result.get(
                    "analyzed_segment_count",
                    sum(bool(segment.get("text", "").strip()) for segment in transcript_segments),
                )
            ),
        )
        audio_flagged = min(
            audio_total,
            max(
                0,
                int(
                    audio_result.get(
                        "flagged_segment_count",
                        len(audio_result.get("violations", [])),
                    )
                ),
            ),
        )
    visual_safe = (
        round(100.0 * (visual_total - visual_flagged) / visual_total, 2)
        if visual_total
        else None
    )
    audio_safe = (
        round(100.0 * (audio_total - audio_flagged) / audio_total, 2)
        if audio_total
        else None
    )
    evidence_total = visual_total + audio_total
    flagged_evidence = visual_flagged + audio_flagged
    overall_safe = (
        round(100.0 * (evidence_total - flagged_evidence) / evidence_total, 2)
        if evidence_total
        else None
    )

    return {
        "overall_safe_pct": overall_safe,
        "overall_unsafe_pct": round(100.0 - overall_safe, 2) if overall_safe is not None else None,
        "visual_safe_pct": visual_safe,
        "audio_safe_pct": audio_safe,
        "score_evidence_count": evidence_total,
        "score_flagged_count": flagged_evidence,
        "score_basis": {
            "age_silo": age_silo,
            "sampled_frames": visual_total,
            "transcript_segments": audio_total,
            "flagged_frames": visual_flagged,
            "flagged_segments": audio_flagged,
            "audio_applies_to_age_silo": audio_applies_to_age_silo,
        },
    }


class PolicyRAGAgent:
    """ChromaDB-backed RAG agent for policy clause retrieval."""

    COLLECTION_NAME = "spectra_policies"

    def __init__(self):
        import chromadb
        from chromadb.utils import embedding_functions

        self._client = chromadb.EphemeralClient()
        self._ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBED_MODEL
        )
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"},
        )

    # ── Seeding ───────────────────────────────────────────────────────────────

    def seed_policies_if_empty(self):
        docs = [p["text"] for p in POLICY_DOCUMENTS]
        ids = [p["id"] for p in POLICY_DOCUMENTS]
        metas = [{"silo": p["silo"]} for p in POLICY_DOCUMENTS]
        existing_ids = set(self._collection.get(ids=ids).get("ids", []))
        missing = [
            (doc, policy_id, metadata)
            for doc, policy_id, metadata in zip(docs, ids, metas)
            if policy_id not in existing_ids
        ]
        if missing:
            self._collection.upsert(
                documents=[item[0] for item in missing],
                ids=[item[1] for item in missing],
                metadatas=[item[2] for item in missing],
            )

    def upsert_admin_policy(
        self, rule_id: str, text: str, silo: str, category: str, active: bool = True
    ):
        """Synchronize an administrator-managed policy document with ChromaDB."""
        builtin_ids = {policy["id"] for policy in POLICY_DOCUMENTS}
        chroma_id = rule_id if rule_id in builtin_ids else f"ADMIN-{rule_id}"
        metadata = {"silo": silo, "rule_id": rule_id, "category": category, "active": active}
        self._collection.upsert(
            ids=[chroma_id],
            documents=[text],
            metadatas=[metadata],
        )

    def is_ready(self) -> bool:
        """Return whether the persisted policy collection can be queried."""
        self._collection.count()
        return True

    # ── Query ─────────────────────────────────────────────────────────────────

    def query(
        self,
        observation: str,
        age_silo: str,
        n_results: int = 4,
        distance_threshold: float = 0.75,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve policy clauses relevant to an observed violation.

        Returns list of {id, text, silo, distance, violated} dicts.
        """
        results = self._collection.query(
            query_texts=[observation],
            n_results=n_results,
        )

        citations = []
        docs = results.get("documents", [[]])[0]
        dists = results.get("distances", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        ids = results.get("ids", [[]])[0]

        for doc, dist, meta, doc_id in zip(docs, dists, metas, ids):
            if meta.get("active") is False:
                continue
            policy_silo = meta.get("silo", "all")
            applicable = policy_silo in (age_silo, "all")
            citations.append(
                {
                    "id": doc_id,
                    "text": doc,
                    "silo": policy_silo,
                    "distance": round(dist, 4),
                    "violated": applicable and dist <= distance_threshold,
                }
            )

        return citations

    # ── Evaluate full AI result against policy ────────────────────────────────

    def evaluate(
        self,
        visual_result: Dict[str, Any],
        audio_result: Dict[str, Any],
        transcript_result: Dict[str, Any],
        age_silo: str,
        distance_threshold: float = 0.75,
        category: str = "",
    ) -> Dict[str, Any]:
        """
        Apply explicit age-tier category and detectable-evidence rules.

        Apply configured automated checks for the selected age tier. A failed
        inference step remains incomplete so publishing cannot treat it as a pass.

        Returns:
            {
              "verdict": "safe" | "unsafe",
              "compliance_status": "passed" | "violation" | "insufficient_evidence",
              "review_reasons": [...],
              "overall_safe_pct": float,
              "overall_unsafe_pct": float,
              "visual_safe_pct": float,
              "audio_safe_pct": float,
              "citations": [...],
              "violations_summary": [...],
            }
        """
        all_violations: List[Dict] = []
        citations: List[Dict] = []
        automatic_checks: List[Dict[str, str]] = []
        checks_complete = True
        allowed_categories = ALLOWED_CATEGORIES.get(age_silo)
        normalized_category = " ".join(category.casefold().split())

        if age_silo not in ALLOWED_CATEGORIES:
            all_violations.append(
                {
                    "type": "policy",
                    "description": "The selected age tier is not recognized.",
                    "suggestion": "Select a supported age tier and run the analysis again.",
                    "policy_citations": [],
                }
            )
        elif allowed_categories is not None and normalized_category not in allowed_categories:
            all_violations.append(
                {
                    "type": "category",
                    "description": (
                        f"Category '{category}' is not permitted for the "
                        f"{age_silo} age tier"
                    ),
                    "suggestion": "Choose a category permitted for this age tier.",
                    "policy_citations": [],
                }
            )
        else:
            automatic_checks.append({"check": "category", "status": "passed"})

        if visual_result.get("total_frames_sampled", 0) > 0:
            automatic_checks.append({"check": "visual_scan", "status": "passed"})
        else:
            automatic_checks.append({"check": "visual_scan", "status": "failed"})
            checks_complete = False

        detected_visual_violations = visual_result.get("violations", [])
        weapon_detections = [
            item
            for item in detected_visual_violations
            if item.get("class", "").casefold() in WEAPON_CLASSES
        ]
        age_rules = AGE_GROUP_POLICY_RULES.get(age_silo, {})
        weapons_are_restricted = age_rules.get("restricted_weapons", False)
        if weapons_are_restricted:
            for detection in weapon_detections:
                description = (
                    f"Detected restricted object: {detection['class']} "
                    f"({detection.get('confidence', 0):.0%} confidence)"
                )
                all_violations.append(
                    {
                        "type": "visual",
                        "timestamp_sec": detection.get("timestamp_sec"),
                        "description": description,
                        "suggestion": (
                            f"Restricted object detected at "
                            f"{_fmt_ts(detection.get('timestamp_sec', 0))}."
                        ),
                        "policy_citations": [],
                    }
                )
            automatic_checks.append(
                {
                    "check": "restricted_objects",
                    "status": "violation" if weapon_detections else "passed",
                }
            )
        else:
            automatic_checks.append(
                {"check": "restricted_objects", "status": "not_applicable"}
            )

        detected_audio_violations = audio_result.get("violations", [])
        if transcript_result.get("analysis_error"):
            automatic_checks.append(
                {"check": "transcript", "status": "failed"}
            )
            checks_complete = False
        applicable_audio_violations = (
            detected_audio_violations
            if age_rules.get("transcript_toxicity", False)
            else []
        )
        automatic_checks.append(
            {
                "check": "transcript_toxicity",
                "status": (
                    "violation"
                    if applicable_audio_violations
                    else "passed"
                    if age_rules.get("transcript_toxicity", False)
                    else "not_applicable"
                ),
            }
        )
        for a in applicable_audio_violations:
            obs = f"Transcript toxicity: {a['label']} score {a['score']} — '{a['text'][:80]}'"
            cits = self.query(obs, age_silo, distance_threshold=distance_threshold)
            violated = [c for c in cits if c["violated"]]
            all_violations.append(
                {
                    "type": "audio",
                    "timestamp_sec": a["timestamp_sec"],
                    "end_sec": a.get("end_sec"),
                    "description": f"Toxicity: {a['label']} ({a['score']})",
                    "score": a["score"],
                    "text": a["text"],
                    "policy_citations": violated,
                    "suggestion": (
                        f"Audio violation at {_fmt_ts(a['timestamp_sec'])}: "
                        f"Profanity/toxicity detected → [Auto-Mute]"
                    ),
                }
            )
            citations.extend(violated)

        max_audio_rms_dbfs = age_rules.get("max_audio_rms_dbfs")
        if max_audio_rms_dbfs is not None:
            levels = transcript_result.get("audio_levels", {})
            max_rms = levels.get("max_window_rms_dbfs")
            loud_audio = (
                levels.get("available")
                and isinstance(max_rms, (int, float))
                and max_rms > max_audio_rms_dbfs
            )
            automatic_checks.append(
                {
                    "check": "audio_loudness",
                    "status": "violation" if loud_audio else "passed",
                }
            )
            if loud_audio:
                all_violations.append(
                    {
                        "type": "audio",
                        "description": (
                            f"Audio exceeds the {age_silo} loudness limit of "
                            f"{max_audio_rms_dbfs:.0f} dBFS RMS."
                        ),
                        "suggestion": "Reduce the audio level and run analysis again.",
                        "policy_citations": [],
                    }
                )
        else:
            automatic_checks.append(
                {"check": "audio_loudness", "status": "not_applicable"}
            )

        # Deduplicate citations by id
        seen = set()
        unique_citations = []
        for c in citations:
            if c["id"] not in seen:
                unique_citations.append(c)
                seen.add(c["id"])

        if all_violations:
            compliance_status = "violation"
        elif not checks_complete:
            compliance_status = "insufficient_evidence"
        else:
            compliance_status = "passed"

        score_metrics = _content_safety_metrics(
            visual_result, audio_result, transcript_result, age_silo
        )
        verdict = "safe" if compliance_status == "passed" else "unsafe"

        return {
            "verdict": verdict,
            "compliance_status": compliance_status,
            "review_reasons": [],
            "automated_checks": automatic_checks,
            "age_silo": age_silo,
            "category": category,
            **score_metrics,
            "citations": unique_citations,
            "violations_summary": all_violations,
        }

    def match_video_metadata(
        self,
        title: str,
        description: str,
        category: str,
        age_silo: str,
        visual_result: Dict[str, Any],
        transcript_result: Dict[str, Any],
        safety_verdict: str,
    ) -> Dict[str, Any]:
        """Compare the supplied title/category with detected video evidence."""
        observations: List[Dict[str, str]] = []
        transcript = (transcript_result.get("text") or "").strip()
        if not transcript:
            transcript = " ".join(
                segment.get("text", "").strip()
                for segment in transcript_result.get("segments", [])
                if segment.get("text", "").strip()
            )
        if transcript:
            observations.append({"modality": "audio transcript", "text": transcript[:4000]})

        detected_objects = visual_result.get("detected_objects", [])
        visual_summary = ", ".join(
            f"{item['class']} ({item['count']} detections)"
            for item in detected_objects
            if item.get("class") and item.get("count", 0) > 0
        )
        if visual_summary:
            observations.append(
                {"modality": "visual objects", "text": visual_summary[:2000]}
            )

        age_labels = {
            "preschool": "Preschool (ages 0–4)",
            "kids": "Kids (ages 5–8)",
            "tweens": "Tweens (ages 9–12)",
            "teens": "Teens (ages 13–17)",
            "unrestricted": "Unrestricted (ages 18+)",
        }
        result: Dict[str, Any] = {
            "title": title,
            "category": category,
            "age_group": age_labels.get(age_silo, age_silo.title()),
            "age_group_status": (
                "passed" if safety_verdict == "safe" else "review_required"
            ),
            "status": "insufficient_evidence",
            "match_score": None,
            "title_score": None,
            "category_score": None,
            "evidence": [item["modality"] for item in observations],
        }
        if not observations:
            return result

        title_reference = f"{title}. {description}".strip()
        category_reference = (
            f"A {category} video about {title}. {description}".strip()
        )
        embeddings = self._ef(
            [title_reference, category_reference]
            + [item["text"] for item in observations]
        )

        def average_similarity(reference: List[float]) -> float:
            similarities = []
            for observation_embedding in embeddings[2:]:
                dot = sum(a * b for a, b in zip(reference, observation_embedding))
                ref_norm = math.sqrt(sum(value * value for value in reference))
                observation_norm = math.sqrt(
                    sum(value * value for value in observation_embedding)
                )
                if ref_norm and observation_norm:
                    similarities.append(float(dot / (ref_norm * observation_norm)))
            return sum(similarities) / len(similarities) if similarities else 0.0

        title_score = average_similarity(embeddings[0])
        category_score = average_similarity(embeddings[1])
        match_score = (title_score + category_score) / 2
        result.update(
            {
                "title_score": float(round(max(0.0, min(1.0, title_score)), 3)),
                "category_score": float(round(max(0.0, min(1.0, category_score)), 3)),
                "match_score": float(round(max(0.0, min(1.0, match_score)), 3)),
                "status": "match" if match_score >= 0.25 else "mismatch",
            }
        )
        return result


_policy_agent: PolicyRAGAgent | None = None
_policy_agent_lock = Lock()


def get_policy_agent() -> PolicyRAGAgent:
    """Return the process-wide RAG agent so its embedding model is loaded once."""
    global _policy_agent
    if _policy_agent is None:
        with _policy_agent_lock:
            if _policy_agent is None:
                _policy_agent = PolicyRAGAgent()
    return _policy_agent


def evaluate_age_group_content(
    video_data: Dict[str, Any],
    selected_age_groups: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Evaluate the same scan evidence independently against each selected tier."""
    if not isinstance(video_data, dict):
        raise ValueError("Video scan data must be an object.")
    if (
        not isinstance(selected_age_groups, list)
        or not 1 <= len(selected_age_groups) <= 3
        or any(
            not isinstance(group, str) or group not in AGE_GROUPS
            for group in selected_age_groups
        )
        or len(set(selected_age_groups)) != len(selected_age_groups)
    ):
        raise ValueError("Select one to three distinct supported age groups.")

    targets = video_data.get("age_group_targets", [])
    if not isinstance(targets, list):
        raise ValueError("Video age-group targets must be a list.")
    categories = {
        target["age_silo"]: target["category"]
        for target in targets
        if isinstance(target, dict)
        and target.get("age_silo") in AGE_GROUPS
        and isinstance(target.get("category"), str)
    }
    if len(categories) != len(selected_age_groups) or not all(
        group in categories for group in selected_age_groups
    ):
        raise ValueError("Each selected age group must have a valid category.")

    agent = get_policy_agent()
    evaluations: Dict[str, Dict[str, Any]] = {}
    for age_silo in selected_age_groups:
        category = categories[age_silo]
        result = agent.evaluate(
            video_data.get("visual", {}),
            video_data.get("audio", {}),
            video_data.get("transcript", {}),
            age_silo=age_silo,
            distance_threshold=video_data.get("distance_threshold", 0.75),
            category=category,
        )
        scan_score = result.get("overall_safe_pct")
        score_threshold = AGE_GROUP_SCAN_THRESHOLDS[age_silo]
        score_passed = (
            isinstance(scan_score, (int, float))
            and scan_score >= score_threshold
        )

        if scan_score is None:
            if result["compliance_status"] == "passed":
                result["compliance_status"] = "insufficient_evidence"
                result["verdict"] = "unsafe"
            score_check_status = "failed"
        elif not score_passed:
            suggestion = (
                f"Improve the video scan score to at least {score_threshold:.0f}% "
                f"for the {age_silo} tier."
            )
            result["violations_summary"].append(
                {
                    "type": "score",
                    "age_silo": age_silo,
                    "category": category,
                    "description": (
                        f"Content scan score {scan_score:.2f}% is below the "
                        f"{score_threshold:.0f}% {age_silo} threshold."
                    ),
                    "suggestion": suggestion,
                    "policy_citations": [],
                }
            )
            result["compliance_status"] = "violation"
            result["verdict"] = "unsafe"
            score_check_status = "violation"
        else:
            score_check_status = "passed"
        result["automated_checks"].append(
            {"check": "scan_score", "status": score_check_status}
        )

        if result["compliance_status"] == "passed" and score_passed:
            status = "Approved"
            feedback = f"Approved for the {age_silo} tier."
            remediation_suggestions = []
        elif result["compliance_status"] == "insufficient_evidence":
            status = "Review Required"
            feedback = (
                f"Automated checks are incomplete for the {age_silo} tier. "
                "Run the analysis again before publishing."
            )
            remediation_suggestions = [feedback]
        else:
            status = "Needs Remediation"
            remediation_suggestions = list(
                dict.fromkeys(
                    violation.get("suggestion") or violation.get("description", "")
                    for violation in result["violations_summary"]
                    if violation.get("suggestion") or violation.get("description")
                )
            )
            feedback = (
                f"Remediation is required for the {age_silo} tier."
                if remediation_suggestions
                else f"The {age_silo} tier did not meet its scan score threshold."
            )

        result.update(
            {
                "age_silo": age_silo,
                "category": category,
                "scan_score": scan_score,
                "score_threshold": score_threshold,
                "policy_thresholds": {
                    "minimum_scan_score": score_threshold,
                    "restricted_weapons": AGE_GROUP_POLICY_RULES[age_silo][
                        "restricted_weapons"
                    ],
                    "transcript_toxicity": AGE_GROUP_POLICY_RULES[age_silo][
                        "transcript_toxicity"
                    ],
                    "max_audio_rms_dbfs": AGE_GROUP_POLICY_RULES[age_silo][
                        "max_audio_rms_dbfs"
                    ],
                },
                "status": status,
                "approved": status == "Approved",
                "feedback": feedback,
                "remediation_suggestions": remediation_suggestions,
                "violations_detected": result["violations_summary"],
            }
        )
        evaluations[age_silo] = result

    return evaluations


def _fmt_ts(sec: float) -> str:
    m = int(sec // 60)
    s = int(sec % 60)
    return f"{m:02d}:{s:02d}"
