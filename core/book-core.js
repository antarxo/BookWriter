(function(global){
  'use strict';

  const VERSION = '0.3.0-rc1-render-parity';
  const V4_SCHEMA = 'bookwriter-v4';
  const LEGACY_SCHEMA = 'pages-v1';
  const SUPPORTED_SCHEMAS = Object.freeze([LEGACY_SCHEMA,V4_SCHEMA]);
  const DEFAULT_LAYOUT = Object.freeze({
    pageSize:'A4',
    orientation:'portrait',
    pageWidthPx:794,
    pageHeightPx:1123,
    pagePaddingTopPx:54,
    pagePaddingRightPx:44,
    pagePaddingBottomPx:54,
    pagePaddingLeftPx:44,
    headerTopPx:20,
    headerHeightPx:16,
    footerBottomPx:18,
    footerHeightPx:16,
    headerFontSize:0,
    footerFontSize:0,
    bodyFontSize:18,
    lineHeight:1.3,
    paragraphGap:6,
    sectionGap:14,
    noteGap:8,
    calloutGap:7,
    bodyFontFamily:'serif',
    headingFontFamily:'serif',
    heroEyebrowFontSize:13,
    heroTitleFontSize:28,
    heroSubtitleFontSize:16,
    heroTitleFontFamily:'serif',
    partKickerFontSize:10,
    partTitleFontSize:24,
    partTitleFontFamily:'classic',
    sectionHeadingFontSize:18,
    sectionHeadingFontFamily:'serif',
    noteFontSize:14,
    noteLineHeight:1.42,
    noteLabelFontSize:10.5,
    captionFontSize:12,
    calloutFontSize:11.5,
    calloutTitleFontSize:10.5,
    calloutLabelFontSize:10,
    calloutChipFontSize:10,
    calloutObserveTitleFontSize:10,
    showPageNumbers:true
  });

  const TEXT_KEYS = new Set([
    'eyebrow','title','subtitle','label','html','left','center','right',
    'alt','caption','setupLabel','pressLabel','observeTitle','navLabel'
  ]);
  const STRUCTURAL_TYPES = new Set([
    'hero','part_title','section_heading','note','side_note',
    'figure','scene','nav_anchor'
  ]);
  const FONT_PRESETS = Object.freeze({
    serif:'Georgia,"Noto Serif","Times New Roman",serif',
    sans:'system-ui,-apple-system,"Segoe UI",Arial,sans-serif',
    classic:'"Avenir Next","Segoe UI",system-ui,-apple-system,Arial,sans-serif'
  });

  function deepClone(value){
    return JSON.parse(JSON.stringify(value == null ? {} : value));
  }

  function normalizeData(raw){
    const out = deepClone(raw);
    const schema = String(out?.schemaVersion || LEGACY_SCHEMA);
    if(!SUPPORTED_SCHEMAS.includes(schema)) out.schemaVersion = schema;
    else out.schemaVersion = schema;
    if(!out.meta || typeof out.meta !== 'object') out.meta = {};
    if(!out.layoutDefaults || typeof out.layoutDefaults !== 'object') out.layoutDefaults = {};
    out.layoutDefaults = Object.assign({}, DEFAULT_LAYOUT, out.layoutDefaults);
    if(!out.nav || typeof out.nav !== 'object') out.nav = {};
    if(!out.nav.mode) out.nav.mode = 'auto';
    if(out.nav.showApp == null) out.nav.showApp = true;
    if(out.nav.showPrint == null) out.nav.showPrint = true;
    if(!Array.isArray(out.nav.groups)) out.nav.groups = [];
    if(schema === V4_SCHEMA){
      if(!out.pageDefaults || typeof out.pageDefaults !== 'object') out.pageDefaults = {};
      if(!out.pageDefaults.header || typeof out.pageDefaults.header !== 'object') out.pageDefaults.header = {inherit:false,left:'',center:'',right:''};
      if(!out.pageDefaults.footer || typeof out.pageDefaults.footer !== 'object') out.pageDefaults.footer = {inherit:false,left:'',center:'',right:'{page}'};
      if(!out.pageDefaults.pageNumbering || typeof out.pageDefaults.pageNumbering !== 'object') out.pageDefaults.pageNumbering = {enabled:true,startAt:1,position:'footer-right',hideOnFirstPage:false};
    }
    if(!Array.isArray(out.pages)) out.pages = [];
    out.pages.forEach((page, index)=>{
      if(!page || typeof page !== 'object') out.pages[index] = page = {};
      if(!page.id) page.id = `page-${index+1}`;
      if(schema === V4_SCHEMA){
        if(!page.header || typeof page.header !== 'object') page.header = {inherit:true,left:'',center:'',right:''};
        if(!page.footer || typeof page.footer !== 'object') page.footer = {inherit:true,left:'',center:'',right:''};
        if(!page.pageNumbering || typeof page.pageNumbering !== 'object') page.pageNumbering = {inherit:true,enabled:true,offset:0,hide:false};
      }else{
        if(!page.header || typeof page.header !== 'object') page.header = {left:'',center:'',right:''};
        if(!page.footer || typeof page.footer !== 'object') page.footer = {left:'',center:'',right:'{page}'};
      }
      if(!Array.isArray(page.items)) page.items = [];
    });
    return out;
  }

  function validateData(raw){
    const errors = [];
    const warnings = [];
    if(!raw || typeof raw !== 'object'){
      errors.push('Το αρχείο δεν περιέχει αντικείμενο JSON.');
      return {ok:false, errors, warnings, schema:null};
    }
    const schema = String(raw.schemaVersion || LEGACY_SCHEMA);
    if(!SUPPORTED_SCHEMAS.includes(schema)) warnings.push(`Άγνωστη έκδοση σχήματος: ${schema}.`);
    if(!Array.isArray(raw.pages)){
      errors.push('Λείπει ο πίνακας pages.');
      return {ok:false, errors, warnings, schema};
    }
    const pageIds = new Set();
    const itemIds = new Set();
    raw.pages.forEach((page, pageIndex)=>{
      const pageName = page?.id || `σελίδα ${pageIndex+1}`;
      if(page?.id && pageIds.has(page.id)) errors.push(`Διπλό id σελίδας: ${page.id}.`);
      if(page?.id) pageIds.add(page.id);
      if(!Array.isArray(page?.items)){
        errors.push(`Η ${pageName} δεν έχει πίνακα items.`);
        return;
      }
      page.items.forEach((item, itemIndex)=>{
        if(!item || typeof item !== 'object'){
          errors.push(`Άκυρο στοιχείο στη ${pageName}, θέση ${itemIndex+1}.`);
          return;
        }
        if(!item.type) errors.push(`Λείπει type στη ${pageName}, θέση ${itemIndex+1}.`);
        if(item.id){
          if(itemIds.has(item.id)) warnings.push(`Το id στοιχείου επαναλαμβάνεται: ${item.id}.`);
          itemIds.add(item.id);
        }
        const sceneSrc = schema === V4_SCHEMA ? item.src : item.singleSrc;
        if(item.type === 'scene' && !sceneSrc) warnings.push(`Σκηνή χωρίς URL στη ${pageName}, θέση ${itemIndex+1}.`);
        if(item.type === 'figure' && !item.src) warnings.push(`Εικόνα χωρίς αρχείο στη ${pageName}, θέση ${itemIndex+1}.`);
      });
    });
    return {ok:errors.length === 0, errors, warnings, schema};
  }

  function locKey(key, lang){
    return lang === 'en' ? `${key}_en` : key;
  }

  function getLoc(obj, key, fallback='', lang='el'){
    if(!obj) return fallback;
    const localized = obj[locKey(key, lang)];
    if(localized != null && localized !== '') return localized;
    const base = obj[key];
    return base != null ? base : fallback;
  }

  function getLocArray(obj, key, lang='el'){
    if(!obj) return [];
    const localized = obj[locKey(key, lang)];
    if(Array.isArray(localized)) return localized;
    return Array.isArray(obj[key]) ? obj[key] : [];
  }

  function isV4Data(data){
    return String(data?.schemaVersion || '') === V4_SCHEMA;
  }

  function escapeHtml(value=''){
    return String(value ?? '').replace(/[&<>"']/g, ch=>({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    })[ch]);
  }

  function mathSourceToHtml(source='',display='inline'){
    const raw = String(source || '').trim();
    if(!raw) return '';
    try{
      const parser = global.MathExpressionV4;
      if(!parser?.sourceToMathML) throw Error('MathExpressionV4 is not loaded');
      let mathml = parser.sourceToMathML(raw);
      if(display === 'block') mathml = mathml.replace('<math', '<math display="block"');
      return `<span class="${display === 'block' ? 'math-block' : 'math-inline'}" data-math-source="${escapeHtml(raw)}">${mathml}</span>`;
    }catch(error){
      const wrapped = display === 'block' ? `\\[${raw}\\]` : `\\(${raw}\\)`;
      return `<code class="math-source-error" title="${escapeHtml(error.message)}">${escapeHtml(wrapped)}</code>`;
    }
  }

  function textWithMath(value=''){
    const text = String(value ?? '');
    if(!/[\\$]/.test(text)) return escapeHtml(text);
    const out = [];
    let i = 0;
    while(i < text.length){
      const inline = text.indexOf('\\(', i);
      const block = text.indexOf('\\[', i);
      const dollar = text.indexOf('$$', i);
      const candidates = [
        inline >= 0 ? {start:inline, open:'\\(', close:'\\)', display:'inline'} : null,
        block >= 0 ? {start:block, open:'\\[', close:'\\]', display:'block'} : null,
        dollar >= 0 ? {start:dollar, open:'$$', close:'$$', display:'block'} : null
      ].filter(Boolean).sort((a,b)=>a.start-b.start);
      const hit = candidates[0];
      if(!hit){ out.push(escapeHtml(text.slice(i))); break; }
      out.push(escapeHtml(text.slice(i, hit.start)));
      const contentStart = hit.start + hit.open.length;
      const end = text.indexOf(hit.close, contentStart);
      if(end < 0){
        out.push(escapeHtml(text.slice(hit.start)));
        break;
      }
      out.push(mathSourceToHtml(text.slice(contentStart, end), hit.display));
      i = end + hit.close.length;
    }
    return out.join('');
  }

  function attrJson(value){
    return escapeHtml(JSON.stringify(value == null ? {} : value));
  }

  function sequenceSteps(item){
    return Array.isArray(item?.sequenceSteps) ? item.sequenceSteps.filter(step=>step && typeof step === 'object') : [];
  }

  function queryValue(value){
    if(value === true) return '1';
    if(value === false) return '0';
    return String(value ?? '');
  }

  function sequenceSceneUrl(source='', step={}){
    const raw = String(source || '').trim();
    if(!raw) return '';
    let url;
    try{ url = new URL(raw, global.location?.href || 'http://bookwriter.local/'); }
    catch(_e){ return raw; }
    const preset = step.printPreset ?? step.preset;
    if(preset != null && String(preset).trim()) url.searchParams.set('state', String(preset));
    const state = step.state && typeof step.state === 'object' ? step.state : {};
    for(const [key,value] of Object.entries(state)) url.searchParams.set(key, queryValue(value));
    const printQuery = step.printQuery && typeof step.printQuery === 'object' ? step.printQuery : {};
    for(const [key,value] of Object.entries(printQuery)) url.searchParams.set(key, queryValue(value));
    return url.href;
  }

  function calloutForSequenceStep(item, step={}, index=0){
    const out = deepClone(item || {});
    out.id = `${item?.id || 'callout'}-step-${index+1}`;
    out.type = 'interactive_callout';
    out.title = step.title || item?.title || out.title || '';
    out.setupLabel = step.setupLabel || item?.setupLabel || out.setupLabel || '';
    out.setupChips = Array.isArray(step.setupChips) ? deepClone(step.setupChips) : deepClone(item?.setupChips || []);
    out.pressLabel = step.pressLabel || item?.pressLabel || out.pressLabel || '';
    out.pressChips = Array.isArray(step.pressChips) ? deepClone(step.pressChips) : deepClone(item?.pressChips || []);
    out.observeTitle = step.observeTitle || item?.observeTitle || out.observeTitle || '';
    out.observeItems = Array.isArray(step.observeItems) ? deepClone(step.observeItems) : deepClone(item?.observeItems || []);
    out.conclusionTitle = step.conclusionTitle || item?.conclusionTitle || out.conclusionTitle || '';
    out.conclusionItems = Array.isArray(step.conclusionItems) ? deepClone(step.conclusionItems) : deepClone(item?.conclusionItems || []);
    out.extras = step.extras && typeof step.extras === 'object' ? deepClone(step.extras) : deepClone(item?.extras || null);
    out.sequenceSteps = [];
    out.extensions = Object.assign({}, out.extensions || {}, {sequenceStep:index+1, sourceCalloutId:item?.id || '', sequenceStepData:deepClone(step)});
    return out;
  }

  function calloutExtrasHtml(extras={}, lang='el'){
    if(!extras || typeof extras !== 'object') return '';
    const items = Array.isArray(extras.items) ? extras.items.filter(entry=>entry && typeof entry === 'object' && String(entry.text || '').trim()) : [];
    if(!items.length) return '';
    const title = String(extras.title || (lang === 'en' ? 'More' : 'Πρόσθετο υλικό'));
    const collapsed = extras.collapsedInBook !== false;
    const body = items.map(entry=>{
      const type = String(entry.type || 'question');
      const label = entry.label || (type === 'exercise' ? (lang === 'en' ? 'Exercise' : 'Άσκηση') : type === 'prompt' ? (lang === 'en' ? 'Think' : 'Σκέψου') : (lang === 'en' ? 'Question' : 'Ερώτηση'));
      const lines = Math.max(0, Math.min(12, Number(entry.answerLines) || 0));
      const answerText = String(entry.answer || '').trim();
      const showAnswer = lang === 'en' ? 'Show answer' : 'Εμφάνιση απάντησης';
      const hideAnswer = lang === 'en' ? 'Hide answer' : 'Απόκρυψη απάντησης';
      const answerHtml = answerText ? `<button type="button" class="callout-extra-answer-toggle" data-callout-answer-toggle data-show-label="${escapeHtml(showAnswer)}" data-hide-label="${escapeHtml(hideAnswer)}">${escapeHtml(showAnswer)}</button><div class="callout-extra-answer" hidden>${textWithMath(answerText).replace(/\n/g,'<br>')}</div>` : '';
      const linesHtml = lines ? `<div class="callout-extra-lines" aria-hidden="true">${Array.from({length:lines},()=>'<span></span>').join('')}</div>` : '';
      return `<div class="callout-extra-item"><b>${textWithMath(label)}</b><p>${textWithMath(entry.text)}</p>${answerHtml}${linesHtml}</div>`;
    }).join('');
    return `<div class="callout-extras${collapsed?' collapsed':''}" data-callout-extras data-print="${extras.print === false ? '0' : '1'}"><button type="button" class="callout-extras-toggle" data-callout-extras-toggle>${escapeHtml(title)}</button><div class="callout-extras-body" ${collapsed?'hidden':''}>${body}</div></div>`;
  }

  function expandScreenSequences(data, options={}){
    if(options.enabled === false) return deepClone(data);
    const book = normalizeData(deepClone(data));
    let changed = false;
    book.pages = (book.pages || []).map((page,pageIndex)=>{
      const items = [];
      (page.items || []).forEach((item,itemIndex)=>{
        const steps = sequenceSteps(item);
        if(steps.length){
          changed = true;
          items.push(item);
          return;
        }
        items.push(item);
      });
      return Object.assign({}, page, {
        id:page.id || `page-${pageIndex+1}`,
        items,
        extensions:Object.assign({}, page.extensions || {}, changed ? {screenSequenceExpansion:true} : {})
      });
    });
    book.extensions = Object.assign({}, book.extensions || {}, {screenSequenceExpansion:{enabled:changed}});
    return book;
  }

  function expandPrintSequences(data, options={}){
    if(options.enabled === false) return deepClone(data);
    const book = normalizeData(deepClone(data));
    const sceneById = new Map();
    (book.pages || []).forEach(page => (page.items || []).forEach(item => {
      if(item?.type === 'scene' && item.id) sceneById.set(String(item.id), item);
    }));
    const expanded = [];
    let changed = false;
    let generatedCount = 0;
    (book.pages || []).forEach((page,pageIndex)=>{
      const generated = [];
      (page.items || []).forEach((item,itemIndex)=>{
        const steps = sequenceSteps(item);
        const sceneId = String(item?.sequenceSceneId || '').trim();
        const scene = sceneId ? sceneById.get(sceneId) : null;
        if(!steps.length || !scene || item?.print?.expand === 'none') return;
        steps.forEach((step,stepIndex)=>{
          const callout = calloutForSequenceStep(item, step, stepIndex);
          callout.extensions = Object.assign({}, callout.extensions || {}, {sequencePrintGenerated:true});
          const sceneCopy = deepClone(scene);
          sceneCopy.id = `${scene.id}-step-${stepIndex+1}`;
          sceneCopy.src = sequenceSceneUrl(scene.src ?? scene.singleSrc ?? '', step);
          sceneCopy.title = step.title || scene.title || '';
          sceneCopy.extensions = Object.assign({}, sceneCopy.extensions || {}, {sequenceSourceSceneId:scene.id, sequenceStep:stepIndex+1});
          generated.push(Object.assign({}, deepClone(page), {
            id:`${page.id || `page-${pageIndex+1}`}-${item.id || `sequence-${itemIndex+1}`}-print-${stepIndex+1}`,
            items:[callout, sceneCopy],
            extensions:Object.assign({}, page.extensions || {}, {printGeneratedSequence:true, sourcePageId:page.id || '', sourceCalloutId:item.id || '', sourceSceneId:scene.id || '', sequenceStep:stepIndex+1})
          }));
        });
      });
      if(generated.length){
        changed = true;
        generatedCount += generated.length;
        expanded.push(...generated);
      }else{
        expanded.push(page);
      }
    });
    book.pages = expanded;
    book.extensions = Object.assign({}, book.extensions || {}, {printSequenceExpansion:{enabled:changed, generatedPages:generatedCount, totalPages:expanded.length, generatedAt:new Date().toISOString()}});
    return book;
  }

  function renderTextRun(run){
    let html = escapeHtml(run?.text ?? '');
    if(run?.bold) html = `<strong>${html}</strong>`;
    if(run?.italic) html = `<em>${html}</em>`;
    if(run?.underline) html = `<u>${html}</u>`;
    if(run?.superscript) html = `<sup>${html}</sup>`;
    if(run?.subscript) html = `<sub>${html}</sub>`;
    const styles=[];
    if(run?.color) styles.push(`color:${String(run.color)}`);
    if(run?.highlight) styles.push(`background-color:${String(run.highlight)}`);
    if(styles.length) html = `<span style="${escapeHtml(styles.join(';'))}">${html}</span>`;
    return html;
  }

  function richTextToHtml(rich){
    const renderNodes = nodes => (nodes || []).map(node=>{
      if(!node || typeof node !== 'object') return escapeHtml(node ?? '');
      switch(node.type){
        case 'text_run': return renderTextRun(node);
        case 'line_break': return '<br>';
        case 'math_inline': return node.mathml || `<span class="math-inline-source">${escapeHtml(node.source || '')}</span>`;
        case 'link': {
          const href=escapeHtml(node.href || '');
          const target=node.target ? ` target="${escapeHtml(node.target)}"` : '';
          return `<a href="${href}"${target}>${renderNodes(node.children)}</a>`;
        }
        default: return '';
      }
    }).join('');
    return renderNodes(rich?.nodes);
  }

  function itemNavVisible(item){
    if(item?.nav && typeof item.nav === 'object') return item.nav.show !== false;
    return item?.showInNav !== false;
  }

  function itemLayout(item){
    const layout = item?.layout && typeof item.layout === 'object' ? item.layout : {};
    const media=item?.type==='figure'||item?.type==='scene';
    const placement=String(layout.placement ?? item?.placement ?? (media?'float-right':'wide'));
    return {
      placement,
      widthPx:layout.widthPx ?? item?.frameWidth,
      heightPx:layout.heightPx ?? item?.frameHeight ?? item?.figureHeight ?? item?.sceneHeight,
      aspectRatio:layout.aspectRatio ?? item?.aspectRatio ?? item?.sceneAspect ?? item?.aspect,
      wrap:!!(layout.wrap ?? item?.wrapTight ?? (media||placement.startsWith('float-'))),
      floatInteraction:String(layout.floatInteraction ?? item?.floatInteraction ?? '')
    };
  }

  function defaultFloatInteraction(type){
    return type==='clear'?'clear':'wrap';
  }

  function itemBodyHtml(item, options={}){
    if(!item || typeof item !== 'object') return '';
    if(typeof item.html === 'string') return item.html;
    const preferRich = item?.provenance?.contentEdited === true || item?.extensions?.preferRichText === true;
    if(!preferRich && typeof item.legacySourceHtml === 'string') return item.legacySourceHtml;
    const html = richTextToHtml(item.body);
    return html || (options.preview ? '<em>(κενό)</em>' : '');
  }

  function resolvedTriplet(value, defaults){
    const useDefaults = value?.inherit === true;
    const source = useDefaults ? (defaults || {}) : (value || {});
    return {left:String(source.left||''),center:String(source.center||''),right:String(source.right||'')};
  }

  function resolvePageFrame(data,page,pageIndex=0){
    const count = Array.isArray(data?.pages) ? data.pages.length : 1;
    if(!isV4Data(data)){
      return {
        header:{left:getLoc(page?.header,'left',''),center:getLoc(page?.header,'center',''),right:getLoc(page?.header,'right','')},
        footer:{left:getLoc(page?.footer,'left',''),center:getLoc(page?.footer,'center',''),right:getLoc(page?.footer,'right','{page}')},
        pageNumber:pageIndex+1,
        pageCount:count,
        numberingEnabled:data?.layoutDefaults?.showPageNumbers !== false
      };
    }
    const defaults=data?.pageDefaults || {};
    const baseNumbering=defaults.pageNumbering || {};
    const own=page?.pageNumbering || {};
    const numbering=own.inherit === true ? baseNumbering : Object.assign({},baseNumbering,own);
    const startAt=Number(baseNumbering.startAt ?? 1) || 1;
    const number=startAt + pageIndex + (Number(numbering.offset)||0);
    const enabled=data?.layoutDefaults?.showPageNumbers !== false && numbering.enabled !== false && numbering.hide !== true && !(numbering.hideOnFirstPage===true && pageIndex===0);
    const header=resolvedTriplet(page?.header,defaults.header);
    const footer=resolvedTriplet(page?.footer,defaults.footer);
    if(enabled){
      const all=[header.left,header.center,header.right,footer.left,footer.center,footer.right].join(' ');
      if(!all.includes('{page}')){
        const [where,side]=String(numbering.position || baseNumbering.position || 'footer-right').split('-');
        const target=where==='header'?header:footer;
        const key=['left','center','right'].includes(side)?side:'right';
        target[key]=target[key] || '{page}';
      }
    }
    return {header,footer,pageNumber:number,pageCount:count,numberingEnabled:enabled};
  }

  function replaceTokens(value, ctx){
    return String(value || '')
      .replace(/\{page\}/g, String(ctx.page || ''))
      .replace(/\{pages\}/g, String(ctx.pages || ''));
  }

  function slugifyForId(value=''){
    const result = String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
      .toLowerCase().replace(/[^a-z0-9\u0370-\u03ff]+/g,'-')
      .replace(/^-+|-+$/g,'').slice(0,48);
    return result || 'anchor';
  }

  function normalizeDomId(value=''){
    const raw = String(value || '').trim();
    if(!raw) return '';
    const safe = raw.replace(/[^A-Za-z0-9_:\-.\u0370-\u03ff]+/g,'-').replace(/^-+|-+$/g,'');
    return (/^[A-Za-z_\u0370-\u03ff]/.test(safe) ? safe : `a-${safe}`) || '';
  }

  function itemNavTitle(item, fallback='', lang='el'){
    const navLabel = item?.nav && typeof item.nav === 'object' ? item.nav.label : getLoc(item,'navLabel','',lang);
    return String(
      navLabel ||
      getLoc(item,'title','',lang) ||
      getLoc(item,'label','',lang) ||
      getLoc(item,'caption','',lang) ||
      fallback || ''
    ).trim();
  }

  function isStructuralNavItem(item){
    return STRUCTURAL_TYPES.has(item?.type);
  }

  function itemAnchorId(page, pageIndex, item, itemIndex, lang='el'){
    const explicit = normalizeDomId(item?.id || item?.anchorId || item?.targetId || '');
    if(explicit) return explicit;
    if(!isStructuralNavItem(item)) return '';
    const pageId = normalizeDomId(page?.id || `page-${pageIndex+1}`) || `page-${pageIndex+1}`;
    const title = itemNavTitle(item, item?.type || 'item', lang);
    return normalizeDomId(`${pageId}-${itemIndex+1}-${slugifyForId(title || item?.type || 'item')}`);
  }

  function parseAspect(value){
    if(value == null || value === '' || value === 'natural') return null;
    if(typeof value === 'number' && Number.isFinite(value) && value > 0) return value;
    const text = String(value).trim();
    const fraction = text.match(/^(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)$/);
    if(fraction){
      const width = Number(fraction[1]);
      const height = Number(fraction[2]);
      if(width > 0 && height > 0) return width / height;
    }
    const number = Number(text);
    return Number.isFinite(number) && number > 0 ? number : null;
  }

  function mediaAspect(item, kind){
    const layout=itemLayout(item);
    const frameWidth = Math.max(120, Number(layout.widthPx || 340) || 340);
    const explicitHeight = Number(layout.heightPx);
    if(Number.isFinite(explicitHeight) && explicitHeight > 0) return frameWidth / explicitHeight;
    const explicitAspect = parseAspect(layout.aspectRatio ?? (kind === 'scene' ? '16/9' : null));
    if(explicitAspect) return explicitAspect;
    const viewportWidth = Number(item?.viewport?.width);
    const viewportHeight = Number(item?.viewport?.height);
    if(viewportWidth > 0 && viewportHeight > 0) return viewportWidth / viewportHeight;
    return kind === 'scene' ? 16/9 : null;
  }

  function fontValue(value, fallback){
    return FONT_PRESETS[value] || value || fallback;
  }

  function applyLayoutVars(element, layout={}){
    const defs = Object.assign({}, DEFAULT_LAYOUT, layout || {});
    const px = (name, key)=>{
      const value = Number(defs[key]);
      if(Number.isFinite(value)) element.style.setProperty(name, `${value}px`);
    };
    const raw = (name, key)=>{
      if(defs[key] != null && defs[key] !== '') element.style.setProperty(name, String(defs[key]));
    };
    [
      ['--sheet-width','pageWidthPx'],['--sheet-height','pageHeightPx'],
      ['--sheet-pad-top','pagePaddingTopPx'],['--sheet-pad-right','pagePaddingRightPx'],
      ['--sheet-pad-bottom','pagePaddingBottomPx'],['--sheet-pad-left','pagePaddingLeftPx'],
      ['--header-top','headerTopPx'],['--header-h','headerHeightPx'],
      ['--footer-bottom','footerBottomPx'],['--footer-h','footerHeightPx'],
      ['--header-font-size','headerFontSize'],['--footer-font-size','footerFontSize'],
      ['--body-font-size','bodyFontSize'],['--para-gap','paragraphGap'],
      ['--section-gap','sectionGap'],['--note-gap','noteGap'],['--callout-gap','calloutGap'],
      ['--hero-eyebrow-font-size','heroEyebrowFontSize'],['--hero-title-font-size','heroTitleFontSize'],
      ['--hero-subtitle-font-size','heroSubtitleFontSize'],['--part-kicker-font-size','partKickerFontSize'],
      ['--part-title-font-size','partTitleFontSize'],['--section-heading-font-size','sectionHeadingFontSize'],
      ['--note-font-size','noteFontSize'],['--note-label-font-size','noteLabelFontSize'],
      ['--caption-font-size','captionFontSize'],['--callout-font-size','calloutFontSize'],
      ['--callout-title-font-size','calloutTitleFontSize'],['--callout-label-font-size','calloutLabelFontSize'],
      ['--callout-chip-font-size','calloutChipFontSize'],
      ['--callout-observe-title-font-size','calloutObserveTitleFontSize']
    ].forEach(([name,key])=>px(name,key));
    raw('--body-leading','lineHeight');
    raw('--note-line-height','noteLineHeight');
    element.style.setProperty('--body-font-family', fontValue(defs.bodyFontFamily, FONT_PRESETS.serif));
    element.style.setProperty('--heading-font-family', fontValue(defs.headingFontFamily, FONT_PRESETS.serif));
    element.style.setProperty('--hero-title-font-family', fontValue(defs.heroTitleFontFamily || defs.headingFontFamily, FONT_PRESETS.serif));
    element.style.setProperty('--part-title-font-family', fontValue(defs.partTitleFontFamily || defs.headingFontFamily, FONT_PRESETS.classic));
    element.style.setProperty('--section-heading-font-family', fontValue(defs.sectionHeadingFontFamily || defs.headingFontFamily, FONT_PRESETS.serif));
    return defs;
  }

  function placementClass(item){
    const layout=itemLayout(item),placement=String(layout.placement || '').trim().toLowerCase();
    if(!layout.wrap&&(placement==='left'||placement==='float-left'))return'no-wrap-left';
    if(!layout.wrap&&(placement==='right'||placement==='float-right'))return'no-wrap-right';
    if(placement === 'left' || placement === 'float-left') return 'float-left';
    if(placement === 'right' || placement === 'float-right') return 'float-right';
    return 'wide';
  }

  function defaultImageCandidates(src){
    const raw = String(src || '').trim();
    return raw ? [raw] : [];
  }

  function applyImageCandidates(img, candidates, onFailure){
    let index = 0;
    const next = ()=>{
      if(index >= candidates.length){
        img.removeEventListener('error', next);
        onFailure();
        return;
      }
      img.src = candidates[index++];
    };
    img.addEventListener('error', next);
    next();
  }

  function applyItemLayout(node,item){
    const layout=itemLayout(item);
    const interaction=layout.floatInteraction || defaultFloatInteraction(item?.type);
    node.dataset.floatInteraction=interaction;
    if(interaction === 'avoid') node.style.display='flow-root';
    else if(interaction === 'clear') node.style.clear='both';
    if(!['figure','scene','side_note'].includes(item?.type)){
      const placement=String(layout.placement||'').toLowerCase();
      if(placement==='left'||placement==='float-left'){
        node.style.float='left';
        if(layout.widthPx) node.style.width=typeof layout.widthPx==='number'?`${layout.widthPx}px`:String(layout.widthPx);
      }else if(placement==='right'||placement==='float-right'){
        node.style.float='right';
        if(layout.widthPx) node.style.width=typeof layout.widthPx==='number'?`${layout.widthPx}px`:String(layout.widthPx);
      }else if(placement==='wide'){
        node.style.width='100%';
      }
    }
    return node;
  }

  function hasExplicitLayout(item){
    const layout = item?.layout;
    if(!layout || typeof layout !== 'object') return false;
    return ['placement','widthPx','heightPx','aspectRatio','wrap'].some(key=>layout[key] !== undefined && layout[key] !== '');
  }

  function sequencePairSide(scene){
    const placement = String(itemLayout(scene).placement || '').toLowerCase();
    if(placement === 'right' || placement === 'float-right') return 'right';
    if(placement === 'left' || placement === 'float-left') return 'left';
    return '';
  }

  function sequencePairMode(callout, scene){
    if(!sequenceSteps(callout).length || !scene || scene.type !== 'scene') return '';
    const side = sequencePairSide(scene);
    if(!side) return '';
    if(!hasExplicitLayout(callout)) return `stack-${side}`;
    const placement = String(itemLayout(callout).placement || 'wide').toLowerCase();
    if(placement === 'wide' || placement === 'full') return `wrap-${side}`;
    if(placement === side || placement === `float-${side}`) return `stack-${side}`;
    return '';
  }

  function applySequencePairWidth(wrapper, scene){
    const layout = itemLayout(scene);
    const width = Number(layout.widthPx || 340);
    if(Number.isFinite(width) && width > 0) wrapper.style.setProperty('--sequence-pair-width', `${width}px`);
  }

  function cssLength(value){
    if(value === null || value === undefined || value === '') return '';
    if(typeof value === 'number' && Number.isFinite(value)) return `${value}px`;
    const n=Number(value);
    return Number.isFinite(n) ? `${n}px` : String(value);
  }

  function applyCanonicalStyle(node,style={}){
    if(!node || !style || typeof style !== 'object') return node;
    const pxMap={
      fontSizePx:'fontSize',marginTopPx:'marginTop',marginRightPx:'marginRight',
      marginBottomPx:'marginBottom',marginLeftPx:'marginLeft',textIndentPx:'textIndent',
      paddingTopPx:'paddingTop',paddingRightPx:'paddingRight',
      paddingBottomPx:'paddingBottom',paddingLeftPx:'paddingLeft',
      borderRadiusPx:'borderRadius'
    };
    Object.entries(pxMap).forEach(([key,prop])=>{
      const value=cssLength(style[key]);
      if(value) node.style[prop]=value;
    });
    if(style.fontFamily) node.style.fontFamily=String(style.fontFamily);
    if(style.lineHeight !== undefined && style.lineHeight !== null && style.lineHeight !== ''){
      const n=Number(style.lineHeight);
      node.style.lineHeight=Number.isFinite(n) ? (n>4?`${n}px`:String(n)) : String(style.lineHeight);
    }else if(style.lineHeightPx){
      node.style.lineHeight=cssLength(style.lineHeightPx);
    }
    if(style.align) node.style.textAlign=String(style.align);
    if(style.color) node.style.color=String(style.color);
    if(style.backgroundColor) node.style.backgroundColor=String(style.backgroundColor);
    if(style.fontWeight) node.style.fontWeight=String(style.fontWeight);
    if(style.fontStyle) node.style.fontStyle=String(style.fontStyle);
    if(style.listStyleType) node.style.listStyleType=String(style.listStyleType);
    if(style.listStylePosition) node.style.listStylePosition=String(style.listStylePosition);
    if(style.hyphenate===false){node.style.hyphens='none';node.style.webkitHyphens='none';}
    if(style.hyphenate===true){node.style.hyphens='auto';node.style.webkitHyphens='auto';}
    if(style.keepTogether) node.dataset.keepTogether='1';
    if(style.keepWithNext) node.dataset.keepWithNext='1';
    if(style.pageBreakBefore) node.dataset.pageBreakBefore='1';
    return node;
  }

  function applyCellStyle(node,style={}){
    applyCanonicalStyle(node,style);
    if(style.verticalAlign) node.style.verticalAlign=String(style.verticalAlign);
    if(style.borderColor) node.style.borderColor=String(style.borderColor);
    return node;
  }

  function addAnchor(node, item, ctx, lang){
    if(!node) return node;
    applyItemLayout(node,item);
    if(!ctx || !isStructuralNavItem(item)) return node;
    const id = itemAnchorId(ctx.page, ctx.pageIndex, item, ctx.itemIndex, lang);
    if(id){ node.dataset.navTargetId = id; if(!node.id) node.id = id; }
    if(!itemNavVisible(item)) node.dataset.navHidden = '1';
    return node;
  }

  function renderItemAnchor(item, ctx, lang){
    if(!ctx || !isStructuralNavItem(item)) return null;
    const id = itemAnchorId(ctx.page, ctx.pageIndex, item, ctx.itemIndex, lang);
    if(!id) return null;
    const anchor = document.createElement('span');
    anchor.className = 'nav-anchor';
    anchor.id = id;
    anchor.dataset.anchorFor = item.type || 'item';
    anchor.setAttribute('aria-hidden','true');
    return anchor;
  }

  function renderItem(item, context={}, options={}){
    const lang = options.lang === 'en' ? 'en' : 'el';
    const text = (key, fallback='')=>getLoc(item,key,fallback,lang);
    const list = key=>getLocArray(item,key,lang);
    const empty = lang === 'en' ? '(empty)' : '(κενό)';
    const layout=itemLayout(item);
    let node;

    if(!item || typeof item !== 'object'){
      node = document.createElement('div');
      node.className = 'book-core-warning';
      node.textContent = lang === 'en' ? 'Invalid page element' : 'Άκυρο στοιχείο σελίδας';
      return node;
    }

    switch(item.type){
      case 'hero': {
        node = document.createElement('section');
        node.className = 'hero';
        node.innerHTML = `${text('eyebrow')?`<p class="eyebrow">${text('eyebrow')}</p>`:''}${text('title')?`<h1>${text('title')}</h1>`:''}${text('subtitle')?`<p class="subtitle">${text('subtitle')}</p>`:''}`;
        applyCanonicalStyle(node,item.style);
        break;
      }
      case 'part_title': {
        node = document.createElement('section');
        node.className = 'part-head';
        node.innerHTML = `${text('label')?`<p class="part-kicker">${text('label')}</p>`:''}${text('title')?`<h2 class="part-title-main">${text('title')}</h2>`:''}`;
        applyCanonicalStyle(node,item.style);
        break;
      }
      case 'section_heading': {
        node = document.createElement('h2');
        node.className = 'section-heading';
        node.innerHTML = text('title');
        applyCanonicalStyle(node,item.style);
        break;
      }
      case 'paragraph': {
        node = document.createElement('p');
        node.className = 'paragraph';
        node.innerHTML = itemBodyHtml(item,{preview:options.preview}) || (options.preview ? `<em>${empty}</em>` : '');
        applyCanonicalStyle(node,item.style);
        break;
      }
      case 'note': {
        node = document.createElement('div');
        node.className = 'note';
        node.innerHTML = `${text('label')?`<span class="label">${text('label')}</span>`:''}${itemBodyHtml(item,{preview:options.preview}) || (options.preview ? `<em>${empty}</em>` : '')}`;
        applyCanonicalStyle(node,item.style);
        break;
      }
      case 'side_note': {
        node = document.createElement('aside');
        node.className = `side-note ${placementClass(item)}`;
        if(layout.widthPx) node.style.setProperty('--figure-width',`${Number(layout.widthPx)}px`);
        node.innerHTML = `${text('label')?`<span class="label">${text('label')}</span>`:''}${text('title')?`<span class="title">${text('title')}</span>`:''}${itemBodyHtml(item,{preview:options.preview}) || (options.preview ? `<em>${empty}</em>` : '')}`;
        applyCanonicalStyle(node,item.style);
        break;
      }
      case 'figure': {
        node = document.createElement('figure');
        node.className = `media ${placementClass(item)}`;
        if(layout.widthPx) node.style.setProperty('--figure-width',`${Number(layout.widthPx)}px`);
        const frame = document.createElement('div');
        const aspect = mediaAspect(item,'figure');
        frame.className = `media-frame ${aspect ? '' : 'natural'}`.trim();
        if(aspect) frame.style.aspectRatio = String(aspect);
        const candidates = (options.imageCandidates || defaultImageCandidates)(item.src || '',item);
        if(candidates.length){
          const image = document.createElement('img');
          image.alt = text('alt') || text('title') || (lang === 'en' ? 'Figure' : 'Εικόνα');
          applyImageCandidates(image,candidates,()=>{
            image.remove();
            const placeholder = document.createElement('div');
            placeholder.className = 'media-placeholder';
            placeholder.textContent = lang === 'en' ? 'Image file not found.' : 'Το αρχείο εικόνας δεν βρέθηκε.';
            frame.appendChild(placeholder);
          });
          frame.appendChild(image);
        }else{
          const placeholder = document.createElement('div');
          placeholder.className = 'media-placeholder';
          placeholder.textContent = lang === 'en' ? 'No image source.' : 'Δεν έχει οριστεί αρχείο εικόνας.';
          frame.appendChild(placeholder);
        }
        node.appendChild(frame);
        if(!item.hideCaption){
          const caption = document.createElement('figcaption');
          caption.innerHTML = text('caption') || text('title') || (lang === 'en' ? 'Figure' : 'Εικόνα');
          node.appendChild(caption);
        }
        break;
      }
      case 'scene': {
        node = document.createElement('figure');
        node.className = `media ${placementClass(item)}`;
        if(layout.widthPx) node.style.setProperty('--figure-width',`${Number(layout.widthPx)}px`);
        const frame = document.createElement('div');
        frame.className = 'media-frame scene-frame';
        frame.style.aspectRatio = String(mediaAspect(item,'scene'));
        const printOptions = item.print && typeof item.print === 'object' ? item.print : {};
        if(printOptions.snapshotTimeoutMs != null) frame.dataset.snapshotTimeoutMs = String(printOptions.snapshotTimeoutMs);
        if(printOptions.snapshotAttempts != null) frame.dataset.snapshotAttempts = String(printOptions.snapshotAttempts);
        if(printOptions.snapshotRetryDelayMs != null) frame.dataset.snapshotRetryDelayMs = String(printOptions.snapshotRetryDelayMs);
        const rawSource=item.src ?? item.singleSrc ?? '';
        const source = String((options.sceneSource || (value=>value))(rawSource,item) || '');
        if(source){
          const iframe = document.createElement('iframe');
          const deferred = options.deferScenes === true;
          iframe.loading = deferred ? 'lazy' : 'eager';
          iframe.referrerPolicy = 'no-referrer';
          iframe.allow = 'fullscreen';
          iframe.dataset.sceneProtocol = 'book-scene-v1';
          iframe.dataset.sceneSource = source;
          frame.dataset.sceneSource = source;
          frame.dataset.sceneBaseSource = source;
          if(deferred){
            iframe.dataset.sceneLoadState = 'deferred';
            iframe.src = 'about:blank';
            iframe.hidden = true;
            const placeholder = document.createElement('div');
            placeholder.className = 'scene-deferred-placeholder';
            placeholder.textContent = lang === 'en'
              ? 'Preparing a printable view of the scene…'
              : 'Προετοιμάζεται η εικόνα της σκηνής…';
            frame.appendChild(placeholder);
          }else{
            iframe.dataset.sceneLoadState = 'loading';
            iframe.addEventListener('load', ()=>{ iframe.dataset.sceneLoadState = 'loaded'; }, {once:true});
            iframe.addEventListener('error', ()=>{ iframe.dataset.sceneLoadState = 'error'; }, {once:true});
            iframe.src = source;
          }
          frame.appendChild(iframe);
        }else{
          const placeholder = document.createElement('div');
          placeholder.className = 'media-placeholder';
          placeholder.textContent = lang === 'en' ? 'No scene URL.' : 'Δεν έχει οριστεί διεύθυνση σκηνής.';
          frame.appendChild(placeholder);
        }
        node.appendChild(frame);
        if(!item.hideCaption){
          const caption = document.createElement('figcaption');
          caption.innerHTML = text('caption') || text('title') || (lang === 'en' ? 'Scene' : 'Σκηνή');
          node.appendChild(caption);
        }
        break;
      }
      case 'interactive_callout': {
        node = document.createElement('div');
        const steps = sequenceSteps(item);
        if(steps.length){
          node.className = 'callout callout-sequence';
          node.dataset.sequenceSceneId = String(item.sequenceSceneId || '');
          const stepHtml = steps.map((step,index)=>{
            const callout = calloutForSequenceStep(item, step, index);
            const setup = (callout.setupChips || []).length ? `<div class="callout-row"><span class="callout-label">${textWithMath(callout.setupLabel || (lang==='en'?'Set':'Ρύθμισε'))}</span>${callout.setupChips.map(value=>`<span class="callout-chip">${textWithMath(value)}</span>`).join('')}</div>` : '';
            const press = (callout.pressChips || []).length ? `<div class="callout-row"><span class="callout-label">${textWithMath(callout.pressLabel || (lang==='en'?'Press':'Πίεσε'))}</span>${callout.pressChips.map(value=>`<span class="callout-chip">${textWithMath(value)}</span>`).join('')}</div>` : '';
            const observe = (callout.observeItems || []).length ? `<div class="callout-observe"><span class="callout-observe-title">${textWithMath(callout.observeTitle || (lang==='en'?'Observe':'Παρατήρησε'))}</span><ul>${callout.observeItems.map(value=>`<li>${textWithMath(value)}</li>`).join('')}</ul></div>` : '';
            const conclusion = (callout.conclusionItems || []).length ? `<div class="callout-conclusion"><span class="callout-conclusion-title">${textWithMath(callout.conclusionTitle || (lang==='en'?'Conclusion':'Συμπέρασμα'))}</span><ul>${callout.conclusionItems.map(value=>`<li>${textWithMath(value)}</li>`).join('')}</ul></div>` : '';
            const extras = calloutExtrasHtml(callout.extras, lang);
            return `<section class="callout-sequence-step${index===Number(item.sequenceInitialStep||0)?' active':''}" data-sequence-index="${index}" data-sequence-step="${attrJson(step)}"><div class="callout-title">${textWithMath(callout.title || `${lang==='en'?'Step':'Βήμα'} ${index+1}`)}</div>${setup}${press}${observe}${conclusion}${extras}</section>`;
          }).join('');
          const buttons = steps.map((step,index)=>`<button type="button" class="callout-sequence-dot${index===Number(item.sequenceInitialStep||0)?' active':''}" data-sequence-goto="${index}" title="${escapeHtml(step.title || `${lang==='en'?'Step':'Βήμα'} ${index+1}`)}">${index+1}</button>`).join('');
          node.innerHTML = `<div class="callout-sequence-head"><button type="button" class="callout-sequence-arrow" data-sequence-move="-1" title="${lang==='en'?'Previous':'Προηγούμενο'}">‹</button><div class="callout-sequence-dots">${buttons}</div><button type="button" class="callout-sequence-arrow" data-sequence-move="1" title="${lang==='en'?'Next':'Επόμενο'}">›</button></div><div class="callout-sequence-body">${stepHtml}</div>`;
        }else{
          node.className = 'callout';
          const setup = (item.setupChips || []).length ? `<div class="callout-row"><span class="callout-label">${textWithMath(getLoc(item,'setupLabel',lang==='en'?'Set':'Ρύθμισε',lang))}</span>${item.setupChips.map(value=>`<span class="callout-chip">${textWithMath(value)}</span>`).join('')}</div>` : '';
          const press = (item.pressChips || []).length ? `<div class="callout-row"><span class="callout-label">${textWithMath(getLoc(item,'pressLabel',lang==='en'?'Press':'Πίεσε',lang))}</span>${item.pressChips.map(value=>`<span class="callout-chip">${textWithMath(value)}</span>`).join('')}</div>` : '';
          const observe = list('observeItems').length ? `<div class="callout-observe"><span class="callout-observe-title">${textWithMath(getLoc(item,'observeTitle',lang==='en'?'Observe':'Παρατήρησε',lang))}</span><ul>${list('observeItems').map(value=>`<li>${textWithMath(value)}</li>`).join('')}</ul></div>` : '';
          const conclusion = list('conclusionItems').length ? `<div class="callout-conclusion"><span class="callout-conclusion-title">${textWithMath(getLoc(item,'conclusionTitle',lang==='en'?'Conclusion':'Συμπέρασμα',lang))}</span><ul>${list('conclusionItems').map(value=>`<li>${textWithMath(value)}</li>`).join('')}</ul></div>` : '';
          const extras = calloutExtrasHtml(item.extras, lang);
          node.innerHTML = `<div class="callout-title">${textWithMath(getLoc(item,'title',lang==='en'?'Try':'Δοκίμασε',lang))}</div>${setup}${press}${observe}${conclusion}${extras}`;
        }
        break;
      }
      case 'equation': {
        node=document.createElement('div');
        node.className='display-equation';
        node.innerHTML=`<div class="display-equation-body">${item.mathml || escapeHtml(item.source || '')}</div>${item.number?`<span class="equation-number">${escapeHtml(item.number)}</span>`:''}${item.caption?`<div class="equation-caption">${escapeHtml(item.caption)}</div>`:''}`;
        applyCanonicalStyle(node,item.style);
        break;
      }
      case 'list': {
        node=document.createElement(item.ordered?'ol':'ul');
        node.className='book-list';
        if(item.ordered && Number.isFinite(Number(item.start))) node.start=Number(item.start);
        applyCanonicalStyle(node,item.style);
        (item.items||[]).forEach(entry=>{const li=document.createElement('li');li.innerHTML=richTextToHtml(entry?.body);if(item.ordered&&Number.isFinite(Number(entry?.value)))li.value=Number(entry.value);if(Number(entry?.level)>0)li.style.marginLeft=`${Number(entry.level)*1.25}em`;applyCanonicalStyle(li,entry?.style);node.appendChild(li);});
        break;
      }
      case 'table': {
        node=document.createElement('table');
        node.className='book-table';
        applyCanonicalStyle(node,item.style);
        const widths=Array.isArray(item?.style?.columnWidthsPx)?item.style.columnWidthsPx:[];
        if(widths.length){const colgroup=document.createElement('colgroup');const total=widths.reduce((a,v)=>a+(Number(v)||0),0)||1;widths.forEach(value=>{const col=document.createElement('col');col.style.width=`${((Number(value)||0)/total)*100}%`;colgroup.appendChild(col)});node.appendChild(colgroup);}
        const tbody=document.createElement('tbody');
        (item.rows||[]).forEach((row,rowIndex)=>{const tr=document.createElement('tr');if(rowIndex<Number(item?.style?.headerRows||0))tr.dataset.tableHeader='1';(row.cells||[]).forEach(cell=>{const td=document.createElement('td');if(Number(cell.colspan)>1)td.colSpan=Number(cell.colspan);if(Number(cell.rowspan)>1)td.rowSpan=Number(cell.rowspan);td.innerHTML=richTextToHtml(cell.body);applyCellStyle(td,cell.style);tr.appendChild(td);});tbody.appendChild(tr);});
        node.appendChild(tbody);
        break;
      }
      case 'nav_anchor': {
        node = document.createElement('span');
        node.className = 'nav-anchor nav-anchor-inline';
        node.setAttribute('aria-hidden','true');
        if(options.editor) node.dataset.editorLabel = `${lang==='en'?'Menu point':'Σημείο μενού'}: ${itemNavTitle(item,item.id || '—',lang)}`;
        break;
      }
      case 'clear': {
        node = document.createElement('div');
        node.className = 'clear';
        break;
      }
      default: {
        node = document.createElement('div');
        node.className = 'book-core-warning';
        node.textContent = `${lang==='en'?'Unknown element type':'Άγνωστος τύπος στοιχείου'}: ${item.type || '—'}`;
      }
    }
    return addAnchor(node,item,context,lang);
  }

  function renderPageNode(data, page, pageIndex=0, options={}){
    const lang = options.lang === 'en' ? 'en' : 'el';
    const pages = Array.isArray(data?.pages) ? data.pages : [];
    const layout = Object.assign({}, DEFAULT_LAYOUT, data?.layoutDefaults || {});
    const frame=resolvePageFrame(data,page,pageIndex);
    const pageNumber=Number(options.pageNumber || frame.pageNumber || pageIndex+1);
    const totalPages=frame.pageCount || pages.length || 1;
    const wrap = document.createElement('div');
    wrap.className = `book-page-root sheet-wrap${(options.editor||options.preview) ? ' editor-preview-sheet-wrap' : ''}`;
    wrap.id = page?.id || `page-${pageNumber}`;
    applyLayoutVars(wrap,layout);

    const sheet = document.createElement('section');
    sheet.className = 'sheet';
    const inner = document.createElement('div');
    inner.className = 'sheet-inner';
    const header = document.createElement('div');
    header.className = 'sheet-header';
    if(Number(layout.headerFontSize) <= 0) header.classList.add('hidden');
    header.innerHTML = `<div class="l">${replaceTokens(frame.header.left,{page:pageNumber,pages:totalPages})}</div><div class="c">${replaceTokens(frame.header.center,{page:pageNumber,pages:totalPages})}</div><div class="r">${replaceTokens(frame.header.right,{page:pageNumber,pages:totalPages})}</div>`;
    inner.appendChild(header);

    const body = document.createElement('div');
    body.className = 'sheet-body';
    const items = page?.items || [];
    const appendSequencePair = (pairMode, callout, scene, calloutIndex, sceneIndex)=>{
      const pair = document.createElement('div');
      pair.className = `sequence-pair sequence-${pairMode}`;
      pair.dataset.sequencePair = pairMode;
      applySequencePairWidth(pair, scene);

      const calloutContext = {page,pageIndex,itemIndex:calloutIndex};
      const sceneContext = {page,pageIndex,itemIndex:sceneIndex};
      const calloutNode = renderItem(callout,calloutContext,options);
      if(callout?.id) calloutNode.dataset.bookItemId = String(callout.id);
      calloutNode.dataset.bookItemType = String(callout?.type || 'unknown');
      calloutNode.dataset.bookItemIndex = String(calloutIndex);
      if(calloutIndex === options.highlightedIndex) calloutNode.classList.add('item-highlight');

      const sceneNode = renderItem(scene,sceneContext,options);
      if(scene?.id) sceneNode.dataset.bookItemId = String(scene.id);
      sceneNode.dataset.bookItemType = String(scene?.type || 'unknown');
      sceneNode.dataset.bookItemIndex = String(sceneIndex);
      if(sceneIndex === options.highlightedIndex) sceneNode.classList.add('item-highlight');

      if(pairMode.startsWith('wrap-')){
        pair.appendChild(sceneNode);
        pair.appendChild(calloutNode);
      }else{
        pair.appendChild(calloutNode);
        pair.appendChild(sceneNode);
      }
      body.appendChild(pair);
    };
    for(let itemIndex=0; itemIndex<items.length; itemIndex++){
      const item = items[itemIndex];
      const nextItem = items[itemIndex + 1];
      const sceneBeforeCallout = item?.type === 'scene' && sequenceSteps(nextItem).length && String(nextItem?.sequenceSceneId || '').trim() === String(item.id || '');
      if(sceneBeforeCallout){
        const pairMode = sequencePairMode(nextItem, item);
        if(pairMode){
          appendSequencePair(pairMode, nextItem, item, itemIndex+1, itemIndex);
          itemIndex++;
          continue;
        }
      }
      const linkedSceneId = String(item?.sequenceSceneId || '').trim();
      const pairScene = linkedSceneId && nextItem?.type === 'scene' && String(nextItem.id || '') === linkedSceneId ? nextItem : null;
      const pairMode = pairScene ? sequencePairMode(item, pairScene) : '';
      if(pairMode){
        appendSequencePair(pairMode, item, pairScene, itemIndex, itemIndex+1);
        itemIndex++;
        continue;
      }
      const context = {page,pageIndex,itemIndex};
      const node = renderItem(item,context,options);
      if(item?.id) node.dataset.bookItemId = String(item.id);
      node.dataset.bookItemType = String(item?.type || 'unknown');
      node.dataset.bookItemIndex = String(itemIndex);
      if(itemIndex === options.highlightedIndex) node.classList.add('item-highlight');
      body.appendChild(node);
    }
    inner.appendChild(body);

    const footer = document.createElement('div');
    footer.className = 'sheet-footer';
    if(Number(layout.footerFontSize) <= 0) footer.classList.add('hidden');
    const rightValue = frame.numberingEnabled ? frame.footer.right : '';
    footer.innerHTML = `<div class="l">${replaceTokens(frame.footer.left,{page:pageNumber,pages:totalPages})}</div><div class="c">${replaceTokens(frame.footer.center,{page:pageNumber,pages:totalPages})}</div><div class="r">${replaceTokens(rightValue,{page:pageNumber,pages:totalPages})}</div>`;
    inner.appendChild(footer);
    sheet.appendChild(inner);
    wrap.appendChild(sheet);
    return wrap;
  }

  function renderPages(host, data, options={}){
    host.innerHTML = '';
    const pages = Array.isArray(data?.pages) ? data.pages : [];
    pages.forEach((page,index)=>host.appendChild(renderPageNode(data,page,index,options)));
    return pages.length;
  }

  function findSequenceSceneFrame(sequenceNode){
    const id = String(sequenceNode?.dataset?.sequenceSceneId || '').trim();
    if(!id) return null;
    const page = sequenceNode.closest?.('.book-page-root,.sheet-wrap') || global.document;
    return page.querySelector?.(`[data-book-item-id="${CSS.escape(id)}"] .scene-frame`) || global.document.querySelector?.(`[data-book-item-id="${CSS.escape(id)}"] .scene-frame`) || null;
  }

  function sequenceStepUsesPatch(step={}){
    return step?.stateMode === 'patch' || step?.patchState === true || step?.continueState === true;
  }

  function postSequenceScenePatch(iframe, step={}){
    const send = ()=>{
      const target = iframe?.contentWindow;
      if(!target) return false;
      const preset = step.preset ?? null;
      if(preset != null && String(preset).trim()){
        target.postMessage({type:'book-scene-preset', preset:String(preset)}, '*');
      }
      const state = step.state && typeof step.state === 'object' ? step.state : {};
      target.postMessage({type:'book-scene-apply', state}, '*');
      return true;
    };
    if(iframe?.dataset?.sceneLoadState === 'loading'){
      iframe.addEventListener('load', send, {once:true});
      return true;
    }
    return send();
  }

  function activateCalloutSequence(sequenceNode,index=0){
    const steps = Array.from(sequenceNode.querySelectorAll('.callout-sequence-step'));
    if(!steps.length) return;
    const next = Math.max(0, Math.min(steps.length-1, Number(index)||0));
    steps.forEach((stepNode,stepIndex)=>stepNode.classList.toggle('active', stepIndex===next));
    sequenceNode.querySelectorAll('.callout-sequence-dot').forEach(button=>button.classList.toggle('active', Number(button.dataset.sequenceGoto)===next));
    sequenceNode.dataset.sequenceActive = String(next);
    const stepNode = steps[next];
    let step = {};
    try{ step = JSON.parse(stepNode.dataset.sequenceStep || '{}'); }catch(_e){}
    const frame = findSequenceSceneFrame(sequenceNode);
    const iframe = frame?.querySelector?.('iframe');
    if(!iframe) return;
    if(sequenceStepUsesPatch(step) && postSequenceScenePatch(iframe, step)) return;
    const base = frame.dataset.sceneBaseSource || frame.dataset.sceneSource || iframe.dataset.sceneSource || iframe.getAttribute('src') || '';
    const nextSrc = sequenceSceneUrl(base, step);
    if(nextSrc && iframe.getAttribute('src') !== nextSrc){
      iframe.dataset.sceneLoadState = 'loading';
      iframe.addEventListener('load', ()=>{ iframe.dataset.sceneLoadState = 'loaded'; }, {once:true});
      iframe.addEventListener('error', ()=>{ iframe.dataset.sceneLoadState = 'error'; }, {once:true});
      iframe.src = nextSrc;
      iframe.dataset.sceneSource = nextSrc;
      frame.dataset.sceneSource = nextSrc;
    }
  }

  function bindCalloutSequences(root=global.document){
    root.querySelectorAll?.('[data-callout-extras]').forEach(extrasNode=>{
      if(extrasNode.dataset.extrasBound === '1') return;
      extrasNode.dataset.extrasBound = '1';
      extrasNode.querySelector?.('[data-callout-extras-toggle]')?.addEventListener('click',event=>{
        event.preventDefault();
        const body = extrasNode.querySelector('.callout-extras-body');
        const open = body?.hasAttribute('hidden');
        if(body) body.hidden = !open;
        extrasNode.classList.toggle('collapsed', !open);
      });
    });
    root.querySelectorAll?.('[data-callout-answer-toggle]').forEach(button=>{
      if(button.dataset.answerBound === '1') return;
      button.dataset.answerBound = '1';
      button.addEventListener('click',event=>{
        event.preventDefault();
        const answer = button.nextElementSibling;
        if(!answer) return;
        const open = answer.hasAttribute('hidden');
        answer.hidden = !open;
        button.textContent = open ? (button.dataset.hideLabel || 'Απόκρυψη απάντησης') : (button.dataset.showLabel || 'Εμφάνιση απάντησης');
      });
    });
    root.querySelectorAll?.('.callout-sequence').forEach(sequenceNode=>{
      if(sequenceNode.dataset.sequenceBound === '1') return;
      sequenceNode.dataset.sequenceBound = '1';
      sequenceNode.addEventListener('click',event=>{
        const goto = event.target.closest?.('[data-sequence-goto]');
        const move = event.target.closest?.('[data-sequence-move]');
        if(!goto && !move) return;
        event.preventDefault();
        const current = Number(sequenceNode.dataset.sequenceActive || 0) || 0;
        if(goto) activateCalloutSequence(sequenceNode, Number(goto.dataset.sequenceGoto));
        else activateCalloutSequence(sequenceNode, current + (Number(move.dataset.sequenceMove)||0));
      });
      const initial = Number(sequenceNode.querySelector('.callout-sequence-step.active')?.dataset.sequenceIndex || sequenceNode.dataset.sequenceActive || 0) || 0;
      activateCalloutSequence(sequenceNode, initial);
    });
  }

  function auditData(data){
    const validation=validateData(data);
    const stats={schema:String(data?.schemaVersion||LEGACY_SCHEMA),pages:0,items:0,figures:0,figuresWithCaptions:0,figuresWithAssets:0,scenes:0,scenesWithSources:0,legacyHtmlBlocks:0,richTextBlocks:0,layoutItems:0};
    const warnings=[...validation.warnings];
    (data?.pages||[]).forEach((page,pageIndex)=>{
      stats.pages++;
      (page.items||[]).forEach((item,itemIndex)=>{
        stats.items++;
        if(['paragraph','note','side_note'].includes(item.type)){
          if(typeof item.legacySourceHtml==='string'||typeof item.html==='string') stats.legacyHtmlBlocks++;
          if(item.body?.format==='rich-text-v1') stats.richTextBlocks++;
        }
        if(item.layout) stats.layoutItems++;
        if(item.type==='figure'){
          stats.figures++;
          if(String(item.caption||item.title||'').trim()) stats.figuresWithCaptions++;
          if(String(item.src||'').trim()) stats.figuresWithAssets++;
          if(!String(item.src||'').trim()) warnings.push(`Σελίδα ${pageIndex+1}, figure ${itemIndex+1}: λείπει src.`);
        }
        if(item.type==='scene'){
          stats.scenes++;
          const src=item.src??item.singleSrc??'';
          if(String(src).trim()) stats.scenesWithSources++;
          if(!String(src).trim()) warnings.push(`Σελίδα ${pageIndex+1}, scene ${itemIndex+1}: λείπει src.`);
        }
      });
    });
    return {ok:validation.ok&&warnings.length===0,renderer:`BookCore ${VERSION}`,directV4:isV4Data(data),validation,stats,warnings};
  }

  global.BookCore = Object.freeze({
    VERSION,
    V4_SCHEMA,
    LEGACY_SCHEMA,
    SUPPORTED_SCHEMAS,
    DEFAULT_LAYOUT,
    TEXT_KEYS,
    normalizeData,
    validateData,
    getLoc,
    getLocArray,
    isV4Data,
    richTextToHtml,
    itemNavVisible,
    itemLayout,
    itemBodyHtml,
    resolvePageFrame,
    replaceTokens,
    slugifyForId,
    normalizeDomId,
    itemNavTitle,
    isStructuralNavItem,
    itemAnchorId,
    parseAspect,
    mediaAspect,
    applyLayoutVars,
    renderItem,
    renderPageNode,
    renderPages,
    sequenceSceneUrl,
    expandScreenSequences,
    expandPrintSequences,
    bindCalloutSequences,
    auditData
  });
})(window);
