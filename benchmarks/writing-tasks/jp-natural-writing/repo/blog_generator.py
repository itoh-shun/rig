"""Japanese blog article generator."""


class BlogGenerator:
    """Generate blog articles with Japanese titles and descriptions."""

    def generate_title(self, topic: str) -> str:
        """Generate a blog title for the given topic in Japanese."""
        raise NotImplementedError("Subclass must implement generate_title")

    def generate_description(self, topic: str) -> str:
        """Generate a blog description for the given topic in Japanese."""
        raise NotImplementedError("Subclass must implement generate_description")
