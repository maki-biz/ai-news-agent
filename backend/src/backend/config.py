"""backend/を作業ディレクトリとして環境設定を読み込む。"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """環境変数と作業ディレクトリの.envから読み込むサーバー設定。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6-luna"
    search_max_tool_calls: int = Field(default=1, ge=1, le=3)
    nvd_api_key: SecretStr | None = None
    api_timeout_seconds: float = Field(default=60, gt=0, le=180)
