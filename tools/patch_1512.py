from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "uniview_camera_bridge"
U = ROOT / "uniview.py"
A = ROOT / "app.py"
C = ROOT / "config.yaml"
CH = ROOT / "CHANGELOG.md"


def repl(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected text not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")

old = '''            transient_get_fault = (\n                method == "GET"\n                and response.status_code == 500\n                and "HTTP GET method not implemented" in response.text\n            )\n            if transient_get_fault and attempt < 2:\n                logging.debug(\n                    "Transient Uniview GET/Digest fault path=%s attempt=%d/3; resetting HTTP session",\n                    path, attempt + 1,\n                )\n'''
new = '''            transient_method_fault = (\n                response.status_code == 500\n                and f"HTTP {method} method not implemented" in response.text\n            )\n            if transient_method_fault and attempt < 2:\n                logging.debug(\n                    "Transient Uniview %s/Digest fault path=%s attempt=%d/3; resetting HTTP session",\n                    method, path, attempt + 1,\n                )\n'''
repl(U, old, new)

repl(C, "version: 1.5.11\n", "version: 1.5.12\n")
repl(A, '    options["addon_version"] = "1.5.11"\n', '    options["addon_version"] = "1.5.12"\n')
repl(A, '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.11")\n', '    logging.info("UNIVIEW CAMERA BRIDGE STARTING - version 1.5.12")\n')

text = CH.read_text(encoding="utf-8")
entry = '''## 1.5.12\n\n- Treat the NVR-forwarded camera's bogus HTTP 500 `HTTP <METHOD> method not implemented` SOAP response as the same transient Digest/session fault for PUT as already handled for GET.\n- Reset the HTTP session and retry up to three times while preserving the exact request body and headers, allowing D2 private Day/Night writes to renegotiate Digest authentication just like successful LampCtrl/Exposure reads.\n\n'''
if "## 1.5.12\n" not in text:
    CH.write_text(entry + text, encoding="utf-8")
