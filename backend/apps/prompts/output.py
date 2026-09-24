"""User templates can vary their result fields without introducing arbitrary result types."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator

_RESERVED_FIELDS = {"summary", "model_config"}


class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fields: dict[str, str] = Field(min_length=1, max_length=15)

    @field_validator("fields")
    @classmethod
    def _valid_names(cls, fields: dict[str, str]) -> dict[str, str]:
        for name in fields:
            if not name.isidentifier() or not name.islower() or len(name) > 40:
                raise ValueError(f"invalid field name {name!r}: use lowercase snake_case")
            if name in _RESERVED_FIELDS or name.startswith("_"):
                raise ValueError(f"field name {name!r} is reserved")
        return fields


def build_output_model(spec: OutputSpec) -> type[BaseModel]:
    field_definitions: dict[str, Any] = {
        "summary": (str, Field(min_length=1, max_length=4000)),
    }
    for name, description in spec.fields.items():
        field_definitions[name] = (
            list[str],
            Field(default_factory=list, max_length=50, description=description),
        )
    model: type[BaseModel] = create_model(
        "TemplateAnalysis",
        __config__=ConfigDict(extra="ignore"),
        **field_definitions,
    )
    return model
