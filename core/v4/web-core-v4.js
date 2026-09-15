'use strict';
(function(global){
const CONVERTER='bookwriter-4.5.0-rc1-web';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const textFromHtml=html=>{const d=document.createElement('div');d.innerHTML=String(html||'');return(d.textContent||'').replace(/\s+/g,' ').trim()};
const safeName=value=>String(value||'web-image').split(/[?#]/)[0].split('/').pop().replace(/[^A-Za-z0-9._-]+/g,'_')||'web-image.bin';
let localPackage=null;
let pendingLocalHtmlFile=null;
let activePreviewUrls=[];
function clearPreviewUrls(){for(const url of activePreviewUrls){try{URL.revokeObjectURL(url)}catch{}}activePreviewUrls=[]}
function normalizeLocalPath(value=''){
  let raw=String(value||'').trim().replace(/\\/g,'/').split('#')[0].split('?')[0];
  try{raw=decodeURIComponent(raw)}catch{}
  raw=raw.normalize('NFC').replace(/^\.\//,'').replace(/^\/+/, '');
  const out=[];
  for(const part of raw.split('/')){
    if(!part||part==='.')continue;
    if(part==='..')out.pop();
    else out.push(part);
  }
  return out.join('/');
}
const foldPath=value=>normalizeLocalPath(value).toLocaleLowerCase('el-GR');
const basename=value=>normalizeLocalPath(value).split('/').pop()||'';
async function collectDirectoryFiles(handle,prefix='',map=new Map()){
  for await(const[name,entry]of handle.entries()){
    const path=prefix?prefix+'/'+name:name;
    if(entry.kind==='file')map.set(normalizeLocalPath(path),await entry.getFile());
    else if(entry.kind==='directory')await collectDirectoryFiles(entry,path,map);
  }
  return map;
}
function joinLocalPath(base='',relative=''){
  const rel=String(relative||'').trim();
  if(!rel)return'';
  if(/^(?:[a-z]+:|\/\/|#)/i.test(rel))return rel;
  const prefix=normalizeLocalPath(base).split('/').slice(0,-1).join('/');
  return normalizeLocalPath((prefix?prefix+'/':'')+rel);
}
function selectedHtmlPath(files,fileName=''){
  const wanted=String(fileName||'').normalize('NFC').toLocaleLowerCase('el-GR');
  const root=[...files.keys()].find(path=>!path.includes('/')&&basename(path).toLocaleLowerCase('el-GR')===wanted);
  if(root)return root;
  return [...files.keys()].find(path=>basename(path).toLocaleLowerCase('el-GR')===wanted)||'';
}
function packageFileFor(src){
  if(!localPackage||!src)return null;
  const joined=joinLocalPath(localPackage.htmlPath,src);
  if(/^(?:[a-z]+:|\/\/|#)/i.test(joined))return null;
  const entries=[...localPackage.files.entries()];
  const exact=localPackage.files.get(normalizeLocalPath(joined));
  if(exact)return exact;
  const folded=foldPath(joined);
  const caseInsensitive=entries.find(([path])=>foldPath(path)===folded)?.[1];
  if(caseInsensitive)return caseInsensitive;
  const wantedBase=basename(src).toLocaleLowerCase('el-GR');
  if(!wantedBase)return null;
  const htmlBase=basename(localPackage.htmlPath).replace(/\.html?$/i,'');
  const companionPrefix=(htmlBase+'_files/').normalize('NFC').toLocaleLowerCase('el-GR');
  const companionMatches=entries.filter(([path])=>foldPath(path).startsWith(companionPrefix)&&basename(path).toLocaleLowerCase('el-GR')===wantedBase);
  if(companionMatches.length===1)return companionMatches[0][1];
  const basenameMatches=entries.filter(([path])=>basename(path).toLocaleLowerCase('el-GR')===wantedBase);
  return basenameMatches.length===1?basenameMatches[0][1]:null;
}
function filterStyle(style=''){
  const allowed=new Set(['font-weight','font-style','text-decoration','vertical-align','color','background-color','background','text-align']);
  return String(style||'').split(';').map(x=>x.trim()).filter(part=>allowed.has(part.split(':')[0]?.trim().toLowerCase())).join(';');
}
function absoluteUrl(value='',base=''){
  const raw=String(value||'').trim();
  if(!raw)return'';
  if(/^(?:data:|blob:|web\/)/i.test(raw))return raw;
  try{return new URL(raw,base||global.location?.href||'http://bookwriter.local/').href}catch{return raw}
}
function cleanHtml(html='',baseUrl=''){
  const template=document.createElement('template');
  template.innerHTML=String(html||'');
  template.content.querySelectorAll('script,style,iframe,object,embed,canvas,svg,nav,header,footer,form,noscript').forEach(node=>node.remove());
  template.content.querySelectorAll('*').forEach(node=>{
    [...node.attributes].forEach(attr=>{
      const name=attr.name.toLowerCase(),value=String(attr.value||'').trim();
      if(name.startsWith('on')||['srcdoc','contenteditable'].includes(name))node.removeAttribute(attr.name);
      if((name==='href'||name==='src')&&/^javascript:/i.test(value))node.removeAttribute(attr.name);
      if(name==='style')node.setAttribute('style',filterStyle(value));
      if(name.startsWith('data-bw-'))node.removeAttribute(attr.name);
    });
    if(node.tagName==='A'&&node.getAttribute('href'))node.setAttribute('href',absoluteUrl(node.getAttribute('href'),baseUrl));
    if(node.tagName==='IMG'&&node.getAttribute('src'))node.setAttribute('src',absoluteUrl(node.getAttribute('src'),baseUrl));
  });
  return template.innerHTML;
}
async function fetchImageBlob(src){
  if(!src)return null;
  try{const response=await fetch(src,{mode:'cors',cache:'no-store'});if(!response.ok)return null;const blob=await response.blob();return String(blob.type||'').startsWith('image/')?blob:null}catch{return null}
}
function imageExtension(blob,name=''){
  const ext=String(name||'').match(/\.[A-Za-z0-9]{2,5}$/)?.[0];
  if(ext)return ext.toLowerCase();
  const kind=String(blob?.type||'').split('/')[1]?.replace(/[^A-Za-z0-9]/g,'')||'png';
  return'.'+kind;
}
function uniqueAssetPath(raw,blob,usedNames){
  let name=safeName(raw),ext=imageExtension(blob,name);
  if(!/\.[A-Za-z0-9]{2,5}$/.test(name))name+=ext;
  let candidate=name,n=2;
  while(usedNames.has(candidate.toLocaleLowerCase())){
    const dot=name.lastIndexOf('.'),stem=dot>0?name.slice(0,dot):name,suffix=dot>0?name.slice(dot):ext;
    candidate=stem+'-'+(n++)+suffix;
  }
  usedNames.add(candidate.toLocaleLowerCase());
  return'web/'+candidate;
}
async function prepareImageAssets(doc,baseUrl=''){
  const imageBlobs=new Map(),usedImages=[],skippedImages=[],cache=new Map(),usedNames=new Set();
  for(const img of [...doc.querySelectorAll('img[src]')]){
    const raw=String(img.getAttribute('src')||'').trim();
    if(!raw)continue;
    let record=cache.get(raw);
    if(!record){
      let blob=null;
      const local=!/^(?:data:|blob:|https?:|file:|\/\/)/i.test(raw)?packageFileFor(raw):null;
      if(local)blob=local;
      else blob=await fetchImageBlob(absoluteUrl(raw,baseUrl));
      if(blob){
        const path=uniqueAssetPath(raw,blob,usedNames);
        imageBlobs.set(path,blob);
        usedImages.push({path,bytes:blob.size,type:blob.type||'',source:raw});
        record={path,blob};
      }else{
        skippedImages.push(raw);
        record={path:'',blob:null};
      }
      cache.set(raw,record);
    }
    if(record.path){
      img.dataset.bwAssetPath=record.path;
      const preview=URL.createObjectURL(record.blob);activePreviewUrls.push(preview);img.setAttribute('src',preview);
    }else img.dataset.bwMissing='1';
  }
  return{imageBlobs,usedImages,skippedImages};
}
function imageInfo(img){
  const srcPath=String(img?.dataset?.bwAssetPath||'');if(!srcPath)return null;
  return{srcPath,alt:img.getAttribute('alt')||'',width:Number(img.getAttribute('width'))||undefined,height:Number(img.getAttribute('height'))||undefined,kind:'web-image'};
}
function htmlWithoutImages(node,baseUrl=''){
  const clone=node.cloneNode(true);clone.querySelectorAll('img').forEach(img=>img.remove());return cleanHtml(clone.innerHTML,baseUrl);
}
function blockText(block){return block?.type==='figure'?block.caption||block.alt||block.srcPath:block?.type==='list'?(block.items||[]).map(x=>textFromHtml(x.html)).join(' · '):block?.type==='table'?(block.rows||[]).map(r=>(r.cells||[]).map(c=>textFromHtml(c.html)).join(' | ')).join(' · '):block?.title||textFromHtml(block?.html||'')}
function entryLabel(entry){const b=entry.block,kind=entry.heading?'H'+(b.level||1):b.type==='figure'?'Σχήμα':b.type==='table'?'Πίνακας':b.type==='list'?'Λίστα':'¶';let text=blockText(b)||'(κενό)';if(text.length>105)text=text.slice(0,102)+'…';return'web · '+kind+' · '+text}
function pushFigureFromImg(blocks,img,sourceIndex,caption=''){
  const info=imageInfo(img);if(!info)return;
  blocks.push({type:'figure',srcPath:info.srcPath,caption:caption||info.alt,alt:info.alt||caption,width:info.width,height:info.height,kind:'web-image',sourceStyle:'img',sourceParagraph:sourceIndex});
}
function pushParagraph(blocks,node,baseUrl,sourceIndex){
  const images=[...node.querySelectorAll('img')];
  const html=images.length?htmlWithoutImages(node,baseUrl):cleanHtml(node.innerHTML,baseUrl);
  if(textFromHtml(html)||/<math/i.test(html))blocks.push({type:'paragraph',html,paragraphStyle:{},sourceStyle:node.tagName.toLowerCase(),sourceParagraph:sourceIndex});
  images.forEach(img=>pushFigureFromImg(blocks,img,sourceIndex));
}
function pushHeading(blocks,node,baseUrl,sourceIndex){const level=Math.max(1,Math.min(6,Number(node.tagName.slice(1))||2)),title=textFromHtml(node.innerHTML||node.textContent);if(title)blocks.push({type:level===1?'part_title':'section_heading',title,level:level===1?1:level,headingStyle:{},sourceStyle:node.tagName.toLowerCase(),sourceParagraph:sourceIndex})}
function pushList(blocks,node,baseUrl,sourceIndex){
  const ordered=node.tagName==='OL',images=[];
  const items=[...node.children].filter(child=>child.tagName==='LI').map((li,index)=>{images.push(...li.querySelectorAll('img'));return{html:htmlWithoutImages(li,baseUrl),level:0,value:ordered?(Number(li.getAttribute('value'))||index+1):undefined}}).filter(entry=>textFromHtml(entry.html));
  if(items.length)blocks.push({type:'list',listType:ordered?'ol':'ul',start:Number(node.getAttribute('start'))||1,items,paragraphStyle:{},sourceStyle:node.tagName.toLowerCase(),sourceParagraph:sourceIndex});
  images.forEach(img=>pushFigureFromImg(blocks,img,sourceIndex));
}
function pushTable(blocks,node,baseUrl,sourceIndex){
  const ownedRows=[...node.querySelectorAll('tr')].filter(tr=>tr.closest('table')===node);
  const rows=ownedRows.map(tr=>({cells:[...tr.children].filter(cell=>['TD','TH'].includes(cell.tagName)).map(cell=>{
    const images=[...cell.querySelectorAll('img')].map(imageInfo).filter(Boolean);
    return{html:cleanHtml(cell.innerHTML,baseUrl),colspan:Number(cell.getAttribute('colspan'))||1,rowspan:Number(cell.getAttribute('rowspan'))||1,style:cell.tagName==='TH'?{bold:true}:{},images};
  })})).filter(row=>row.cells.length);
  if(!rows.length)return;
  const columns=Math.max(1,...rows.map(row=>row.cells.reduce((sum,cell)=>sum+(Number(cell.colspan)||1),0)));
  blocks.push({type:'table',rows,columns,tableStyle:{headerRows:node.querySelector('thead')?1:0},sourceStyle:'table',sourceParagraph:sourceIndex});
}
function pushFigure(blocks,node,baseUrl,sourceIndex){const img=node.tagName==='IMG'?node:node.querySelector('img');if(!img)return;const caption=node.tagName==='FIGURE'?textFromHtml(node.querySelector('figcaption')?.innerHTML||''):'';pushFigureFromImg(blocks,img,sourceIndex,caption)}
function collectBlocks(doc,baseUrl=''){
  const source=doc.querySelector('article,main,[role="main"]')||doc.body;if(!source)return[];
  source.querySelectorAll('script,style,noscript,template,iframe,object,embed,canvas,svg,form,aside,.sidebar,.menu,.nav,.advertisement,.ads,[aria-hidden="true"]').forEach(node=>node.remove());
  const blocks=[];let index=0;
  const visit=node=>{
    if(node.nodeType!==1)return;const tag=node.tagName;index++;
    if(/^H[1-6]$/.test(tag)){pushHeading(blocks,node,baseUrl,index);return}
    if(tag==='P'||tag==='BLOCKQUOTE'){pushParagraph(blocks,node,baseUrl,index);return}
    if(tag==='UL'||tag==='OL'){pushList(blocks,node,baseUrl,index);return}
    if(tag==='TABLE'){pushTable(blocks,node,baseUrl,index);return}
    if(tag==='FIGURE'||tag==='IMG'){pushFigure(blocks,node,baseUrl,index);return}
    const direct=[...node.children].filter(child=>/^(H[1-6]|P|BLOCKQUOTE|UL|OL|TABLE|FIGURE|IMG|SECTION|ARTICLE|DIV)$/i.test(child.tagName));
    if(direct.length)direct.forEach(visit);else if(textFromHtml(node.innerHTML).length>25)pushParagraph(blocks,node,baseUrl,index);
  };
  [...source.children].forEach(visit);return blocks;
}
async function parseHtml(html='',options={}){
  clearPreviewUrls();
  const baseUrl=options.baseUrl||'',doc=new DOMParser().parseFromString(String(html||''),'text/html');
  const title=textFromHtml(doc.querySelector('title')?.innerHTML||doc.querySelector('h1')?.innerHTML||options.fileName||'Ιστοσελίδα');
  const images=await prepareImageAssets(doc,baseUrl);
  const blocks=collectBlocks(doc,baseUrl);
  localPackage=null;
  if(!blocks.length)throw Error('Δεν βρέθηκε καθαρό περιεχόμενο για εισαγωγή.');
  const pages=new Map([[1,blocks]]),rawImageRefs=blocks.reduce((sum,b)=>sum+(b.type==='figure'?1:b.type==='table'?(b.rows||[]).reduce((r,row)=>r+(row.cells||[]).reduce((c,cell)=>c+(cell.images||[]).length,0),0):0),0);
  return{sourceType:'web',converter:CONVERTER,fileName:options.fileName||baseUrl||'web-page.html',title,pageCount:1,pages,imageBlobs:images.imageBlobs,usedImages:images.usedImages,rawImageRefs,skippedImages:images.skippedImages,paras:blocks.filter(x=>x.type==='paragraph').length,lists:blocks.filter(x=>x.type==='list').length,tables:blocks.filter(x=>x.type==='table').length,mathCount:blocks.filter(x=>/<math/i.test(x.html||'')).length,importedMathObjects:0,mathDuplicatesSkipped:0,inlineMath:0,displayMath:0,textBoxes:0,textBoxesUnique:0,textBoxCaptions:0,textBoxesImported:0,textBoxesImportedCanonical:0,unsupportedMath:[],documentLayout:{source:{bodyFontFamily:'Calibri',bodyFontSize:14.6667},layoutDefaults:{bodyFontFamily:'Calibri',bodyFontSize:14.6667,lineHeight:1.25,paragraphGap:6}}};
}
async function parseUrl(url){localPackage=null;pendingLocalHtmlFile=null;const response=await fetch(url,{cache:'no-store'});if(!response.ok)throw Error(`Η ιστοσελίδα δεν διαβάστηκε: HTTP ${response.status}`);const html=await response.text();return parseHtml(html,{baseUrl:response.url||url,fileName:url})}
function flattenEntries(result){const out=[];if(!result)return out;for(let p=1;p<=result.pageCount;p++){const arr=result.pages.get(p)||[];for(let i=0;i<arr.length;i++){const block=arr[i];out.push({key:p+':'+i,page:p,blockIndex:i,block,type:block.type||'block',level:Number(block.level||0),heading:block.type==='part_title'||block.type==='section_heading',label:blockText(block)})}}return out}
function audit(result,entries){return{sourceFile:result.fileName,sourceType:'web',selectedBlocks:entries.length,paragraphs:result.paras,lists:result.lists,tables:result.tables,imagesImported:result.usedImages.length,imagesSkipped:result.skippedImages?.length||0,skippedImages:result.skippedImages||[],converter:CONVERTER,canonicalTarget:'bookwriter-v4'}}
async function grantFolderAccess(){
  if(!pendingLocalHtmlFile||typeof showDirectoryPicker!=='function')return;
  try{
    const handle=await showDirectoryPicker({mode:'read'}),files=await collectDirectoryFiles(handle),htmlPath=selectedHtmlPath(files,pendingLocalHtmlFile.name);
    if(!htmlPath){global.alert('Ο επιλεγμένος φάκελος δεν περιέχει το HTML «'+pendingLocalHtmlFile.name+'». Επίλεξε τον φάκελο όπου αποθηκεύτηκε η ιστοσελίδα.');return}
    localPackage={files,htmlPath};
    const input=document.querySelector('#insertWebFileInput'),button=document.querySelector('#insertWebFileButton');if(!input)throw Error('Δεν βρέθηκε το πεδίο εισαγωγής HTML.');
    if(button){button.textContent='HTML αρχείο…';button.title=''}
    const file=pendingLocalHtmlFile;pendingLocalHtmlFile=null;
    const transfer=new DataTransfer();transfer.items.add(file);input.files=transfer.files;
    if(typeof input.onchange==='function')input.onchange({target:input});
  }catch(error){if(error?.name!=='AbortError'){console.error('Local HTML folder access failed',error);global.alert('Αποτυχία πρόσβασης στον φάκελο της ιστοσελίδας: '+(error?.message||error))}}
}
function bindLocalFolderAccess(){
  const input=document.querySelector('#insertWebFileInput'),button=document.querySelector('#insertWebFileButton');if(!input||!button||typeof showDirectoryPicker!=='function')return;
  input.addEventListener('change',event=>{const file=event.target.files?.[0];if(!file||!/\.html?$/i.test(file.name))return;event.stopImmediatePropagation();pendingLocalHtmlFile=file;button.textContent='Πρόσβαση στον φάκελο…';button.title='Επίλεξε τον φάκελο όπου βρίσκονται το HTML και ο συνοδευτικός φάκελος εικόνων.'},true);
  button.addEventListener('click',async event=>{if(!pendingLocalHtmlFile)return;event.preventDefault();event.stopImmediatePropagation();await grantFolderAccess()},true);
}
global.WebCoreV4=Object.freeze({VERSION:CONVERTER,parseHtml,parseUrl,flattenEntries,entryLabel,blockText,audit});
bindLocalFolderAccess();
})(window);
