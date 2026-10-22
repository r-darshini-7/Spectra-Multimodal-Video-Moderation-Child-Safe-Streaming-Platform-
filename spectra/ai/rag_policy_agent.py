"""
Policy RAG Agent — ChromaDB vector store + sentence-transformers.
Retrieves relevant age-tiered safety policy clauses for multimodal observations.
"""
from __future__ import annotations

import math
import os
from threading import Lock
from typing import List, Dict, Any

from ..category_rules import CATEGORY_OPTIONS

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
            "zero tolerance for loud audio, combat, or conflict."
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
            "Platform Policy: If an automated model cannot verify an age-tier rule, "
            "mark the result insufficient evidence and require human review."
        ),
        "silo": "all",
    },
]


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
    audio_applies_to_age_silo = age_silo in {"kids", "tweens"}
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

        Unsupported visual/legal checks remain insufficient evidence rather than
        being inferred from generic object detections or semantic retrieval.

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
        review_reasons: List[str] = []
        allowed_categories = ALLOWED_CATEGORIES.get(age_silo)
        normalized_category = " ".join(category.casefold().split())

        if age_silo not in ALLOWED_CATEGORIES:
            review_reasons.append("The selected age tier is not recognized.")
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

        # COCO object detections cannot establish combat, conflict, horror, gore,
        # sexual content, or whether an object is being used as a weapon.
        if age_silo in {"preschool", "kids", "tweens", "teens"}:
            sampled_frames = visual_result.get("total_frames_sampled", 0)
            detected_classes = len(visual_result.get("detected_objects", []))
            review_reasons.append(
                f"Visual policy checks need human review ({sampled_frames} frames "
                f"sampled; {detected_classes} detected object classes): the current "
                "detector cannot verify this age tier's prohibited visual themes."
            )
        elif age_silo == "unrestricted":
            review_reasons.append(
                "Human review is required to verify that content is legal; "
                "the current models do not detect illegal material."
            )

        if age_silo == "preschool":
            review_reasons.append(
                "Preschool is whitelist-only, but no approved-content whitelist "
                "is configured."
            )
        elif age_silo in {"kids", "tweens"}:
            review_reasons.append(
                "The transcript model scores general toxicity; it cannot reliably "
                "verify every instance of profanity."
            )

        detected_audio_violations = audio_result.get("violations", [])
        applicable_audio_violations = (
            detected_audio_violations
            if age_silo in {"kids", "tweens"}
            else []
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

        if age_silo == "preschool":
            levels = transcript_result.get("audio_levels", {})
            if not levels.get("available"):
                review_reasons.append(
                    "Audio loudness could not be measured; preschool has zero "
                    "tolerance for loud audio."
                )
            else:
                peak = levels.get("peak_dbfs")
                max_rms = levels.get("max_window_rms_dbfs")
                levels_detail = (
                    f" Measured peak: {peak} dBFS; maximum 100 ms RMS: {max_rms} dBFS."
                    if peak is not None and max_rms is not None
                    else ""
                )
                review_reasons.append(
                    "Preschool audio loudness needs human review because the policy "
                    "does not define a numeric cutoff."
                    + levels_detail
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
        elif review_reasons:
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
            "review_reasons": review_reasons,
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


def _fmt_ts(sec: float) -> str:
    m = int(sec // 60)
    s = int(sec % 60)
    return f"{m:02d}:{s:02d}"
