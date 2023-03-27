from systems.agent.steve.documents.users import User

active_document_models = [
    User,
]

__all__ = [cls.__name__ for cls in active_document_models]
