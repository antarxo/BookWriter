(function installBookSnapshotTransport(root){
  'use strict';

  const PATH = 'book-scene-v1';
  const LEGACY_PATH = 'book-scene-legacy';
  const PATHS = Object.freeze({ BOOK_SCENE_V1:PATH, LEGACY:LEGACY_PATH });
  const SUPPORTED_PATHS = Object.freeze([PATH, LEGACY_PATH]);

  function now(){
    return root.performance?.now?.() ?? Date.now();
  }

  function makeRequestId(){
    return `book_snapshot_${Date.now()}_${Math.random().toString(36).slice(2)}`;
  }

  function failure(attempts, reason='no-snapshot'){
    return {
      requestedPath:PATH,
      executedPath:null,
      attempts,
      result:'failure',
      reason,
      fallbackUsed:false,
      dataUrl:null
    };
  }

  function isImageDataUrl(value){
    return typeof value === 'string' && /^data:image\//i.test(value);
  }

  async function capture(iframe, options={}){
    const started = now();
    const win = iframe?.contentWindow;
    if(!win){
      return failure([{
        path:PATH,
        result:'failure',
        reason:'iframe-unavailable',
        durationMs:Math.round(now()-started)
      }], 'iframe-unavailable');
    }

    if(options.path && options.path !== PATH){
      return failure([{
        path:String(options.path),
        result:'failure',
        reason:'unsupported-path',
        durationMs:Math.round(now()-started)
      }], 'unsupported-path');
    }

    const requestId = options.requestId || makeRequestId();
    const timeoutMs = Math.max(250, Number(options.timeoutMs) || 4800);

    return await new Promise(resolve=>{
      let settled = false;
      let legacyTimer = null;
      const finish = (dataUrl, reason=null, message=null, path=PATH)=>{
        if(settled) return;
        settled = true;
        root.removeEventListener('message', onMessage);
        clearTimeout(timer);
        clearTimeout(legacyTimer);
        const valid = isImageDataUrl(dataUrl);
        const attempt = {
          path,
          result:valid ? 'success' : 'failure',
          reason:valid ? null : (reason || 'invalid-snapshot'),
          responseType:message?.type || null,
          telemetryPath:message?.executedPath || null,
          meta:message?.meta || null,
          durationMs:Math.round(now()-started)
        };
        if(!valid){
          resolve(failure([attempt], attempt.reason));
          return;
        }
        resolve({
          requestedPath:PATH,
          executedPath:path,
          attempts:[attempt],
          result:'success',
          reason:null,
          fallbackUsed:path !== PATH,
          meta:message?.meta || null,
          dataUrl
        });
      };
      const onMessage = event=>{
        if(event.source !== win) return;
        const message = event.data;
        if(!message || message.requestId !== requestId) return;
        if(message.type === 'book-scene-snapshot-result'){
          finish(message.dataUrl || null, 'empty-snapshot', message, LEGACY_PATH);
          return;
        }
        if(message.type !== 'book-scene:print-snapshot') return;
        if(message.protocol !== PATH) return;
        if(message.executedPath !== PATH) return;
        finish(message.dataUrl || null, 'empty-snapshot', message, PATH);
      };
      const timer = setTimeout(()=>finish(null, 'timeout'), timeoutMs);
      root.addEventListener('message', onMessage);
      try{
        win.postMessage({
          type:'book-scene:capture-print-snapshot',
          protocol:PATH,
          requestId
        }, '*');
        legacyTimer = setTimeout(()=>{
          if(settled) return;
          try{
            win.postMessage({
              type:'book-scene-snapshot',
              requestId
            }, '*');
          }catch(_error){}
        }, Math.min(350, Math.max(80, timeoutMs / 6)));
      }catch(error){
        finish(null, 'post-message-error', {
          error:String(error?.message || error)
        });
      }
    });
  }

  root.BookSnapshotTransport = Object.freeze({
    PATHS,
    SUPPORTED_PATHS,
    capture
  });
})(window);
