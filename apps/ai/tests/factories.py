"""Factory classes for AI app models."""

import factory
from factory.django import DjangoModelFactory

from apps.ai.models import ChatFeedback, ChatMessage, ChatSession
from apps.common.tests.factories import UserFactory


class ChatSessionFactory(DjangoModelFactory):
    """Factory for creating ChatSession instances."""

    class Meta:
        model = ChatSession

    user = factory.SubFactory(UserFactory)
    title = factory.Faker("sentence", nb_words=4)
    is_active = True
    metadata = factory.Dict(
        {
            "client": factory.Faker("user_agent"),
            "ip_address": factory.Faker("ipv4"),
        }
    )


class ChatMessageFactory(DjangoModelFactory):
    """Factory for creating ChatMessage instances."""

    class Meta:
        model = ChatMessage

    session = factory.SubFactory(ChatSessionFactory)
    role = factory.Iterator(["user", "assistant"])
    content = factory.Faker("paragraph", nb_sentences=3)
    is_processed = True
    metadata = factory.Dict(
        {
            "tokens": factory.Faker("random_int", min=10, max=500),
            "model": "gpt-4o-mini",
        }
    )


class ChatFeedbackFactory(DjangoModelFactory):
    """Factory for creating ChatFeedback instances."""

    class Meta:
        model = ChatFeedback

    message = factory.SubFactory(ChatMessageFactory)
    user = factory.SubFactory(UserFactory)
    rating = factory.Faker("random_int", min=1, max=5)
    comment = factory.Faker("text", max_nb_chars=200)
    is_helpful = factory.Faker("boolean")


class AnonymousChatSessionFactory(ChatSessionFactory):
    """Factory for creating anonymous ChatSession instances."""

    user = None
    metadata = factory.Dict(
        {
            "anonymous": True,
            "client": factory.Faker("user_agent"),
            "ip_address": factory.Faker("ipv4"),
        }
    )


class UserMessageFactory(ChatMessageFactory):
    """Factory for creating user messages."""

    role = "user"
    content = factory.Faker("sentence", nb_words=10)


class AssistantMessageFactory(ChatMessageFactory):
    """Factory for creating assistant messages."""

    role = "assistant"
    content = factory.Faker("paragraph", nb_sentences=5)
    metadata = factory.Dict(
        {
            "model": "gpt-4o-mini",
            "tokens": factory.Faker("random_int", min=50, max=500),
            "response_time_ms": factory.Faker("random_int", min=100, max=2000),
        }
    )


class SystemMessageFactory(ChatMessageFactory):
    """Factory for creating system messages."""

    role = "system"
    content = factory.Faker("sentence", nb_words=15)
    metadata = factory.Dict(
        {
            "type": "system_notification",
        }
    )
