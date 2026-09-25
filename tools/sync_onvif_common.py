from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shared" / "onvif_camera.py"
TARGETS = (
    ROOT / "uniview_camera_bridge" / "onvif_camera.py",
    ROOT / "tapo_camera_bridge" / "onvif_camera.py",
)


def main() -> int:
    payload = SOURCE.read_bytes()
    for target in TARGETS:
        target.write_bytes(payload)
        print(f"synced {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
