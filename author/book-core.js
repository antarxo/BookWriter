(function(global){
  'use strict';

  const VERSION = '0.1.0';
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
    if(!out.schemaVersion) out.schemaVersion = 'pages-v1';
    if(!out.meta || typeof out.meta !== 'object') out.meta = {};
    if(!out.layoutDefaults || typeof out.layoutDefaults !== 'object') out.layoutDefaults = {};
    out.layoutDefaults = Object.assign({}, DEFAULT_LAYOUT, out.layoutDefaults);
    if(!out.nav || typeof out.nav !== 'object') out.nav = {};
    if(!out.nav.mode) out.nav.mode = 'auto';
    if(out.nav.showApp == null) out.nav.showApp = true;
    if(out.nav.showPrint == null) out.nav.showPrint = true;
    if(!Array.isArray(out.nav.groups)) out.nav.groups = [];
    if(!Array.isArray(out.pages)) out.pages = [];
    out.pages.forEach((page, index)=>{
      if(!page || typeof page !== 'object') out.pages[index] = page = {};
      if(!page.id) page.id = `page-${index+1}`;
      if(!page.header || typeof page.header !== 'object') page.header = {left:'',center:'',right:''};
      if(!page.footer || typeof page.footer !== 'object') page.footer = {left:'',center:'',right:'{page}'};
      if(!Array.isArray(page.items)) page.items = [];
    });
    return out;
  }

  function validateData(raw){
    const errors = [];
    const warnings = [];
    if(!raw || typeof raw !== 'object'){
      errors.push('Το αρχείο δεν περιέχει αντικείμενο JSON.');
      return {ok:false, errors, warnings};
    }
    if(raw.schemaVersion && raw.schemaVersion !== 'pages-v1'){
      warnings.push(`Άγνωστη έκδοση σχήματος: ${raw.schemaVersion}.`);
    }
    if(!Array.isArray(raw.pages)){
      errors.push('Λείπει ο πίνακας pages.');
      return {ok:false, errors, warnings};
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
        if(item.type === 'scene' && !item.singleSrc) warnings.push(`Σκηνή χωρίς URL στη ${pageName}, θέση ${itemIndex+1}.`);
        if(item.type === 'figure' && !item.src) warnings.push(`Εικόνα χωρίς αρχείο στη ${pageName}, θέση ${itemIndex+1}.`);
      });
    });
    return {ok:errors.length === 0, errors, warnings};
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
    return String(
      getLoc(item,'navLabel','',lang) ||
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
    const frameWidth = Math.max(120, Number(item?.frameWidth || 340) || 340);
    const heightKey = kind === 'scene' ? 'sceneHeight' : 'figureHeight';
    const explicitHeight = Number(item?.[heightKey] ?? item?.frameHeight ?? item?.height);
    if(Number.isFinite(explicitHeight) && explicitHeight > 0) return frameWidth / explicitHeight;
    const explicitAspect = parseAspect(item?.aspectRatio ?? item?.[`${kind}Aspect`] ?? item?.aspect);
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
    const placement = String(item?.placement || '').trim().toLowerCase();
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

  function addAnchor(node, item, ctx, lang){
    if(!node || !ctx || !isStructuralNavItem(item)) return node;
    const id = itemAnchorId(ctx.page, ctx.pageIndex, item, ctx.itemIndex, lang);
    if(id) node.dataset.navTargetId = id;
    if(item?.showInNav === false) node.dataset.navHidden = '1';
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
        break;
      }
      case 'part_title': {
        node = document.createElement('section');
        node.className = 'part-head';
        node.innerHTML = `${text('label')?`<p class="part-kicker">${text('label')}</p>`:''}${text('title')?`<h2 class="part-title-main">${text('title')}</h2>`:''}`;
        break;
      }
      case 'section_heading': {
        node = document.createElement('h2');
        node.className = 'section-heading';
        node.innerHTML = text('title');
        break;
      }
      case 'paragraph': {
        node = document.createElement('p');
        node.className = 'paragraph';
        node.innerHTML = text('html', options.preview ? `<em>${empty}</em>` : '');
        break;
      }
      case 'note': {
        node = document.createElement('div');
        node.className = 'note';
        node.innerHTML = `${text('label')?`<span class="label">${text('label')}</span>`:''}${text('html', options.preview ? `<em>${empty}</em>` : '')}`;
        break;
      }
      case 'side_note': {
        node = document.createElement('aside');
        node.className = `side-note ${placementClass(item)}`;
        if(item.frameWidth) node.style.setProperty('--figure-width',`${Number(item.frameWidth)}px`);
        node.innerHTML = `${text('label')?`<span class="label">${text('label')}</span>`:''}${text('title')?`<span class="title">${text('title')}</span>`:''}${text('html',options.preview ? `<em>${empty}</em>` : '')}`;
        break;
      }
      case 'figure': {
        node = document.createElement('figure');
        node.className = `media ${placementClass(item)}`;
        if(item.frameWidth) node.style.setProperty('--figure-width',`${Number(item.frameWidth)}px`);
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
        if(item.frameWidth) node.style.setProperty('--figure-width',`${Number(item.frameWidth)}px`);
        const frame = document.createElement('div');
        frame.className = 'media-frame scene-frame';
        frame.style.aspectRatio = String(mediaAspect(item,'scene'));
        const source = String((options.sceneSource || (value=>value))(item.singleSrc || '',item) || '');
        if(source){
          const iframe = document.createElement('iframe');
          iframe.loading = 'eager';
          iframe.referrerPolicy = 'no-referrer';
          iframe.allow = 'fullscreen';
          iframe.dataset.sceneProtocol = 'book-scene-v1';
          iframe.src = source;
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
        node.className = 'callout';
        const setup = (item.setupChips || []).length ? `<div class="callout-row"><span class="callout-label">${text('setupLabel',lang==='en'?'Set':'Ρύθμισε')}</span>${item.setupChips.map(value=>`<span class="callout-chip">${value}</span>`).join('')}</div>` : '';
        const press = (item.pressChips || []).length ? `<div class="callout-row"><span class="callout-label">${text('pressLabel',lang==='en'?'Press':'Πίεσε')}</span>${item.pressChips.map(value=>`<span class="callout-chip">${value}</span>`).join('')}</div>` : '';
        const observe = list('observeItems').length ? `<div class="callout-observe"><span class="callout-observe-title">${text('observeTitle',lang==='en'?'Observe':'Παρατήρησε')}</span><ul>${list('observeItems').map(value=>`<li>${value}</li>`).join('')}</ul></div>` : '';
        node.innerHTML = `<div class="callout-title">${text('title',lang==='en'?'Try':'Δοκίμασε')}</div>${setup}${press}${observe}`;
        break;
      }
      case 'nav_anchor': {
        node = document.createElement('span');
        node.className = options.preview ? 'nav-anchor-preview' : 'nav-anchor nav-anchor-inline';
        if(options.preview) node.textContent = `${lang==='en'?'Menu point':'Σημείο μενού'}: ${itemNavTitle(item,item.id || '—',lang)}`;
        else node.setAttribute('aria-hidden','true');
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
    const totalPages = pages.length || 1;
    const pageNumber = Number(options.pageNumber || pageIndex+1);
    const layout = Object.assign({}, DEFAULT_LAYOUT, data?.layoutDefaults || {});
    const wrap = document.createElement('div');
    wrap.className = `book-page-root sheet-wrap${options.preview ? ' editor-preview-sheet-wrap' : ''}`;
    wrap.id = page?.id || `page-${pageNumber}`;
    applyLayoutVars(wrap,layout);

    const sheet = document.createElement('section');
    sheet.className = 'sheet';
    const inner = document.createElement('div');
    inner.className = 'sheet-inner';
    const header = document.createElement('div');
    header.className = 'sheet-header';
    if(Number(layout.headerFontSize) <= 0) header.classList.add('hidden');
    header.innerHTML = `<div class="l">${replaceTokens(getLoc(page?.header,'left','',lang),{page:pageNumber,pages:totalPages})}</div><div class="c">${replaceTokens(getLoc(page?.header,'center','',lang),{page:pageNumber,pages:totalPages})}</div><div class="r">${replaceTokens(getLoc(page?.header,'right','',lang),{page:pageNumber,pages:totalPages})}</div>`;
    inner.appendChild(header);

    const body = document.createElement('div');
    body.className = 'sheet-body';
    (page?.items || []).forEach((item,itemIndex)=>{
      const context = {page,pageIndex,itemIndex};
      if(!options.preview){
        const anchor = renderItemAnchor(item,context,lang);
        if(anchor) body.appendChild(anchor);
      }
      const node = renderItem(item,context,options);
      if(itemIndex === options.highlightedIndex) node.classList.add('item-highlight');
      body.appendChild(node);
    });
    inner.appendChild(body);

    const footer = document.createElement('div');
    footer.className = 'sheet-footer';
    if(Number(layout.footerFontSize) <= 0) footer.classList.add('hidden');
    const rightValue = layout.showPageNumbers === false ? '' : getLoc(page?.footer,'right','',lang);
    footer.innerHTML = `<div class="l">${replaceTokens(getLoc(page?.footer,'left','',lang),{page:pageNumber,pages:totalPages})}</div><div class="c">${replaceTokens(getLoc(page?.footer,'center','',lang),{page:pageNumber,pages:totalPages})}</div><div class="r">${replaceTokens(rightValue,{page:pageNumber,pages:totalPages})}</div>`;
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

  global.BookCore = Object.freeze({
    VERSION,
    DEFAULT_LAYOUT,
    TEXT_KEYS,
    normalizeData,
    validateData,
    getLoc,
    getLocArray,
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
    renderPages
  });
})(window);
