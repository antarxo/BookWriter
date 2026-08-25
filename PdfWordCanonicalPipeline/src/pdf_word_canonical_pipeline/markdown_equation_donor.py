from __future__ import annotations
import json,re
from pathlib import Path
from typing import Any

IMG_PAGE_RE=re.compile(
 r"-(\d{3})(?=_[0-9]+_[0-9]+_[0-9]+_[0-9]+\.(?:jpg|jpeg|png|webp)\b)|"
 r"-(\d{3})(?=\.(?:jpg|jpeg|png|webp)\b)",
 re.I,
)
DISPLAY_PATTERNS=[
 re.compile(r"\$\$(.+?)\$\$",re.S),
 re.compile(r"\\\[(.+?)\\\]",re.S),
 re.compile(r"\\begin\{(?:equation\*?|aligned|align\*?|gather\*?)\}(.+?)\\end\{(?:equation\*?|aligned|align\*?|gather\*?)\}",re.S),
]

def _plain(latex:str)->str:
 s=re.sub(r"\\(?:mathrm|text|operatorname|mathbf|boldsymbol)\s*\{([^{}]*)\}",r"\1",latex)
 s=re.sub(r"\\(?:left|right|displaystyle|quad|qquad|,|;|!)",'',s)
 s=re.sub(r"\\(?:frac)\{([^{}]*)\}\{([^{}]*)\}",r"\1/\2",s)
 s=re.sub(r"\\([A-Za-z]+)",r"\1",s)
 s=re.sub(r"[{}\s]",'',s)
 return s.casefold()

def _image_page_anchors(text:str)->list[tuple[int,int]]:
 anchors=[]
 for m in IMG_PAGE_RE.finditer(text):
  value=m.group(1) or m.group(2)
  if value: anchors.append((m.start(),int(value)))
 return anchors

def extract_markdown_equations(markdown_files:list[Path],out_path:Path)->dict[str,Any]:
 records=[]
 for md in markdown_files:
  text=Path(md).read_text(encoding='utf-8',errors='replace')
  anchors=_image_page_anchors(text)
  for pattern in DISPLAY_PATTERNS:
   for m in pattern.finditer(text):
    latex=m.group(1).strip()
    if not latex or len(latex)>4000: continue
    before=[a for a in anchors if a[0] <= m.start()]
    after=[a for a in anchors if a[0] > m.start()]
    page=None; confidence='none'
    if before and after and before[-1][1]==after[0][1]: page=before[-1][1]; confidence='high'
    elif before and (not after or m.start()-before[-1][0] <= after[0][0]-m.start()): page=before[-1][1]; confidence='medium'
    elif after: page=after[0][1]; confidence='medium'
    records.append({'id':f'mdeq-{len(records)+1:05d}','source':str(md),'offset':m.start(),'page':page,'pageConfidence':confidence,'latex':latex,'signature':_plain(latex)})
 # dedupe same latex/page
 seen=set(); unique=[]
 for r in records:
  key=(r['page'],r['signature'])
  if not r['signature'] or key in seen: continue
  seen.add(key); unique.append(r)
 result={'version':'markdown-equation-donor-0.2','count':len(unique),'records':unique}
 out_path.parent.mkdir(parents=True,exist_ok=True); out_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 return result
