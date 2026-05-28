import os, re
pattern = re.compile(r'except(?:\s+Exception(?:\s+as\s+\w+)?)?:\s*(?:#[^\n]*\n\s*)*(?:pass|return\s+\[\]|return\s+\{\}|return\s+False|return\s+None)')
pattern_import = re.compile(r'except\s+ImportError:\s*(?:#[^\n]*\n\s*)*(?:pass|return\s+\[\]|return\s+\{\}|return\s+False|return\s+None)')

for root, dirs, files in os.walk('.'):
    if '.venv' in dirs:
        dirs.remove('.venv')
    if '.git' in dirs:
        dirs.remove('.git')
    for f in files:
        if f.endswith('.py') and f != 'search_exceptions.py':
            path = os.path.join(root, f)
            try:
                with open(path, 'r', encoding='utf-8') as file:
                    content = file.read()
            except UnicodeDecodeError:
                continue
            if pattern.search(content) or pattern_import.search(content):
                print(f'Match found in {path}')
                for m in pattern.finditer(content):
                    print("  " + repr(m.group(0)))
                for m in pattern_import.finditer(content):
                    print("  " + repr(m.group(0)))
