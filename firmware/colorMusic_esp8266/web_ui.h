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
input[type=text],input[type=password],input[type=number]{width:100%;padding:10px;border:1px solid #333;background:#111;color:#e0e0e0;border-radius:6px;font-size:.85rem;margin-bottom:8px;outline:none}
input:focus{border-color:#2ee8b7}
.btn{display:block;width:100%;padding:12px;border:none;border-radius:8px;font-size:.85rem;font-weight:600;cursor:pointer;margin-bottom:8px}
.btn.go{background:#2ee8b7;color:#111}
.btn.scan{background:#1a1a2e;color:#2ee8b7;border:1px solid #2ee8b7}
.btn.rst{background:#1a1a2e;color:#c0392b;border:1px solid #c0392b}
.msg{text-align:center;font-size:.82rem;color:#f39c12;padding:8px}
.info{text-align:center;font-size:.72rem;color:#555;margin-top:6px}
</style>
</head>
<body>
<h1>ColorMusic</h1>
<p class="sub">Настройка WiFi и сервера</p>

<div class="sec">
<h2>Доступные сети</h2>
<div class="nets" id="nets"><p style="color:#666;font-size:.8rem">Нажмите "Сканировать"</p></div>
<button class="btn scan" onclick="scan()" style="margin-top:8px">Сканировать</button>
</div>

<div class="sec">
<h2>Подключение к WiFi</h2>
<input type="text" id="ssid" placeholder="Имя сети (SSID)">
<input type="password" id="pass" placeholder="Пароль">
</div>

<div class="sec">
<h2>Сервер управления</h2>
<input type="text" id="host" placeholder="Адрес сервера (IP или домен)">
<input type="number" id="port" placeholder="Порт (по умолчанию 3000)" value="3000">
</div>

<button class="btn go" onclick="save()">Сохранить и подключиться</button>

<p id="msg" class="msg"></p>

<button class="btn rst" onclick="reset()">Сбросить всё (режим точки доступа)</button>

<p class="info">Двойное нажатие кнопки = сброс WiFi и сервера</p>

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
  var h=document.getElementById('host').value;
  var pt=document.getElementById('port').value||'3000';
  if(!s){alert('Введите имя сети');return}
  document.getElementById('msg').textContent='Сохранение... ESP перезагрузится и подключится к '+s;
  fetch('/wifisave?ssid='+encodeURIComponent(s)+'&pass='+encodeURIComponent(p)+'&host='+encodeURIComponent(h)+'&port='+pt).catch(function(){});
}
function reset(){
  if(!confirm('Сбросить все настройки подключения? ESP перейдёт в режим точки доступа.'))return;
  document.getElementById('msg').textContent='Сброс... ESP перезагрузится';
  fetch('/wifireset').catch(function(){});
}
</script>
</body>
</html>
)rawliteral";
