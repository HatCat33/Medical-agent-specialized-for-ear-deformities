"""Download the Chinese Huayan ONNX voice and its Chinese phonemizer for CPU inference."""

import hashlib
from pathlib import Path

import httpx

REPOSITORY = "csukuangfj/vits-piper-zh_CN-huayan-medium"
DESTINATION = Path(__file__).resolve().parents[1] / "data" / "tts" / "huayan"


def main() -> None:
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        response = client.get(f"https://huggingface.co/api/models/{REPOSITORY}?blobs=true")
        response.raise_for_status()
        metadata = response.json()
        revision = metadata["sha"]
        files = [item for item in metadata["siblings"] if item["rfilename"] in {
            "zh_CN-huayan-medium.onnx", "zh_CN-huayan-medium.onnx.json", "tokens.txt", "MODEL_CARD",
            "espeak-ng-data/cmn_dict", "espeak-ng-data/en_dict",
            "espeak-ng-data/intonations", "espeak-ng-data/phondata", "espeak-ng-data/phonindex",
            "espeak-ng-data/phontab", "espeak-ng-data/lang/sit/cmn",
            "espeak-ng-data/lang/sit/cmn-Latn-pinyin", "espeak-ng-data/lang/gmw/en",
        }]
        DESTINATION.mkdir(parents=True, exist_ok=True)
        for item in files:
            name = item["rfilename"]
            target = (DESTINATION / name).resolve()
            if not target.is_relative_to(DESTINATION):
                raise ValueError("invalid_model_path")
            expected = item.get("lfs", {}).get("sha256")
            if (target.exists() and target.stat().st_size == item["size"]
                    and (not expected or hashlib.sha256(target.read_bytes()).hexdigest() == expected)):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".part")
            print(f"Downloading {name} ({item['size'] / 1048576:.1f} MB)", flush=True)
            digest = hashlib.sha256()
            with client.stream("GET", f"https://huggingface.co/{REPOSITORY}/resolve/{revision}/{name}") as result:
                result.raise_for_status()
                with temporary.open("wb") as output:
                    for block in result.iter_bytes():
                        digest.update(block)
                        output.write(block)
            if temporary.stat().st_size != item["size"]:
                raise ValueError(f"size_mismatch:{name}")
            if expected and digest.hexdigest() != expected:
                raise ValueError(f"checksum_mismatch:{name}")
            temporary.replace(target)
        print(f"Ready: {DESTINATION}; revision={revision}", flush=True)


if __name__ == "__main__":
    main()
