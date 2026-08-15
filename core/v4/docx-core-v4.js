(function(global){
'use strict';
const NS={w:'http://schemas.openxmlformats.org/wordprocessingml/2006/main',r:'http://schemas.openxmlformats.org/officeDocument/2006/relationships',m:'http://schemas.openxmlformats.org/officeDocument/2006/math',a:'http://schemas.openxmlformats.org/drawingml/2006/main',pic:'http://schemas.openxmlformats.org/drawingml/2006/picture',wp:'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',wpg:'http://schemas.microsoft.com/office/word/2010/wordprocessingGroup',wps:'http://schemas.microsoft.com/office/word/2010/wordprocessingShape',v:'urn:schemas-microsoft-com:vml',o:'urn:schemas-microsoft-com:office:office',mc:'http://schemas.openxmlformats.org/markup-compatibility/2006'};
const CONVERTER='bookwriter-4.8.7e-hf27-font-style-theme-truth';
const REQUIRED_WORD_PROFILE='canonical-word-v1';
const twipsToPx=v=>Number(v||0)/15;
const halfPointsToPx=v=>Number(v||0)*2/3;
const numOrNull=v=>v===''||v===null||v===undefined?null:Number(v);
const finiteNumber=v=>v!==''&&v!==null&&v!==undefined&&Number.isFinite(Number(v));
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const lname=n=>n&&n.localName||''; const elems=n=>[...(n?.childNodes||[])].filter(x=>x.nodeType===1);
const q=(n,ns,name)=>Array.from(n?.getElementsByTagNameNS(ns,name)||[]);
const first=(n,ns,name)=>q(n,ns,name)[0]||null;
const attr=(n,ns,name)=>n?.getAttributeNS(ns,name)||n?.getAttribute(name)||'';
const xml=s=>new DOMParser().parseFromString(s,'application/xml');
const serializer=new XMLSerializer();
function directText(n){let s='';for(const t of q(n,NS.w,'t')){let p=t.parentNode,insideBox=false;while(p&&p!==n){if(lname(p)==='txbxContent'){insideBox=true;break}p=p.parentNode}if(!insideBox)s+=t.textContent}return s}
function allText(n){return [...q(n,NS.w,'t'),...q(n,NS.m,'t')].map(x=>x.textContent).join('')}
function themeFonts(theme){
  const findFont=(kind,slot)=>{const group=first(theme,NS.a,kind),node=first(group,NS.a,slot);return node?.getAttribute('typeface')||''};
  return{
    majorHAnsi:findFont('majorFont','latin')||'Cambria',
    minorHAnsi:findFont('minorFont','latin')||'Calibri',
    majorEastAsia:findFont('majorFont','ea')||'',
    minorEastAsia:findFont('minorFont','ea')||'',
    majorBidi:findFont('majorFont','cs')||'',
    minorBidi:findFont('minorFont','cs')||''
  };
}
function themeFontName(token,themes={},script='western'){
  const t=String(token||'');if(!t)return'';const major=/major/i.test(t),minor=/minor/i.test(t);if(!major&&!minor)return'';
  if(script==='eastAsia')return (major?themes.majorEastAsia:themes.minorEastAsia)||(major?themes.majorHAnsi:themes.minorHAnsi)||'';
  if(script==='complex')return (major?themes.majorBidi:themes.minorBidi)||(major?themes.majorHAnsi:themes.minorHAnsi)||'';
  return (major?themes.majorHAnsi:themes.minorHAnsi)||'';
}
function textScript(text=''){
  const s=String(text||'');
  if(/[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]/u.test(s))return'eastAsia';
  if(/[\u0590-\u08ff]/u.test(s))return'complex';
  return'western';
}
function fontName(rFonts,themes={},script='western'){
  if(!rFonts)return'';
  if(script==='eastAsia'){
    const direct=attr(rFonts,NS.w,'eastAsia');if(direct)return direct;
    const themed=attr(rFonts,NS.w,'eastAsiaTheme');const resolved=themeFontName(themed,themes,'eastAsia');if(resolved)return resolved;
    return fontName(rFonts,themes,'western');
  }
  if(script==='complex'){
    const direct=attr(rFonts,NS.w,'cs');if(direct)return direct;
    const themed=attr(rFonts,NS.w,'cstheme')||attr(rFonts,NS.w,'csTheme');const resolved=themeFontName(themed,themes,'complex');if(resolved)return resolved;
    return fontName(rFonts,themes,'western');
  }
  // Word stores independent Western, East-Asian and complex-script faces in w:rFonts.
  // For Greek/Latin text, eastAsia/cs MUST NOT override ascii/hAnsi theme inheritance.
  const direct=attr(rFonts,NS.w,'ascii')||attr(rFonts,NS.w,'hAnsi');if(direct)return direct;
  const themed=attr(rFonts,NS.w,'asciiTheme')||attr(rFonts,NS.w,'hAnsiTheme');return themeFontName(themed,themes,'western');
}
function runMetrics(rPr,themes={},script='western'){
  if(!rPr)return{};
  const col=first(rPr,NS.w,'color'),rawColor=attr(col,NS.w,'val'),sz=first(rPr,NS.w,'sz'),fonts=first(rPr,NS.w,'rFonts'),shd=first(rPr,NS.w,'shd'),hi=first(rPr,NS.w,'highlight');
  const out={};
  const family=fontName(fonts,themes,script);if(family)out.fontFamily=family;
  const size=attr(sz,NS.w,'val');if(size!=='')out.fontSizePx=halfPointsToPx(size);
  const color=validHex(rawColor)||(String(rawColor).toLowerCase()==='auto'?'#000000':'');if(color)out.textColor=color;
  const fill=nodeColor(shd,'fill')||validHex(attr(hi,NS.w,'val'));if(fill)out.highlight=fill;
  for(const [key,name]of[['bold','b'],['italic','i'],['underline','u']]){const n=first(rPr,NS.w,name);if(n)out[key]=wordBool(n,true)}
  return out;
}
function runProps(r,themes={}){const p=first(r,NS.w,'rPr'),m=runMetrics(p,themes,textScript(allText(r))),vert=attr(first(p,NS.w,'vertAlign'),NS.w,'val');return{b:!!m.bold,i:!!m.italic,u:!!m.underline,sup:vert==='superscript',sub:vert==='subscript',color:m.textColor||'',highlight:m.highlight||'',fontFamily:m.fontFamily||'',fontSizePx:m.fontSizePx||null}}
function wrapRun(text,pr,href=''){let h=esc(text);if(pr.sup)h='<sup>'+h+'</sup>';if(pr.sub)h='<sub>'+h+'</sub>';if(pr.u)h='<u>'+h+'</u>';if(pr.i)h='<em>'+h+'</em>';if(pr.b)h='<strong>'+h+'</strong>';const css=[];if(pr.color)css.push('color:'+pr.color);if(pr.highlight)css.push('background-color:'+pr.highlight);if(pr.fontFamily)css.push('font-family:'+JSON.stringify(pr.fontFamily));if(pr.fontSizePx)css.push('font-size:'+pr.fontSizePx+'px');if(css.length)h='<span style="'+esc(css.join(';'))+'">'+h+'</span>';if(href)h='<a href="'+esc(href)+'">'+h+'</a>';return h}
const unsupportedMath=new Set();
function mathChildren(n){return elems(n).filter(c=>!lname(c).endsWith('Pr')).map(mathNode).join('')}
function mathPart(n,name){const x=elems(n).find(c=>lname(c)===name);return x?'<mrow>'+mathChildren(x)+'</mrow>':'<mrow></mrow>'}
function mathChr(n,def){const p=elems(n).find(c=>lname(c).endsWith('Pr'));const c=p&&first(p,NS.m,'chr');return attr(c,NS.m,'val')||def}
const functionNamesWithArguments=new Set(['ημ','συν','εφ','σφ','sin','cos','tan','cot','sec','csc','sinh','cosh','tanh','ln','log']);
function fencedMath(open,close,body){return '<mrow>'+(open?'<mo fence="true" stretchy="true">'+esc(open)+'</mo>':'')+body+(close?'<mo fence="true" stretchy="true">'+esc(close)+'</mo>':'')+'</mrow>'}
function hasOuterParens(mathml=''){return /^<mrow>\s*(?:<mrow>\s*)?(?:<mfenced\b|<mo\b[^>]*>\s*\()/i.test(String(mathml||''))}
function mathRunStyle(n){const p=first(n,NS.m,'rPr');return attr(first(p,NS.m,'sty'),NS.m,'val')||''}
function mathTokenNodes(text,run){
  const style=mathRunStyle(run), plain=style==='p';
  const raw=String(text??'');const sub=raw.match(/^([A-Za-zΑ-Ωα-ωΆ-Ώά-ώ]+)_([A-Za-zΑ-Ωα-ωΆ-Ώά-ώ0-9]+)$/u);if(sub)return '<msub><mrow>'+mathTokenNodes(sub[1],run)+'</mrow><mrow><mi mathvariant="normal">'+esc(sub[2])+'</mi></mrow></msub>';const parts=raw.match(/\s+|[A-Za-zΑ-Ωα-ωΆ-Ώά-ώ]+_[A-Za-zΑ-Ωα-ωΆ-Ώά-ώ0-9]+|\d+(?:[.,]\d+)?|[A-Za-zΑ-Ωα-ωΆ-Ώά-ώ]+|./gu)||[];
  return parts.map(token=>{
    if(/^\s+$/u.test(token))return '<mspace width=".24em"/>';
    if(/^\d+(?:[.,]\d+)?$/u.test(token))return '<mn>'+esc(token)+'</mn>';const subToken=token.match(/^([A-Za-zΑ-Ωα-ωΆ-Ώά-ώ]+)_([A-Za-zΑ-Ωα-ωΆ-Ώά-ώ0-9]+)$/u);if(subToken)return '<msub><mrow>'+mathTokenNodes(subToken[1],run)+'</mrow><mrow><mi mathvariant="normal">'+esc(subToken[2])+'</mi></mrow></msub>';
    if(/^[A-Za-zΑ-Ωα-ωΆ-Ώά-ώ]+$/u.test(token)){
      const greek=/[Α-Ωα-ωΆ-Ώά-ώ]/u.test(token);const normal=plain||greek||/^(sin|cos|tan|cot|log|ln|exp|max|min|lim)$/i.test(token);
      if(greek&&[...token].length>1)return '<mtext>'+esc(token)+'</mtext>';
      if(normal)return '<mi mathvariant="normal">'+esc(token)+'</mi>';
      if([...token].length>1)return [...token].map(ch=>'<mi>'+esc(ch)+'</mi>').join('');
      return '<mi>'+esc(token)+'</mi>';
    }
    return '<mo>'+esc(token)+'</mo>';
  }).join('');
}
function mathNode(n){
  const l=lname(n);
  if(l.endsWith('Pr'))return'';
  if(l==='t')return '<mtext>'+esc(n.textContent)+'</mtext>';
  if(l==='r')return '<mrow>'+elems(n).filter(c=>lname(c)==='t').map(t=>mathTokenNodes(t.textContent,n)).join('')+'</mrow>';
  if(['oMath','oMathPara','e','num','den','base','sup','sub','lim','deg'].includes(l))return '<mrow>'+mathChildren(n)+'</mrow>';
  if(l==='f')return '<mfrac>'+mathPart(n,'num')+mathPart(n,'den')+'</mfrac>';
  if(l==='sSup')return '<msup>'+mathPart(n,'e')+mathPart(n,'sup')+'</msup>';
  if(l==='sSub')return '<msub>'+mathPart(n,'e')+mathPart(n,'sub')+'</msub>';
  if(l==='sSubSup')return '<msubsup>'+mathPart(n,'e')+mathPart(n,'sub')+mathPart(n,'sup')+'</msubsup>';
  if(l==='sPre')return '<mmultiscripts>'+mathPart(n,'e')+'<mprescripts/>'+mathPart(n,'sub')+mathPart(n,'sup')+'</mmultiscripts>';
  if(l==='rad'){const deg=elems(n).find(c=>lname(c)==='deg');return deg&&allText(deg).trim()?'<mroot>'+mathPart(n,'e')+mathChildren(deg)+'</mroot>':'<msqrt>'+mathPart(n,'e')+'</msqrt>'}
  if(l==='d'){const p=elems(n).find(c=>lname(c)==='dPr');const beg=attr(first(p,NS.m,'begChr'),NS.m,'val')||'(';const endNode=first(p,NS.m,'endChr');const end=endNode?attr(endNode,NS.m,'val'):(beg==='{'?'':')');return fencedMath(beg,end,mathPart(n,'e'))}
  if(l==='nary'){const ch=mathChr(n,'∑');return '<mrow><munderover><mo>'+esc(ch)+'</mo>'+mathPart(n,'sub')+mathPart(n,'sup')+'</munderover>'+mathPart(n,'e')+'</mrow>'}
  if(l==='limLow')return '<munder>'+mathPart(n,'e')+mathPart(n,'lim')+'</munder>';
  if(l==='limUpp')return '<mover>'+mathPart(n,'e')+mathPart(n,'lim')+'</mover>';
  if(l==='bar')return '<mover accent="true">'+mathPart(n,'e')+'<mo>¯</mo></mover>';
  if(l==='acc')return '<mover accent="true">'+mathPart(n,'e')+'<mo>'+esc(mathChr(n,'ˆ'))+'</mo></mover>';
  if(l==='func'){
    const name=allText(elems(n).find(c=>lname(c)==='fName')||n).replace(/\s+/g,'').trim();
    const fName=mathPart(n,'fName'),arg=mathPart(n,'e');
    if(functionNamesWithArguments.has(name)&&!hasOuterParens(arg))return '<mrow>'+fName+fencedMath('(',')',arg)+'</mrow>';
    return '<mrow>'+fName+arg+'</mrow>';
  }
  if(l==='fName')return '<mi mathvariant="normal">'+esc(allText(n))+'</mi>';
  if(l==='eqArr')return '<mtable>'+elems(n).filter(c=>lname(c)==='e').map(e=>'<mtr><mtd>'+mathChildren(e)+'</mtd></mtr>').join('')+'</mtable>';
  if(l==='m')return '<mtable>'+elems(n).filter(c=>lname(c)==='mr').map(mathNode).join('')+'</mtable>';
  if(l==='mr')return '<mtr>'+elems(n).filter(c=>lname(c)==='e').map(e=>'<mtd>'+mathChildren(e)+'</mtd>').join('')+'</mtr>';
  if(l==='groupChr'){const p=elems(n).find(c=>lname(c)==='groupChrPr');const ch=attr(first(p,NS.m,'chr'),NS.m,'val')||'⏞';const vert=attr(first(p,NS.m,'vertJc'),NS.m,'val')||'top';return vert==='bot'?'<munder>'+mathPart(n,'e')+'<mo>'+esc(ch)+'</mo></munder>':'<mover>'+mathPart(n,'e')+'<mo>'+esc(ch)+'</mo></mover>'}
  if(['box','borderBox','phant'].includes(l))return mathPart(n,'e');
  if(['ctrlPr','argPr'].includes(l))return'';
  if(elems(n).length){unsupportedMath.add(l);return '<mrow>'+mathChildren(n)+'</mrow>'}
  return '';
}
function ommlToMath(n,display=false){
  const body=lname(n)==='oMathPara'?elems(n).filter(c=>lname(c)==='oMath').map(mathNode).join(''):mathNode(n);
  const cls=display?'math-display':'math-inline';
  const style=display?' style="display:block;overflow:visible;max-width:none;text-align:center;margin:0.45em 0"':'';
  return '<span class="'+cls+'"'+style+'><math xmlns="http://www.w3.org/1998/Math/MathML" display="'+(display?'block':'inline')+'">'+body+'</math></span>';
}
async function readXml(zip,path,optional=false){const f=zip.file(path);if(!f){if(optional)return null;throw new Error('Λείπει '+path)}return xml(await f.async('string'))}
async function readJson(zip,path,optional=false){const f=zip.file(path);if(!f){if(optional)return null;throw new Error('Λείπει '+path)}try{return JSON.parse(await f.async('string'))}catch(error){if(optional)return null;throw new Error('Άκυρο JSON '+path+': '+error.message)}}
function parsedWordPageMap(doc){
  const root=doc?.documentElement;if(!root||lname(root)!=='pageMap')return null;const num=name=>{const v=Number(root.getAttribute(name));return Number.isFinite(v)?v:0},blocks={};
  for(const node of q(root,root.namespaceURI,'block')){
    const index=Number(node.getAttribute('index'));if(!Number.isFinite(index)||index<1)continue;
    const listValueRaw=Number(node.getAttribute('listValue'));
    const block={kind:String(node.getAttribute('kind')||''),startPage:Number(node.getAttribute('startPage'))||null,endPage:Number(node.getAttribute('endPage'))||Number(node.getAttribute('startPage'))||null,rowSpansPages:Number(node.getAttribute('rowSpansPages'))||0,...(Number.isFinite(listValueRaw)&&listValueRaw>0?{listValue:listValueRaw}:{}),listString:String(node.getAttribute('listString')||''),rows:[]};
    for(const row of elems(node).filter(x=>lname(x)==='row')){
      const rowData={row:Number(row.getAttribute('row'))||0,startPage:Number(row.getAttribute('startPage'))||null,endPage:Number(row.getAttribute('endPage'))||Number(row.getAttribute('startPage'))||null,cells:[]};
      for(const cell of elems(row).filter(x=>lname(x)==='cell')){
        const cellData={cell:Number(cell.getAttribute('cell'))||0,paragraphs:[]};
        for(const paragraph of elems(cell).filter(x=>lname(x)==='paragraph'))cellData.paragraphs.push({paragraph:Number(paragraph.getAttribute('paragraph'))||0,startPage:Number(paragraph.getAttribute('startPage'))||null});
        rowData.cells.push(cellData);
      }
      block.rows.push(rowData);
    }
    blocks[String(index)]=block;
  }
  const missing=q(root,root.namespaceURI,'marker').map(n=>String(n.getAttribute('name')||'')).filter(Boolean),errorNode=q(root,root.namespaceURI,'error')[0];
  return{version:num('version')||1,source:String(root.getAttribute('source')||''),status:String(root.getAttribute('status')||''),available:String(root.getAttribute('available')||'').toLowerCase()==='true',pageCount:num('pageCount'),topLevelBlocks:num('topLevelBlocks'),paragraphs:num('paragraphs'),tables:num('tables'),rows:num('rows'),mappedBlocks:num('mappedBlocks'),listCandidates:num('listCandidates'),listValuesMapped:num('listValuesMapped'),pageQueries:num('pageQueries'),boundaryQueries:num('boundaryQueries'),spanningTableRows:num('spanningTableRows'),tableParagraphPageQueries:num('tableParagraphPageQueries'),paragraphEndPageExact:String(root.getAttribute('paragraphEndPageExact')||'').toLowerCase()==='true',missingMarkers:missing,error:errorNode?.textContent||'',blocks};
}
function customProperties(doc){
  const out={};
  if(!doc)return out;
  for(const prop of [...doc.getElementsByTagName('*')].filter(n=>lname(n)==='property')){
    const name=prop.getAttribute('name')||'';
    if(!name)continue;
    out[name]=elems(prop).map(x=>x.textContent||'').join('').trim();
  }
  return out;
}
function hintRole(hint){
  const value=typeof hint==='string'?hint:hint?.role;
  return String(value||'').trim().toLowerCase().replace(/[_\s]+/g,'-');
}
function skipImageRole(role){
  return ['ignore','ignored','text-fragment','inline-text','ocr-text','text-run','decorative','watermark','page-background','background','header','footer'].includes(hintRole(role));
}
function textImageRole(role){
  return ['text-fragment','inline-text','ocr-text','text-run'].includes(hintRole(role));
}
function validHex(v){const s=String(v||'').replace(/^#/,'').trim();return /^[0-9A-Fa-f]{6}$/.test(s)?'#'+s.toUpperCase():''}
function nodeColor(n,kind='color'){
  if(!n)return '';
  const val=attr(n,NS.w,kind==='fill'?'fill':'val');
  return validHex(val);
}
function wordBool(n,fallback=null){if(!n)return fallback;const v=String(attr(n,NS.w,'val')||'1').toLowerCase();return !['0','false','off','no'].includes(v)}
function xmlBoolAttr(n,name,fallback=false){if(!n)return fallback;const raw=n.getAttribute(name);if(raw===null||raw==='')return fallback;return !['0','false','off','no'].includes(String(raw).toLowerCase())}
function emuToPx(v){const n=Number(v);return Number.isFinite(n)?n/9525:null}
function pxAttr(n,name){const value=emuToPx(n?.getAttribute(name));return Number.isFinite(value)?Math.round(value):null}
function drawingWrapContract(anchor){
  if(!anchor)return{type:'inline',side:'bothSides',distTopPx:0,distRightPx:0,distBottomPx:0,distLeftPx:0};
  const kinds=['wrapNone','wrapSquare','wrapTight','wrapThrough','wrapTopAndBottom'];
  const wrapNode=kinds.map(name=>first(anchor,NS.wp,name)).find(Boolean)||null;
  const type=wrapNode?lname(wrapNode):'wrapNone';
  const side=String(wrapNode?.getAttribute('wrapText')||'bothSides');
  const polygon=wrapNode?[...q(wrapNode,NS.wp,'lineTo')].map(point=>({x:Number(point.getAttribute('x')||0),y:Number(point.getAttribute('y')||0)})).filter(point=>Number.isFinite(point.x)&&Number.isFinite(point.y)):[];
  return{type,side,distTopPx:pxAttr(anchor,'distT')||0,distRightPx:pxAttr(anchor,'distR')||0,distBottomPx:pxAttr(anchor,'distB')||0,distLeftPx:pxAttr(anchor,'distL')||0,...(polygon.length?{polygon}:{}),contourApplied:false};
}
function drawingAnchorContract(anchor,pos,vpos){
  if(!anchor)return{version:1,mode:'inline',horizontal:{relativeFrom:'character',align:'',offsetPx:null},vertical:{relativeFrom:'line',align:'',offsetPx:null},wrap:drawingWrapContract(null),stacking:{relativeHeight:0,behindDoc:false,allowOverlap:false,layoutInCell:true,locked:false}};
  const ox=first(pos,NS.wp,'posOffset'),oy=first(vpos,NS.wp,'posOffset');
  const horizontal={relativeFrom:attr(pos,null,'relativeFrom')||'',align:first(pos,NS.wp,'align')?.textContent?.trim().toLowerCase()||'',offsetPx:ox?Math.round(Number(ox.textContent||0)/9525):null};
  const vertical={relativeFrom:attr(vpos,null,'relativeFrom')||'',align:first(vpos,NS.wp,'align')?.textContent?.trim().toLowerCase()||'',offsetPx:oy?Math.round(Number(oy.textContent||0)/9525):null};
  const paragraphRelative=['paragraph','character','line'].includes(String(horizontal.relativeFrom).toLowerCase())||['paragraph','character','line'].includes(String(vertical.relativeFrom).toLowerCase());
  return{version:1,mode:paragraphRelative?'paragraph-anchored':'page-absolute',horizontal,vertical,wrap:drawingWrapContract(anchor),stacking:{relativeHeight:Number(anchor.getAttribute('relativeHeight')||0)||0,behindDoc:xmlBoolAttr(anchor,'behindDoc',false),allowOverlap:xmlBoolAttr(anchor,'allowOverlap',true),layoutInCell:xmlBoolAttr(anchor,'layoutInCell',true),locked:xmlBoolAttr(anchor,'locked',false)},simplePos:xmlBoolAttr(anchor,'simplePos',false)};
}
function documentSettings(settings){return{autoHyphenation:wordBool(first(settings,NS.w,'autoHyphenation'),false),doNotHyphenateCaps:wordBool(first(settings,NS.w,'doNotHyphenateCaps'),false)}}
function paragraphSectionPageBreak(p){
  const sect=first(first(p,NS.w,'pPr'),NS.w,'sectPr');
  if(!sect)return false;
  const type=attr(first(sect,NS.w,'type'),NS.w,'val')||'nextPage';
  return type!=='continuous';
}
function sectionColumnInfo(sect,layoutSource={},index=1){
  const cols=first(sect,NS.w,'cols'),pgSz=first(sect,NS.w,'pgSz'),pgMar=first(sect,NS.w,'pgMar'),type=attr(first(sect,NS.w,'type'),NS.w,'val')||'';
  const pageWidth=twipsToPx(attr(pgSz,NS.w,'w')||0)||Number(layoutSource.pageWidthPx)||794;
  const left=twipsToPx(attr(pgMar,NS.w,'left')||0)||Number(layoutSource.marginsPx?.left)||44;
  const right=twipsToPx(attr(pgMar,NS.w,'right')||0)||Number(layoutSource.marginsPx?.right)||44;
  const bodyWidth=Math.max(1,pageWidth-left-right);
  const explicit=cols?elems(cols).filter(x=>lname(x)==='col'):[];
  const attrNum=Number(attr(cols,NS.w,'num')||0);
  const columnCount=Math.max(1,attrNum||explicit.length||1);
  const gapPx=twipsToPx(attr(cols,NS.w,'space')||720)||48;
  let columnWidthsPx=explicit.map(col=>twipsToPx(attr(col,NS.w,'w'))).filter(value=>value>0);
  if(columnWidthsPx.length<columnCount){
    const available=Math.max(1,bodyWidth-gapPx*(columnCount-1));
    columnWidthsPx=Array.from({length:columnCount},()=>Math.max(80,available/columnCount));
  }
  return{id:`docx-section-${index}`,type,columnCount,columnGapPx:gapPx,columnWidthsPx:columnWidthsPx.map(value=>Math.round(value)),bodyWidthPx:Math.round(bodyWidth)};
}
function buildSectionPlan(body,layoutSource={}){
  const children=elems(body),boundaries=[];let sourceIndex=0;
  for(const child of children){
    if(lname(child)==='p'||lname(child)==='tbl')sourceIndex++;
    if(lname(child)==='p'){
      const sect=first(first(child,NS.w,'pPr'),NS.w,'sectPr');
      if(sect)boundaries.push({end:sourceIndex,info:sectionColumnInfo(sect,layoutSource,boundaries.length+1)});
    }
  }
  const bodySect=children.find(child=>lname(child)==='sectPr');
  if(sourceIndex&&!boundaries.some(boundary=>boundary.end===sourceIndex)){
    boundaries.push({end:sourceIndex,info:sectionColumnInfo(bodySect,layoutSource,boundaries.length+1)});
  }
  const byParagraph=new Map();let start=1;
  for(const boundary of boundaries){
    for(let i=start;i<=boundary.end;i++)byParagraph.set(i,boundary.info);
    start=boundary.end+1;
  }
  if(start<=sourceIndex){
    const info=sectionColumnInfo(bodySect,layoutSource,boundaries.length+1);
    for(let i=start;i<=sourceIndex;i++)byParagraph.set(i,info);
  }
  return byParagraph;
}
function mergeVisual(parent={},child={}){
  const out={...(parent||{})};
  for(const[k,v]of Object.entries(child||{}))if(v!==undefined&&v!==null&&v!=='')out[k]=v;
  return out;
}
function paragraphMetrics(pPr,themes={}){
  if(!pPr)return{};
  const out={},shd=first(pPr,NS.w,'shd'),jc=first(pPr,NS.w,'jc'),suppress=first(pPr,NS.w,'suppressAutoHyphens'),spacing=first(pPr,NS.w,'spacing'),ind=first(pPr,NS.w,'ind'),frame=first(pPr,NS.w,'framePr'),rPr=first(pPr,NS.w,'rPr');
  const background=nodeColor(shd,'fill');if(background)out.backgroundColor=background;
  const align=attr(jc,NS.w,'val');if(align)out.align=align;
  if(suppress)out.suppressAutoHyphens=wordBool(suppress,true);
  if(spacing){
    const before=attr(spacing,NS.w,'before'),after=attr(spacing,NS.w,'after'),line=attr(spacing,NS.w,'line'),rule=attr(spacing,NS.w,'lineRule')||'auto';
    if(before!=='')out.marginTopPx=twipsToPx(before);
    if(after!=='')out.marginBottomPx=twipsToPx(after);
    if(line!==''){
      if(rule==='auto')out.lineHeight=Number(line)/240;
      else out.lineHeightPx=twipsToPx(line);
    }
    const contextual=first(pPr,NS.w,'contextualSpacing');if(contextual)out.contextualSpacing=wordBool(contextual,true);
  }
  if(ind){
    const left=attr(ind,NS.w,'left')||attr(ind,NS.w,'start'),right=attr(ind,NS.w,'right')||attr(ind,NS.w,'end'),firstLine=attr(ind,NS.w,'firstLine'),hanging=attr(ind,NS.w,'hanging');
    if(left!=='')out.marginLeftPx=twipsToPx(left);
    if(right!=='')out.marginRightPx=twipsToPx(right);
    if(firstLine!=='')out.textIndentPx=twipsToPx(firstLine);
    if(hanging!=='')out.textIndentPx=-twipsToPx(hanging);
  }
  const keepNext=first(pPr,NS.w,'keepNext'),keepLines=first(pPr,NS.w,'keepLines'),pageBreak=first(pPr,NS.w,'pageBreakBefore');
  if(keepNext)out.keepWithNext=wordBool(keepNext,true);
  if(keepLines)out.keepTogether=wordBool(keepLines,true);
  if(pageBreak)out.pageBreakBefore=wordBool(pageBreak,true);
  if(frame){
    const frameWidth=attr(frame,NS.w,'w'),frameHeight=attr(frame,NS.w,'h'),frameX=attr(frame,NS.w,'x'),frameY=attr(frame,NS.w,'y');
    out.wordFrame=true;
    if(frameWidth!=='')out.frameWidthPx=twipsToPx(frameWidth);
    if(frameHeight!=='')out.frameHeightPx=twipsToPx(frameHeight);
    if(frameX!=='')out.frameXPx=twipsToPx(frameX);
    if(frameY!=='')out.frameYPx=twipsToPx(frameY);
    out.frameHAnchor=attr(frame,NS.w,'hAnchor')||'';
    out.frameVAnchor=attr(frame,NS.w,'vAnchor')||'';
    out.frameXAlign=attr(frame,NS.w,'xAlign')||'';
    out.frameYAlign=attr(frame,NS.w,'yAlign')||'';
    out.frameWrap=attr(frame,NS.w,'wrap')||'';
    const hSpace=attr(frame,NS.w,'hSpace'),vSpace=attr(frame,NS.w,'vSpace');
    if(hSpace!=='')out.frameHSpacePx=twipsToPx(hSpace);
    if(vSpace!=='')out.frameVSpacePx=twipsToPx(vSpace);
    out.frameHeightRule=attr(frame,NS.w,'hRule')||'';
    out.frameDropCap=attr(frame,NS.w,'dropCap')||'';
  }
  return mergeVisual(out,runMetrics(rPr,themes));
}
function docDefaultVisual(styles,themes={}){
  const d=first(styles,NS.w,'docDefaults'),r=first(first(d,NS.w,'rPrDefault'),NS.w,'rPr'),p=first(first(d,NS.w,'pPrDefault'),NS.w,'pPr');
  return mergeVisual(paragraphMetrics(p,themes),runMetrics(r,themes));
}
function styleVisual(style,themes={}){
  const pPr=first(style,NS.w,'pPr'),rPr=first(style,NS.w,'rPr');
  return mergeVisual(paragraphMetrics(pPr,themes),runMetrics(rPr,themes));
}
function styleMap(styles,themes={},defaults={}){
  const raw=new Map();if(!styles)return raw;
  for(const style of q(styles,NS.w,'style')){
    if(attr(style,NS.w,'type')!=='paragraph')continue;
    const id=attr(style,NS.w,'styleId'),nm=attr(first(style,NS.w,'name'),NS.w,'val')||id,ol=attr(first(style,NS.w,'outlineLvl'),NS.w,'val'),basedOn=attr(first(style,NS.w,'basedOn'),NS.w,'val');
    raw.set(id,{name:nm,outline:ol===''?null:Number(ol),basedOn,visual:styleVisual(style,themes),isDefault:attr(style,NS.w,'default')==='1'});
  }
  const resolved=new Map();
  function resolve(id,stack=new Set()){
    if(resolved.has(id))return resolved.get(id);const own=raw.get(id);if(!own)return null;
    if(stack.has(id))return own;stack.add(id);
    const parent=own.basedOn?resolve(own.basedOn,stack):null;
    const val={...own,visual:mergeVisual(parent?.visual||defaults,own.visual)};resolved.set(id,val);stack.delete(id);return val;
  }
  for(const id of raw.keys())resolve(id);
  const def=[...resolved.values()].find(x=>x.isDefault)||resolved.get('Normal')||resolved.get('a');
  resolved.defaultVisual=mergeVisual(defaults,def?.visual||{});
  return resolved;
}
function directParagraphVisual(p,themes={}){
  const pPr=first(p,NS.w,'pPr'),pRPr=first(pPr,NS.w,'rPr'),firstRun=elems(p).find(x=>lname(x)==='r'),rPr=pRPr||first(firstRun,NS.w,'rPr');
  return mergeVisual(paragraphMetrics(pPr,themes),runMetrics(rPr,themes));
}
function normalizedBookMargins(pageWidthPx,left,right){
  let layoutLeft=Number(left)||44,layoutRight=Number(right)||44;
  if(layoutRight>pageWidthPx*.22&&layoutLeft<pageWidthPx*.14)layoutRight=layoutLeft;
  else if(layoutLeft>pageWidthPx*.22&&layoutRight<pageWidthPx*.14)layoutLeft=layoutRight;
  else if(pageWidthPx-layoutLeft-layoutRight<pageWidthPx*.68){
    layoutLeft=Math.min(Math.max(layoutLeft,36),72);
    layoutRight=Math.min(Math.max(layoutRight,36),72);
  }
  return{left:Math.round(layoutLeft*1000)/1000,right:Math.round(layoutRight*1000)/1000};
}
function fontResolutionAudit(styles,themes={}){
  const d=first(styles,NS.w,'docDefaults'),rPr=first(first(d,NS.w,'rPrDefault'),NS.w,'rPr'),rFonts=first(rPr,NS.w,'rFonts');
  const pick=name=>attr(rFonts,NS.w,name)||'';
  return{
    theme:{majorHAnsi:themes.majorHAnsi||'',minorHAnsi:themes.minorHAnsi||'',majorEastAsia:themes.majorEastAsia||'',minorEastAsia:themes.minorEastAsia||'',majorBidi:themes.majorBidi||'',minorBidi:themes.minorBidi||''},
    docDefaultsRFonts:{ascii:pick('ascii'),hAnsi:pick('hAnsi'),asciiTheme:pick('asciiTheme'),hAnsiTheme:pick('hAnsiTheme'),eastAsia:pick('eastAsia'),eastAsiaTheme:pick('eastAsiaTheme'),cs:pick('cs'),csTheme:pick('cstheme')||pick('csTheme')},
    resolvedWestern:fontName(rFonts,themes,'western'),
    resolvedEastAsia:fontName(rFonts,themes,'eastAsia'),
    resolvedComplex:fontName(rFonts,themes,'complex'),
    rule:'western ascii/hAnsi direct-or-theme before eastAsia/cs; script slots do not override one another'
  };
}
function documentLayout(doc,styles,themes={}){
  const defaults=docDefaultVisual(styles,themes),sect=q(doc,NS.w,'sectPr')[q(doc,NS.w,'sectPr').length-1]||null,pgSz=first(sect,NS.w,'pgSz'),pgMar=first(sect,NS.w,'pgMar');
  const pageWidthPx=twipsToPx(attr(pgSz,NS.w,'w')||11906),pageHeightPx=twipsToPx(attr(pgSz,NS.w,'h')||16838),top=twipsToPx(attr(pgMar,NS.w,'top')||851),right=twipsToPx(attr(pgMar,NS.w,'right')||851),bottom=twipsToPx(attr(pgMar,NS.w,'bottom')||851),left=twipsToPx(attr(pgMar,NS.w,'left')||851),header=twipsToPx(attr(pgMar,NS.w,'header')||708),footer=twipsToPx(attr(pgMar,NS.w,'footer')||708);
  const bodyFontFamily=defaults.fontFamily||themes.minorHAnsi||'Calibri',bodyFontSize=defaults.fontSizePx||14.6667,lineHeight=defaults.lineHeight||1.15,paragraphGap=defaults.marginBottomPx??(10*96/72);
  const fontAudit=fontResolutionAudit(styles,themes);
  const headerHeight=Math.max(12,top-header-4),footerHeight=Math.max(12,bottom-footer-4);
  return{defaults,layoutDefaults:{pageSize:'A4',orientation:pageWidthPx>pageHeightPx?'landscape':'portrait',pageWidthPx,pageHeightPx,pagePaddingTopPx:top,pagePaddingRightPx:right,pagePaddingBottomPx:bottom,pagePaddingLeftPx:left,headerTopPx:Math.max(0,header),headerHeightPx:headerHeight,headerPaddingBottomPx:4,headerBorderWidthPx:1,headerFontSize:11,headerLineHeight:1.05,headerFontFamily:'sans',footerBottomPx:Math.max(0,footer),footerHeightPx:footerHeight,footerPaddingTopPx:4,footerBorderWidthPx:1,footerFontSize:11,footerLineHeight:1.05,footerFontFamily:'sans',bodyFontSize,lineHeight,paragraphGap,sectionGap:Math.max(8,paragraphGap),showPageNumbers:true,bodyFontFamily,headingFontFamily:themes.majorHAnsi||'Cambria',sectionHeadingFontSize:18.6667,partTitleFontSize:21.3333,captionFontSize:12},source:{pageWidthPx,pageHeightPx,marginsPx:{top,right,bottom,left},bookMarginsPx:{top,right,bottom,left},headerDistancePx:header,footerDistancePx:footer,headerHeightPx:headerHeight,footerHeightPx:footerHeight,bodyFontFamily,bodyFontSize,lineHeight,paragraphGap,fontResolutionAudit:fontAudit}}}
function relMap(rels){const m=new Map();if(!rels)return m;for(const x of rels.getElementsByTagName('Relationship'))m.set(x.getAttribute('Id'),{target:x.getAttribute('Target'),type:x.getAttribute('Type')});return m}
function numberingMap(num){
  const result={numToAbs:new Map(),absFmt:new Map(),absLevels:new Map(),numOverrides:new Map()};
  if(!num)return result;
  for(const a of q(num,NS.w,'abstractNum')){
    const id=attr(a,NS.w,'abstractNumId'),formats=new Map(),levels=new Map();
    for(const l of q(a,NS.w,'lvl')){
      const il=Number(attr(l,NS.w,'ilvl')||0),fmt=attr(first(l,NS.w,'numFmt'),NS.w,'val')||'bullet';
      formats.set(il,fmt);
      const markerRPr=first(l,NS.w,'rPr'),markerFonts=first(markerRPr,NS.w,'rFonts');
      levels.set(il,{
        fmt,
        start:Number(attr(first(l,NS.w,'start'),NS.w,'val')||1)||1,
        text:attr(first(l,NS.w,'lvlText'),NS.w,'val')||'',
        markerFontFamily:attr(markerFonts,NS.w,'ascii')||attr(markerFonts,NS.w,'hAnsi')||attr(markerFonts,NS.w,'eastAsia')||attr(markerFonts,NS.w,'cs')||'',
        restart:Number(attr(first(l,NS.w,'lvlRestart'),NS.w,'val')||0)||0
      });
    }
    result.absFmt.set(id,formats);
    result.absLevels.set(id,levels);
  }
  for(const n of q(num,NS.w,'num')){
    const numId=attr(n,NS.w,'numId'),absId=attr(first(n,NS.w,'abstractNumId'),NS.w,'val'),overrides=new Map();
    result.numToAbs.set(numId,absId);
    for(const override of elems(n).filter(x=>lname(x)==='lvlOverride')){
      const il=Number(attr(override,NS.w,'ilvl')||0),startOverride=attr(first(override,NS.w,'startOverride'),NS.w,'val');
      if(startOverride!=='')overrides.set(il,Number(startOverride)||1);
    }
    result.numOverrides.set(numId,overrides);
  }
  return result;
}
function numberingLevel(nums,numId,ilvl){
  const abs=nums.numToAbs.get(numId),base=nums.absLevels.get(abs)?.get(ilvl)||{fmt:'bullet',start:1,text:'',restart:0};
  return{...base,start:nums.numOverrides.get(numId)?.get(ilvl)??base.start};
}
function nextListOrdinal(info,counters){
  if(info.listType!=='ol'||!info.numId)return null;
  let levels=counters.get(info.numId);
  if(!levels){levels=new Map();counters.set(info.numId,levels)}
  const level=Number(info.ilvl)||0,start=Number(info.listStartBase)||1,current=levels.has(level)?levels.get(level)+1:start;
  levels.set(level,current);
  for(const key of [...levels.keys()])if(key>level)levels.delete(key);
  return current;
}
function cssListStyle(fmt='decimal'){
  return({decimal:'decimal',decimalZero:'decimal-leading-zero',lowerLetter:'lower-alpha',upperLetter:'upper-alpha',lowerRoman:'lower-roman',upperRoman:'upper-roman'})[fmt]||'decimal';
}
function visibleListMarker(info,wordBlock){
  const rendered=String(wordBlock?.listString||'').trim();
  if(rendered)return rendered;
  const template=String(info?.listText||'').trim();
  // Bullet levels store the literal glyph in w:lvlText.  Numbered levels use
  // placeholders such as %1, so do not expose those as visible markers.
  return info?.numFmt==='bullet'&&!/%\d+/.test(template)?template:'';
}
function markerLooksLikeBullet(marker='',numFmt=''){
  const m=String(marker||'').trim();
  if(String(numFmt||'').toLowerCase()==='bullet')return true;
  if(!m)return false;
  if(/[•·◦○●▪▫■□◆◇►▸▶▷✓✔✦✧❖➢➤➜→]/u.test(m))return true;
  if([...m].some(ch=>{const cp=ch.codePointAt(0)||0;return cp>=0xE000&&cp<=0xF8FF}))return true;
  return false;
}
function markerLooksOrdered(marker='',numFmt=''){
  if(markerLooksLikeBullet(marker,numFmt))return false;
  const m=String(marker||'').trim();
  if(!m)return !['bullet','none'].includes(String(numFmt||'').toLowerCase());
  return /^(?:[\(\[]\s*)?(?:\d+(?:[.\-]\d+)*|[A-Za-zΑ-Ωα-ω]+|[IVXLCDMivxlcdm]+)(?:\s*[\)\]\.:-])?$/u.test(m);
}
function effectiveListType(info,marker=''){
  if(markerLooksLikeBullet(marker,info?.numFmt))return'ul';
  if(markerLooksOrdered(marker,info?.numFmt))return'ol';
  return info?.listType||'ul';
}
function pInfo(p,styles,nums,themes={}){
  const pPr=first(p,NS.w,'pPr'),sid=attr(first(pPr,NS.w,'pStyle'),NS.w,'val'),st=styles.get(sid)||{name:sid||'Normal',outline:null,visual:styles.defaultVisual||{}};
  let level=null;const hm=String(st.name).match(/heading\s*([1-9])/i);if(hm)level=Number(hm[1]);else if(st.outline!==null)level=st.outline+1;
  const numPr=first(pPr,NS.w,'numPr'),numId=attr(first(numPr,NS.w,'numId'),NS.w,'val'),ilvl=Number(attr(first(numPr,NS.w,'ilvl'),NS.w,'val')||0);let listType='',numFmt='',listStartBase=1,listText='',listMarkerFontFamily='';
  if(numId&&numId!=='0'){const spec=numberingLevel(nums,numId,ilvl);numFmt=spec.fmt;listStartBase=spec.start;listText=spec.text;listMarkerFontFamily=spec.markerFontFamily||'';listType=(numFmt==='bullet'||numFmt==='none'&&/^[-•·]$/.test(String(listText||'')))?'ul':'ol'}
  const paragraphStyle=mergeVisual(st.visual||styles.defaultVisual||{},directParagraphVisual(p,themes));
  const isTocStyle=/^(toc\s*heading|toc\s*\d+|contents?)$/i.test(String(st.name||'').trim());
  return{styleId:sid,styleName:st.name,headingLevel:level,listType,numId,ilvl,numFmt,listStartBase,listText,listMarkerFontFamily,headingStyle:paragraphStyle,paragraphStyle,isTocStyle};
}
function framePlacement(style={},layoutSource={}){
  const x=Number(style.frameXPx),w=Number(style.frameWidthPx)||300,pageWidth=Number(layoutSource.pageWidthPx)||0;
  if(Number.isFinite(x)&&pageWidth)return x+w/2<pageWidth/2?'float-left':'float-right';
  return 'float-right';
}
function framePositionContract(style={},sourceParagraph=null){
  const hRelative=String(style.frameHAnchor||''),vRelative=String(style.frameVAnchor||'');
  const paragraphRelative=['text','paragraph','character','line'].includes(hRelative.toLowerCase())||['text','paragraph','character','line'].includes(vRelative.toLowerCase());
  return{version:1,mode:paragraphRelative?'paragraph-anchored':'page-absolute',horizontal:{relativeFrom:hRelative,align:String(style.frameXAlign||'').toLowerCase(),offsetPx:Number.isFinite(Number(style.frameXPx))?Number(style.frameXPx):null},vertical:{relativeFrom:vRelative,align:String(style.frameYAlign||'').toLowerCase(),offsetPx:Number.isFinite(Number(style.frameYPx))?Number(style.frameYPx):null},anchor:{kind:paragraphRelative?'paragraph':'page',sourceParagraph:Number(sourceParagraph)||null,itemId:''},wrap:{type:String(style.frameWrap||'around'),side:'bothSides',distTopPx:Number(style.frameVSpacePx)||0,distRightPx:Number(style.frameHSpacePx)||0,distBottomPx:Number(style.frameVSpacePx)||0,distLeftPx:Number(style.frameHSpacePx)||0,contourApplied:false},stacking:{relativeHeight:0,behindDoc:false,allowOverlap:true,layoutInCell:true,locked:false},frame:{heightRule:String(style.frameHeightRule||''),dropCap:String(style.frameDropCap||'')}};
}
function frameBlock(seg,inf,layoutSource={}){
  const style=inf.paragraphStyle||{},text=stripHtml(seg.html||''),labelMatch=text.match(/^([^.!?]{2,45}[!?:])\s+/u);
  return{type:'textbox',html:seg.html,text,label:labelMatch?labelMatch[1]:'',width:Math.round(Number(style.frameWidthPx)||300),height:Math.round(Number(style.frameHeightPx)||0)||undefined,x:style.frameXPx,y:style.frameYPx,xAnchor:style.frameHAnchor,yAnchor:style.frameVAnchor,positionContract:framePositionContract(style,seg.sourceParagraph),placement:framePlacement(style,layoutSource),floating:true,paragraphStyle:style,sourceStyle:'DOCX Text Frame',sourceParagraph:seg.sourceParagraph,breakBefore:seg.breakBefore};
}
function headingSlug(value=''){const s=String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9\u0370-\u03ff]+/g,'-').replace(/^-+|-+$/g,'').slice(0,64);return s||'heading'}
function stableHeadingId(text,level,counts){const base='docx-h'+level+'-'+headingSlug(text),n=(counts.get(base)||0)+1;counts.set(base,n);return n===1?base:base+'-'+n}
function imageRefs(node){const ids=[];for(const b of q(node,NS.a,'blip')){const id=attr(b,NS.r,'embed');if(id)ids.push({rid:id,kind:'drawing'})}for(const im of q(node,NS.v,'imagedata')){const id=attr(im,NS.r,'id');if(id)ids.push({rid:id,kind:'vml'})}const seen=new Set();return ids.filter(x=>{const k=x.kind+'|'+x.rid;if(seen.has(k))return false;seen.add(k);return true})}
function vmlSize(node){const shape=first(node,NS.v,'shape');const style=shape?.getAttribute('style')||'';const read=name=>{const m=style.match(new RegExp('(?:^|;)\\s*'+name+'\\s*:\\s*([0-9.]+)(pt|px|in|cm|mm)','i'));if(!m)return 0;const n=Number(m[1]),u=m[2].toLowerCase();return Math.round(n*({px:1,pt:96/72,in:96,cm:96/2.54,mm:96/25.4}[u]||1))};return{width:read('width')||undefined,height:read('height')||undefined}}
function drawingMemberInfo(node,rid){
  if(!rid)return null;
  for(const blip of q(node,NS.a,'blip')){
    if(attr(blip,NS.r,'embed')!==rid)continue;
    let picture=blip;
    while(picture&&picture!==node&&lname(picture)!=='pic')picture=picture.parentNode;
    if(!picture||lname(picture)!=='pic')return null;
    const spPr=elems(picture).find(x=>lname(x)==='spPr');
    const xfrm=spPr&&first(spPr,NS.a,'xfrm');
    const memberExtent=xfrm&&elems(xfrm).find(x=>lname(x)==='ext');
    const width=Math.round(Number(memberExtent?.getAttribute('cx')||0)/9525)||undefined;
    const height=Math.round(Number(memberExtent?.getAttribute('cy')||0)/9525)||undefined;
    const srcRect=first(picture,NS.a,'srcRect');
    const crop={left:Number(srcRect?.getAttribute('l')||0),top:Number(srcRect?.getAttribute('t')||0),right:Number(srcRect?.getAttribute('r')||0),bottom:Number(srcRect?.getAttribute('b')||0)};
    const rotationDeg=Number(xfrm?.getAttribute('rot')||0)/60000;
    const flipH=['1','true','on'].includes(String(xfrm?.getAttribute('flipH')||'').toLowerCase());
    const flipV=['1','true','on'].includes(String(xfrm?.getAttribute('flipV')||'').toLowerCase());
    return{width,height,crop,rotationDeg,flipH,flipV};
  }
  return null;
}
function imagePositionHint(node){
  const docPr=first(node,NS.wp,'docPr');
  if(!docPr)return null;
  const text=[docPr.getAttribute('descr')||'',docPr.getAttribute('title')||'',docPr.getAttribute('name')||''].join('\n');
  const jsonMatch=text.match(/BW_IMPORT\s*(\{[\s\S]*?\})(?:\s|$)/);
  let data=null;
  if(jsonMatch){
    try{data=JSON.parse(jsonMatch[1])}catch(_error){data=null}
  }
  if(!data){
    const bbox=text.match(/(?:bw:)?bbox(?:Pt|Px)?\s*=\s*([0-9.+-]+)\s*,\s*([0-9.+-]+)\s*,\s*([0-9.+-]+)\s*,\s*([0-9.+-]+)/i);
    if(bbox)data={bboxPt:bbox.slice(1).map(Number)};
  }
  const hint=data?.bw||data;
  if(!hint||typeof hint!=='object')return null;
  const toPx=(value,unit)=>Number(value)*(/pt/i.test(String(unit||''))?96/72:1);
  const bbox=hint.bboxPx||hint.bboxPt||hint.bbox;
  let x=Number(hint.xPx??hint.x),y=Number(hint.yPx??hint.y),width=Number(hint.widthPx??hint.wPx??hint.width??hint.w),height=Number(hint.heightPx??hint.hPx??hint.height??hint.h);
  const unit=hint.unit||hint.units||(hint.bboxPt?'pt':'px');
  if(Array.isArray(bbox)&&bbox.length>=4){
    const vals=bbox.map(v=>toPx(v,unit));
    x=vals[0];y=vals[1];width=Math.max(0,vals[2]-vals[0]);height=Math.max(0,vals[3]-vals[1]);
  }else{
    if(Number.isFinite(x))x=toPx(x,unit);
    if(Number.isFinite(y))y=toPx(y,unit);
    if(Number.isFinite(width))width=toPx(width,unit);
    if(Number.isFinite(height))height=toPx(height,unit);
  }
  const role=hintRole(hint),captionId=String(hint.captionId||hint.caption||''),textValue=String(hint.text||''),htmlValue=String(hint.html||''),alt=String(hint.alt||'');
  const hasGeometry=Number.isFinite(x)||Number.isFinite(y)||Number.isFinite(width)||Number.isFinite(height);
  const hasSemantic=!!(role||captionId||textValue||htmlValue||alt||hint.sourcePage||hint.page);
  if(!hasGeometry&&!hasSemantic)return null;
  return{...(Number.isFinite(x)?{x:Math.round(x)}:{}),...(Number.isFinite(y)?{y:Math.round(y)}:{}),...(Number.isFinite(width)&&width>0?{width:Math.round(width)}:{}),...(Number.isFinite(height)&&height>0?{height:Math.round(height)}:{}),sourcePage:hint.sourcePage||hint.page||null,role,captionId,text:textValue,html:htmlValue,alt,raw:hint};
}
function imageGeometry(node,rid=''){
  const ex=first(node,NS.wp,'extent');
  const wpExtent=ex?{width:Math.round(Number(ex.getAttribute('cx')||0)/9525)||undefined,height:Math.round(Number(ex.getAttribute('cy')||0)/9525)||undefined}:null;
  const vml=vmlSize(node),member=drawingMemberInfo(node,rid),imageRefCount=imageRefs(node).length;
  // HF5 truth rule: a normal one-picture Word drawing is displayed in wp:extent.
  // a:xfrm/a:ext is member geometry and must not silently replace the outer Word display box.
  // Only a drawing that really contains multiple image members uses the member extent per member.
  let width,height,geometrySource='missing';
  if(imageRefCount===1&&wpExtent?.width&&wpExtent?.height){width=wpExtent.width;height=wpExtent.height;geometrySource='wp:extent'}
  else if(imageRefCount>1&&member?.width&&member?.height){width=member.width;height=member.height;geometrySource='member-a:xfrm/a:ext'}
  else if(wpExtent?.width&&wpExtent?.height){width=wpExtent.width;height=wpExtent.height;geometrySource='wp:extent'}
  else if(vml.width&&vml.height){width=vml.width;height=vml.height;geometrySource='vml-style'}
  const geometryConflict=!!(wpExtent?.width&&wpExtent?.height&&member?.width&&member?.height&&(Math.abs(wpExtent.width-member.width)>1||Math.abs(wpExtent.height-member.height)>1));
  const crop=member?.crop||{left:0,top:0,right:0,bottom:0};
  const cropActive=Object.values(crop).some(v=>Number(v)!==0);
  const geometryTruth={version:1,imageRefCount,geometrySource,wpExtent:wpExtent||null,memberExtent:member?.width&&member?.height?{width:member.width,height:member.height}:null,vmlExtent:vml.width&&vml.height?{width:vml.width,height:vml.height}:null,geometryConflict,crop,cropActive,rotationDeg:Number(member?.rotationDeg)||0,flipH:!!member?.flipH,flipV:!!member?.flipV};
  const anchor=first(node,NS.wp,'anchor');const floating=!!anchor;
  let placement=floating?'float-right':'wide';
  let x,y,xAnchor='',yAnchor='',positionContract=drawingAnchorContract(null,null,null);
  if(anchor){
    const pos=first(anchor,NS.wp,'positionH'),vpos=first(anchor,NS.wp,'positionV');
    const align=first(pos,NS.wp,'align')?.textContent?.trim().toLowerCase()||'';
    if(align==='left'||align==='inside')placement='float-left';else if(align==='right'||align==='outside')placement='float-right';else if(align==='center')placement='float-right';
    const ox=first(pos,NS.wp,'posOffset'),oy=first(vpos,NS.wp,'posOffset');
    if(ox)x=Math.round(Number(ox.textContent||0)/9525);
    if(oy)y=Math.round(Number(oy.textContent||0)/9525);
    xAnchor=attr(pos,null,'relativeFrom')||'';
    yAnchor=attr(vpos,null,'relativeFrom')||'';
    positionContract=drawingAnchorContract(anchor,pos,vpos);
  }
  const hint=imagePositionHint(node);
  const hintedPosition=hint&&(Number.isFinite(Number(hint.x))||Number.isFinite(Number(hint.y)));
  if(hint){
    if(hint.width)width=hint.width;
    if(hint.height)height=hint.height;
    if(hintedPosition){
      if(Number.isFinite(Number(hint.x)))x=hint.x;
      if(Number.isFinite(Number(hint.y)))y=hint.y;
      xAnchor=xAnchor||'page';
      yAnchor=yAnchor||'page';
      placement='float-right';
      positionContract={...positionContract,mode:'page-absolute',horizontal:{...(positionContract.horizontal||{}),relativeFrom:xAnchor,offsetPx:Number.isFinite(Number(x))?Number(x):null},vertical:{...(positionContract.vertical||{}),relativeFrom:yAnchor,offsetPx:Number.isFinite(Number(y))?Number(y):null},hintOverride:true};
    }
  }
  return{width,height,floating:floating||!!hintedPosition,placement,x,y,xAnchor,yAnchor,positionHint:hint||null,positionContract,geometryTruth};
}

// 4.8.7e HF6 — native Word DrawingML group probe.
// The browser reads Word vector groups directly. No PNG/EMF fallback is generated here.
function directChildNS(node,ns,name){return elems(node).find(x=>x.namespaceURI===ns&&lname(x)===name)||null}
function ancestorNS(node,ns,name){let n=node?.parentNode;while(n){if(n.namespaceURI===ns&&lname(n)===name)return n;n=n.parentNode}return null}
function nativeGroupAncestor(node){return ancestorNS(node,NS.wpg,'wgp')||ancestorNS(node,NS.wpg,'grpSp')}
function emu(v){const n=Number(v);return Number.isFinite(n)?n:0}
function matrixIdentity(){return[1,0,0,1,0,0]}
function matrixMul(a,b){return[a[0]*b[0]+a[2]*b[1],a[1]*b[0]+a[3]*b[1],a[0]*b[2]+a[2]*b[3],a[1]*b[2]+a[3]*b[3],a[0]*b[4]+a[2]*b[5]+a[4],a[1]*b[4]+a[3]*b[5]+a[5]]}
function matrixApply(m,x,y){return{x:m[0]*x+m[2]*y+m[4],y:m[1]*x+m[3]*y+m[5]}}
function groupXfrm(group){const pr=directChildNS(group,NS.wpg,'grpSpPr'),x=pr&&directChildNS(pr,NS.a,'xfrm');if(!x)return null;const off=directChildNS(x,NS.a,'off'),ext=directChildNS(x,NS.a,'ext'),chOff=directChildNS(x,NS.a,'chOff'),chExt=directChildNS(x,NS.a,'chExt');return{offX:emu(off?.getAttribute('x')),offY:emu(off?.getAttribute('y')),extX:Math.max(1,emu(ext?.getAttribute('cx'))),extY:Math.max(1,emu(ext?.getAttribute('cy'))),chOffX:emu(chOff?.getAttribute('x')),chOffY:emu(chOff?.getAttribute('y')),chExtX:Math.max(1,emu(chExt?.getAttribute('cx'))||emu(ext?.getAttribute('cx'))),chExtY:Math.max(1,emu(chExt?.getAttribute('cy'))||emu(ext?.getAttribute('cy')))} }
function groupMatrix(x){if(!x)return matrixIdentity();const sx=x.extX/x.chExtX,sy=x.extY/x.chExtY;return[sx,0,0,sy,x.offX-x.chOffX*sx,x.offY-x.chOffY*sy]}
function groupRootMatrix(x){const m=groupMatrix(x);return[m[0],m[1],m[2],m[3],m[4]-(Number(x?.offX)||0),m[5]-(Number(x?.offY)||0)]}
function shapeInfo(shape){const spPr=directChildNS(shape,NS.wps,'spPr'),cNvSpPr=directChildNS(shape,NS.wps,'cNvSpPr'),style=directChildNS(shape,NS.wps,'style'),x=spPr&&directChildNS(spPr,NS.a,'xfrm'),off=x&&directChildNS(x,NS.a,'off'),ext=x&&directChildNS(x,NS.a,'ext'),geom=spPr&&directChildNS(spPr,NS.a,'prstGeom'),custGeom=spPr&&directChildNS(spPr,NS.a,'custGeom'),isTextBox=['1','true','on'].includes(String(cNvSpPr?.getAttribute('txBox')||'').toLowerCase());return{spPr,cNvSpPr,style,isTextBox,x,x:emu(off?.getAttribute('x')),y:emu(off?.getAttribute('y')),w:Math.max(1,emu(ext?.getAttribute('cx'))),h:Math.max(1,emu(ext?.getAttribute('cy'))),rot:emu(x?.getAttribute('rot'))/60000,flipH:['1','true','on'].includes(String(x?.getAttribute('flipH')||'').toLowerCase()),flipV:['1','true','on'].includes(String(x?.getAttribute('flipV')||'').toLowerCase()),preset:String(geom?.getAttribute('prst')||''),geom,custGeom};}
function shapeRectInRoot(info,parentMatrix){const pts=[matrixApply(parentMatrix,info.x,info.y),matrixApply(parentMatrix,info.x+info.w,info.y),matrixApply(parentMatrix,info.x,info.y+info.h),matrixApply(parentMatrix,info.x+info.w,info.y+info.h)];const xs=pts.map(p=>p.x),ys=pts.map(p=>p.y);return{x:Math.min(...xs),y:Math.min(...ys),width:Math.max(...xs)-Math.min(...xs),height:Math.max(...ys)-Math.min(...ys)}}
function drawingEffectExtent(node){const ex=first(node,NS.wp,'effectExtent');return{left:Math.max(0,Math.round(emu(ex?.getAttribute('l'))/9525)),top:Math.max(0,Math.round(emu(ex?.getAttribute('t'))/9525)),right:Math.max(0,Math.round(emu(ex?.getAttribute('r'))/9525)),bottom:Math.max(0,Math.round(emu(ex?.getAttribute('b'))/9525))}}
function schemeColor(v){const key=String(v||'').toLowerCase();if(['lt1','bg1'].includes(key))return'#FFFFFF';if(['dk1','tx1'].includes(key))return'#000000';return'#000000'}
function svgPaint(container,kind='fill'){
  if(!container)return kind==='fill'?'none':'#000000';
  if(directChildNS(container,NS.a,'noFill'))return'none';
  const solid=directChildNS(container,NS.a,'solidFill');if(solid){const rgb=directChildNS(solid,NS.a,'srgbClr')?.getAttribute('val');if(rgb&&/^[0-9a-f]{6}$/i.test(rgb))return'#'+rgb;const sch=directChildNS(solid,NS.a,'schemeClr')?.getAttribute('val');if(sch)return schemeColor(sch)}
  return kind==='fill'?'none':'#000000';
}
function svgShapeFill(info){
  const sp=info.spPr;if(directChildNS(sp,NS.a,'noFill')||directChildNS(sp,NS.a,'solidFill'))return svgPaint(sp,'fill');
  const ref=info.style&&directChildNS(info.style,NS.a,'fillRef'),idx=Number(ref?.getAttribute('idx')||0);
  if(ref&&idx>0){const rgb=directChildNS(ref,NS.a,'srgbClr')?.getAttribute('val');if(rgb&&/^[0-9a-f]{6}$/i.test(rgb))return'#'+rgb;const sch=directChildNS(ref,NS.a,'schemeClr')?.getAttribute('val');if(sch)return schemeColor(sch)}
  return'none';
}

function drawingColor(container,def='#000000'){
  if(!container)return{color:def,opacity:1};
  const rgb=directChildNS(container,NS.a,'srgbClr'),sch=directChildNS(container,NS.a,'schemeClr'),node=rgb||sch;
  let color=rgb?.getAttribute('val');if(color&&/^[0-9a-f]{6}$/i.test(color))color='#'+color;else color=sch?schemeColor(sch.getAttribute('val')):def;
  const alpha=directChildNS(node,NS.a,'alpha');const opacity=alpha?Math.max(0,Math.min(1,Number(alpha.getAttribute('val')||100000)/100000)):1;
  return{color:color||def,opacity};
}
function shapeTextDefaultColor(shape){
  const style=directChildNS(shape,NS.wps,'style'),fontRef=style&&directChildNS(style,NS.a,'fontRef');
  if(!fontRef)return'';
  const rgb=directChildNS(fontRef,NS.a,'srgbClr')?.getAttribute('val');if(rgb&&/^[0-9a-f]{6}$/i.test(rgb))return'#'+rgb;
  const sch=directChildNS(fontRef,NS.a,'schemeClr')?.getAttribute('val');return sch?schemeColor(sch):'';
}
function guideBaseEnv(w,h){return{w,h,l:0,t:0,r:w,b:h,hc:w/2,vc:h/2,wd2:w/2,hd2:h/2,wd3:w/3,hd3:h/3,wd4:w/4,hd4:h/4,wd5:w/5,hd5:h/5,wd6:w/6,hd6:h/6,wd8:w/8,hd8:h/8,ss:Math.min(w,h),ls:Math.max(w,h),ssd2:Math.min(w,h)/2,ssd4:Math.min(w,h)/4,cd2:10800000,cd4:5400000,cd8:2700000};}
function guideNum(v,env){const k=String(v??'').trim();if(!k)return 0;if(Object.prototype.hasOwnProperty.call(env,k))return Number(env[k])||0;const n=Number(k);return Number.isFinite(n)?n:0}
function evalGuideFormula(fmla,env){
  const p=String(fmla||'').trim().split(/\s+/),op=p.shift()||'val',a=guideNum(p[0],env),b=guideNum(p[1],env),c=guideNum(p[2],env);
  if(op==='val')return a;if(op==='*/')return c?a*b/c:0;if(op==='+-')return a+b-c;if(op==='+/')return c?(a+b)/c:0;if(op==='?:')return a>0?b:c;
  if(op==='abs')return Math.abs(a);if(op==='min')return Math.min(a,b);if(op==='max')return Math.max(a,b);if(op==='sqrt')return Math.sqrt(Math.max(0,a));if(op==='mod')return Math.hypot(a,b,c);
  if(op==='pin')return Math.max(a,Math.min(b,c));
  const rad=v=>guideNum(v,env)/60000*Math.PI/180;
  if(op==='sin')return a*Math.sin(rad(p[1]));if(op==='cos')return a*Math.cos(rad(p[1]));if(op==='tan')return a*Math.tan(rad(p[1]));
  if(op==='at2')return Math.atan2(b,a)*180/Math.PI*60000;
  return 0;
}
function customGeomEnv(cust,pathW,pathH){
  const env=guideBaseEnv(pathW,pathH);
  for(const gd of q(cust,NS.a,'gd')){const name=String(gd.getAttribute('name')||'');if(name)env[name]=evalGuideFormula(gd.getAttribute('fmla')||'',env)}
  return env;
}
function customGeometrySvg(info,unsupported,markerId,fill,ls,tr,fillPrefix=''){
  const cust=info.custGeom;if(!cust)return'';
  const paths=q(cust,NS.a,'path');if(!paths.length){unsupported.add('custGeom:no-path');return''}
  const rendered=[];
  for(const path of paths){
    const pathW=Math.max(1,guideNum(path.getAttribute('w')||info.w,guideBaseEnv(info.w,info.h))||info.w),pathH=Math.max(1,guideNum(path.getAttribute('h')||info.h,guideBaseEnv(info.w,info.h))||info.h),env=customGeomEnv(cust,pathW,pathH);
    const sx=info.w/pathW,sy=info.h/pathH,coord=(pt,axis)=>{const val=guideNum(pt?.getAttribute(axis)||0,env);return(axis==='x'?info.x+val*sx:info.y+val*sy)},bits=[];let current={x:info.x,y:info.y},bad=false;
    for(const cmd of elems(path)){
      const kind=lname(cmd);
      if(kind==='moveTo'||kind==='lnTo'){const pt=directChildNS(cmd,NS.a,'pt');if(!pt){bad=true;continue}const x=coord(pt,'x'),y=coord(pt,'y');bits.push((kind==='moveTo'?'M':'L')+` ${x} ${y}`);current={x,y};continue}
      if(kind==='cubicBezTo'){const pts=elems(cmd).filter(x=>lname(x)==='pt');if(pts.length<3){bad=true;continue}const vals=pts.slice(0,3).map(pt=>({x:coord(pt,'x'),y:coord(pt,'y')}));bits.push(`C ${vals[0].x} ${vals[0].y} ${vals[1].x} ${vals[1].y} ${vals[2].x} ${vals[2].y}`);current=vals[2];continue}
      if(kind==='quadBezTo'){const pts=elems(cmd).filter(x=>lname(x)==='pt');if(pts.length<2){bad=true;continue}const vals=pts.slice(0,2).map(pt=>({x:coord(pt,'x'),y:coord(pt,'y')}));bits.push(`Q ${vals[0].x} ${vals[0].y} ${vals[1].x} ${vals[1].y}`);current=vals[1];continue}
      if(kind==='close'){bits.push('Z');continue}
      // ArcTo is deliberately not guessed: preserve a visible diagnostic only
      // for commands the generic DrawingML path evaluator does not implement.
      bad=true;unsupported.add('custGeom:'+kind)
    }
    if(bits.length){const common=` fill="${fill}" stroke="${ls.stroke}" stroke-width="${ls.sw}"${ls.dash}${svgMarkerRef(markerId,ls.arrowEnd,'end')}${svgMarkerRef(markerId,ls.arrowStart,'start')}${tr}`;rendered.push(`${fillPrefix}<path d="${bits.join(' ')}"${common}/>`)}
    if(bad&&!bits.length)unsupported.add('custGeom:unrendered')
  }
  return rendered.join('');
}
function svgPatternFill(info,markerId){
  const patt=info.spPr&&directChildNS(info.spPr,NS.a,'pattFill');if(!patt)return null;
  const prst=String(patt.getAttribute('prst')||'').toLowerCase(),fg=drawingColor(directChildNS(patt,NS.a,'fgClr'),'#808080'),bg=drawingColor(directChildNS(patt,NS.a,'bgClr'),'#FFFFFF'),tile=Math.max(20,Math.min(info.w,info.h)/18),id=`${markerId}-pat-${Math.round(info.x)}-${Math.round(info.y)}`.replace(/[^A-Za-z0-9_-]/g,'_');
  let strokes='';
  const line=(x1,y1,x2,y2)=>`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${fg.color}" stroke-opacity="${fg.opacity}" stroke-width="${Math.max(1,tile*.11)}"/>`;
  if(prst.includes('diagcross'))strokes=line(0,tile,tile,0)+line(0,0,tile,tile);
  else if(prst.includes('updiag'))strokes=line(0,tile,tile,0);
  else if(prst.includes('dndiag')||prst.includes('dn'))strokes=line(0,0,tile,tile);
  else if(prst.includes('cross'))strokes=line(0,tile/2,tile,tile/2)+line(tile/2,0,tile/2,tile);
  else if(prst.includes('vert'))strokes=line(tile/2,0,tile/2,tile);
  else strokes=line(0,tile/2,tile,tile/2);
  const defs=`<defs><pattern id="${id}" patternUnits="userSpaceOnUse" width="${tile}" height="${tile}"><rect width="${tile}" height="${tile}" fill="${bg.color}" fill-opacity="${bg.opacity}"/>${strokes}</pattern></defs>`;
  return{fill:`url(#${id})`,defs};
}
function svgLineStyle(info){
  const ln=info.spPr&&directChildNS(info.spPr,NS.a,'ln');
  if(!ln&&info.isTextBox)return{stroke:'none',sw:0,dash:'',arrowEnd:'',arrowStart:''};
  let stroke=svgPaint(ln,'line'),sw=stroke==='none'?0:Math.max(1,emu(ln?.getAttribute('w'))||6350),dash='';
  const d=String(ln&&directChildNS(ln,NS.a,'prstDash')?.getAttribute('val')||'').toLowerCase();
  if(sw>0){
    const patterns={
      sysdot:[1,2],dot:[1,2],sysdash:[4,3],dash:[3,2],lgdash:[7,3],
      sysdashdot:[4,2,1,2],dashdot:[3,2,1,2],lgdashdot:[7,3,1,3],
      sysdashdotdot:[4,2,1,2,1,2],lgdashdotdot:[7,3,1,3,1,3]
    };
    const pattern=patterns[d];
    if(pattern)dash=` stroke-dasharray="${pattern.map(v=>Math.max(1,sw*v)).join(' ')}"`;
  }
  const tail=String(ln&&directChildNS(ln,NS.a,'tailEnd')?.getAttribute('type')||'').toLowerCase();
  const head=String(ln&&directChildNS(ln,NS.a,'headEnd')?.getAttribute('type')||'').toLowerCase();
  const arrowType=v=>['triangle','stealth','arrow','open','diamond','oval'].includes(v)?v:'';
  return{stroke,sw,dash,arrowEnd:arrowType(tail),arrowStart:arrowType(head)}
}
function svgMarkerRef(markerId,type,where){
  const t=String(type||'').toLowerCase();if(!t)return'';
  return` marker-${where}="url(#${markerId}-${t})"`;
}
function svgMarkerDefs(markerId){
  const common='markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto-start-reverse" markerUnits="strokeWidth"';
  // context-stroke keeps arrowheads faithful for non-black Word connectors too.
  return[
    `<marker id="${markerId}-triangle" ${common}><path d="M0,0 L7,3.5 L0,7 z" fill="context-stroke"/></marker>`,
    `<marker id="${markerId}-stealth" ${common}><path d="M0,0 L7,3.5 L1.4,7 L2.5,3.5 z" fill="context-stroke"/></marker>`,
    `<marker id="${markerId}-arrow" ${common}><path d="M0,0 L7,3.5 L0,7 z" fill="context-stroke"/></marker>`,
    `<marker id="${markerId}-open" ${common}><path d="M0,0 L7,3.5 L0,7" fill="none" stroke="context-stroke" stroke-width="1.2"/></marker>`,
    `<marker id="${markerId}-diamond" ${common}><path d="M0,3.5 L3.5,0 L7,3.5 L3.5,7 z" fill="context-stroke"/></marker>`,
    `<marker id="${markerId}-oval" ${common}><ellipse cx="3.5" cy="3.5" rx="3.2" ry="2.7" fill="context-stroke"/></marker>`
  ].join('');
}
function svgTransform(info){const cx=info.x+info.w/2,cy=info.y+info.h/2,parts=[];if(info.rot)parts.push(`rotate(${info.rot} ${cx} ${cy})`);if(info.flipH||info.flipV)parts.push(`translate(${cx} ${cy}) scale(${info.flipH?-1:1} ${info.flipV?-1:1}) translate(${-cx} ${-cy})`);return parts.length?` transform="${parts.join(' ')}"`:''}
function presetAdjustment(info,name,def){for(const gd of q(info.geom,NS.a,'gd'))if(gd.getAttribute('name')===name){const m=String(gd.getAttribute('fmla')||'').match(/val\s+(-?[0-9.]+)/);if(m)return Number(m[1])}return def}
function pictureInfo(pic,ctx,token){
  const spPr=directChildNS(pic,NS.pic,'spPr'),xfrm=spPr&&directChildNS(spPr,NS.a,'xfrm'),off=xfrm&&directChildNS(xfrm,NS.a,'off'),ext=xfrm&&directChildNS(xfrm,NS.a,'ext');
  const blipFill=directChildNS(pic,NS.pic,'blipFill'),blip=blipFill&&directChildNS(blipFill,NS.a,'blip'),rid=attr(blip,NS.r,'embed'),rel=ctx.rels.get(rid);
  if(!rid||!rel)return null;
  const rawPath=('word/'+String(rel.target||'').replace(/^\.\.\//,'')).replace(/\\/g,'/'),path=ctx.vectorSurrogateMap?.get(rawPath)||rawPath,srcRect=blipFill&&directChildNS(blipFill,NS.a,'srcRect');
  const crop={left:Number(srcRect?.getAttribute('l')||0),top:Number(srcRect?.getAttribute('t')||0),right:Number(srcRect?.getAttribute('r')||0),bottom:Number(srcRect?.getAttribute('b')||0)};
  return{token,rid,path,rawPath,x:emu(off?.getAttribute('x')),y:emu(off?.getAttribute('y')),w:Math.max(1,emu(ext?.getAttribute('cx'))),h:Math.max(1,emu(ext?.getAttribute('cy'))),rot:emu(xfrm?.getAttribute('rot'))/60000,flipH:['1','true','on'].includes(String(xfrm?.getAttribute('flipH')||'').toLowerCase()),flipV:['1','true','on'].includes(String(xfrm?.getAttribute('flipV')||'').toLowerCase()),crop};
}
function svgPictureLayer(info,clipId){
  const l=Math.max(0,Math.min(.999,Number(info.crop?.left||0)/100000)),t=Math.max(0,Math.min(.999,Number(info.crop?.top||0)/100000)),r=Math.max(0,Math.min(.999,Number(info.crop?.right||0)/100000)),b=Math.max(0,Math.min(.999,Number(info.crop?.bottom||0)/100000));
  const vw=Math.max(.001,1-l-r),vh=Math.max(.001,1-t-b),fullW=info.w/vw,fullH=info.h/vh,fullX=info.x-fullW*l,fullY=info.y-fullH*t;
  const tr=svgTransform(info),clip=(l||t||r||b)?`<clipPath id="${clipId}"><rect x="${info.x}" y="${info.y}" width="${info.w}" height="${info.h}"/></clipPath>`:'';
  const clipAttr=clip?` clip-path="url(#${clipId})"`:'';
  return`${clip?`<defs>${clip}</defs>`:''}<g${tr}><image href="${info.token}" x="${fullX}" y="${fullY}" width="${fullW}" height="${fullH}" preserveAspectRatio="none"${clipAttr}/></g>`;
}
function mapBoxThroughGroupMatrix(info,m){
  const p1=matrixApply(m,info.x,info.y),p2=matrixApply(m,info.x+info.w,info.y+info.h);
  return{...info,x:Math.min(p1.x,p2.x),y:Math.min(p1.y,p2.y),w:Math.max(1,Math.abs(p2.x-p1.x)),h:Math.max(1,Math.abs(p2.y-p1.y)),groupMatrixFlattened:true};
}
function svgShapeInfo(i,unsupported,markerId){const p=i.preset,x=i.x,y=i.y,w=i.w,h=i.h,pattern=svgPatternFill(i,markerId),fill=pattern?.fill||svgShapeFill(i),ls=svgLineStyle(i),tr=svgTransform(i),prefix=pattern?.defs||'',common=` fill="${fill}" stroke="${ls.stroke}" stroke-width="${ls.sw}"${ls.dash}${svgMarkerRef(markerId,ls.arrowEnd,'end')}${svgMarkerRef(markerId,ls.arrowStart,'start')}${tr}`;
  if(i.custGeom){const custom=customGeometrySvg(i,unsupported,markerId,fill,ls,tr,prefix);if(custom)return custom}
  if(p==='rect')return`${prefix}<rect x="${x}" y="${y}" width="${w}" height="${h}"${common}/>`;
  if(p==='roundRect')return`${prefix}<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${Math.min(w,h)*.14}" ry="${Math.min(w,h)*.14}"${common}/>`;
  if(p==='ellipse')return`${prefix}<ellipse cx="${x+w/2}" cy="${y+h/2}" rx="${w/2}" ry="${h/2}"${common}/>`;
  if(p==='line')return`${prefix}<line x1="${x}" y1="${y}" x2="${x+w}" y2="${y+h}" fill="none" stroke="${ls.stroke}" stroke-width="${ls.sw}"${ls.dash}${svgMarkerRef(markerId,ls.arrowEnd,'end')}${svgMarkerRef(markerId,ls.arrowStart,'start')}${tr}/>`;
  if(p==='straightConnector1')return`${prefix}<line x1="${x}" y1="${y}" x2="${x+w}" y2="${y+h}"${common}/>`;
  if(p==='bentConnector3'){const a=presetAdjustment(i,'adj1',50000)/100000,xx=x+w*a;return`${prefix}<polyline points="${x},${y} ${xx},${y} ${xx},${y+h} ${x+w},${y+h}"${common}/>`}
  if(p==='arc'){const a1=presetAdjustment(i,'adj1',0)/60000*Math.PI/180,a2=presetAdjustment(i,'adj2',10800000)/60000*Math.PI/180,cx=x+w/2,cy=y+h/2,rx=w/2,ry=h/2,x1=cx+rx*Math.cos(a1),y1=cy+ry*Math.sin(a1),x2=cx+rx*Math.cos(a2),y2=cy+ry*Math.sin(a2),delta=((a2-a1)%(Math.PI*2)+Math.PI*2)%(Math.PI*2),large=delta>Math.PI?1:0;return`${prefix}<path d="M ${x1} ${y1} A ${rx} ${ry} 0 ${large} 1 ${x2} ${y2}" fill="none" stroke="${ls.stroke}" stroke-width="${ls.sw}"${ls.dash}${svgMarkerRef(markerId,ls.arrowEnd,'end')}${svgMarkerRef(markerId,ls.arrowStart,'start')}${tr}/>`}
  if(p==='stripedRightArrow'){const head=Math.min(w*.34,h*.75),shaftY=h*.26,pts=[[x,y+shaftY],[x+w-head,y+shaftY],[x+w-head,y],[x+w,y+h/2],[x+w-head,y+h],[x+w-head,y+h-shaftY],[x,y+h-shaftY]].map(v=>v.join(',')).join(' ');return`${prefix}<g${tr}><polygon points="${pts}" fill="${fill==='none'?'#FFFFFF':fill}" stroke="${ls.stroke}" stroke-width="${ls.sw}"/><line x1="${x+w*.08}" y1="${y+shaftY}" x2="${x+w*.08}" y2="${y+h-shaftY}" stroke="${ls.stroke}" stroke-width="${ls.sw}"/><line x1="${x+w*.16}" y1="${y+shaftY}" x2="${x+w*.16}" y2="${y+h-shaftY}" stroke="${ls.stroke}" stroke-width="${ls.sw}"/></g>`}
  if(p==='cloudCallout'){const cx=x+w*.48,cy=y+h*.42,rx=w*.42,ry=h*.34;return`${prefix}<g${tr}><ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}" fill="none" stroke="${ls.stroke}" stroke-width="${ls.sw}"/><circle cx="${x+w*.34}" cy="${y+h*.78}" r="${Math.min(w,h)*.07}" fill="none" stroke="${ls.stroke}" stroke-width="${ls.sw}"/><circle cx="${x+w*.24}" cy="${y+h*.92}" r="${Math.min(w,h)*.035}" fill="none" stroke="${ls.stroke}" stroke-width="${ls.sw}"/></g>`}
  unsupported.add(p||'(missing)');return`${prefix}<g${tr}><rect x="${x}" y="${y}" width="${w}" height="${h}" fill="none" stroke="#d000d0" stroke-width="${Math.max(ls.sw,8000)}" stroke-dasharray="18000 12000"/><text x="${x+w*.02}" y="${y+h*.18}" font-size="${Math.max(30000,h*.12)}" fill="#d000d0">UNSUPPORTED ${esc(p||'shape')}</text></g>`;
}
function svgShape(shape,unsupported,markerId){return svgShapeInfo(shapeInfo(shape),unsupported,markerId)}
function paragraphTextBoxHtml(p,ctx,shapeDefaults={}){
  const pPr=first(p,NS.w,'pPr'),align=attr(first(pPr,NS.w,'jc'),NS.w,'val')||'',pRunPr=first(pPr,NS.w,'rPr'),pDefaults=runMetrics(pRunPr,ctx.themes||{}),bits=[];
  for(const r of q(p,NS.w,'r')){
    if(ancestorNS(r,NS.m,'oMath')||ancestorNS(r,NS.m,'oMathPara'))continue;
    const rp=runProps(r,ctx.themes||{});
    if(pDefaults.bold&&!rp.b)rp.b=true;if(pDefaults.italic&&!rp.i)rp.i=true;if(pDefaults.underline&&!rp.u)rp.u=true;
    if(!rp.fontFamily&&pDefaults.fontFamily)rp.fontFamily=pDefaults.fontFamily;if(!rp.fontSizePx&&pDefaults.fontSizePx)rp.fontSizePx=pDefaults.fontSizePx;if(!rp.color&&pDefaults.textColor)rp.color=pDefaults.textColor;
    // DrawingML shape fontRef is the default color for runs that do not carry
    // an explicit Word color. This matters for mixed visible/hidden shape text:
    // Word can intentionally make helper runs white while another run is black.
    if(!rp.color&&shapeDefaults.textColor)rp.color=shapeDefaults.textColor;
    let piece='';for(const ch of elems(r)){if(lname(ch)==='t')piece+=wrapRun(ch.textContent,rp);else if(lname(ch)==='tab')piece+='&emsp;';else if(lname(ch)==='br')piece+='<br>'}bits.push(piece)
  }
  const inf=pInfo(p,ctx.styles,ctx.nums,ctx.themes||{}),rawMarker=visibleListMarker(inf,null),plain=stripHtml(bits.join('')).trim();
  let marker='';
  if(rawMarker&&markerLooksLikeBullet(rawMarker,inf.numFmt)&&!plain.startsWith(rawMarker)){
    const markerCss=[];if(inf.listMarkerFontFamily)markerCss.push('font-family:'+JSON.stringify(inf.listMarkerFontFamily));
    marker='<span class="docx-shape-list-marker"'+(markerCss.length?' style="'+esc(markerCss.join(';'))+'"':'')+'>'+esc(rawMarker)+'</span>&nbsp;';
  }
  const css=[];if(align)css.push('text-align:'+(align==='both'?'justify':align));const spacing=first(pPr,NS.w,'spacing'),line=attr(spacing,NS.w,'line'),rule=attr(spacing,NS.w,'lineRule');if(line&&rule==='exact')css.push(`line-height:${twipsToPx(line)}px`);
  return`<div${css.length?` style="${esc(css.join(';'))}"`:''}>${marker}${bits.join('')}</div>`
}
function shapeTextHtml(shape,ctx){const box=first(shape,NS.w,'txbxContent');if(!box)return'';const defaults={textColor:shapeTextDefaultColor(shape)};return elems(box).filter(x=>lname(x)==='p').map(p=>paragraphTextBoxHtml(p,ctx,defaults)).join('').replace(/<div[^>]*>\s*<\/div>/g,'').trim()}
function normalizedOverlayGeometry(rect,rootExt){return{x:Math.max(0,rect.x/rootExt.extX),y:Math.max(0,rect.y/rootExt.extY),width:Math.max(.001,rect.width/rootExt.extX),height:Math.max(.001,rect.height/rootExt.extY)}}
function nativeGroupRender(group,ctx,id){
  const rootX=groupXfrm(group);if(!rootX)return null;
  const unsupported=new Set(),markerId='bw-arrow-'+id.replace(/[^A-Za-z0-9_-]/g,'_'),overlays=[],parts=[],pictureLayers=[],rootM=groupRootMatrix(rootX);
  let textCount=0,equationCount=0,pictureCount=0,shapeCount=0;
  const addShape=(child,m)=>{
    // HF21 GROUP STYLE TRUTH:
    // DrawingML group xfrm maps child geometry into the outer group coordinate
    // system, but a:ln width and pattern density are physical style properties,
    // not arbitrary child-coordinate units. Applying the group matrix to an SVG
    // <g> scaled those styles as well (sometimes by x600+), turning thin curves
    // and hatch fills into solid black rectangles. Flatten group geometry first
    // and render the style once in root coordinates.
    const localInfo=shapeInfo(child),info=mapBoxThroughGroupMatrix(localInfo,m);
    parts.push(svgShapeInfo(info,unsupported,markerId));shapeCount++;
    const rect={x:info.x,y:info.y,width:info.w,height:info.h},geometry=normalizedOverlayGeometry(rect,rootX),html=shapeTextHtml(child,ctx),maths=q(child,NS.m,'oMath');
    if(html&&stripHtml(html))overlays.push({id:`${id}-text-${++textCount}`,type:'text',html,plainText:stripHtml(html),geometry,visible:true});
    for(const math of maths){
      const mathml=ommlToMath(math,true),fontSizePt=(runMetrics(first(first(math,NS.m,'ctrlPr'),NS.w,'rPr'),ctx.themes||{}).fontSizePx||0)*.75;
      overlays.push({id:`${id}-eq-${++equationCount}`,type:'equation',mathml,plainText:allText(math),geometry,visible:true,...(fontSizePt?{fontSizePt}:{})});
    }
  };
  const addPicture=(child,m)=>{
    const token=`__BW_NATIVE_GROUP_IMAGE_${++pictureCount}__`,local=pictureInfo(child,ctx,token);
    if(!local)return;
    const info=mapBoxThroughGroupMatrix(local,m),clipId=`bw-native-clip-${id.replace(/[^A-Za-z0-9_-]/g,'_')}-${pictureCount}`;
    parts.push(svgPictureLayer(info,clipId));
    pictureLayers.push({token,path:info.path,rawPath:info.rawPath,rid:info.rid});
  };
  const walk=(container,parentMatrix)=>{
    const gx=groupXfrm(container),local=gx?groupMatrix(gx):matrixIdentity(),m=matrixMul(parentMatrix,local);
    for(const child of elems(container)){
      if(child.namespaceURI===NS.wpg&&lname(child)==='grpSp'){walk(child,m);continue}
      if(child.namespaceURI===NS.pic&&lname(child)==='pic'){addPicture(child,m);continue}
      if(child.namespaceURI===NS.wps&&lname(child)==='wsp'){addShape(child,m);continue}
    }
  };
  // Root SVG coordinate space is the actual outer group extent. Child order is
  // preserved so picture + vectors remain one Word composite instead of being
  // emitted as unrelated figures.
  for(const child of elems(group)){
    if(child.namespaceURI===NS.wpg&&lname(child)==='grpSp')walk(child,rootM);
    else if(child.namespaceURI===NS.pic&&lname(child)==='pic')addPicture(child,rootM);
    else if(child.namespaceURI===NS.wps&&lname(child)==='wsp')addShape(child,rootM);
  }
  return{rootX,overlays,unsupported:[...unsupported],shapeCount,pictureCount,pictureLayers,svgBody:parts.join(''),markerId};
}

function nativeGroupRecords(paragraph,ctx,sourceParagraph){
  const groups=q(paragraph,NS.wpg,'wgp').filter(g=>!ancestorNS(g,NS.wpg,'wgp')),out=[];let n=0;
  for(const group of groups){
    const drawing=ancestorNS(group,NS.w,'drawing');if(!drawing)continue;
    const ordinal=Number(ctx.nativeGroupOrdinals?.get(group)||0),surrogate=ordinal?ctx.groupSurrogateMap?.get(String(ordinal))||null:null,triageId=String(surrogate?.triageId||(`G${String(ordinal||0).padStart(4,'0')}`)),triageStage=String(surrogate?.triageStage||''),triageDecision=String(surrogate?.decision||'');
    const geom=imageGeometry(drawing),id=`bw-native-${sourceParagraph}-${++n}`;
    if(surrogate&&String(surrogate.fidelityClass||'')==='render-required'&&surrogate.path){
      const width=Math.max(1,Number(geom.width)||1),height=Math.max(1,Number(geom.height)||1),composite={version:5,id,nativeVector:false,wordRenderedBase:true,backgroundClean:false,immutableBase:true,basePixelPolicy:'word-rendered-whole-composite',status:'render-required-word-surrogate',fidelityClass:'render-required',fidelityReasons:Array.isArray(surrogate.reasons)?surrogate.reasons:[],baseGeometry:{x:0,y:0,width:1,height:1,lockAspect:true},overlays:[]};
      out.push({type:'figure',srcPath:String(surrogate.path),width,height,x:geom.x,y:geom.y,xAnchor:geom.xAnchor,yAnchor:geom.yAnchor,positionContract:geom.positionContract||null,geometryTruth:{version:3,geometrySource:'word-rendered-whole-composite-surrogate',nativeVector:false,groupOrdinal:ordinal,triageId,triageStage,triageDecision,fidelityClass:'render-required',fidelityReasons:Array.isArray(surrogate.reasons)?surrogate.reasons:[],captureGeometry:surrogate.captureGeometry||null,wpExtent:{width,height},cropActive:false,rotationDeg:0,flipH:false,flipV:false},triageId,triageStage,triageDecision,compositeId:id,composite,floating:geom.floating,placement:geom.placement,kind:'word-rendered-composite-surrogate',sourceParagraph});
      ctx.wordRenderedCompositeSurrogates=(ctx.wordRenderedCompositeSurrogates||0)+1;
      continue;
    }
    const render=nativeGroupRender(group,ctx,id);if(!render)continue;
    const effect=drawingEffectExtent(drawing),width=Math.max(1,Number(geom.width)||Math.round(render.rootX.extX/9525)),height=Math.max(1,Number(geom.height)||Math.round(render.rootX.extY/9525)),leftU=effect.left*render.rootX.extX/width,topU=effect.top*render.rootX.extY/height,rightU=effect.right*render.rootX.extX/width,bottomU=effect.bottom*render.rootX.extY/height,viewX=-leftU,viewY=-topU,viewW=render.rootX.extX+leftU+rightU,viewH=render.rootX.extY+topU+bottomU;
    const svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewX} ${viewY} ${viewW} ${viewH}" preserveAspectRatio="none"><defs>${svgMarkerDefs(render.markerId)}</defs>${render.svgBody}</svg>`;
    const srcPath=`__native__/${id}.svg`,mixed=render.pictureCount>0,composite={version:4,id,nativeVector:true,backgroundClean:true,immutableBase:false,basePixelPolicy:mixed?'native-mixed-picture-vector-svg':'native-drawingml-svg',status:render.unsupported.length?'native-vector-probe-with-unsupported-presets':(mixed?'native-mixed-picture-vector':'native-vector-drawingml'),effectExtentPx:effect,unsupportedPresets:render.unsupported,shapeCount:render.shapeCount,pictureCount:render.pictureCount,baseGeometry:{x:0,y:0,width:1,height:1,lockAspect:true},overlays:render.overlays};
    out.push({type:'figure',srcPath,nativeSvg:svg,nativeSvgImages:render.pictureLayers,width,height,x:geom.x,y:geom.y,xAnchor:geom.xAnchor,yAnchor:geom.yAnchor,positionContract:geom.positionContract||null,geometryTruth:{version:2,geometrySource:mixed?'native-wpg-mixed-svg':'native-wpg-svg',nativeVector:true,nativePictureCount:render.pictureCount,groupOrdinal:ordinal,triageId,triageStage,triageDecision,captureGeometry:surrogate?.captureGeometry||null,referencePath:String(surrogate?.referencePath||''),wpExtent:{width,height},effectExtentPx:effect,unsupportedPresets:render.unsupported,cropActive:false,rotationDeg:0,flipH:false,flipV:false},triageId,triageStage,triageDecision,compositeId:id,composite,floating:geom.floating,placement:geom.placement,kind:mixed?'native-wpg-mixed-svg':'native-wpg-svg',sourceParagraph});
    ctx.nativeVectorComposites=(ctx.nativeVectorComposites||0)+1;ctx.nativeVectorShapes=(ctx.nativeVectorShapes||0)+render.shapeCount;ctx.nativeVectorPictures=(ctx.nativeVectorPictures||0)+render.pictureCount;ctx.nativeMixedGroups=(ctx.nativeMixedGroups||0)+(mixed?1:0);ctx.nativeVectorTextOverlays=(ctx.nativeVectorTextOverlays||0)+render.overlays.filter(x=>x.type==='text').length;ctx.nativeVectorEquationOverlays=(ctx.nativeVectorEquationOverlays||0)+render.overlays.filter(x=>x.type==='equation').length;for(const p of render.unsupported)ctx.nativeVectorUnsupportedPresets.add(p)
  }
  return out
}

function paragraphSegments(p,ctx,sourceParagraph=0){
  const info=pInfo(p,ctx.styles,ctx.nums,ctx.themes),section=ctx.sectionInfo||null;
  const makeSeg=extra=>({html:'',images:[],flow:[],_flowHtml:'',page:ctx.page,sourceParagraph,breakBefore:info.paragraphStyle?.pageBreakBefore?'style':null,columnIndex:Number(ctx.columnIndex)||0,section,...(extra||{})});
  let segments=[makeSeg()];const cur=()=>segments[segments.length-1];
  const appendHtml=value=>{const h=String(value||'');if(!h)return;cur().html+=h;cur()._flowHtml+=h};
  const flushFlow=seg=>{const target=seg||cur();if(target._flowHtml){target.flow.push({type:'html',html:target._flowHtml});target._flowHtml=''}};
  function pageBreak(kind='rendered'){flushFlow();ctx.detectedBreaks=(ctx.detectedBreaks||0)+1;ctx.page++;ctx.columnIndex=0;segments.push(makeSeg({page:ctx.page,breakBefore:kind,columnIndex:0}))}
  function columnBreak(){flushFlow();ctx.columnIndex=(Number(ctx.columnIndex)||0)+1;segments.push(makeSeg({page:ctx.page,columnBreakBefore:'explicit',columnIndex:Number(ctx.columnIndex)||0}))}
  function walk(n,href=''){
    const l=lname(n);
    if(l==='AlternateContent'){
      const children=elems(n),chosen=children.find(c=>lname(c)==='Choice')||children.find(c=>lname(c)==='Fallback');
      const skipped=children.find(c=>c!==chosen&&lname(c)==='Fallback');
      if(skipped){ctx.alternateFallbacksSkipped++;ctx.fallbackImagesSkipped+=imageRefs(skipped).length}
      if(chosen)for(const c of elems(chosen))walk(c,href);
      return;
    }
    if(l==='Choice'||l==='Fallback'){for(const c of elems(n))walk(c,href);return}
    if(l==='txbxContent')return;
    if(l==='lastRenderedPageBreak'){if(ctx.useRenderedBreaks)pageBreak('rendered');return}
    if(l==='br'&&attr(n,NS.w,'type')==='column'){columnBreak();return}
    if(l==='br'&&attr(n,NS.w,'type')==='page'&&!ctx.hasRenderedBreaks){pageBreak('explicit');return}
    if(l==='oMathPara'){appendHtml(ommlToMath(n,true));ctx.displayMath++;ctx.importedMathObjects+=q(n,NS.m,'oMath').length;return}
    if(l==='oMath'){appendHtml(ommlToMath(n,false));ctx.inlineMath++;ctx.importedMathObjects++;return}
    if(l==='drawing'||l==='pict'||l==='object'){
      // Native wpg groups are emitted by nativeGroupRecords. Other Word media
      // keep their exact position in the paragraph flow when they are inline.
      if(l==='drawing'&&q(n,NS.wpg,'wgp').length)return;
      const oleOccurrence=l==='object'?Number(ctx.oleObjectOrdinals?.get(n)||0):0,oleOccurrencePath=oleOccurrence?String(ctx.oleOccurrenceSurrogateMap?.get(String(oleOccurrence))||''):'',oleAudit=oleOccurrence?ctx.oleOccurrenceAuditMap?.get(String(oleOccurrence))||null:null,oleTriageId=oleOccurrence?`OLE${String(oleOccurrence).padStart(4,'0')}`:'';
      for(const ref of imageRefs(n)){
        const rr=ctx.rels.get(ref.rid);if(!rr)continue;
        const geom=imageGeometry(n,ref.rid),rawPath=('word/'+rr.target.replace(/^\.\.\//,'')).replace(/\\/g,'/'),path=oleOccurrencePath||ctx.vectorSurrogateMap?.get(rawPath)||rawPath;
        const compositeId=String(geom.positionHint?.raw?.compositeId||geom.positionHint?.raw?.bw?.compositeId||''),composite=compositeId?ctx.compositeMap?.get(compositeId)||null:null;
        const image={rid:ref.rid,path,kind:ref.kind,...geom,geometryTruth:{...(geom.geometryTruth||{}),...(oleOccurrence?{oleOccurrence,oleTriageId,oleRenderMethod:String(oleAudit?.method||''),oleRendered:!!oleOccurrencePath,oleSourcePage:Number(oleAudit?.sourcePage)||0,oleContextKey:String(oleAudit?.contextKey||'')}:{}),...(oleOccurrencePath?{geometrySource:'word-ole-occurrence-surrogate'}:{})},triageId:oleTriageId||'',triageStage:oleOccurrence?(oleOccurrencePath?'ole-rendered':'ole-vector-fallback'):'',triageDecision:oleOccurrence?(oleOccurrencePath?'word-ole-surrogate':'vector-preview-fallback'):'',compositeId,composite,sourceParagraph,positionContract:{...(geom.positionContract||{}),anchor:{kind:geom.positionContract?.mode==='paragraph-anchored'?'paragraph':'page',sourceParagraph:Number(sourceParagraph)||null,itemId:''}}};
        cur().images.push(image);ctx.rawImageRefs++;
        // HF19: keep the anchor point in the paragraph flow even for floating
        // table-cell media.  Outside tables seg.flow is only diagnostic, while
        // tableBlock can now resolve paragraph-relative coordinates without
        // pretending that every Word posOffset is cell-top-left relative.
        flushFlow();cur().flow.push({type:'image',image,floating:!!geom.floating})
      }
      return;
    }
    if(l==='r'){
      const pr=runProps(n,ctx.themes);
      for(const c of elems(n)){
        const cl=lname(c);
        if(cl==='t')appendHtml(wrapRun(c.textContent,pr,href));
        else if(cl==='tab')appendHtml('&emsp;');
        else if(cl==='br'){
          const kind=attr(c,NS.w,'type');if(kind==='column')columnBreak();else if(kind==='page'){if(!ctx.hasRenderedBreaks)pageBreak('explicit')}else appendHtml('<br>')
        }else if(cl==='lastRenderedPageBreak'){if(ctx.useRenderedBreaks)pageBreak('rendered')}else walk(c,href)
      }
      return;
    }
    if(l==='hyperlink'){const id=attr(n,NS.r,'id'),anchor=attr(n,NS.w,'anchor');const target=ctx.rels.get(id)?.target||(anchor?'#'+BookModelV4.normalizeId(anchor):'');for(const c of elems(n))walk(c,target);return}
    for(const c of elems(n))walk(c,href);
  }
  for(const c of elems(p)){if(lname(c)==='pPr')continue;walk(c)}
  for(const seg of segments)flushFlow(seg);
  const hyphenate=!!ctx.settings?.autoHyphenation&&info.paragraphStyle?.suppressAutoHyphens!==true;
  return segments.map(seg=>{const{_flowHtml,...clean}=seg;return{...clean,info,paragraphStyle:info.paragraphStyle||{},hyphenate,plain:seg.html.replace(/<[^>]+>/g,'').replace(/&emsp;/g,' ').trim()}}).filter(seg=>seg.html.trim()||seg.images.length);
}
function tablePositionContract(tbl,sourceParagraph=0){
  const tblPr=first(tbl,NS.w,'tblPr'),pos=first(tblPr,NS.w,'tblpPr');
  if(!pos)return null;
  const horz=String(attr(pos,NS.w,'horzAnchor')||'column'),vert=String(attr(pos,NS.w,'vertAnchor')||'paragraph');
  const xSpec=String(attr(pos,NS.w,'tblpXSpec')||'').toLowerCase(),ySpec=String(attr(pos,NS.w,'tblpYSpec')||'').toLowerCase();
  const xRaw=attr(pos,NS.w,'tblpX'),yRaw=attr(pos,NS.w,'tblpY'),x=xRaw!==''?twipsToPx(xRaw):null,y=yRaw!==''?twipsToPx(yRaw):null;
  const left=twipsToPx(attr(pos,NS.w,'leftFromText')),right=twipsToPx(attr(pos,NS.w,'rightFromText')),top=twipsToPx(attr(pos,NS.w,'topFromText')),bottom=twipsToPx(attr(pos,NS.w,'bottomFromText'));
  return{version:2,mode:'paragraph-anchored',renderMode:'flow-wrap',xPx:Number.isFinite(x)?x:null,yPx:Number.isFinite(y)?y:null,horizontal:{relativeFrom:horz,align:xSpec,offsetPx:Number.isFinite(x)?x:null},vertical:{relativeFrom:vert,align:ySpec,offsetPx:Number.isFinite(y)?y:null},anchor:{kind:'paragraph',sourceParagraph:Number(sourceParagraph)||null,itemId:''},wrap:{type:'wrapSquare',side:'bothSides',distTopPx:Math.max(0,top||0),distRightPx:Math.max(0,right||0),distBottomPx:Math.max(0,bottom||0),distLeftPx:Math.max(0,left||0),contourApplied:false},stacking:{relativeHeight:0,behindDoc:false,allowOverlap:true,layoutInCell:true,locked:false},source:'w:tblpPr'};
}
function tablePlacement(position,columnWidthsPx=[]){
  const align=String(position?.horizontal?.align||'').toLowerCase();
  if(['left','inside'].includes(align))return'float-left';
  if(['right','outside'].includes(align))return'float-right';
  const x=Number(position?.xPx??position?.horizontal?.offsetPx);
  const width=(columnWidthsPx||[]).reduce((a,v)=>a+(Number(v)||0),0);
  return Number.isFinite(x)&&x+width/2>360?'float-right':'float-left';
}
function tableDeclaredWidth(tbl,columnWidthsPx=[]){
  const tblPr=first(tbl,NS.w,'tblPr'),tblW=first(tblPr,NS.w,'tblW'),kind=String(attr(tblW,NS.w,'type')||'dxa').toLowerCase(),raw=attr(tblW,NS.w,'w');
  if(kind==='dxa'&&raw!==''){const width=twipsToPx(raw);if(width>0)return width;}
  return(columnWidthsPx||[]).reduce((a,v)=>a+(Number(v)||0),0)||undefined;
}
function tableBlock(tbl,ctx,page,sourceParagraph=0){
  const grid=first(tbl,NS.w,'tblGrid'),columnWidthsPx=elems(grid).filter(x=>lname(x)==='gridCol').map(x=>twipsToPx(attr(x,NS.w,'w'))).filter(x=>x>0),sourcePositionContract=tablePositionContract(tbl,sourceParagraph),declaredWidth=tableDeclaredWidth(tbl,columnWidthsPx),rows=[];let headerRows=0,stillHeader=true,maxColumns=1,rowIndex=0;
  const mappedRows=new Map((ctx.currentWordPageBlock?.rows||[]).map(r=>[Number(r.row),r]));
  for(const tr of elems(tbl).filter(x=>lname(x)==='tr')){
    rowIndex++;const mappedRow=mappedRows.get(rowIndex)||null;
    const trPr=first(tr,NS.w,'trPr'),isHeader=!!first(trPr,NS.w,'tblHeader'),allowBreakAcrossPages=!first(trPr,NS.w,'cantSplit'),renderedPageBreaks=q(tr,NS.w,'lastRenderedPageBreak').length,explicitPageBreaks=q(tr,NS.w,'br').filter(n=>attr(n,NS.w,'type')==='page').length;if(stillHeader&&isHeader)headerRows++;else stillHeader=false;
    const cells=[];let columns=0;
    const rowCells=elems(tr).filter(x=>lname(x)==='tc'),mappedCells=new Map((mappedRow?.cells||[]).map(c=>[Number(c.cell),c]));
    for(let cellIndex=0;cellIndex<rowCells.length;cellIndex++){
      const tc=rowCells[cellIndex],mappedCell=mappedCells.get(cellIndex+1)||null,mappedParagraphs=new Map((mappedCell?.paragraphs||[]).map(p=>[Number(p.paragraph),p]));
      const oleCount=q(tc,NS.o,'OLEObject').length,rootGroupCount=q(tc,NS.wpg,'wgp').filter(g=>!ancestorNS(g,NS.wpg,'wgp')).length,customGeomCount=q(tc,NS.a,'custGeom').length;
      if(oleCount||rootGroupCount){ctx.tableCompositeCells=(ctx.tableCompositeCells||0)+1;if(oleCount&&rootGroupCount)ctx.tableOlePlusGroupCells=(ctx.tableOlePlusGroupCells||0)+1;else if(oleCount>1)ctx.tableMultiOleCells=(ctx.tableMultiOleCells||0)+1;else if(oleCount)ctx.tableOleOnlyCells=(ctx.tableOleOnlyCells||0)+1;else ctx.tableGroupOnlyCells=(ctx.tableGroupOnlyCells||0)+1}
      if(customGeomCount)ctx.tableCustomGeometryCells=(ctx.tableCustomGeometryCells||0)+1;
      const tcPr=first(tc,NS.w,'tcPr'),span=Math.max(1,Number(attr(first(tcPr,NS.w,'gridSpan'),NS.w,'val'))||1),shade=nodeColor(first(tcPr,NS.w,'shd'),'fill'),vAlign=attr(first(tcPr,NS.w,'vAlign'),NS.w,'val')||'top',tcW=twipsToPx(attr(first(tcPr,NS.w,'tcW'),NS.w,'w'));
      let html='',cellParagraphIndex=0;const paragraphStyles=[],images=[],paragraphs=[],paragraphFlowRecords=[];let hasInlineMediaFlow=false;
      for(const cp of elems(tc).filter(x=>lname(x)==='p')){
        cellParagraphIndex++;const mappedParagraph=mappedParagraphs.get(cellParagraphIndex)||null,paragraphPage=Number(mappedParagraph?.startPage)||Number(mappedRow?.startPage)||Number(page)||1;let paragraphHtml='';const paragraphImages=[],paragraphFlow=[];
        const addParagraphHtml=h=>{const value=String(h||'');if(!value)return;paragraphFlow.push({type:'html',html:value})};
        const addParagraphMedia=index=>{if(!Number.isFinite(Number(index)))return;paragraphFlow.push({type:'media',mediaIndex:Number(index)});hasInlineMediaFlow=true};
        // HF19: every native group keeps the Word paragraph that owns its anchor.
        // Inline groups stay inline; paragraph-relative floating groups are later
        // positioned inside that paragraph, not against the entire <td>.
        for(const ng of nativeGroupRecords(cp,ctx,sourceParagraph)){
          const record={srcPath:ng.srcPath,nativeSvg:ng.nativeSvg,nativeSvgImages:ng.nativeSvgImages||[],width:ng.width,height:ng.height,kind:ng.kind,geometryTruth:ng.geometryTruth||null,compositeId:ng.compositeId||'',composite:ng.composite||null,alt:'',sourceParagraph:ng.sourceParagraph||sourceParagraph,sourcePage:paragraphPage,cellParagraphIndex,floating:!!ng.floating,placement:ng.placement||'',x:ng.x,y:ng.y,xAnchor:ng.xAnchor||'',yAnchor:ng.yAnchor||'',positionContract:deepClone(ng.positionContract||null)};
          images.push(record);paragraphImages.push(record);addParagraphMedia(images.length-1)
        }
        const local={...ctx,page};const before={inlineMath:local.inlineMath,displayMath:local.displayMath,importedMathObjects:local.importedMathObjects,rawImageRefs:local.rawImageRefs};const segs=paragraphSegments(cp,local,sourceParagraph);
        ctx.inlineMath+=local.inlineMath-before.inlineMath;ctx.displayMath+=local.displayMath-before.displayMath;ctx.importedMathObjects+=local.importedMathObjects-before.importedMathObjects;ctx.rawImageRefs+=local.rawImageRefs-before.rawImageRefs;
        for(let segIndex=0;segIndex<segs.length;segIndex++){
          const seg=segs[segIndex];if(html)html+='<br>';html+=seg.html;if(paragraphHtml)paragraphHtml+='<br>';paragraphHtml+=seg.html;paragraphStyles.push(seg.paragraphStyle||{});
          if(segIndex>0&&paragraphFlow.length)addParagraphHtml('<br>');
          const rawToCell=new Map();
          for(const im of seg.images||[]){
            const role=hintRole(im.positionHint);if(skipImageRole(role)||textImageRole(role))continue;
            const record={srcPath:im.path,width:im.width,height:im.height,kind:im.kind,geometryTruth:im.geometryTruth||null,compositeId:im.compositeId||'',composite:im.composite||null,alt:im.positionHint?.alt||'',sourceParagraph:im.sourceParagraph||sourceParagraph,sourcePage:paragraphPage,cellParagraphIndex,floating:!!im.floating,placement:im.placement||'',x:im.x,y:im.y,xAnchor:im.xAnchor||'',yAnchor:im.yAnchor||'',positionContract:deepClone(im.positionContract||null)};
            images.push(record);paragraphImages.push(record);rawToCell.set(im,images.length-1)
          }
          for(const token of seg.flow||[]){
            if(token?.type==='html')addParagraphHtml(String(token.html||''));
            else if(token?.type==='image'){const index=rawToCell.get(token.image);if(index!==undefined)addParagraphMedia(index)}
          }
        }
        const paragraphStyle=segs[0]?.paragraphStyle||{};
        if(paragraphHtml.trim()||paragraphImages.length)paragraphs.push({html:paragraphHtml,sourcePage:paragraphPage,paragraphIndex:cellParagraphIndex,paragraphStyle,images:paragraphImages});
        paragraphFlowRecords.push({type:'paragraph',paragraphIndex:cellParagraphIndex,sourcePage:paragraphPage,style:paragraphStyle,tokens:paragraphFlow});
      }
      const inlineMediaFlow=hasInlineMediaFlow?paragraphFlowRecords:[];
      const style={verticalAlign:vAlign};if(shade)style.backgroundColor=shade;if(tcW>0)style.widthPx=tcW;
      cells.push({html,colspan:span,rowspan:1,style,paragraphStyle:paragraphStyles[0]||{},images,paragraphs,...(hasInlineMediaFlow?{inlineMediaFlow}:{} )});columns+=span;
      if(hasInlineMediaFlow)ctx.tableInlineMediaFlowCells=(ctx.tableInlineMediaFlowCells||0)+1;
    }
    const rowStart=Number(mappedRow?.startPage)||Number(page)||1,rowEnd=Number(mappedRow?.endPage)||rowStart;
    maxColumns=Math.max(maxColumns,columns);rows.push({cells,allowBreakAcrossPages,renderedPageBreaks,explicitPageBreaks,sourcePage:rowStart,sourcePageEnd:Math.max(rowStart,rowEnd),sourceRowSpansPages:rowEnd>rowStart});
  }
  const html='<table>'+rows.map(r=>'<tr>'+r.cells.map(c=>'<td'+(c.colspan>1?' colspan="'+c.colspan+'"':'')+'>'+c.html+'</td>').join('')+'</tr>').join('')+'</table>';
  const sourceLayout=ctx.layoutSource||{},margins=sourceLayout.marginsPx||{},bodyWidth=Math.max(1,Number(sourceLayout.pageWidthPx||793)-(Number(margins.left)||0)-(Number(margins.right)||0));
  const largeFlowTable=!!sourcePositionContract&&((Number(declaredWidth)||0)>bodyWidth*.78||rows.length>=12),positionContract=largeFlowTable?null:sourcePositionContract;
  const tableRenderedBreaks=q(tbl,NS.w,'lastRenderedPageBreak').length,tableExplicitBreaks=q(tbl,NS.w,'br').filter(n=>attr(n,NS.w,'type')==='page').length;
  return{type:'table',html,source:'table',page,sourcePageEnd:Number(ctx.currentWordPageBlock?.endPage)||Number(page)||1,rows,columns:maxColumns,sourcePageSpan:Math.max(1,(Number(ctx.currentWordPageBlock?.endPage)||Number(page)||1)-Number(page)+1),tableRenderedBreaks,tableExplicitBreaks,tableStyle:{columnWidthsPx,headerRows,keepTogether:largeFlowTable,widthPx:declaredWidth,layoutMode:positionContract?'floating-around':'flow'},width:declaredWidth,floating:!!positionContract,placement:positionContract?tablePlacement(positionContract,columnWidthsPx):'wide',positionContract,sourcePositionContract:largeFlowTable?sourcePositionContract:null,sourceAroundConvertedToFlow:largeFlowTable,sourceParagraph};
}

function ext(path){const m=path.match(/\.([a-zA-Z0-9]+)$/);return m?m[1].toLowerCase():'bin'}
function mime(path){return({png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',gif:'image/gif',svg:'image/svg+xml',emf:'image/x-emf',wmf:'image/x-wmf',bmp:'image/bmp',tif:'image/tiff',tiff:'image/tiff'})[ext(path)]||'application/octet-stream'}
async function blobHash(blob){
  const bytes=new Uint8Array(await blob.arrayBuffer());
  if(globalThis.crypto?.subtle){const digest=await crypto.subtle.digest('SHA-256',bytes);return [...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('')}
  let h=2166136261;for(const b of bytes){h^=b;h=Math.imul(h,16777619)}return 'fnv-'+(h>>>0).toString(16)+'-'+bytes.length;
}
function imageScore(b){return (b.kind==='drawing'?1000:0)+(b.floating?500:0)+(b.width||0)+(b.height?Math.min(b.height,300):0)}
async function blobDataUrl(blob,type='application/octet-stream'){
  const bytes=new Uint8Array(await blob.arrayBuffer());let binary='';const chunk=0x8000;
  for(let i=0;i<bytes.length;i+=chunk)binary+=String.fromCharCode(...bytes.subarray(i,i+chunk));
  return`data:${type||blob.type||'application/octet-stream'};base64,${btoa(binary)}`
}
async function materializeNativeSvg(meta,zip){
  let svg=String(meta?.svg||''),embedded=0,missing=0;
  for(const layer of meta?.images||[]){
    const token=String(layer?.token||''),path=String(layer?.path||'');if(!token||!path)continue;
    const f=zip.file(path);if(!f){missing++;svg=svg.split(token).join('');continue}
    const source=await f.async('blob'),uri=await blobDataUrl(source,mime(path));svg=svg.split(token).join(uri);embedded++;
  }
  return{svg,embedded,missing}
}
async function finalizeImages(pages,zip){
  const tableImages=block=>block?.type==='table'?(block.rows||[]).flatMap(row=>(row.cells||[]).flatMap(cell=>cell.images||[])):[];
  const paths=new Set(),nativeSvg=new Map();
  for(const arr of pages.values())for(const b of arr){
    if(b.type==='figure'){paths.add(b.srcPath);if(b.nativeSvg)nativeSvg.set(b.srcPath,{svg:b.nativeSvg,images:b.nativeSvgImages||[]})}
    for(const im of tableImages(b))if(im.srcPath){paths.add(im.srcPath);if(im.nativeSvg)nativeSvg.set(im.srcPath,{svg:im.nativeSvg,images:im.nativeSvgImages||[]})}
  }
  const blobs=new Map(),hashes=new Map();let nativeGroupEmbeddedImages=0,nativeGroupEmbeddedImagesMissing=0;
  for(const path of paths){
    const f=zip.file(path);let blob=null;
    if(f)blob=await f.async('blob');
    else if(nativeSvg.has(path)){
      const materialized=await materializeNativeSvg(nativeSvg.get(path),zip);nativeGroupEmbeddedImages+=materialized.embedded;nativeGroupEmbeddedImagesMissing+=materialized.missing;
      blob=new Blob([materialized.svg],{type:'image/svg+xml'});
    }
    if(!blob)continue;blobs.set(path,blob);hashes.set(path,await blobHash(blob))
  }
  // Preserve every Word image occurrence. Content equality is not occurrence equality.
  for(const [,arr] of pages)for(let i=0;i<arr.length;i++)if(arr[i]?.type==='figure')arr[i]={...arr[i],contentHash:hashes.get(arr[i].srcPath)||arr[i].srcPath};
  return{imageBlobs:blobs,usedImages:[...paths],duplicateImagesRemoved:0,nativeGroupEmbeddedImages,nativeGroupEmbeddedImagesMissing};
}

function textBoxAncestor(box){let n=box?.parentNode;while(n&& !['pict','drawing','object'].includes(lname(n)))n=n.parentNode;return n}
function textOutsideTables(box){
  let text='';
  for(const t of q(box,NS.w,'t')){
    let n=t.parentNode,insideTable=false;
    while(n&&n!==box){if(lname(n)==='tbl'){insideTable=true;break}n=n.parentNode}
    if(!insideTable)text+=t.textContent;
  }
  return text.replace(/\s+/g,' ').trim();
}
function topLevelTextBoxTables(box){
  return q(box,NS.w,'tbl').filter(tbl=>{let n=tbl.parentNode;while(n&&n!==box){if(lname(n)==='tbl')return false;n=n.parentNode}return true});
}
function textBoxRecords(paragraph,ctx,sourceParagraph){
  const records=[],seen=new Set();
  for(const box of q(paragraph,NS.w,'txbxContent')){
    if(nativeGroupAncestor(box))continue;
    const shape=textBoxAncestor(box),geom=imageGeometry(shape||box),tableNodes=topLevelTextBoxTables(box);
    ctx.textBoxTablesRaw=(ctx.textBoxTablesRaw||0)+q(box,NS.w,'tbl').length;
    const tableRecords=[],tableSeen=new Set();
    for(const tbl of tableNodes){
      const signature=allText(tbl).replace(/\s+/g,' ').trim().toLocaleLowerCase('el');
      if(!signature||tableSeen.has(signature))continue;tableSeen.add(signature);
      const table=tableBlock(tbl,ctx,ctx.page,sourceParagraph);
      table.fromTextBox=true;table.width=geom.width||table.width;table.sourceContainerHeight=geom.height;table.x=geom.x;table.y=geom.y;table.xAnchor=geom.xAnchor;table.yAnchor=geom.yAnchor;table.floating=true;table.placement=geom.placement||table.placement||'float-right';
      table.tableStyle={...(table.tableStyle||{}),widthPx:table.width,layoutMode:'floating-around'};
      table.positionContract={...(geom.positionContract||{}),renderMode:'flow-wrap',widthPx:Number(table.width)||undefined,anchor:{kind:'paragraph',sourceParagraph:Number(sourceParagraph)||null,itemId:''},wrap:{...((geom.positionContract||{}).wrap||{}),type:'wrapSquare'}};
      tableRecords.push(table);
    }
    const text=textOutsideTables(box);let html='',paragraphStyle={};const images=[];
    for(const bp of elems(box).filter(x=>lname(x)==='p')){const local={...ctx,page:ctx.page,hasRenderedBreaks:false};const before={inlineMath:local.inlineMath,displayMath:local.displayMath,importedMathObjects:local.importedMathObjects,rawImageRefs:local.rawImageRefs};const segs=paragraphSegments(bp,local,sourceParagraph);ctx.inlineMath+=local.inlineMath-before.inlineMath;ctx.displayMath+=local.displayMath-before.displayMath;ctx.importedMathObjects+=local.importedMathObjects-before.importedMathObjects;ctx.rawImageRefs+=local.rawImageRefs-before.rawImageRefs;for(const seg of segs){if(html&&seg.html)html+='<br>';html+=seg.html;if(!Object.keys(paragraphStyle).length)paragraphStyle=seg.paragraphStyle||{};for(const im of seg.images||[]){const role=hintRole(im.positionHint);if(skipImageRole(role)||textImageRole(role))continue;images.push(im)}}}
    const key=(text||tableRecords.map(t=>stripHtml(t.html||'')).join('|')||images.map(im=>im.path||im.rid||'image').join('|')).toLocaleLowerCase('el');
    if(!key||seen.has(key))continue;seen.add(key);
    ctx.textBoxTablesImported=(ctx.textBoxTablesImported||0)+tableRecords.length;
    const caption=/^(Εικόνα|Σχήμα|Γράφημα|Figure|Fig\.|Graph)\s*\d*/i.test(text);
    const substantive=!!text&&!caption&&(text.length>=170||/^(Πυκνότητα!|Σχόλιο|Παρατήρηση|Θυμήσου|Να πάρεις υπόψη|Η εξίσωση Schrödinger)/i.test(text));
    const labelMatch=text.match(/^([^.!?]{2,45}[!?:])\s+/u);
    records.push({text,html:html||esc(text),caption,substantive,label:labelMatch?labelMatch[1]:'',width:geom.width,height:geom.height,x:geom.x,y:geom.y,xAnchor:geom.xAnchor,yAnchor:geom.yAnchor,positionContract:{...(geom.positionContract||{}),anchor:{kind:geom.positionContract?.mode==='paragraph-anchored'?'paragraph':'page',sourceParagraph:Number(sourceParagraph)||null,itemId:''}},placement:geom.placement||'wide',floating:geom.floating,paragraphStyle,sourceParagraph,tables:tableRecords,images});
  }
  return records;
}
function captionTexts(paragraph,ctx,sourceParagraph){return textBoxRecords(paragraph,ctx,sourceParagraph).filter(x=>x.caption).map(x=>x.text)}
function restartDecimalListItem(block){
  if(block?.type!=='list_item')return false;
  if(block.listType==='ul')return Number(block.level||0)===0&&['none','bullet'].includes(String(block.numFmt||'bullet'));
  if(block.listType!=='ol')return false;
  const fmt=String(block.numFmt||'decimal');
  if(!['decimal','decimalZero','none'].includes(fmt))return false;
  return Number(block.listOrdinal||1)===1&&Number(block.level||0)===0;
}
function syntheticRestartListRun(ordered,index){
  const current=ordered[index],next=ordered[index+1];
  return restartDecimalListItem(current)&&restartDecimalListItem(next)&&!next.breakBefore&&current.numId!==next.numId;
}
function sameSyntheticRestartList(first,next){
  return restartDecimalListItem(next)&&!next.breakBefore&&Number(first.level||0)===Number(next.level||0);
}
// HF6: legacy composite-equation fallback injection intentionally removed from the active importer.

async function parseDocx(file){
  unsupportedMath.clear();
  const zip=await JSZip.loadAsync(file);
  const doc=await readXml(zip,'word/document.xml');
  const stylesXml=await readXml(zip,'word/styles.xml',true);
  const theme=await readXml(zip,'word/theme/theme1.xml',true);
  const themes=themeFonts(theme);
  const layout=documentLayout(doc,stylesXml,themes);
  const styles=styleMap(stylesXml,themes,layout.defaults);
  const rels=relMap(await readXml(zip,'word/_rels/document.xml.rels',true));
  const nums=numberingMap(await readXml(zip,'word/numbering.xml',true));
  const settings=documentSettings(await readXml(zip,'word/settings.xml',true));
  const customProps=customProperties(await readXml(zip,'docProps/custom.xml',true));
  const compositeManifest=await readJson(zip,'customXml/bookwriter-composites.json',true)||{version:1,composites:{}};
  const compositeMap=new Map(Object.entries(compositeManifest.composites||{}));
  const vectorSurrogateManifest=await readJson(zip,'customXml/bookwriter-vector-surrogates.json',true)||{version:0,map:{},oleOccurrences:{}};
  const vectorSurrogateMap=new Map(Object.entries(vectorSurrogateManifest.map||{}));
  const oleOccurrenceSurrogateMap=new Map(Object.entries(vectorSurrogateManifest.oleOccurrences||{}));
  const oleOccurrenceAuditMap=new Map(Object.entries(vectorSurrogateManifest.oleOccurrenceAudit||{}));
  if(!oleOccurrenceAuditMap.size){for(const record of vectorSurrogateManifest.oleOccurrenceRecords||[]){for(const occ of record.occurrences||[]){oleOccurrenceAuditMap.set(String(Number(occ)||0),record)}}}
  const groupSurrogateManifest=await readJson(zip,'customXml/bookwriter-group-surrogates.json',true)||{version:0,groups:{},summary:{}};
  const groupSurrogateMap=new Map(Object.entries(groupSurrogateManifest.groups||{}));
  const oleObjectOrdinals=new Map(q(doc,NS.w,'object').map((node,index)=>[node,index+1]));
  const nativeGroupOrdinals=new Map(q(doc,NS.wpg,'wgp').filter(node=>!ancestorNS(node,NS.wpg,'wgp')).map((node,index)=>[node,index+1]));
  const wordPageMap=parsedWordPageMap(await readXml(zip,'customXml/bookwriter-page-map.xml',true));
  const wordPageMapActive=!!(wordPageMap?.available&&Number(wordPageMap?.pageCount)>0&&wordPageMap?.blocks&&typeof wordPageMap.blocks==='object');
  const pageBlock=index=>wordPageMapActive?(wordPageMap.blocks[String(index)]||null):null;
  const canonicalProfile=String(customProps.BookWriterImportMode||'').trim();
  if(canonicalProfile!==REQUIRED_WORD_PROFILE)throw new Error(`Το χαμηλού επιπέδου importer δέχεται μόνο ${REQUIRED_WORD_PROFILE}. Η εφαρμογή πρέπει να χρησιμοποιεί την αυτόματη πύλη εισαγωγής DOCX.`);
  const ctx={zip,styles,rels,nums,settings,themes,compositeMap,vectorSurrogateMap,oleOccurrenceSurrogateMap,oleOccurrenceAuditMap,groupSurrogateMap,nativeGroupOrdinals,oleObjectOrdinals,wordPageMap,wordPageMapActive,layoutSource:layout.source||{},page:1,listCounters:new Map(),mathCount:q(doc,NS.m,'oMath').length,inlineMath:0,displayMath:0,importedMathObjects:0,rawImageRefs:0,textBoxTablesRaw:0,textBoxTablesImported:0,tableInlineMediaFlowCells:0,tableTrustedAnchorsApplied:0,tableParagraphAnchorsApplied:0,tableAnchorsDeferred:0,nativeVectorComposites:0,nativeVectorShapes:0,nativeVectorPictures:0,nativeMixedGroups:0,nativeVectorTextOverlays:0,nativeVectorEquationOverlays:0,nativeVectorUnsupportedPresets:new Set(),alternateFallbacksSkipped:0,fallbackImagesSkipped:0,detectedBreaks:0,reconciledBreaks:0,useRenderedBreaks:false,hasRenderedBreaks:false,skippedStaticToc:0,currentWordPageBlock:null};
  const ordered=[];let tables=0,paras=0,lists=0,paraIndex=0;const headings=[],headingIdCounts=new Map();let textBoxesUnique=0,textBoxCaptions=0,textBoxesImported=0,textBoxLabelsRetained=0,textBoxTablesRaw=0,textBoxTablesImported=0;
  const push=(block,page=ctx.page)=>ordered.push({...block,page:Number(page)||1});
  const body=first(doc,NS.w,'body'),sectionPlan=buildSectionPlan(body,layout.source||{});
  for(const child of elems(body)){
    const beforeBreaks=ctx.detectedBreaks,expectedBreaks=ctx.useRenderedBreaks?q(child,NS.w,'lastRenderedPageBreak').length:0;
    if(lname(child)==='p'){
      paras++;paraIndex++;ctx.currentWordPageBlock=pageBlock(paraIndex);if(ctx.currentWordPageBlock?.startPage)ctx.page=Number(ctx.currentWordPageBlock.startPage)||ctx.page;
      const sectionInfo=sectionPlan.get(paraIndex)||null;
      if(sectionInfo?.id!==ctx.currentSectionId){ctx.currentSectionId=sectionInfo?.id||'';ctx.columnIndex=0}
      ctx.sectionInfo=sectionInfo;
      const bookmarkNames=[...q(child,NS.w,'bookmarkStart')].map(x=>attr(x,NS.w,'name')).filter(x=>x&&x!=='_GoBack');const boxes=textBoxRecords(child,ctx,paraIndex);textBoxesUnique+=boxes.length;textBoxCaptions+=boxes.filter(x=>x.caption).length;textBoxTablesRaw=ctx.textBoxTablesRaw||0;textBoxTablesImported=ctx.textBoxTablesImported||0;
      const captions=boxes.filter(x=>x.caption);const labels=boxes.filter(x=>!x.caption&&!x.substantive).map(x=>({text:x.text,html:x.html}));let captionIndex=0;
      for(const box of boxes)for(const nestedTable of box.tables||[])if(nestedTable.floating){tables++;push({...nestedTable,section:sectionInfo||null,columnIndex:Number(ctx.columnIndex)||0},ctx.page);}
      for(const box of boxes)for(const im of box.images||[]){
        const width=Number(im.width)||Number(box.width)||1,height=Number(im.height)||Number(box.height)||1;
        push({type:'figure',srcPath:im.path,width,height,x:Number(box.x)||Number(im.x)||0,y:Number(box.y)||Number(im.y)||0,xAnchor:box.xAnchor||im.xAnchor||'',yAnchor:box.yAnchor||im.yAnchor||'',positionContract:box.positionContract||im.positionContract||null,positionHint:im.positionHint||null,geometryTruth:im.geometryTruth||null,compositeId:im.compositeId||'',composite:im.composite||null,floating:box.floating!==false,placement:box.placement||im.placement||'wide',kind:'textbox-'+(im.kind||'image'),sourceParagraph:paraIndex,caption:'',textLabels:[],section:sectionInfo||null,columnIndex:Number(ctx.columnIndex)||0},ctx.page);
        ctx.textBoxNestedImages=(ctx.textBoxNestedImages||0)+1;
      }
      const nativeGroups=nativeGroupRecords(child,ctx,paraIndex);for(const group of nativeGroups)push({...group,section:sectionInfo||null,columnIndex:Number(ctx.columnIndex)||0},ctx.page);
      const segs=paragraphSegments(child,ctx,paraIndex);let segmentNo=0,listOrdinalAssigned=false,listOrdinal=null;
      for(const seg of segs){const firstParagraphSegment=segmentNo++===0,paragraphBookmarkId=firstParagraphSegment?BookModelV4.normalizeId(bookmarkNames[0]||''):'';
        const t=seg.plain,inf=seg.info,breakBefore=seg.breakBefore||null;
        if(inf.isTocStyle){ctx.skippedStaticToc++;continue}
        let headingId='';
        if(inf.paragraphStyle?.wordFrame&&seg.html.trim()){
          textBoxesImported++;
          push({...frameBlock(seg,inf,layout.source||{}),section:seg.section||null,columnIndex:seg.columnIndex,columnBreakBefore:seg.columnBreakBefore||null},seg.page);
        }else if(inf.headingLevel&&t){
          headingId=BookModelV4.normalizeId(bookmarkNames[0]||stableHeadingId(t,inf.headingLevel,headingIdCounts));
          headings.push({page:seg.page,level:inf.headingLevel,text:t,id:headingId,headingStyle:inf.headingStyle,sourceStyle:inf.styleName,sourceParagraph:seg.sourceParagraph});
          push({type:inf.headingLevel===1?'part_title':'section_heading',title:t,level:inf.headingLevel,id:headingId,headingStyle:inf.headingStyle,sourceStyle:inf.styleName,sourceParagraph:seg.sourceParagraph,breakBefore,section:seg.section||null,columnIndex:seg.columnIndex,columnBreakBefore:seg.columnBreakBefore||null},seg.page);
        }else if(inf.listType&&t){
          const listMarker=visibleListMarker(inf,ctx.currentWordPageBlock),effectiveType=effectiveListType(inf,listMarker);
          if(!listOrdinalAssigned){
            const wordListValue=Number(ctx.currentWordPageBlock?.listValue);
            listOrdinal=effectiveType==='ol'?(Number.isFinite(wordListValue)&&wordListValue>0?wordListValue:nextListOrdinal({...inf,listType:'ol'},ctx.listCounters)):null;
            listOrdinalAssigned=true;
          }
          if(firstParagraphSegment){
            lists++;
            push({type:'list_item',id:paragraphBookmarkId,listType:effectiveType,numId:inf.numId,numFmt:inf.numFmt,listText:inf.listText,listString:String(ctx.currentWordPageBlock?.listString||''),visibleMarker:listMarker,listMarkerFontFamily:inf.listMarkerFontFamily||'',listOrdinal,level:inf.ilvl,html:seg.html,paragraphStyle:seg.paragraphStyle,hyphenate:seg.hyphenate,sourceStyle:inf.styleName,sourceParagraph:seg.sourceParagraph,breakBefore,section:seg.section||null,columnIndex:seg.columnIndex,columnBreakBefore:seg.columnBreakBefore||null},seg.page);
          }else{
            push({type:'paragraph',id:'',html:seg.html,paragraphStyle:seg.paragraphStyle,hyphenate:seg.hyphenate,sourceStyle:inf.styleName,sourceParagraph:seg.sourceParagraph,listContinuation:true,breakBefore,section:seg.section||null,columnIndex:seg.columnIndex,columnBreakBefore:seg.columnBreakBefore||null},seg.page);
          }
        }else if(seg.html.trim()){
          push({type:'paragraph',id:paragraphBookmarkId,html:seg.html,paragraphStyle:seg.paragraphStyle,hyphenate:seg.hyphenate,sourceStyle:inf.styleName,sourceParagraph:seg.sourceParagraph,breakBefore,section:seg.section||null,columnIndex:seg.columnIndex,columnBreakBefore:seg.columnBreakBefore||null},seg.page);
        }
        let imageNo=0;
        for(const im of seg.images){
          const captionBox=captions[captionIndex++]||null,caption=captionBox?.html||captionBox?.text||'';
          const imageLabels=imageNo===0?labels:[];imageNo++;
          if(imageLabels.length)textBoxLabelsRetained+=imageLabels.length;
          const role=hintRole(im.positionHint);
          if(textImageRole(role)){
            const html=im.positionHint?.html||esc(im.positionHint?.text||'');
            if(html)push({type:'paragraph',id:'',html,paragraphStyle:seg.paragraphStyle,hyphenate:seg.hyphenate,sourceStyle:'DOCX image text fragment',sourceParagraph:im.sourceParagraph,positionHint:im.positionHint||null,breakBefore,section:seg.section||null,columnIndex:seg.columnIndex,columnBreakBefore:seg.columnBreakBefore||null},seg.page);
            continue;
          }
          if(skipImageRole(role))continue;
          push({type:'figure',srcPath:im.path,width:im.width,height:im.height,x:im.x,y:im.y,xAnchor:im.xAnchor,yAnchor:im.yAnchor,positionContract:im.positionContract||null,positionHint:im.positionHint||null,geometryTruth:im.geometryTruth||null,compositeId:im.compositeId||'',composite:im.composite||null,floating:im.floating,placement:im.placement,kind:im.kind,sourceParagraph:im.sourceParagraph,caption,textLabels:imageLabels,breakBefore,section:seg.section||null,columnIndex:seg.columnIndex,columnBreakBefore:seg.columnBreakBefore||null},seg.page);
        }
      }
      for(const box of boxes)for(const nestedTable of box.tables||[])if(!nestedTable.floating){tables++;push({...nestedTable,section:sectionInfo||null,columnIndex:Number(ctx.columnIndex)||0},ctx.page);}
      for(const box of boxes.filter(x=>x.substantive)){
        textBoxesImported++;
        push({type:'textbox',html:box.html,text:box.text,label:box.label,width:box.width,height:box.height,x:box.x,y:box.y,xAnchor:box.xAnchor,yAnchor:box.yAnchor,positionContract:box.positionContract||null,placement:box.placement,floating:box.floating,paragraphStyle:box.paragraphStyle,sourceStyle:'DOCX Text Box',sourceParagraph:paraIndex,section:sectionInfo||null,columnIndex:Number(ctx.columnIndex)||0},ctx.page);
      }
      ctx.page=Math.max(ctx.page,...segs.map(x=>x.page),ctx.page);
      if(paragraphSectionPageBreak(child)){ctx.page++;ctx.detectedBreaks++}
    }else if(lname(child)==='tbl'){
      tables++;paraIndex++;ctx.currentWordPageBlock=pageBlock(paraIndex);if(ctx.currentWordPageBlock?.startPage)ctx.page=Number(ctx.currentWordPageBlock.startPage)||ctx.page;
      const sectionInfo=sectionPlan.get(paraIndex)||null;
      if(sectionInfo?.id!==ctx.currentSectionId){ctx.currentSectionId=sectionInfo?.id||'';ctx.columnIndex=0}
      ctx.sectionInfo=sectionInfo;
      push({...tableBlock(child,ctx,ctx.page,paraIndex),section:sectionInfo||null,columnIndex:Number(ctx.columnIndex)||0},ctx.page);
    }
    const seenBreaks=ctx.detectedBreaks-beforeBreaks;if(expectedBreaks>seenBreaks){const miss=expectedBreaks-seenBreaks;ctx.page+=miss;ctx.detectedBreaks+=miss;ctx.reconciledBreaks+=miss}
  }
  // HF6 diagnostic rule: do not synthesize legacy composite-equation fallbacks.
  // If a native group/equation cannot be represented, the failure must remain visible.
  const compositeFallbackAudit={fallbackEquationCount:0,fallbackCompositeCount:0,attachedCompositeCount:0};
  const compact=[];
  for(let i=0;i<ordered.length;i++){
    const b=ordered[i];
    if(b.type==='list_item'){
      // HF12: one Word w:p is the natural pagination fragment. A list remains
      // semantically a list, but each Word list paragraph becomes its own
      // canonical list item instead of being merged into a large unbreakable block.
      // The actual Word-rendered ordinal wins when HF12 page-map metadata exists.
      const value=Number.isFinite(Number(b.listOrdinal))?Number(b.listOrdinal):undefined;
      compact.push({type:'list',id:b.id||'',listType:b.listType,numId:b.numId,numFmt:b.numFmt,listText:b.listText,listString:b.listString||'',visibleMarker:b.visibleMarker||b.listString||'',listMarkerFontFamily:b.listMarkerFontFamily||'',start:value||1,items:[{html:b.html,level:b.level,...(value!==undefined?{value}:{}),marker:b.visibleMarker||b.listString||'',markerFontFamily:b.listMarkerFontFamily||'',style:b.paragraphStyle||{},sourceParagraph:b.sourceParagraph,sourcePage:b.page}],paragraphStyle:b.paragraphStyle||{},hyphenate:b.hyphenate!==false,sourceStyle:b.sourceStyle||'',sourceParagraph:b.sourceParagraph,page:b.page,breakBefore:b.breakBefore,section:b.section||null,columnIndex:b.columnIndex,columnBreakBefore:b.columnBreakBefore||null,extensions:{docxListParagraph:true,docxListSeriesKey:`${b.numId||''}:${b.level||0}:${b.numFmt||''}`,docxListString:b.listString||'',docxVisibleListMarker:b.visibleMarker||b.listString||'',docxListMarkerFontFamily:b.listMarkerFontFamily||''}});continue;
    }
    if(b.type==='figure'&&ordered[i+1]?.type==='paragraph'){
      const txt=stripHtml(ordered[i+1].html||'');
      if(/^(Εικόνα|Γράφημα|Σχήμα)\s*\d*/i.test(txt)){b.caption=txt;i++}
    }
    compact.push(b);
  }
  if(wordPageMapActive){for(const b of compact){const mapped=pageBlock(Number(b.sourceParagraph)||0);if(mapped?.startPage){const start=Number(mapped.startPage)||1,end=Number(mapped.endPage)||start,current=Number(b.page)||start;if(current<start||current>end)b.page=start;b.sourcePageEnd=end}if(b.type==='list')for(const item of b.items||[]){const mi=pageBlock(Number(item.sourceParagraph)||0);if(mi?.startPage){const start=Number(mi.startPage)||1,end=Number(mi.endPage)||start,current=Number(item.sourcePage)||start;if(current<start||current>end)item.sourcePage=start;item.sourcePageEnd=end}}}}
  const pages=new Map();for(const b of compact){if(!pages.has(b.page))pages.set(b.page,[]);pages.get(b.page).push(b)}
  const imageInfo=await finalizeImages(pages,zip);
  const sectionColumns=[...new Map([...sectionPlan.values()].filter(info=>Number(info?.columnCount)>1).map(info=>[info.id,info])).values()];
  const sectionColumnBlocks=compact.filter(block=>Number(block?.section?.columnCount)>1).length;
  const nativeFloatingTables=compact.filter(block=>block?.type==='table'&&block.floating).length,aroundTablesConvertedToFlow=compact.filter(block=>block?.type==='table'&&block.sourceAroundConvertedToFlow).length;
  const pageCount=wordPageMapActive?Math.max(1,Number(wordPageMap.pageCount)||1):Math.max(...pages.keys(),1),txbxRaw=q(doc,NS.w,'txbxContent').length,drawings=q(doc,NS.w,'drawing').length+q(doc,NS.w,'pict').length;
  const headingPalette={};for(const h of headings)if(!headingPalette['h'+h.level])headingPalette['h'+h.level]=h.headingStyle||{};
  const imageGeometryAudit={occurrences:0,geometryConflicts:0,cropActive:0,missingDisplayExtent:0,sourceCounts:{}};
  for(const block of compact){
    const images=block?.type==='figure'?[block]:block?.type==='table'?(block.rows||[]).flatMap(row=>(row.cells||[]).flatMap(cell=>cell.images||[])):[];
    for(const image of images){const g=image.geometryTruth||{};imageGeometryAudit.occurrences++;if(g.geometryConflict)imageGeometryAudit.geometryConflicts++;if(g.cropActive)imageGeometryAudit.cropActive++;if(!(Number(image.width)>0&&Number(image.height)>0))imageGeometryAudit.missingDisplayExtent++;const src=String(g.geometrySource||'missing');imageGeometryAudit.sourceCounts[src]=(imageGeometryAudit.sourceCounts[src]||0)+1;}
  }
  return{fileName:file.name,pageCount,pages,orderedBlocks:compact,wordPageMap:{available:wordPageMapActive,status:wordPageMap?.status||'missing',source:wordPageMap?.source||'',pageCount:wordPageMapActive?pageCount:0,mappedBlocks:Number(wordPageMap?.mappedBlocks)||0,missingMarkers:(wordPageMap?.missingMarkers||[]).length,listValuesMapped:Number(wordPageMap?.listValuesMapped)||0,pageQueries:Number(wordPageMap?.pageQueries)||0},headings,headingPalette,documentLayout:layout,documentSettings:settings,customProperties:customProps,compositeManifest,vectorSurrogateManifest,vectorSurrogates:Object.keys(vectorSurrogateManifest.map||{}).length,groupSurrogateManifest,groupFidelitySummary:groupSurrogateManifest.summary||{},compositeTriage:{status:String(groupSurrogateManifest.source||''),groupCount:Number(groupSurrogateManifest.summary?.groupCount)||0,referencesCaptured:Number(groupSurrogateManifest.summary?.referencesCaptured)||0,referencesFailed:Number(groupSurrogateManifest.summary?.referencesFailed)||0,geometryMismatch:Number(groupSurrogateManifest.summary?.geometryMismatch)||0,contentMismatch:Number(groupSurrogateManifest.summary?.contentMismatch)||0,wordReferenceSurrogates:Number(groupSurrogateManifest.summary?.rendered)||0,sourceCropRequired:Number(groupSurrogateManifest.summary?.sourceCropRequired)||0,browserValidationRequired:Number(groupSurrogateManifest.summary?.browserValidationRequired)||0,directBitmapReferences:Number(groupSurrogateManifest.summary?.directBitmapReferences)||0,enhMetafileReferences:Number(groupSurrogateManifest.summary?.enhMetafileReferences)||0,wordPdfReferences:Number(groupSurrogateManifest.summary?.wordPdfReferences)||0,powerPointFallbackReferences:Number(groupSurrogateManifest.summary?.powerPointFallbackReferences)||0,failedIds:Object.values(groupSurrogateManifest.groups||{}).filter(x=>String(x.triageStage||'')&&String(x.triageStage||'')!=='word-reference-ok').map(x=>String(x.triageId||''))},wordRenderedCompositeSurrogates:ctx.wordRenderedCompositeSurrogates||0,textBoxNestedImages:ctx.textBoxNestedImages||0,tableInlineMediaFlowCells:ctx.tableInlineMediaFlowCells||0,tableCompositeCells:ctx.tableCompositeCells||0,tableOleOnlyCells:ctx.tableOleOnlyCells||0,tableMultiOleCells:ctx.tableMultiOleCells||0,tableGroupOnlyCells:ctx.tableGroupOnlyCells||0,tableOlePlusGroupCells:ctx.tableOlePlusGroupCells||0,tableCustomGeometryCells:ctx.tableCustomGeometryCells||0,tableTrustedAnchorsApplied:ctx.tableTrustedAnchorsApplied||0,tableParagraphAnchorsApplied:ctx.tableParagraphAnchorsApplied||0,tableAnchorsDeferred:ctx.tableAnchorsDeferred||0,compositeCount:compositeMap.size,compositeEquationOverlays:Number(compositeManifest.equationOverlayCount)||0,compositeEquationFallbacks:compositeFallbackAudit.fallbackEquationCount,compositeFallbackComposites:compositeFallbackAudit.fallbackCompositeCount,compositeBackgroundsAttached:compositeFallbackAudit.attachedCompositeCount,nativeVectorComposites:ctx.nativeVectorComposites||0,nativeVectorShapes:ctx.nativeVectorShapes||0,nativeVectorPictures:ctx.nativeVectorPictures||0,nativeMixedGroups:ctx.nativeMixedGroups||0,nativeVectorTextOverlays:ctx.nativeVectorTextOverlays||0,nativeVectorEquationOverlays:ctx.nativeVectorEquationOverlays||0,nativeVectorUnsupportedPresets:[...(ctx.nativeVectorUnsupportedPresets||new Set())].sort(),sectionColumns,sectionColumnBlocks,nativeFloatingTables,aroundTablesConvertedToFlow,paras,lists,tables,mathCount:ctx.mathCount,inlineMath:ctx.inlineMath,displayMath:ctx.displayMath,importedMathObjects:ctx.importedMathObjects,mathDuplicatesSkipped:Math.max(0,ctx.mathCount-ctx.importedMathObjects),usedImages:imageInfo.usedImages,imageBlobs:imageInfo.imageBlobs,nativeGroupEmbeddedImages:imageInfo.nativeGroupEmbeddedImages||0,nativeGroupEmbeddedImagesMissing:imageInfo.nativeGroupEmbeddedImagesMissing||0,rawImageRefs:ctx.rawImageRefs,duplicateImagesRemoved:imageInfo.duplicateImagesRemoved,imageGeometryAudit,alternateFallbacksSkipped:ctx.alternateFallbacksSkipped,fallbackImagesSkipped:ctx.fallbackImagesSkipped,textBoxes:txbxRaw,textBoxesUnique,textBoxCaptions,textBoxesImported,textBoxLabelsRetained,textBoxTablesRaw,textBoxTablesImported,drawings,detectedBreaks:ctx.detectedBreaks,reconciledBreaks:ctx.reconciledBreaks,skippedStaticToc:ctx.skippedStaticToc,unsupportedMath:[...unsupportedMath].sort(),zip};
}

function deepClone(v){return JSON.parse(JSON.stringify(v));}
function stripHtml(v=''){const d=document.createElement('div');d.innerHTML=String(v||'');return(d.textContent||'').replace(/\s+/g,' ').trim()}
function safeId(s){return String(s||'').trim().normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[^A-Za-z0-9_-]+/g,'_').replace(/^_+|_+$/g,'')}
function cssAlign(value=''){const v=String(value||'').toLowerCase();if(v==='both'||v==='distribute'||v==='thaidistribute')return'justify';if(['left','right','center','justify','start','end'].includes(v))return v;return''}
function canonicalTextStyle(style={},hyphenate=true){
  const out={align:cssAlign(style.align)||'left',hyphenate:hyphenate!==false};
  const copy=(from,to=from)=>{const v=style[from];if(v!==undefined&&v!==null&&v!=='')out[to]=typeof v==='number'?v:(Number.isFinite(Number(v))?Number(v):v)};
  for(const k of['fontFamily','fontSizePx','lineHeight','lineHeightPx','marginTopPx','marginBottomPx','marginLeftPx','marginRightPx','textIndentPx','backgroundColor','keepWithNext','keepTogether','pageBreakBefore','contextualSpacing'])copy(k);
  if(style.textColor)out.color=style.textColor;
  return out;
}
function canonicalListTextStyle(style={},hyphenate=true){
  const out=canonicalTextStyle(style,hyphenate);
  // Το Word κωδικοποιεί την εσοχή της αρίθμησης και μέσα στο paragraph
  // style. Στο canonical list η ίδια πληροφορία ανήκει αποκλειστικά στο
  // item.level και στον renderer. Διαφορετικά η εσοχή εφαρμόζεται 2–3 φορές.
  delete out.marginLeftPx;
  delete out.textIndentPx;
  return out;
}
function paragraphStyleCss(style={},hyphenate=true){const s=canonicalTextStyle(style,hyphenate),css=[];const add=(k,v)=>{if(v!==undefined&&v!==null&&v!=='')css.push(k+':'+v)};add('text-align',s.align);if(s.align==='justify')add('text-justify','inter-word');add('hyphens',s.hyphenate?'auto':'manual');add('-webkit-hyphens',s.hyphenate?'auto':'manual');add('overflow-wrap','break-word');add('word-break','normal');add('color',s.color||'#000000');if(s.fontFamily)add('font-family',JSON.stringify(s.fontFamily));if(s.fontSizePx)add('font-size',s.fontSizePx+'px');if(s.lineHeight)add('line-height',s.lineHeight);else if(s.lineHeightPx)add('line-height',s.lineHeightPx+'px');for(const [key,cssKey]of[['marginTopPx','margin-top'],['marginBottomPx','margin-bottom'],['marginLeftPx','margin-left'],['marginRightPx','margin-right'],['textIndentPx','text-indent']])if(s[key]!==undefined)add(cssKey,s[key]+'px');if(s.backgroundColor)add('background-color',s.backgroundColor);return css.join(';')}
function injectRootStyle(html,css){const t=String(html||'').trim();if(/^<(ul|ol|table)\b/i.test(t))return t.replace(/^<([a-z]+)([^>]*)>/i,(m,tag,rest)=>'<'+tag+rest+' style="'+esc(css)+'">');return '<span class="docx-paragraph-format" style="display:block;'+esc(css)+'">'+html+'</span>'}
function cleanupImportedHtml(html){const t=document.createElement('template');t.innerHTML=String(html||'');for(const el of t.content.querySelectorAll('.math-display')){el.style.overflow='visible';el.style.maxWidth='100%';el.style.scrollbarWidth='none'}const mathNodes=[...t.content.querySelectorAll('.math-inline,.math-display')];for(const el of mathNodes){let prev=el.previousSibling;while(prev&&prev.nodeType===3&&!prev.textContent.trim())prev=prev.previousSibling;if(prev&&prev.nodeType===1&&prev.matches?.('.math-inline,.math-display')){const a=(prev.textContent||'').replace(/\s+/g,'').trim(),b=(el.textContent||'').replace(/\s+/g,'').trim();if(a&&a===b&&prev.className===el.className)el.remove()}}return t.innerHTML}
function formattedParagraphHtml(block){return injectRootStyle(cleanupImportedHtml(block.html||''),paragraphStyleCss(block.paragraphStyle||{},block.hyphenate!==false))}
function flattenEntries(result){const out=[];if(!result)return out;for(let p=1;p<=result.pageCount;p++){const arr=result.pages.get(p)||[];for(let i=0;i<arr.length;i++){const block=arr[i];out.push({key:p+':'+i,page:p,blockIndex:i,block,type:block.type||'block',level:Number(block.level||0),heading:block.type==='part_title'||block.type==='section_heading',label:blockText(block)})}}return out}
function blockText(block){if(!block)return'';if(block.type==='part_title'||block.type==='section_heading')return block.title||'';if(block.type==='figure')return block.caption||('Σχήμα '+(block.srcPath?.split('/').pop()||''));if(block.type==='textbox')return block.text||stripHtml(block.html||'');if(block.type==='list')return block.items?.map(x=>stripHtml(x.html||'')).join(' · ')||'';if(block.type==='table')return block.rows?.map(r=>r.cells.map(c=>stripHtml(c.html||'')).join(' | ')).join(' · ')||'';return stripHtml(block.html||'')}
function entryLabel(entry){const b=entry.block,kind=entry.heading?'H'+(b.level||1):b.type==='figure'?'Σχήμα':b.type==='table'?'Πίνακας':b.type==='list'?'Λίστα':b.type==='textbox'?'Πλαίσιο':'¶';let text=blockText(b)||'(κενό)';if(text.length>105)text=text.slice(0,102)+'…';return'σ.'+entry.page+' · '+kind+' · '+text}
function entriesInRange(result,startKey,endKey){const all=flattenEntries(result);if(!all.length)return[];let a=all.findIndex(x=>x.key===startKey),b=all.findIndex(x=>x.key===endKey);if(a<0)a=0;if(b<0)b=a;if(a>b)[a,b]=[b,a];return all.slice(a,b+1)}
function headingsForEntries(result,entries,depth=3){return(result.headings||[]).filter(h=>h.level<=depth&&entries.some(x=>x.page===h.page&&(x.block.id===h.id||x.block.sourceParagraph===h.sourceParagraph)))}
function headingTitle(title,style={},preserve=true){if(!preserve)return esc(title);const css=[];if(style.backgroundColor)css.push('display:block','background:'+style.backgroundColor,'padding:4px 8px','border-radius:2px');css.push('color:#000000');if(style.fontFamily)css.push('font-family:'+JSON.stringify(style.fontFamily));if(style.fontSizePx)css.push('font-size:'+style.fontSizePx+'px');return css.length?'<span style="'+esc(css.join(';'))+'">'+esc(title)+'</span>':esc(title)}
function sourceRef(ctx,block){return{kind:ctx.sourceKind||'docx',sourceFile:ctx.fileName,sourcePage:Number(block.page||ctx.page)||1,sourcePageEnd:Number(block.sourcePageEnd||block.page||ctx.page)||1,sourceParagraph:block.sourceParagraph||null}}
function rich(html){return BookModelV4.htmlToRichText(cleanupImportedHtml(html||''))}
function docxPosition(block={}){
  const contract=deepClone(block.positionContract||{}),horizontal=contract.horizontal||{},vertical=contract.vertical||{};
  const xValue=block.x??contract.xPx??horizontal.offsetPx,yValue=block.y??contract.yPx??vertical.offsetPx,widthValue=block.width??contract.widthPx,heightValue=block.height??contract.heightPx;
  const x=finiteNumber(xValue)?Number(xValue):NaN,y=finiteNumber(yValue)?Number(yValue):NaN,width=finiteNumber(widthValue)?Number(widthValue):NaN,height=finiteNumber(heightValue)?Number(heightValue):NaN;
  const hasAlignment=!!String(horizontal.align||vertical.align||'').trim();
  if(!Number.isFinite(x)&&!Number.isFinite(y)&&!hasAlignment)return null;
  const sourceParagraph=Number(block.sourceParagraph)||null;
  const mode=String(contract.mode||'')||((['paragraph','character','line','text'].includes(String(block.xAnchor||'').toLowerCase())||['paragraph','character','line','text'].includes(String(block.yAnchor||'').toLowerCase()))?'paragraph-anchored':'page-absolute');
  const anchor={...(contract.anchor||{}),kind:mode==='paragraph-anchored'?'paragraph':'page',sourceParagraph:itemNumber(contract.anchor?.sourceParagraph,sourceParagraph),itemId:String(contract.anchor?.itemId||'')};
  return{version:2,mode,renderMode:String(contract.renderMode||contract.behavior||''),...(Number.isFinite(x)?{xPx:x}:{}),...(Number.isFinite(y)?{yPx:y}:{}),...(Number.isFinite(width)&&width>0?{widthPx:width}:{}),...(Number.isFinite(height)&&height>0?{heightPx:height}:{}),xAnchor:String(block.xAnchor||horizontal.relativeFrom||''),yAnchor:String(block.yAnchor||vertical.relativeFrom||''),horizontal:{relativeFrom:String(horizontal.relativeFrom||block.xAnchor||''),align:String(horizontal.align||''),offsetPx:finiteNumber(horizontal.offsetPx)?Number(horizontal.offsetPx):(Number.isFinite(x)?x:null)},vertical:{relativeFrom:String(vertical.relativeFrom||block.yAnchor||''),align:String(vertical.align||''),offsetPx:finiteNumber(vertical.offsetPx)?Number(vertical.offsetPx):(Number.isFinite(y)?y:null)},anchor,wrap:deepClone(contract.wrap||{type:'wrapNone',side:'bothSides',distTopPx:0,distRightPx:0,distBottomPx:0,distLeftPx:0,contourApplied:false}),stacking:deepClone(contract.stacking||{relativeHeight:0,behindDoc:false,allowOverlap:true,layoutInCell:true,locked:false}),...(contract.frame?{frame:deepClone(contract.frame)}:{}),...(contract.hintOverride?{hintOverride:true}:{})};
}
function itemNumber(value,fallback=null){const n=Number(value);return Number.isFinite(n)&&n>0?n:fallback}
function soleDisplayMath(html){const t=document.createElement('template');t.innerHTML=String(html||'');const all=[...t.content.querySelectorAll('math')];if(all.length!==1)return null;const math=all[0],display=math.getAttribute('display')==='block'||math.closest('.math-display');const clone=t.content.cloneNode(true);clone.querySelectorAll('math').forEach(x=>x.remove());if(!display||clone.textContent.trim()||clone.querySelector('img,table,ul,ol'))return null;return new XMLSerializer().serializeToString(math)}
function mathmlFromOmmlXml(value=''){
  try{
    const doc=xml(String(value||'')),root=doc.documentElement;if(!root||!['oMath','oMathPara'].includes(lname(root)))return'';
    const wrapped=ommlToMath(root,true),t=document.createElement('template');t.innerHTML=wrapped;const math=t.content.querySelector('math');return math?serializer.serializeToString(math):'';
  }catch(_error){return''}
}
function normalizedCompositeFigure(composite){
  if(!composite||typeof composite!=='object')return null;
  const overlays=[];
  for(const [index,raw] of (composite.overlays||[]).entries()){
    const type=String(raw?.type||'');const g=raw.geometry||{},clamp=(v,min=0,max=1)=>Math.max(min,Math.min(max,Number(v)||0)),geometry={x:clamp(g.x),y:clamp(g.y),width:clamp(g.width,.001,1),height:clamp(g.height,.001,1)};
    if(type==='equation'){
      const mathml=String(raw.mathml||'').trim()||mathmlFromOmmlXml(raw.ommlXml||'');if(!mathml)continue;
      const fontSizePt=Number(raw.fontSizePt)||0,fontSizePx=Number(raw.fontSizePx)|| (fontSizePt>0?fontSizePt*4/3:undefined);
      overlays.push({id:String(raw.id||('eq-'+(index+1))),type:'equation',source:String(raw.source||''),mathml,plainText:String(raw.plainText||''),geometry,mask:{mode:String(raw.mask?.mode||'local-equation-mask'),fallbackColor:validHex(raw.mask?.fallbackColor)||'#FFFFFF'},...(fontSizePx?{fontSizePx}:{}),visible:raw.visible!==false});
    }else if(type==='text'){
      const body=raw.body?.format==='rich-text-v1'?deepClone(raw.body):BookModelV4.htmlToRichText(String(raw.html||esc(raw.plainText||'')));
      overlays.push({id:String(raw.id||('text-'+(index+1))),type:'text',body,plainText:String(raw.plainText||BookModelV4.richTextPlain(body)||''),geometry,style:deepClone(raw.style||{}),visible:raw.visible!==false});
    }
  }
  if(!overlays.length&&!composite.nativeVector)return null;
  const bg=composite.baseGeometry||{},clampBase=(v,min=0,max=1)=>Math.max(min,Math.min(max,Number(v)||0)),baseGeometry={x:clampBase(bg.x),y:clampBase(bg.y),width:clampBase(bg.width,.005,2),height:clampBase(bg.height,.005,2),lockAspect:bg.lockAspect!==false};return{version:Number(composite.version)||4,id:String(composite.id||''),nativeVector:!!composite.nativeVector,backgroundClean:composite.backgroundClean===true,immutableBase:composite.immutableBase!==false,basePixelPolicy:String(composite.basePixelPolicy||'untouched-word-render'),status:String(composite.status||''),effectExtentPx:deepClone(composite.effectExtentPx||{}),unsupportedPresets:[...(composite.unsupportedPresets||[])],shapeCount:Number(composite.shapeCount)||0,baseGeometry,overlays};
}
function convertBlock(block,ctx){
  const id=ctx.id(block),sr=sourceRef(ctx,block),hardBreak=block.breakBefore==='explicit'||block.paragraphStyle?.pageBreakBefore;
  const extBase={...(ctx.sourceKind==='web'?{webCanonicalImport:'v1'}:{docxCanonicalImport:REQUIRED_WORD_PROFILE}),...(hardBreak?{paginationHardBreakBefore:true}:{})};
  if(block.type==='part_title'||block.type==='section_heading')return BookModelV4.normalizeItem({id,type:block.type,title:headingTitle(block.title||'',block.headingStyle||{},ctx.preserveHeadingColors),level:Number(block.level)||2,style:canonicalTextStyle(block.headingStyle||{},true),nav:{show:true,label:block.title||''},sourceRef:sr,provenance:{sourceHeadingLevel:Number(block.level)||2,sourceHeadingStyle:block.headingStyle||{},sourceStyle:block.sourceStyle||''},extensions:extBase});
  if(block.type==='clear')return BookModelV4.normalizeItem({id,type:'clear',sourceRef:sr,provenance:{sourceStyle:block.sourceStyle||'Source page section'},extensions:{...extBase,sourcePageSectionBreak:true}});
  if(block.type==='figure'){
    const name=ctx.imageName(block.srcPath);if(!name)return null;
    const position=docxPosition(block),floating=!!block.floating,rawPlacement=String(block.placement||'').toLowerCase();
    const placement=!floating?'block':(rawPlacement.includes('left')?'float-left':'float-right');
    const wrapType=String(position?.wrap?.type||'wrapNone'),wraps=['wrapSquare','wrapTight','wrapThrough'].includes(wrapType);
    const compositeFigure=normalizedCompositeFigure(block.composite);
    return BookModelV4.normalizeItem({id,type:'figure',src:'images/'+name,alt:block.positionHint?.alt||stripHtml(block.caption||'')||block.textLabels?.map(x=>x.text||stripHtml(x.html||'')).join(' ')||'Εικόνα από '+ctx.fileName,title:'',caption:block.caption||'',hideCaption:!block.caption,layout:{placement,widthPx:block.width||undefined,heightPx:block.height||undefined,aspectRatio:'natural',wrap:wraps,floatInteraction:wraps?'wrap':'clear'},sourceRef:sr,provenance:{sourceImagePath:block.srcPath,sourceKind:block.kind||'',sourceParagraph:block.sourceParagraph||null,sourceRole:hintRole(block.positionHint)||'figure'},extensions:{...extBase,imageGeometryTruth:deepClone(block.geometryTruth||{}),docxTextLabels:block.textLabels||[],...(position?{positioningContract:position}:{}),docxPositionHint:block.positionHint||null,...(compositeFigure?{compositeFigure:deepClone(compositeFigure),compositeBackground:{version:compositeFigure.version,id:compositeFigure.id,nativeVector:!!compositeFigure.nativeVector,backgroundClean:compositeFigure.backgroundClean===true,immutableBase:compositeFigure.immutableBase,basePixelPolicy:compositeFigure.basePixelPolicy,status:compositeFigure.status,overlayCount:compositeFigure.overlays.length,effectExtentPx:deepClone(compositeFigure.effectExtentPx||{}),unsupportedPresets:[...(compositeFigure.unsupportedPresets||[])]}}:{})}});
  }
  if(block.type==='textbox'){
    const narrow=block.floating||Number(block.width||0)>0&&Number(block.width)<500,type=narrow?'side_note':'note',position=docxPosition(block);
    return BookModelV4.normalizeItem({id,type,label:block.label||'',title:type==='side_note'?(block.label||''):'',body:rich(block.html||esc(block.text||'')),style:canonicalTextStyle(block.paragraphStyle||{},true),layout:type==='side_note'?{placement:block.placement==='float-left'?'left':'right',widthPx:block.width||340,floatInteraction:'avoid'}:undefined,sourceRef:sr,provenance:{sourceStyle:'DOCX Text Box',sourceParagraph:block.sourceParagraph||null},extensions:{...extBase,docxTextBox:true,...(position?{positioningContract:position}:{})}});
  }
  if(block.type==='list'){
    return BookModelV4.normalizeItem({id,type:'list',ordered:block.listType==='ol',start:Number(block.start)||1,items:(block.items||[]).map(e=>({level:Number(e.level)||0,value:Number.isFinite(Number(e.value))?Number(e.value):undefined,marker:String(e.marker??block.visibleMarker??block.listString??''),markerFontFamily:String(e.markerFontFamily??block.listMarkerFontFamily??''),body:rich(e.html||''),style:canonicalListTextStyle(e.style||{},block.hyphenate!==false),sourceParagraph:Number(e.sourceParagraph)||undefined,sourcePage:Number(e.sourcePage)||Number(block.page)||undefined})),style:{...canonicalListTextStyle(block.paragraphStyle||{},block.hyphenate!==false),listStylePosition:'outside',...(block.listType==='ol'?{listStyleType:cssListStyle(block.numFmt)}:{})},sourceRef:sr,provenance:{sourceStyle:block.sourceStyle||'list',sourceNumId:block.numId||'',sourceNumFmt:block.numFmt||'',sourceLvlText:block.listText||'',sourceVisibleMarker:block.visibleMarker||block.listString||''},extensions:{...extBase,...(block.extensions||{})}});
  }
  if(block.type==='table'){
    const makeMedia=(im,rowIndex,cellIndex)=>{
      const name=ctx.imageName(im.srcPath),composite=normalizedCompositeFigure(im.composite),equations=(composite?.overlays||[]).filter(overlay=>String(overlay?.mathml||'').trim()).map((overlay,index)=>({id:String(overlay.id||`eq-${index+1}`),source:String(overlay.source||''),mathml:String(overlay.mathml||''),plainText:String(overlay.plainText||''),visible:overlay.visible!==false,geometry:deepClone(overlay.geometry||{x:0,y:0,width:1,height:1}),style:{...(Number(overlay.fontSizePx)>0?{fontSizePx:Number(overlay.fontSizePx)}:{})},mask:deepClone(overlay.mask||{})}));
      if(!name&&!equations.length)return null;
      const pos=im.positionContract?docxPosition(im):null,free=!!im.floating||!!pos;
      const sourceX=Number(im.x??pos?.xPx??pos?.horizontal?.offsetPx)||0,sourceY=Number(im.y??pos?.yPx??pos?.vertical?.offsetPx)||0,wrapType=String(pos?.wrap?.type||'wrapNone'),hRelative=String(pos?.horizontal?.relativeFrom||im.xAnchor||'').toLowerCase(),vRelative=String(pos?.vertical?.relativeFrom||im.yAnchor||'').toLowerCase(),paragraphIndex=Number(im.cellParagraphIndex)||0;
      // HF19 coordinate truth: Word posOffset is only meaningful after its
      // declared relativeFrom origin is known.  In table cells the common
      // column+paragraph contract is resolved against the owning paragraph.
      // Page/margin/line/character coordinate systems are preserved as source
      // metadata but are NOT invented as <td>-local pixels.
      const paragraphCellAnchor=!!im.floating&&!!pos&&!pos.hintOverride&&pos?.stacking?.layoutInCell!==false&&wrapType==='wrapNone'&&paragraphIndex>0&&hRelative==='column'&&vRelative==='paragraph'&&Number.isFinite(sourceX)&&Number.isFinite(sourceY)&&Math.abs(sourceX)<2000&&Math.abs(sourceY)<2000;
      const unresolvedFloating=!!im.floating&&!!pos&&!paragraphCellAnchor;
      const tableOverlay={enabled:paragraphCellAnchor,coordinateSpace:paragraphCellAnchor?'cell-paragraph':'unresolved-word-anchor',paragraphIndex,sourceFloating:free,anchorRow:Number(rowIndex)||0,anchorCell:Number(cellIndex)||0,xPx:paragraphCellAnchor?sourceX:0,yPx:paragraphCellAnchor?sourceY:0,sourceXPx:sourceX,sourceYPx:sourceY,hRelativeFrom:hRelative,vRelativeFrom:vRelative,widthPx:Number(im.width)||undefined,heightPx:Number(im.height)||undefined,zIndex:Math.max(1,Math.round(Number(pos?.stacking?.relativeHeight||0)/1000000)+1),lockAspect:true,source:paragraphCellAnchor?'docx-cell-paragraph-anchor-resolved':'docx-word-anchor-preserved-unresolved'};
      if(paragraphCellAnchor){ctx.tableTrustedAnchorsApplied=(ctx.tableTrustedAnchorsApplied||0)+1;ctx.tableParagraphAnchorsApplied=(ctx.tableParagraphAnchorsApplied||0)+1}else if(unresolvedFloating)ctx.tableAnchorsDeferred=(ctx.tableAnchorsDeferred||0)+1;
      return{src:name?'images/'+name:'',alt:im.alt||'Σχήμα μέσα σε πίνακα',widthPx:Number(im.width)||undefined,heightPx:Number(im.height)||undefined,sourcePage:Number(im.sourcePage)||undefined,imageGeometryTruth:deepClone(im.geometryTruth||{}),sourceCompositeId:im.compositeId||'',backgroundClean:composite?.backgroundClean===true,immutableBase:composite?.immutableBase!==false,basePixelPolicy:String(composite?.basePixelPolicy||'untouched-word-render'),equations:composite?[]:equations,tableOverlay,...(pos?{positioningContract:deepClone(pos)}:{}),...(composite?{compositeFigure:deepClone(composite)}:{})};
    };
    const rows=(block.rows||[]).map((r,rowIndex)=>({cells:(r.cells||[]).map((c,cellIndex)=>{const rawImages=c.images||[],media=[],rawToCanonical=[];rawImages.forEach((im,rawIndex)=>{const converted=makeMedia(im,rowIndex,cellIndex);if(converted){rawToCanonical[rawIndex]=media.length;media.push(converted)}});const mapFlowPart=part=>{if(part?.type==='html')return{type:'html',body:rich(part.html||part.body||'')};if(part?.type==='media'){const mapped=rawToCanonical[Number(part.mediaIndex)];if(Number.isFinite(mapped))return{type:'media',mediaIndex:mapped}}return null};
      const inlineMediaFlow=(c.inlineMediaFlow||[]).map(token=>{
        if(token?.type==='paragraph'){const tokens=(token.tokens||[]).map(mapFlowPart).filter(Boolean);if(!tokens.length)return null;return{type:'paragraph',paragraphIndex:Number(token.paragraphIndex)||undefined,sourcePage:Number(token.sourcePage)||Number(r.sourcePage)||undefined,style:canonicalTextStyle(token.style||{},true),tokens}}
        return mapFlowPart(token)
      }).filter(Boolean);
      const flowHasMedia=inlineMediaFlow.some(token=>token?.type==='media'||token?.type==='paragraph'&&(token.tokens||[]).some(part=>part?.type==='media'));
      return{colspan:Number(c.colspan)||1,rowspan:Number(c.rowspan)||1,body:rich(c.html||''),paragraphFragments:(c.paragraphs||[]).map(p=>({body:rich(p.html||''),sourcePage:Number(p.sourcePage)||Number(r.sourcePage)||undefined,paragraphIndex:Number(p.paragraphIndex)||undefined})),media,...(flowHasMedia?{inlineMediaFlow}:{}),style:{...canonicalTextStyle(c.paragraphStyle||{},true),...(c.style||{})}}}),allowBreakAcrossPages:r.allowBreakAcrossPages!==false,sourcePage:Number(r.sourcePage)||undefined,sourcePageEnd:Number(r.sourcePageEnd)||Number(r.sourcePage)||undefined,sourceRowSpansPages:r.sourceRowSpansPages===true,sourceRenderedPageBreaks:Number(r.renderedPageBreaks)||0,sourceExplicitPageBreaks:Number(r.explicitPageBreaks)||0}));
    const position=docxPosition(block),floating=!!position&&String(position.renderMode||'')==='flow-wrap',width=Number(block.width||block.tableStyle?.widthPx)||undefined;
    const placement=floating?(String(block.placement||'').includes('left')?'float-left':'float-right'):'wide';
    return BookModelV4.normalizeItem({id,type:'table',columns:Number(block.columns)||1,rows,layout:{placement,widthPx:width,wrap:floating,floatInteraction:floating?'wrap':'clear'},style:{columnWidthsPx:block.tableStyle?.columnWidthsPx||[],headerRows:Number(block.tableStyle?.headerRows)||0,keepTogether:false,marginBottomPx:floating?0:4,widthPx:width,layoutMode:floating?'floating-around':'flow'},sourceRef:sr,provenance:{sourceStyle:block.fromTextBox?'DOCX native table inside positioned text box':'table',sourceParagraph:block.sourceParagraph||null},extensions:{...extBase,tableOverlayModel:'cell-anchored-free-v1',sourcePageSpan:Math.max(1,Number(block.sourcePageSpan)||1),sourceRenderedPageBreaks:Number(block.tableRenderedBreaks)||0,sourceExplicitPageBreaks:Number(block.tableExplicitBreaks)||0,...(position?{positioningContract:position}:{}),...(block.fromTextBox?{docxTextBoxTable:true,docxNativeFloatingTable:true,sourceContainerHeightPx:Number(block.sourceContainerHeight)||undefined}:{})}});
  }
  if(block.type==='paragraph'){
    const html=formattedParagraphHtml(block),mathml=soleDisplayMath(html);
    if(mathml)return BookModelV4.normalizeItem({id,type:'equation',source:'',mathml,caption:'',style:{align:'center',...canonicalTextStyle(block.paragraphStyle||{},block.hyphenate!==false)},sourceRef:sr,provenance:{sourceFormat:'OMML→MathML',sourceStyle:block.sourceStyle||''},extensions:{...extBase,mathSourceFormat:'omml-mathml'}});
    return BookModelV4.normalizeItem({id,type:'paragraph',body:rich(html),style:canonicalTextStyle(block.paragraphStyle||{},block.hyphenate!==false),sourceRef:sr,provenance:{sourceStyle:block.sourceStyle||'',sourceParagraphStyle:block.paragraphStyle||{},sourceHyphenation:block.hyphenate!==false},extensions:{...extBase}});
  }
  return null;
}
function rawPositionedObjectAudit(entries=[]){
  const out={positioned:0,pageAbsolute:0,paragraphAnchored:0,inline:0,withExplicitOffsets:0,alignOnly:0,wrapTypes:{},paragraphAnchorSourceMissing:0,contourWrapParsedButNotApplied:0};
  for(const entry of entries||[]){
    const position=docxPosition(entry?.block||{});if(!position)continue;out.positioned++;
    const mode=String(position.mode||'page-absolute');if(mode==='paragraph-anchored')out.paragraphAnchored++;else if(mode==='inline')out.inline++;else out.pageAbsolute++;
    const hasOffset=finiteNumber(position.xPx)||finiteNumber(position.yPx)||finiteNumber(position.horizontal?.offsetPx)||finiteNumber(position.vertical?.offsetPx);
    if(hasOffset)out.withExplicitOffsets++;else out.alignOnly++;
    const wrap=String(position.wrap?.type||'unknown');out.wrapTypes[wrap]=(out.wrapTypes[wrap]||0)+1;
    if(mode==='paragraph-anchored'&&!Number(position.anchor?.sourceParagraph))out.paragraphAnchorSourceMissing++;
    if(Array.isArray(position.wrap?.polygon)&&position.wrap.polygon.length&&!position.wrap.contourApplied)out.contourWrapParsedButNotApplied++;
  }
  return out;
}
function canonicalPositionedObjectAudit(items=[]){
  const out={positioned:0,pageAbsolute:0,paragraphAnchored:0,exactParagraphAnchors:0,previousFlowAnchors:0,unresolvedParagraphAnchors:0,behindDoc:0,allowOverlapFalse:0,wrapTypes:{},contourWrapParsedButNotApplied:0};
  for(const item of items||[]){
    const contract=item?.extensions?.positioningContract;if(!contract)continue;out.positioned++;
    const mode=String(contract.mode||'page-absolute');if(mode==='paragraph-anchored')out.paragraphAnchored++;else out.pageAbsolute++;
    const resolution=String(item?.extensions?.positioningResolution||'');
    if(resolution==='exact-source-paragraph')out.exactParagraphAnchors++;
    else if(resolution==='previous-flow-item')out.previousFlowAnchors++;
    else if(mode==='paragraph-anchored'&&!String(contract.anchor?.itemId||''))out.unresolvedParagraphAnchors++;
    if(contract.stacking?.behindDoc)out.behindDoc++;
    if(contract.stacking?.allowOverlap===false)out.allowOverlapFalse++;
    const wrap=String(contract.wrap?.type||'unknown');out.wrapTypes[wrap]=(out.wrapTypes[wrap]||0)+1;
    if(Array.isArray(contract.wrap?.polygon)&&contract.wrap.polygon.length&&!contract.wrap.contourApplied)out.contourWrapParsedButNotApplied++;
  }
  return out;
}
function nestedCanonicalItems(items=[]){const out=[];for(const item of items||[]){out.push(item);if(item?.type==='columns')for(const region of item.regions||[])out.push(...nestedCanonicalItems(region.items||[]));}return out;}
function resolveParagraphPositionAnchors(items=[]){
  const flowTypes=new Set(['paragraph','part_title','section_heading','list','table','equation','note']);
  const all=nestedCanonicalItems(items),candidates=all.filter(item=>flowTypes.has(item?.type)&&item?.id&&Number(item?.sourceRef?.sourceParagraph)>0&&!item?.extensions?.positioningContract);
  for(const current of all){
    const contract=current?.extensions?.positioningContract;
    if(!contract||String(contract.mode||'')!=='paragraph-anchored')continue;
    contract.anchor=contract.anchor&&typeof contract.anchor==='object'?contract.anchor:{kind:'paragraph',sourceParagraph:null,itemId:''};
    if(String(contract.anchor.itemId||''))continue;
    const sourceParagraph=Number(contract.anchor.sourceParagraph||current?.sourceRef?.sourceParagraph||0);
    const exact=candidates.find(item=>Number(item.sourceRef?.sourceParagraph)===sourceParagraph);
    if(exact){contract.anchor.itemId=exact.id;current.extensions.positioningResolution='exact-source-paragraph';continue;}
    const previous=candidates.filter(item=>Number(item.sourceRef?.sourceParagraph)<=sourceParagraph).sort((a,b)=>Number(b.sourceRef?.sourceParagraph)-Number(a.sourceRef?.sourceParagraph))[0];
    if(previous){contract.anchor.itemId=previous.id;current.extensions.positioningResolution='previous-flow-item';}
    else current.extensions.positioningResolution='unresolved';
  }
  return items;
}
function detachedFigureCaption(block,ctx){
  const text=String(block?.caption||'').trim();
  if(!text)return null;
  const id=ctx.id({}),sr=sourceRef(ctx,block);
  return BookModelV4.normalizeItem({
    id,type:'paragraph',body:rich(esc(text)),
    style:{align:'center',hyphenate:true,fontFamily:'Georgia',fontSizePx:12,lineHeight:1.2,color:'#4F7895',fontStyle:'italic',marginTopPx:2,marginBottomPx:8,keepTogether:true},
    sourceRef:sr,provenance:{sourceStyle:'DOCX detached figure caption',sourceParagraph:block.sourceParagraph||null},
    extensions:{docxCanonicalImport:REQUIRED_WORD_PROFILE,docxDetachedFigureCaption:true,preferRichText:true}
  });
}
function shouldDetachFigureCaption(block){
  const caption=String(block?.caption||'').trim(),width=Number(block?.width||0);
  return !!caption&&caption.length>=48&&width>0&&width<260;
}
function standaloneCompositeEquationItems(){return[]} // HF6: overlays stay inside the composite container; no standalone fallback items.

function convertedEntryItems(entry,ctx){
  const block=entryBlock(entry),out=[];
  if(block.type==='figure'&&shouldDetachFigureCaption(block)){
    const item=convertBlock({...block,caption:''},ctx);if(item){out.push(item);}
    const caption=detachedFigureCaption(block,ctx);if(caption)out.push(caption);
  }else{
    const item=convertBlock(block,ctx);if(item){out.push(item);}
  }
  return out;
}
function sourceColumnContract(pageEntries=[]){
  for(const entry of pageEntries){const section=entry?.block?.section;if(Number(section?.columnCount)>1)return section;}
  return null;
}
function canonicalSourceColumns(pageEntries,ctx,section){
  const count=Math.max(2,Number(section?.columnCount)||2),regions=Array.from({length:count},(_,index)=>({role:`column-${index+1}`,title:'',items:[]})),positioned=[];
  for(const entry of pageEntries){
    const columnIndex=Math.max(0,Math.min(count-1,Number(entry?.block?.columnIndex)||0));
    for(const item of convertedEntryItems(entry,ctx)){
      if(item?.extensions?.positioningContract)positioned.push(item);
      else regions[columnIndex].items.push(item);
    }
  }
  for(const region of regions)applyContextualSpacing(region.items);
  const active=regions.filter(region=>region.items.length);
  if(active.length<2)return null;
  const first=pageEntries[0],sourceWidths=Array.isArray(section?.columnWidthsPx)?section.columnWidthsPx.map(Number).filter(Number.isFinite):[];
  const columns=BookModelV4.normalizeItem({
    id:ctx.id({}),type:'columns',title:'',regions:active.map((region,index)=>({role:region.role,title:'',items:region.items})),
    style:{columnGapPx:Number(section?.columnGapPx)||12},sourceRef:sourceRef(ctx,{page:first?.page||ctx.page,sourceParagraph:first?.block?.sourceParagraph||null}),
    provenance:{sourceStyle:'DOCX equal-width section columns',sourceSectionId:String(section?.id||''),sourceColumnCount:count,sourceColumnWidthsPx:sourceWidths,sourceBodyWidthPx:Number(section?.bodyWidthPx)||null},
    extensions:{docxCanonicalImport:REQUIRED_WORD_PROFILE,docxSourceColumns:true,docxEqualColumns:true,sourceSectionId:String(section?.id||''),sourceColumnCount:count}
  });
  return{columns,positioned};
}
function convertEntries(result,entries,ctx){
  const out=[];let index=0,previousPage=null;
  while(index<entries.length){
    const page=Number(entries[index]?.page)||1,pageEntries=[];
    while(index<entries.length&&(Number(entries[index]?.page)||1)===page)pageEntries.push(entries[index++]);
    if(previousPage!==null&&page!==previousPage){const br=convertBlock(sourceSectionBreakBlock(pageEntries[0]),ctx);if(br)out.push(br);}
    const section=sourceColumnContract(pageEntries),grouped=section?canonicalSourceColumns(pageEntries,ctx,section):null;
    if(grouped){out.push(grouped.columns,...grouped.positioned);}
    else for(const entry of pageEntries)out.push(...convertedEntryItems(entry,ctx));
    previousPage=page;
  }
  resolveParagraphPositionAnchors(out);
  return out;
}
function tocBody(headings){const nodes=[];if(!headings.length)return BookModelV4.createRichText([BookModelV4.createTextRun('Δεν βρέθηκαν επικεφαλίδες.')]);const min=Math.min(...headings.map(h=>h.level));for(const h of headings){const indent=' '.repeat(Math.max(0,h.level-min)*4);nodes.push({type:'link',href:'#'+h.id,children:[BookModelV4.createTextRun(indent+h.text,{bold:h.level===min})]},{type:'line_break'})}if(nodes.at(-1)?.type==='line_break')nodes.pop();return BookModelV4.createRichText(nodes)}
function applyContextualSpacing(items){for(let i=0;i<items.length-1;i++){const a=items[i],b=items[i+1],sa=a?.provenance?.sourceStyle,sb=b?.provenance?.sourceStyle;if(sa&&sa===sb&&a?.style?.contextualSpacing&&b?.style?.contextualSpacing)a.style.marginBottomPx=0}return items}
function entryBlock(entry){return{...entry.block,page:entry.page}}
function sourceSectionBreakBlock(entry){return{type:'clear',page:entry.page,breakBefore:'explicit',sourceStyle:'Source page section'}}
function typographyScaleFactor(options={}){const value=Number(options.typographyScale||1);return Math.max(.6,Math.min(1.8,Number.isFinite(value)?value:1))}
function scaleInlineFontCss(value,factor){if(typeof value!=='string'||factor===1||!value.includes('font-size'))return value;return value.replace(/font-size\s*:\s*([0-9.]+)(px|pt)/gi,(_,n,u)=>'font-size:'+(Math.round(Number(n)*factor*1000)/1000)+u)}
function scaleTypographyObject(value,factor,key=''){if(factor===1||value==null)return value;if(Array.isArray(value)){for(let i=0;i<value.length;i++)value[i]=scaleTypographyObject(value[i],factor,key);return value}if(typeof value==='string')return scaleInlineFontCss(value,factor);if(typeof value!=='object')return value;for(const[k,v]of Object.entries(value)){if(k==='sourceRef'||k==='provenance')continue;if(typeof v==='number'&&(k==='fontSizePx'||k==='lineHeightPx'||/FontSize$/.test(k)))value[k]=Math.round(v*factor*1000)/1000;else value[k]=scaleTypographyObject(v,factor,k)}return value}
function combineRichBodies(fragments=[]){
  const nodes=[];
  for(const fragment of fragments||[]){
    const body=fragment?.body,part=Array.isArray(body?.nodes)?body.nodes:[];
    if(!part.length)continue;
    if(nodes.length&&nodes.at(-1)?.type!=='line_break')nodes.push({type:'line_break'});
    nodes.push(...deepClone(part));
  }
  return BookModelV4.createRichText(nodes);
}
function sourcePageRowFragments(row,fallbackPage=1){
  const start=Number(row?.sourcePage)||Number(fallbackPage)||1,end=Math.max(start,Number(row?.sourcePageEnd)||start);
  if(end<=start)return[deepClone(row)];
  const hasParagraphMap=(row?.cells||[]).some(cell=>(cell?.paragraphFragments||[]).some(fragment=>Number(fragment?.sourcePage)>0));
  if(!hasParagraphMap)return[deepClone(row)];
  const out=[];
  for(let page=start;page<=end;page++){
    const fragment=deepClone(row);let hasContent=false;
    fragment.sourcePage=page;fragment.sourcePageEnd=page;fragment.sourceRowSpansPages=false;fragment.sourceRowFragment=true;fragment.sourceRowFragmentPage=page;fragment.sourceRowOriginalStartPage=start;fragment.sourceRowOriginalEndPage=end;
    fragment.cells=(row.cells||[]).map(cell=>{
      const c=deepClone(cell),paragraphs=(cell?.paragraphFragments||[]).filter(p=>Number(p?.sourcePage||start)===page),media=(cell?.media||[]).filter(m=>Number(m?.sourcePage||start)===page);
      c.paragraphFragments=paragraphs.map(deepClone);c.body=combineRichBodies(paragraphs);c.media=media.map(deepClone);
      if((c.body?.nodes||[]).length||c.media.length)hasContent=true;
      return c;
    });
    if(hasContent)out.push(fragment);
  }
  return out.length?out:[deepClone(row)];
}
function sourcePageTableFragments(item){
  if(item?.type!=='table'||item?.layout?.wrap===true||String(item?.style?.layoutMode||'')==='floating-around')return[item];
  const originalRows=Array.isArray(item.rows)?item.rows:[],fallbackPage=Number(item?.sourceRef?.sourcePage)||1,rows=originalRows.flatMap(row=>sourcePageRowFragments(row,fallbackPage)),groups=[];let current=null;
  for(const row of rows){const page=Number(row?.sourcePage)||fallbackPage;if(!current||current.page!==page){current={page,rows:[]};groups.push(current)}current.rows.push(row)}
  if(groups.length<=1){const only=deepClone(item);only.rows=rows;return[only]}
  const headerCount=Math.max(0,Math.min(originalRows.length,Number(item?.style?.headerRows)||0)),headers=originalRows.slice(0,headerCount);
  return groups.map((group,index)=>{const out=deepClone(item),repeat=index>0&&headerCount>0;out.id=index===0?item.id:`${item.id}-sp${group.page}`;out.rows=[...(repeat?headers.map(r=>({...deepClone(r),sourcePage:group.page,sourcePageEnd:group.page,sourceRepeatedHeader:true})):[]),...group.rows.map(deepClone)];out.sourceRef={...(out.sourceRef||{}),sourcePage:group.page,sourcePageEnd:group.page};out.extensions={...(out.extensions||{}),sourceTableFragment:true,sourceTableFragmentIndex:index+1,sourceTableFragmentCount:groups.length,sourceTableContinuationGroup:item.id,sourceSpanningRowFragments:group.rows.filter(r=>r?.sourceRowFragment).length,sourceRowSpansPages:originalRows.filter(r=>Number(r?.sourcePageEnd||r?.sourcePage)!==Number(r?.sourcePage)).map(r=>({sourcePage:Number(r?.sourcePage)||group.page,sourcePageEnd:Number(r?.sourcePageEnd)||Number(r?.sourcePage)||group.page}))};return out});
}
function sourcePageFragments(items){return(items||[]).flatMap(item=>sourcePageTableFragments(item))}

function makeBook(result,entries,id,title,options={}){
  if(!entries?.length)throw Error('Δεν έχει επιλεγεί περιεχόμενο DOCX.');
  const preserve=options.preserveHeadingColors!==false,depth=Number(options.tocDepth||3),generateToc=options.generateToc!==false,usedImages=new Map(),usedIds=new Set();
  const imageName=path=>{if(!path)return'';if(!usedImages.has(path)){let base=path.split('/').pop().replace(/[^A-Za-z0-9._-]+/g,'_')||'image.bin',name=base,n=2;const names=new Set(usedImages.values());while(names.has(name))name=(n++)+'_'+base;usedImages.set(path,name)}return usedImages.get(path)};
  const unique=base=>BookModelV4.uniqueId(base,usedIds);let itemCounter=0;const pages=[];
  if(generateToc){const hs=headingsForEntries(result,entries,depth);pages.push(BookModelV4.createPage({id:unique('contents-flow'),header:{inherit:false,left:'',center:'',right:title},footer:{inherit:false,left:'© antarxo 2026',center:'',right:''},pageNumbering:{inherit:false,enabled:true,offset:0,hide:true},extensions:{paginationSection:'toc'},items:[BookModelV4.normalizeItem({id:unique('contents-title'),type:'part_title',label:'',title:'Περιεχόμενα',nav:{show:false,label:'Περιεχόμενα'},style:{keepWithNext:true},sourceRef:{kind:'generated-toc',sourceFile:result.fileName}}),BookModelV4.normalizeItem({id:unique('contents-body'),type:'paragraph',body:tocBody(hs),style:{align:'left',hyphenate:false,fontFamily:result.documentLayout?.source?.bodyFontFamily||'Calibri',fontSizePx:result.documentLayout?.source?.bodyFontSize||14.6667,lineHeight:1.15},sourceRef:{kind:'generated-toc',sourceFile:result.fileName},extensions:{generatedActiveToc:true}})]}))}
  const sourcePrefix=result.sourceType==='web'?'web-item-':'docx-item-';
  const items=[],ctx={fileName:result.fileName,page:entries[0]?.page||1,sourceKind:result.sourceType||'docx',preserveHeadingColors:preserve,imageName,id:block=>block.id?unique(block.id):unique(sourcePrefix+(++itemCounter))};
  const typographyScale=typographyScaleFactor(options),sourcePageFidelity=options.sourcePageFidelity!==false&&String(result.sourceType||'docx')==='docx'&&result.wordPageMap?.available===true;
  const convertedItems=convertEntries(result,entries,ctx).filter(item=>!(sourcePageFidelity&&item?.type==='clear'&&item?.extensions?.docxSourcePageSectionBreak));
  const canonicalItems=sourcePageFidelity?sourcePageFragments(convertedItems):convertedItems;items.push(...canonicalItems);
  const standaloneCompositeEquations=nestedCanonicalItems(canonicalItems).filter(item=>item?.type==='equation'&&(item?.extensions?.compositeEquationOverlay||item?.extensions?.compositeEquationFallback)).length;
  let tableMediaEquations=0;for(const table of nestedCanonicalItems(canonicalItems).filter(item=>item?.type==='table'))for(const row of table.rows||[])for(const cell of row.cells||[])for(const media of cell.media||[])tableMediaEquations+=(media.equations||[]).length;
  const positionedObjectAudit=canonicalPositionedObjectAudit(canonicalItems);
  if(sourcePageFidelity){
    const selectedStarts=entries.map(e=>Number(e?.page||e?.block?.page)).filter(Number.isFinite),selectedEnds=entries.map(e=>Number(e?.block?.sourcePageEnd||e?.block?.page||e?.page)).filter(Number.isFinite);
    const startPage=Math.max(1,selectedStarts.length?Math.min(...selectedStarts):1),endPage=Math.max(startPage,selectedEnds.length?Math.max(...selectedEnds):startPage);
    for(let sourcePage=startPage;sourcePage<=endPage;sourcePage++){
      const pageItems=items.filter(item=>Number(item?.sourceRef?.sourcePage||0)===sourcePage);
      applyContextualSpacing(pageItems);
      pages.push(BookModelV4.createPage({id:unique('source-page-'+sourcePage),sourcePage,header:{inherit:false,left:'',center:'',right:title},footer:{inherit:false,left:'© antarxo 2026',center:'',right:'{page}'},pageNumbering:{inherit:false,enabled:true,offset:0,hide:false},extensions:{paginationSection:'source-page',sourcePageFidelity:true,sourcePageLocked:true},items:pageItems}));
    }
  }else{
    applyContextualSpacing(items);
    pages.push(BookModelV4.createPage({id:unique('document-flow'),sourcePage:`${entries[0]?.page||1}-${entries.at(-1)?.page||1}`,header:{inherit:false,left:'',center:'',right:title},footer:{inherit:false,left:'© antarxo 2026',center:'',right:'{page}'},pageNumbering:{inherit:false,enabled:true,offset:0,hide:false},extensions:{paginationSection:'document-flow'},items}));
  }
  scaleTypographyObject(pages,typographyScale);
  const headingIndex=headingsForEntries(result,entries,9).map(h=>({id:h.id,title:h.text,level:h.level,sourcePage:h.page,sourceParagraph:h.sourceParagraph}));
  const importedLayout={...BookModelV4.DEFAULT_LAYOUT,...(result.documentLayout?.layoutDefaults||{})};scaleTypographyObject(importedLayout,typographyScale);
  const book=BookModelV4.createBook({meta:{projectId:id,fileName:'/'+id+'/book.json',title,subtitle:'Εισαγωγή από DOCX',defaultLanguage:'el',authoringVersion:CONVERTER,createdAt:new Date().toISOString(),updatedAt:new Date().toISOString()},layoutDefaults:importedLayout,pageDefaults:{header:{inherit:false,left:'',center:'',right:title},footer:{inherit:false,left:'© antarxo 2026',center:'',right:'{page}'},pageNumbering:{enabled:true,startAt:1,position:'footer-right',hideOnFirstPage:false}},pages,nav:{mode:'auto',showApp:false,showPrint:true,groups:[]},importManifest:{version:1,sourceType:'docx',sourceFile:result.fileName,createdAt:new Date().toISOString(),selection:{startBlock:entries[0]?.key||'',endBlock:entries.at(-1)?.key||'',startPage:entries[0]?.page||1,endPage:entries.at(-1)?.page||1},headingIndex,mergeReady:true,converter:CONVERTER,typographyScalePercent:Math.round(typographyScale*100),metrics:{sourcePages:result.pageCount,wordRenderedPageMap:deepClone(result.wordPageMap||{}),paragraphs:result.paras,listParagraphs:result.lists,tables:result.tables,nativeFloatingTables:result.nativeFloatingTables||0,aroundTablesConvertedToFlow:result.aroundTablesConvertedToFlow||0,mathDetectedRaw:result.mathCount,mathImportedCanonical:result.importedMathObjects,mathDuplicatesSkipped:result.mathDuplicatesSkipped,inlineMath:result.inlineMath,displayMath:result.displayMath,images:result.usedImages.length,duplicateImagesRemoved:result.duplicateImagesRemoved,imageGeometryAudit:result.imageGeometryAudit||{},textBoxesRaw:result.textBoxes,textBoxesUnique:result.textBoxesUnique,textBoxCaptions:result.textBoxCaptions,textBoxesImported:result.textBoxesImported,textBoxLabelsRetained:result.textBoxLabelsRetained,textBoxTablesRaw:result.textBoxTablesRaw||0,textBoxTablesImported:result.textBoxTablesImported||0,compositeFigures:(result.compositeCount||0)+(result.nativeVectorComposites||0),compositeEquationOverlays:(result.compositeEquationOverlays||0)+(result.nativeVectorEquationOverlays||0),tableInlineMediaFlowCells:result.tableInlineMediaFlowCells||0,tableTrustedAnchorsApplied:result.tableTrustedAnchorsApplied||0,tableParagraphAnchorsApplied:result.tableParagraphAnchorsApplied||0,tableAnchorsDeferred:result.tableAnchorsDeferred||0,nativeVectorComposites:result.nativeVectorComposites||0,nativeVectorShapes:result.nativeVectorShapes||0,nativeVectorPictures:result.nativeVectorPictures||0,nativeMixedGroups:result.nativeMixedGroups||0,nativeGroupEmbeddedImages:result.nativeGroupEmbeddedImages||0,nativeGroupEmbeddedImagesMissing:result.nativeGroupEmbeddedImagesMissing||0,nativeVectorTextOverlays:result.nativeVectorTextOverlays||0,nativeVectorEquationOverlays:result.nativeVectorEquationOverlays||0,nativeVectorUnsupportedPresets:result.nativeVectorUnsupportedPresets||[],standaloneCompositeEquations,tableMediaEquations,compositeBackgroundsAttached:result.compositeBackgroundsAttached||0,compositeEquationFallbacks:result.compositeEquationFallbacks||0,compositeFallbackComposites:result.compositeFallbackComposites||0}},extensions:{docxHeadingPalette:result.headingPalette||{},docxDocumentSettings:result.documentSettings||{},docxDocumentLayout:result.documentLayout?.source||{},docxCustomProperties:result.customProperties||{},positionedObjectAudit,...(sourcePageFidelity?{paginationPolicy:'source-page-overflow-first',sourcePageFidelity:true,sourcePageStart:Number(entries[0]?.page)||1,sourcePageEnd:Number(entries.at(-1)?.page)||1}:{})}});
  const normalized=BookModelV4.normalizeBook(book,{assignIds:true}).book,validation=BookModelV4.validateBook(normalized);if(!validation.ok)throw Error('Το canonical v4 βιβλίο δεν πέρασε validation: '+validation.errors.join('; '));return{book:normalized,imageMap:usedImages,validation};
}
function bookItemIds(book){const ids=new Set();for(const page of book?.pages||[]){if(page.id)ids.add(page.id);for(const item of nestedCanonicalItems(page.items||[]))if(item.id)ids.add(item.id)}return ids}
function usedImageNames(book){const names=new Set();for(const page of book?.pages||[])for(const item of nestedCanonicalItems(page.items||[])){if(item.type==='figure'&&String(item.src||'').startsWith('images/'))names.add(String(item.src).slice(7));if(item.type==='table')for(const row of item.rows||[])for(const cell of row.cells||[])for(const media of cell.media||[])if(String(media.src||'').startsWith('images/'))names.add(String(media.src).slice(7));}return names}
function starterPlaceholderItem(book,item){
  if(!item||item.type!=='hero')return false;
  if(item.sourceRef||item.provenance||item.extensions?.docxCanonicalImport)return false;
  const title=String(item.title||'').trim(),bookTitle=String(book?.meta?.title||'').trim();
  if(!['Τίτλος','Νέο βιβλίο',bookTitle].filter(Boolean).includes(title))return false;
  return !String(item.eyebrow||'').trim()&&!String(item.subtitle||'').trim();
}
function starterPlaceholderTarget(book,pageIndex,anchorIndex){
  if(!book||!Array.isArray(book.pages)||book.pages.length!==1)return false;
  const page=book.pages[pageIndex];
  if(!page||!Array.isArray(page.items)||page.items.length!==1||anchorIndex!==0)return false;
  return starterPlaceholderItem(book,page.items[0]);
}
function sourceSignature(fileName,entry){const block=entry?.block||{};return[String(fileName||'').toLowerCase(),entry?.page||0,block.sourceParagraph||0,block.type||'',blockText(block).slice(0,180).toLowerCase()].join('|')}
function duplicateEvidence(book,fileName,entries){
  const previous=(book?.importManifest?.insertions||[]).find(record=>String(record.sourceFile||'').toLowerCase()===String(fileName||'').toLowerCase()&&record.selection?.startBlock===entries[0]?.key&&record.selection?.endBlock===entries.at(-1)?.key);
  if(previous)return{kind:'manifest',message:'Το ίδιο λογικό τμήμα του ίδιου DOCX έχει ήδη εισαχθεί.',insertionId:previous.id};
  const existing=new Set();for(const page of book?.pages||[])for(const item of page.items||[]){const sr=item.sourceRef;if(sr?.sourceFile)existing.add([String(sr.sourceFile).toLowerCase(),sr.sourcePage||0,sr.sourceParagraph||0,item.type||'',BookModelV4.summarizeItem(item).slice(0,180).toLowerCase()].join('|'))}
  let hits=0;for(const entry of entries)if(existing.has(sourceSignature(fileName,entry)))hits++;
  if(entries.length&&hits>=Math.max(2,Math.ceil(entries.length*.7)))return{kind:'sourceRef',message:'Μεγάλο μέρος της επιλογής φαίνεται ήδη στο βιβλίο.',hits,total:entries.length};
  return null;
}
function buildInsertionDraft(inputBook,result,entries,anchor,position='after',options={}){
  if(!entries?.length)throw Error('Δεν έχει επιλεγεί περιεχόμενο DOCX.');
  const book=deepClone(inputBook),pageIndex=book.pages.findIndex(page=>page.id===anchor?.pageId);if(pageIndex<0)throw Error('Δεν βρέθηκε η σελίδα προορισμού.');
  const targetPage=book.pages[pageIndex],anchorIndex=targetPage.items.findIndex(item=>item.id===anchor?.itemId);if(anchorIndex<0)throw Error('Η παρεμβολή απαιτεί επιλεγμένο block προορισμού.');
  const sourceKind=result.sourceType||'docx',insertionId=(sourceKind==='web'?'web-insert-':'docx-insert-')+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,7),usedIds=bookItemIds(book),existingNames=usedImageNames(book),imageMap=new Map();
  const imageName=path=>{if(!path)return'';if(!imageMap.has(path)){const raw=path.split('/').pop().replace(/[^A-Za-z0-9._-]+/g,'_')||'image.bin',stem=insertionId.replace(/[^A-Za-z0-9_-]+/g,'_');let name=stem+'_'+raw,n=2;while(existingNames.has(name)||[...imageMap.values()].includes(name))name=stem+'_'+(n++)+'_'+raw;imageMap.set(path,name)}return imageMap.get(path)};
  let counter=0;const ctx={fileName:result.fileName,page:entries[0]?.page||1,sourceKind,preserveHeadingColors:options.preserveHeadingColors!==false,imageName,id:block=>BookModelV4.uniqueId(block.id||`${insertionId}-item-${++counter}`,usedIds)};
  const typographyScale=typographyScaleFactor(options);const inserted=convertEntries(result,entries,ctx);scaleTypographyObject(inserted,typographyScale);for(const item of inserted)item.sourceRef={...(item.sourceRef||{}),insertionId};
  applyContextualSpacing(inserted);if(!inserted.length)throw Error('Η επιλεγμένη περιοχή δεν παρήγαγε canonical blocks.');
  const replaceStarter=options.replaceStarterPlaceholder===true&&starterPlaceholderTarget(book,pageIndex,anchorIndex);
  const insertAt=replaceStarter?anchorIndex:anchorIndex+(position==='after'?1:0);
  targetPage.items.splice(insertAt,replaceStarter?1:0,...inserted);
  const evidence=duplicateEvidence(inputBook,result.fileName,entries);
  return{book,pageIndex,insertionId,sourceKind,entries:deepClone(entries),anchor:{pageId:targetPage.id,itemId:anchor.itemId,label:anchor.label||BookModelV4.summarizeItem(targetPage.items[anchorIndex])},position:replaceStarter?'replace':position,replacedStarterPlaceholder:replaceStarter,insertedIds:inserted.map(item=>item.id),firstInsertedId:inserted[0].id,imageMap,duplicateEvidence:evidence,positionedObjectAudit:canonicalPositionedObjectAudit(inserted)};
}
function finalizeInsertionManifest(draft,meta={}){
  const book=draft.book;book.importManifest=book.importManifest&&typeof book.importManifest==='object'?book.importManifest:{version:1,sourceType:'mixed'};
  book.importManifest.insertions=Array.isArray(book.importManifest.insertions)?book.importManifest.insertions:[];
  book.importManifest.insertions.push({id:draft.insertionId,createdAt:new Date().toISOString(),sourceType:draft.sourceKind||'docx',sourceFile:meta.sourceFile||'',selection:{startPage:draft.entries[0]?.page||1,endPage:draft.entries.at(-1)?.page||1,startBlock:draft.entries[0]?.key||'',endBlock:draft.entries.at(-1)?.key||'',firstLabel:entryLabel(draft.entries[0]),lastLabel:entryLabel(draft.entries.at(-1))},target:{pageId:draft.anchor.pageId,itemId:draft.anchor.itemId,label:draft.anchor.label||'',position:draft.position},itemsInserted:draft.insertedIds.length,pagesAfterLocalPagination:draft.generatedPageIds?.length||1,imagesAdded:Number(meta.imagesAdded)||0,positionedObjectAudit:deepClone(draft.positionedObjectAudit||{}),backup:meta.backupPath||'',tool:draft.sourceKind==='web'?'bookwriter-web-v1':CONVERTER});
  book.meta={...(book.meta||{}),...(draft.sourceKind==='web'?{lastWebInsertionVersion:'bookwriter-web-v1'}:{lastDocxInsertionVersion:CONVERTER}),updatedAt:new Date().toISOString()};
  return book;
}
function audit(result,entries){return{sourceFile:result.fileName,totalSourcePages:result.pageCount,selectedBlocks:entries.length,selectedPages:entries.length?{start:entries[0].page,end:entries.at(-1).page}:null,paragraphs:result.paras,listParagraphs:result.lists,tables:result.tables,nativeFloatingTables:result.nativeFloatingTables||0,aroundTablesConvertedToFlow:result.aroundTablesConvertedToFlow||0,mathObjectsDetectedRaw:result.mathCount,mathObjectsImportedCanonical:result.importedMathObjects,mathDuplicatesSkipped:result.mathDuplicatesSkipped,inlineMath:result.inlineMath,displayMath:result.displayMath,rawImageRefs:result.rawImageRefs,imagesAfterDedupe:result.usedImages.length,duplicateImagesRemoved:result.duplicateImagesRemoved,imageGeometryAudit:result.imageGeometryAudit||{},fallbackImagesSkipped:result.fallbackImagesSkipped,textBoxesRaw:result.textBoxes,textBoxesUnique:result.textBoxesUnique,textBoxCaptions:result.textBoxCaptions,textBoxesImported:result.textBoxesImported,textBoxLabelsRetained:result.textBoxLabelsRetained,textBoxTablesRaw:result.textBoxTablesRaw||0,textBoxTablesImported:result.textBoxTablesImported||0,compositeFigures:(result.compositeCount||0)+(result.nativeVectorComposites||0),compositeEquationOverlays:(result.compositeEquationOverlays||0)+(result.nativeVectorEquationOverlays||0),tableInlineMediaFlowCells:result.tableInlineMediaFlowCells||0,tableTrustedAnchorsApplied:result.tableTrustedAnchorsApplied||0,tableParagraphAnchorsApplied:result.tableParagraphAnchorsApplied||0,tableAnchorsDeferred:result.tableAnchorsDeferred||0,nativeVectorComposites:result.nativeVectorComposites||0,nativeVectorShapes:result.nativeVectorShapes||0,nativeVectorPictures:result.nativeVectorPictures||0,nativeMixedGroups:result.nativeMixedGroups||0,nativeGroupEmbeddedImages:result.nativeGroupEmbeddedImages||0,nativeGroupEmbeddedImagesMissing:result.nativeGroupEmbeddedImagesMissing||0,nativeVectorTextOverlays:result.nativeVectorTextOverlays||0,nativeVectorEquationOverlays:result.nativeVectorEquationOverlays||0,nativeVectorUnsupportedPresets:result.nativeVectorUnsupportedPresets||[],compositeBackgroundsAttached:result.compositeBackgroundsAttached||0,compositeEquationFallbacks:result.compositeEquationFallbacks||0,compositeFallbackComposites:result.compositeFallbackComposites||0,drawings:result.drawings,reconciledPageBreaks:result.reconciledBreaks,skippedStaticTocParagraphs:result.skippedStaticToc,unsupportedMath:result.unsupportedMath,documentLayout:result.documentLayout?.source||{},positionedObjectsRaw:rawPositionedObjectAudit(entries),canonicalTarget:'bookwriter-v4',converter:CONVERTER}}
function launcher(title,id,editor=false){if(editor)return`<!doctype html><html lang="el"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Επεξεργασία — ${esc(title)}</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;font:16px system-ui}</style></head><body><p>Άνοιξε τον ΣΥΓΓΡΑΦΕΑ και επίλεξε τον φάκελο του βιβλίου <b>${esc(id)}</b>.</p><p><a href="../../author/index.html">Άνοιγμα ΣΥΓΓΡΑΦΕΑ</a></p></body></html>`;return`<!doctype html><html lang="el"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(title)}</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;font:16px system-ui}</style></head><body><p>Άνοιγμα… <a id="a">συνέχεια</a></p><script>const t=new URL('../../reader/index.html',location.href);t.searchParams.set('book','../books/${id}/book.json');document.getElementById('a').href=t;location.replace(t);<\/script></body></html>`}
global.DocxCoreV4=Object.freeze({VERSION:'4.8.7e-hf27-font-style-theme-truth',REQUIRED_WORD_PROFILE,parseDocx,flattenEntries,entriesInRange,entryLabel,blockText,formattedParagraphHtml,normalizedCompositeFigure,standaloneCompositeEquationItems,makeBook,buildInsertionDraft,rawPositionedObjectAudit,canonicalPositionedObjectAudit,finalizeInsertionManifest,duplicateEvidence,audit,launcher,safeId,deepClone,canonicalTextStyle});
})(window);
