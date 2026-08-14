(function(global){
  'use strict';

  const FORMAT = 'bookwriter-render-snapshot-v1';

  function escapeHtml(value){
    return String(value == null ? '' : value)
      .replace(/&/g,'&amp;')
      .replace(/</g,'&lt;')
      .replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;');
  }

  function normalizeBaseHref(value){
    const text = String(value || '').trim();
    if(!text) return '';
    try{return new URL(text, global.location?.href || 'http://localhost/').href;}
    catch(_e){return text;}
  }

  function capture(host, options={}){
    if(!host || typeof host.innerHTML !== 'string') throw new Error('RenderSnapshotV5.capture: invalid host');
    const cssText = String(options.cssText || '');
    const baseHref = normalizeBaseHref(options.baseHref || global.location?.href || '');
    const title = String(options.title || global.document?.title || 'BookWriter snapshot');
    const bodyHtml = host.innerHTML;
    const meta = Object.assign({
      format:FORMAT,
      createdAt:new Date().toISOString(),
      pageCount:host.querySelectorAll?.('.book-page-root,.sheet-wrap')?.length || 0
    }, options.meta || {});

    const documentHtml = [
      '<!doctype html>',
      '<html lang="el">',
      '<head>',
      '<meta charset="utf-8">',
      '<meta name="viewport" content="width=device-width,initial-scale=1">',
      `<title>${escapeHtml(title)}</title>`,
      baseHref ? `<base href="${escapeHtml(baseHref)}">` : '',
      `<meta name="bookwriter-snapshot-format" content="${FORMAT}">`,
      '<style>html,body{margin:0;padding:0;}body{min-height:100vh;}'+cssText+'</style>',
      '</head>',
      '<body>',
      `<main id="bookwriter-render-snapshot" data-snapshot-format="${FORMAT}">${bodyHtml}</main>`,
      `<script type="application/json" id="bookwriter-snapshot-meta">${JSON.stringify(meta).replace(/</g,'\\u003c')}</script>`,
      '</body>',
      '</html>'
    ].join('');

    return Object.freeze({format:FORMAT, meta, bodyHtml, cssText, baseHref, documentHtml});
  }

  function replay(host, snapshot){
    if(!host) throw new Error('RenderSnapshotV5.replay: invalid host');
    const html = typeof snapshot === 'string' ? snapshot : snapshot?.bodyHtml;
    if(typeof html !== 'string') throw new Error('RenderSnapshotV5.replay: invalid snapshot');
    host.innerHTML = html;
    return host.querySelectorAll?.('.book-page-root,.sheet-wrap')?.length || 0;
  }

  function replayDocument(iframe, snapshot){
    if(!iframe) throw new Error('RenderSnapshotV5.replayDocument: invalid iframe');
    const html = typeof snapshot === 'string' ? snapshot : snapshot?.documentHtml;
    if(typeof html !== 'string') throw new Error('RenderSnapshotV5.replayDocument: invalid snapshot');
    iframe.srcdoc = html;
    return html.length;
  }

  function makeFile(snapshot, fileName='book.rendered.html'){
    const html = typeof snapshot === 'string' ? snapshot : snapshot?.documentHtml;
    if(typeof html !== 'string') throw new Error('RenderSnapshotV5.makeFile: invalid snapshot');
    return new File([html], fileName, {type:'text/html;charset=utf-8'});
  }

  function download(snapshot, fileName='book.rendered.html'){
    const file = makeFile(snapshot,fileName);
    const url = URL.createObjectURL(file);
    const a = global.document.createElement('a');
    a.href = url;
    a.download = file.name;
    a.style.display = 'none';
    global.document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    return file;
  }

  global.RenderSnapshotV5 = Object.freeze({FORMAT,capture,replay,replayDocument,makeFile,download});
})(typeof window !== 'undefined' ? window : globalThis);
