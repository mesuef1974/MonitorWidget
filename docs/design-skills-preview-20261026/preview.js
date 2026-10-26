'use strict';
(()=>{
 const routes=window.PREVIEW_ROUTES,root=document.documentElement,$=id=>document.getElementById(id),media=matchMedia('(prefers-color-scheme: dark)');
 const state={route:'home',section:0,page:0,mode:'light',palette:'graphite'},groups=[['الرئيسية',['home','health','alerts','advice']],['الأداء',['cpu','mem','gpu','procs','wsl','power']],['الصيانة',['space','io','startup','tasks','rules']],['السجل والإعدادات',['history','dbx','ctrl','appearance']]];
 const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let lastWheel=0;
 function notify(text){$('notice').textContent='معاينة فقط؛ لم يُشغّل أمر. '+text;$('notice').hidden=false;}
 function current(){return routes.find(r=>r.id===state.route)||routes[0]}
 function capacity(){const cols=innerWidth>=1500?3:innerWidth>=1000?2:1,rows=innerHeight>=740?2:1;return {cols,rows,count:cols*rows}}
 function paletteTools(){return ['teal','blue','violet','graphite'].map((p,i)=>`<button class="button" data-palette="${p}" aria-pressed="${p===state.palette}">${['أخضر بحري','أزرق','بنفسجي','رمادي'][i]}</button>`).join('')}
 function panel(card,index){
  const rows=card.rows.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('');
  const chart=card.chart?`<div class="compact-chart" data-chart="${card.chart}" data-last="${esc(card.rows[0][1])}" role="img" aria-label="رسم توضيحي لعينة ${esc(card.title)}"></div><span class="chart-caption">${card.chart==='memory'?'فعلية: خط متصل · التزام: متقطع · %':'اتجاه توضيحي للمعاينة؛ الوحدة والقيمة الحالية في القراءات'}</span>`:'';
  const tools=card.kind==='appearance'?`<div class="local-colors">${paletteTools()}</div>`:card.kind==='opacity'?'<label class="opacity-control">شفافية السطح <input id="widgetOpacity" type="range" min="70" max="100" value="95"><output id="opacityValue">95%</output></label>':'';
  return `<article class="content-panel" data-panel="${index}"><header><h2>${esc(card.title)}</h2></header>${chart}<dl class="compact-pairs">${rows}</dl>${tools}<div class="card-actions">${card.actions.map(a=>`<button class="button" data-action="${esc(a)}">${esc(a)}</button>`).join('')}</div></article>`;
 }
 function render(){
  const widget=state.route==='widget';$('appPreview').hidden=widget;$('widgetPreview').hidden=!widget;
  $('routeNav').innerHTML=groups.map(([name,ids])=>`<div class="nav-category"><h2>${name}</h2><div>${ids.map(id=>`<button class="rail-link ${id===state.route?'active':''}" data-route="${id}" aria-current="${id===state.route?'page':'false'}">${routes.find(r=>r.id===id).title}</button>`).join('')}</div></div>`).join('');
  if(widget){drawCharts();return}
  const route=current(),section=route.sections[state.section],cap=capacity(),pages=Math.max(1,Math.ceil(section.cards.length/cap.count));state.page=Math.min(state.page,pages-1);
  $('pageTitle').textContent=route.title;$('pageDescription').textContent=route.description;
  $('subNav').innerHTML=route.sections.map((s,i)=>`<button class="button ${i===state.section?'primary':''}" data-section="${i}" aria-pressed="${i===state.section}">${s.title}</button>`).join('');
  const cards=section.cards.slice(state.page*cap.count,(state.page+1)*cap.count);$('content').innerHTML=cards.map((c,i)=>panel(c,state.page*cap.count+i)).join('');
  $('content').style.gridTemplateColumns=`repeat(${Math.min(cap.cols,cards.length)},minmax(0,1fr))`;$('content').style.gridTemplateRows=`repeat(${Math.ceil(cards.length/cap.cols)},minmax(0,1fr))`;
  $('sectionName').textContent=section.title+' · '+section.cards.length+' بطاقات';$('pageCount').textContent=`${state.page+1} من ${pages}`;$('previousPage').disabled=state.page===0;$('nextPage').disabled=state.page===pages-1;
  drawCharts();
 }
 function go(id,section=0){if(id!=='widget'&&!routes.some(r=>r.id===id))return;state.route=id;state.section=section;state.page=0;render()}
 function page(d){if(state.route==='widget')return false;const max=Math.ceil(current().sections[state.section].cards.length/capacity().count)-1,n=Math.max(0,Math.min(max,state.page+d));if(n===state.page)return false;state.page=n;render();return true}
 function appearance(){root.dataset.mode=state.mode;root.dataset.theme=state.mode==='auto'?(media.matches?'dark':'light'):state.mode;root.dataset.palette=state.palette;document.querySelectorAll('button[data-palette]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.palette===state.palette)))}
 function drawCharts(){document.querySelectorAll('[data-chart]').forEach(host=>{if(host.closest('[hidden]'))return;const memory=host.dataset.chart==='memory',last=memory?73:Number((host.dataset.last||'22').match(/[0-9]+(?:\.[0-9]+)?/)?.[0]||22),max=memory?100:Math.max(1,Math.ceil(last*1.4)),bottom=67,top=8,y=v=>bottom-(v/max)*(bottom-top),x=i=>28+i*17;const shape=[.65,.68,.63,.72,.7,.76,.74,.81,.78,.84,.82,.88,.86,.9,.87,.94,.91,.98,1.02,1],path=target=>shape.map((v,i)=>(i?'L':'M')+x(i)+' '+y(target*v).toFixed(1)).join(' '),ticks=[0,max/2,max];host.innerHTML=`<svg viewBox="0 0 360 90" preserveAspectRatio="none" aria-hidden="true">${ticks.map(v=>`<path class="grid-line" d="M28 ${y(v)}H352"/><text x="1" y="${y(v)+4}">${Number(v.toFixed(1))}</text>`).join('')}<path class="ram-path" d="${path(last)}"/>${memory?`<path class="commit-path" d="${path(52)}"/>`:''}<text x="28" y="87">قبل 30 د</text><text x="325" y="87">الآن</text></svg>`})}
 function action(name){
  const links={'أداء المعالج':'cpu','تحليل الذاكرة':'mem','فتح الصحة':'health','فتح الجدولة':'tasks','سجل الإجراءات':'history','سجل التنظيف':'history','فتح مستعرض القاعدة':'dbx','فتح العمليات':'procs','معاينة الويدجت الكامل':'widget','فتح النظرة العامة':'home','فتح سجل النسخ':'history','فتح حدود التنبيه':'alerts'};
  if(links[name]){go(links[name]);return}
  if(name==='استعادة المظهر الافتراضي'){state.mode='auto';state.palette='teal';$('mode').value='auto';appearance();render();notify('استُعيد المظهر داخل المعاينة.');return}
  const dialog=$('detailDialog');$('dialogTitle').textContent=name;$('dialogBody').innerHTML=`<p>هذه محاكاة محلية لخطوة «${esc(name)}».</p><dl class="compact-pairs"><div><dt>العنصر</dt><dd>${esc(current().title)} · بيانات عينة</dd></div><div><dt>الأثر</dt><dd>لن تُعدَّل ملفات أو إعدادات الجهاز.</dd></div></dl><p class="plain-note">يعرض التنفيذ النهائي تفاصيل العنصر والنتيجة أو حقول الإعداد الخاصة بهذا الإجراء. يمكنك هنا مراجعة موضع الإجراء وخطوة التأكيد فقط.</p>`;if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');
 }
 $('tierRows').innerHTML=[['daily','يومي','اليوم 13:00','غدًا 13:00'],['weekly','أسبوعي','الخميس الماضي','الخميس 02:30'],['monthly','شهري','أول الشهر','1 نوفمبر 03:00'],['quarterly','ربع سنوي','أول أكتوبر','1 يناير 04:00']].map(([id,title,last,next])=>`<div class="tier-row"><button class="switch" role="switch" aria-checked="true" aria-label="جدولة التنظيف ${title}" data-tier="${id}"></button><div class="tier-copy"><b>تنظيف ${title}</b><small>آخر: ${last} · نجح</small><small>التالي: ${next}</small></div><button class="button" data-demo="تشغيل ${title}">تشغيل</button></div>`).join('');
 document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.route)go(b.dataset.route);else if(b.dataset.view)go(b.dataset.view==='overview'?'home':b.dataset.view);else if(b.dataset.section!==undefined){state.section=Number(b.dataset.section);state.page=0;render()}else if(b.dataset.palette){state.palette=b.dataset.palette;appearance()}else if(b.dataset.action)action(b.dataset.action);else if(b.dataset.tier){b.setAttribute('aria-checked',String(b.getAttribute('aria-checked')!=='true'));notify('تغيرت الجدولة في المعاينة.')}else if(b.hasAttribute('data-demo'))notify(b.dataset.demo);else if(b.hasAttribute('data-close-dialog')||b.hasAttribute('data-confirm')){const d=$('detailDialog');if(d.close)d.close();else d.removeAttribute('open');if(b.hasAttribute('data-confirm'))notify('تأكيد تجريبي.')}});
 document.addEventListener('input',e=>{if(e.target.id==='widgetOpacity'){const value=Number(e.target.value);$('opacityValue').textContent=value+'%';root.style.setProperty('--widget-opacity',value/100);}});
 $('mode').addEventListener('change',e=>{state.mode=e.target.value;appearance()});$('previousPage').addEventListener('click',()=>page(-1));$('nextPage').addEventListener('click',()=>page(1));
 $('appPreview').addEventListener('wheel',e=>{if(e.ctrlKey||e.metaKey||e.shiftKey||Math.abs(e.deltaY)<=Math.abs(e.deltaX)||e.target.closest('input,select,textarea,button'))return;const now=Date.now();if(now-lastWheel<250||Math.abs(e.deltaY)<4)return;if(page(e.deltaY>0?1:-1)){lastWheel=now;e.preventDefault()}},{passive:false});
 document.addEventListener('keydown',e=>{if(e.target.closest('input,select,textarea')||e.ctrlKey||e.metaKey)return;if(e.key==='PageDown'&&page(1))e.preventDefault();if(e.key==='PageUp'&&page(-1))e.preventDefault()});
 addEventListener('resize',render);media.addEventListener('change',()=>{if(state.mode==='auto')appearance()});
 window.previewReview={state,routes,go,page,capacity,render};const hash=location.hash.slice(1);if(hash)state.route=hash==='overview'?'home':hash;appearance();render();
})();
