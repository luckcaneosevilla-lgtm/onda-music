"""Bounded, unauthenticated YouTube Music catalogue search."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from youtube_provider import ProviderError

class MusicSearch:
    def search(self,query,kind='songs',limit=20):
        env={k:v for k,v in os.environ.items() if k!='SSLKEYLOGFILE'}
        env['PYTHONIOENCODING']='utf-8'
        vendor=Path(__file__).resolve().parent/'.vendor'
        if vendor.exists():env['PYTHONPATH']=str(vendor)+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        try:
            result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker',query,kind,str(min(limit,100))],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=25,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode!=0:raise ValueError()
            entries=json.loads(result.stdout)
            if not isinstance(entries,list):raise ValueError()
            return entries
        except (OSError,ValueError,subprocess.TimeoutExpired):
            raise ProviderError(502,'ytmusic_unavailable','YouTube Music no está disponible temporalmente. Se intentará la búsqueda de respaldo.') from None

    @staticmethod
    def normalize(entry):
        raw=entry.get('videoId','')
        if not isinstance(raw,str) or not re.fullmatch('[A-Za-z0-9_-]{11}',raw) or entry.get('isAvailable') is False:return None
        artists=[str(a['name']) for a in (entry.get('artists') or []) if isinstance(a,dict) and a.get('name')]
        seconds=entry.get('duration_seconds')
        if not isinstance(seconds,(int,float)):
            value=entry.get('duration','')
            try:
                parts=[int(p) for p in value.split(':')];seconds=sum(v*60**i for i,v in enumerate(reversed(parts))) if 1<=len(parts)<=3 else None
            except (TypeError,ValueError):seconds=None
        thumbnails=entry.get('thumbnails') or []
        image=next((t.get('url','') for t in reversed(thumbnails) if str(t.get('url','')).startswith('https://')),'')
        return {'id':'piped:'+raw,'provider_id':raw,'provider':'piped','source':'YouTube Music','title':str(entry.get('title',''))[:300],'channel':', '.join(artists)[:200],'artists':artists,'album':entry.get('album'),'thumbnail':image,'duration':seconds,'available':True}

    @staticmethod
    def artist(entry):
        raw=entry.get('browseId','');name=entry.get('artist',entry.get('name',entry.get('title','')))
        if not raw or not name:return None
        image=next((t.get('url','') for t in reversed(entry.get('thumbnails',[])) if str(t.get('url','')).startswith('https://')),'')
        return {'id':str(raw)[:100],'name':str(name)[:200],'thumbnail':image,'source':'YouTube Music'}

if __name__=='__main__':
    # A subprocess bounds both client initialization and search, and keeps
    # third-party TLS/client globals out of the threaded application server.
    from ytmusicapi import YTMusic
    import requests
    query,kind,limit=sys.argv[2],sys.argv[3],int(sys.argv[4])
    if kind not in ('songs','artists') or not 1<=limit<=100:raise SystemExit(2)
    class BoundedSession(requests.Session):
        def request(self,*args,**kwargs):
            kwargs.setdefault('timeout',10)
            return super().request(*args,**kwargs)
    # The pinned parser returns empty song lists with the Spanish localization.
    # English internal labels work; original song/artist names and ONDA stay unchanged.
    client=YTMusic(language='en',location='ES',requests_session=BoundedSession())
    print(json.dumps(client.search(query,filter=kind,limit=limit),ensure_ascii=True))
