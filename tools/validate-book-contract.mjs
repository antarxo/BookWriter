#!/usr/bin/env node
import { readdir, readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(process.cwd());
const booksRoot = path.join(root, 'books');
const failures = [];
const reports = [];
const forbiddenMeta = ['projectId', 'fileName', 'appHref', 'defaultLanguage'];
const forbiddenNav = ['appHref', 'showApp'];

function fail(message){ failures.push(message); }
function present(value){ return typeof value === 'string' && value.trim().length > 0; }

async function walk(dir){
  const out=[];
  for(const entry of await readdir(dir,{withFileTypes:true})){
    const full=path.join(dir,entry.name);
    if(entry.isDirectory()) out.push(...await walk(full));
    else out.push(full);
  }
  return out;
}

let bookDirs=[];
try{
  bookDirs=(await readdir(booksRoot,{withFileTypes:true})).filter(e=>e.isDirectory()).map(e=>e.name).sort();
}catch(error){
  fail(`Λείπει ο φάκελος books/: ${error.message}`);
}

for(const dirName of bookDirs){
  const dir=path.join(booksRoot,dirName);
  const bookPath=path.join(dir,'book.json');
  try{ await stat(bookPath); }catch{ fail(`books/${dirName}: λείπει book.json`); continue; }
  let book;
  try{ book=JSON.parse(await readFile(bookPath,'utf8')); }
  catch(error){ fail(`books/${dirName}/book.json: άκυρο JSON: ${error.message}`); continue; }

  if(book.schemaVersion !== 'pages-v1') fail(`books/${dirName}: schemaVersion !== pages-v1`);
  if(!book.meta || typeof book.meta !== 'object') fail(`books/${dirName}: λείπει meta`);
  else{
    for(const key of ['id','title','version','language']) if(!present(book.meta[key])) fail(`books/${dirName}: λείπει meta.${key}`);
    if(book.meta.id !== dirName) fail(`books/${dirName}: meta.id=${JSON.stringify(book.meta.id)} δεν συμφωνεί με τον φάκελο`);
    if(!['el','en'].includes(book.meta.language)) fail(`books/${dirName}: μη αποδεκτό meta.language`);
    for(const key of forbiddenMeta) if(Object.hasOwn(book.meta,key)) fail(`books/${dirName}: απαγορευμένο meta.${key}`);
  }
  if(book.nav && typeof book.nav === 'object') for(const key of forbiddenNav) if(Object.hasOwn(book.nav,key)) fail(`books/${dirName}: απαγορευμένο nav.${key}`);
  if(!Array.isArray(book.pages) || book.pages.length===0) fail(`books/${dirName}: λείπουν pages`);

  let items=0, scenes=0;
  for(const [pageIndex,page] of (book.pages || []).entries()){
    if(!Array.isArray(page.items)){ fail(`books/${dirName}: page ${pageIndex+1} χωρίς items`); continue; }
    items += page.items.length;
    for(const [itemIndex,item] of page.items.entries()){
      if(item?.type !== 'scene') continue;
      scenes += 1;
      if(!present(item.singleSrc)) fail(`books/${dirName}: scene ${pageIndex+1}.${itemIndex+1} χωρίς singleSrc`);
      else{
        try{
          const url=new URL(item.singleSrc);
          if(!['http:','https:'].includes(url.protocol)) throw new Error('μη δημόσιο URL');
        }catch(error){ fail(`books/${dirName}: scene ${pageIndex+1}.${itemIndex+1} έχει άκυρο URL (${error.message})`); }
      }
    }
  }
  for(const launcher of ['index.html','Editor.html']){
    const launcherPath=path.join(dir,launcher);
    try{
      const text=await readFile(launcherPath,'utf8');
      if(!text.includes('book.json')) fail(`books/${dirName}/${launcher}: δεν δείχνει σε book.json`);
    }catch{ fail(`books/${dirName}: λείπει ${launcher}`); }
  }
  reports.push({id:dirName,pages:book.pages?.length || 0,items,scenes});
}

for(const file of await walk(root)){
  const rel=path.relative(root,file).replaceAll('\\','/');
  if(rel.startsWith('.git/') || rel === 'tools/validate-book-contract.mjs') continue;
  if(path.basename(file) === 'chapter_content.json') fail(`${rel}: απαγορευμένο ιστορικό όνομα αρχείου`);
  if(/\.(?:html|js|mjs|json|md)$/i.test(file)){
    const text=await readFile(file,'utf8');
    if(text.includes('chapter_content.json')) fail(`${rel}: αναφορά σε απαγορευμένο ιστορικό όνομα`);
  }
}

for(const report of reports) console.log(`OK ${report.id}: ${report.pages} pages · ${report.items} items · ${report.scenes} scenes`);
if(failures.length){
  console.error('\nBOOK CONTRACT FAILED');
  failures.forEach(message=>console.error(`- ${message}`));
  process.exit(1);
}
console.log(`\nBOOK CONTRACT OK: ${reports.length} βιβλίο/βιβλία`);
