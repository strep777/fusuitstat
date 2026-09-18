import collections
import json
from pathlib import Path
import re
import urllib.parse as up

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / '_调研证据'

def read(name):
    return json.loads((E / name).read_text(encoding='utf-8'))

def esc(s):
    return str(s).replace('|','／').replace('\n',' ').strip()

def table(rows):
    out=['| URL | HTTP/证据 | 页面标题或边界 |','|---|---|---|']
    for r in rows:
        url=r['url']; status=r['status']; title=r.get('title','')
        if status=='error':
            evidence='读取失败'; title=r.get('error','未返回内容')
        elif any(x in url for x in ('halosea-fursuit.com/estimate','halosea-fursuit.com/account','halosea-fursuit.com/rush','halosea-fursuit.com/commissions','halosea-fursuit.com/reset-password')):
            evidence=f'{status} / A+C';title+='；仅公开壳/状态说明，内部流程未验证'
        else:evidence=f'{status} / A'
        out.append(f'| [{esc(url)}]({url}) | {evidence} | {esc(title)[:140]} |')
    return '\n'.join(out)

docs={n:read(n+'.json') for n in ['ww-pass','alkaid','fureststory','ulfursuit','halosea']}
alt=read('fureststory-alternates.json'); haloextra=read('halosea-extra.json')
if (E/'wp-entry.js').exists():
    script=(E/'wp-entry.js').read_text(encoding='utf-8')
    registered=sorted(set(p for p in re.findall(r'path:\s*[\"\']([^\"\']+)',script) if p.startswith('/')))
else:
    registered=read('wp-registered-routes.json')['routes']
(E/'wp-registered-routes.json').write_text(json.dumps({'source':docs['ww-pass']['pages'][0]['scripts'][0],'routes':registered},ensure_ascii=False,indent=2),encoding='utf-8')

lines=['# 参考站公开路由清单','',
'采集日期：2026-09-18。A=公开 HTML/正文证据，B=公开脚本路由/组件证据，C=登录或运行状态待验证。HTTP 200 仅表示返回响应，不等于完整功能可用。','',
'URL 由站内链接、sitemap/alternate 或页面直接引用的公开脚本发现，没有递增枚举私密 ID，没有提交表单。外链只登记功能去向，不爬外部商城结算。','',
'## 数量与覆盖','',
'| 网站 | 实际页面请求记录 | 覆盖说明 |','|---|---:|---|',
f'| 万物通行 | 1 入口 + 1 合同正文 | 另提取 {len(registered)} 个含多语言/动态参数的注册路由；{len(read("wp-routes.json"))} 个相关组件作静态阅读 |',
f'| ALKAID | {len(docs["alkaid"]["pages"])} | 含 170 个详情和栏目/分页/首页别名 |',
f'| 森绒物语 | {len(docs["fureststory"]["pages"])} 中文 + {len(alt)} 多语言链接 | sitemap alternate 后继续递归多语言站内链接 |',
f'| 宇灵制造 | {len(docs["ulfursuit"]["pages"])} | 合并登录/注册 returnTo 参数后 41 种 URL 形态 |',
f'| 光海印记 | {len(docs["halosea"]["pages"])} 导航 + {len(haloextra)} 补充请求 | /me 与 /me/guarantee 读取失败，内部业务未验证 |','',
'请求数不含 robots、sitemap 和脚本。未把不同语言、登录返回地址和首页别名都算成独立功能。页面随站点更新变化，本清单不是永远不变的站点承诺。','',
'## robots 与 sitemap','',
'| 地址 | 结果 |','|---|---|']
for d in docs.values():
    for x in d['discovery']:
        body=x.get('text','')
        result=x.get('observation') or (x.get('error','') if x['status']=='error' else ('XML 地图，已提取 loc 与 alternate' if '<urlset' in body else '返回 HTML 应用页，不是有效地图/robots' if '<html' in body.lower() else '空正文' if not body.strip() else '已读取文本'))
        lines.append(f'| [{x["url"]}]({x["url"]}) | {esc(result)[:160]} |')
lines += ['', '## 万物通行注册路由', '',
'来源：[页面引用的公开入口脚本]('+docs['ww-pass']['pages'][0]['scripts'][0]+')。以下统一为 B；动态模板没有填入真实工单号或验证码。非兽装品牌路由单独保留，不据此扩大本项目必做范围。','',
'| 声明的路由/模板 | 范围 |','|---|---|']
for r in registered:
    scope='兽装/作品/委托相关' if any(t in r for t in ['fursuit','gallery']) else '品牌关联/其他入口，功能未逐页验证'
    lines.append(f'| `{r}` | {scope} |')
lines += ['', '### 相关组件读取', '', '| 业务路由 | 公开组件 |','|---|---|']
for r in read('wp-routes.json'):
    lines.append(f'| `{r["route"]}` | `{r["component"]}` |')
lines += ['','合同正文：[fursuit-contract.html](https://www.ww-pass.com/docs/fursuit-contract.html)，HTTP 200/A。flow/specs 组件为空主体的限制见 01，不当作完成功能。','',
'## ALKAID 全部已发现地址','',table(docs['alkaid']['pages']),'',
'分页链接 `/?m555page=...`、`/?m614page=...` 本次返回首页内容；这里只保留证据，不据此认定分页交互正常。','',
'## 森绒物语中文地址','',table(docs['fureststory']['pages']),'',
'## 森绒物语多语言地址','',table(alt),'',
'## 宇灵制造全部已发现地址','',table(docs['ulfursuit']['pages']),'',
'login/register 的 returnTo 仅表示返回目标；邮箱表单可读不代表认证成功。公开 designers/test 保留为发现事实。','',
'## 光海印记公开与补充地址','',table(docs['halosea']['pages']+haloextra),'',
'### 仅脚本发现的路径','',
'下列路径包含别名、登录状态参数和后台入口，统一 B/C；除上表已请求的地址外未逐项执行。','']
paths=sorted(set(x for r in read('halosea-source-routes.json') for x in r['routes']))
for x in paths:lines.append(f'- `{x}`')
lines += ['','## 无法从公共调研证明的范围','',
'登录后用户资料、后台内部路由、有效令牌结果页、支付后的订单行为、非当前开放活动、未链接隐藏页以及只由接口动态返回的全部记录，均无法在未获授权的情况下证明已穷尽。公开页面中的支付方式/分期描述不等于实际支付集成；本次不对竞品安全性作结论。','',
'功能归纳与本项目映射见 [01_参考网站调研](01_参考网站调研.md)，实际要开发的路由见 [03_站点路由与页面功能](03_站点路由与页面功能.md)。']
(ROOT/'附录A_参考站公开路由清单.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
summary={'date':'2026-09-18','main_page_requests':{k:len(v['pages']) for k,v in docs.items()},'furest_alternates':len(alt),'halo_extra':len(haloextra),'wp_registered_routes':len(registered),'total_page_requests':sum(len(v['pages']) for v in docs.values())+len(alt)+len(haloextra)+1}
(E/'coverage-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
