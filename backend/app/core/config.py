from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase
    database_url: str
    direct_url: str
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str

    # Cloudflare R2
    r2_endpoint: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket: str = "dayone-verify-docs"

    # App
    app_env: str = "staging"
    internal_api_secret: str


settings = Settings()
