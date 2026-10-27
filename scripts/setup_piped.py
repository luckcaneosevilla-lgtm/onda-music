"""Prepare the private Compose DB password without printing existing secrets."""
from pathlib import Path
import secrets
root=Path(__file__).resolve().parents[1]
env=root/'.env'
text=env.read_text(encoding='utf-8-sig') if env.exists() else ''
values={line.split('=',1)[0].strip():line.split('=',1)[1].strip() for line in text.splitlines() if '=' in line and not line.lstrip().startswith('#')}
defaults={'PIPED_API_URL':'http://127.0.0.1:8080','PIPED_PROXY_URL':'http://127.0.0.1:8081','AUDIUS_API_URL':'https://api.audius.co','ALLOW_YOUTUBE_EXTERNAL':'0','PIPED_DB_PASSWORD':secrets.token_urlsafe(32)}
lines=text.splitlines()
for key,value in defaults.items():
    if values.get(key):continue
    lines=[line for line in lines if not line.startswith(key+'=')]
    lines.append(key+'='+value)
env.write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('Configuración privada preparada. No se han mostrado ni sustituido claves existentes.')
