"""Pytest 全局配置：在导入 app 之前设定统一的测试环境。

conftest.py 会先于所有测试模块加载，确保 settings 只按这一份配置初始化，
避免多个测试文件各自设置环境变量导致的冲突。
"""
import os
import tempfile

_DB = os.path.join(tempfile.gettempdir(), "relay_pytest_all.db")
if os.path.exists(_DB):
    try:
        os.remove(_DB)
    except OSError:
        pass

os.environ["DATABASE_URL"] = "sqlite:///" + _DB.replace("\\", "/")
os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET"] = "test-secret-shared-long-enough-string-1234"
os.environ["ENCRYPTION_SECRET"] = "test-enc-secret-shared-string"
os.environ["RUN_INPROCESS_WORKER"] = "false"   # 测试中手动 drain，确定性更强
os.environ["ALERT_ERROR_THRESHOLD"] = "3"
