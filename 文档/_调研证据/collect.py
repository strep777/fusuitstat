import concurrent.futures as cf
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.request
import urllib.parse as up
from html.parser import HTMLParser

OUT = Path(__file__).parent
SEEDS = {
    'ww-pass': 'https://www.ww-pass.com/wp-fursuit/',
    'alkaid': 'https://www.alkaidfur.com/',
    'fureststory': 'https://fureststory.com/',
    'ulfursuit': 'https://www.ulfursuit.com/zh-Hans',
    'halosea': 'https://halosea-fursuit.com/',
}

class Parse(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.scripts=[]; self.text=[]; self.controls=[]; self.skip=0; self.title=False; self.titles=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag in ('script','style'): self.skip+=1
        if tag=='title': self.title=True
        if tag=='a' and a.get('href'): self.links.append(a['href'])
        if tag=='script' and a.get('src'): self.scripts.append(a['src'])
        if tag in ('input','select','textarea','button'): self.controls.append({k:v for k,v in a.items() if k in ('name','type','placeholder','aria-label','required','accept')})
    def handle_endtag(self,tag):
        if tag in ('script','style'): self.skip=max(0,self.skip-1)
        if tag=='title': self.title=False
    def handle_data(self,t):
        if self.title:self.titles.append(t)
        if not self.skip and t.strip():self.text.append(t.strip())

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; public-site-research)','Accept-Encoding':'gzip'})
    try:
        with urllib.request.urlopen(req,timeout=15) as r:
            raw=r.read(3000000)
            if raw[:2]==b'\x1f\x8b':raw=gzip.decompress(raw)
            return {'url':url,'final_url':r.url,'status':r.status,'body':raw.decode('utf-8','replace'),'content_type':r.headers.get('Content-Type','')}
    except Exception as e:return {'url':url,'status':'error','error':str(e)}

def normal(url):
    p=up.urlsplit(url)
    if p.scheme not in ('http','https'):return None
    if re.search(r'\.(png|jpg|jpeg|gif|webp|svg|mp4|pdf|zip|css|js|ico|woff2?)$',p.path,re.I):return None
    if any(x in p.path.lower() for x in ('logout','signout','/api/','/wp-admin','/cdn-cgi/')):return None
    query=[(k,v) for k,v in up.parse_qsl(p.query) if not k.startswith(('utm_','fbclid'))]
    return up.urlunsplit((p.scheme,p.netloc,p.path or '/',up.urlencode(query),''))

def crawl(item):
    name,seed=item; host=up.urlsplit(seed).netloc; base=up.urlunsplit(('https',host,'','','')); seen=set(); todo=[seed]; pages=[]; discovery=[]
    for path in ('/robots.txt','/sitemap.xml'):
        d=fetch(base+path); body=d.pop('body',''); d['text']=body[:35000]; discovery.append(d)
        if path.endswith('xml') and '<loc>' in body:
            for loc in re.findall(r'<loc>(.*?)</loc>',body):
                if loc.endswith('.xml'):
                    sub=fetch(loc); b=sub.pop('body',''); sub['text']=b[:60000]; discovery.append(sub)
                    todo.extend(re.findall(r'<loc>(.*?)</loc>',b))
                else:todo.append(loc)
    while todo and len(seen)<240:
        url=normal(todo.pop(0))
        if not url or url in seen or up.urlsplit(url).netloc!=host:continue
        seen.add(url); d=fetch(url); body=d.pop('body','')
        if body and 'html' in d.get('content_type',''):
            p=Parse();p.feed(body)
            d.update(title=' '.join(p.titles),text='\n'.join(p.text),controls=p.controls,scripts=[up.urljoin(url,s) for s in p.scripts],links=sorted(set(up.urljoin(url,h) for h in p.links)))
            d['sha256']=hashlib.sha256(body.encode()).hexdigest()
            for h in d['links']:
                n=normal(h)
                if n and n not in seen and up.urlsplit(n).netloc==host:todo.append(n)
        pages.append(d)
        (OUT/(name+'.json')).write_text(json.dumps({'seed':seed,'pages':pages,'discovery':discovery,'remaining':list(dict.fromkeys(todo))},ensure_ascii=False,indent=2),encoding='utf-8')
        time.sleep(.12)
    return name,len(pages),len(set(todo))

if __name__=='__main__':
    with cf.ThreadPoolExecutor(max_workers=5) as pool:
        for result in pool.map(crawl,SEEDS.items()): print(result,flush=True)
