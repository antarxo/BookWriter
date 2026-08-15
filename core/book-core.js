(function(global){
  'use strict';

  const VERSION = '0.5.1k-hf13-multilevel-list-spanning-table-rows';
  const V4_SCHEMA = 'bookwriter-v4';
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
    headerPaddingBottomPx:8,
    headerBorderWidthPx:1,
    headerFontSize:0,
    headerLineHeight:1,
    headerFontFamily:'sans',
    headerTextColor:'#7f8890',
    footerBottomPx:18,
    footerHeightPx:16,
    footerPaddingTopPx:8,
    footerBorderWidthPx:1,
    footerFontSize:0,
    footerLineHeight:1,
    footerFontFamily:'sans',
    footerTextColor:'#7b8791',
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
    'alt','caption','setupLabel','pressLabel','observeTitle','navLabel','intro'
  ]);
  const STRUCTURAL_TYPES = new Set([
    'hero','part_title','section_heading','note','side_note',
    'figure','scene','dialogue','nav_anchor'
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
    const schema = String(out?.schemaVersion || '');
    if(schema !== V4_SCHEMA) throw new Error(`Απαιτείται ${V4_SCHEMA}. Δεν υποστηρίζεται άλλη δομή αρχείου.`);
    out.schemaVersion = V4_SCHEMA;
    if(!out.meta || typeof out.meta !== 'object') out.meta = {};
    if(!out.layoutDefaults || typeof out.layoutDefaults !== 'object') out.layoutDefaults = {};
    out.layoutDefaults = Object.assign({}, DEFAULT_LAYOUT, out.layoutDefaults);
    if(!out.nav || typeof out.nav !== 'object') out.nav = {};
    if(!out.nav.mode) out.nav.mode = 'auto';
    if(out.nav.showApp == null) out.nav.showApp = true;
    if(out.nav.showPrint == null) out.nav.showPrint = true;
    if(!Array.isArray(out.nav.groups)) out.nav.groups = [];
    if(!out.pageDefaults || typeof out.pageDefaults !== 'object') out.pageDefaults = {};
    if(!out.pageDefaults.header || typeof out.pageDefaults.header !== 'object') out.pageDefaults.header = {inherit:false,left:'',center:'',right:''};
    if(!out.pageDefaults.footer || typeof out.pageDefaults.footer !== 'object') out.pageDefaults.footer = {inherit:false,left:'',center:'',right:'{page}'};
    if(!out.pageDefaults.pageNumbering || typeof out.pageDefaults.pageNumbering !== 'object') out.pageDefaults.pageNumbering = {enabled:true,startAt:1,position:'footer-right',hideOnFirstPage:false};
    if(!Array.isArray(out.pages)) out.pages = [];
    out.pages.forEach((page, index)=>{
      if(!page || typeof page !== 'object') out.pages[index] = page = {};
      if(!page.id) page.id = `page-${index+1}`;
      if(!page.header || typeof page.header !== 'object') page.header = {inherit:true,left:'',center:'',right:''};
      if(!page.footer || typeof page.footer !== 'object') page.footer = {inherit:true,left:'',center:'',right:''};
      if(!page.pageNumbering || typeof page.pageNumbering !== 'object') page.pageNumbering = {inherit:true,enabled:true,offset:0,hide:false};
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
    const schema = String(raw.schemaVersion || '');
    if(schema !== V4_SCHEMA) errors.push(`Απαιτείται ${V4_SCHEMA}. Δεν υποστηρίζεται άλλη δομή αρχείου.`);
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
        const sceneSrc = item.src;
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

  function calloutListHtml(values=[]){
    const parsed = (Array.isArray(values) ? values : []).map(value=>{
      let text = String(value ?? '').trim();
      let level = 0;
      while(/^↳\s*/.test(text)){
        level++;
        text = text.replace(/^↳\s*/, '').trim();
      }
      text = text.replace(/^[•*-]\s+/, '');
      return {level,text};
    }).filter(entry=>entry.text);
    if(!parsed.length) return '';
    const minLevel = Math.min(...parsed.map(entry=>entry.level));
    parsed.forEach(entry=>{ entry.level = Math.max(0, entry.level - minLevel); });
    let index = 0;
    const renderLevel = level=>{
      let html = '<ul class="callout-list">';
      while(index < parsed.length && parsed[index].level >= level){
        if(parsed[index].level > level){
          html += renderLevel(parsed[index].level);
          continue;
        }
        const item = parsed[index++];
        html += `<li>${textWithMath(item.text)}`;
        while(index < parsed.length && parsed[index].level > level) html += renderLevel(parsed[index].level);
        html += '</li>';
      }
      return html + '</ul>';
    };
    return renderLevel(0);
  }

  function dialogueMarkupHtml(value=''){
    return escapeHtml(String(value ?? ''))
      .replace(/\r?\n/g,'<br>')
      .replace(/&lt;(\/?)(b|strong|i|em|sub|sup)&gt;/gi,'<$1$2>')
      .replace(/&lt;br\s*\/?&gt;/gi,'<br>');
  }

  function dialogueCellHtml(value='',empty=''){
    const raw = String(value ?? '').trim();
    return raw ? dialogueMarkupHtml(raw) : `<span class="dialogue-empty">${escapeHtml(empty)}</span>`;
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
    const body = items.map((entry,index)=>{
      const type = String(entry.type || 'question');
      const label = entry.label || (type === 'exercise' ? (lang === 'en' ? 'Exercise' : 'Άσκηση') : type === 'prompt' ? (lang === 'en' ? 'Think' : 'Σκέψου') : (lang === 'en' ? 'Question' : 'Ερώτηση'));
      const lines = Math.max(0, Math.min(12, Number(entry.answerLines) || 0));
      const answerText = String(entry.answer || '').trim();
      const showAnswer = lang === 'en' ? 'Show answer' : 'Εμφάνιση απάντησης';
      const hideAnswer = lang === 'en' ? 'Hide answer' : 'Απόκρυψη απάντησης';
      const answerHtml = answerText ? `<button type="button" class="callout-extra-answer-toggle" data-callout-answer-toggle data-show-label="${escapeHtml(showAnswer)}" data-hide-label="${escapeHtml(hideAnswer)}">${escapeHtml(showAnswer)}</button><div class="callout-extra-answer" hidden>${textWithMath(answerText).replace(/\n/g,'<br>')}</div>` : '';
      const linesHtml = lines ? `<div class="callout-extra-lines" aria-hidden="true">${Array.from({length:lines},()=>'<span></span>').join('')}</div>` : '';
      return `<div class="callout-extra-item"><b><span class="callout-extra-number">${index+1}.</span> ${textWithMath(label)}</b><p>${textWithMath(entry.text)}</p>${answerHtml}${linesHtml}</div>`;
    }).join('');
    return `<div class="callout-extras${collapsed?' collapsed':''}" data-callout-extras data-print="${extras.print === false ? '0' : '1'}"><button type="button" class="callout-extras-toggle" data-callout-extras-toggle>${escapeHtml(title)}</button><div class="callout-extras-body" ${collapsed?'hidden':''}>${body}</div></div>`;
  }

  function visibleInMode(entry, mode='screen'){
    const visibility = entry?.visibility && typeof entry.visibility === 'object' ? entry.visibility : {};
    if(entry?.printOnly === true) return mode === 'print';
    if(entry?.screenOnly === true) return mode === 'screen';
    if(mode === 'screen' && visibility.screen === false) return false;
    if(mode === 'print' && visibility.print === false) return false;
    return true;
  }

  function materializeVisibleEntry(entry, mode='screen'){
    const next = Object.assign({}, entry || {});
    if(mode === 'print'){
      delete next.visibility;
      delete next.printOnly;
      delete next.screenOnly;
    }
    return next;
  }

  function continuationPageInfo(page){
    const match = String(page?.id || '').match(/^(.*)-r(\d+)$/);
    if(!match) return null;
    const part = Number(match[2]);
    return part > 1 ? {baseId:match[1], part} : null;
  }

  function printOnlyContinuationPage(page){
    return !visibleInMode(page, 'screen') && visibleInMode(page, 'print');
  }

  function orderContinuationPages(pages=[], shouldMove=()=>true){
    const baseIds = new Set((pages || []).map(page=>String(page?.id || '')).filter(Boolean));
    const continuations = new Map();
    const moved = new Set();
    (pages || []).forEach(page=>{
      const info = continuationPageInfo(page);
      if(!info || !baseIds.has(info.baseId) || !shouldMove(page)) return;
      if(!continuations.has(info.baseId)) continuations.set(info.baseId, []);
      continuations.get(info.baseId).push({page, part:info.part});
      moved.add(page);
    });
    continuations.forEach(list=>list.sort((a,b)=>a.part-b.part));
    const ordered = [];
    (pages || []).forEach(page=>{
      if(moved.has(page)) return;
      ordered.push(page);
      const list = continuations.get(String(page?.id || ''));
      if(list) ordered.push(...list.map(entry=>entry.page));
    });
    return ordered;
  }

  function filterBookForMode(book, mode='screen'){
    const next = normalizeData(deepClone(book));
    let filteredPages = 0;
    let filteredItems = 0;
    if(mode === 'screen'){
      let hiddenPages = 0;
      let hiddenItems = 0;
      next.pages = (next.pages || []).map((page,pageIndex)=>{
        if(!visibleInMode(page, mode)) hiddenPages++;
        const items = (page.items || []).map(item=>{
          if(!visibleInMode(item, mode)) hiddenItems++;
          return item;
        });
        return Object.assign({}, page, {
          id:page.id || `page-${pageIndex+1}`,
          items
        });
      });
      next.pages = orderContinuationPages(next.pages, printOnlyContinuationPage);
      next.extensions = Object.assign({}, next.extensions || {}, {
        screenVisibilityExpansion:{
          enabled:hiddenPages > 0 || hiddenItems > 0,
          mode,
          filteredPages:0,
          filteredItems:0,
          hiddenPages,
          hiddenItems,
          totalPages:next.pages.length
        }
      });
      return next;
    }
    next.pages = orderContinuationPages((next.pages || [])
      .filter(page=>{
        const visible = visibleInMode(page, mode);
        if(!visible) filteredPages++;
        return visible;
      }), printOnlyContinuationPage)
      .map((page,pageIndex)=>{
        const items = (page.items || []).filter(item=>{
          const visible = visibleInMode(item, mode);
          if(!visible) filteredItems++;
          return visible;
        }).map(item=>materializeVisibleEntry(item, mode));
        return Object.assign(materializeVisibleEntry(page, mode), {
          id:page.id || `page-${pageIndex+1}`,
          items
        });
      })
      .filter(page=>(page.items || []).length || page.keepEmpty === true);
    next.extensions = Object.assign({}, next.extensions || {}, {
      [`${mode}VisibilityExpansion`]:{
        enabled:filteredPages > 0 || filteredItems > 0,
        mode,
        filteredPages,
        filteredItems,
        totalPages:next.pages.length
      }
    });
    return next;
  }

  function expandScreenSequences(data, options={}){
    if(options.enabled === false) return deepClone(data);
    const book = filterBookForMode(data, 'screen');
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
    const book = filterBookForMode(data, 'print');
    const sceneById = new Map();
    const scenePageById = new Map();
    (book.pages || []).forEach((page,pageIndex) => (page.items || []).forEach((item,itemIndex) => {
      if(item?.type === 'scene' && item.id){
        sceneById.set(String(item.id), item);
        scenePageById.set(String(item.id), {pageIndex,itemIndex});
      }
    }));
    let changed = false;
    let generatedSteps = 0;
    const expandedPages = (book.pages || []).map((page,pageIndex)=>{
      const items = page.items || [];
      const skip = new Set();
      const out = [];
      let pageChanged = false;
      const appendGenerated = (callout, scene, calloutIndex)=>{
        const steps = sequenceSteps(callout);
        if(!steps.length || !scene || callout?.print?.expand === 'none') return false;
        steps.forEach((step,stepIndex)=>{
          const stepNumber = stepIndex + 1;
          const stepCallout = calloutForSequenceStep(callout, step, stepIndex);
          stepCallout.extensions = Object.assign({}, stepCallout.extensions || {}, {sequencePrintGenerated:true, sourcePageId:page.id || '', sourceCalloutId:callout.id || '', sourceSceneId:scene.id || ''});
          const sceneCopy = deepClone(scene);
          sceneCopy.id = `${scene.id || 'scene'}-step-${stepNumber}`;
          sceneCopy.src = sequenceSceneUrl(scene.src ?? scene.singleSrc ?? '', step);
          sceneCopy.title = step.title || scene.title || '';
          sceneCopy.extensions = Object.assign({}, sceneCopy.extensions || {}, {sequencePrintGenerated:true, sequenceSourceSceneId:scene.id || '', sourceCalloutId:callout.id || '', sequenceStep:stepNumber});
          stepCallout.sequenceSceneId = sceneCopy.id;
          out.push(stepCallout, sceneCopy);
          generatedSteps++;
        });
        changed = true;
        pageChanged = true;
        const scenePos = scene?.id ? scenePageById.get(String(scene.id)) : null;
        if(scenePos && scenePos.pageIndex === pageIndex) skip.add(scenePos.itemIndex);
        skip.add(calloutIndex);
        return true;
      };
    for(let itemIndex=0; itemIndex<items.length; itemIndex++){
        if(skip.has(itemIndex)) continue;
        const item = items[itemIndex];
        const next = items[itemIndex + 1];
        if(item?.type === 'scene' && sequenceSteps(next).length && String(next?.sequenceSceneId || '').trim() === String(item.id || '')){
          if(appendGenerated(next, item, itemIndex + 1)){
            skip.add(itemIndex);
            continue;
          }
        }
        const sceneId = String(item?.sequenceSceneId || '').trim();
        const scene = sceneId ? sceneById.get(sceneId) : null;
        if(appendGenerated(item, scene, itemIndex)) continue;
        out.push(item);
      }
      return Object.assign({}, page, {
        items:out,
        extensions:Object.assign({}, page.extensions || {}, pageChanged ? {printSequenceExpansion:true} : {})
      });
    });
    book.pages = expandedPages;
    book.extensions = Object.assign({}, book.extensions || {}, {printSequenceExpansion:{enabled:changed, mode:'inline-flow', generatedSteps, generatedPages:0, totalPages:expandedPages.length, generatedAt:new Date().toISOString()}});
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
    if(run?.highlight) styles.push(`background-color:${String(run.highlight)}`);if(run?.fontFamily)styles.push(`font-family:${String(run.fontFamily)}`);if(Number(run?.fontSizePx)>0)styles.push(`font-size:${Number(run.fontSizePx)}px`);
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
    const placement=String(layout.placement ?? item?.placement ?? (media?'float-right':'block'));
    return {
      placement,
      widthPx:layout.widthPx ?? item?.frameWidth,
      heightPx:layout.heightPx ?? item?.frameHeight ?? item?.figureHeight ?? item?.sceneHeight,
      aspectRatio:layout.aspectRatio ?? item?.aspectRatio ?? item?.sceneAspect ?? item?.aspect,
      wrap:!!(layout.wrap ?? item?.wrapTight ?? (media||placement.startsWith('float-'))),
      floatInteraction:String(layout.floatInteraction ?? item?.floatInteraction ?? ''),
      crop:layout.crop ?? item?.crop ?? null
    };
  }

  function defaultFloatInteraction(type){
    return type==='clear'?'clear':'wrap';
  }

  function itemBodyHtml(item, options={}){
    if(!item || typeof item !== 'object') return '';
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

  function sceneCropStyle(crop={}){
    if(!crop || typeof crop !== 'object') return '';
    const baseScale = Math.max(1, Number(crop.scale) || 1);
    const scaleX = Math.max(1, Number(crop.scaleX) || baseScale);
    const scaleY = Math.max(1, Number(crop.scaleY) || baseScale);
    const x = Number(crop.xPercent ?? crop.x ?? 0) || 0;
    const y = Number(crop.yPercent ?? crop.y ?? 0) || 0;
    return [
      `width:${(scaleX * 100).toFixed(4)}%`,
      `height:${(scaleY * 100).toFixed(4)}%`,
      `transform:translate(${-x}%, ${-y}%)`,
      'transform-origin:top left'
    ].join(';');
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
      ['--header-pad-bottom','headerPaddingBottomPx'],['--header-border-width','headerBorderWidthPx'],
      ['--footer-bottom','footerBottomPx'],['--footer-h','footerHeightPx'],
      ['--footer-pad-top','footerPaddingTopPx'],['--footer-border-width','footerBorderWidthPx'],
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
    raw('--header-line-height','headerLineHeight');
    raw('--footer-line-height','footerLineHeight');
    raw('--header-text-color','headerTextColor');
    raw('--footer-text-color','footerTextColor');
    element.style.setProperty('--header-font-family', fontValue(defs.headerFontFamily, FONT_PRESETS.sans));
    element.style.setProperty('--footer-font-family', fontValue(defs.footerFontFamily, FONT_PRESETS.sans));
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
    if(placement === 'block' || placement === 'inline') return 'block-media';
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

  function isGeneratedSequencePair(callout, scene){
    return callout?.type === 'interactive_callout'
      && scene?.type === 'scene'
      && callout?.extensions?.sequencePrintGenerated === true
      && scene?.extensions?.sequencePrintGenerated === true
      && String(callout?.extensions?.sourceCalloutId || '') === String(scene?.extensions?.sourceCalloutId || '')
      && Number(callout?.extensions?.sequenceStep || 0) === Number(scene?.extensions?.sequenceStep || 0);
  }

  function sequencePairMode(callout, scene){
    if((!sequenceSteps(callout).length && !isGeneratedSequencePair(callout, scene)) || !scene || scene.type !== 'scene') return '';
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

  function positioningRenderMode(contract,item={}){
    if(!contract||typeof contract!=='object')return'';
    const explicit=String(contract.renderMode||contract.behavior||'').toLowerCase();
    if(['exact','flow-wrap','flow-block'].includes(explicit))return explicit;
    const mode=String(contract.mode||'page-absolute');
    const wrap=String(contract.wrap?.type||'wrapNone');
    if(contract.stacking?.behindDoc||mode==='page-absolute')return'exact';
    if(mode!=='paragraph-anchored')return'';
    if(['wrapSquare','wrapTight','wrapThrough'].includes(wrap))return'flow-wrap';
    if(wrap==='wrapTopAndBottom')return'flow-block';
    return'exact';
  }

  function applyPositioningContract(node,item,ctx={}){
    const contract=item?.extensions?.positioningContract;
    if(!node||!contract)return node;
    const mode=String(contract.mode||'page-absolute');
    if(!['page-absolute','paragraph-anchored'].includes(mode))return node;
    const renderMode=positioningRenderMode(contract,item);
    const layout=Object.assign({},DEFAULT_LAYOUT,ctx.layout||{});
    const x=Number(contract.xPx ?? contract.horizontal?.offsetPx);
    const y=Number(contract.yPx ?? contract.vertical?.offsetPx);
    const width=Number(contract.widthPx ?? item?.layout?.widthPx);
    const height=Number(contract.heightPx ?? item?.layout?.heightPx);
    const wrap=contract.wrap||{};
    node.dataset.positioningMode=mode;
    node.dataset.positioningRenderMode=renderMode;
    if(mode==='paragraph-anchored')node.dataset.positionAnchorItem=String(contract.anchor?.itemId||'');
    if(Number.isFinite(width)&&width>0)node.style.width=`${width}px`;
    if(Number.isFinite(height)&&height>0&&item?.type!=='figure'){node.style.height=`${height}px`;node.style.minHeight='0'}else if(Number.isFinite(height)&&height>0&&item?.type==='figure'){node.dataset.wordDisplayHeight=String(height)}

    if(renderMode==='flow-wrap'){
      node.classList.add('flow-positioned-item','flow-wrap-item');
      node.classList.remove('positioned-item');
      node.style.position='relative';node.style.left='';node.style.top='';node.style.zIndex='';node.style.clear='none';
      const placement=String(item?.layout?.placement||'').toLowerCase();
      const side=placement.includes('left')?'left':placement.includes('right')?'right':(Number.isFinite(x)&&x>Number(layout.pageWidthPx||793)/2?'right':'left');
      node.style.float=side;
      const top=Math.max(0,Number(wrap.distTopPx)||0),right=Math.max(0,Number(wrap.distRightPx)||0),bottom=Math.max(0,Number(wrap.distBottomPx)||0),left=Math.max(0,Number(wrap.distLeftPx)||0);
      node.style.margin=`${top}px ${right}px ${bottom}px ${left}px`;
      node.dataset.wrapSide=side;
      return node;
    }
    if(renderMode==='flow-block'){
      node.classList.add('flow-positioned-item','flow-block-item');
      node.classList.remove('positioned-item');
      node.style.position='relative';node.style.left='';node.style.top='';node.style.zIndex='';node.style.float='none';node.style.clear='both';
      const top=Math.max(0,Number(wrap.distTopPx)||0),right=Math.max(0,Number(wrap.distRightPx)||0),bottom=Math.max(0,Number(wrap.distBottomPx)||0),left=Math.max(0,Number(wrap.distLeftPx)||0);
      node.style.margin=`${top}px ${right}px ${bottom}px ${left}px`;
      return node;
    }

    node.classList.add('positioned-item');
    node.classList.remove('flow-positioned-item','flow-wrap-item','flow-block-item');
    node.style.position='absolute';node.style.float='none';node.style.clear='none';node.style.margin='0';
    if(mode==='page-absolute'){
      if(Number.isFinite(x))node.style.left=`${x-Number(layout.pagePaddingLeftPx||0)}px`;
      if(Number.isFinite(y))node.style.top=`${y-Number(layout.pagePaddingTopPx||0)}px`;
    }else{
      if(Number.isFinite(x))node.dataset.positionOffsetX=String(x);
      if(Number.isFinite(y))node.dataset.positionOffsetY=String(y);
      node.style.left='0px';node.style.top='0px';
    }
    const stacking=contract.stacking||{};
    const rawZ=Number(stacking.relativeHeight||0);
    node.style.zIndex=stacking.behindDoc?'-1':String(Math.max(1,Math.min(9999,1+Math.round(rawZ/1000000))));
    return node;
  }

  function offsetWithin(node,ancestor){
    let x=0,y=0,current=node;
    while(current&&current!==ancestor){x+=Number(current.offsetLeft||0);y+=Number(current.offsetTop||0);current=current.offsetParent;}
    return{x,y,ok:current===ancestor};
  }

  function resolvePositionedAnchors(root,page,layoutInput={}){
    if(!root||!page)return{resolved:0,unresolved:0};
    const body=root.querySelector?.('.sheet-body');if(!body)return{resolved:0,unresolved:0};
    const layout=Object.assign({},DEFAULT_LAYOUT,layoutInput||{});let resolved=0,unresolved=0;
    for(const item of page.items||[]){
      const contract=item?.extensions?.positioningContract;
      if(!contract||String(contract.mode||'')!=='paragraph-anchored'||positioningRenderMode(contract,item)!=='exact'||!item.id)continue;
      const node=body.querySelector(`[data-book-item-id="${CSS.escape(String(item.id))}"]`);
      const anchorId=String(contract.anchor?.itemId||'');
      const anchor=anchorId?body.querySelector(`[data-book-item-id="${CSS.escape(anchorId)}"]`):null;
      if(!node||!anchor){if(node)node.dataset.positionAnchorUnresolved='1';unresolved++;continue;}
      const pos=offsetWithin(anchor,body);if(!pos.ok){node.dataset.positionAnchorUnresolved='1';unresolved++;continue;}
      const dx=Number(contract.xPx ?? contract.horizontal?.offsetPx ?? 0)||0;
      const dy=Number(contract.yPx ?? contract.vertical?.offsetPx ?? 0)||0;
      const hRel=String(contract.horizontal?.relativeFrom||contract.xAnchor||'paragraph').toLowerCase();
      const vRel=String(contract.vertical?.relativeFrom||contract.yAnchor||'paragraph').toLowerCase();
      const hParagraph=['paragraph','character','line','text'].includes(hRel),vParagraph=['paragraph','character','line','text'].includes(vRel);
      const left=hParagraph?pos.x+dx:(hRel==='page'?dx-Number(layout.pagePaddingLeftPx||0):dx);
      const top=vParagraph?pos.y+dy:(vRel==='page'?dy-Number(layout.pagePaddingTopPx||0):dy);
      node.style.left=`${left}px`;node.style.top=`${top}px`;
      node.dataset.positionResolvedHorizontal=hRel;node.dataset.positionResolvedVertical=vRel;
      delete node.dataset.positionAnchorUnresolved;resolved++;
    }
    return{resolved,unresolved};
  }

  function addAnchor(node, item, ctx, lang){
    if(!node) return node;
    if(!visibleInMode(item,'screen')) node.dataset.screenHidden = '1';
    if(!visibleInMode(item,'print')) node.dataset.printHidden = '1';
    applyItemLayout(node,item);
    applyPositioningContract(node,item,ctx);
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

  function compositeFigureData(item){
    const data=item?.extensions?.compositeFigure;
    return data&&typeof data==='object'&&Array.isArray(data.overlays)?data:null;
  }

  function compositeBaseGeometry(composite){
    const raw=composite?.baseGeometry||{},num=(v,d)=>Number.isFinite(Number(v))?Number(v):d;
    return{x:num(raw.x,0),y:num(raw.y,0),width:Math.max(.005,num(raw.width,1)),height:Math.max(.005,num(raw.height,1)),lockAspect:raw.lockAspect!==false};
  }
  function applyCompositeBaseGeometry(node,composite){
    if(!node||!composite)return node;const g=compositeBaseGeometry(composite);
    node.style.left=`${g.x*100}%`;node.style.top=`${g.y*100}%`;node.style.width=`${g.width*100}%`;node.style.height=`${g.height*100}%`;
    node.dataset.compositeBaseVisual='1';node.dataset.compositeBaseLockAspect=g.lockAspect?'1':'0';return node;
  }

  // HF7: equation content is document content only. Editor chrome/form controls are illegal
  // inside a rendered book page and are stripped instead of being allowed to leak into preview/print.
  function sanitizeEquationMarkup(markup=''){
    const template=document.createElement('template');
    template.innerHTML=String(markup||'');
    const forbidden=[...template.content.querySelectorAll('input,button,select,textarea,option,datalist,[contenteditable]')];
    let stripped=0;
    for(const node of forbidden){
      if(node.matches?.('[contenteditable]')&&!node.matches?.('input,button,select,textarea,option,datalist')){node.removeAttribute('contenteditable');stripped++;continue;}
      node.remove();stripped++;
    }
    return{html:template.innerHTML,stripped};
  }

  function appendCompositeOverlays(frame,item,options={}){
    const composite=compositeFigureData(item);if(!composite)return 0;
    const layer=document.createElement('div');layer.className='composite-overlay-layer';layer.dataset.compositeId=String(composite.id||'');
    let count=0;
    for(const [index,overlay] of composite.overlays.entries()){
      if(overlay?.visible===false)continue;const type=String(overlay?.type||''),g=overlay.geometry||{},clamp=(v,min=0,max=1)=>Math.max(min,Math.min(max,Number(v)||0));
      const node=document.createElement('div');node.className=`composite-overlay ${type==='text'?'composite-text-overlay':'composite-equation-overlay'}`;node.dataset.compositeOverlayId=String(overlay.id||(`${type||'overlay'}-`+(index+1)));node.dataset.compositeOverlayIndex=String(index);node.dataset.compositeOverlayType=type;
      node.style.left=`${clamp(g.x)*100}%`;node.style.top=`${clamp(g.y)*100}%`;node.style.width=`${clamp(g.width,.001)*100}%`;node.style.height=`${clamp(g.height,.001)*100}%`;
      if(type==='equation'){
        if(!String(overlay?.mathml||'').trim())continue;const immutableBase=composite.immutableBase!==false;if(immutableBase||composite.backgroundClean!==true)node.style.background=String(overlay.mask?.fallbackColor||'#FFFFFF');if(Number(overlay.fontSizePx)>0)node.style.fontSize=`${Number(overlay.fontSizePx)}px`;const safe=sanitizeEquationMarkup(overlay.mathml||'');node.innerHTML=safe.html;if(safe.stripped){node.dataset.illegalEditorChromeStripped=String(safe.stripped);console.warn('HF7 stripped illegal editor controls from composite equation',overlay.id||index,safe.stripped);}if(options.preview===true)node.title=String(overlay.plainText||'Επεξεργάσιμη εξίσωση σύνθετου σχήματος');
      }else if(type==='text'){
        const content=document.createElement('div');content.className='composite-text-content';content.innerHTML=richTextToHtml(overlay.body);node.appendChild(content);applyCanonicalStyle(node,overlay.style||{});if(options.preview===true)node.title=String(overlay.plainText||'Επεξεργάσιμο κείμενο σύνθετου σχήματος');
      }else continue;
      layer.appendChild(node);count++;
    }
    if(count)frame.appendChild(layer);return count;
  }

  function appendTableMediaEquations(frame,media,options={}){
    const equations=Array.isArray(media?.equations)?media.equations:[];if(!equations.length)return 0;
    const layer=document.createElement('div');layer.className='composite-overlay-layer table-media-equation-layer';layer.dataset.compositeId=String(media?.sourceCompositeId||'');
    let count=0;
    for(const [index,equation] of equations.entries()){
      if(equation?.visible===false||(!String(equation?.mathml||'').trim()&&!String(equation?.source||'').trim()))continue;
      const g=equation.geometry||{},clamp=(v,min=0,max=1)=>Math.max(min,Math.min(max,Number(v)||0));
      const node=document.createElement('div');node.className='composite-overlay composite-equation-overlay standalone-table-media-equation';node.dataset.tableMediaEquationId=String(equation.id||('eq-'+(index+1)));node.dataset.tableMediaEquationIndex=String(index);
      node.style.left=`${clamp(g.x)*100}%`;node.style.top=`${clamp(g.y)*100}%`;node.style.width=`${clamp(g.width,.001)*100}%`;node.style.height=`${clamp(g.height,.001)*100}%`;
      if(media?.immutableBase!==false||media?.backgroundClean!==true)node.style.background=String(equation.mask?.fallbackColor||'#FFFFFF');
      if(Number(equation.style?.fontSizePx)>0)node.style.fontSize=`${Number(equation.style.fontSizePx)}px`;
      const safe=sanitizeEquationMarkup(equation.mathml||escapeHtml(equation.source||''));node.innerHTML=safe.html;if(safe.stripped){node.dataset.illegalEditorChromeStripped=String(safe.stripped);console.warn('HF7 stripped illegal editor controls from table equation',equation.id||index,safe.stripped);}
      if(options.preview===true)node.title=String(equation.plainText||'Αυτόνομη επεξεργάσιμη εξίσωση μέσα σε πίνακα');
      layer.appendChild(node);count++;
    }
    if(count)frame.appendChild(layer);
    return count;
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
        const composite = compositeFigureData(item);
        const imageTruth=item?.extensions?.imageGeometryTruth&&typeof item.extensions.imageGeometryTruth==='object'?item.extensions.imageGeometryTruth:null;const nativeVector=!!(composite?.nativeVector||imageTruth?.nativeVector);
        node.className = `media ${placementClass(item)}${imageTruth?' docx-image-truth':''}${composite?' composite-media immutable-composite':''}${nativeVector?' docx-native-vector':''}`;
        if(layout.widthPx){node.style.setProperty('--figure-width',`${Number(layout.widthPx)}px`);if(imageTruth)node.style.setProperty('--word-display-width',`${Number(layout.widthPx)}px`);}
        if(imageTruth&&layout.heightPx)node.style.setProperty('--word-display-height',`${Number(layout.heightPx)}px`);
        if(imageTruth){node.dataset.imageGeometrySource=String(imageTruth.geometrySource||'missing');node.dataset.imageGeometryConflict=imageTruth.geometryConflict?'1':'0';node.dataset.imageCropActive=imageTruth.cropActive?'1':'0';}
        const frame = document.createElement('div');
        const aspect = imageTruth?null:mediaAspect(item,'figure');
        frame.className = `media-frame ${aspect ? '' : 'natural'}${imageTruth?' docx-image-truth-frame':''}${composite?' composite-frame immutable-composite-frame':''}`.trim();
        if(imageTruth&&Number(layout.widthPx)>0&&Number(layout.heightPx)>0){frame.style.width='100%';frame.style.height=`${Number(layout.heightPx)}px`;frame.style.aspectRatio='auto';}if(nativeVector){const e=composite?.effectExtentPx||imageTruth?.effectExtentPx||{};frame.style.setProperty('--effect-left',`${Math.max(0,Number(e.left)||0)}px`);frame.style.setProperty('--effect-top',`${Math.max(0,Number(e.top)||0)}px`);frame.style.setProperty('--effect-right',`${Math.max(0,Number(e.right)||0)}px`);frame.style.setProperty('--effect-bottom',`${Math.max(0,Number(e.bottom)||0)}px`);}
        else if(aspect) frame.style.aspectRatio = String(aspect);else if(composite&&Number(layout.widthPx)>0&&Number(layout.heightPx)>0)frame.style.aspectRatio=`${Number(layout.widthPx)}/${Number(layout.heightPx)}`;
        const candidates = (options.imageCandidates || defaultImageCandidates)(item.src || '',item);
        if(candidates.length){
          const image = document.createElement('img');
          image.alt = text('alt') || text('title') || (lang === 'en' ? 'Figure' : 'Εικόνα');
          if(imageTruth){image.className='docx-image-truth-img';image.dataset.wordDisplayWidth=String(Number(layout.widthPx)||'');image.dataset.wordDisplayHeight=String(Number(layout.heightPx)||'');}
          applyImageCandidates(image,candidates,()=>{
            image.remove();
            const placeholder = document.createElement('div');
            placeholder.className = 'media-placeholder';
            placeholder.textContent = lang === 'en' ? 'Image file not found.' : 'Το αρχείο εικόνας δεν βρέθηκε.';
            frame.appendChild(placeholder);
          });
          if(composite){const baseVisual=document.createElement('div');baseVisual.className='composite-base-visual';applyCompositeBaseGeometry(baseVisual,composite);baseVisual.appendChild(image);frame.appendChild(baseVisual);}else frame.appendChild(image);
        }else{
          const placeholder = document.createElement('div');
          placeholder.className = 'media-placeholder';
          placeholder.textContent = lang === 'en' ? 'No image source.' : 'Δεν έχει οριστεί αρχείο εικόνας.';
          frame.appendChild(placeholder);
        }
        appendCompositeOverlays(frame,item,options);
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
        if(printOptions.snapshot === false) frame.dataset.printSnapshot = '0';
        if(printOptions.snapshotTimeoutMs != null) frame.dataset.snapshotTimeoutMs = String(printOptions.snapshotTimeoutMs);
        if(printOptions.snapshotAttempts != null) frame.dataset.snapshotAttempts = String(printOptions.snapshotAttempts);
        if(printOptions.snapshotRetryDelayMs != null) frame.dataset.snapshotRetryDelayMs = String(printOptions.snapshotRetryDelayMs);
        const cropStyle = sceneCropStyle(layout.crop || item.crop);
        const rawSource=item.src ?? item.singleSrc ?? '';
        const source = String((options.sceneSource || (value=>value))(rawSource,item) || '');
        if(source){
          const iframe = document.createElement('iframe');
          const deferred = options.deferScenes === true;
          iframe.loading = deferred ? 'lazy' : 'eager';
          iframe.referrerPolicy = 'no-referrer';
          iframe.allow = 'fullscreen';
          if(cropStyle) iframe.style.cssText += cropStyle;
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
            const observeHtml = calloutListHtml(callout.observeItems || []);
            const conclusionHtml = calloutListHtml(callout.conclusionItems || []);
            const observe = observeHtml ? `<div class="callout-observe"><span class="callout-observe-title">${textWithMath(callout.observeTitle || (lang==='en'?'Observe':'Παρατήρησε'))}</span>${observeHtml}</div>` : '';
            const conclusion = conclusionHtml ? `<div class="callout-conclusion"><span class="callout-conclusion-title">${textWithMath(callout.conclusionTitle || (lang==='en'?'Conclusion':'Συμπέρασμα'))}</span>${conclusionHtml}</div>` : '';
            const extras = calloutExtrasHtml(callout.extras, lang);
            return `<section class="callout-sequence-step${index===Number(item.sequenceInitialStep||0)?' active':''}" data-sequence-index="${index}" data-sequence-step="${attrJson(step)}"><div class="callout-title">${textWithMath(callout.title || `${lang==='en'?'Step':'Βήμα'} ${index+1}`)}</div>${setup}${press}${observe}${conclusion}${extras}</section>`;
          }).join('');
          const buttons = steps.map((step,index)=>`<button type="button" class="callout-sequence-dot${index===Number(item.sequenceInitialStep||0)?' active':''}" data-sequence-goto="${index}" title="${escapeHtml(step.title || `${lang==='en'?'Step':'Βήμα'} ${index+1}`)}">${index+1}</button>`).join('');
          node.innerHTML = `<div class="callout-sequence-head"><button type="button" class="callout-sequence-arrow" data-sequence-move="-1" title="${lang==='en'?'Previous':'Προηγούμενο'}">‹</button><div class="callout-sequence-dots">${buttons}</div><button type="button" class="callout-sequence-arrow" data-sequence-move="1" title="${lang==='en'?'Next':'Επόμενο'}">›</button></div><div class="callout-sequence-body">${stepHtml}</div>`;
        }else{
          node.className = 'callout';
          const setup = (item.setupChips || []).length ? `<div class="callout-row"><span class="callout-label">${textWithMath(getLoc(item,'setupLabel',lang==='en'?'Set':'Ρύθμισε',lang))}</span>${item.setupChips.map(value=>`<span class="callout-chip">${textWithMath(value)}</span>`).join('')}</div>` : '';
          const press = (item.pressChips || []).length ? `<div class="callout-row"><span class="callout-label">${textWithMath(getLoc(item,'pressLabel',lang==='en'?'Press':'Πίεσε',lang))}</span>${item.pressChips.map(value=>`<span class="callout-chip">${textWithMath(value)}</span>`).join('')}</div>` : '';
          const observeHtml = calloutListHtml(list('observeItems'));
          const conclusionHtml = calloutListHtml(list('conclusionItems'));
          const observe = observeHtml ? `<div class="callout-observe"><span class="callout-observe-title">${textWithMath(getLoc(item,'observeTitle',lang==='en'?'Observe':'Παρατήρησε',lang))}</span>${observeHtml}</div>` : '';
          const conclusion = conclusionHtml ? `<div class="callout-conclusion"><span class="callout-conclusion-title">${textWithMath(getLoc(item,'conclusionTitle',lang==='en'?'Conclusion':'Συμπέρασμα',lang))}</span>${conclusionHtml}</div>` : '';
          const extras = calloutExtrasHtml(item.extras, lang);
          node.innerHTML = `<div class="callout-title">${textWithMath(getLoc(item,'title',lang==='en'?'Try':'Δοκίμασε',lang))}</div>${setup}${press}${observe}${conclusion}${extras}`;
        }
        break;
      }
      case 'dialogue': {
        node = document.createElement('section');
        node.className = 'book-dialogue';
        applyCanonicalStyle(node,item.style);
        const rows = Array.isArray(item.rows) ? item.rows : [];
        const safeInitial = Math.max(0, Math.min(rows.length-1, Number(item.initialRow)||0));
        const intro = text('intro');
        const rowHtml = rows.length
          ? rows.map((row,index)=>{
            const speaker = String(row?.speaker || (row?.viewer ? `${lang==='en'?'Viewer':'Θεατής'} ${row.viewer}` : '')).trim();
            const left = dialogueCellHtml(row?.left, lang === 'en' ? 'No comment' : 'Χωρίς σχόλιο');
            const right = dialogueCellHtml(row?.right, '');
            return `<div class="dialogue-row${index===safeInitial?' active':''}" data-dialogue-row="${index}"><div class="dialogue-cell dialogue-left">${speaker?`<div class="dialogue-speaker">${escapeHtml(speaker)}</div>`:''}<div class="dialogue-text">${left}</div></div><div class="dialogue-cell dialogue-right">${right}</div></div>`;
          }).join('')
          : `<div class="dialogue-empty-block">${lang === 'en' ? 'No dialogue rows.' : 'Δεν υπάρχουν ατάκες στη συζήτηση.'}</div>`;
        node.innerHTML = `${text('title')?`<h3 class="dialogue-title">${dialogueMarkupHtml(text('title'))}</h3>`:''}${intro?`<p class="dialogue-intro">${dialogueMarkupHtml(intro)}</p>`:''}<div class="dialogue-table">${rowHtml}</div>`;
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
        const docxParagraph=!!item?.extensions?.docxListParagraph;
        node.className=`book-list${docxParagraph?' docx-list-paragraph':''}`;
        if(item.ordered && Number.isFinite(Number(item.start))) node.start=Number(item.start);
        applyCanonicalStyle(node,item.style);
        (item.items||[]).forEach(entry=>{
          const li=document.createElement('li'),marker=String(entry?.marker??item?.extensions?.docxVisibleListMarker??item?.extensions?.docxListString??'');
          if(docxParagraph&&marker){
            node.style.listStyle='none';li.style.listStyle='none';li.classList.add('docx-list-entry');
            const markerNode=document.createElement('span');markerNode.className='docx-list-marker';markerNode.setAttribute('aria-hidden','true');markerNode.textContent=marker;if(String(entry?.markerFontFamily||item?.extensions?.docxListMarkerFontFamily||'').trim())markerNode.style.fontFamily=String(entry?.markerFontFamily||item?.extensions?.docxListMarkerFontFamily);
            const content=document.createElement('span');content.className='docx-list-content';content.innerHTML=richTextToHtml(entry?.body);li.append(markerNode,content);
          }else{
            li.innerHTML=richTextToHtml(entry?.body);if(item.ordered&&Number.isFinite(Number(entry?.value)))li.value=Number(entry.value);
          }
          if(Number(entry?.level)>0)li.style.marginLeft=`${Number(entry.level)*1.25}em`;applyCanonicalStyle(li,entry?.style);node.appendChild(li);
        });
        break;
      }
      case 'table': {
        node=document.createElement('table');
        const floatingTable=String(item?.style?.layoutMode||'').toLowerCase()==='floating-around'||String(item?.extensions?.positioningContract?.renderMode||'').toLowerCase()==='flow-wrap';
        node.className=`book-table ${floatingTable?'native-floating-table':'native-flow-table'}`;
        node.dataset.tableLayoutMode=floatingTable?'floating-around':'flow';
        applyCanonicalStyle(node,item.style);
        const widths=Array.isArray(item?.style?.columnWidthsPx)?item.style.columnWidthsPx:[];
        if(widths.length){const colgroup=document.createElement('colgroup');const total=widths.reduce((a,v)=>a+(Number(v)||0),0)||1;widths.forEach(value=>{const col=document.createElement('col');col.style.width=`${((Number(value)||0)/total)*100}%`;colgroup.appendChild(col)});node.appendChild(colgroup);}
        const tbody=document.createElement('tbody');
        (item.rows||[]).forEach((row,rowIndex)=>{
          const tr=document.createElement('tr');if(rowIndex<Number(item?.style?.headerRows||0))tr.dataset.tableHeader='1';
          (row.cells||[]).forEach((cell,cellIndex)=>{
            const td=document.createElement('td');td.dataset.tableRowIndex=String(rowIndex);td.dataset.tableCellIndex=String(cellIndex);if(Number(cell.colspan)>1)td.colSpan=Number(cell.colspan);if(Number(cell.rowspan)>1)td.rowSpan=Number(cell.rowspan);
            const renderedMedia=new Set();
            const buildMedia=(media,mediaIndex)=>{
              const wrap=document.createElement('span');const compositeMedia=Boolean((Array.isArray(media?.equations)&&media.equations.length)||media?.compositeFigure||media?.sourceCompositeId);const tableImageTruth=media?.imageGeometryTruth&&typeof media.imageGeometryTruth==='object'?media.imageGeometryTruth:null,free=media?.tableOverlay?.enabled===true;
              wrap.className=`table-cell-media${tableImageTruth?' docx-image-truth-cell':''}${compositeMedia?' composite-table-media':''}${free?' table-free-overlay':''}`;wrap.dataset.tableMediaIndex=String(mediaIndex);wrap.dataset.tableMediaRow=String(rowIndex);wrap.dataset.tableMediaCell=String(cellIndex);
              if(Number(media.widthPx)>0){wrap.style.width=`${Number(media.widthPx)}px`;if(tableImageTruth)wrap.style.setProperty('--word-display-width',`${Number(media.widthPx)}px`);}if(Number(media.heightPx)>0){wrap.style.height=`${Number(media.heightPx)}px`;if(tableImageTruth)wrap.style.setProperty('--word-display-height',`${Number(media.heightPx)}px`);}
              if(free){const overlayZ=Math.max(1,Number(media.tableOverlay?.zIndex)||5);wrap.style.left=`${Number(media.tableOverlay?.xPx)||0}px`;wrap.style.top=`${Number(media.tableOverlay?.yPx)||0}px`;wrap.style.zIndex=String(overlayZ);wrap.dataset.tableFreeOverlay='1';td.classList.add('has-free-table-overlay');td.style.zIndex=String(Math.max(Number(td.style.zIndex)||0,overlayZ));}
              if(tableImageTruth){wrap.dataset.imageGeometrySource=String(tableImageTruth.geometrySource||'missing');wrap.dataset.imageGeometryConflict=tableImageTruth.geometryConflict?'1':'0';wrap.dataset.imageCropActive=tableImageTruth.cropActive?'1':'0';}
              if(media.compositeFigure?.nativeVector){wrap.classList.add('docx-native-vector-table');const e=media.compositeFigure?.effectExtentPx||tableImageTruth?.effectExtentPx||{};wrap.style.setProperty('--effect-left',`${Math.max(0,Number(e.left)||0)}px`);wrap.style.setProperty('--effect-top',`${Math.max(0,Number(e.top)||0)}px`);wrap.style.setProperty('--effect-right',`${Math.max(0,Number(e.right)||0)}px`);wrap.style.setProperty('--effect-bottom',`${Math.max(0,Number(e.bottom)||0)}px`);}
              const candidates=(options.imageCandidates||defaultImageCandidates)(media.src||'',media);if(candidates.length){const image=document.createElement('img');image.alt=String(media.alt||'Σχήμα πίνακα');if(tableImageTruth)image.className='docx-image-truth-img';applyImageCandidates(image,candidates,()=>{image.remove();wrap.classList.add('missing')});if(media.compositeFigure){const baseVisual=document.createElement('div');baseVisual.className='composite-base-visual';applyCompositeBaseGeometry(baseVisual,media.compositeFigure);baseVisual.appendChild(image);wrap.appendChild(baseVisual);}else wrap.appendChild(image);}
              if(media.compositeFigure)appendCompositeOverlays(wrap,{extensions:{compositeFigure:media.compositeFigure}},options);else if(Array.isArray(media.equations)&&media.equations.length)appendTableMediaEquations(wrap,media,options);
              renderedMedia.add(mediaIndex);return wrap;
            };
            const flow=Array.isArray(cell.inlineMediaFlow)?cell.inlineMediaFlow:[];
            const paragraphFlow=flow.some(token=>token?.type==='paragraph');
            const hasFlowMedia=flow.some(token=>token?.type==='media'||token?.type==='paragraph'&&(token.tokens||[]).some(part=>part?.type==='media'));
            if(hasFlowMedia){
              td.classList.add('has-inline-media-flow');
              if(paragraphFlow){
                for(const token of flow){
                  if(token?.type!=='paragraph')continue;
                  const para=document.createElement('div');para.className='table-cell-paragraph-flow';para.dataset.tableCellParagraph=String(Number(token.paragraphIndex)||0);applyCanonicalStyle(para,token.style);
                  for(const part of token.tokens||[]){
                    if(part?.type==='html'){const span=document.createElement('span');span.className='table-cell-flow-text';span.innerHTML=richTextToHtml(part.body);para.appendChild(span);continue}
                    if(part?.type!=='media')continue;
                    const index=Number(part.mediaIndex),media=cell.media?.[index];if(!media)continue;
                    const overlay=media.tableOverlay||{},space=String(overlay.coordinateSpace||'');
                    if(overlay.enabled===true){
                      if(space==='cell-paragraph'&&(!Number(overlay.paragraphIndex)||Number(overlay.paragraphIndex)===Number(token.paragraphIndex)))para.appendChild(buildMedia(media,index));
                      // Other absolute coordinate systems remain unresolved here
                      // and are appended against the cell in the fallback pass.
                    }else para.appendChild(buildMedia(media,index));
                  }
                  td.appendChild(para);
                }
              }else{
                for(const token of flow){
                  if(token?.type==='html'){const span=document.createElement('span');span.className='table-cell-flow-text';span.innerHTML=richTextToHtml(token.body);td.appendChild(span);}
                  else if(token?.type==='media'){const index=Number(token.mediaIndex),media=cell.media?.[index];if(media&&!media?.tableOverlay?.enabled)td.appendChild(buildMedia(media,index));}
                }
              }
            }else td.innerHTML=richTextToHtml(cell.body);
            for(const [mediaIndex,media] of (cell.media||[]).entries())if(!renderedMedia.has(mediaIndex))td.appendChild(buildMedia(media,mediaIndex));
            applyCellStyle(td,cell.style);tr.appendChild(td);
          });tbody.appendChild(tr);
        });
        node.appendChild(tbody);
        break;
      }
      case 'columns': {
        node=document.createElement('section');
        node.className=`book-columns${item?.extensions?.docxSourceColumns?' docx-source-columns':''}`;
        applyCanonicalStyle(node,item.style);
        const regions=Array.isArray(item.regions)?item.regions.filter(region=>Array.isArray(region?.items)&&region.items.length):[];
        const equalSourceColumns=!!item?.extensions?.docxEqualColumns;
        if(equalSourceColumns&&regions.length)node.style.gridTemplateColumns=`repeat(${regions.length},minmax(0,1fr))`;
        else{const widths=regions.map(region=>region.widthPx?cssLength(region.widthPx):'minmax(0,1fr)');if(widths.length)node.style.gridTemplateColumns=widths.join(' ');}
        if(item?.style?.columnGapPx!==undefined)node.style.gap=cssLength(item.style.columnGapPx);
        regions.forEach((region,regionIndex)=>{
          const col=document.createElement('div');
          col.className=`book-column book-column-${escapeHtml(region.role||`col-${regionIndex+1}`)}`;
          if(region.widthPx&&!equalSourceColumns)col.style.width=cssLength(region.widthPx);
          (region.items||[]).forEach((child,childIndex)=>{
            const childNode=renderItem(child,{page:context?.page,pageIndex:context?.pageIndex,itemIndex:childIndex},options);
            if(child?.id)childNode.dataset.bookItemId=String(child.id);
            childNode.dataset.bookItemType=String(child?.type||'unknown');
            childNode.dataset.bookColumnRole=String(region.role||'');
            col.appendChild(childNode);
          });
          node.appendChild(col);
        });
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
    if(!visibleInMode(page,'screen')) wrap.dataset.screenHidden = '1';
    if(!visibleInMode(page,'print')) wrap.dataset.printHidden = '1';
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

      const calloutContext = {page,pageIndex,itemIndex:calloutIndex,layout};
      const sceneContext = {page,pageIndex,itemIndex:sceneIndex,layout};
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
      const pairScene = isGeneratedSequencePair(item, nextItem) ? nextItem : (linkedSceneId && nextItem?.type === 'scene' && String(nextItem.id || '') === linkedSceneId ? nextItem : null);
      const pairMode = pairScene ? sequencePairMode(item, pairScene) : '';
      if(pairMode){
        appendSequencePair(pairMode, item, pairScene, itemIndex, itemIndex+1);
        itemIndex++;
        continue;
      }
      const context = {page,pageIndex,itemIndex,layout};
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
    pages.forEach((page,index)=>{const node=renderPageNode(data,page,index,options);host.appendChild(node);resolvePositionedAnchors(node,page,data?.layoutDefaults||{});});
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
    const stats={schema:String(data?.schemaVersion||''),pages:0,items:0,figures:0,figuresWithCaptions:0,figuresWithAssets:0,docxImageTruthFigures:0,imageGeometryConflicts:0,imageCropActive:0,imageMissingDisplayExtent:0,scenes:0,scenesWithSources:0,richTextBlocks:0,layoutItems:0};
    const warnings=[...validation.warnings];
    (data?.pages||[]).forEach((page,pageIndex)=>{
      stats.pages++;
      (page.items||[]).forEach((item,itemIndex)=>{
        stats.items++;
        if(['paragraph','note','side_note'].includes(item.type)){
          if(item.body?.format==='rich-text-v1') stats.richTextBlocks++;
        }
        if(item.layout) stats.layoutItems++;
        if(item.type==='figure'){
          stats.figures++;
          if(String(item.caption||item.title||'').trim()) stats.figuresWithCaptions++;
          if(String(item.src||'').trim()) stats.figuresWithAssets++;
          const truth=item?.extensions?.imageGeometryTruth;if(truth&&typeof truth==='object'){stats.docxImageTruthFigures++;if(truth.geometryConflict)stats.imageGeometryConflicts++;if(truth.cropActive)stats.imageCropActive++;if(!(Number(item?.layout?.widthPx)>0&&Number(item?.layout?.heightPx)>0)){stats.imageMissingDisplayExtent++;warnings.push(`Σελίδα ${pageIndex+1}, figure ${itemIndex+1}: λείπει Word display extent.`);}}
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
    positioningRenderMode,
    resolvePositionedAnchors,
    sequenceSceneUrl,
    expandScreenSequences,
    expandPrintSequences,
    bindCalloutSequences,
    auditData
  });
})(window);
