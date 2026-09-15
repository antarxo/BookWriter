'use strict';
(function(global){
const VERSION='bookwriter-web-rendered-0.2.0';
let activeUrls=[];
let activeUrlSources=new Map();
const cleanupUrls=()=>{for(const url of activeUrls){try{URL.revokeObjectURL(url)}catch{}}activeUrls=[];activeUrlSources=new Map()};
const makeUrl=(blob,sourcePath='')=>{const url=URL.createObjectURL(blob);activeUrls.push(url);if(sourcePath)activeUrlSources.set(url,sourcePath);return url};
function normalizePath(value=''){
  let raw=String(value||'').trim().replace(/\\/g,'/').split('#')[0].split('?')[0];
  try{raw=decodeURIComponent(raw)}catch{}
  raw=raw.normalize('NFC').replace(/^\.\//,'').replace(/^\/+/, '');
  const out=[];
  for(const part of raw.split('/')){if(!part||part==='.')continue;if(part==='..')out.pop();else out.push(part)}
  return out.join('/');
}
const basename=value=>normalizePath(value).split('/').pop()||'';
const fold=value=>normalizePath(value).toLocaleLowerCase('el-GR');
function joinPath(base='',relative=''){
  const rel=String(relative||'').trim();
  if(!rel)return'';
  if(/^(?:[a-z]+:|\/\/|#)/i.test(rel))return rel;
  const dir=normalizePath(base).split('/').slice(0,-1).join('/');
  return normalizePath((dir?dir+'/':'')+rel);
}
async function collectDirectoryFiles(handle,prefix='',map=new Map()){
  for await(const[name,entry]of handle.entries()){
    const path=normalizePath(prefix?prefix+'/'+name:name);
    if(entry.kind==='file')map.set(path,await entry.getFile());
    else if(entry.kind==='directory')await collectDirectoryFiles(entry,path,map);
  }
  return map;
}
function findHtmlPath(files,fileName=''){
  const wanted=String(fileName||'').normalize('NFC').toLocaleLowerCase('el-GR');
  const root=[...files.keys()].find(path=>!path.includes('/')&&basename(path).toLocaleLowerCase('el-GR')===wanted);
  if(root)return root;
  return [...files.keys()].find(path=>basename(path).toLocaleLowerCase('el-GR')===wanted)||'';
}
function createResolver(files,htmlPath){
  const entries=[...files.entries()];
  const htmlBase=basename(htmlPath).replace(/\.html?$/i,'');
  const companionPrefix=fold(htmlBase+'_files/');
  const urlCache=new Map();
  const usedAssets=new Map();
  function fileFor(raw,basePath=htmlPath){
    const joined=joinPath(basePath,raw);
    if(/^(?:data:|blob:|https?:|file:|\/\/|#)/i.test(joined))return null;
    const exact=files.get(normalizePath(joined));if(exact)return{file:exact,path:normalizePath(joined)};
    const folded=fold(joined);
    const ci=entries.find(([path])=>fold(path)===folded);if(ci)return{file:ci[1],path:ci[0]};
    const wanted=basename(raw).toLocaleLowerCase('el-GR');if(!wanted)return null;
    const companion=entries.filter(([path])=>fold(path).startsWith(companionPrefix)&&basename(path).toLocaleLowerCase('el-GR')===wanted);
    if(companion.length===1)return{file:companion[0][1],path:companion[0][0]};
    const byBase=entries.filter(([path])=>basename(path).toLocaleLowerCase('el-GR')===wanted);
    return byBase.length===1?{file:byBase[0][1],path:byBase[0][0]}:null;
  }
  function objectUrlFor(record){
    if(!record)return'';
    if(urlCache.has(record.path))return urlCache.get(record.path);
    const url=makeUrl(record.file,record.path);urlCache.set(record.path,url);
    usedAssets.set(record.path,{path:record.path,name:record.file.name||basename(record.path),type:record.file.type||'',size:Number(record.file.size)||0,lastModified:Number(record.file.lastModified)||0});
    return url;
  }
  return{fileFor,objectUrlFor,usedAssets};
}
function parseSrcset(value=''){
  return String(value||'').split(',').map(part=>part.trim()).filter(Boolean).map(part=>{
    const m=part.match(/^(\S+)(\s+.+)?$/);return m?{url:m[1],descriptor:m[2]||''}:{url:part,descriptor:''};
  });
}
async function rewriteCssText(cssText,cssPath,resolver,unresolved){
  let text=String(cssText||'');
  const importRe=/@import\s+(?:url\()?\s*(["']?)([^"')\s;]+)\1\s*\)?\s*([^;]*);/gi;
  const imports=[];let m;
  while((m=importRe.exec(text)))imports.push({full:m[0],url:m[2],media:m[3]||''});
  for(const imp of imports){
    const rec=resolver.fileFor(imp.url,cssPath);
    if(rec){const nested=await rewriteCssText(await rec.file.text(),rec.path,resolver,unresolved);text=text.replace(imp.full,(imp.media.trim()?`@media ${imp.media.trim()}{${nested}}`:nested));}
    else unresolved.add(imp.url);
  }
  const urlRe=/url\(\s*(["']?)([^"')]+)\1\s*\)/gi;
  text=text.replace(urlRe,(full,q,url)=>{
    const raw=String(url||'').trim();
    if(!raw||/^(?:data:|blob:|https?:|file:|\/\/|#)/i.test(raw))return full;
    const rec=resolver.fileFor(raw,cssPath);
    if(!rec){unresolved.add(raw);return full}
    return `url("${resolver.objectUrlFor(rec)}")`;
  });
  return text;
}
function exactDoctype(html=''){
  const m=String(html||'').match(/<!doctype[^>]*>/i);return m?m[0]:'<!DOCTYPE html>';
}
async function buildRenderableHtml(html,files,htmlPath,options={}){
  const unresolved=new Set();
  const resolver=createResolver(files,htmlPath);
  const doc=new DOMParser().parseFromString(String(html||''),'text/html');
  doc.querySelectorAll('script').forEach(node=>node.remove());
  doc.querySelectorAll('*').forEach(node=>{for(const attr of [...node.attributes])if(attr.name.toLowerCase().startsWith('on'))node.removeAttribute(attr.name)});
  for(const link of [...doc.querySelectorAll('link[rel~="stylesheet"][href]')]){
    const href=link.getAttribute('href')||'';
    const rec=resolver.fileFor(href,htmlPath);
    if(rec){
      const css=await rewriteCssText(await rec.file.text(),rec.path,resolver,unresolved);
      const style=doc.createElement('style');style.dataset.bwSourceCss=rec.path;style.textContent=css;link.replaceWith(style);
    }else if(!/^(?:https?:|\/\/|data:|blob:)/i.test(href)){unresolved.add(href);link.remove()}
  }
  for(const style of [...doc.querySelectorAll('style')])style.textContent=await rewriteCssText(style.textContent||'',htmlPath,resolver,unresolved);
  const assetAttrs=[['img','src'],['input[type="image"]','src'],['source','src'],['video','poster'],['audio','src'],['video','src'],['embed','src'],['object','data']];
  for(const [selector,attr] of assetAttrs){for(const node of [...doc.querySelectorAll(`${selector}[${attr}]`)]){
    const raw=node.getAttribute(attr)||'';
    if(/^(?:data:|blob:|https?:|file:|\/\/|#)/i.test(raw))continue;
    const rec=resolver.fileFor(raw,htmlPath);
    if(rec){
      node.dataset.bwSourcePath=rec.path;
      node.dataset.bwOriginalUrl=raw;
      node.dataset.bwSourceAttr=attr;
      node.setAttribute(attr,resolver.objectUrlFor(rec));
    }else unresolved.add(raw);
  }}
  for(const node of [...doc.querySelectorAll('[srcset]')]){
    const originalSrcset=node.getAttribute('srcset')||'';
    const sourcePaths=[];
    const out=parseSrcset(originalSrcset).map(item=>{
      if(/^(?:data:|blob:|https?:|file:|\/\/|#)/i.test(item.url))return item.url+item.descriptor;
      const rec=resolver.fileFor(item.url,htmlPath);if(!rec){unresolved.add(item.url);return item.url+item.descriptor}
      sourcePaths.push(rec.path);
      return resolver.objectUrlFor(rec)+item.descriptor;
    });
    node.dataset.bwOriginalSrcset=originalSrcset;
    if(sourcePaths.length)node.dataset.bwSourceSrcset=JSON.stringify(sourcePaths);
    node.setAttribute('srcset',out.join(', '));
  }
  for(const node of [...doc.querySelectorAll('[style]')])node.setAttribute('style',await rewriteCssText(node.getAttribute('style')||'',htmlPath,resolver,unresolved));
  const base=doc.querySelector('base');if(base)base.remove();
  if(options.injectReset===true){const style=doc.createElement('style');style.textContent='html,body{min-height:100%;}';doc.head.appendChild(style)}
  let nextNodeId=1;
  for(const el of [...doc.body.querySelectorAll('*')])el.dataset.bwNodeId=String(nextNodeId++);
  doc.body.dataset.bwNodeId='0';
  return{
    html:exactDoctype(html)+'\n'+doc.documentElement.outerHTML,
    unresolved:[...unresolved],
    assets:[...resolver.usedAssets.values()]
  };
}
function styleSnapshot(cs){
  const props=[
    'display','position','float','clear','visibility','opacity','zIndex','boxSizing',
    'width','height','minWidth','minHeight','maxWidth','maxHeight','aspectRatio',
    'marginTop','marginRight','marginBottom','marginLeft',
    'paddingTop','paddingRight','paddingBottom','paddingLeft',
    'borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth',
    'borderTopStyle','borderRightStyle','borderBottomStyle','borderLeftStyle',
    'borderTopColor','borderRightColor','borderBottomColor','borderLeftColor',
    'borderRadius','borderCollapse','borderSpacing','tableLayout','captionSide',
    'backgroundColor','backgroundImage','backgroundPosition','backgroundSize','backgroundRepeat',
    'color','fontFamily','fontSize','fontWeight','fontStyle','fontVariant','fontStretch',
    'lineHeight','letterSpacing','wordSpacing','textAlign','textIndent','textTransform',
    'textDecorationLine','textDecorationStyle','textDecorationColor','textDecorationThickness',
    'textShadow','whiteSpace','verticalAlign','direction','unicodeBidi','writingMode',
    'listStyleType','listStylePosition','listStyleImage',
    'columnCount','columnGap','columnWidth',
    'flexDirection','flexWrap','justifyContent','alignItems','alignContent','gap',
    'gridTemplateColumns','gridTemplateRows','gridAutoFlow',
    'transform','transformOrigin','transformStyle',
    'objectFit','objectPosition',
    'overflowX','overflowY','clipPath','filter','boxShadow','outlineWidth','outlineStyle','outlineColor'
  ];
  const out={};for(const p of props)out[p]=cs[p];return out;
}
function sourceRefsFromCssValue(value=''){
  const refs=[];const text=String(value||'');const re=/blob:[^)"'\s]+/g;let m;
  while((m=re.exec(text))){const source=activeUrlSources.get(m[0]);if(source&&!refs.includes(source))refs.push(source)}
  return refs;
}
function selectedAttributes(el){
  const out={};
  for(const attr of [...el.attributes]){
    const n=attr.name.toLowerCase();
    if(n.startsWith('on'))continue;
    if(n==='style'||n==='class'||n==='id'||n==='data-bw-node-id')continue;
    if(n.startsWith('data-bw-'))continue;
    out[attr.name]=attr.value;
  }
  return out;
}
function pseudoSnapshot(win,el,pseudo){
  const cs=win.getComputedStyle(el,pseudo),content=cs.content;
  if(!content||content==='none'||content==='normal')return null;
  return{kind:pseudo.slice(2),content,style:styleSnapshot(cs),sourceRefs:sourceRefsFromCssValue(cs.backgroundImage||'')};
}
function elementPath(el){
  const parts=[];let cur=el;
  while(cur&&cur.nodeType===1&&cur.tagName!=='HTML'){
    let part=cur.tagName.toLowerCase();
    if(cur.id){part+='#'+cur.id;parts.unshift(part);break}
    const parent=cur.parentElement;if(parent){const same=[...parent.children].filter(x=>x.tagName===cur.tagName);if(same.length>1)part+=`:nth-of-type(${same.indexOf(cur)+1})`}
    parts.unshift(part);cur=parent;
  }
  return parts.join('>');
}
function rectObject(rect,win){return{x:rect.left+win.scrollX,y:rect.top+win.scrollY,width:rect.width,height:rect.height,right:rect.right+win.scrollX,bottom:rect.bottom+win.scrollY}}
function captureSnapshot(frame){
  const doc=frame.contentDocument,win=frame.contentWindow;if(!doc||!win)throw Error('Rendered document unavailable');
  const elements=[];
  const all=[doc.body,...doc.body.querySelectorAll('*')];
  for(const el of all){
    if(['SCRIPT','STYLE','LINK','META','TITLE'].includes(el.tagName))continue;
    const cs=win.getComputedStyle(el),rect=el.getBoundingClientRect();
    if(cs.display==='none')continue;
    const nodeId=Number(el.dataset.bwNodeId||-1);
    const parent=el.parentElement;
    const parentId=parent?Number(parent.dataset.bwNodeId||-1):-1;
    const childIndex=parent?[...parent.children].indexOf(el):-1;
    const pseudo=[pseudoSnapshot(win,el,'::before'),pseudoSnapshot(win,el,'::after'),pseudoSnapshot(win,el,'::marker')].filter(Boolean);
    elements.push({
      nodeId,parentId,childIndex,path:elementPath(el),tag:el.tagName.toLowerCase(),id:el.id||'',
      className:typeof el.className==='string'?el.className:'',
      role:el.getAttribute('role')||'',lang:el.getAttribute('lang')||'',dir:el.getAttribute('dir')||'',
      attributes:selectedAttributes(el),
      rect:rectObject(rect,win),style:styleSnapshot(cs),
      backgroundSourceRefs:sourceRefsFromCssValue(cs.backgroundImage||''),
      pseudo
    });
  }
  const textRuns=[];
  const textIndexByParent=new Map();
  const walker=doc.createTreeWalker(doc.body,NodeFilter.SHOW_TEXT,{acceptNode(node){return node.nodeValue&&node.nodeValue.trim()?NodeFilter.FILTER_ACCEPT:NodeFilter.FILTER_REJECT}});
  let node,runId=1;
  while((node=walker.nextNode())){
    const parent=node.parentElement;if(!parent)continue;const cs=win.getComputedStyle(parent);if(cs.display==='none'||cs.visibility==='hidden')continue;
    const range=doc.createRange();range.selectNodeContents(node);const rects=[...range.getClientRects()].filter(r=>r.width||r.height).map(r=>rectObject(r,win));
    if(!rects.length)continue;
    const parentId=Number(parent.dataset.bwNodeId||-1);
    const textIndex=textIndexByParent.get(parentId)||0;textIndexByParent.set(parentId,textIndex+1);
    textRuns.push({
      runId:runId++,parentId,textIndex,parentPath:elementPath(parent),
      textRaw:node.nodeValue,text:node.nodeValue.replace(/\s+/g,' '),rects,
      style:{
        fontFamily:cs.fontFamily,fontSize:cs.fontSize,fontWeight:cs.fontWeight,fontStyle:cs.fontStyle,
        fontVariant:cs.fontVariant,fontStretch:cs.fontStretch,lineHeight:cs.lineHeight,
        letterSpacing:cs.letterSpacing,wordSpacing:cs.wordSpacing,color:cs.color,
        textDecorationLine:cs.textDecorationLine,textDecorationStyle:cs.textDecorationStyle,
        textDecorationColor:cs.textDecorationColor,textDecorationThickness:cs.textDecorationThickness,
        verticalAlign:cs.verticalAlign,whiteSpace:cs.whiteSpace,direction:cs.direction,unicodeBidi:cs.unicodeBidi
      }
    });
  }
  const images=[...doc.images].map(img=>{
    const r=img.getBoundingClientRect(),cs=win.getComputedStyle(img);
    const current=img.currentSrc||img.src;
    return{
      nodeId:Number(img.dataset.bwNodeId||-1),path:elementPath(img),
      sourcePath:img.dataset.bwSourcePath||activeUrlSources.get(current)||'',
      originalUrl:img.dataset.bwOriginalUrl||'',
      src:current,alt:img.alt||'',title:img.title||'',
      naturalWidth:img.naturalWidth,naturalHeight:img.naturalHeight,
      rect:rectObject(r,win),style:styleSnapshot(cs)
    };
  });
  const backgrounds=elements.filter(e=>e.style.backgroundImage&&e.style.backgroundImage!=='none').map(e=>({
    nodeId:e.nodeId,path:e.path,rect:e.rect,backgroundImage:e.style.backgroundImage,
    sourcePaths:e.backgroundSourceRefs||[],backgroundSize:e.style.backgroundSize,
    backgroundPosition:e.style.backgroundPosition,backgroundRepeat:e.style.backgroundRepeat
  }));
  const zeroSized=elements.filter(e=>e.rect.width<0.5||e.rect.height<0.5).length;
  return{
    version:VERSION,
    viewport:{width:frame.clientWidth,height:frame.clientHeight},
    document:{
      width:Math.max(doc.documentElement.scrollWidth,doc.body.scrollWidth),
      height:Math.max(doc.documentElement.scrollHeight,doc.body.scrollHeight),
      title:doc.title,lang:doc.documentElement.lang||'',dir:doc.documentElement.dir||''
    },
    counts:{elements:elements.length,textRuns:textRuns.length,images:images.length,backgrounds:backgrounds.length,zeroSized},
    elements,textRuns,images,backgrounds
  };
}
async function renderPackage({files,htmlPath,frame,viewportWidth=1200,viewportHeight=900}){
  if(!files||!htmlPath||!frame)throw Error('files, htmlPath and frame are required');cleanupUrls();
  const source=files.get(htmlPath);if(!source)throw Error('HTML file not found in package');
  const built=await buildRenderableHtml(await source.text(),files,htmlPath);
  frame.style.width=Math.max(320,Number(viewportWidth)||1200)+'px';frame.style.height=Math.max(320,Number(viewportHeight)||900)+'px';
  frame.setAttribute('sandbox','allow-same-origin');
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Render timeout')),15000);frame.onload=()=>{clearTimeout(timer);resolve()};frame.srcdoc=built.html});
  try{await frame.contentDocument.fonts?.ready}catch{}
  await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
  const snapshot=captureSnapshot(frame);
  snapshot.source={htmlPath};
  snapshot.assets=built.assets||[];
  snapshot.unresolvedResources=built.unresolved;
  return snapshot;
}
global.WebRenderedCoreV4=Object.freeze({VERSION,normalizePath,collectDirectoryFiles,findHtmlPath,buildRenderableHtml,renderPackage,captureSnapshot,cleanup:cleanupUrls});
})(window);
