"""Typed settings. Every value has a default for this Mac and an env override."""

import getpass
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    pghost: str = os.environ.get("VALOR_PGHOST", "/tmp")
    pgport: int = int(os.environ.get("VALOR_PGPORT", "5432"))
    database: str = os.environ.get("VALOR_DB", "valor_rebuild")
    kernel_role: str = "valor_kernel"
    owner_role: str = os.environ.get("VALOR_PG_OWNER", getpass.getuser())
    upstream: str = os.environ.get("VALOR_UPSTREAM", "https://api.anthropic.com")

    def dsn(self, *, owner: bool = False, database: str | None = None) -> str:
        role = self.owner_role if owner else self.kernel_role
        return f"host={self.pghost} port={self.pgport} dbname={database or self.database} user={role}"


settings = Settings()
