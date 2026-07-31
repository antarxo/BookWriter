(function(global){
'use strict';
const VERSION='1.0.0';
const NS='http://www.w3.org/1998/Math/MathML';
const GREEK={
  alpha:'α',beta:'β',gamma:'γ',delta:'δ',epsilon:'ε',varepsilon:'ϵ',zeta:'ζ',eta:'η',theta:'θ',vartheta:'ϑ',iota:'ι',kappa:'κ',lambda:'λ',mu:'μ',nu:'ν',xi:'ξ',omicron:'ο',pi:'π',varpi:'ϖ',rho:'ρ',varrho:'ϱ',sigma:'σ',varsigma:'ς',tau:'τ',upsilon:'υ',phi:'φ',varphi:'ϕ',chi:'χ',psi:'ψ',omega:'ω',
  Gamma:'Γ',Delta:'Δ',Theta:'Θ',Lambda:'Λ',Xi:'Ξ',Pi:'Π',Sigma:'Σ',Upsilon:'Υ',Phi:'Φ',Psi:'Ψ',Omega:'Ω'
};
const OPERATORS={
  cdot:'⋅',times:'×',div:'÷',pm:'±',mp:'∓',le:'≤',leq:'≤',ge:'≥',geq:'≥',ne:'≠',neq:'≠',approx:'≈',equiv:'≡',propto:'∝',to:'→',rightarrow:'→',leftarrow:'←',leftrightarrow:'↔',infty:'∞',partial:'∂',nabla:'∇',sum:'∑',prod:'∏',int:'∫',oint:'∮',therefore:'∴',because:'∵',perp:'⟂',parallel:'∥',angle:'∠',degree:'°'
};
const FUNCTIONS=new Set(['sin','cos','tan','cot','sec','csc','sinh','cosh','tanh','ln','log','exp','lim','max','min']);
const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":'&apos;'}[ch]));
const stripMath=value=>String(value||'').replace(/^\s*\$+|\$+\s*$/g,'').trim();
function tokenize(input){
  const source=stripMath(input);const tokens=[];let i=0;
  while(i<source.length){
    const ch=source[i];
    if(/\s/.test(ch)){i++;continue;}
    if(ch==='\\'){
      let j=i+1;while(j<source.length&&/[A-Za-z]/.test(source[j]))j++;
      if(j===i+1&&j<source.length)j++;
      tokens.push({type:'command',value:source.slice(i+1,j)});i=j;continue;
    }
    if('{}_^'.includes(ch)){tokens.push({type:ch,value:ch});i++;continue;}
    if(/[0-9]/.test(ch)||((ch==='.'||ch===',')&&/[0-9]/.test(source[i+1]||''))){
      let j=i+1;while(j<source.length&&/[0-9.,]/.test(source[j]))j++;
      tokens.push({type:'number',value:source.slice(i,j)});i=j;continue;
    }
    if(/[A-Za-zΑ-Ωα-ωΆ-ώϐ-ϖ]/.test(ch)){
      let j=i+1;while(j<source.length&&/[A-Za-zΑ-Ωα-ωΆ-ώϐ-ϖ]/.test(source[j]))j++;
      tokens.push({type:'identifier',value:source.slice(i,j)});i=j;continue;
    }
    if('+-=<>/⋅×·±∓≈≠≤≥→←↔⇔∞∂∇∑∫∏()[]|,:;'.includes(ch)){tokens.push({type:'operator',value:ch});i++;continue;}
    tokens.push({type:'identifier',value:ch});i++;
  }
  return tokens;
}
function parseBody(source){
  const tokens=tokenize(source);let pos=0;
  const peek=()=>tokens[pos];const take=()=>tokens[pos++];
  const node=(tag,content='',attrs='')=>`<${tag}${attrs}>${content}</${tag}>`;
  const row=parts=>node('mrow',parts.join(''));
  function parseGroup(){
    if(peek()?.type==='{'){
      take();const content=parseSequence('}');
      if(peek()?.type!=='}')throw new Error('Λείπει κλείσιμο αγκύλης }');
      take();return content;
    }
    return parseAtom();
  }
  function parseCommand(command){
    if(command==='left'||command==='right')return parseAtom();
    if(command==='frac')return node('mfrac',parseGroup()+parseGroup());
    if(command==='sqrt')return node('msqrt',parseGroup());
    if(command==='paren'){
      return row([node('mo','(',' stretchy="true"'),parseGroup(),node('mo',')',' stretchy="true"')]);
    }
    if(command==='bracket'){
      return row([node('mo','[',' stretchy="true"'),parseGroup(),node('mo',']',' stretchy="true"')]);
    }
    if(command==='text'){
      if(peek()?.type!=='{')throw new Error('Η \\text απαιτεί {...}');
      take();let text='';let depth=1;
      while(pos<tokens.length&&depth){const t=take();if(t.type==='{')depth++;else if(t.type==='}')depth--;if(depth)text+=t.value;}
      if(depth)throw new Error('Λείπει κλείσιμο αγκύλης στη \\text');
      return node('mtext',esc(text));
    }
    if(GREEK[command])return node('mi',GREEK[command]);
    if(OPERATORS[command])return node('mo',OPERATORS[command]);
    if(FUNCTIONS.has(command))return node('mi',esc(command),' mathvariant="normal"');
    if(command==='mathrm'||command==='mathbf'||command==='mathit'){
      const variant=command==='mathbf'?'bold':command==='mathit'?'italic':'normal';
      return node('mstyle',parseGroup(),` mathvariant="${variant}"`);
    }
    return node('mi',esc(command));
  }
  function parseAtom(){
    const t=take();if(!t)return node('mrow','');
    if(t.type==='{'){pos--;return parseGroup();}
    if(t.type==='command')return parseCommand(t.value);
    if(t.type==='number')return node('mn',esc(t.value.replace(',','.')));
    if(t.type==='identifier')return node('mi',esc(t.value));
    if(t.type==='operator'){
      if(t.value==='('||t.value==='['||t.value==='|'){
        const close=t.value==='('?')':t.value==='['?']':'|';
        const content=parseSequence({type:'operator',value:close});
        if(peek()?.type==='operator'&&peek()?.value===close)take();
        else throw new Error(`Λείπει κλείσιμο ${close}`);
        return row([node('mo',esc(t.value),' stretchy="true"'),content,node('mo',esc(close),' stretchy="true"')]);
      }
      if(t.value===')'||t.value===']')return node('mo',esc(t.value),' stretchy="true"');
      return node('mo',esc(t.value));
    }
    if(t.type==='}')throw new Error('Απρόσμενο }');
    return node('mi',esc(t.value));
  }
  function parseScripted(){
    let base=parseAtom(),sub=null,sup=null;
    while(peek()&&(peek().type==='_'||peek().type==='^')){
      const kind=take().type;const script=parseGroup();if(kind==='_')sub=script;else sup=script;
    }
    if(sub&&sup)return node('msubsup',base+sub+sup);
    if(sub)return node('msub',base+sub);
    if(sup)return node('msup',base+sup);
    return base;
  }
  function parseSequence(stop=null){
    const parts=[];
    const isStop=token=>{
      if(!stop||!token)return false;
      if(typeof stop==='string')return token.type===stop;
      return token.type===stop.type&&(!stop.value||token.value===stop.value);
    };
    while(pos<tokens.length&&!isStop(peek()))parts.push(parseScripted());
    return row(parts);
  }
  const body=parseSequence();
  if(pos<tokens.length)throw new Error('Η εξίσωση δεν αναλύθηκε πλήρως.');
  return body;
}
function parse(source,display='inline'){
  const mode=display==='block'?'block':'inline';
  const lines=stripMath(source).split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
  const systemTable=parts=>`<mrow><mo fence="true" stretchy="true">{</mo><mtable columnalign="left">${parts.map(part=>`<mtr><mtd>${parseBody(part)}</mtd></mtr>`).join('')}</mtable></mrow>`;
  if(lines.length===1&&lines[0].includes('||')){
    const cells=lines[0].split(/\s*\|\|\s*/).map(cell=>cell.trim()).filter(Boolean).map(cell=>{
      const sublines=cell.split(/\s*;\s*/).map(part=>part.trim()).filter(Boolean);
      const content=sublines.length>1
        ? systemTable(sublines)
        : parseBody(cell);
      return `<mtd>${content}</mtd>`;
    }).join('');
    return `<math xmlns="${NS}" display="${mode}"><mtable columnalign="left center left center left"><mtr>${cells}</mtr></mtable></math>`;
  }
  if(lines.length===1&&lines[0].includes(';')){
    const parts=lines[0].split(/\s*;\s*/).map(part=>part.trim()).filter(Boolean);
    if(parts.length>1)return `<math xmlns="${NS}" display="${mode}">${systemTable(parts)}</math>`;
  }
  if(lines.length>1){
    const table=lines.map(line=>`<mtr><mtd>${parseBody(line)}</mtd></mtr>`).join('');
    return `<math xmlns="${NS}" display="${mode}"><mtable columnalign="left">${table}</mtable></math>`;
  }
  const body=parseBody(source);
  return `<math xmlns="${NS}" display="${mode}">${body}</math>`;
}
function validate(source){try{return{ok:true,mathml:parse(source)}}catch(error){return{ok:false,error:error.message,mathml:''}}}
const templates=[
  {label:'Κλάσμα',insert:'\\frac{a}{b}'},{label:'Ρίζα',insert:'\\sqrt{x}'},{label:'Δείκτης',insert:'x_{i}'},{label:'Εκθέτης',insert:'x^{2}'},
  {label:'()',insert:'\\paren{x}'},{label:'()²',insert:'\\paren{x}^{2}'},{label:'[]',insert:'\\bracket{x}'},
  {label:'x/A',insert:'\\frac{x}{A}'},{label:'υ/ωA',insert:'\\frac{υ}{\\omega A}'},{label:'x²/A²',insert:'\\frac{x^{2}}{A^{2}}'},{label:'υ²/ω²A²',insert:'\\frac{υ^{2}}{\\omega^{2} A^{2}}'},
  {label:'ημ',insert:'\\text{ημ}(\\omega t + φ_{0})'},{label:'συν',insert:'\\text{συν}(\\omega t + φ_{0})'},
  {label:'ημ²',insert:'\\text{ημ}^{2}(\\omega t + φ_{0})'},{label:'συν²',insert:'\\text{συν}^{2}(\\omega t + φ_{0})'},
  {label:'ΑΑΤ x',insert:'x=A\\text{ημ}(\\omega t + φ_{0})'},{label:'ΑΑΤ υ',insert:'υ=\\omega A\\text{συν}(\\omega t + φ_{0})'},
  {label:'γραμμή',insert:'\n'},{label:'ΑΑΤ ζεύγος',insert:'x=A\\text{ημ}(\\omega t + φ_{0})\nυ=\\omega A\\text{συν}(\\omega t + φ_{0})'},
  {label:'στήλη ||',insert:' || '},{label:'σύστημα ;',insert:' ; '},
  {label:'{ σύστημα',insert:'α=-\\omega^{2} A\\text{ημ}(\\omega t + φ_{0})\\text{(1)} ; υ=\\omega A\\text{συν}(\\omega t + φ_{0})\\text{(2)}'},
  {label:'ΑΑΤ σύστημα',insert:'x=A\\text{ημ}(\\omega t + φ_{0}) ; υ=\\omega A\\text{συν}(\\omega t + φ_{0}) || ⇔ \\text{ως προς ημ και συν} || \\text{ημ}(\\omega t + φ_{0})=\\frac{x}{A} ; \\text{συν}(\\omega t + φ_{0})=\\frac{υ}{\\omega A}'},
  {label:'ΑΑΤ τετρ.',insert:'\\text{ημ}^{2}(\\omega t + φ_{0}) = \\frac{x^{2}}{A^{2}}\n\\text{συν}^{2}(\\omega t + φ_{0}) = \\frac{υ^{2}}{\\omega^{2} A^{2}}'},
  {label:'ΑΑΤ άθρ.',insert:'\\text{ημ}^{2}(\\omega t + φ_{0}) + \\text{συν}^{2}(\\omega t + φ_{0}) = \\frac{x^{2}}{A^{2}} + \\frac{υ^{2}}{\\omega^{2} A^{2}}'},
  {label:'Άθροισμα',insert:'\\sum_{i=1}^{n}'},{label:'Ολοκλήρωμα',insert:'\\int_{a}^{b}'},{label:'Διάνυσμα',insert:'\\mathbf{E}'},
  {label:'α',insert:'\\alpha'},{label:'β',insert:'\\beta'},{label:'γ',insert:'\\gamma'},{label:'δ',insert:'\\delta'},{label:'Δ',insert:'\\Delta'},
  {label:'λ',insert:'\\lambda'},{label:'μ',insert:'\\mu'},{label:'π',insert:'\\pi'},{label:'φ',insert:'φ'},{label:'φ₀',insert:'φ_{0}'},{label:'ω',insert:'\\omega'},
  {label:'Α',insert:'A'},{label:'υ',insert:'υ'},{label:'θ',insert:'\\theta'},{label:'Σ',insert:'\\Sigma'},
  {label:'±',insert:'\\pm'},{label:'×',insert:'\\times'},{label:'⋅',insert:'\\cdot'},{label:'→',insert:'\\to'},{label:'⇔',insert:'⇔'},{label:'∞',insert:'\\infty'},
  {label:'κείμενο',insert:'\\text{κείμενο}'},{label:'μονάδα',insert:'\\text{μονάδα}'}
];
const api={VERSION,tokenize,sourceToMathML:parse,validate,templates};
global.MathExpressionV4=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
