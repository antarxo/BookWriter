(function(global){
'use strict';
const VERSION='1.4.7-hf13-multilevel-list-spanning-table-rows';
const clone=v=>v===undefined?undefined:JSON.parse(JSON.stringify(v));
const finite=v=>Number.isFinite(Number(v));
const px=v=>finite(v)?Number(v):0;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function sameMarks(a,b){return['bold','italic','underline','superscript','subscript','color','highlight','fontFamily','fontSizePx'].every(k=>(a?.[k]??null)===(b?.[k]??null))}
function textAtom(node,text){const out={type:'text_run',text};for(const k of['bold','italic','underline','superscript','subscript','color','highlight','fontFamily','fontSizePx'])if(node?.[k]!==undefined)out[k]=node[k];return out}
function explodeInlineNode(node){if(!node||typeof node!=='object')return[];if(node.type==='text_run'){const parts=String(node.text||'').match(/\S+(?:\s+|$)|\s+/gu)||[];return parts.map(t=>textAtom(node,t))}if(node.type==='line_break'||node.type==='math_inline')return[clone(node)];if(node.type==='link'){const out=[];for(const child of node.children||[])for(const atom of explodeInlineNode(child))out.push({type:'link',href:String(node.href||''),...(node.target?{target:node.target}:{}),children:[atom]});return out}return[]}
function explodeRich(rich){const out=[];for(const node of rich?.nodes||[])out.push(...explodeInlineNode(node));return out}
function trimAtoms(atoms,start=false,end=false){const out=clone(atoms||[]),trim=(node,mode)=>{if(node?.type==='text_run')node.text=mode==='start'?node.text.replace(/^\s+/u,''):node.text.replace(/\s+$/u,'');if(node?.type==='link'&&node.children?.length)trim(node.children[mode==='start'?0:node.children.length-1],mode)};if(start&&out.length)trim(out[0],'start');if(end&&out.length)trim(out.at(-1),'end');return out.filter(n=>n.type!=='text_run'||n.text!=='').filter(n=>n.type!=='link'||(n.children||[]).some(c=>c.type!=='text_run'||c.text!==''))}
function collapseAtoms(atoms){const out=[];for(const raw of atoms||[]){const a=clone(raw),last=out.at(-1);if(a.type==='text_run'&&last?.type==='text_run'&&sameMarks(a,last)){last.text+=a.text;continue}if(a.type==='link'&&last?.type==='link'&&a.href===last.href&&(a.target||'')===(last.target||'')){const child=a.children?.[0],prev=last.children?.at(-1);if(child?.type==='text_run'&&prev?.type==='text_run'&&sameMarks(child,prev))prev.text+=child.text;else if(child)last.children.push(child);continue}out.push(a)}return{format:'rich-text-v1',nodes:out}}
function splitRich(rich,count){const atoms=explodeRich(rich),n=Math.max(0,Math.min(atoms.length,Number(count)||0));return{first:collapseAtoms(trimAtoms(atoms.slice(0,n),false,true)),rest:collapseAtoms(trimAtoms(atoms.slice(n),true,false)),units:atoms.length}}
const richUnits=rich=>explodeRich(rich).length;
const isHeading=item=>['hero','part_title','section_heading'].includes(item?.type);
const isHardBreak=item=>!!(item?.extensions?.paginationHardBreakBefore||item?.style?.pageBreakBefore);
function isGeneratedSequencePair(a,b){
  return a?.type==='interactive_callout'
    && b?.type==='scene'
    && a?.extensions?.sequencePrintGenerated===true
    && b?.extensions?.sequencePrintGenerated===true
    && String(a?.extensions?.sourceCalloutId||'')===String(b?.extensions?.sourceCalloutId||'')
    && Number(a?.extensions?.sequenceStep||0)===Number(b?.extensions?.sequenceStep||0);
}
function paginationUnits(items=[]){
  const units=[];
  for(let i=0;i<items.length;i++){
    if(isGeneratedSequencePair(items[i],items[i+1])){
      units.push([items[i],items[i+1]]);
      i++;
    }else units.push([items[i]]);
  }
  return units;
}
const flattenUnits=units=>(units||[]).flatMap(unit=>Array.isArray(unit)?unit:[unit]);
function splittableUnits(item){if(['paragraph','note'].includes(item?.type))return richUnits(item.body);if(item?.type==='side_note'&&String(item?.layout?.placement||'wide')==='wide')return richUnits(item.body);if(item?.type==='list')return(item.items||[]).length;if(item?.type==='table')return(item.rows||[]).length;if(item?.type==='dialogue')return(item.rows||[]).length;return 0}
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
  }else if(item.type==='dialogue'){
    first.rows=clone((item.rows||[]).slice(0,n));
    rest.rows=clone((item.rows||[]).slice(n));
    rest.intro='';
  }else return null;
  rest.id=continuationId(item,part);rest.nav={...(rest.nav||{}),show:false,label:''};
  first.extensions={...(first.extensions||{}),paginationOriginId:item.extensions?.paginationOriginId||item.id,paginationPart:item.extensions?.paginationPart||1};
  rest.extensions={...(rest.extensions||{}),paginationOriginId:item.extensions?.paginationOriginId||item.id,paginationPart:part};
  delete rest.extensions.paginationHardBreakBefore;if(rest.style)delete rest.style.pageBreakBefore;
  return{first,rest,total};
}
function sourceRange(items){const pages=(items||[]).map(i=>Number(i?.sourceRef?.sourcePage)).filter(Number.isFinite);if(!pages.length)return null;const a=Math.min(...pages),b=Math.max(...pages);return a===b?a:`${a}-${b}`}
function pageTemplate(section,items,index){const p=clone(section);p.id=index===0?section.id:`${section.id}-r${index+1}`;p.items=items;p.sourcePage=sourceRange(items);p.extensions={...(p.extensions||{}),paginationGenerated:true,paginationPagePart:index+1};return p}
function ensureUniquePageIds(pages=[]){const used=new Set();return pages.map((page,index)=>{const next=clone(page),base=String(next.id||`page-${index+1}`);let id=base,n=2;while(used.has(id))id=`${base}-p${n++}`;used.add(id);next.id=id;return next})}
async function waitAssets(root,timeout=2200){try{if(document.fonts?.ready)await Promise.race([document.fonts.ready,sleep(timeout)])}catch{}const tasks=[...root.querySelectorAll('img')].map(img=>new Promise(resolve=>{if(img.complete){resolve();return}img.addEventListener('load',resolve,{once:true});img.addEventListener('error',resolve,{once:true});setTimeout(resolve,timeout)}));await Promise.all(tasks);await new Promise(r=>requestAnimationFrame(r))}
function collectItemImageSources(item,urls,options){
  if(!item||typeof item!=='object')return;
  const add=(src,meta)=>{if(!src)return;const candidates=(options.imageCandidates||((s)=>s?[s]:[]))(src,meta)||[];for(const url of candidates)if(url)urls.add(url)};
  if(item.type==='figure')add(item.src,item);
  if(item.type==='table')for(const row of item.rows||[])for(const cell of row.cells||[])for(const media of cell.media||[])add(media.src,media);
  if(item.type==='columns')for(const region of item.regions||[])for(const child of region.items||[])collectItemImageSources(child,urls,options);
}
async function preloadAssets(book,options={}){try{if(document.fonts?.ready)await Promise.race([document.fonts.ready,sleep(options.assetTimeout||2200)])}catch{}const urls=new Set();for(const page of book.pages||[])for(const item of page.items||[])collectItemImageSources(item,urls,options);await Promise.all([...urls].map(url=>new Promise(resolve=>{const img=new Image(),done=()=>resolve();img.onload=done;img.onerror=done;img.src=url;if(img.complete)resolve();setTimeout(resolve,options.assetTimeout||2200)})));await new Promise(r=>requestAnimationFrame(r));options.assetsReady=true;options.preloadedImageCount=urls.size}
function makeHost(){const host=document.createElement('div');Object.assign(host.style,{position:'fixed',left:'-100000px',top:'0',width:'2000px',height:'2000px',overflow:'visible',opacity:'0',pointerEvents:'none',zIndex:'-2147483647'});document.body.appendChild(host);return host}
const availableHeight=book=>Math.max(1,px(book.layoutDefaults?.pageHeightPx||1123)-px(book.layoutDefaults?.pagePaddingTopPx||54)-px(book.layoutDefaults?.pagePaddingBottomPx||54));
function renderProbe(book,page,items,options,host,tall=false){host.innerHTML='';const data=clone(book),probe=clone(page);probe.items=clone(items);if(tall)data.layoutDefaults={...(data.layoutDefaults||{}),pageHeightPx:Math.max(200000,availableHeight(data)*Math.max(10,items.length*2)),pagePaddingBottomPx:0};data.pages=[probe];const node=BookCore.renderPageNode(data,probe,0,{lang:'el',preview:false,imageCandidates:options.imageCandidates,sceneSource:options.sceneSource||(()=>''),pageNumber:1});node.style.setProperty('--screen-scale','1');host.appendChild(node);return{node,data}}
function nodeIsExactPositioned(node){return String(node?.dataset?.positioningRenderMode||'')==='exact'||node?.classList?.contains('positioned-item')}
function visualExtent(body,includeExact=true){
  if(!body)return{bottom:Infinity,culprit:null};
  const bodyRect=body.getBoundingClientRect(),bodyTop=bodyRect.top;
  let deepestBottom=bodyRect.height,culprit=null;
  for(const child of body.children){
    if(!includeExact&&nodeIsExactPositioned(child))continue;
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
        exactPositioned:nodeIsExactPositioned(child),
        bottomPx:candidate
      };
    }
  }
  return{bottom:deepestBottom,culprit};
}
async function measure(book,page,items,options,host){
  const {node}=renderProbe(book,page,items,options,host,false);
  if(!options.assetsReady)await waitAssets(node,options.assetTimeout||1800);
  const body=node.querySelector('.sheet-body'),bodyHeight=body?body.getBoundingClientRect().height:Infinity,flowVisual=visualExtent(body,false),allVisual=visualExtent(body,true),flowUsed=Math.max(bodyHeight,flowVisual.bottom),includeExact=options.includeExactVisualOverflow===true,used=includeExact?Math.max(flowUsed,allVisual.bottom):flowUsed,available=availableHeight(book),overflow=Math.max(0,used-available),culprit=(includeExact&&allVisual.bottom>flowUsed)?allVisual.culprit:flowVisual.culprit;
  const floatCulprit=!!culprit&&(culprit.cssFloat&&culprit.cssFloat!=='none'||/\bfloat-(?:left|right)\b/.test(culprit.className));
  const exactCulprit=!!culprit?.exactPositioned;
  return{fits:overflow<=Number(options.tolerancePx??1),used,flowUsed,visualUsed:allVisual.bottom,flowVisualUsed:flowVisual.bottom,available,overflow,overflowKind:exactCulprit?'positioned-visual-overflow':floatCulprit?'float-bottom':'document-flow',culprit};
}
async function estimateHeights(book,section,units,options,host){
  if(!units.length)return[];
  const items=flattenUnits(units),{node}=renderProbe(book,section,items,options,host,true);await waitAssets(node,options.assetTimeout||1800);
  const body=node.querySelector('.sheet-body'),children=[...(body?.children||[])],bodyTop=body?.getBoundingClientRect().top||0,bodyBottom=body?.getBoundingClientRect().bottom||bodyTop;
  const unitNodes=units.map(unit=>{const ids=(Array.isArray(unit)?unit:[unit]).map(x=>String(x?.id||'')).filter(Boolean);return children.find(child=>ids.includes(String(child.dataset.bookItemId||''))||ids.some(id=>child.querySelector?.(`[data-book-item-id=\"${CSS.escape(id)}\"]`)))||null;});
  return unitNodes.map((child,index)=>{
    if(!child)return 1;if(nodeIsExactPositioned(child))return .01;
    const rect=child.getBoundingClientRect(),top=rect.top-bodyTop;let nextTop=bodyBottom-bodyTop;
    for(let j=index+1;j<unitNodes.length;j++){const next=unitNodes[j];if(!next||nodeIsExactPositioned(next))continue;nextTop=next.getBoundingClientRect().top-bodyTop;break;}
    return Math.max(1,rect.height,nextTop-top);
  });
}
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
function initialChunks(units,heights,available){const chunks=[];let current=[],used=0;const firstItem=unit=>Array.isArray(unit)?unit[0]:unit;const finish=()=>{if(!current.length)return;chunks.push(current);current=[];used=0};for(let i=0;i<units.length;i++){const unit=clone(units[i]),item=firstItem(unit),h=Math.max(1,Number(heights[i])||1);if(isHardBreak(item)&&current.length)finish();if(current.length&&used+h>available){const splittableParent=unit.length===1&&['list','table','dialogue'].includes(item?.type);if(splittableParent){current.push(unit);used+=h;continue}const lastUnit=current.at(-1),lastItem=firstItem(lastUnit);if(isHeading(lastItem)){if(current.length>1){const heading=current.pop();finish();current=[heading];used=Math.max(1,Number(heights[i-1])||1)}else{current.push(unit);used+=h;continue}}else finish()}current.push(unit);used+=h}finish();return chunks}
function ensureNext(chunks,index){if(!chunks[index+1])chunks[index+1]=[];return chunks[index+1]}
async function repairChunks(book,section,chunks,options,host,report){let i=0;const finalMeasures=[];const flat=units=>flattenUnits(units);const firstItem=unit=>Array.isArray(unit)?unit[0]:unit;while(i<chunks.length){if(!chunks[i]?.length){chunks.splice(i,1);continue}const m=await measure(book,section,flat(chunks[i]),options,host);if(m.fits){finalMeasures[i]=m;i++;continue}const units=chunks[i];if(units.length===1){const unit=units[0];if(unit.length>1){report.oversizeItems.push({id:unit.map(x=>x.id).join('+'),type:'sequence_pair',overflowPx:Math.round(m.overflow*10)/10});finalMeasures[i]=m;i++;continue}const item=unit[0],adjusted=await fitOversizeFigure(book,section,item,options,host);if(adjusted){chunks[i]=[[adjusted]];report.resizedFigures++;continue}const part=Number(item?.extensions?.paginationPart||1)+1,pair=await maxSplitThatFits(book,section,[],item,options,host,part);if(pair){chunks[i]=[[pair.first]];ensureNext(chunks,i).unshift([pair.rest]);report.splitItems++;report.splitByType[item.type]=(report.splitByType[item.type]||0)+1;continue}report.oversizeItems.push({id:item.id,type:item.type,overflowPx:Math.round(m.overflow*10)/10});finalMeasures[i]=m;i++;continue}
    const lastUnit=units.at(-1),prevUnit=units.at(-2),last=firstItem(lastUnit),prev=firstItem(prevUnit);
    if(units.length===2&&isHeading(prev)&&lastUnit.length===1){const part=Number(last?.extensions?.paginationPart||1)+1,pair=await maxSplitThatFits(book,section,flat([prevUnit]),last,options,host,part);if(pair){chunks[i]=[prevUnit,[pair.first]];ensureNext(chunks,i).unshift([pair.rest]);report.splitItems++;report.splitByType[last.type]=(report.splitByType[last.type]||0)+1;continue}}
    // HF10: a list/table/dialogue is a semantic parent, not an indivisible pagination block.
    // Before moving the whole object to the next page, keep as many natural child fragments
    // (list items / table rows / dialogue rows) as actually fit in the remaining space.
    if(lastUnit.length===1&&['list','table','dialogue'].includes(last?.type)){
      const baseUnits=units.slice(0,-1),part=Number(last?.extensions?.paginationPart||1)+1,pair=await maxSplitThatFits(book,section,flat(baseUnits),last,options,host,part);
      if(pair){chunks[i]=[...baseUnits,[pair.first]];ensureNext(chunks,i).unshift([pair.rest]);report.splitItems++;report.splitByType[last.type]=(report.splitByType[last.type]||0)+1;continue}
    }
    const moved=[units.pop()];if(units.length&&isHeading(firstItem(units.at(-1))))moved.unshift(units.pop());ensureNext(chunks,i).unshift(...moved)
  }return finalMeasures}
function isSourceLockedPage(page){return page?.extensions?.sourcePageFidelity===true||page?.extensions?.sourcePageLocked===true;}

function hardBreakSegments(units=[]){
  const segments=[];let current=[];
  const firstItem=unit=>Array.isArray(unit)?unit[0]:unit;
  for(const unit of units){
    if(isHardBreak(firstItem(unit))&&current.length){segments.push(current);current=[];}
    current.push(unit);
  }
  if(current.length)segments.push(current);
  return segments;
}
async function paginateSection(book,section,options,host,report){
  const units=paginationUnits(section.items||[]),segments=hardBreakSegments(units),pages=[];
  for(const segment of segments){
    const fullItems=flattenUnits(segment),whole=await measure(book,section,fullItems,options,host);
    if(whole.fits){pages.push(pageTemplate(section,fullItems,pages.length));continue;}
    const heights=await estimateHeights(book,section,segment,options,host),chunks=initialChunks(segment,heights,availableHeight(book));
    await repairChunks(book,section,chunks,options,host,report);
    for(const chunk of chunks.filter(x=>x.length))pages.push(pageTemplate(section,flattenUnits(chunk),pages.length));
  }
  return pages;
}
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
      const p=book.pages[i],m=await measure(book,p,p.items||[],{...options,includeExactVisualOverflow:true},host);
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
async function paginateBook(input,options={}){if(!global.BookCore)throw Error('BookCore is required for canonical pagination.');const book=clone(input);await preloadAssets(book,options);const host=makeHost(),report={version:VERSION,startedAt:new Date().toISOString(),sourcePages:(book.pages||[]).length,outputPages:0,splitItems:0,splitByType:{},resizedFigures:0,oversizeItems:[],hardBreaks:0,sourceLockedPages:0,rendererVersion:BookCore.VERSION};try{const generated=[];for(const section of book.pages||[]){report.hardBreaks+=(section.items||[]).filter(isHardBreak).length;if(isSourceLockedPage(section)){const kept=clone(section);kept.extensions={...(kept.extensions||{}),paginationPolicy:'overflow-first',paginationSourceLocked:true};generated.push(kept);report.sourceLockedPages++;continue}generated.push(...await paginateSection(book,section,options,host,report))}book.pages=ensureUniquePageIds(generated);book.meta={...(book.meta||{}),authoringVersion:'bookwriter-4.8.7e-hf13-multilevel-list-spanning-table-rows',updatedAt:new Date().toISOString()};report.outputPages=book.pages.length;report.completedAt=new Date().toISOString();const audit=await auditOverflow(book,options);report.audit=audit;report.pagination={version:VERSION,generatedAt:new Date().toISOString(),sourcePages:report.sourcePages,outputPages:book.pages.length,sourceLockedPages:report.sourceLockedPages,policy:report.sourceLockedPages?'source-page-overflow-first':'reflow',splitItems:report.splitItems,splitByType:report.splitByType,resizedFigures:report.resizedFigures,oversizeItems:report.oversizeItems,hardBreaks:report.hardBreaks,rendererVersion:BookCore.VERSION,audit};if(!audit.ok&&options.rejectOverflow===true)throw Object.assign(new Error(`Η σελιδοποίηση άφησε ${audit.overflowPages} σελίδες με υπερχείλιση.`),{paginationReport:report});return{book,report,audit}}finally{host.remove()}}
global.BookPaginationV4=Object.freeze({VERSION,paginateBook,auditOverflow,coalesceDocxFlow,splitRich,splitItem,splittableUnits});
})(window);
