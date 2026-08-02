'use strict';
(function(global){
const CONVERTER='bookwriter-4.5.0-rc1-web';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const textFromHtml=html=>{const d=document.createElement('div');d.innerHTML=String(html||'');return(d.textContent||'').replace(/\s+/g,' ').trim()};
const safeName=value=>String(value||'web-image').split(/[?#]/)[0].split('/').pop().replace(/[^A-Za-z0-9._-]+/g,'_')||'web-image.bin';
function cleanHtml(html='',baseUrl=''){
  const template=document.createElement('template');
  template.innerHTML=String(html||'');
  template.content.querySelectorAll('script,style,iframe,object,embed,canvas,svg,nav,header,footer,form,noscript').forEach(node=>node.remove());
  template.content.querySelectorAll('*').forEach(node=>{
    [...node.attributes].forEach(attr=>{
      const name=attr.name.toLowerCase();
      const value=String(attr.value||'').trim();
      if(name.startsWith('on')||['srcdoc','contenteditable'].includes(name)) node.removeAttribute(attr.name);
      if((name==='href'||name==='src')&&/^javascript:/i.test(value)) node.removeAttribute(attr.name);
      if(name==='style') node.setAttribute('style',filterStyle(value));
    });
    if(node.tagName==='A'&&node.getAttribute('href')) node.setAttribute('href',absoluteUrl(node.getAttribute('href'),baseUrl));
    if(node.tagName==='IMG'&&node.getAttribute('src')) node.setAttribute('src',absoluteUrl(node.getAttribute('src'),baseUrl));
  });
  return template.innerHTML;
}
function filterStyle(style=''){
  const allowed=new Set(['font-weight','font-style','text-decoration','vertical-align','color','background-color','background']);
  return String(style||'').split(';').map(x=>x.trim()).filter(part=>allowed.has(part.split(':')[0]?.trim().toLowerCase())).join(';');
}
function absoluteUrl(value='',base=''){
  const raw=String(value||'').trim();
  if(!raw)return'';
  if(/^data:/i.test(raw))return raw;
  try{return new URL(raw,base||global.location?.href||'http://bookwriter.local/').href}catch{return raw}
}
function blockText(block){return block?.type==='figure'?block.caption||block.alt||block.srcPath:block?.type==='list'?(block.items||[]).map(x=>textFromHtml(x.html)).join(' · '):block?.type==='table'?(block.rows||[]).map(r=>(r.cells||[]).map(c=>textFromHtml(c.html)).join(' | ')).join(' · '):block?.title||textFromHtml(block?.html||'')}
function entryLabel(entry){
  const b=entry.block,kind=entry.heading?'H'+(b.level||1):b.type==='figure'?'Σχήμα':b.type==='table'?'Πίνακας':b.type==='list'?'Λίστα':'¶';
  let text=blockText(b)||'(κενό)';
  if(text.length>105)text=text.slice(0,102)+'…';
  return 'web · '+kind+' · '+text;
}
function pushParagraph(blocks,node,baseUrl,sourceIndex){
  const html=cleanHtml(node.innerHTML,baseUrl);
  if(!textFromHtml(html)&&!/<math|<img/i.test(html))return;
  blocks.push({type:'paragraph',html,paragraphStyle:{},sourceStyle:node.tagName.toLowerCase(),sourceParagraph:sourceIndex});
}
function pushHeading(blocks,node,baseUrl,sourceIndex){
  const level=Math.max(1,Math.min(6,Number(node.tagName.slice(1))||2));
  const title=textFromHtml(node.innerHTML||node.textContent);
  if(!title)return;
  blocks.push({type:level===1?'part_title':'section_heading',title,level:level===1?1:level,headingStyle:{},sourceStyle:node.tagName.toLowerCase(),sourceParagraph:sourceIndex});
}
function pushList(blocks,node,baseUrl,sourceIndex){
  const ordered=node.tagName==='OL';
  const items=[...node.children].filter(child=>child.tagName==='LI').map((li,index)=>({
    html:cleanHtml(li.innerHTML,baseUrl),
    level:0,
    value:ordered?(Number(li.getAttribute('value'))||index+1):undefined
  })).filter(entry=>textFromHtml(entry.html));
  if(!items.length)return;
  blocks.push({type:'list',listType:ordered?'ol':'ul',start:Number(node.getAttribute('start'))||1,items,paragraphStyle:{},sourceStyle:node.tagName.toLowerCase(),sourceParagraph:sourceIndex});
}
function pushTable(blocks,node,baseUrl,sourceIndex){
  const rows=[...node.querySelectorAll('tr')].map(tr=>({cells:[...tr.children].filter(cell=>['TD','TH'].includes(cell.tagName)).map(cell=>({html:cleanHtml(cell.innerHTML,baseUrl),colspan:Number(cell.getAttribute('colspan'))||1,rowspan:Number(cell.getAttribute('rowspan'))||1,style:cell.tagName==='TH'?{bold:true}:{} }))})).filter(row=>row.cells.length);
  if(!rows.length)return;
  const columns=Math.max(1,...rows.map(row=>row.cells.reduce((sum,cell)=>sum+(Number(cell.colspan)||1),0)));
  blocks.push({type:'table',rows,columns,tableStyle:{headerRows:node.querySelector('thead')?1:0},sourceStyle:'table',sourceParagraph:sourceIndex});
}
function pushFigure(blocks,node,baseUrl,sourceIndex){
  const img=node.tagName==='IMG'?node:node.querySelector('img');
  const src=absoluteUrl(img?.getAttribute('src')||'',baseUrl);
  if(!src)return;
  const caption=node.tagName==='FIGURE'?textFromHtml(node.querySelector('figcaption')?.innerHTML||''):(img.getAttribute('alt')||'');
  blocks.push({type:'figure',srcPath:src,caption,alt:img.getAttribute('alt')||caption,width:Number(img.getAttribute('width'))||undefined,height:Number(img.getAttribute('height'))||undefined,kind:'web-image',sourceStyle:'img',sourceParagraph:sourceIndex});
}
function collectBlocks(doc,baseUrl=''){
  const source=doc.querySelector('article,main,[role="main"]')||doc.body;
  if(!source)return[];
  source.querySelectorAll('aside,.sidebar,.menu,.nav,.advertisement,.ads,[aria-hidden="true"]').forEach(node=>node.remove());
  const blocks=[];
  let index=0;
  const visit=node=>{
    if(node.nodeType!==1)return;
    const tag=node.tagName;
    index++;
    if(/^H[1-6]$/.test(tag)){pushHeading(blocks,node,baseUrl,index);return}
    if(tag==='P'||tag==='BLOCKQUOTE'){pushParagraph(blocks,node,baseUrl,index);return}
    if(tag==='UL'||tag==='OL'){pushList(blocks,node,baseUrl,index);return}
    if(tag==='TABLE'){pushTable(blocks,node,baseUrl,index);return}
    if(tag==='FIGURE'||tag==='IMG'){pushFigure(blocks,node,baseUrl,index);return}
    const direct=[...node.children].filter(child=>/^(H[1-6]|P|BLOCKQUOTE|UL|OL|TABLE|FIGURE|IMG|SECTION|ARTICLE|DIV)$/i.test(child.tagName));
    if(direct.length)direct.forEach(visit);
    else if(textFromHtml(node.innerHTML).length>25)pushParagraph(blocks,node,baseUrl,index);
  };
  [...source.children].forEach(visit);
  return blocks;
}
async function fetchImageBlob(src){
  if(!src)return null;
  try{
    const response=await fetch(src,{mode:'cors',cache:'no-store'});
    if(!response.ok)return null;
    const blob=await response.blob();
    if(!String(blob.type||'').startsWith('image/'))return null;
    return blob;
  }catch{return null}
}
async function attachImages(blocks){
  const imageBlobs=new Map(),usedImages=[],skippedImages=[];
  for(const block of blocks.filter(x=>x.type==='figure')){
    const blob=await fetchImageBlob(block.srcPath);
    if(blob){
      let name=safeName(block.srcPath);
      if(!/\.[A-Za-z0-9]{2,5}$/.test(name)){
        const ext=(blob.type.split('/')[1]||'png').replace(/[^A-Za-z0-9]/g,'')||'png';
        name+='.'+ext;
      }
      block.srcPath='web/'+name;
      imageBlobs.set(block.srcPath,blob);
      usedImages.push({path:block.srcPath,bytes:blob.size,type:blob.type});
    }else{
      skippedImages.push(block.srcPath);
      block.skipFigure=true;
    }
  }
  return {imageBlobs,usedImages,skippedImages};
}
async function parseHtml(html='',options={}){
  const baseUrl=options.baseUrl||'';
  const doc=new DOMParser().parseFromString(String(html||''),'text/html');
  const title=textFromHtml(doc.querySelector('title')?.innerHTML||doc.querySelector('h1')?.innerHTML||options.fileName||'Ιστοσελίδα');
  let blocks=collectBlocks(doc,baseUrl).filter(block=>!block.skipFigure);
  const images=await attachImages(blocks);
  blocks=blocks.filter(block=>block.type!=='figure'||!block.skipFigure);
  if(!blocks.length)throw Error('Δεν βρέθηκε καθαρό περιεχόμενο για εισαγωγή.');
  const pages=new Map([[1,blocks]]);
  return {sourceType:'web',converter:CONVERTER,fileName:options.fileName||baseUrl||'web-page.html',title,pageCount:1,pages,imageBlobs:images.imageBlobs,usedImages:images.usedImages,rawImageRefs:blocks.filter(x=>x.type==='figure').length,skippedImages:images.skippedImages,paras:blocks.filter(x=>x.type==='paragraph').length,lists:blocks.filter(x=>x.type==='list').length,tables:blocks.filter(x=>x.type==='table').length,mathCount:blocks.filter(x=>/<math/i.test(x.html||'')).length,importedMathObjects:0,mathDuplicatesSkipped:0,inlineMath:0,displayMath:0,textBoxes:0,textBoxesUnique:0,textBoxCaptions:0,textBoxesImported:0,textBoxesImportedCanonical:0,unsupportedMath:[],documentLayout:{source:{bodyFontFamily:'Calibri',bodyFontSize:14.6667},layoutDefaults:{bodyFontFamily:'Calibri',bodyFontSize:14.6667,lineHeight:1.25,paragraphGap:6}}};
}
async function parseUrl(url){
  const response=await fetch(url,{cache:'no-store'});
  if(!response.ok)throw Error(`Η ιστοσελίδα δεν διαβάστηκε: HTTP ${response.status}`);
  const html=await response.text();
  return parseHtml(html,{baseUrl:response.url||url,fileName:url});
}
function flattenEntries(result){const out=[];if(!result)return out;for(let p=1;p<=result.pageCount;p++){const arr=result.pages.get(p)||[];for(let i=0;i<arr.length;i++){const block=arr[i];out.push({key:p+':'+i,page:p,blockIndex:i,block,type:block.type||'block',level:Number(block.level||0),heading:block.type==='part_title'||block.type==='section_heading',label:blockText(block)})}}return out}
function audit(result,entries){return{sourceFile:result.fileName,sourceType:'web',selectedBlocks:entries.length,paragraphs:result.paras,lists:result.lists,tables:result.tables,imagesImported:result.usedImages.length,imagesSkipped:result.skippedImages?.length||0,skippedImages:result.skippedImages||[],converter:CONVERTER,canonicalTarget:'bookwriter-v4'}}
global.WebCoreV4=Object.freeze({VERSION:CONVERTER,parseHtml,parseUrl,flattenEntries,entryLabel,blockText,audit});
})(window);
