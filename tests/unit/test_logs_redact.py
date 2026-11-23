"""日志脱敏（AC-47）。

两条底线，缺一条这条通道就不成立：

1. 密钥不能出现在日志里——包括**异常堆栈**里的，那是最容易漏的一路。
2. 正常文本不能被误改——误改会让人不敢信日志，等于把排障工具变成噪声源。

## 测试数据为什么要拼出来

本模块自己会被 `tests/security/test_privacy_scan.py` 扫。若把密钥字面量整段写进
源码，扫描器会判定「仓库里混进了密钥」——它分辨不出这是测试数据还是真 key，
而**它不该去分辨**：一旦为测试目录开白名单，真 key 粘进来也就没人拦了。
所以假密钥一律在运行时拼出，源码里不出现完整形状。
"""

from __future__ import annotations

import io
import logging

import pytest

from novel_management.logs import RedactingFormatter, redact

# 运行时拼接：源码里看到的都是碎片，扫描器不会命中。
FAKE_SK = "sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9" + "." + "payload" + "." + "sig"
FAKE_BASIC = "dXNlcjpwYXNz"
FAKE_PASSWORD = "s3cr3t-value"
FAKE_CLIENT_SECRET = "cs-" + "9f8e7d6c5b4a"
FAKE_QUERY_TOKEN = "qt-" + "0123456789ab"
FAKE_DB_PASS = "hunter2"
FAKE_GH = "ghp" + "_" + "z9y8x7w6v5u4t3s2r1q0p9o8n7m6l5k4"
FAKE_AWS = "AKIA" + "Z7Y6X5W4V3U2T1S0"
FAKE_X_API_KEY = "xak-" + "1122334455"
FAKE_PEM_BODY = "MIIEowIBAAKCAQEA" + "fakebody"


@pytest.mark.parametrize(
    "raw,secret",
    [
        (f"api_key={FAKE_SK}", FAKE_SK),
        (f"Authorization: Bearer {FAKE_JWT}", FAKE_JWT),
        (f"Authorization: Basic {FAKE_BASIC}", FAKE_BASIC),
        (f'password: "{FAKE_PASSWORD}"', FAKE_PASSWORD),
        (f"client_secret={FAKE_CLIENT_SECRET}", FAKE_CLIENT_SECRET),
        (f"POST https://api.example.com?token={FAKE_QUERY_TOKEN}", FAKE_QUERY_TOKEN),
        (f"db url mysql://root:{FAKE_DB_PASS}@localhost/db", FAKE_DB_PASS),
        (f"GH token {FAKE_GH}", FAKE_GH),
        (f"AWS {FAKE_AWS}", FAKE_AWS),
        (f"x-api-key={FAKE_X_API_KEY}", FAKE_X_API_KEY),
    ],
)
def test_secret_is_removed(raw: str, secret: str) -> None:
    out = redact(raw)
    assert secret not in out, f"密钥没被脱掉：{out}"
    assert "<REDACTED>" in out


def test_bearer_does_not_leave_token_behind() -> None:
    """回归：早前版本先跑「键值对」规则，把 `Bearer` 这个词当值换掉，
    JWT 本体反而留在原地——看起来脱过了，比没脱更危险。"""
    out = redact(f"Authorization: Bearer {FAKE_JWT}")
    assert FAKE_JWT not in out


def test_basic_does_not_leave_credentials_behind() -> None:
    out = redact(f"Authorization: Basic {FAKE_BASIC}")
    assert FAKE_BASIC not in out


def test_pem_private_key_body_is_removed() -> None:
    """私钥是整段的。单行规则只换掉 `-----BEGIN`，密钥体仍在后几行。"""
    begin = "-----BEGIN " + "RSA PRIVATE KEY" + "-----"
    end = "-----END " + "RSA PRIVATE KEY" + "-----"
    pem = f'private_key: "{begin}\n{FAKE_PEM_BODY}\n{end}"'
    out = redact(pem)
    assert FAKE_PEM_BODY not in out


@pytest.mark.parametrize(
    "text",
    [
        "token 用量 1200，正常日志不该改",
        "本次发布 22 步，耗时 3.2s",
        "索引建了 1067 个产物、12867 个块",
        "error: 文件不存在 05_Ledgers/Events/EVT-0001.md",
    ],
)
def test_normal_text_is_untouched(text: str) -> None:
    assert redact(text) == text


def test_formatter_redacts_traceback() -> None:
    """脱敏挂在 formatter 上而不是调用点：异常堆栈里的密钥由框架渲染，
    调用点根本看不到，只有 formatter 这一层拦得住。"""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(RedactingFormatter("%(message)s"))
    logger = logging.getLogger("novel.test.redact")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    message = f"request failed: Authorization: Bearer {FAKE_JWT}; api_key={FAKE_SK}"
    try:
        raise RuntimeError(message)
    except RuntimeError:
        logger.exception("调用失败")

    text = buf.getvalue()
    assert FAKE_SK not in text
    assert FAKE_JWT not in text
    assert "<REDACTED>" in text


def test_empty_text_passes_through() -> None:
    assert redact("") == ""
