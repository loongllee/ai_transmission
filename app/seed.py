"""初始化数据库：建表 + 初始管理员 + 默认 mock 模型与 Key（开箱即用）。"""
from .config import settings
from .database import Base, SessionLocal, engine
from .models import ApiKeyPool, Model, User, WalletAccount
from .security import encrypt_secret, hash_password


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        _seed_admin(db)
        _seed_mock_models_and_key(db)
        db.commit()
    finally:
        db.close()


def _seed_admin(db) -> None:
    admin = db.query(User).filter(User.username == settings.admin_username).first()
    if admin:
        return
    admin = User(
        username=settings.admin_username,
        email=settings.admin_email,
        password_hash=hash_password(settings.admin_password),
        role="admin",
        status="active",
    )
    db.add(admin)
    db.flush()
    db.add(
        WalletAccount(
            user_id=admin.id,
            free_points=100000,
            subsidy_points=100000,
            project_points=100000,
        )
    )


def _seed_mock_models_and_key(db) -> None:
    """默认接入内置 mock 供应商，三个等级各一个模型，离线可用。

    扣点倍率遵循方案 10.3：基础 1x / 标准 3x / 高级 10x。
    """
    if db.query(Model).count() == 0:
        db.add_all(
            [
                Model(
                    provider="mock",
                    model_name="mock-basic",
                    model_level="basic",
                    display_name="基础模型",
                    context_length=8192,
                    multiplier=1,
                    capability_tags="chat,translate,summary",
                ),
                Model(
                    provider="mock",
                    model_name="mock-standard",
                    model_level="standard",
                    display_name="标准模型",
                    context_length=32768,
                    multiplier=3,
                    capability_tags="chat,code,research",
                ),
                Model(
                    provider="mock",
                    model_name="mock-advanced",
                    model_level="advanced",
                    display_name="高级模型",
                    context_length=131072,
                    multiplier=10,
                    capability_tags="chat,code,reasoning,long-context",
                ),
            ]
        )

    if db.query(ApiKeyPool).filter(ApiKeyPool.provider == "mock").count() == 0:
        db.add(
            ApiKeyPool(
                resource_pool_type="school",
                provider="mock",
                account_name="内置 Mock 资源池",
                base_url=None,
                encrypted_api_key=encrypt_secret("mock-no-key-needed"),
                supported_models=None,  # 通配
                status="active",
                priority=0,
            )
        )


if __name__ == "__main__":
    init_db()
    print("数据库初始化完成。")
