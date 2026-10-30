'use strict';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {user:null, csrf:'', tab:'dashboard', kind:'place', data:null, auth:'login', selected:null, recovery:null};
let refreshTimer, toastTimer;
const brand = '<div class="brand"><span class="logo">▥</span><span class="brand-text">학원 랭크</span><small>NOTE</small></div>';
const statuses = {queued:'대기 중',running:'수집 중',succeeded:'확인 완료',partial:'일부 수집 실패',failed:'수집 실패'};
function toast(text) {
  $('#toast').textContent=text; $('#toast').style.display='block';
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').style.display='none',4000);
}
async function api(path,p) {
  const response=await fetch('/api/'+path,{method:p===undefined?'GET':'POST',headers:p===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:p===undefined?undefined:JSON.stringify(p)});
  const d=await response.json();
  if(!response.ok) {
    if(response.status===401 && state.user) {state.user=null; renderAuth();}
    throw Error(d.error || '요청에 실패했습니다.');
  }
  return d;
}
function date(s) {return s?new Date(s).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):'아직 없음';}
function day(s) {return s?new Date(s).toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'long',day:'numeric'}):'기록 없음';}
function url(token) {return location.origin+'/s/'+token;}
async function copy(text) {
  try {await navigator.clipboard.writeText(text); toast('복사했습니다.');}
  catch {window.prompt('아래 내용을 선택해 복사해 주세요.',text);}
}
function bind(action,fn) {
  document.querySelectorAll(`[data-action="${action}"]`).forEach(el=>el.addEventListener('click',async e=>{
    e.preventDefault(); el.disabled=true;
    try {await fn(el);} catch(err) {toast(err.message);} finally {el.disabled=false;}
  }));
}
function fields(form) {return Object.fromEntries(new FormData(form).entries());}
function heading(kicker,title,sub,buttons='') {
  return `<div class="page-heading"><div><div class="eyebrow">${esc(kicker)}</div><h1>${esc(title)}</h1><p>${esc(sub)}</p></div><div class="actions">${buttons}</div></div>`;
}
function showRecovery(code) {
  clearTimeout(refreshTimer); state.recovery=code;
  $('#app').innerHTML=`<main class="recovery-page">${brand}<section class="card"><div class="card-body"><div class="eyebrow">FOR YOUR ACCOUNT</div><h1>복구 코드를 보관해 주세요</h1><p>비밀번호를 잊었을 때 이 코드로 직접 계정을 복구할 수 있습니다. 계정 소유자에게 한 번만 표시합니다.</p><div class="recovery-code">${esc(code)}</div><div class="actions"><button data-action="copy-recovery">코드 복사</button><button data-action="download-recovery">복구 안내 저장</button></div><p class="small muted">다른 사람에게 전달하지 말고 비밀번호 관리자 등 안전한 곳에 보관하세요. 비밀번호와 복구 코드를 모두 잃으면 서비스 운영자에게 복구를 요청할 수 없습니다.</p><button class="primary full" data-action="continue">보관했어요 · 내 순위판으로 →</button></div></section></main>`;
  bind('copy-recovery',()=>copy(code));
  bind('download-recovery',async()=>{
    const blob=new Blob([`학원 랭크 계정 복구 안내\n접속 주소: ${location.origin}\n이메일: ${state.user.email}\n복구 코드: ${code}\n\n로그인 화면의 '비밀번호를 잊었어요'에서 이메일·복구 코드·새 비밀번호를 입력하세요.\n이 코드를 아는 사람은 계정 비밀번호를 바꿀 수 있습니다. 안전하게 보관하세요.\n`],{type:'text/plain;charset=utf-8'});
    const link=document.createElement('a'); link.href=URL.createObjectURL(blob); link.download='학원랭크-계정복구.txt'; link.click(); URL.revokeObjectURL(link.href);
  });
  bind('continue',async()=>{state.recovery=null;state.tab='dashboard';await load();});
}
function renderAuth() {
  clearTimeout(refreshTimer);
  const reg=state.auth==='register',recover=state.auth==='recover';
  $('#app').innerHTML=`<main class="auth"><section class="auth-art">${brand}<div><div class="eyebrow">YOUR WEEKLY RANKING NOTE</div><h1>우리 학원의 변화를<br>내 손으로, 매주.</h1><p>직접 키워드를 넣고 순위를 기록하세요.<br>보여주고 싶은 결과만 링크로 나눌 수 있어요.</p></div><div class="orb"></div><div class="auth-note">나만의 키워드 · 주간 순위 기록 · 결과 링크 공유</div></section><section class="auth-panel"><div class="eyebrow">ACADEMY RANK</div><h2>${recover?'계정 복구':reg?'나만의 순위 기록장':'반갑습니다, 원장님'}</h2><p class="muted small">${recover?'보관한 복구 코드로 새 비밀번호를 설정하세요.':'우리 학원 순위를 확인하고, 필요한 결과만 공유하세요.'}</p><div class="tabs"><button data-auth="login" class="${!reg&&!recover?'active':''}">로그인</button><button data-auth="register" class="${reg?'active':''}">회원가입</button></div><form id="auth-form">${reg?'<div class="field"><label for="username">이름</label><input id="username" name="name" required maxlength="60" autocomplete="name" placeholder="홍길동 원장님"></div>':''}<div class="field"><label for="email">이메일</label><input id="email" name="email" type="email" required maxlength="200" autocomplete="username" placeholder="hello@academy.kr"></div>${recover?'<div class="field"><label for="recovery">복구 코드</label><input id="recovery" name="recovery_code" required autocomplete="off" placeholder="가입할 때 보관한 복구 코드"></div>':''}<div class="field"><label for="password">${recover?'새 비밀번호':'비밀번호'}</label><input id="password" name="password" type="password" required minlength="10" maxlength="200" autocomplete="${reg||recover?'new-password':'current-password'}" placeholder="10자 이상 입력해 주세요"></div>${reg?'<p class="small muted">이메일·계정 정보와 학원 설정·순위 기록은 내 계정에 저장됩니다. 다른 회원의 관리 화면은 열 수 없으며, 공유는 기본적으로 꺼져 있습니다.</p>':''}<div id="form-error" class="small danger" role="alert"></div><button class="primary full" type="submit">${recover?'계정 복구하기':reg?'가입하고 시작하기':'로그인'} →</button></form><button class="quiet back" data-auth="${recover?'login':'recover'}">${recover?'로그인으로 돌아가기':'비밀번호를 잊었어요'}</button><p class="muted small">결과 공유 링크는 로그인 없이 열어볼 수 있습니다.</p></section></main>`;
  document.querySelectorAll('[data-auth]').forEach(b=>b.onclick=()=>{state.auth=b.dataset.auth;renderAuth();});
  $('#auth-form').onsubmit=async e=>{
    e.preventDefault();const btn=e.target.querySelector('button');btn.disabled=true;$('#form-error').textContent='';
    try {const d=await api(recover?'recover':reg?'register':'login',fields(e.target));state.user=d.user;state.csrf=d.csrf;state.auth='login';state.tab='dashboard';if(d.recovery_code)showRecovery(d.recovery_code);else await load();}
    catch(err){$('#form-error').textContent=err.message;}finally{btn.disabled=false;}
  };
}
async function load(quiet=false) {
  clearTimeout(refreshTimer); state.data=await api('dashboard');
  if(!quiet || !['settings','account'].includes(state.tab))render();
  if(state.data.runs.some(r=>['queued','running'].includes(r.status)))refreshTimer=setTimeout(()=>load(true).catch(e=>toast(e.message)),5000);
}
function shell(body) {
  $('#app').innerHTML=`<div class="shell"><aside class="sidebar">${brand}<div class="nav-label">MY NOTEBOOK</div><nav class="nav">${[['dashboard','▦','내 순위판'],['settings','⚙','학원 · 키워드 설정'],['history','◷','기록 · 공유 링크'],['account','○','내 계정']].map(([tab,icon,label])=>`<button data-tab="${tab}" class="${state.tab===tab?'active':''}"><span class="icon" aria-hidden="true">${icon}</span><span class="label">${label}</span><span class="sr-only">${label}</span></button>`).join('')}</nav><div class="sidebar-foot"><div class="profile"><div class="avatar">${esc(state.user.name[0])}</div><div><strong>${esc(state.user.name)}</strong><span>나만의 순위 기록장</span></div></div><button class="quiet small" data-action="logout">로그아웃 ↗</button></div></aside><main class="workspace"><header class="topbar"><span>내 기록장 / <strong>${esc({dashboard:'검색 노출 순위판',settings:'학원 설정',history:'기록과 공유',account:'내 계정'}[state.tab])}</strong></span><span class="live">● &nbsp; 내 기록은 내가 관리해요</span><button class="quiet small" data-action="logout">로그아웃</button></header><div class="content">${body}<footer><span>ACADEMY RANK · 공유한 결과만 함께 볼 수 있습니다.</span><span>모든 일시 · 한국 표준시 (KST)</span></footer></div></main></div>`;
  document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{state.tab=b.dataset.tab;state.selected=null;render();});
  bind('logout',async()=>{await api('logout',{});state.user=null;state.data=null;state.recovery=null;renderAuth();});
}
function stats(run,publicMode=false) {
  const rows=run?.results||[],p=rows.filter(x=>x.kind==='place'&&x.status==='ranked'),b=rows.filter(x=>x.kind==='blog'&&x.status==='ranked');
  const count=publicMode?rows.length:state.data.config.blog_keywords.length+state.data.config.place_keywords.length;
  return `<div class="stats"><div class="stat"><div class="stat-label">${publicMode?'확인한':'등록한'} 키워드 <span>⌕</span></div><div class="stat-value">${count}<small>개</small></div><p>플레이스와 블로그를 함께 관리</p></div><div class="stat"><div class="stat-label">플레이스 노출 <span>⌖</span></div><div class="stat-value">${p.length}<small>개 키워드</small></div><p>확인한 목록 내 최대 50위 기준</p></div><div class="stat"><div class="stat-label">블로그 노출 <span>▧</span></div><div class="stat-value">${b.length}<small>개 키워드</small></div><p>통합검색 문서 중 15위 이내</p></div><div class="stat"><div class="stat-label">최근 순위 체크 <span>◷</span></div><div class="stat-date">${run?esc(date(run.created)):'—'}</div><p>${run?esc(statuses[run.status]):'첫 순위 체크를 시작해 보세요'}</p></div></div>`;
}
function rankCell(r) {
  if(r.status==='error')return '<span class="rank err">수집 실패</span>';
  if(r.status==='outside')return '<span class="rank out">순위 밖</span>';
  return `<span class="rank ${r.rank>3?'mid':''}">${esc(r.rank)}위</span>`;
}
function resultsCard(run,publicMode=false) {
  const rows=(run?.results||[]).filter(r=>r.kind===state.kind);
  const previous=publicMode?null:state.data.runs.find(r=>r.created<run?.created&&['succeeded','partial'].includes(r.status));
  return `<section class="card"><div class="card-head"><h2>키워드별 검색 순위</h2><span class="muted small">${run?esc(date(run.created)):'아직 체크 전'} 기준</span></div><div class="tabs"><button data-kind="place" class="${state.kind==='place'?'active':''}">플레이스 ${(run?.results||[]).filter(x=>x.kind==='place').length}</button><button data-kind="blog" class="${state.kind==='blog'?'active':''}">블로그 ${(run?.results||[]).filter(x=>x.kind==='blog').length}</button></div>${run&&['queued','running'].includes(run.status)?`<div class="progress"></div><div class="statusline">${statuses[run.status]} · 순서대로 확인합니다. 이 창을 닫아도 서버에서 계속 진행됩니다.</div>`:''}${rows.length?`<div class="table-wrap"><table><thead><tr><th>검색 키워드</th><th>현재 순위</th>${publicMode?'':'<th>이전 대비</th>'}<th>확인 범위</th></tr></thead><tbody>${rows.map(r=>{const prev=previous?.results.find(x=>x.keyword===r.keyword&&x.kind===r.kind),diff=prev?.status==='ranked'&&r.status==='ranked'?prev.rank-r.rank:null;return `<tr><td class="kw">${esc(r.keyword)}${r.url&&/^https:\/\/(?:[a-z0-9-]+\.)*naver\.com\//i.test(r.url)?` <a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer" title="검색된 글 열기">↗</a>`:''}</td><td>${rankCell(r)}</td>${publicMode?'':`<td class="${diff>0?'up':diff<0?'down':'muted'}">${diff===null?'—':diff===0?'유지':(diff>0?'↑ ':'↓ ')+Math.abs(diff)}</td>`}<td class="muted">${r.status==='error'?'확인 불가':esc(r.count)+'건 확인'}</td></tr>`;}).join('')}</tbody></table></div>`:`<div class="empty"><strong>${run?.status==='failed'?'순위 수집을 완료하지 못했습니다.':run?'표시할 결과가 없습니다.':'첫 순위 기록을 남겨보세요'}</strong>${run?'수집 중이라면 완료될 때까지 기다려 주세요. 실패한 경우 잠시 후 다시 확인해 주세요.':'학원과 키워드를 등록하면 여기에 순위가 쌓입니다.'}${!publicMode&&!run?'<br><button data-action="settings">학원 · 키워드 등록 →</button>':''}</div>`}${run?.error?`<div class="card-body"><div class="notice error">${esc(run.error)}</div></div>`:''}<div class="card-foot">${state.kind==='blog'?'광고·플레이스 등을 제외한 통합검색 문서 순서입니다. 블로그 전용 탭의 순위와 다릅니다.':'플레이스 목록에서 확인한 최대 50위 기준입니다.'} 수집 실패는 순위 밖으로 계산하지 않습니다.</div></section>`;
}
function canShare(r) {return r&&['succeeded','partial'].includes(r.status);}
async function shareRecord(runId) {
  const out=await api('reports',{run_id:runId}); await load(); await copy(url(out.share_token));
}
function dashboard() {
  const d=state.data,run=d.runs[0],active=d.runs.find(r=>['queued','running'].includes(r.status)),good=d.runs.find(canShare);
  shell(heading('WEEKLY RANKING',d.config.name?d.config.name+' 순위판':'우리 학원 순위판','내 키워드로 직접 확인하고, 매주 달라지는 순위를 기록하세요.',`<button data-action="reload">↻ 새로고침</button><button class="primary" data-action="check" ${active?'disabled':''}>${active?'순위 확인 중…':'＋ 순위 체크'}</button>`)+`<div class="weekbox"><div><span class="tag">나만의 주간 루틴</span><h2>확인하고, 기록하고, 필요할 때 공유하세요.</h2><p>학원 설정과 기록은 내 계정에 저장됩니다. 공유할 결과는 직접 선택하세요.</p></div><div class="step"><b>1</b> 키워드 등록 <span class="arrow">→</span><b>2</b> 순위 체크 <span class="arrow">→</span><b>3</b> 링크 공유</div></div>`+stats(run)+`<div class="grid"><div>${resultsCard(run)}<div class="help">ⓘ &nbsp; 검색 시점·지역·개인화에 따라 결과가 달라질 수 있어요. 비슷한 조건에서의 주간 변화를 살펴보세요.</div></div><aside class="aside"><section class="card"><div class="card-body"><div class="aside-title"><span class="circle">↗</span><h3>이번 기록 공유하기</h3></div><p>지금 확인한 결과를 고정한 링크입니다. 선생님께 카톡이나 과제로 보내세요.</p><button class="primary full" data-action="report" data-id="${esc(run?.id||'')}" ${canShare(run)?'':'disabled'}>결과 링크 만들고 복사 →</button><p class="small">다음에 순위를 체크해도 이 링크의 결과는 바뀌지 않습니다.</p></div></section><section class="card"><div class="card-body"><div class="aside-title"><span class="circle">♧</span><h3>최신 순위판 공유</h3></div><p>매번 같은 주소에서 가장 최근에 확인된 결과를 보여주고 싶을 때 사용하세요.</p>${d.sharing?`<div class="linkbox">${esc(url(d.share_token))}</div><button class="full" data-action="copy" data-value="${esc(url(d.share_token))}">공유 링크 복사</button><div class="actions"><button class="quiet small" data-action="share-off">공유 끄기</button><button class="quiet small" data-action="rotate">주소 재발급</button></div>`:`<button class="full" data-action="share-on" ${good?'':'disabled'}>최신 결과 공유 켜기</button>`}<p class="small">공유 링크를 가진 사람은 로그인 없이 결과를 볼 수 있습니다.</p></div></section><div class="help">회원 이메일과 학원 설정 화면은 공유 페이지에 표시되지 않습니다.</div></aside></div>`);
  bind('check',async()=>{if(!d.config.name){state.tab='settings';render();toast('학원과 키워드를 먼저 등록해 주세요.');return;}await api('check',{});toast('순위 체크를 대기열에 등록했습니다.');await load();});
  bind('share-on',async()=>{await api('share',{enabled:true});await load();toast('최신 결과 공유를 켰습니다.');});
  bind('share-off',async()=>{await api('share',{enabled:false});await load();toast('최신 순위판 공유를 껐습니다.');});
  bind('rotate',async()=>{await api('share',{enabled:true,rotate:true});await load();toast('이전 최신 순위판 링크는 종료되었습니다.');});
}
function settings() {
  const c=state.data.config;
  shell(heading('ACADEMY SETTINGS','학원 · 키워드 설정','다른 회원에게 공개되지 않는 나만의 설정입니다.')+`<form id="settings-form"><section class="card"><div class="card-head"><h2>우리 학원 정보</h2><span class="badge">계정당 학원 1개</span></div><div class="card-body"><div class="field"><label for="academy-name">학원명</label><input id="academy-name" name="name" required maxlength="80" value="${esc(c.name)}" placeholder="예: 중앙 태권도"></div><div class="form-grid"><div class="field"><label for="blog-id">네이버 블로그 ID / 주소</label><input id="blog-id" name="blog" value="${esc(c.blog)}" placeholder="blog.naver.com/myacademy"><small>블로그 기본 주소 또는 영문 ID를 입력하세요.</small></div><div class="field"><label for="place-url">네이버 플레이스 상세 주소</label><input id="place-url" name="place" value="${esc(c.place)}" placeholder="https://map.naver.com/p/entry/place/1234567890"><small>숫자 ID가 포함된 상세 주소를 사용하세요.</small></div></div></div></section><div class="form-grid">${[['place','플레이스',c.place_keywords,'중계동 태권도&#10;노원구 태권도장'],['blog','블로그',c.blog_keywords,'수암초 태권도&#10;중계동 태권도 7세']].map(([k,label,keywords,hint])=>`<section class="card"><div class="card-head"><h2>${label} 키워드</h2><span class="muted small">최대 40개</span></div><div class="card-body"><div class="field"><label for="${k}-keywords">한 줄에 한 개씩 입력</label><textarea id="${k}-keywords" name="${k}_keywords" placeholder="${hint}">${esc(keywords.join('\n'))}</textarea><small>중복은 자동 제거되며, 변경 전 수집 기록은 보존됩니다.</small></div></div></section>`).join('')}</div><div class="notice">검색 사이에는 6초 간격을 둡니다. 키워드 수와 응답 속도에 따라 수분 이상 걸릴 수 있습니다. 하루 3회까지 실행할 수 있습니다.</div><div id="settings-error" role="alert" class="danger small"></div><button class="primary" type="submit">설정 저장하기 →</button></form>`);
  $('#settings-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button[type=submit]');b.disabled=true;try{await api('config',fields(e.target));toast('학원 설정을 저장했습니다.');state.tab='dashboard';await load();}catch(err){$('#settings-error').textContent=err.message;}finally{b.disabled=false;}};
}
function historyPage() {
  const d=state.data,chosen=d.runs.find(r=>r.id===state.selected);
  shell(heading('MY RANKING ARCHIVE','기록 · 공유 링크','지난 기록을 다시 보거나, 공유를 켜고 끌 수 있습니다.')+`<div class="grid"><div><section class="card"><div class="card-head"><h2>순위 체크 기록</h2><span class="muted small">최근 40회</span></div>${d.runs.map(r=>`<div class="history-row"><div><strong>${esc(date(r.created))}</strong><small>${esc(r.config.name)} · ${esc(statuses[r.status])} · ${r.results.length}개 결과</small></div><div class="actions"><button data-action="select-run" data-id="${r.id}">결과 보기</button>${canShare(r)?`<button data-action="report" data-id="${r.id}">링크 공유</button>`:''}</div></div>`).join('')||'<div class="empty">아직 기록이 없습니다.</div>'}</section>${chosen?resultsCard(chosen):''}</div><aside><section class="card"><div class="card-head"><h2>내가 공유한 기록</h2></div>${d.reports.map(s=>`<div class="card-body report-item"><strong class="small">${esc(date(s.created))} 공유</strong><p>${s.enabled?'공유 중 · 이 기록의 결과는 고정됩니다.':'공유 종료 · 기존 링크로 볼 수 없습니다.'}</p>${s.enabled?`<a href="${esc(url(s.share_token))}" target="_blank" rel="noopener">공유 결과 열기 ↗</a><button class="full" data-action="copy" data-value="${esc(url(s.share_token))}">링크 복사</button>`:''}<div class="actions"><button class="quiet small" data-action="report-toggle" data-id="${s.id}" data-enabled="${s.enabled?'0':'1'}">${s.enabled?'공유 끄기':'다시 공유하기'}</button><button class="quiet small" data-action="report-rotate" data-id="${s.id}">새 주소 발급</button></div></div>`).join('')||'<div class="empty">아직 공유한 기록이 없습니다.</div>'}</section></aside></div>`);
  bind('select-run',async b=>{state.selected=b.dataset.id;render();});
  bind('report-toggle',async b=>{await api('report-share',{id:b.dataset.id,enabled:b.dataset.enabled==='1'});await load();});
  bind('report-rotate',async b=>{const d=await api('report-share',{id:b.dataset.id,enabled:true,rotate:true});await load();await copy(url(d.share_token));});
}
function account() {
  shell(heading('MY ACCOUNT','내 계정','비밀번호와 계정 복구 코드를 직접 관리하세요.')+`<section class="card"><div class="card-head"><h2>계정 정보</h2></div><div class="card-body"><p><strong>${esc(state.user.name)}</strong></p><p>${esc(state.user.email)}</p><p class="small muted">이 정보는 결과 공유 페이지에 표시되지 않습니다.</p></div></section><form id="account-form"><section class="card"><div class="card-head"><h2>비밀번호 변경 · 복구 코드 재발급</h2></div><div class="card-body"><div class="form-grid"><div class="field"><label for="current-password">현재 비밀번호</label><input id="current-password" name="current_password" type="password" required maxlength="200" autocomplete="current-password"></div><div class="field"><label for="new-password">새 비밀번호 (선택)</label><input id="new-password" name="new_password" type="password" minlength="10" maxlength="200" autocomplete="new-password"><small>비워 두면 복구 코드만 새로 발급합니다.</small></div></div><p class="small muted">저장하면 이전 복구 코드와 다른 기기의 로그인은 종료됩니다.</p><div id="account-error" role="alert" class="small danger"></div><button class="primary" type="submit">변경하고 새 복구 코드 받기</button></div></section></form>`);
  $('#account-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{const d=await api('account',fields(e.target));state.csrf=d.csrf;showRecovery(d.recovery_code);}catch(err){$('#account-error').textContent=err.message;}finally{b.disabled=false;}};
}
function render() {
  ({dashboard,settings,history:historyPage,account}[state.tab]||dashboard)();
  bind('reload',async()=>{await load();toast('새로고침했습니다.');});
  bind('copy',b=>copy(b.dataset.value)); bind('report',b=>shareRecord(b.dataset.id));
  bind('settings',async()=>{state.tab='settings';render();});
  document.querySelectorAll('[data-kind]').forEach(b=>b.onclick=()=>{state.kind=b.dataset.kind;render();});
}
async function publicPage(token) {
  const data=await api('public/'+token),r=data.snapshot;
  function draw() {
    $('#app').innerHTML=`<main class="shared"><div class="share-head">${brand}<span class="shared-banner">${data.fixed?'공유 당시 결과 · 고정 기록':'최신 순위판 · 읽기 전용'}</span></div>${heading('SHARED RANKING NOTE',r.academy_name+' 검색 노출 리포트',day(r.created)+' 확인 · '+(data.fixed?'원장님이 선택해 공유한 결과입니다.':'가장 최근에 확인된 결과입니다.'))}${stats(r,true)}${resultsCard(r,true)}<div class="help">원장님이 공유한 순위 결과만 볼 수 있습니다. 검색 시점·지역·개인화에 따라 실제 검색화면과 다를 수 있습니다.</div><footer><span>ACADEMY RANK · 읽기 전용 공유 페이지</span><a href="/">나도 순위 기록하기 →</a></footer></main>`;
    document.querySelectorAll('[data-kind]').forEach(b=>b.onclick=()=>{state.kind=b.dataset.kind;draw();});
  }
  draw();
}
async function start() {
  try {
    const shared=location.pathname.match(/^\/s\/([A-Za-z0-9_-]+)$/);
    if(shared){await publicPage(shared[1]);return;}
    const d=await api('session');state.user=d.user;state.csrf=d.csrf;
    if(state.user)await load();else renderAuth();
  } catch(e) {$('#app').innerHTML=`<main class="public-error">${brand}<h1>페이지를 열 수 없습니다</h1><p>${esc(e.message)}</p><a href="/">처음 화면으로 돌아가기</a></main>`;}
}
start();
