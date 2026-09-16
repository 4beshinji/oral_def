"""Bounded local TTS worker. Input is private JSON on stdin, output is PCM WAV."""

import io
import json
import subprocess
import sys
import wave
from pathlib import Path

from .audio import ffmpeg_path


def generate(request):
    import onnxruntime as ort

    ort.disable_telemetry_events()
    preset = request["preset"]
    models = Path(request["models_dir"])
    buffer = io.BytesIO()
    if preset["engine"] == "piper":
        from piper import PiperVoice

        voice = PiperVoice.load(str(models / "en_US-ljspeech-high.onnx"))
        with wave.open(buffer, "wb") as wav:
            voice.synthesize_wav(request["text"], wav)
    else:
        import numpy as np
        from kokoro_onnx import Kokoro

        class KokoroSession(ort.InferenceSession):
            def run(self, output_names, input_feed, run_options=None):
                # kokoro-onnx 0.4.9 sends int32 speed for input_ids exports,
                # while the v1.1 release model declares speed as float32.
                dtypes = {
                    "tensor(float)": np.float32,
                    "tensor(int32)": np.int32,
                    "tensor(int64)": np.int64,
                }
                typed = {
                    item.name: np.asarray(input_feed[item.name], dtype=dtypes[item.type])
                    for item in self.get_inputs()
                }
                return super().run(output_names, typed, run_options)

        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        session = KokoroSession(
            str(models / "kokoro-v1.0.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        voice = Kokoro.from_session(session, str(models / "voices-v1.0.bin"))
        samples, rate = voice.create(
            request["text"], voice=preset["voice"], speed=1.0, lang="en-us"
        )
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


def main():
    request = json.load(sys.stdin)
    output = Path(sys.argv[1])
    data = generate(request)
    pitch = request["preset"]["pitch"]
    if pitch == 1:
        output.write_bytes(data)
    else:
        with wave.open(io.BytesIO(data)) as wav:
            rate = wav.getframerate()
        subprocess.run(
            [
                ffmpeg_path(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                "pipe:0",
                "-af",
                f"asetrate={rate * pitch},aresample={rate},atempo={1 / pitch}",
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            input=data,
            capture_output=True,
            timeout=30,
            check=True,
        )


if __name__ == "__main__":
    main()
