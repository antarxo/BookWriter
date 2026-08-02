(function(global){
'use strict';
const NS={w:'http://schemas.openxmlformats.org/wordprocessingml/2006/main',r:'http://schemas.openxmlformats.org/officeDocument/2006/relationships',m:'http://schemas.openxmlformats.org/officeDocument/2006/math',a:'http://schemas.openxmlformats.org/drawingml/2006/main',wp:'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',v:'urn:schemas-microsoft-com:vml',mc:'http://schemas.openxmlformats.org/markup-compatibility/2006'};
const CONVERTER='bookwriter-4.5.0-rc1-docx';
const twipsToPx=v=>Number(v||0)/15;
const halfPointsToPx=v=>Number(v||0)*2/3;
const numOrNull=v=>v===''||v===null||v===undefined?null:Number(v);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const lname=n=>n&&n.localName||''; const elems=n=>[...(n?.childNodes||[])].filter(x=>x.nodeType===1);
const q=(n,ns,name)=>n?.getElementsByTagNameNS(ns,name)||[];
const first=(n,ns,name)=>q(n,ns,name)[0]||null;
const attr=(n,ns,name)=>n?.getAttributeNS(ns,name)||n?.getAttribute(name)||'';
const xml=s=>new DOMParser().parseFromString(s,'application/xml');
const serializer=new XMLSerializer();
function directText(n){let s='';for(const t of q(n,NS.w,'t')){let p=t.parentNode,insideBox=false;while(p&&p!==n){if(lname(p)==='txbxContent'){insideBox=true;break}p=p.parentNode}if(!insideBox)s+=t.textContent}return s}
function allText(n){return [...q(n,NS.w,'t'),...q(n,NS.m,'t')].map(x=>x.textContent).join('')}
function themeFonts(theme){
  const findFont=kind=>{const group=first(theme,NS.a,kind),latin=first(group,NS.a,'latin');return latin?.getAttribute('typeface')||''};
  return{majorHAnsi:findFont('majorFont')||'Cambria',minorHAnsi:findFont('minorFont')||'Calibri'};
}
function fontName(rFonts,themes={}){
  if(!rFonts)return'';
  const direct=attr(rFonts,NS.w,'ascii')||attr(rFonts,NS.w,'hAnsi')||attr(rFonts,NS.w,'cs')||attr(rFonts,NS.w,'eastAsia');
  if(direct)return direct;
  const themed=attr(rFonts,NS.w,'asciiTheme')||attr(rFonts,NS.w,'hAnsiTheme');
  if(/major/i.test(themed))return themes.majorHAnsi||'Cambria';
  if(/minor/i.test(themed))return themes.minorHAnsi||'Calibri';
  return'';
}
function runMetrics(rPr,themes={}){
  if(!rPr)return{};
  const col=first(rPr,NS.w,'color'),rawColor=attr(col,NS.w,'val'),sz=first(rPr,NS.w,'sz'),fonts=first(rPr,NS.w,'rFonts'),shd=first(rPr,NS.w,'shd'),hi=first(rPr,NS.w,'highlight');
  const out={};
  const family=fontName(fonts,themes);if(family)out.fontFamily=family;
  const size=attr(sz,NS.w,'val');if(size!=='')out.fontSizePx=halfPointsToPx(size);
  const color=validHex(rawColor)||(String(rawColor).toLowerCase()==='auto'?'#000000':'');if(color)out.textColor=color;
  const fill=nodeColor(shd,'fill')||validHex(attr(hi,NS.w,'val'));if(fill)out.highlight=fill;
  for(const [key,name]of[['bold','b'],['italic','i'],['underline','u']]){const n=first(rPr,NS.w,name);if(n)out[key]=wordBool(n,true)}
  return out;
}
function runProps(r,themes={}){const p=first(r,NS.w,'rPr'),m=runMetrics(p,themes),vert=attr(first(p,NS.w,'vertAlign'),NS.w,'val');return{b:!!m.bold,i:!!m.italic,u:!!m.underline,sup:vert==='superscript',sub:vert==='subscript',color:m.textColor||'',highlight:m.highlight||'',fontFamily:m.fontFamily||'',fontSizePx:m.fontSizePx||null}}
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
  const style=display?' style="display:block;overflow-x:auto;text-align:center;margin:0.45em 0"':'';
  return '<span class="'+cls+'"'+style+'><math xmlns="http://www.w3.org/1998/Math/MathML" display="'+(display?'block':'inline')+'">'+body+'</math></span>';
}
async function readXml(zip,path,optional=false){const f=zip.file(path);if(!f){if(optional)return null;throw new Error('Λείπει '+path)}return xml(await f.async('string'))}
function validHex(v){const s=String(v||'').replace(/^#/,'').trim();return /^[0-9A-Fa-f]{6}$/.test(s)?'#'+s.toUpperCase():''}
function nodeColor(n,kind='color'){
  if(!n)return '';
  const val=attr(n,NS.w,kind==='fill'?'fill':'val');
  return validHex(val);
}
function wordBool(n,fallback=null){if(!n)return fallback;const v=String(attr(n,NS.w,'val')||'1').toLowerCase();return !['0','false','off','no'].includes(v)}
function documentSettings(settings){return{autoHyphenation:wordBool(first(settings,NS.w,'autoHyphenation'),false),doNotHyphenateCaps:wordBool(first(settings,NS.w,'doNotHyphenateCaps'),false)}}
function mergeVisual(parent={},child={}){
  const out={...(parent||{})};
  for(const[k,v]of Object.entries(child||{}))if(v!==undefined&&v!==null&&v!=='')out[k]=v;
  return out;
}
function paragraphMetrics(pPr,themes={}){
  if(!pPr)return{};
  const out={},shd=first(pPr,NS.w,'shd'),jc=first(pPr,NS.w,'jc'),suppress=first(pPr,NS.w,'suppressAutoHyphens'),spacing=first(pPr,NS.w,'spacing'),ind=first(pPr,NS.w,'ind'),rPr=first(pPr,NS.w,'rPr');
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
function documentLayout(doc,styles,themes={}){
  const defaults=docDefaultVisual(styles,themes),sect=q(doc,NS.w,'sectPr')[q(doc,NS.w,'sectPr').length-1]||null,pgSz=first(sect,NS.w,'pgSz'),pgMar=first(sect,NS.w,'pgMar');
  const pageWidthPx=twipsToPx(attr(pgSz,NS.w,'w')||11906),pageHeightPx=twipsToPx(attr(pgSz,NS.w,'h')||16838),top=twipsToPx(attr(pgMar,NS.w,'top')||851),right=twipsToPx(attr(pgMar,NS.w,'right')||851),bottom=twipsToPx(attr(pgMar,NS.w,'bottom')||851),left=twipsToPx(attr(pgMar,NS.w,'left')||851),header=twipsToPx(attr(pgMar,NS.w,'header')||708),footer=twipsToPx(attr(pgMar,NS.w,'footer')||708);
  const bodyFontFamily=defaults.fontFamily||themes.minorHAnsi||'Calibri',bodyFontSize=defaults.fontSizePx||14.6667,lineHeight=defaults.lineHeight||1.15,paragraphGap=defaults.marginBottomPx??(10*96/72);
  return{defaults,layoutDefaults:{pageSize:'A4',orientation:pageWidthPx>pageHeightPx?'landscape':'portrait',pageWidthPx,pageHeightPx,pagePaddingTopPx:top,pagePaddingRightPx:right,pagePaddingBottomPx:bottom,pagePaddingLeftPx:left,headerTopPx:Math.max(18,top-header+16),headerHeightPx:20,footerBottomPx:Math.max(18,bottom-footer+16),footerHeightPx:20,headerFontSize:11,footerFontSize:11,bodyFontSize,lineHeight,paragraphGap,sectionGap:Math.max(8,paragraphGap),showPageNumbers:true,bodyFontFamily,headingFontFamily:themes.majorHAnsi||'Cambria',sectionHeadingFontSize:18.6667,partTitleFontSize:21.3333,captionFontSize:12},source:{pageWidthPx,pageHeightPx,marginsPx:{top,right,bottom,left},headerDistancePx:header,footerDistancePx:footer,bodyFontFamily,bodyFontSize,lineHeight,paragraphGap}}}
function relMap(rels){const m=new Map();if(!rels)return m;for(const x of rels.getElementsByTagName('Relationship'))m.set(x.getAttribute('Id'),{target:x.getAttribute('Target'),type:x.getAttribute('Type')});return m}
function numberingMap(num){
  const result={numToAbs:new Map(),absFmt:new Map(),absLevels:new Map(),numOverrides:new Map()};
  if(!num)return result;
  for(const a of q(num,NS.w,'abstractNum')){
    const id=attr(a,NS.w,'abstractNumId'),formats=new Map(),levels=new Map();
    for(const l of q(a,NS.w,'lvl')){
      const il=Number(attr(l,NS.w,'ilvl')||0),fmt=attr(first(l,NS.w,'numFmt'),NS.w,'val')||'bullet';
      formats.set(il,fmt);
      levels.set(il,{
        fmt,
        start:Number(attr(first(l,NS.w,'start'),NS.w,'val')||1)||1,
        text:attr(first(l,NS.w,'lvlText'),NS.w,'val')||'',
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
  return({decimal:'decimal',decimalZero:'decimal-leading-zero',lowerLetter:'lower-greek',upperLetter:'upper-alpha',lowerRoman:'lower-roman',upperRoman:'upper-roman'})[fmt]||'decimal';
}
function pInfo(p,styles,nums,themes={}){
  const pPr=first(p,NS.w,'pPr'),sid=attr(first(pPr,NS.w,'pStyle'),NS.w,'val'),st=styles.get(sid)||{name:sid||'Normal',outline:null,visual:styles.defaultVisual||{}};
  let level=null;const hm=String(st.name).match(/heading\s*([1-9])/i);if(hm)level=Number(hm[1]);else if(st.outline!==null)level=st.outline+1;
  const numPr=first(pPr,NS.w,'numPr'),numId=attr(first(numPr,NS.w,'numId'),NS.w,'val'),ilvl=Number(attr(first(numPr,NS.w,'ilvl'),NS.w,'val')||0);let listType='',numFmt='',listStartBase=1,listText='';
  if(numId&&numId!=='0'){const spec=numberingLevel(nums,numId,ilvl);numFmt=spec.fmt;listStartBase=spec.start;listText=spec.text;listType=numFmt==='bullet'?'ul':'ol'}
  const paragraphStyle=mergeVisual(st.visual||styles.defaultVisual||{},directParagraphVisual(p,themes));
  const isTocStyle=/^(toc\s*heading|toc\s*\d+|contents?)$/i.test(String(st.name||'').trim());
  return{styleId:sid,styleName:st.name,headingLevel:level,listType,numId,ilvl,numFmt,listStartBase,listText,headingStyle:paragraphStyle,paragraphStyle,isTocStyle};
}
function headingSlug(value=''){const s=String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9\u0370-\u03ff]+/g,'-').replace(/^-+|-+$/g,'').slice(0,64);return s||'heading'}
function stableHeadingId(text,level,counts){const base='docx-h'+level+'-'+headingSlug(text),n=(counts.get(base)||0)+1;counts.set(base,n);return n===1?base:base+'-'+n}
function imageRefs(node){const ids=[];for(const b of q(node,NS.a,'blip')){const id=attr(b,NS.r,'embed');if(id)ids.push({rid:id,kind:'drawing'})}for(const im of q(node,NS.v,'imagedata')){const id=attr(im,NS.r,'id');if(id)ids.push({rid:id,kind:'vml'})}const seen=new Set();return ids.filter(x=>{const k=x.kind+'|'+x.rid;if(seen.has(k))return false;seen.add(k);return true})}
function vmlSize(node){const shape=first(node,NS.v,'shape');const style=shape?.getAttribute('style')||'';const read=name=>{const m=style.match(new RegExp('(?:^|;)\\s*'+name+'\\s*:\\s*([0-9.]+)(pt|px|in|cm|mm)','i'));if(!m)return 0;const n=Number(m[1]),u=m[2].toLowerCase();return Math.round(n*({px:1,pt:96/72,in:96,cm:96/2.54,mm:96/25.4}[u]||1))};return{width:read('width')||undefined,height:read('height')||undefined}}
function drawingMemberExtent(node,rid){
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
    return width&&height?{width,height}:null;
  }
  return null;
}
function imageGeometry(node,rid=''){
  const ex=first(node,NS.wp,'extent');let width,height;
  if(ex){width=Math.round(Number(ex.getAttribute('cx')||0)/9525)||undefined;height=Math.round(Number(ex.getAttribute('cy')||0)/9525)||undefined}
  if(!width){const v=vmlSize(node);width=v.width;height=v.height}
  // In a grouped Word drawing wp:extent is the size of the whole group.
  // Each embedded picture has its own a:xfrm/a:ext. Applying the group extent
  // to every member turns small graphs into page-sized figures.
  const member=drawingMemberExtent(node,rid);
  if(member){width=member.width;height=member.height}
  const anchor=first(node,NS.wp,'anchor');const floating=!!anchor;
  let placement=floating?'float-right':'wide';
  if(anchor){const pos=first(anchor,NS.wp,'positionH');const align=first(pos,NS.wp,'align')?.textContent?.trim().toLowerCase()||'';if(align==='left'||align==='inside')placement='float-left';else if(align==='right'||align==='outside')placement='float-right';else if(align==='center')placement='float-right'}
  return{width,height,floating,placement};
}
function paragraphSegments(p,ctx,sourceParagraph=0){
  const info=pInfo(p,ctx.styles,ctx.nums,ctx.themes);let segments=[{html:'',images:[],page:ctx.page,sourceParagraph,breakBefore:info.paragraphStyle?.pageBreakBefore?'style':null}];const cur=()=>segments[segments.length-1];
  function pageBreak(kind='rendered'){ctx.detectedBreaks=(ctx.detectedBreaks||0)+1;ctx.page++;segments.push({html:'',images:[],page:ctx.page,sourceParagraph,breakBefore:kind})}
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
    if(l==='lastRenderedPageBreak'){pageBreak('rendered');return}
    if(l==='br'&&attr(n,NS.w,'type')==='page'&&!ctx.hasRenderedBreaks){pageBreak('explicit');return}
    if(l==='oMathPara'){cur().html+=ommlToMath(n,true);ctx.displayMath++;ctx.importedMathObjects+=q(n,NS.m,'oMath').length;return}
    if(l==='oMath'){cur().html+=ommlToMath(n,false);ctx.inlineMath++;ctx.importedMathObjects++;return}
    if(l==='drawing'||l==='pict'||l==='object'){
      for(const ref of imageRefs(n)){const rr=ctx.rels.get(ref.rid);if(rr){const geom=imageGeometry(n,ref.rid);const path=('word/'+rr.target.replace(/^\.\.\//,'')).replace(/\\/g,'/');cur().images.push({rid:ref.rid,path,kind:ref.kind,...geom,sourceParagraph});ctx.rawImageRefs++;}}
      return;
    }
    if(l==='r'){
      const pr=runProps(n,ctx.themes);
      for(const c of elems(n)){const cl=lname(c);if(cl==='t')cur().html+=wrapRun(c.textContent,pr,href);else if(cl==='tab')cur().html+='&emsp;';else if(cl==='br'){if(attr(c,NS.w,'type')==='page'){if(!ctx.hasRenderedBreaks)pageBreak('explicit')}else cur().html+='<br>'}else if(cl==='lastRenderedPageBreak')pageBreak('rendered');else walk(c,href)}
      return;
    }
    if(l==='hyperlink'){const id=attr(n,NS.r,'id'),anchor=attr(n,NS.w,'anchor');const target=ctx.rels.get(id)?.target||(anchor?'#'+BookModelV4.normalizeId(anchor):'');for(const c of elems(n))walk(c,target);return}
    for(const c of elems(n))walk(c,href);
  }
  for(const c of elems(p)){if(lname(c)==='pPr')continue;walk(c)}
  const hyphenate=!!ctx.settings?.autoHyphenation&&info.paragraphStyle?.suppressAutoHyphens!==true;
  return segments.map(seg=>({...seg,info,paragraphStyle:info.paragraphStyle||{},hyphenate,plain:seg.html.replace(/<[^>]+>/g,'').replace(/&emsp;/g,' ').trim()})).filter(seg=>seg.html.trim()||seg.images.length);
}
function tableBlock(tbl,ctx,page,sourceParagraph=0){
  const grid=first(tbl,NS.w,'tblGrid'),columnWidthsPx=elems(grid).filter(x=>lname(x)==='gridCol').map(x=>twipsToPx(attr(x,NS.w,'w'))).filter(x=>x>0),rows=[];let headerRows=0,stillHeader=true,maxColumns=1;
  for(const tr of elems(tbl).filter(x=>lname(x)==='tr')){
    const trPr=first(tr,NS.w,'trPr'),isHeader=!!first(trPr,NS.w,'tblHeader');if(stillHeader&&isHeader)headerRows++;else stillHeader=false;
    const cells=[];let columns=0;
    for(const tc of elems(tr).filter(x=>lname(x)==='tc')){
      const tcPr=first(tc,NS.w,'tcPr'),span=Math.max(1,Number(attr(first(tcPr,NS.w,'gridSpan'),NS.w,'val'))||1),shade=nodeColor(first(tcPr,NS.w,'shd'),'fill'),vAlign=attr(first(tcPr,NS.w,'vAlign'),NS.w,'val')||'top',tcW=twipsToPx(attr(first(tcPr,NS.w,'tcW'),NS.w,'w'));
      let html='';const paragraphStyles=[];
      for(const cp of elems(tc).filter(x=>lname(x)==='p')){const local={...ctx,page};const before={inlineMath:local.inlineMath,displayMath:local.displayMath,importedMathObjects:local.importedMathObjects,rawImageRefs:local.rawImageRefs};const segs=paragraphSegments(cp,local);ctx.inlineMath+=local.inlineMath-before.inlineMath;ctx.displayMath+=local.displayMath-before.displayMath;ctx.importedMathObjects+=local.importedMathObjects-before.importedMathObjects;ctx.rawImageRefs+=local.rawImageRefs-before.rawImageRefs;for(const seg of segs){if(html)html+='<br>';html+=seg.html;paragraphStyles.push(seg.paragraphStyle||{})}}
      const style={verticalAlign:vAlign};if(shade)style.backgroundColor=shade;if(tcW>0)style.widthPx=tcW;
      cells.push({html,colspan:span,rowspan:1,style,paragraphStyle:paragraphStyles[0]||{}});columns+=span;
    }
    maxColumns=Math.max(maxColumns,columns);rows.push({cells});
  }
  const html='<table>'+rows.map(r=>'<tr>'+r.cells.map(c=>'<td'+(c.colspan>1?' colspan="'+c.colspan+'"':'')+'>'+c.html+'</td>').join('')+'</tr>').join('')+'</table>';
  return{type:'table',html,source:'table',page,rows,columns:maxColumns,tableStyle:{columnWidthsPx,headerRows,keepTogether:false},sourceParagraph};
}
function ext(path){const m=path.match(/\.([a-zA-Z0-9]+)$/);return m?m[1].toLowerCase():'bin'}
function mime(path){return({png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',gif:'image/gif',svg:'image/svg+xml',emf:'image/x-emf',wmf:'image/x-wmf',bmp:'image/bmp',tif:'image/tiff',tiff:'image/tiff'})[ext(path)]||'application/octet-stream'}
async function blobHash(blob){
  const bytes=new Uint8Array(await blob.arrayBuffer());
  if(globalThis.crypto?.subtle){const digest=await crypto.subtle.digest('SHA-256',bytes);return [...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('')}
  let h=2166136261;for(const b of bytes){h^=b;h=Math.imul(h,16777619)}return 'fnv-'+(h>>>0).toString(16)+'-'+bytes.length;
}
function imageScore(b){return (b.kind==='drawing'?1000:0)+(b.floating?500:0)+(b.width||0)+(b.height?Math.min(b.height,300):0)}
async function finalizeImages(pages,zip){
  const paths=new Set();for(const arr of pages.values())for(const b of arr)if(b.type==='figure')paths.add(b.srcPath);
  const blobs=new Map(),hashes=new Map();
  for(const path of paths){const f=zip.file(path);if(!f)continue;const blob=await f.async('blob');blobs.set(path,blob);hashes.set(path,await blobHash(blob))}
  let removed=0;
  for(const [page,arr] of pages){
    const out=[],seen=new Map();
    for(const b of arr){
      if(b.type!=='figure'){out.push(b);continue}
      const hash=hashes.get(b.srcPath)||b.srcPath;const key=b.sourceParagraph+'|'+hash;
      if(!seen.has(key)){seen.set(key,out.length);out.push({...b,contentHash:hash});continue}
      const idx=seen.get(key),old=out[idx];
      if(imageScore(b)>imageScore(old)){out[idx]={...b,caption:b.caption||old.caption||'',contentHash:hash}}
      else if(!old.caption&&b.caption)old.caption=b.caption;
      removed++;
    }
    pages.set(page,out);
  }
  const effectivePaths=new Set();for(const arr of pages.values())for(const b of arr)if(b.type==='figure')effectivePaths.add(b.srcPath);
  const imageBlobs=new Map();for(const path of effectivePaths)if(blobs.has(path))imageBlobs.set(path,blobs.get(path));
  return{imageBlobs,usedImages:[...effectivePaths],duplicateImagesRemoved:removed};
}
function textBoxAncestor(box){let n=box?.parentNode;while(n&& !['pict','drawing','object'].includes(lname(n)))n=n.parentNode;return n}
function textBoxRecords(paragraph,ctx,sourceParagraph){
  const records=[],seen=new Set();
  for(const box of q(paragraph,NS.w,'txbxContent')){
    const text=allText(box).replace(/\s+/g,' ').trim();if(!text)continue;
    const key=text.toLocaleLowerCase('el');if(seen.has(key))continue;seen.add(key);
    const caption=/^(Εικόνα|Σχήμα|Γράφημα|Figure|Fig\.|Graph)\s*\d*/i.test(text);
    const shape=textBoxAncestor(box),geom=imageGeometry(shape||box);let html='',paragraphStyle={};
    for(const bp of elems(box).filter(x=>lname(x)==='p')){const local={...ctx,page:ctx.page,hasRenderedBreaks:false};const before={inlineMath:local.inlineMath,displayMath:local.displayMath,importedMathObjects:local.importedMathObjects,rawImageRefs:local.rawImageRefs};const segs=paragraphSegments(bp,local,sourceParagraph);ctx.inlineMath+=local.inlineMath-before.inlineMath;ctx.displayMath+=local.displayMath-before.displayMath;ctx.importedMathObjects+=local.importedMathObjects-before.importedMathObjects;ctx.rawImageRefs+=local.rawImageRefs-before.rawImageRefs;for(const seg of segs){if(html)html+='<br>';html+=seg.html;if(!Object.keys(paragraphStyle).length)paragraphStyle=seg.paragraphStyle||{}}}
    const substantive=!caption&&(text.length>=170||/^(Πυκνότητα!|Σχόλιο|Παρατήρηση|Θυμήσου|Να πάρεις υπόψη|Η εξίσωση Schrödinger)/i.test(text));
    const labelMatch=text.match(/^([^.!?]{2,45}[!?:])\s+/u);
    records.push({text,html:html||esc(text),caption,substantive,label:labelMatch?labelMatch[1]:'',width:geom.width,height:geom.height,placement:geom.placement||'wide',floating:geom.floating,paragraphStyle,sourceParagraph});
  }
  return records;
}
function captionTexts(paragraph,ctx,sourceParagraph){return textBoxRecords(paragraph,ctx,sourceParagraph).filter(x=>x.caption).map(x=>x.text)}
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
  const ctx={zip,styles,rels,nums,settings,themes,page:1,listCounters:new Map(),mathCount:q(doc,NS.m,'oMath').length,inlineMath:0,displayMath:0,importedMathObjects:0,rawImageRefs:0,alternateFallbacksSkipped:0,fallbackImagesSkipped:0,detectedBreaks:0,reconciledBreaks:0,hasRenderedBreaks:q(doc,NS.w,'lastRenderedPageBreak').length>0,skippedStaticToc:0};
  const ordered=[];let tables=0,paras=0,lists=0,paraIndex=0;const headings=[],headingIdCounts=new Map();let textBoxesUnique=0,textBoxCaptions=0,textBoxesImported=0,textBoxLabelsRetained=0;
  const push=(block,page=ctx.page)=>ordered.push({...block,page:Number(page)||1});
  const body=first(doc,NS.w,'body');
  for(const child of elems(body)){
    const beforeBreaks=ctx.detectedBreaks,expectedBreaks=ctx.hasRenderedBreaks?q(child,NS.w,'lastRenderedPageBreak').length:0;
    if(lname(child)==='p'){
      paras++;paraIndex++;
      const bookmarkNames=[...q(child,NS.w,'bookmarkStart')].map(x=>attr(x,NS.w,'name')).filter(x=>x&&x!=='_GoBack');const boxes=textBoxRecords(child,ctx,paraIndex);textBoxesUnique+=boxes.length;textBoxCaptions+=boxes.filter(x=>x.caption).length;
      const captions=boxes.filter(x=>x.caption);const labels=boxes.filter(x=>!x.caption&&!x.substantive).map(x=>({text:x.text,html:x.html}));let captionIndex=0;
      const segs=paragraphSegments(child,ctx,paraIndex);let segmentNo=0,listOrdinalAssigned=false,listOrdinal=null;
      for(const seg of segs){const firstParagraphSegment=segmentNo++===0,paragraphBookmarkId=firstParagraphSegment?BookModelV4.normalizeId(bookmarkNames[0]||''):'';
        const t=seg.plain,inf=seg.info,breakBefore=seg.breakBefore||null;
        if(inf.isTocStyle){ctx.skippedStaticToc++;continue}
        let headingId='';
        if(inf.headingLevel&&t){
          headingId=BookModelV4.normalizeId(bookmarkNames[0]||stableHeadingId(t,inf.headingLevel,headingIdCounts));
          headings.push({page:seg.page,level:inf.headingLevel,text:t,id:headingId,headingStyle:inf.headingStyle,sourceStyle:inf.styleName,sourceParagraph:seg.sourceParagraph});
          push({type:inf.headingLevel===1?'part_title':'section_heading',title:t,level:inf.headingLevel,id:headingId,headingStyle:inf.headingStyle,sourceStyle:inf.styleName,sourceParagraph:seg.sourceParagraph,breakBefore},seg.page);
        }else if(inf.listType&&t){
          if(!listOrdinalAssigned){listOrdinal=nextListOrdinal(inf,ctx.listCounters);listOrdinalAssigned=true}
          if(firstParagraphSegment){
            lists++;
            push({type:'list_item',id:paragraphBookmarkId,listType:inf.listType,numId:inf.numId,numFmt:inf.numFmt,listText:inf.listText,listOrdinal,level:inf.ilvl,html:seg.html,paragraphStyle:seg.paragraphStyle,hyphenate:seg.hyphenate,sourceStyle:inf.styleName,sourceParagraph:seg.sourceParagraph,breakBefore},seg.page);
          }else{
            push({type:'paragraph',id:'',html:seg.html,paragraphStyle:seg.paragraphStyle,hyphenate:seg.hyphenate,sourceStyle:inf.styleName,sourceParagraph:seg.sourceParagraph,listContinuation:true,breakBefore},seg.page);
          }
        }else if(seg.html.trim()){
          push({type:'paragraph',id:paragraphBookmarkId,html:seg.html,paragraphStyle:seg.paragraphStyle,hyphenate:seg.hyphenate,sourceStyle:inf.styleName,sourceParagraph:seg.sourceParagraph,breakBefore},seg.page);
        }
        let imageNo=0;
        for(const im of seg.images){
          const captionBox=captions[captionIndex++]||null,caption=captionBox?.html||captionBox?.text||'';
          const imageLabels=imageNo===0?labels:[];imageNo++;
          if(imageLabels.length)textBoxLabelsRetained+=imageLabels.length;
          push({type:'figure',srcPath:im.path,width:im.width,height:im.height,floating:im.floating,placement:im.placement,kind:im.kind,sourceParagraph:im.sourceParagraph,caption,textLabels:imageLabels,breakBefore},seg.page);
        }
      }
      for(const box of boxes.filter(x=>x.substantive)){
        textBoxesImported++;
        push({type:'textbox',html:box.html,text:box.text,label:box.label,width:box.width,height:box.height,placement:box.placement,floating:box.floating,paragraphStyle:box.paragraphStyle,sourceStyle:'DOCX Text Box',sourceParagraph:paraIndex},ctx.page);
      }
      ctx.page=Math.max(ctx.page,...segs.map(x=>x.page),ctx.page);
    }else if(lname(child)==='tbl'){
      tables++;paraIndex++;push(tableBlock(child,ctx,ctx.page,paraIndex),ctx.page);
    }
    const seenBreaks=ctx.detectedBreaks-beforeBreaks;if(expectedBreaks>seenBreaks){const miss=expectedBreaks-seenBreaks;ctx.page+=miss;ctx.detectedBreaks+=miss;ctx.reconciledBreaks+=miss}
  }
  const compact=[];
  for(let i=0;i<ordered.length;i++){
    const b=ordered[i];
    if(b.type==='list_item'){
      const items=[{html:b.html,level:b.level,value:b.listOrdinal,style:b.paragraphStyle||{},sourceParagraph:b.sourceParagraph}],type=b.listType,page=b.page,first=b;
      while(i+1<ordered.length&&ordered[i+1].type==='list_item'&&ordered[i+1].listType===type&&ordered[i+1].numId===b.numId&&ordered[i+1].numFmt===b.numFmt&&!ordered[i+1].breakBefore){const n=ordered[++i];items.push({html:n.html,level:n.level,value:n.listOrdinal,style:n.paragraphStyle||{},sourceParagraph:n.sourceParagraph})}
      compact.push({type:'list',id:b.id||'',listType:type,numId:b.numId,numFmt:b.numFmt,listText:b.listText,start:Number(b.listOrdinal)||1,items,paragraphStyle:b.paragraphStyle||{},hyphenate:b.hyphenate!==false,sourceStyle:b.sourceStyle||'',sourceParagraph:b.sourceParagraph,page,breakBefore:b.breakBefore});continue;
    }
    if(b.type==='figure'&&ordered[i+1]?.type==='paragraph'){
      const txt=stripHtml(ordered[i+1].html||'');
      if(/^(Εικόνα|Γράφημα|Σχήμα)\s*\d*/i.test(txt)){b.caption=txt;i++}
    }
    compact.push(b);
  }
  const pages=new Map();for(const b of compact){if(!pages.has(b.page))pages.set(b.page,[]);pages.get(b.page).push(b)}
  const imageInfo=await finalizeImages(pages,zip);
  const pageCount=Math.max(ctx.page,...pages.keys(),1),txbxRaw=q(doc,NS.w,'txbxContent').length,drawings=q(doc,NS.w,'drawing').length+q(doc,NS.w,'pict').length;
  const headingPalette={};for(const h of headings)if(!headingPalette['h'+h.level])headingPalette['h'+h.level]=h.headingStyle||{};
  return{fileName:file.name,pageCount,pages,orderedBlocks:compact,headings,headingPalette,documentLayout:layout,documentSettings:settings,paras,lists,tables,mathCount:ctx.mathCount,inlineMath:ctx.inlineMath,displayMath:ctx.displayMath,importedMathObjects:ctx.importedMathObjects,mathDuplicatesSkipped:Math.max(0,ctx.mathCount-ctx.importedMathObjects),usedImages:imageInfo.usedImages,imageBlobs:imageInfo.imageBlobs,rawImageRefs:ctx.rawImageRefs,duplicateImagesRemoved:imageInfo.duplicateImagesRemoved,alternateFallbacksSkipped:ctx.alternateFallbacksSkipped,fallbackImagesSkipped:ctx.fallbackImagesSkipped,textBoxes:txbxRaw,textBoxesUnique,textBoxCaptions,textBoxesImported,textBoxLabelsRetained,drawings,detectedBreaks:ctx.detectedBreaks,reconciledBreaks:ctx.reconciledBreaks,skippedStaticToc:ctx.skippedStaticToc,unsupportedMath:[...unsupportedMath].sort(),zip};
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
function sourceRef(ctx,block){return{kind:ctx.sourceKind||'docx',sourceFile:ctx.fileName,sourcePage:Number(block.page||ctx.page)||1,sourceParagraph:block.sourceParagraph||null}}
function rich(html){return BookModelV4.htmlToRichText(cleanupImportedHtml(html||''))}
function soleDisplayMath(html){const t=document.createElement('template');t.innerHTML=String(html||'');const all=[...t.content.querySelectorAll('math')];if(all.length!==1)return null;const math=all[0],display=math.getAttribute('display')==='block'||math.closest('.math-display');const clone=t.content.cloneNode(true);clone.querySelectorAll('math').forEach(x=>x.remove());if(!display||clone.textContent.trim()||clone.querySelector('img,table,ul,ol'))return null;return new XMLSerializer().serializeToString(math)}
function convertBlock(block,ctx){
  const id=ctx.id(block),sr=sourceRef(ctx,block),hardBreak=block.breakBefore==='explicit'||block.paragraphStyle?.pageBreakBefore;
  const extBase={...(ctx.sourceKind==='web'?{webCanonicalImport:'v1'}:{docxCanonicalImport:'v4.4'}),...(hardBreak?{paginationHardBreakBefore:true}:{})};
  if(block.type==='part_title'||block.type==='section_heading')return BookModelV4.normalizeItem({id,type:block.type,title:headingTitle(block.title||'',block.headingStyle||{},ctx.preserveHeadingColors),level:Number(block.level)||2,style:canonicalTextStyle(block.headingStyle||{},true),nav:{show:true,label:block.title||''},sourceRef:sr,provenance:{sourceHeadingLevel:Number(block.level)||2,sourceHeadingStyle:block.headingStyle||{},sourceStyle:block.sourceStyle||''},extensions:extBase});
  if(block.type==='figure'){
    const name=ctx.imageName(block.srcPath);if(!name)return null;const placement=block.placement||((block.width&&block.width<520)?'float-right':'wide');
    return BookModelV4.normalizeItem({id,type:'figure',src:'images/'+name,alt:stripHtml(block.caption||'')||block.textLabels?.map(x=>x.text||stripHtml(x.html||'')).join(' ')||'Εικόνα από '+ctx.fileName,title:'',caption:block.caption||'',hideCaption:!block.caption,layout:{placement,widthPx:block.width||undefined,heightPx:block.height||undefined,aspectRatio:'natural',wrap:String(placement).startsWith('float-'),floatInteraction:'wrap'},sourceRef:sr,provenance:{sourceImagePath:block.srcPath,sourceKind:block.kind||'',sourceParagraph:block.sourceParagraph||null},extensions:{...extBase,docxTextLabels:block.textLabels||[]}});
  }
  if(block.type==='textbox'){
    const narrow=block.floating||Number(block.width||0)>0&&Number(block.width)<500,type=narrow?'side_note':'note';
    return BookModelV4.normalizeItem({id,type,label:block.label||'',title:type==='side_note'?(block.label||''):'',body:rich(block.html||esc(block.text||'')),style:canonicalTextStyle(block.paragraphStyle||{},true),layout:type==='side_note'?{placement:block.placement==='float-left'?'left':'right',widthPx:block.width||340,floatInteraction:'avoid'}:undefined,sourceRef:sr,provenance:{sourceStyle:'DOCX Text Box',sourceParagraph:block.sourceParagraph||null},extensions:{...extBase,docxTextBox:true,docxSourceHtml:block.html||''}});
  }
  if(block.type==='list'){
    return BookModelV4.normalizeItem({id,type:'list',ordered:block.listType==='ol',start:Number(block.start)||1,items:(block.items||[]).map(e=>({level:Number(e.level)||0,value:Number.isFinite(Number(e.value))?Number(e.value):undefined,body:rich(e.html||''),style:canonicalListTextStyle(e.style||{},block.hyphenate!==false)})),style:{...canonicalListTextStyle(block.paragraphStyle||{},block.hyphenate!==false),listStylePosition:'outside',...(block.listType==='ol'?{listStyleType:cssListStyle(block.numFmt)}:{})},sourceRef:sr,provenance:{sourceStyle:block.sourceStyle||'list',sourceNumId:block.numId||'',sourceNumFmt:block.numFmt||'',sourceLvlText:block.listText||''},extensions:extBase});
  }
  if(block.type==='table'){
    const rows=(block.rows||[]).map(r=>({cells:(r.cells||[]).map(c=>({colspan:Number(c.colspan)||1,rowspan:Number(c.rowspan)||1,body:rich(c.html||''),style:{...canonicalTextStyle(c.paragraphStyle||{},true),...(c.style||{})}}))}));
    return BookModelV4.normalizeItem({id,type:'table',columns:Number(block.columns)||1,rows,style:{columnWidthsPx:block.tableStyle?.columnWidthsPx||[],headerRows:Number(block.tableStyle?.headerRows)||0,keepTogether:false,marginBottomPx:4},sourceRef:sr,provenance:{sourceStyle:'table'},extensions:extBase});
  }
  if(block.type==='paragraph'){
    const html=formattedParagraphHtml(block),mathml=soleDisplayMath(html);
    if(mathml)return BookModelV4.normalizeItem({id,type:'equation',source:'',mathml,caption:'',style:{align:'center',...canonicalTextStyle(block.paragraphStyle||{},block.hyphenate!==false)},sourceRef:sr,provenance:{sourceFormat:'OMML→MathML',sourceStyle:block.sourceStyle||''},extensions:{...extBase,mathSourceFormat:'omml-mathml'}});
    return BookModelV4.normalizeItem({id,type:'paragraph',body:rich(html),style:canonicalTextStyle(block.paragraphStyle||{},block.hyphenate!==false),sourceRef:sr,provenance:{sourceStyle:block.sourceStyle||'',sourceParagraphStyle:block.paragraphStyle||{},sourceHyphenation:block.hyphenate!==false},extensions:{...extBase,docxSourceHtml:String(block.html||''),preferRichText:true,richTextSync:'canonical-primary-v4.4'}});
  }
  return null;
}
function tocBody(headings){const nodes=[];if(!headings.length)return BookModelV4.createRichText([BookModelV4.createTextRun('Δεν βρέθηκαν επικεφαλίδες.')]);const min=Math.min(...headings.map(h=>h.level));for(const h of headings){const indent=' '.repeat(Math.max(0,h.level-min)*4);nodes.push({type:'link',href:'#'+h.id,children:[BookModelV4.createTextRun(indent+h.text,{bold:h.level===min})]},{type:'line_break'})}if(nodes.at(-1)?.type==='line_break')nodes.pop();return BookModelV4.createRichText(nodes)}
function applyContextualSpacing(items){for(let i=0;i<items.length-1;i++){const a=items[i],b=items[i+1],sa=a?.provenance?.sourceStyle,sb=b?.provenance?.sourceStyle;if(sa&&sa===sb&&a?.style?.contextualSpacing&&b?.style?.contextualSpacing)a.style.marginBottomPx=0}return items}
function makeBook(result,entries,id,title,options={}){
  if(!entries?.length)throw Error('Δεν έχει επιλεγεί περιεχόμενο DOCX.');
  const preserve=options.preserveHeadingColors!==false,depth=Number(options.tocDepth||3),generateToc=options.generateToc!==false,usedImages=new Map(),usedIds=new Set();
  const imageName=path=>{if(!path)return'';if(!usedImages.has(path)){let base=path.split('/').pop().replace(/[^A-Za-z0-9._-]+/g,'_')||'image.bin',name=base,n=2;const names=new Set(usedImages.values());while(names.has(name))name=(n++)+'_'+base;usedImages.set(path,name)}return usedImages.get(path)};
  const unique=base=>BookModelV4.uniqueId(base,usedIds);let itemCounter=0;const pages=[];
  if(generateToc){const hs=headingsForEntries(result,entries,depth);pages.push(BookModelV4.createPage({id:unique('contents-flow'),header:{inherit:false,left:'',center:'',right:title},footer:{inherit:false,left:'© antarxo 2026',center:'',right:''},pageNumbering:{inherit:false,enabled:true,offset:0,hide:true},extensions:{paginationSection:'toc'},items:[BookModelV4.normalizeItem({id:unique('contents-title'),type:'part_title',label:'',title:'Περιεχόμενα',nav:{show:false,label:'Περιεχόμενα'},style:{keepWithNext:true},sourceRef:{kind:'generated-toc',sourceFile:result.fileName}}),BookModelV4.normalizeItem({id:unique('contents-body'),type:'paragraph',body:tocBody(hs),style:{align:'left',hyphenate:false,fontFamily:result.documentLayout?.source?.bodyFontFamily||'Calibri',fontSizePx:result.documentLayout?.source?.bodyFontSize||14.6667,lineHeight:1.15},sourceRef:{kind:'generated-toc',sourceFile:result.fileName},extensions:{generatedActiveToc:true}})]}))}
  const items=[],ctx={fileName:result.fileName,page:entries[0]?.page||1,sourceKind:result.sourceType||'docx',preserveHeadingColors:preserve,imageName,id:block=>block.id?unique(block.id):unique((result.sourceType==='web'?'web-item-':'docx-item-')+(++itemCounter))};
  for(const e of entries){const block={...e.block,page:e.page};const it=convertBlock(block,ctx);if(it)items.push(it)}
  applyContextualSpacing(items);
  pages.push(BookModelV4.createPage({id:unique('document-flow'),sourcePage:`${entries[0]?.page||1}-${entries.at(-1)?.page||1}`,header:{inherit:false,left:'',center:'',right:title},footer:{inherit:false,left:'© antarxo 2026',center:'',right:'{page}'},pageNumbering:{inherit:false,enabled:true,offset:0,hide:false},extensions:{paginationSection:'document-flow'},items}));
  const headingIndex=headingsForEntries(result,entries,9).map(h=>({id:h.id,title:h.text,level:h.level,sourcePage:h.page,sourceParagraph:h.sourceParagraph}));
  const importedLayout={...BookModelV4.DEFAULT_LAYOUT,...(result.documentLayout?.layoutDefaults||{})};
  const book=BookModelV4.createBook({meta:{projectId:id,fileName:'/'+id+'/book.json',title,subtitle:'Εισαγωγή από DOCX',defaultLanguage:'el',authoringVersion:CONVERTER,createdAt:new Date().toISOString(),updatedAt:new Date().toISOString()},layoutDefaults:importedLayout,pageDefaults:{header:{inherit:false,left:'',center:'',right:title},footer:{inherit:false,left:'© antarxo 2026',center:'',right:'{page}'},pageNumbering:{enabled:true,startAt:1,position:'footer-right',hideOnFirstPage:false}},pages,nav:{mode:'auto',showApp:false,showPrint:true,groups:[]},importManifest:{version:1,sourceType:'docx',sourceFile:result.fileName,createdAt:new Date().toISOString(),selection:{startBlock:entries[0]?.key||'',endBlock:entries.at(-1)?.key||'',startPage:entries[0]?.page||1,endPage:entries.at(-1)?.page||1},headingIndex,mergeReady:true,converter:CONVERTER,metrics:{sourcePages:result.pageCount,paragraphs:result.paras,listParagraphs:result.lists,tables:result.tables,mathDetectedRaw:result.mathCount,mathImportedCanonical:result.importedMathObjects,mathDuplicatesSkipped:result.mathDuplicatesSkipped,inlineMath:result.inlineMath,displayMath:result.displayMath,images:result.usedImages.length,duplicateImagesRemoved:result.duplicateImagesRemoved,textBoxesRaw:result.textBoxes,textBoxesUnique:result.textBoxesUnique,textBoxCaptions:result.textBoxCaptions,textBoxesImported:result.textBoxesImported,textBoxLabelsRetained:result.textBoxLabelsRetained}},extensions:{docxHeadingPalette:result.headingPalette||{},docxDocumentSettings:result.documentSettings||{},docxDocumentLayout:result.documentLayout?.source||{}}});
  const normalized=BookModelV4.normalizeBook(book,{assignIds:true}).book,validation=BookModelV4.validateBook(normalized);if(!validation.ok)throw Error('Το canonical v4 βιβλίο δεν πέρασε validation: '+validation.errors.join('; '));return{book:normalized,imageMap:usedImages,validation};
}
function bookItemIds(book){const ids=new Set();for(const page of book?.pages||[]){if(page.id)ids.add(page.id);for(const item of page.items||[])if(item.id)ids.add(item.id)}return ids}
function usedImageNames(book){const names=new Set();for(const page of book?.pages||[])for(const item of page.items||[])if(item.type==='figure'&&String(item.src||'').startsWith('images/'))names.add(String(item.src).slice(7));return names}
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
  const inserted=[];for(const entry of entries){const converted=convertBlock({...entry.block,page:entry.page},ctx);if(converted){converted.sourceRef={...(converted.sourceRef||{}),insertionId};inserted.push(converted)}}
  applyContextualSpacing(inserted);if(!inserted.length)throw Error('Η επιλεγμένη περιοχή δεν παρήγαγε canonical blocks.');
  const insertAt=anchorIndex+(position==='after'?1:0);targetPage.items.splice(insertAt,0,...inserted);
  const evidence=duplicateEvidence(inputBook,result.fileName,entries);
  return{book,pageIndex,insertionId,sourceKind,entries:deepClone(entries),anchor:{pageId:targetPage.id,itemId:anchor.itemId,label:anchor.label||BookModelV4.summarizeItem(targetPage.items[anchorIndex])},position,insertedIds:inserted.map(item=>item.id),firstInsertedId:inserted[0].id,imageMap,duplicateEvidence:evidence};
}
function finalizeInsertionManifest(draft,meta={}){
  const book=draft.book;book.importManifest=book.importManifest&&typeof book.importManifest==='object'?book.importManifest:{version:1,sourceType:'mixed'};
  book.importManifest.insertions=Array.isArray(book.importManifest.insertions)?book.importManifest.insertions:[];
  book.importManifest.insertions.push({id:draft.insertionId,createdAt:new Date().toISOString(),sourceType:draft.sourceKind||'docx',sourceFile:meta.sourceFile||'',selection:{startPage:draft.entries[0]?.page||1,endPage:draft.entries.at(-1)?.page||1,startBlock:draft.entries[0]?.key||'',endBlock:draft.entries.at(-1)?.key||'',firstLabel:entryLabel(draft.entries[0]),lastLabel:entryLabel(draft.entries.at(-1))},target:{pageId:draft.anchor.pageId,itemId:draft.anchor.itemId,label:draft.anchor.label||'',position:draft.position},itemsInserted:draft.insertedIds.length,pagesAfterLocalPagination:draft.generatedPageIds?.length||1,imagesAdded:Number(meta.imagesAdded)||0,backup:meta.backupPath||'',tool:draft.sourceKind==='web'?'bookwriter-web-v1':CONVERTER});
  book.meta={...(book.meta||{}),...(draft.sourceKind==='web'?{lastWebInsertionVersion:'bookwriter-web-v1'}:{lastDocxInsertionVersion:CONVERTER}),updatedAt:new Date().toISOString()};
  return book;
}
function audit(result,entries){return{sourceFile:result.fileName,totalSourcePages:result.pageCount,selectedBlocks:entries.length,selectedPages:entries.length?{start:entries[0].page,end:entries.at(-1).page}:null,paragraphs:result.paras,listParagraphs:result.lists,tables:result.tables,mathObjectsDetectedRaw:result.mathCount,mathObjectsImportedCanonical:result.importedMathObjects,mathDuplicatesSkipped:result.mathDuplicatesSkipped,inlineMath:result.inlineMath,displayMath:result.displayMath,rawImageRefs:result.rawImageRefs,imagesAfterDedupe:result.usedImages.length,duplicateImagesRemoved:result.duplicateImagesRemoved,fallbackImagesSkipped:result.fallbackImagesSkipped,textBoxesRaw:result.textBoxes,textBoxesUnique:result.textBoxesUnique,textBoxCaptions:result.textBoxCaptions,textBoxesImported:result.textBoxesImported,textBoxLabelsRetained:result.textBoxLabelsRetained,drawings:result.drawings,reconciledPageBreaks:result.reconciledBreaks,skippedStaticTocParagraphs:result.skippedStaticToc,unsupportedMath:result.unsupportedMath,documentLayout:result.documentLayout?.source||{},canonicalTarget:'bookwriter-v4',converter:CONVERTER}}
function launcher(title,id,editor=false){if(editor)return`<!doctype html><html lang="el"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Επεξεργασία — ${esc(title)}</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;font:16px system-ui}</style></head><body><p>Άνοιξε τον ΣΥΓΓΡΑΦΕΑ και επίλεξε τον φάκελο του βιβλίου <b>${esc(id)}</b>.</p><p><a href="../../author/index.html">Άνοιγμα ΣΥΓΓΡΑΦΕΑ</a></p></body></html>`;return`<!doctype html><html lang="el"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(title)}</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;font:16px system-ui}</style></head><body><p>Άνοιγμα… <a id="a">συνέχεια</a></p><script>const t=new URL('../../reader/index.html',location.href);t.searchParams.set('book','../books/${id}/book.json');document.getElementById('a').href=t;location.replace(t);<\/script></body></html>`}
global.DocxCoreV4=Object.freeze({VERSION:'4.5.0-rc1-docx',parseDocx,flattenEntries,entriesInRange,entryLabel,blockText,formattedParagraphHtml,makeBook,buildInsertionDraft,finalizeInsertionManifest,duplicateEvidence,audit,launcher,safeId,deepClone,canonicalTextStyle});
})(window);
