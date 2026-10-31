"""生成 API Key 加密主密钥。

用法：
    python scripts/gen_encryption_key.py

把输出的那行写进 backend/.env：
    API_KEY_ENCRYPTION_KEY="xxxx"

注意：这个密钥一旦更换，库里已加密的 API Key 就再也解不开了，只能重新录入。
"""

from cryptography.fernet import Fernet


def main() -> None:
    key = Fernet.generate_key().decode()
    print("把下面这行加到 backend/.env：\n")
    print(f'API_KEY_ENCRYPTION_KEY="{key}"')
    print(
        "\n注意：更换这个密钥会导致库里已加密的 API Key 无法解密，届时需要重新录入所有供应商配置。"
    )


if __name__ == "__main__":
    main()
