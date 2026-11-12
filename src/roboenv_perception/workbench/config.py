"""Studio deployment settings; native DSH alone owns model credentials and chat."""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dsh_root: Path
    model_config_path: Path | None = None
    model: str | None = None
    state_root: Path = Path("outputs/studio")
    import_root: Path | None = Path("outputs")
    port: int = Field(default=8890, ge=1024, le=65535)
    perception_config: Path | None = None
    construction_config: Path | None = None
    artisan_python: Path | None = None
    usd_python: Path | None = None
    blender: Path | None = None
    isaac_python: Path | None = None
    env_file: Path | None = None
    max_concurrent: int = Field(default=1, ge=1, le=4)
    max_revisions: int = Field(default=3, ge=1, le=8)
    max_wall_seconds: int = Field(default=7200, ge=60, le=86400)
    debug_component_tools: bool = False
    physics: bool = True
    web_search: bool = True
    background_mode: Literal["off", "masks", "auto"] = "auto"

    @property
    def usd_executable(self):
        return self.usd_python or self.artisan_python

    @classmethod
    def load(cls, path):
        config = cls.model_validate(tomllib.loads(path.read_text()))
        for name in cls.model_fields:
            value = getattr(config, name)
            if isinstance(value, Path):
                setattr(config, name, value.expanduser().absolute())
        return config

    def generation_missing(self):
        required = ["perception_config", "construction_config", "usd_executable"]
        if self.physics:
            required.append("isaac_python")
        required.extend(name for name in ("blender", "env_file") if getattr(self, name))
        return [
            "usd_python" if name == "usd_executable" else name
            for name in required
            if not getattr(self, name) or not getattr(self, name).is_file()
        ]
