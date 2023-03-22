from datetime import datetime
from pydantic import Field, BaseModel


class TimestampableMixin(BaseModel):
    created_at: datetime = Field(default_factory=datetime.utcnow, alias="_created_at")
    modified_at: datetime = Field(default_factory=datetime.utcnow, alias="_modified_at")

    def save(self):
        self.modified_at = datetime.utcnow()
        return super().save()
