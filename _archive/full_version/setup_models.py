"""
一次性下载 faster-whisper tiny 模型到项目本地。

运行一次后，video_analyzer.py 将直接使用本地模型，不再触发网络下载。
国内网络下建议先设置镜像：set HF_ENDPOINT=https://hf-mirror.com

用法：
    python setup_models.py
"""

import os
import sys
from pathlib import Path

MODEL_DIR = Path(__file__).parent / "models" / "faster-whisper-tiny"
REPO_ID = "Systran/faster-whisper-tiny"


def download_via_huggingface_hub():
    """使用 huggingface_hub 下载 CTranslate2 模型文件."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("❌ 需要安装 huggingface_hub: pip install huggingface-hub")
        return False

    # 支持国内镜像
    endpoint = os.environ.get("HF_ENDPOINT", "")
    if endpoint:
        print(f"🌐 使用 HF 镜像: {endpoint}")

    print(f"📥 正在下载 {REPO_ID} ...")
    print(f"   保存到: {MODEL_DIR}")
    print()

    try:
        snapshot_download(
            repo_id=REPO_ID,
            local_dir=str(MODEL_DIR),
            local_dir_use_symlinks=False,
            resume_download=True,
            max_workers=4,
        )
        return True
    except Exception as e:
        print(f"❌ 下载失败: {e}")
        return False


def verify_model():
    """验证模型文件完整."""
    required = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]
    missing = [f for f in required if not (MODEL_DIR / f).exists()]
    if missing:
        print(f"❌ 模型文件不完整，缺少: {missing}")
        return False

    # 尝试加载验证
    try:
        from faster_whisper import WhisperModel
        _ = WhisperModel(str(MODEL_DIR), device="cpu", compute_type="int8",
                        local_files_only=True)
        print(f"✅ 模型验证成功: {MODEL_DIR}")
        return True
    except Exception as e:
        print(f"⚠️  模型文件存在但加载失败: {e}")
        return False


def main():
    print("=" * 50)
    print("  faster-whisper 模型本地化安装")
    print("=" * 50)
    print()
    print("💡 提示：国内网络建议先设置 HF 镜像：")
    print('   set HF_ENDPOINT=https://hf-mirror.com')
    print()

    # 如果已存在，先验证
    if MODEL_DIR.exists() and any(MODEL_DIR.iterdir()):
        print("📂 模型目录已存在，验证中...")
        if verify_model():
            print()
            print("✅ 模型已就绪，无需重复下载。")
            return 0
        else:
            print("   模型不完整，将重新下载...")
            import shutil
            shutil.rmtree(MODEL_DIR, ignore_errors=True)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    if not download_via_huggingface_hub():
        print()
        print("=" * 50)
        print("  手动下载方法")
        print("=" * 50)
        print()
        print("1. 浏览器打开: https://hf-mirror.com/Systran/faster-whisper-tiny/tree/main")
        print("   或: https://huggingface.co/Systran/faster-whisper-tiny/tree/main")
        print()
        print("2. 下载以下文件到 models/faster-whisper-tiny/:")
        print("   - config.json")
        print("   - model.bin")
        print("   - tokenizer.json")
        print("   - preprocessor_config.json")
        print()
        print("3. 重新运行: python setup_models.py")
        print()
        return 1

    if not verify_model():
        return 1

    print()
    print("✅ faster-whisper 模型安装完成！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
