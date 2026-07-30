"""Tests for Japanese blog generator."""

import pytest
from blog_generator import BlogGenerator


class SimpleBlogGenerator(BlogGenerator):
    """Simple implementation for testing."""

    def generate_title(self, topic: str) -> str:
        return f"{topic}についての記事"

    def generate_description(self, topic: str) -> str:
        return f"これは{topic}についての説明です。"


def test_generate_title():
    """Test that generate_title returns a non-empty string."""
    gen = SimpleBlogGenerator()
    result = gen.generate_title("Python")
    assert isinstance(result, str)
    assert len(result) > 0


def test_generate_description():
    """Test that generate_description returns a non-empty string."""
    gen = SimpleBlogGenerator()
    result = gen.generate_description("Python")
    assert isinstance(result, str)
    assert len(result) > 0
