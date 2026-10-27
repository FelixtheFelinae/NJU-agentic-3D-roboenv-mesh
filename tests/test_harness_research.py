from pathlib import Path

from PIL import Image

from roboenv_perception.contracts import VLMConfig
from roboenv_perception.harness import research
from roboenv_perception.harness.web import (
    SourceRecord,
    WebSearchPolicy,
    WebSearchResult,
)
from roboenv_perception.io import read_json, write_json


def _source(source_id: str, excerpt: str) -> SourceRecord:
    return SourceRecord(
        source_id=source_id, url=f"https://example.org/{source_id}", title="manual",
        discovery_snippet="", text_excerpt=excerpt, content_sha256="0" * 64,
        retrieved_at="2026-10-27T00:00:00+00:00", source_kind="other",
        applicability="model_mentioned", retrieval_status="fetched",
    )


def _evidence(tmp_path: Path) -> Path:
    root = tmp_path / "evidence"
    view = root / "objects/asset-0001/views/view-001"
    view.mkdir(parents=True)
    Image.new("RGB", (10, 10), "red").save(view / "frame.png")
    Image.new("RGB", (10, 10), "red").save(view / "context.png")
    write_json(root / "objects.json", {"objects": [{"asset_id": "asset-0001",
                                            "object_id": "object-1",
                                            "evidence_ref": "objects/asset-0001/evidence.json"}]})
    write_json(root / "objects/asset-0001/evidence.json", {
        "object_id": "object-1", "asset_id": "asset-0001", "category": "tablet",
        "status": "ready_for_design", "views": [{
            "ref_id": "view-001",
            "frame_ref": "objects/asset-0001/views/view-001/frame.png",
            "context_ref": "objects/asset-0001/views/view-001/context.png",
        }],
    })
    return root


def test_matched_page_only_enters_design_claim_candidates(tmp_path, monkeypatch):
    evidence = _evidence(tmp_path)
    source_a = _source("web:a", "Example Tab 3 has a red side switch and width 120 mm.")
    source_b = _source("web:b", "Example Tab 3 Plus has a black side switch and width 140 mm.")

    class Client:
        def __init__(self, policy):
            pass

        def search(self, identity, cache_dir):
            return WebSearchResult(identity, identity.query, "duckduckgo", "ok", (source_a, source_b))

    calls = []

    def fake_infer(vlm, stage, prompt, images, model, trace):
        calls.append((stage, prompt, images))
        if stage == "web-product-identity":
            return research.ProductGuess(manufacturer="Example", model="Tab 3", confidence="high",
                                         basis="visible model label")
        if stage == "web-image-source-match":
            assert len(images) == 2  # One original frame and one object crop.
            if '"source_id": "web:a"' in prompt:
                return research.SourceImageReview(
                    source_id="web:a", decision="match",
                    visible_evidence_refs=["view-001"],
                    visible_features=["red side switch"],
                    source_quote="red side switch", reason="same distinctive switch colour",
                )
            return research.SourceImageReview(
                source_id="web:b", decision="conflict",
                visible_evidence_refs=["view-001"],
                visible_features=["red side switch"],
                source_quote="black side switch", reason="switch colour disagrees",
            )
        assert stage == "web-claim-extraction"
        assert "width 120 mm" in prompt and "width 140 mm" not in prompt
        return research.ProposedClaims(claims=[
            research.ProposedClaim(key="width", value="120 mm", source_id="web:a", quote="width 120 mm"),
            research.ProposedClaim(key="width", value="140 mm", source_id="web:b", quote="width 140 mm"),
        ])

    monkeypatch.setattr(research, "WebEvidenceClient", Client)
    monkeypatch.setattr(research, "make_vlm", lambda *args, **kwargs: object())
    monkeypatch.setattr(research, "infer_model", fake_infer)
    out = tmp_path / "out"
    research.research_objects(evidence, out, VLMConfig(), WebSearchPolicy(), task="push tablet")
    record = read_json(out / "research.json")["objects"]["object-1"]
    assert record["status"] == "conflict"
    assert [review["decision"] for review in record["source_image_reviews"]] == ["match", "conflict"]
    assert [claim["status"] for claim in record["claims"]] == ["candidate", "unverified"]
    assert [stage for stage, *_ in calls].count("web-image-source-match") == 2
    assert '"task": "push tablet"' in calls[0][1]


def test_model_name_mention_without_visible_match_is_not_usable(tmp_path, monkeypatch):
    evidence = _evidence(tmp_path)
    source = _source("web:a", "Example Tab 3 width 120 mm.")

    class Client:
        def __init__(self, policy):
            pass

        def search(self, identity, cache_dir):
            return WebSearchResult(identity, identity.query, "duckduckgo", "ok", (source,))

    def fake_infer(vlm, stage, prompt, images, model, trace):
        if stage == "web-product-identity":
            return research.ProductGuess(model="Tab 3", confidence="medium", basis="similar shape")
        assert stage == "web-image-source-match"
        return research.SourceImageReview(source_id="web:a", decision="uncertain",
                                          reason="model variant is hidden from all visible views")

    monkeypatch.setattr(research, "WebEvidenceClient", Client)
    monkeypatch.setattr(research, "make_vlm", lambda *args, **kwargs: object())
    monkeypatch.setattr(research, "infer_model", fake_infer)
    out = tmp_path / "out"
    research.research_objects(evidence, out, VLMConfig(), WebSearchPolicy())
    record = read_json(out / "research.json")["objects"]["object-1"]
    assert record["status"] == "unverified_applicability"
    assert record["claims"] == []
    assert len(record["sources"]) == 1
    assert record["source_image_reviews"][0]["reason"].startswith("model variant")


def test_invalid_source_quote_falls_back_to_uncertain(tmp_path, monkeypatch):
    evidence = _evidence(tmp_path)
    source = _source("web:a", "Example Tab 3 width 120 mm.")

    class Client:
        def __init__(self, policy):
            pass

        def search(self, identity, cache_dir):
            return WebSearchResult(identity, identity.query, "duckduckgo", "ok", (source,))

    def fake_infer(vlm, stage, prompt, images, model, trace):
        if stage == "web-product-identity":
            return research.ProductGuess(model="Tab 3", confidence="high", basis="logo")
        return research.SourceImageReview(source_id="web:a", decision="match",
                                          visible_evidence_refs=["view-001"],
                                          visible_features=["red switch"],
                                          source_quote="red switch", reason="matches")

    monkeypatch.setattr(research, "WebEvidenceClient", Client)
    monkeypatch.setattr(research, "make_vlm", lambda *args, **kwargs: object())
    monkeypatch.setattr(research, "infer_model", fake_infer)
    out = tmp_path / "out"
    research.research_objects(evidence, out, VLMConfig(), WebSearchPolicy())
    record = read_json(out / "research.json")["objects"]["object-1"]
    assert record["status"] == "unverified_applicability"
    assert record["source_image_reviews"][0]["decision"] == "uncertain"
    assert "quote is absent" in record["source_image_reviews"][0]["reason"]
    assert record["claims"] == []


def test_product_name_quote_alone_cannot_establish_image_match(tmp_path, monkeypatch):
    evidence = _evidence(tmp_path)
    source = _source("web:a", "Example Tab 3 width 120 mm.")

    class Client:
        def __init__(self, policy):
            pass

        def search(self, identity, cache_dir):
            return WebSearchResult(identity, identity.query, "duckduckgo", "ok", (source,))

    def fake_infer(vlm, stage, prompt, images, model, trace):
        if stage == "web-product-identity":
            return research.ProductGuess(manufacturer="Example", model="Tab 3",
                                         confidence="high", basis="looks similar")
        return research.SourceImageReview(source_id="web:a", decision="match",
                                          visible_evidence_refs=["view-001"],
                                          visible_features=["Tab 3"],
                                          source_quote="Example Tab 3", reason="same name")

    monkeypatch.setattr(research, "WebEvidenceClient", Client)
    monkeypatch.setattr(research, "make_vlm", lambda *args, **kwargs: object())
    monkeypatch.setattr(research, "infer_model", fake_infer)
    out = tmp_path / "out"
    research.research_objects(evidence, out, VLMConfig(), WebSearchPolicy())
    record = read_json(out / "research.json")["objects"]["object-1"]
    assert record["status"] == "unverified_applicability"
    assert "product name/category" in record["source_image_reviews"][0]["reason"]


def test_blank_visible_feature_cannot_establish_match():
    import pytest
    with pytest.raises(ValueError, match='visible feature'):
        research.SourceImageReview(source_id='web:a', decision='match',
                                   visible_evidence_refs=['view-001'], visible_features=['  '],
                                   source_quote='Tab 3 red side switch', reason='same')
