from pathlib import Path
p = Path('tools/patch_155.py')
s = p.read_text(encoding='utf-8')
s = s.replace("# Keep newest entries first regardless of the historical misplaced heading.\\n    CHANGELOG.write_text(entry + text, encoding='utf-8')", "# Keep newest entries first regardless of the historical misplaced heading.\n    CHANGELOG.write_text(entry + text, encoding='utf-8')")
p.write_text(s, encoding='utf-8')
