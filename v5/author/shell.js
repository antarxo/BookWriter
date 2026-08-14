(function(){
'use strict';
const frame=document.getElementById('authorFrame');
const captureButton=document.getElementById('capture');
const baseInput=document.getElementById('bookBase');
const status=document.getElementById('status');

function setStatus(text){status.textContent=text;}
function frameDocument(){
  try{return frame.contentDocument||frame.contentWindow?.document||null;}
  catch(_e){return null;}
}
function previewHost(){return frameDocument()?.querySelector?.('#bookPreviewPages')||null;}
function inferredBase(){
  const raw=String(baseInput.value||'').trim();
  if(raw){try{return new URL(raw,location.href).href;}catch{return raw;}}
  const firstPersistent=previewHost()?.querySelector?.('img[src]:not([src^="blob:"]),iframe[src]:not([src^="blob:"])');
  if(firstPersistent){
    try{return new URL('.',firstPersistent.src).href;}catch(_e){}
  }
  return location.href;
}
async function canonicalBookFor(baseHref){
  try{
    const url=new URL('book.json',baseHref).href;
    const response=await fetch(url,{cache:'no-store'});
    if(!response.ok) return null;
    return await response.json();
  }catch(_e){return null;}
}
async function capture(){
  const host=previewHost();
  if(!host){setStatus('Δεν υπάρχει ακόμη rendered preview. Άνοιξε βιβλίο στον Author.');return;}
  const baseHref=inferredBase();
  setStatus('καταγραφή…');
  try{
    const canonicalBook=await canonicalBookFor(baseHref);
    const snapshot=await SnapshotAuthorBridgeV5.capturePreview({
      host,
      cssUrl:'../../core/book-core.css',
      baseHref,
      canonicalBook,
      title:frameDocument()?.title||'BookWriter rendered snapshot',
      meta:{shell:'v5-author',sourceAuthor:'4.5.0-rc1',canonicalBookLoaded:!!canonicalBook}
    });
    RenderSnapshotV5.download(snapshot,'book.rendered.html');
    const assetState=canonicalBook?'canonical assets':'χωρίς canonical asset map';
    setStatus(`${snapshot.meta.pageCount} σελίδες · ${Math.round(snapshot.documentHtml.length/1024)} KB · ${assetState}`);
  }catch(error){console.error(error);setStatus('ΣΦΑΛΜΑ: '+(error?.message||error));}
}
frame.addEventListener('load',()=>setStatus('Author 4.5 έτοιμος · άνοιξε βιβλίο και πάτησε Καταγραφή rendered HTML'));
captureButton.addEventListener('click',capture);
})();
