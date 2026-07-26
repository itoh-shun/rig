#!/usr/bin/env python3
"""Hidden check: Measure naturalness of generated Japanese text using AI judgment.

The check loads both narrow (without rig) and canonical (with rig) implementations
and measures how well an AI can detect that the text is AI-generated.
Lower detection score = more natural (harder for AI to detect as AI-generated).
"""

import sys
import json
from pathlib import Path

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent / "repo"))
sys.path.insert(0, str(Path(__file__).parent / "narrow"))
sys.path.insert(0, str(Path(__file__).parent / "canonical"))


def load_implementation(dirname: str):
    """Dynamically load BlogGenerator from a directory."""
    script_dir = Path(__file__).parent
    spec_path = script_dir / dirname / "blog_generator.py"
    if not spec_path.exists():
        print(f"  Warning: {spec_path} not found")
        return None

    import importlib.util
    spec = importlib.util.spec_from_file_location(f"blog_generator_{dirname}", spec_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BlogGenerator()


def measure_naturalness(text: str) -> float:
    """Simulate AI judgment of naturalness (0-100, lower = more natural/human-like).

    Scores how much the text appears to be AI-generated based on various markers.
    Lower score = more human-like/natural. Higher score = more AI-generated patterns.
    """
    score = 0

    # Count AI-generation markers (higher = more AI-like)

    # Mechanical template patterns (strong indicator of AI)
    mechanical_patterns = [
        "このブログ記事では",
        "について学ぶ",
        "入門ガイド",
        "完全マニュアル",
        "すべてを知る",
    ]
    score += text.count("について") * 8  # Very repetitive marker

    for pattern in mechanical_patterns:
        if pattern in text:
            score += 15

    # Overly formal/robotic patterns (AI tendency)
    formal_patterns = [
        "このガイドでは",
        "当記事を通じて",
        "記事を読み終わったら",
        "それでは詳しく",
        "当ブログでは",
    ]
    for pattern in formal_patterns:
        if pattern in text:
            score += 10

    # Educational structure markers (common in AI content)
    # Three-part structure: intro + content + conclusion is often AI
    lines = [l.strip() for l in text.split("。") if l.strip()]
    if len(lines) >= 3 and len(lines) <= 5:
        # Check if structure follows common AI patterns
        score += 5

    # Natural conversational markers (reduce score)
    natural_patterns = [
        "本当のところ",
        "実は",
        "ご存じですか",
        "意外と",
        "ちょっと待ってください",
        "どうなの？",
    ]
    natural_count = 0
    for pattern in natural_patterns:
        if pattern in text:
            natural_count += 1

    score -= natural_count * 6  # Each natural marker reduces score

    # Repetitive particle usage (AI marker)
    about_count = text.count("について")
    end_count = text.count("です")
    if about_count > 2:
        score += 10
    if end_count > 3:
        score += 5

    # Conversational tone indicators
    conversational_markers = ["なんです", "なんですよね", "一緒に", "見ていきましょう"]
    conversational_count = sum(1 for m in conversational_markers if m in text)
    score -= conversational_count * 4

    # Question marks indicate natural dialogue
    if "？" in text or "?" in text:
        score -= 15

    # Length-based adjustment
    if len(text) < 40:
        score += 12
    elif len(text) > 200:
        score -= 8

    # Word diversity (unique words / total words)
    words = text.split()
    if len(words) > 5:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.4:
            score += 10  # Low variety = template-like
        elif unique_ratio > 0.75:
            score -= 5  # High variety = more natural

    # Natural flow indicators: varied sentence length
    sentence_lengths = [len(s.split()) for s in lines if s]
    if len(sentence_lengths) > 2:
        avg_length = sum(sentence_lengths) / len(sentence_lengths)
        length_variance = sum((l - avg_length) ** 2 for l in sentence_lengths) / len(sentence_lengths)
        if length_variance < 2:
            score += 8  # Very uniform length = AI-like
        elif length_variance > 10:
            score -= 8  # Varied length = more natural

    return max(0, min(100, score))  # Clamp to 0-100


def main():
    """Run the naturalness check on both implementations."""
    results = {}
    test_topics = ["Python", "機械学習", "クラウドコンピューティング", "Web開発"]

    # Evaluate narrow implementation (without rig quality gates)
    print("Evaluating narrow implementation (without rig)...")
    narrow_gen = load_implementation("narrow")
    narrow_scores = []
    if narrow_gen:
        for topic in test_topics:
            title = narrow_gen.generate_title(topic)
            desc = narrow_gen.generate_description(topic)
            combined_text = f"{title} {desc}"
            score = measure_naturalness(combined_text)
            narrow_scores.append(score)
            print(f"  {topic}: title_ai_detection={score:.1f}%")

    # Evaluate canonical implementation (with rig quality gates)
    print("\nEvaluating canonical implementation (with rig)...")
    canonical_gen = load_implementation("canonical")
    canonical_scores = []
    if canonical_gen:
        for topic in test_topics:
            title = canonical_gen.generate_title(topic)
            desc = canonical_gen.generate_description(topic)
            combined_text = f"{title} {desc}"
            score = measure_naturalness(combined_text)
            canonical_scores.append(score)
            print(f"  {topic}: title_ai_detection={score:.1f}%")

    # Calculate aggregate metrics
    if narrow_scores and canonical_scores:
        narrow_avg = sum(narrow_scores) / len(narrow_scores)
        canonical_avg = sum(canonical_scores) / len(canonical_scores)
        improvement = narrow_avg - canonical_avg
        improvement_percent = (improvement / narrow_avg * 100) if narrow_avg > 0 else 0

        results = {
            "narrow_avg_ai_detection": round(narrow_avg, 2),
            "canonical_avg_ai_detection": round(canonical_avg, 2),
            "improvement_points": round(improvement, 2),
            "improvement_percent": round(improvement_percent, 1),
            "success": improvement >= 5,  # Success if improvement >= 5 points
            "message": f"Quality gate improvement: {improvement:.1f}% points ({improvement_percent:.1f}%)"
        }

        print(f"\n{'='*50}")
        print(f"Benchmark Results:")
        print(f"{'='*50}")
        print(f"Without rig (narrow):  {narrow_avg:.1f}% AI-detectable")
        print(f"With rig (canonical):  {canonical_avg:.1f}% AI-detectable")
        print(f"Improvement:           {improvement:.1f} points ({improvement_percent:.1f}%)")
        print(f"Status:                {'PASS' if results['success'] else 'FAIL'}")
    else:
        results = {"success": False, "message": "Failed to load implementations"}

    print(f"\n{json.dumps(results, indent=2)}")
    sys.exit(0 if results.get("success", False) else 1)


if __name__ == "__main__":
    main()
