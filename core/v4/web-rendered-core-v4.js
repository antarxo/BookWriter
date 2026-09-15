'use strict';
(function(global){
const VERSION='bookwriter-web-rendered-0.1.0';
let activeUrls=[];
const cleanupUrls=()=>{for(const url of activeUrls){try{URL.revokeObjectURL(url)}catch{}}activeUrls=[]};
const makeUrl=blob=>{const url=URL.createObjectURL(blob);activeUrls.push(url);return url};
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
    const url=makeUrl(record.file);urlCache.set(record.path,url);return url;
  }
  return{fileFor,objectUrlFor};
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
    if(rec)node.setAttribute(attr,resolver.objectUrlFor(rec));else unresolved.add(raw);
  }}
  for(const node of [...doc.querySelectorAll('[srcset]')]){
    const out=parseSrcset(node.getAttribute('srcset')).map(item=>{
      if(/^(?:data:|blob:|https?:|file:|\/\/|#)/i.test(item.url))return item.url+item.descriptor;
      const rec=resolver.fileFor(item.url,htmlPath);if(!rec){unresolved.add(item.url);return item.url+item.descriptor}
      return resolver.objectUrlFor(rec)+item.descriptor;
    });
    node.setAttribute('srcset',out.join(', '));
  }
  for(const node of [...doc.querySelectorAll('[style]')])node.setAttribute('style',await rewriteCssText(node.getAttribute('style')||'',htmlPath,resolver,unresolved));
  const base=doc.querySelector('base');if(base)base.remove();
  if(options.injectReset===true){const style=doc.createElement('style');style.textContent='html,body{min-height:100%;}';doc.head.appendChild(style)}
  return{html:exactDoctype(html)+'\n'+doc.documentElement.outerHTML,unresolved:[...unresolved]};
}
function styleSnapshot(cs){
  const props=['display','position','float','clear','visibility','opacity','zIndex','boxSizing','width','height','minWidth','minHeight','maxWidth','maxHeight','marginTop','marginRight','marginBottom','marginLeft','paddingTop','paddingRight','paddingBottom','paddingLeft','borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth','borderTopStyle','borderRightStyle','borderBottomStyle','borderLeftStyle','borderTopColor','borderRightColor','borderBottomColor','borderLeftColor','borderRadius','backgroundColor','backgroundImage','backgroundPosition','backgroundSize','backgroundRepeat','color','fontFamily','fontSize','fontWeight','fontStyle','fontVariant','lineHeight','letterSpacing','wordSpacing','textAlign','textIndent','textTransform','textDecorationLine','whiteSpace','verticalAlign','columnCount','columnGap','flexDirection','flexWrap','justifyContent','alignItems','alignContent','gap','gridTemplateColumns','gridTemplateRows','transform','transformOrigin','overflowX','overflowY'];
  const out={};for(const p of props)out[p]=cs[p];return out;
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
  for(const el of [...doc.body.querySelectorAll('*')]){
    if(['SCRIPT','STYLE','LINK','META','TITLE'].includes(el.tagName))continue;
    const cs=win.getComputedStyle(el),rect=el.getBoundingClientRect();
    if(cs.display==='none')continue;
    elements.push({path:elementPath(el),tag:el.tagName.toLowerCase(),id:el.id||'',className:typeof el.className==='string'?el.className:'',role:el.getAttribute('role')||'',rect:rectObject(rect,win),style:styleSnapshot(cs)});
  }
  const textRuns=[];
  const walker=doc.createTreeWalker(doc.body,NodeFilter.SHOW_TEXT,{acceptNode(node){return node.nodeValue&&node.nodeValue.trim()?NodeFilter.FILTER_ACCEPT:NodeFilter.FILTER_REJECT}});
  let node;
  while((node=walker.nextNode())){
    const parent=node.parentElement;if(!parent)continue;const cs=win.getComputedStyle(parent);if(cs.display==='none'||cs.visibility==='hidden')continue;
    const range=doc.createRange();range.selectNodeContents(node);const rects=[...range.getClientRects()].filter(r=>r.width||r.height).map(r=>rectObject(r,win));
    if(!rects.length)continue;
    textRuns.push({text:node.nodeValue.replace(/\s+/g,' '),parentPath:elementPath(parent),rects,style:{fontFamily:cs.fontFamily,fontSize:cs.fontSize,fontWeight:cs.fontWeight,fontStyle:cs.fontStyle,lineHeight:cs.lineHeight,letterSpacing:cs.letterSpacing,color:cs.color,textDecorationLine:cs.textDecorationLine,verticalAlign:cs.verticalAlign,whiteSpace:cs.whiteSpace}});
  }
  const images=[...doc.images].map(img=>{const r=img.getBoundingClientRect(),cs=win.getComputedStyle(img);return{path:elementPath(img),src:img.currentSrc||img.src,alt:img.alt||'',naturalWidth:img.naturalWidth,naturalHeight:img.naturalHeight,rect:rectObject(r,win),style:styleSnapshot(cs)}});
  const backgrounds=elements.filter(e=>e.style.backgroundImage&&e.style.backgroundImage!=='none').map(e=>({path:e.path,rect:e.rect,backgroundImage:e.style.backgroundImage,backgroundSize:e.style.backgroundSize,backgroundPosition:e.style.backgroundPosition}));
  return{version:VERSION,viewport:{width:frame.clientWidth,height:frame.clientHeight},document:{width:Math.max(doc.documentElement.scrollWidth,doc.body.scrollWidth),height:Math.max(doc.documentElement.scrollHeight,doc.body.scrollHeight),title:doc.title},counts:{elements:elements.length,textRuns:textRuns.length,images:images.length,backgrounds:backgrounds.length},elements,textRuns,images,backgrounds};
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
  const snapshot=captureSnapshot(frame);snapshot.unresolvedResources=built.unresolved;return snapshot;
}
global.WebRenderedCoreV4=Object.freeze({VERSION,normalizePath,collectDirectoryFiles,findHtmlPath,buildRenderableHtml,renderPackage,captureSnapshot,cleanup:cleanupUrls});
})(window);
