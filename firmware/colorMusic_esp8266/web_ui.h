#pragma once

const char WIFI_PAGE[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>ColorMusic - WiFi</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,system-ui,sans-serif;background:#111;color:#e0e0e0;padding:12px;max-width:480px;margin:0 auto}
h1{font-size:1.4rem;text-align:center;padding:12px 0 4px;color:#2ee8b7}
.sub{text-align:center;font-size:.75rem;color:#666;margin-bottom:14px}
.sec{background:#1a1a2e;border-radius:8px;padding:12px;margin-bottom:10px}
.sec h2{font-size:.8rem;color:#2ee8b7;margin-bottom:10px;text-transform:uppercase;letter-spacing:.05em}
.nets{display:flex;flex-direction:column;gap:4px}
.nets button{padding:10px;border:1px solid #333;background:#111;color:#ccc;border-radius:6px;font-size:.82rem;cursor:pointer;text-align:left;display:flex;justify-content:space-between}
.nets button:active{border-color:#2ee8b7;color:#2ee8b7}
input[type=text],input[type=password]{width:100%;padding:10px;border:1px solid #333;background:#111;color:#e0e0e0;border-radius:6px;font-size:.85rem;margin-bottom:8px;outline:none}
input:focus{border-color:#2ee8b7}
.btn{display:block;width:100%;padding:12px;border:none;border-radius:8px;font-size:.85rem;font-weight:600;cursor:pointer;margin-bottom:8px}
.btn.go{background:#2ee8b7;color:#111}
.btn.scan{background:#1a1a2e;color:#2ee8b7;border:1px solid #2ee8b7}
.btn.rst{background:#1a1a2e;color:#c0392b;border:1px solid #c0392b}
.link{display:block;text-align:center;color:#2ee8b7;font-size:.82rem;margin-top:12px;text-decoration:none}
.msg{text-align:center;font-size:.82rem;color:#f39c12;padding:8px}
.info{text-align:center;font-size:.72rem;color:#555;margin-top:6px}
</style>
</head>
<body>
<h1>ColorMusic</h1>
<p class="sub">Настройка WiFi</p>

<div class="sec">
<h2>Доступные сети</h2>
<div class="nets" id="nets"><p style="color:#666;font-size:.8rem">Нажмите "Сканировать"</p></div>
<button class="btn scan" onclick="scan()" style="margin-top:8px">Сканировать</button>
</div>

<div class="sec">
<h2>Подключение к домашней сети</h2>
<input type="text" id="ssid" placeholder="Имя сети (SSID)">
<input type="password" id="pass" placeholder="Пароль">
<button class="btn go" onclick="save()">Подключиться</button>
</div>

<p id="msg" class="msg"></p>

<button class="btn rst" onclick="reset()">Сбросить WiFi (режим точки доступа)</button>

<a class="link" href="/ctrl">Управление лентой &rarr;</a>
<p class="info">Двойное нажатие кнопки = сброс WiFi</p>

<script>
function scan(){
  document.getElementById('nets').innerHTML='<p style="color:#666;font-size:.8rem">Сканирование...</p>';
  fetch('/scan').then(function(r){return r.json()}).then(function(d){
    var h='';
    d.forEach(function(n){
      h+='<button onclick="document.getElementById(\'ssid\').value=\''+n.s.replace(/'/g,"\\'")+'\'">'+n.s+'<span style="color:#666">'+(n.e?'&#128274; ':'')+n.r+'dBm</span></button>';
    });
    document.getElementById('nets').innerHTML=h||'<p style="color:#666;font-size:.8rem">Сети не найдены</p>';
  }).catch(function(){document.getElementById('nets').innerHTML='<p style="color:#c0392b;font-size:.8rem">Ошибка сканирования</p>'});
}
function save(){
  var s=document.getElementById('ssid').value;
  var p=document.getElementById('pass').value;
  if(!s){alert('Введите имя сети');return}
  document.getElementById('msg').textContent='Сохранение... ESP перезагрузится и подключится к '+s;
  fetch('/wifisave?ssid='+encodeURIComponent(s)+'&pass='+encodeURIComponent(p)).catch(function(){});
}
function reset(){
  if(!confirm('Сбросить WiFi? ESP перейдёт в режим точки доступа.'))return;
  document.getElementById('msg').textContent='Сброс... ESP перезагрузится';
  fetch('/wifireset').catch(function(){});
}
</script>
</body>
</html>
)rawliteral";

const char WEB_PAGE[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>ColorMusic</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,system-ui,sans-serif;background:#111;color:#e0e0e0;padding:12px;max-width:480px;margin:0 auto;-webkit-tap-highlight-color:transparent;user-select:none}
h1{font-size:1.4rem;text-align:center;padding:12px 0 4px;color:#2ee8b7}
.sub{text-align:center;font-size:.75rem;color:#666;margin-bottom:12px}
.pwr{display:flex;justify-content:center;margin-bottom:14px}
.pwr button{font-size:.9rem;padding:10px 32px;border:none;border-radius:8px;cursor:pointer;font-weight:600;transition:.2s}
.pwr .on{background:#2ee8b7;color:#111}
.pwr .off{background:#c0392b;color:#fff}
.modes{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:14px}
.modes button{padding:8px 4px;border:2px solid #333;background:#1a1a2e;color:#ccc;border-radius:6px;font-size:.72rem;cursor:pointer;transition:.2s;line-height:1.2}
.modes button.act{border-color:#2ee8b7;color:#2ee8b7;background:#0d2b23}
.sec{background:#1a1a2e;border-radius:8px;padding:12px;margin-bottom:10px}
.sec h2{font-size:.8rem;color:#2ee8b7;margin-bottom:10px;text-transform:uppercase;letter-spacing:.05em}
.row{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
.row:last-child{margin-bottom:0}
.row label{font-size:.78rem;color:#aaa;flex-shrink:0;min-width:90px}
.row .val{font-size:.75rem;color:#2ee8b7;min-width:32px;text-align:right;font-variant-numeric:tabular-nums;margin-left:6px}
.row input[type=range]{flex:1;margin:0 6px;height:6px;-webkit-appearance:none;appearance:none;background:#333;border-radius:3px;outline:none}
.row input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:22px;height:22px;border-radius:50%;background:#2ee8b7;cursor:pointer}
.subm{display:flex;gap:4px;margin-bottom:10px}
.subm button{flex:1;padding:7px;border:1px solid #333;background:#1a1a2e;color:#aaa;border-radius:5px;font-size:.72rem;cursor:pointer}
.subm button.act{border-color:#2ee8b7;color:#2ee8b7;background:#0d2b23}
.cbtn{display:block;width:100%;padding:12px;border:none;border-radius:8px;font-size:.85rem;font-weight:600;cursor:pointer;margin-bottom:10px;transition:.2s}
.cbtn.cal{background:#1a1a2e;color:#f39c12;border:1px solid #f39c12}
.cbtn.cal:active{background:#2c1810}
.cbtn.save{background:#2ee8b7;color:#111}
.cbtn.save:active{background:#26c49a}
.cbtn.rst{background:#1a1a2e;color:#c0392b;border:1px solid #c0392b}
.cbtn.rst:active{background:#2c1015}
.hide{display:none!important}
.rrow{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
.rrow:last-child{margin-bottom:0}
.rrow span{font-size:.82rem;color:#aaa}
.rrow button{padding:6px 18px;border:none;border-radius:6px;font-size:.78rem;font-weight:600;cursor:pointer;min-width:56px;transition:.2s}
.ron{background:#2ee8b7;color:#111}
.roff{background:#333;color:#666}
.msg{text-align:center;font-size:.8rem;color:#f39c12;padding:8px;opacity:0;transition:opacity .3s}
.msg.show{opacity:1}
.info{text-align:center;font-size:.7rem;color:#555;margin-top:8px}
.info a{color:#2ee8b7;text-decoration:none}
</style>
</head>
<body>
<h1>ColorMusic</h1>
<p class="sub">NodeMCU ESP8266</p>

<div class="pwr"><button id="pbtn" onclick="togglePower()">ON</button></div>

<div class="modes" id="modes">
<button onclick="setMode(0)">VU<br>Градиент</button>
<button onclick="setMode(1)">VU<br>Радуга</button>
<button onclick="setMode(2)">Цвет<br>5 полос</button>
<button onclick="setMode(3)">Цвет<br>3 полосы</button>
<button onclick="setMode(4)">Цвет<br>Вспышки</button>
<button onclick="setMode(5)">Стробо<br>скоп</button>
<button onclick="setMode(6)">Под<br>светка</button>
<button onclick="setMode(7)">Бегущие<br>огни</button>
<button onclick="setMode(8)">Анали<br>затор</button>
</div>

<div class="sec">
<h2>Реле</h2>
<div class="rrow"><span>Реле 1</span><button id="r1" onclick="tR(1)">OFF</button></div>
<div class="rrow"><span>Реле 2</span><button id="r2" onclick="tR(2)">OFF</button></div>
<div class="rrow"><span>Реле 3</span><button id="r3" onclick="tR(3)">OFF</button></div>
<div class="rrow"><span>Реле 4</span><button id="r4" onclick="tR(4)">OFF</button></div>
</div>

<div class="sec">
<h2>Общие</h2>
<div class="row"><label>Яркость</label><input type="range" min="0" max="255" id="br" oninput="send('br',this.value)"><span class="val" id="vbr"></span></div>
<div class="row"><label>Фон яркость</label><input type="range" min="0" max="255" id="ebr" oninput="send('ebr',this.value)"><span class="val" id="vebr"></span></div>
<div class="row"><label>Фон цвет</label><input type="range" min="0" max="255" id="ecol" oninput="send('ecol',this.value)"><span class="val" id="vecol"></span></div>
</div>

<div class="sec" id="s_vu">
<h2>VU-метр</h2>
<div class="row"><label>Плавность</label><input type="range" min="5" max="100" id="sm" oninput="send('sm',this.value/100)"><span class="val" id="vsm"></span></div>
<div class="row"><label>Усиление</label><input type="range" min="10" max="30" id="exp" oninput="send('exp',this.value/10)"><span class="val" id="vexp"></span></div>
<div class="row"><label>Авто-макс</label><input type="range" min="10" max="30" id="mc" oninput="send('mc',this.value/10)"><span class="val" id="vmc"></span></div>
</div>

<div class="sec" id="s_rain">
<h2>Радуга</h2>
<div class="row"><label>Шаг радуги</label><input type="range" min="1" max="40" id="rs" oninput="send('rs',this.value/2)"><span class="val" id="vrs"></span></div>
</div>

<div class="sec" id="s_freq">
<h2>Цветомузыка</h2>
<div class="row"><label>Плавность</label><input type="range" min="5" max="100" id="smf" oninput="send('smf',this.value/100)"><span class="val" id="vsmf"></span></div>
<div class="row"><label>Чувствит.</label><input type="range" min="0" max="50" id="mcf" oninput="send('mcf',this.value/10)"><span class="val" id="vmcf"></span></div>
</div>

<div class="sec" id="s_fsm">
<h2>Частотный фильтр</h2>
<div class="subm" id="fsm_btns">
<button onclick="setFSM(0)">Все</button>
<button onclick="setFSM(1)">Высокие</button>
<button onclick="setFSM(2)">Средние</button>
<button onclick="setFSM(3)">Низкие</button>
</div>
</div>

<div class="sec" id="s_strobe">
<h2>Стробоскоп</h2>
<div class="row"><label>Период мс</label><input type="range" min="10" max="1000" step="10" id="sp" oninput="send('sp',this.value)"><span class="val" id="vsp"></span></div>
<div class="row"><label>Плавность</label><input type="range" min="1" max="255" id="ss" oninput="send('ss',this.value)"><span class="val" id="vss"></span></div>
</div>

<div class="sec" id="s_light">
<h2>Подсветка</h2>
<div class="subm" id="lm_btns">
<button onclick="setLM(0)">Статика</button>
<button onclick="setLM(1)">Перелив</button>
<button onclick="setLM(2)">Радуга</button>
</div>
<div id="s_l0">
<div class="row"><label>Цвет</label><input type="range" min="0" max="255" id="lc" oninput="send('lc',this.value)"><span class="val" id="vlc"></span></div>
<div class="row"><label>Насыщенн.</label><input type="range" min="0" max="255" id="ls" oninput="send('ls',this.value)"><span class="val" id="vls"></span></div>
</div>
<div id="s_l1" class="hide">
<div class="row"><label>Скорость</label><input type="range" min="1" max="255" id="cs" oninput="send('cs',this.value)"><span class="val" id="vcs"></span></div>
<div class="row"><label>Насыщенн.</label><input type="range" min="0" max="255" id="ls1" oninput="send('ls',this.value)"><span class="val" id="vls1"></span></div>
</div>
<div id="s_l2" class="hide">
<div class="row"><label>Шаг</label><input type="range" min="1" max="20" id="rs2" oninput="send('rs2',this.value/2)"><span class="val" id="vrs2"></span></div>
<div class="row"><label>Период</label><input type="range" min="-20" max="20" id="rp" oninput="send('rp',this.value)"><span class="val" id="vrp"></span></div>
</div>
</div>

<div class="sec" id="s_run">
<h2>Бегущие огни</h2>
<div class="row"><label>Скорость</label><input type="range" min="1" max="255" id="rns" oninput="send('rns',this.value)"><span class="val" id="vrns"></span></div>
</div>

<div class="sec" id="s_anal">
<h2>Анализатор</h2>
<div class="row"><label>Начало цвет</label><input type="range" min="0" max="255" id="hs" oninput="send('hs',this.value)"><span class="val" id="vhs"></span></div>
<div class="row"><label>Шаг цвета</label><input type="range" min="1" max="255" id="hst" oninput="send('hst',this.value)"><span class="val" id="vhst"></span></div>
</div>

<div class="sec">
<h2>Аудио</h2>
<div class="row"><label>Порог VU</label><input type="range" min="0" max="800" id="lp" oninput="send('lp',this.value)"><span class="val" id="vlp"></span></div>
<div class="row"><label>Порог спектра</label><input type="range" min="0" max="500" id="slp" oninput="send('slp',this.value)"><span class="val" id="vslp"></span></div>
<button class="cbtn cal" onclick="calibrate()">Калибровка шума</button>
</div>

<button class="cbtn rst" onclick="resetDefaults()">Сброс настроек</button>

<p id="msg" class="msg"></p>
<p class="info" id="ipinfo"></p>
<p class="info"><a href="/wifi">Настройки WiFi</a></p>

<script>
var S={},T=null;
function $(id){return document.getElementById(id)}
function send(k,v){
  clearTimeout(T);
  T=setTimeout(function(){
    fetch('/set?'+k+'='+v).catch(function(){});
  },50);
  S[k]=v;
  upVal(k,v);
}
function upVal(k,v){
  var e=$('v'+k);
  if(e)e.textContent=v;
}
function setMode(m){
  fetch('/set?mode='+m).then(function(){load()}).catch(function(){});
}
function togglePower(){
  var n=S.on?0:1;
  fetch('/set?on='+n).then(function(){load()}).catch(function(){});
}
function tR(n){
  var v=S['r'+n]?0:1;
  fetch('/set?r'+n+'='+v).then(function(){load()}).catch(function(){});
}
function setFSM(v){
  fetch('/set?fsm='+v).then(function(){load()}).catch(function(){});
}
function setLM(v){
  fetch('/set?lm='+v).then(function(){load()}).catch(function(){});
}
function calibrate(){
  showMsg('Калибровка...');
  fetch('/calibrate').then(function(r){return r.json()}).then(function(d){
    S.lp=d.lp;S.slp=d.slp;
    $('lp').value=d.lp;upVal('lp',d.lp);
    $('slp').value=d.slp;upVal('slp',d.slp);
    showMsg('Готово! VU='+d.lp+' Спектр='+d.slp);
  }).catch(function(){showMsg('Ошибка')});
}
function resetDefaults(){
  if(!confirm('Сбросить все настройки?'))return;
  fetch('/set?on=1&mode=0&br=200&ebr=30&ecol=192&sm=0.5&smf=0.8&rs=5&mcf=1.5&mc=1.8&exp=1.4&rs2=0.5&sp=140&ss=200&lc=0&ls=255&cs=100&rp=1&rns=11&hs=0&hst=5&fsm=0&lm=0&r1=0&r2=0&r3=0&r4=0')
  .then(function(){showMsg('Сброшено');load()}).catch(function(){});
}
function showMsg(t){
  var e=$('msg');e.textContent=t;e.classList.add('show');
  setTimeout(function(){e.classList.remove('show')},3000);
}
function load(){
  fetch('/get').then(function(r){return r.json()}).then(function(d){
    S=d;
    var pb=$('pbtn');
    if(d.on){pb.textContent='ON';pb.className='on'}else{pb.textContent='OFF';pb.className='off'}
    var btns=$('modes').children;
    for(var i=0;i<btns.length;i++){
      btns[i].className=i==d.mode?'act':'';
    }
    var sl={br:d.br,ebr:d.ebr,ecol:d.ecol,
      sm:Math.round(d.sm*100),smf:Math.round(d.smf*100),
      rs:Math.round(d.rs*2),mcf:Math.round(d.mcf*10),
      mc:Math.round(d.mc*10),exp:Math.round(d.exp*10),
      rs2:Math.round(d.rs2*2),sp:d.sp,ss:d.ss,
      lc:d.lc,ls:d.ls,cs:d.cs,rp:d.rp,rns:d.rns,
      hs:d.hs,hst:d.hst,lp:d.lp,slp:d.slp};
    for(var k in sl){
      var el=$(k);
      if(el)el.value=sl[k];
    }
    upVal('br',d.br);upVal('ebr',d.ebr);upVal('ecol',d.ecol);
    upVal('sm',d.sm);upVal('smf',d.smf);upVal('rs',d.rs);
    upVal('mcf',d.mcf);upVal('mc',d.mc);upVal('exp',d.exp);
    upVal('rs2',d.rs2);upVal('sp',d.sp);upVal('ss',d.ss);
    upVal('lc',d.lc);upVal('ls',d.ls);upVal('cs',d.cs);
    upVal('rp',d.rp);upVal('rns',d.rns);upVal('hs',d.hs);
    upVal('hst',d.hst);upVal('lp',d.lp);upVal('slp',d.slp);
    $('ls1').value=d.ls;upVal('ls1',d.ls);
    var fb=$('fsm_btns').children;
    for(var i=0;i<fb.length;i++)fb[i].className=i==d.fsm?'act':'';
    var lb=$('lm_btns').children;
    for(var i=0;i<lb.length;i++)lb[i].className=i==d.lm?'act':'';
    for(var i=1;i<=4;i++){var rb=$('r'+i);if(d['r'+i]){rb.textContent='ON';rb.className='ron'}else{rb.textContent='OFF';rb.className='roff'}}
    showSections(d.mode,d.lm);
    $('ipinfo').textContent='IP: '+d.ip+(d.ap?' (точка доступа)':' (домашняя сеть)');
  }).catch(function(){});
}
function showSections(m,lm){
  var ids=['s_vu','s_rain','s_freq','s_fsm','s_strobe','s_light','s_run','s_anal'];
  ids.forEach(function(id){$(id).classList.add('hide')});
  switch(m){
    case 0:$('s_vu').classList.remove('hide');break;
    case 1:$('s_vu').classList.remove('hide');$('s_rain').classList.remove('hide');break;
    case 2:case 3:$('s_freq').classList.remove('hide');break;
    case 4:$('s_freq').classList.remove('hide');$('s_fsm').classList.remove('hide');break;
    case 5:$('s_strobe').classList.remove('hide');break;
    case 6:
      $('s_light').classList.remove('hide');
      $('s_l0').className=lm==0?'':'hide';
      $('s_l1').className=lm==1?'':'hide';
      $('s_l2').className=lm==2?'':'hide';
      break;
    case 7:$('s_freq').classList.remove('hide');$('s_fsm').classList.remove('hide');$('s_run').classList.remove('hide');break;
    case 8:$('s_anal').classList.remove('hide');break;
  }
}
load();
setInterval(load,5000);
</script>
</body>
</html>
)rawliteral";
