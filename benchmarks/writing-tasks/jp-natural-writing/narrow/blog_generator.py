"""Japanese blog article generator - narrow implementation (without quality gates)."""


class BlogGenerator:
    """Generate blog articles with Japanese titles and descriptions.

    This implementation uses simple template-based generation without any
    quality checks or human review, resulting in more mechanical output.
    """

    def generate_title(self, topic: str) -> str:
        """Generate a simple blog title (template-based, mechanical tone)."""
        templates = [
            "{topic}のすべてを知る",
            "{topic}について学ぶ",
            "{topic}入門ガイド",
            "{topic}の完全マニュアル",
            "{topic}について",
        ]
        template = templates[hash(topic) % len(templates)]
        return template.format(topic=topic)

    def generate_description(self, topic: str) -> str:
        """Generate mechanical blog description without review."""
        parts = [
            f"このブログ記事では、{topic}についての詳しい情報を提供します。",
            f"{topic}に関する重要なポイントを学んでください。",
            f"{topic}について知っておくべき知識をすべてカバーしています。",
            f"当ブログでは、{topic}についての最新情報をお届けします。",
        ]
        return "".join(parts)
