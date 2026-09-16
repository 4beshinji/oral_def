import io
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import imageio_ffmpeg

from .errors import APIError

MAX_UPLOAD = 10 * 1024 * 1024


def ffmpeg_path():
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


def convert(source: Path, target: Path):
    try:
        result = subprocess.run(
            [
                ffmpeg_path(),
                "-nostdin",
                "-hide_banner",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-t",
                "31",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(target),
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
        with wave.open(str(target)) as audio:
            duration = audio.getnframes() / audio.getframerate()
        if not 0 < duration <= 30:
            raise APIError(422, "audio_duration", "録音は0秒より長く30秒以下にしてください。")
        info = result.stderr.decode(errors="replace")
        source_info = re.search(r"Audio:.*?,\s*(\d+) Hz,\s*([^,\n]+)", info)
        return {
            "duration_s": duration,
            "sample_rate_hz": 16000,
            "channels": 1,
            "original_sample_rate_hz": int(source_info[1]) if source_info else None,
            "original_channel_layout": source_info[2] if source_info else None,
            "conversion": "ffmpeg pcm_s16le mono 16000 Hz",
            "trim_offset_s": 0,
        }
    except subprocess.TimeoutExpired:
        raise APIError(504, "audio_timeout", "音声変換が時間切れになりました。", True)
    except (subprocess.CalledProcessError, wave.Error, EOFError, OSError):
        raise APIError(
            422, "invalid_audio", "音声を読み取れません。対応する録音ファイルを使用してください。"
        )


def inspect_upload(path):
    with tempfile.TemporaryDirectory(prefix="oral-audio-") as work:
        return convert(path, Path(work) / "converted.wav")


def validate_tts(data):
    try:
        with wave.open(io.BytesIO(data)) as wav:
            if wav.getnframes() <= 0 or wav.getframerate() <= 0:
                raise wave.Error("empty")
            expected_bytes = wav.getnframes() * wav.getnchannels() * wav.getsampwidth()
            if len(wav.readframes(wav.getnframes())) != expected_bytes:
                raise wave.Error("truncated")
    except (wave.Error, EOFError):
        raise APIError(502, "invalid_tts_audio", "TTS APIが有効なPCM WAVを返しませんでした。", True)
