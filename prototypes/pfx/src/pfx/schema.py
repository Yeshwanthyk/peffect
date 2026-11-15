"""Pydantic is pfx's Schema: errors are models, boundaries decode, config is a Layer.

class UserNotFound(TaggedError):        # a frozen BaseModel with a `_tag` discriminator
    user_id: str

user = yield from decode(User, payload) # Effect[User, DecodeError, Never]

class Config(SettingsTag[AppSettings]): ...    # pydantic-settings as a service
ConfigLive = Config.from_env(AppSettings)        # Layer[Config, ConfigError, Never]
"""

from __future__ import annotations

from typing import Any, ClassVar, Never, Self

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from pydantic_settings import BaseSettings

from pfx.core import Effect, attempt, catch_tag, fail, succeed
from pfx.layer import Layer, Tag


class TaggedError(BaseModel):
    """Typed, serializable error value (Effect's Data.TaggedError).

    Not an Exception: failures travel in E, not through raise. `tag` is the class name, so
    errors round-trip through JSON and work as a discriminated union in FastAPI responses.
    """

    model_config = ConfigDict(frozen=True)
    tag: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.tag = cls.__name__

    def to_json_dict(self) -> dict[str, Any]:
        return {"_tag": self.tag, **self.model_dump(mode="json")}


class DecodeError(TaggedError):
    target: str
    errors: list[dict[str, Any]]


def decode[M](model: type[M], data: object) -> Effect[M, DecodeError, Never]:
    """Validate untrusted input at the boundary: ValidationError becomes a typed failure."""
    try:
        return succeed(TypeAdapter(model).validate_python(data))
    except ValidationError as e:
        errs: list[dict[str, Any]] = [dict(err) for err in e.errors(include_url=False)]
        return fail(DecodeError(target=getattr(model, "__name__", str(model)), errors=errs))


class ConfigError(TaggedError):
    message: str


class SettingsTag[S: BaseSettings](Tag[S]):
    """A Tag whose service is a pydantic-settings object.

    class Config(SettingsTag[AppSettings]): ...
    ConfigLive = Config.from_env(AppSettings)   # Layer[Config, ConfigError, Never]
    ConfigTest = Config.succeed(AppSettings(db_url="sqlite://"))
    """

    @classmethod
    def from_env(cls, settings: type[S]) -> Layer[Self, ConfigError, Never]:
        load = attempt(settings, ValidationError).pipe(
            catch_tag(ValidationError, lambda e: fail(ConfigError(message=str(e))))
        )
        return cls.effect(load)
