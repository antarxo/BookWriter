(function(global){
'use strict';
const VERSION='1.2.0-overflow-page-diagnostics';
const clone=v=>v===undefined?undefined:JSON.parse(JSON.stringify(v));
const finite=v=>Number.isFinite(Number(v));
const px=v=>finite(v)?Number(v):0;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function sameMarks(a,b){return['bold','italic','underline','superscript','subscript','color','highlight'].every(k=>(a?.[k]??null)===(b?.[k]??null))}
function textAtom(node,text){const out={type:'text_run',text};for(const k of['bold','italic','underline','superscript','subscript','color','highlight'])if(node?.[k]!==undefined)out[k]=node[k];return out}
function explodeInlineNode(node){if(!node||typeof node!=='object')return[];if(node.type==='text_run'){const parts=String(node.text||'').match(/\S+(?:\s+|$)|\s+/gu)||[];return parts.map(t=>textAtom(node,t))}if(node.type==='line_break'||node.type==='math_inline')return[clone(node)];if(node.type==='link'){const out=[];for(const child of node.children||[])for(const atom of explodeInlineNode(child))out.push({type:'link',href:String(node.href||''),...(node.target?{target:node.target}:{}),children:[atom]});return out}return[]}
function explodeRich(rich){const out=[];for(const node of rich?.nodes||[])out.push(...explodeInlineNode(node));return out}
function trimAtoms(atoms,start=false,end=false){const out=clone(atoms||[]),trim=(node,mode)=>{if(node?.type==='text_run')node.text=mode==='start'?node.text.replace(/^\s+/u,''):node.text.replace(/\s+$/u,'');if(node?.type==='link'&&node.children?.length)trim(node.children[mode==='start'?0:node.children.length-1],mode)};if(start&&out.length)trim(out[0],'start');if(end&&out.length)trim(out.at(-1),'end');return out.filter(n=>n.type!=='text_run'||n.text!=='').filter(n=>n.type!=='link'||(n.children||[]).some(c=>c.type!=='text_run'||c.text!==''))}
function collapseAtoms(atoms){const out=[];for(const raw of atoms||[]){const a=clone(raw),last=out.at(-1);if(a.type==='text_run'&&last?.type==='text_run'&&sameMarks(a,last)){last.text+=a.text;continue}if(a.type==='link'&&last?.type==='link'&&a.href===last.href&&(a.target||'')===(last.target||'')){const child=a.children?.[0],prev=last.children?.at(-1);if(child?.type==='text_run'&&prev?.type==='text_run'&&sameMarks(child,prev))prev.text+=child.text;else if(child)last.children.push(child);continue}out.push(a)}return{format:'rich-text-v1',nodes:out}}
function splitRich(rich,count){const atoms=explodeRich(rich),n=Math.max(0,Math.min(atoms.length,Number(count)||0));return{first:collapseAtoms(trimAtoms(atoms.slice(0,n),false,true)),rest:collapseAtoms(trimAtoms(atoms.slice(n),true,false)),units:atoms.length}}
const richUnits=rich=>explodeRich(rich).length;
const isHeading=item=>['hero','part_title','section_heading'].includes(item?.type);
const isHardBreak=item=>!!(item?.extensions?.paginationHardBreakBefore||item?.style?.pageBreakBefore);
function splittableUnits(item){if(['paragraph','note'].includes(item?.type))return richUnits(item.body);if(item?.type==='side_note'&&String(item?.layout?.placement||'wide')==='wide')return richUnits(item.body);if(item?.type==='list')return(item.items||[]).length;if(item?.type==='table')return(item.rows||[]).length;return 0}
const continuationId=(item,part)=>`${item.id}--cont-${part}`;
function splitItem(item,count,part=2){
  const total=splittableUnits(item),n=Math.max(0,Math.min(total,Number(count)||0));
  if(n<=0||n>=total)return null;
  const first=clone(item),rest=clone(item);
  if(['paragraph','note','side_note'].includes(item.type)){
    const x=splitRich(item.body,n);first.body=x.first;rest.body=x.rest;
  }else if(item.type==='list'){
    first.items=clone((item.items||[]).slice(0,n));rest.items=clone((item.items||[]).slice(n));rest.start=item.ordered?(Number(item.start)||1)+n:(Number(item.start)||1);
  }else if(item.type==='table'){
    first.rows=clone((item.rows||[]).slice(0,n));rest.rows=clone((item.rows||[]).slice(n));
    const repeat=Math.max(0,Number(item?.style?.headerRows)||0);
    if(repeat&&rest.rows.length){const headers=clone((item.rows||[]).slice(0,Math.min(repeat,n)));rest.rows=[...headers,...rest.rows];rest.extensions={...(rest.extensions||{}),paginationRepeatedHeaderRows:headers.length};}
  }else return null;
  rest.id=continuationId(item,part);rest.nav={...(rest.nav||{}),show:false,label:''};
  first.extensions={...(first.extensions||{}),paginationOriginId:item.extensions?.paginationOriginId||item.id,paginationPart:item.extensions?.paginationPart||1};
  rest.extensions={...(rest.extensions||{}),paginationOriginId:item.extensions?.paginationOriginId||item.id,paginationPart:part};
  delete rest.extensions.paginationHardBreakBefore;if(rest.style)delete rest.style.pageBreakBefore;
  return{first,rest,total};
}
function sourceRange(items){const pages=(items||[]).map(i=>Number(i?.sourceRef?.sourcePage)).filter(Number.isFinite);if(!pages.length)return null;const a=Math.min(...pages),b=Math.max(...pages);return a===b?a:`${a}-${b}`}
function pageTemplate(section,items,index){const p=clone(section);p.id=index===0?section.id:`${section.id}-r${index+1}`;p.items=items;p.sourcePage=sourceRange(items);p.extensions={...(p.extensions||{}),paginationGenerated:true,paginationPagePart:index+1};return p}
async function waitAssets(root,timeout=2200){try{if(document.fonts?.ready)await Promise.race([document.fonts.ready,sleep(timeout)])}catch{}const tasks=[...root.querySelectorAll('img')].map(img=>new Promise(resolve=>{if(img.complete){resolve();return}img.addEventListener('load',resolve,{once:true});img.addEventListener('error',resolve,{once:true});setTimeout(resolve,timeout)}));await Promise.all(tasks);await new Promise(r=>requestAnimationFrame(r))}
async function preloadAssets(book,options={}){try{if(document.fonts?.ready)await Promise.race([document.fonts.ready,sleep(options.assetTimeout||2200)])}catch{}const urls=new Set();for(const page of book.pages||[])for(const item of page.items||[])if(item?.type==='figure'&&item.src){const c=(options.imageCandidates||((s)=>s?[s]:[]))(item.src,item)||[];if(c[0])urls.add(c[0])}await Promise.all([...urls].map(url=>new Promise(resolve=>{const img=new Image(),done=()=>resolve();img.onload=done;img.onerror=done;img.src=url;if(img.complete)resolve();setTimeout(resolve,options.assetTimeout||2200)})));await new Promise(r=>requestAnimationFrame(r));options.assetsReady=true}
function makeHost(){const host=document.createElement('div');Object.assign(host.style,{position:'fixed',left:'-100000px',top:'0',width:'2000px',height:'2000px',overflow:'visible',opacity:'0',pointerEvents:'none',zIndex:'-2147483647'});document.body.appendChild(host);return host}
const availableHeight=book=>Math.max(1,px(book.layoutDefaults?.pageHeightPx||1123)-px(book.layoutDefaults?.pagePaddingTopPx||54)-px(book.layoutDefaults?.pagePaddingBottomPx||54));
function renderProbe(book,page,items,options,host,tall=false){host.innerHTML='';const data=clone(book),probe=clone(page);probe.items=clone(items);if(tall)data.layoutDefaults={...(data.layoutDefaults||{}),pageHeightPx:Math.max(200000,availableHeight(data)*Math.max(10,items.length*2)),pagePaddingBottomPx:0};data.pages=[probe];const node=BookCore.renderPageNode(data,probe,0,{lang:'el',preview:false,imageCandidates:options.imageCandidates,sceneSource:options.sceneSource||(()=>''),pageNumber:1});node.style.setProperty('--screen-scale','1');host.appendChild(node);return{node,data}}
function visualExtent(body){
  if(!body)return{bottom:Infinity,culprit:null};
  const bodyRect=body.getBoundingClientRect(),bodyTop=bodyRect.top;
  const flowBottom=Math.max(body.scrollHeight,bodyRect.height);
  let deepestBottom=-Infinity,culprit=null;
  for(const child of body.children){
    const rect=child.getBoundingClientRect();
    if(!rect.width&&!rect.height)continue;
    const style=getComputedStyle(child),marginBottom=parseFloat(style.marginBottom)||0;
    const candidate=rect.bottom-bodyTop+marginBottom;
    if(candidate>deepestBottom){
      deepestBottom=candidate;
      culprit={
        itemId:child.dataset.bookItemId||'',
        itemType:child.dataset.bookItemType||'',
        itemIndex:Number.isFinite(Number(child.dataset.bookItemIndex))?Number(child.dataset.bookItemIndex):null,
        cssFloat:style.cssFloat||'none',
        className:String(child.className||''),
        bottomPx:candidate
      };
    }
  }
  return{bottom:Math.max(flowBottom,deepestBottom),culprit};
}
async function measure(book,page,items,options,host){
  const {node}=renderProbe(book,page,items,options,host,false);
  if(!options.assetsReady)await waitAssets(node,options.assetTimeout||1800);
  const body=node.querySelector('.sheet-body'),flowUsed=body?Math.max(body.scrollHeight,body.getBoundingClientRect().height):Infinity,visual=visualExtent(body),used=Math.max(flowUsed,visual.bottom),available=availableHeight(book),overflow=Math.max(0,used-available),culprit=visual.culprit;
  const floatCulprit=!!culprit&&(culprit.cssFloat&&culprit.cssFloat!=='none'||/\bfloat-(?:left|right)\b/.test(culprit.className));
  return{fits:overflow<=Number(options.tolerancePx??1),used,flowUsed,visualUsed:visual.bottom,available,overflow,overflowKind:floatCulprit?'float-bottom':'document-flow',culprit};
}
async function estimateHeights(book,section,options,host){const items=section.items||[];if(!items.length)return[];const {node}=renderProbe(book,section,items,options,host,true);await waitAssets(node,options.assetTimeout||1800);const body=node.querySelector('.sheet-body'),children=[...(body?.children||[])];const bodyTop=body?.getBoundingClientRect().top||0,scroll=Math.max(body?.scrollHeight||0,body?.getBoundingClientRect().height||0),tops=children.map(x=>x.getBoundingClientRect().top-bodyTop);return items.map((item,i)=>{const own=children[i]?.getBoundingClientRect().height||1,next=i+1<tops.length?tops[i+1]:scroll;return Math.max(1,next-tops[i],own)})}
async function fitOversizeFigure(book,page,item,options,host){
  if(item?.type!=='figure'||!item.caption)return null;
  const current=Math.max(80,Number(item?.layout?.widthPx)||340),content=Math.max(current,px(book.layoutDefaults?.pageWidthPx||794)-px(book.layoutDefaults?.pagePaddingLeftPx||54)-px(book.layoutDefaults?.pagePaddingRightPx||54));
  for(const factor of[1.2,1.4,1.65,2]){
    const width=Math.min(content,Math.round(current*factor));if(width<=current)continue;
    const candidate=clone(item);candidate.layout={...(candidate.layout||{}),widthPx:width};
    const m=await measure(book,page,[candidate],options,host);if(m.fits){candidate.extensions={...(candidate.extensions||{}),paginationAdjustedFigureWidthFrom:current,paginationAdjustedFigureWidthTo:width};return candidate;}
    if(width>=content)break;
  }
  return null;
}
async function maxSplitThatFits(book,page,base,item,options,host,part){const total=splittableUnits(item);if(total<2)return null;let lo=1,hi=total-1,best=null;while(lo<=hi){const mid=(lo+hi)>>1,pair=splitItem(item,mid,part);if(!pair){hi=mid-1;continue}const m=await measure(book,page,[...base,pair.first],options,host);if(m.fits){best=pair;lo=mid+1}else hi=mid-1}if(!best)return null;const min=['paragraph','note','side_note'].includes(item.type)?Math.min(3,total-1):1;const kept=splittableUnits(best.first);return kept>=min?best:null}
function initialChunks(items,heights,available){const chunks=[];let current=[],used=0;const finish=()=>{if(!current.length)return;chunks.push(current);current=[];used=0};for(let i=0;i<items.length;i++){const item=clone(items[i]),h=Math.max(1,Number(heights[i])||1);if(isHardBreak(item)&&current.length)finish();if(current.length&&used+h>available){if(isHeading(current.at(-1))){if(current.length>1){const heading=current.pop();finish();current=[heading];used=Math.max(1,Number(heights[i-1])||1)}else{current.push(item);used+=h;continue}}else finish()}current.push(item);used+=h}finish();return chunks}
function ensureNext(chunks,index){if(!chunks[index+1])chunks[index+1]=[];return chunks[index+1]}
async function repairChunks(book,section,chunks,options,host,report){let i=0;const finalMeasures=[];while(i<chunks.length){if(!chunks[i]?.length){chunks.splice(i,1);continue}const m=await measure(book,section,chunks[i],options,host);if(m.fits){finalMeasures[i]=m;i++;continue}const items=chunks[i];if(items.length===1){const item=items[0],adjusted=await fitOversizeFigure(book,section,item,options,host);if(adjusted){chunks[i]=[adjusted];report.resizedFigures++;continue}const part=Number(item?.extensions?.paginationPart||1)+1,pair=await maxSplitThatFits(book,section,[],item,options,host,part);if(pair){chunks[i]=[pair.first];ensureNext(chunks,i).unshift(pair.rest);report.splitItems++;report.splitByType[item.type]=(report.splitByType[item.type]||0)+1;continue}report.oversizeItems.push({id:item.id,type:item.type,overflowPx:Math.round(m.overflow*10)/10});finalMeasures[i]=m;i++;continue}
    const last=items.at(-1),prev=items.at(-2);
    if(items.length===2&&isHeading(prev)){const part=Number(last?.extensions?.paginationPart||1)+1,pair=await maxSplitThatFits(book,section,[prev],last,options,host,part);if(pair){chunks[i]=[prev,pair.first];ensureNext(chunks,i).unshift(pair.rest);report.splitItems++;report.splitByType[last.type]=(report.splitByType[last.type]||0)+1;continue}}
    const moved=[items.pop()];if(items.length&&isHeading(items.at(-1)))moved.unshift(items.pop());ensureNext(chunks,i).unshift(...moved)
  }return finalMeasures}
async function paginateSection(book,section,options,host,report){const heights=await estimateHeights(book,section,options,host),chunks=initialChunks(section.items||[],heights,availableHeight(book));await repairChunks(book,section,chunks,options,host,report);return chunks.filter(x=>x.length).map((items,index)=>pageTemplate(section,items,index))}
function coalesceDocxFlow(input){
  const book=clone(input),tocItems=[],bodyItems=[];let tocPage=null,bodyPage=null;
  for(const page of book.pages||[]){
    const isToc=page?.extensions?.paginationSection==='toc'||(page.items||[]).some(i=>i?.extensions?.generatedActiveToc||i?.sourceRef?.kind==='generated-toc');
    if(isToc){tocPage=tocPage||clone(page);tocItems.push(...clone(page.items||[]));}
    else{bodyPage=bodyPage||clone(page);bodyItems.push(...clone(page.items||[]));}
  }
  const pages=[];
  if(tocItems.length){tocPage.id='contents-flow';tocPage.items=tocItems;tocPage.extensions={...(tocPage.extensions||{}),paginationSection:'toc',paginationCoalesced:true};pages.push(tocPage);}
  if(bodyItems.length){bodyPage=bodyPage||{id:'document-flow',items:[]};bodyPage.id='document-flow';bodyPage.items=bodyItems;bodyPage.sourcePage=sourceRange(bodyItems);bodyPage.extensions={...(bodyPage.extensions||{}),paginationSection:'document-flow',paginationCoalesced:true};pages.push(bodyPage);}
  book.pages=pages.length?pages:book.pages;
  return book;
}
async function auditOverflow(book,options={}){
  if(!global.BookCore)throw Error('BookCore is required for pagination audit.');
  await preloadAssets(book,options);
  const host=makeHost(),pages=[];
  try{
    for(let i=0;i<(book.pages||[]).length;i++){
      const p=book.pages[i],m=await measure(book,p,p.items||[],options,host);
      if(!m.fits)pages.push({
        pageId:p.id,
        pageIndex:i,
        displayPage:i+1,
        sourcePage:p.sourcePage??null,
        overflowPx:Math.round(m.overflow*10)/10,
        usedPx:Math.round(m.used*10)/10,
        flowUsedPx:Math.round(m.flowUsed*10)/10,
        visualUsedPx:Math.round(m.visualUsed*10)/10,
        availablePx:Math.round(m.available*10)/10,
        overflowKind:m.overflowKind,
        bottomItemId:m.culprit?.itemId||'',
        bottomItemType:m.culprit?.itemType||'',
        bottomItemIndex:m.culprit?.itemIndex,
        bottomItemFloat:m.culprit?.cssFloat||'none'
      });
    }
    return{ok:pages.length===0,pagesChecked:(book.pages||[]).length,overflowPages:pages.length,pages,rendererVersion:BookCore.VERSION,paginationVersion:VERSION,auditTolerancePx:Number(options.tolerancePx??1)};
  }finally{host.remove()}
}
async function paginateBook(input,options={}){if(!global.BookCore)throw Error('BookCore is required for canonical pagination.');const book=clone(input);await preloadAssets(book,options);const host=makeHost(),report={version:VERSION,startedAt:new Date().toISOString(),sourcePages:(book.pages||[]).length,outputPages:0,splitItems:0,splitByType:{},resizedFigures:0,oversizeItems:[],hardBreaks:0,rendererVersion:BookCore.VERSION};try{const generated=[];for(const section of book.pages||[]){report.hardBreaks+=(section.items||[]).filter(isHardBreak).length;generated.push(...await paginateSection(book,section,options,host,report))}book.pages=generated;book.meta={...(book.meta||{}),authoringVersion:'bookwriter-4.5.0-rc1',updatedAt:new Date().toISOString()};report.outputPages=generated.length;report.completedAt=new Date().toISOString();const audit=await auditOverflow(book,options);report.audit=audit;report.pagination={version:VERSION,generatedAt:new Date().toISOString(),sourcePages:report.sourcePages,outputPages:generated.length,splitItems:report.splitItems,splitByType:report.splitByType,resizedFigures:report.resizedFigures,oversizeItems:report.oversizeItems,hardBreaks:report.hardBreaks,rendererVersion:BookCore.VERSION,audit};if(!audit.ok&&options.rejectOverflow===true)throw Object.assign(new Error(`Η σελιδοποίηση άφησε ${audit.overflowPages} σελίδες με υπερχείλιση.`),{paginationReport:report});return{book,report,audit}}finally{host.remove()}}
global.BookPaginationV4=Object.freeze({VERSION,paginateBook,auditOverflow,coalesceDocxFlow,splitRich,splitItem,splittableUnits});
})(window);
