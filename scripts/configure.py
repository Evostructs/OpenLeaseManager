"""Create local configuration without printing secrets or replacing existing files."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
example = (root / '.env.example').read_text(encoding='utf-8')
try:
    with (root / '.env').open('x', encoding='utf-8') as stream:
        stream.write(example.replace('JWT_SECRET=\n', 'JWT_SECRET=' + secrets.token_urlsafe(48) + '\n'))
except FileExistsError:
    raise SystemExit('.env already exists; left unchanged.')
print('Created .env. Keep this file private. No default administrator account is installed.')
