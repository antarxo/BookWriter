(function(global){
  'use strict';

  const DEFAULT_PREVIEW_SELECTOR = '#bookPreviewPages';
  const DEFAULT_FILE_NAME = 'book.rendered.html';

  async function cssTextFromUrl(url='../../core/book-core.css'){
    const response = await fetch(url,{cache:'no-store'});
    if(!response.ok) throw new Error(`SnapshotAuthorBridgeV5: CSS ${response.status}`);
    return await response.text();
  }

  async function waitForStablePreview(host){
    if(!host) throw new Error('SnapshotAuthorBridgeV5: preview host not found');
    if(global.document?.fonts?.ready) await global.document.fonts.ready;
    await new Promise(resolve=>global.requestAnimationFrame(()=>global.requestAnimationFrame(resolve)));
    return host;
  }

  function previewHost(selector=DEFAULT_PREVIEW_SELECTOR){
    return global.document?.querySelector?.(selector) || null;
  }

  function canonicalItemsById(book){
    const map=new Map();
    for(const page of book?.pages||[]){
      for(const item of page?.items||[]){
        if(item?.id) map.set(String(item.id),item);
      }
    }
    return map;
  }

  function persistentSnapshotHost(host, canonicalBook){
    if(!canonicalBook) return host;
    const clone=host.cloneNode(true);
    const items=canonicalItemsById(canonicalBook);
    clone.querySelectorAll?.('[data-book-item-id]').forEach(node=>{
      const item=items.get(String(node.dataset.bookItemId||''));
      if(!item) return;
      if(item.type==='figure' && String(item.src||'').trim()){
        const image=node.querySelector('img');
        if(image){
          image.setAttribute('src',String(item.src));
          image.removeAttribute('srcset');
        }
      }
      if(item.type==='scene'){
        const raw=String(item.src??item.singleSrc??'').trim();
        const iframe=node.querySelector('iframe');
        const frame=node.querySelector('.scene-frame');
        if(raw && iframe){
          iframe.setAttribute('src',raw);
          iframe.dataset.sceneSource=raw;
        }
        if(raw && frame){
          frame.dataset.sceneSource=raw;
          frame.dataset.sceneBaseSource=raw;
        }
      }
    });
    return clone;
  }

  async function capturePreview(options={}){
    if(!global.RenderSnapshotV5) throw new Error('SnapshotAuthorBridgeV5: RenderSnapshotV5 is not loaded');
    const host = options.host || previewHost(options.selector);
    await waitForStablePreview(host);
    const cssText = options.cssText != null ? String(options.cssText) : await cssTextFromUrl(options.cssUrl || '../../core/book-core.css');
    const baseHref = options.baseHref || global.location?.href || '';
    const snapshotHost=persistentSnapshotHost(host,options.canonicalBook||null);
    return global.RenderSnapshotV5.capture(snapshotHost,{
      cssText,
      baseHref,
      title:options.title || global.document?.title || 'BookWriter rendered snapshot',
      meta:Object.assign({source:'author-preview',bridge:'snapshot-author-bridge-v2',canonicalAssetPaths:!!options.canonicalBook},options.meta||{})
    });
  }

  async function downloadPreviewSnapshot(options={}){
    const snapshot = await capturePreview(options);
    global.RenderSnapshotV5.download(snapshot,options.fileName || DEFAULT_FILE_NAME);
    return snapshot;
  }

  async function writeSnapshotToDirectory(directoryHandle, options={}){
    if(!directoryHandle?.getFileHandle) throw new Error('SnapshotAuthorBridgeV5: invalid directory handle');
    const snapshot = options.snapshot || await capturePreview(options);
    const fileName = options.fileName || DEFAULT_FILE_NAME;
    const fileHandle = await directoryHandle.getFileHandle(fileName,{create:true});
    const writable = await fileHandle.createWritable();
    await writable.write(snapshot.documentHtml);
    await writable.close();
    return {snapshot,fileHandle,fileName};
  }

  global.SnapshotAuthorBridgeV5 = Object.freeze({
    DEFAULT_PREVIEW_SELECTOR,
    DEFAULT_FILE_NAME,
    previewHost,
    persistentSnapshotHost,
    capturePreview,
    downloadPreviewSnapshot,
    writeSnapshotToDirectory
  });
})(typeof window!=='undefined'?window:globalThis);
